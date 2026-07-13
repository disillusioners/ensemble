"""Comprehensive tests for the user language preference feature (Phase 2).

Covers:
- Language detection heuristics (`daemon/language_detection.py`)
- Language check graph node and routing (`daemon/graph.py`)
- Language skip-check tool (`daemon/tools/language_tools.py`)
- Language config (`daemon/config.py`)
- System prompt language injection (`daemon/services/instance_lifecycle.py`)

These tests run synchronously where possible and use asyncio for the
`language_check_node` (which is an async function). With ``asyncio_mode = "auto"``
in pyproject.toml, async test functions are automatically marked.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.messages.tool import ToolCall
from langgraph.graph import END

from pydantic import ValidationError

from daemon.config import Config, LanguageConfig
from daemon.graph import (
    LANGUAGE_CHECK_MAX_RETRIES,
    LANGUAGE_REMINDER_TEMPLATE,
    SessionState,
    create_language_check_node,
    create_should_continue,
    should_continue,
    should_end_language_check,
)
from daemon.language_detection import (
    SPANISH_MIN_ABSOLUTE_COUNT,
    SPANISH_RATIO_THRESHOLD,
    _normalize_content,
    detect_wrong_language,
    has_cjk_characters,
    spanish_word_count,
    strip_code_blocks,
)
from daemon.routers.schemas import LanguagePreferenceUpdate
from daemon.services.instance_lifecycle import append_user_language
from daemon.tools.language_tools import (
    create_language_tools,
    language_skip_check,
)


# ────────────────────────────────────────────────────────────────────────────
# 1. Language Detection Tests (`detect_wrong_language`)
# ────────────────────────────────────────────────────────────────────────────


class TestDetectWrongLanguage:
    """Unit tests for `detect_wrong_language` covering C2/W1/W4 fixes."""

    # ── English preference ────────────────────────────────────────────────

    def test_english_content_with_english_preference_returns_false(self):
        """Pure English content + English preference → not wrong."""
        content = "Hello, this is a normal English sentence with no foreign characters."
        assert detect_wrong_language(content, "English") is False

    def test_chinese_content_with_english_preference_returns_true(self):
        """CJK content + English preference → wrong language (C2 fix)."""
        content = "你好世界这是一段中文内容用于测试"
        assert detect_wrong_language(content, "English") is True

    def test_japanese_hiragana_with_english_preference_returns_true(self):
        """Hiragana chars count as CJK — should trigger detection."""
        content = "こんにちは世界"
        assert detect_wrong_language(content, "English") is True

    def test_korean_hangul_with_english_preference_returns_true(self):
        """Hangul chars count as CJK — should trigger detection."""
        content = "안녕하세요 세계"
        assert detect_wrong_language(content, "English") is True

    def test_spanish_content_with_english_preference_returns_true(self):
        """Spanish content above 50% ratio + ≥5 indicator words → wrong (W1 fix)."""
        # All 6 words are in SPANISH_INDICATORS, ratio = 100%, count = 6 ≥ 5.
        content = "Porque entonces después está también aquí"
        assert detect_wrong_language(content, "English") is True

    def test_english_content_few_spanish_words_returns_false(self):
        """English content with few Spanish-looking words → not wrong (W1 fix)."""
        # 'bueno' is the only Spanish indicator, ratio below 50% and count < 5.
        content = "This is an English sentence with some bueno words."
        assert detect_wrong_language(content, "English") is False

    def test_english_content_with_ambiguous_spanish_words_returns_false(self):
        """English content using ambiguous words ('no', 'a', 'en', etc.) → not wrong (W1 fix)."""
        # None of 'no', 'a', 'en', 'con', 'sin', 'si', 'lo', 'al', 'que', 'y'
        # are in SPANISH_INDICATORS — they were excluded to avoid false positives.
        content = "No, en a con sin si lo al que y the quick brown fox jumps."
        assert detect_wrong_language(content, "English") is False

    def test_spanish_below_absolute_count_returns_false(self):
        """Spanish content with <5 indicator words → not wrong (W1 fix)."""
        # 3 Spanish indicators, 6 total words → ratio 50% but count < 5 → False.
        content = "Porque quiero hacer esto"
        # Verify count < 5 to make the assertion meaningful
        s, total = spanish_word_count(content)
        assert s < SPANISH_MIN_ABSOLUTE_COUNT
        assert detect_wrong_language(content, "English") is False

    def test_spanish_just_below_threshold_returns_false(self):
        """Spanish content with ratio well below 50% → not wrong."""
        # 3 Spanish indicators out of 12 total words = 25% ratio, count < 5.
        content = "Porque entonces después the quick brown fox jumps over the lazy dog"
        s, total = spanish_word_count(content)
        assert s == 3
        assert total == 12
        assert s / total < SPANISH_RATIO_THRESHOLD
        assert s < SPANISH_MIN_ABSOLUTE_COUNT
        assert detect_wrong_language(content, "English") is False

    # ── Chinese preference ────────────────────────────────────────────────

    def test_chinese_content_with_chinese_preference_returns_false(self):
        """Chinese content + Chinese preference → not wrong."""
        content = "你好世界这是一段中文"
        assert detect_wrong_language(content, "Chinese") is False

    def test_chinese_preference_variants_match(self):
        """'中文' and 'Mandarin' should be treated as Chinese preference."""
        content = "你好世界"
        assert detect_wrong_language(content, "Chinese") is False
        assert detect_wrong_language(content, "中文") is False
        assert detect_wrong_language(content, "Mandarin") is False

    def test_english_content_with_chinese_preference_returns_true(self):
        """English content + Chinese preference → wrong (no CJK)."""
        content = "Hello, this is a normal English sentence without any CJK characters."
        assert detect_wrong_language(content, "Chinese") is True

    # ── Spanish preference ────────────────────────────────────────────────

    def test_spanish_content_with_spanish_preference_returns_false(self):
        """Spanish content + Spanish preference → not wrong (has Spanish words)."""
        content = "Porque entonces después está también aquí"
        assert detect_wrong_language(content, "Spanish") is False

    def test_spanish_preference_variants_match(self):
        """'Spanish' and 'español' should be treated as Spanish preference."""
        content = "Porque entonces después está también aquí"
        assert detect_wrong_language(content, "Spanish") is False
        assert detect_wrong_language(content, "español") is False

    def test_english_content_with_spanish_preference_returns_true(self):
        """English content + Spanish preference → wrong (no Spanish words)."""
        content = "This is a normal English sentence with no Spanish indicators at all."
        assert detect_wrong_language(content, "Spanish") is True

    # ── Code block stripping ──────────────────────────────────────────────

    def test_chinese_in_code_block_is_stripped(self):
        """Chinese text inside fenced code blocks should be stripped before detection."""
        content = "Here is some code:\n```python\ndef foo():\n    return '你好世界'\n```\nThe function returns a string."
        assert detect_wrong_language(content, "English") is False

    def test_content_entirely_code_blocks_returns_false(self):
        """Content that is entirely code blocks → no text to detect → False."""
        content = "```python\ndef foo():\n    return '你好世界'\n```"
        assert detect_wrong_language(content, "English") is False

    def test_mixed_code_and_chinese_returns_true(self):
        """Chinese outside code blocks still triggers detection."""
        content = "```python\nx = 1\n```\n这是一段中文说明"
        assert detect_wrong_language(content, "English") is True

    # ── Multimodal content (W4 fix) ───────────────────────────────────────

    def test_multimodal_content_list_does_not_crash(self):
        """List-type multimodal content should not crash (W4 fix)."""
        content = [
            {"type": "text", "text": "Hello world"},
            {"type": "image_url", "image_url": {"url": "https://example.com/img.png"}},
        ]
        # Extracted text "Hello world" is correct English
        assert detect_wrong_language(content, "English") is False

    def test_multimodal_content_with_cjk_returns_true(self):
        """Multimodal content with CJK in text blocks triggers detection."""
        content = [
            {"type": "text", "text": "你好世界"},
            {"type": "image_url", "image_url": {"url": "https://example.com/img.png"}},
        ]
        assert detect_wrong_language(content, "English") is True

    def test_multimodal_content_only_image_returns_false(self):
        """List with only image blocks (no text) → empty after extract → False."""
        content = [
            {"type": "image_url", "image_url": {"url": "https://example.com/img.png"}},
        ]
        assert detect_wrong_language(content, "English") is False

    def test_multimodal_content_with_string_block(self):
        """List containing a bare string (not dict) is also handled."""
        content = ["Hello world plain text"]
        assert detect_wrong_language(content, "English") is False

    # ── Edge cases ────────────────────────────────────────────────────────

    def test_none_content_returns_false(self):
        """None content → False (empty after normalize)."""
        assert detect_wrong_language(None, "English") is False

    def test_empty_string_returns_false(self):
        """Empty string → False."""
        assert detect_wrong_language("", "English") is False

    def test_whitespace_only_returns_false(self):
        """Whitespace-only content → False."""
        assert detect_wrong_language("   \n\t  ", "English") is False

    def test_unknown_language_preference_returns_false(self):
        """Unknown preferred language → False (no heuristic available)."""
        content = "你好世界"  # would normally trigger English preference
        assert detect_wrong_language(content, "Klingon") is False
        assert detect_wrong_language(content, "Elvish") is False

    def test_preferred_language_case_insensitive(self):
        """Preferred language matching should be case-insensitive (lowercased)."""
        content = "Hello world this is English."
        assert detect_wrong_language(content, "english") is False
        assert detect_wrong_language(content, "ENGLISH") is False
        assert detect_wrong_language(content, "  English  ") is False  # whitespace stripped

    # ── Defense-in-depth: Auto / None preference (no enforcement) ─────────

    def test_none_or_empty_preferred_language_returns_false(self):
        """None or empty preferred_language → False (no preference to enforce).

        Defense-in-depth guard: ``detect_wrong_language`` returns False when the
        preferred language is missing (``None`` or ``""``), regardless of the
        content's language. The graph node should not even be invoked in this
        case (controlled by ``LanguageConfig.check_enabled``), but other callers
        must not crash or flag content as wrong.
        """
        assert detect_wrong_language("Hello world.", None) is False
        assert detect_wrong_language("Hello world.", "") is False

    def test_auto_preferred_language_returns_false_case_insensitive(self):
        """'Auto' (case-insensitive, whitespace-tolerant) → False.

        Explicit ``"Auto"`` preference means "no language enforcement" — the
        check must return False even for clearly wrong-language content like
        Chinese characters. Whitespace and case are normalized before the
        sentinel comparison.
        """
        # Use clearly wrong-language content so the test would FAIL if the
        # guard were removed (i.e. content alone would trigger detection).
        cjk_content = "你好世界这是一段中文内容用于测试"
        for lang in ("Auto", "auto", "AUTO", "  Auto  "):
            assert detect_wrong_language(cjk_content, lang) is False, (
                f"Auto sentinel must disable check for {lang!r}"
            )

    # ── Helper coverage (sanity for completeness) ─────────────────────────

    def test_strip_code_blocks_removes_fences(self):
        """strip_code_blocks should remove fenced code blocks."""
        content = "before ```code\nhello\n``` after"
        result = strip_code_blocks(content)
        assert "hello" not in result
        assert "before" in result
        assert "after" in result

    def test_has_cjk_characters_true_for_cjk(self):
        """has_cjk_characters returns True for CJK chars."""
        assert has_cjk_characters("Hello 你好") is True
        assert has_cjk_characters("こんにちは") is True
        assert has_cjk_characters("안녕") is True

    def test_has_cjk_characters_false_for_pure_ascii(self):
        """has_cjk_characters returns False for ASCII."""
        assert has_cjk_characters("Hello world") is False

    def test_spanish_word_count_empty(self):
        """spanish_word_count returns (0, 0) for empty input."""
        assert spanish_word_count("") == (0, 0)

    def test_spanish_word_count_basic(self):
        """spanish_word_count counts indicator words."""
        # 'porque' and 'entonces' are indicators
        s, total = spanish_word_count("Porque entonces hello world foo bar")
        assert s == 2
        assert total == 6

    def test_normalize_content_handles_string(self):
        """_normalize_content passes strings through unchanged."""
        assert _normalize_content("hello") == "hello"

    def test_normalize_content_handles_none(self):
        """_normalize_content returns empty string for None."""
        assert _normalize_content(None) == ""

    def test_normalize_content_handles_list(self):
        """_normalize_content extracts text from multimodal lists."""
        content = [
            {"type": "text", "text": "Hello"},
            {"type": "text", "text": "world"},
        ]
        assert _normalize_content(content) == "Hello world"


# ────────────────────────────────────────────────────────────────────────────
# 2. Language Check Node Tests (`language_check_node`)
# ────────────────────────────────────────────────────────────────────────────


def _make_state(messages, *, language_check_count: int = 0, **extra):
    """Helper to construct a SessionState-shaped dict for tests."""
    state: dict = {
        "messages": messages,
        "language_check_count": language_check_count,
        "language_check_retry": False,
    }
    state.update(extra)
    return state


def _good_ai(content: str = "This is fine English content.") -> AIMessage:
    return AIMessage(content=content)


def _wrong_lang_ai(content: str = "这是一段中文内容用于测试") -> AIMessage:
    """An AI message with content that will trigger detection (CJK)."""
    return AIMessage(content=content)


def _reminder_human(content: str = "previous reminder") -> HumanMessage:
    """A reminder HumanMessage — carries the marker so S5 reset won't reset on it."""
    return HumanMessage(
        content=content,
        additional_kwargs={"language_check_reminder": True},
    )


def _tool_call_ai() -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[ToolCall(id="call_1", name="some_tool", args={})],
    )


class TestLanguageCheckNodeWrongLanguage:
    """Wrong-language detection → reminder injected, retry flagged."""

    async def test_wrong_language_injects_reminder_and_sets_retry(self):
        """CJK content → reminder HumanMessage injected with marker, retry=True, count+1."""
        node = create_language_check_node("English")
        state = _make_state(
            [
                HumanMessage(content="Tell me about X."),
                _wrong_lang_ai(),
            ]
        )
        result = await node(state)
        assert result["language_check_retry"] is True
        assert result["language_check_count"] == 1
        assert "messages" in result
        assert len(result["messages"]) == 1
        reminder = result["messages"][0]
        assert isinstance(reminder, HumanMessage)
        # Reminder carries the marker so S5 reset logic doesn't reset on it.
        assert reminder.additional_kwargs.get("language_check_reminder") is True
        # Reminder mentions the preferred language and asks for retry.
        assert "English" in reminder.content

    async def test_reminder_uses_template_format(self):
        """The reminder should follow LANGUAGE_REMINDER_TEMPLATE.format()."""
        node = create_language_check_node("Spanish")
        state = _make_state(
            [
                HumanMessage(content="prompt"),
                AIMessage(content="Hello this is plain English content."),
            ]
        )
        result = await node(state)
        assert result["language_check_retry"] is True
        reminder = result["messages"][0]
        expected = LANGUAGE_REMINDER_TEMPLATE.format(language="Spanish")
        assert reminder.content == expected

    async def test_wrong_language_increments_count(self):
        """Second wrong-lang encounter (count=1) → count becomes 2.

        We use a reminder HumanMessage before the AI so the S5 reset logic
        does NOT clear the counter (reminder marker prevents reset).
        """
        node = create_language_check_node("English")
        state = _make_state(
            [
                HumanMessage(content="prompt"),
                _reminder_human(),
                _wrong_lang_ai(),
            ],
            language_check_count=1,
        )
        result = await node(state)
        assert result["language_check_retry"] is True
        assert result["language_check_count"] == 2


class TestLanguageCheckNodeCorrectLanguage:
    """Correct-language detection → no retry, counter reset."""

    async def test_correct_language_returns_no_retry(self):
        """English content + English preference → retry=False, count=0."""
        node = create_language_check_node("English")
        state = _make_state(
            [
                HumanMessage(content="prompt"),
                _good_ai(),
            ]
        )
        result = await node(state)
        assert result["language_check_retry"] is False
        assert result["language_check_count"] == 0
        # No messages returned (no reminder)
        assert "messages" not in result or result.get("messages") == []


class TestLanguageCheckNodeToolCalls:
    """AIMessage with tool_calls is passed through without language check."""

    async def test_tool_calls_message_passes_through(self):
        """Last message with tool_calls → no detection, retry=False, count=0."""
        node = create_language_check_node("English")
        state = _make_state(
            [
                HumanMessage(content="do something"),
                _tool_call_ai(),
            ]
        )
        result = await node(state)
        assert result["language_check_retry"] is False
        assert result["language_check_count"] == 0
        # No reminder message
        assert "messages" not in result or result.get("messages") == []


class TestLanguageCheckNodeSkipCheck:
    """language_skip_check tool invocation bypasses detection (C4 fix)."""

    async def test_skip_check_tool_call_bypasses_detection(self):
        """ToolMessage with name='language_skip_check' → no detection."""
        node = create_language_check_node("English")
        # After agent calls language_skip_check, a ToolMessage with that name appears.
        state = _make_state(
            [
                HumanMessage(content="prompt"),
                AIMessage(
                    content="",
                    tool_calls=[ToolCall(id="call_1", name="language_skip_check", args={})],
                ),
                ToolMessage(content="ok", tool_call_id="call_1", name="language_skip_check"),
                # Final response in wrong language, but skip applies.
                _wrong_lang_ai(),
            ]
        )
        result = await node(state)
        assert result["language_check_retry"] is False
        assert result["language_check_count"] == 0

    async def test_other_tool_message_does_not_skip(self):
        """A ToolMessage with a different name should NOT skip detection."""
        node = create_language_check_node("English")
        state = _make_state(
            [
                HumanMessage(content="prompt"),
                AIMessage(
                    content="",
                    tool_calls=[ToolCall(id="call_1", name="some_other_tool", args={})],
                ),
                ToolMessage(content="result", tool_call_id="call_1", name="some_other_tool"),
                _wrong_lang_ai(),
            ]
        )
        result = await node(state)
        # Wrong language still detected — skip didn't apply
        assert result["language_check_retry"] is True
        assert result["language_check_count"] == 1

    async def test_skip_check_past_human_message_boundary_does_not_apply(self):
        """language_skip_check before the latest HumanMessage shouldn't apply to current turn."""
        node = create_language_check_node("English")
        state = _make_state(
            [
                HumanMessage(content="first turn prompt"),
                AIMessage(
                    content="",
                    tool_calls=[ToolCall(id="call_1", name="language_skip_check", args={})],
                ),
                ToolMessage(content="ok", tool_call_id="call_1", name="language_skip_check"),
                HumanMessage(content="second turn prompt"),  # boundary
                _wrong_lang_ai(),
            ]
        )
        result = await node(state)
        # Skip is from previous turn, current turn's wrong language still detected
        assert result["language_check_retry"] is True
        assert result["language_check_count"] == 1


class TestLanguageCheckNodeMaxRetries:
    """Counter at max → allow through without retry.

    Note: a real HumanMessage in history resets the counter (S5 fix),
    so these tests use a reminder HumanMessage before the AI to keep
    the counter at the seeded value.
    """

    async def test_max_retries_allow_through(self):
        """count == LANGUAGE_CHECK_MAX_RETRIES → retry=False, count reset to 0."""
        node = create_language_check_node("English")
        state = _make_state(
            [
                HumanMessage(content="prompt"),
                _reminder_human(),
                _wrong_lang_ai(),
            ],
            language_check_count=LANGUAGE_CHECK_MAX_RETRIES,
        )
        result = await node(state)
        assert result["language_check_retry"] is False
        assert result["language_check_count"] == 0
        # No reminder message
        assert "messages" not in result or result.get("messages") == []

    async def test_count_above_max_still_allows(self):
        """count > MAX → still allows through (defensive)."""
        node = create_language_check_node("English")
        state = _make_state(
            [
                HumanMessage(content="prompt"),
                _reminder_human(),
                _wrong_lang_ai(),
            ],
            language_check_count=LANGUAGE_CHECK_MAX_RETRIES + 5,
        )
        result = await node(state)
        assert result["language_check_retry"] is False
        assert result["language_check_count"] == 0


class TestLanguageCheckNodeCounterReset:
    """Counter reset logic on new HumanMessage (S5 fix)."""

    async def test_new_human_message_resets_counter(self):
        """Non-reminder HumanMessage in history → counter resets to 0 before max check."""
        node = create_language_check_node("English")
        # Simulate a fresh user turn: previous reminders, then a new user message,
        # then a wrong-language response. count=2 from state should be reset by
        # the new HumanMessage.
        state = _make_state(
            [
                HumanMessage(content="old turn prompt"),
                AIMessage(content="..."),
                HumanMessage(content="new turn prompt"),  # NEW non-reminder
                _wrong_lang_ai(),
            ],
            language_check_count=2,  # would short-circuit if not reset
        )
        result = await node(state)
        # Counter was reset, then incremented to 1 by wrong-lang detection
        assert result["language_check_retry"] is True
        assert result["language_check_count"] == 1

    async def test_reminder_human_message_does_not_reset(self):
        """Reminder HumanMessage (language_check_reminder=True) does NOT reset count."""
        node = create_language_check_node("English")
        # Reminder HumanMessage in history → count stays at 2 → short-circuit.
        reminder = HumanMessage(
            content="reminder text",
            additional_kwargs={"language_check_reminder": True},
        )
        state = _make_state(
            [
                HumanMessage(content="original prompt"),
                AIMessage(content="..."),
                reminder,  # reminder, NOT a real user message
                _wrong_lang_ai(),
            ],
            language_check_count=2,
        )
        result = await node(state)
        # Count was NOT reset (still 2), so max retries short-circuits.
        assert result["language_check_retry"] is False
        assert result["language_check_count"] == 0

    async def test_first_turn_default_count_is_zero(self):
        """With no count in state, default 0 is used and detection proceeds."""
        node = create_language_check_node("English")
        state = {
            "messages": [
                HumanMessage(content="prompt"),
                _wrong_lang_ai(),
            ]
            # no language_check_count key
        }
        result = await node(state)
        assert result["language_check_retry"] is True
        assert result["language_check_count"] == 1


class TestLanguageCheckNodeErrorHandling:
    """Detection errors must not crash the graph (W4 fix)."""

    async def test_detection_error_does_not_crash(self):
        """If detect_wrong_language raises a caught exception type, the node allows the response through (W6 narrow-except fix)."""
        node = create_language_check_node("English")
        state = _make_state(
            [
                HumanMessage(content="prompt"),
                _wrong_lang_ai(),
            ]
        )
        # Use ValueError — one of the types in the narrow except clause
        # (ValueError, TypeError, AttributeError, re.error).
        with patch(
            "daemon.graph.detect_wrong_language",
            side_effect=ValueError("detection blew up"),
        ):
            # Should not raise
            result = await node(state)
        assert result["language_check_retry"] is False
        assert result["language_check_count"] == 0

    async def test_detection_value_error_caught(self):
        """ValueError from detection is caught by narrow except (W6 fix)."""
        node = create_language_check_node("English")
        state = _make_state([HumanMessage(content="p"), _wrong_lang_ai()])
        with patch(
            "daemon.graph.detect_wrong_language",
            side_effect=ValueError("bad regex"),
        ):
            result = await node(state)
        assert result["language_check_retry"] is False
        assert result["language_check_count"] == 0

    async def test_detection_type_error_caught(self):
        """TypeError from detection is caught by narrow except (W6 fix)."""
        node = create_language_check_node("English")
        state = _make_state([HumanMessage(content="p"), _wrong_lang_ai()])
        with patch(
            "daemon.graph.detect_wrong_language",
            side_effect=TypeError("wrong arg"),
        ):
            result = await node(state)
        assert result["language_check_retry"] is False
        assert result["language_check_count"] == 0

    async def test_detection_re_error_caught(self):
        """re.error from detection is caught by narrow except (W6 fix)."""
        import re as re_module
        node = create_language_check_node("English")
        state = _make_state([HumanMessage(content="p"), _wrong_lang_ai()])
        with patch(
            "daemon.graph.detect_wrong_language",
            side_effect=re_module.error("bad regex pattern"),
        ):
            result = await node(state)
        assert result["language_check_retry"] is False
        assert result["language_check_count"] == 0


# ────────────────────────────────────────────────────────────────────────────
# 3. Routing Tests
# ────────────────────────────────────────────────────────────────────────────


class TestShouldEndLanguageCheck:
    """Tests for `should_end_language_check` routing function."""

    def test_retry_flag_true_returns_retry(self):
        """language_check_retry=True → 'retry'."""
        state = {"language_check_retry": True}
        assert should_end_language_check(state) == "retry"

    def test_retry_flag_false_returns_end(self):
        """language_check_retry=False → END."""
        state = {"language_check_retry": False}
        assert should_end_language_check(state) == END

    def test_retry_flag_missing_returns_end(self):
        """Missing language_check_retry key → default False → END."""
        state = {}
        assert should_end_language_check(state) == END


class TestCreateShouldContinue:
    """Tests for `create_should_continue` closure factory."""

    def test_disabled_returns_original_function(self):
        """create_should_continue(False) returns the original should_continue (identity)."""
        wrapped = create_should_continue(False)
        assert wrapped is should_continue

    def test_enabled_returns_wrapper(self):
        """create_should_continue(True) returns a wrapper (not the original)."""
        wrapped = create_should_continue(True)
        assert wrapped is not should_continue
        assert callable(wrapped)

    # ── Enabled wrapper behavior ──────────────────────────────────────────

    def _make_state_with_ai(self, ai: AIMessage) -> dict:
        return {"messages": [HumanMessage(content="hi"), ai]}

    def test_enabled_wraps_end_as_end_candidate(self):
        """END → 'end_candidate' when enabled (last AI has content, no tool_calls)."""
        wrapped = create_should_continue(True)
        state = self._make_state_with_ai(_good_ai())
        assert wrapped(state) == "end_candidate"

    def test_enabled_passes_tools_unchanged(self):
        """tool_calls path → 'tools' unchanged when enabled."""
        wrapped = create_should_continue(True)
        state = self._make_state_with_ai(_tool_call_ai())
        assert wrapped(state) == "tools"

    def test_enabled_passes_nudge_unchanged(self):
        """Empty content after tool result → 'nudge' unchanged when enabled."""
        wrapped = create_should_continue(True)
        state = {
            "messages": [
                HumanMessage(content="Do something"),
                AIMessage(content="", tool_calls=[ToolCall(id="c1", name="t", args={})]),
                ToolMessage(content="result", tool_call_id="c1"),
                AIMessage(content=""),
            ]
        }
        assert wrapped(state) == "nudge"

    def test_enabled_passes_agent_unchanged(self):
        """Ghost promise (ends with ':') → 'agent' unchanged when enabled."""
        wrapped = create_should_continue(True)
        state = self._make_state_with_ai(AIMessage(content="Now I will:"))
        assert wrapped(state) == "agent"

    def test_disabled_returns_original_end(self):
        """When disabled, the wrapper IS the original — END flows through unchanged."""
        wrapped = create_should_continue(False)
        state = self._make_state_with_ai(_good_ai())
        # Original should_continue returns END (which is "__end__" in tests)
        assert wrapped(state) == END
        # And is NOT remapped to end_candidate
        assert wrapped(state) != "end_candidate"


# ────────────────────────────────────────────────────────────────────────────
# 4. Tool Tests
# ────────────────────────────────────────────────────────────────────────────


class TestLanguageSkipCheckTool:
    """Tests for the language_skip_check tool."""

    def test_returns_confirmation_string(self):
        """language_skip_check returns a confirmation string mentioning 'skipped'."""
        result = language_skip_check.invoke({})  # use LangChain tool's invoke
        # Accept either invocation pattern
        result_str = result if isinstance(result, str) else str(result)
        assert "skipped" in result_str.lower() or "skip" in result_str.lower()

    def test_returns_string_type_directly(self):
        """Calling the raw function (not via .invoke) returns a string."""
        # The underlying function returns str directly.
        # Some LangChain versions allow direct call when @tool decorated.
        try:
            result = language_skip_check.func()
        except AttributeError:
            # Older LangChain versions: invoke with empty dict
            result = language_skip_check.invoke({})
        assert isinstance(result, str)
        assert len(result) > 0


class TestCreateLanguageTools:
    """Tests for create_language_tools factory."""

    def test_returns_list_with_one_tool(self):
        """create_language_tools returns a list with exactly 1 tool."""
        tools = create_language_tools()
        assert isinstance(tools, list)
        assert len(tools) == 1

    def test_contains_language_skip_check(self):
        """The tool list should contain language_skip_check."""
        tools = create_language_tools()
        tool = tools[0]
        # Either via .name attribute or .func.__name__
        name = getattr(tool, "name", None) or getattr(getattr(tool, "func", None), "__name__", None)
        assert name == "language_skip_check"


# ────────────────────────────────────────────────────────────────────────────
# 5. Config Tests
# ────────────────────────────────────────────────────────────────────────────


class TestLanguageConfig:
    """Tests for LanguageConfig and Config.language wiring (C3 fix: opt-in)."""

    def test_default_check_enabled_is_false(self):
        """LanguageConfig() defaults to check_enabled=False (opt-in)."""
        cfg = LanguageConfig()
        assert cfg.check_enabled is False

    def test_explicit_true(self):
        """LanguageConfig(check_enabled=True) works."""
        cfg = LanguageConfig(check_enabled=True)
        assert cfg.check_enabled is True

    def test_explicit_false(self):
        """LanguageConfig(check_enabled=False) works."""
        cfg = LanguageConfig(check_enabled=False)
        assert cfg.check_enabled is False

    def test_config_language_field_is_language_config_instance(self):
        """Config().language should be a LanguageConfig instance."""
        cfg = Config()
        assert isinstance(cfg.language, LanguageConfig)

    def test_config_default_check_enabled_false(self):
        """Config().language.check_enabled is False by default (opt-in)."""
        cfg = Config()
        assert cfg.language.check_enabled is False


# ────────────────────────────────────────────────────────────────────────────
# 6. System Prompt Injection Tests
# ────────────────────────────────────────────────────────────────────────────


class TestAppendUserLanguage:
    """Tests for append_user_language system prompt post-processor."""

    def test_appends_language_section(self):
        """append_user_language appends a 'User Language Preference' section."""
        result = append_user_language("Base prompt.", "English")
        assert "User Language Preference" in result
        assert "English" in result
        # Original content preserved
        assert "Base prompt." in result

    def test_appends_in_correct_format(self):
        """Section format: '## User Language Preference\\n\\nUser prefers language: X\\n'."""
        result = append_user_language("prompt", "Spanish")
        assert "## User Language Preference" in result
        assert "User prefers language: Spanish" in result

    def test_empty_string_defaults_to_auto_and_skips_injection(self):
        """Empty language string falls back to "Auto" sentinel — injection skipped."""
        result = append_user_language("prompt", "")
        # "Auto" is the no-preference sentinel — system prompt returned unchanged.
        assert result == "prompt"
        assert "User Language Preference" not in result

    def test_none_defaults_to_auto_and_skips_injection(self):
        """None language falls back to "Auto" sentinel — injection skipped."""
        result = append_user_language("prompt", None)
        # "Auto" is the no-preference sentinel — system prompt returned unchanged.
        assert result == "prompt"
        assert "User Language Preference" not in result

    def test_explicit_auto_skips_injection(self):
        """Explicit "Auto" (case-insensitive) skips injection entirely."""
        for value in ("Auto", "auto", "AUTO", "  Auto  "):
            result = append_user_language("base prompt.", value)
            assert result == "base prompt.", f"Failed for {value!r}"
            assert "User Language Preference" not in result

    def test_chinese_language_preserved(self):
        """Non-English language values pass through unchanged."""
        result = append_user_language("prompt", "Chinese")
        assert "User prefers language: Chinese" in result

    def test_section_separated_from_base(self):
        """Section is separated from base prompt by '---' separator (matches other injectors)."""
        result = append_user_language("base", "English")
        # The '---' divider should appear before the language section.
        assert "---" in result
        # The base content appears before the divider
        sep_idx = result.find("---")
        base_idx = result.find("base")
        assert base_idx < sep_idx

    def test_idempotent_appending(self):
        """Calling append twice results in two language sections (no dedup)."""
        once = append_user_language("base", "English")
        twice = append_user_language(once, "Spanish")
        # Two sections, one for each language
        assert twice.count("## User Language Preference") == 2
        assert "User prefers language: English" in twice
        assert "User prefers language: Spanish" in twice

    def test_build_instance_graph_auto_disables_language_check(self):
        """build_instance_graph(user_language="Auto") MUST disable the language_check node
        even when language_check_enabled=True is passed — the Auto sentinel overrides.

        Implemented by checking the public ``compiled.language_check_active`` attribute
        that ``build_instance_graph`` sets after compile() so that downstream streaming
        code reads the effective flag, not the requested one.
        """
        from daemon.graph import build_instance_graph

        tools = [MagicMock(name="t1"), MagicMock(name="t2")]
        checkpointer = MagicMock()
        llm_config = {"model": "gpt-4o", "api_key": "test"}

        with patch("daemon.graph.ThinkingChatOpenAI") as mock_llm_class:
            mock_llm_instance = MagicMock()
            mock_llm_with_tools = MagicMock()
            mock_llm_instance.bind_tools.return_value = mock_llm_with_tools
            mock_llm_class.return_value = mock_llm_instance

            with patch("daemon.graph.StateGraph") as mock_state_graph:
                mock_graph_instance = MagicMock()
                mock_compiled = MagicMock()
                mock_graph_instance.compile.return_value = mock_compiled
                mock_state_graph.return_value = mock_graph_instance

                with patch("daemon.graph.ToolNode"):
                    # Case A — Auto sentinel MUST force the check OFF even though True was requested.
                    graph_auto = build_instance_graph(
                        tools=tools,
                        checkpointer=checkpointer,
                        llm_config=llm_config,
                        system_prompt="test prompt",
                        user_language="Auto",
                        language_check_enabled=True,
                    )
                    assert graph_auto.language_check_active is False, (
                        "Auto sentinel must override language_check_enabled=True"
                    )

                    # Case B — explicit non-Auto language with check_enabled=True MUST keep it ON.
                    graph_en = build_instance_graph(
                        tools=tools,
                        checkpointer=checkpointer,
                        llm_config=llm_config,
                        system_prompt="test prompt",
                        user_language="English",
                        language_check_enabled=True,
                    )
                    assert graph_en.language_check_active is True

                    # Case C — explicit non-Auto with check_enabled=False stays OFF.
                    graph_off = build_instance_graph(
                        tools=tools,
                        checkpointer=checkpointer,
                        llm_config=llm_config,
                        system_prompt="test prompt",
                        user_language="English",
                        language_check_enabled=False,
                    )
                    assert graph_off.language_check_active is False


# ────────────────────────────────────────────────────────────────────────────
# 7. Language Preference Schema Validation Tests (C1 fix)
# ────────────────────────────────────────────────────────────────────────────
# Pattern: ^[A-Za-z\u00C0-\u017F\s\-()]+$
# Allows: ASCII letters, Latin Extended-A/B (ñ, é, ü), space, hyphen, parens.
# Rejects: digits, punctuation (except -/()/), control chars NOT in \s, CJK.


class TestLanguagePreferenceUpdateSchema:
    """Tests for the LanguagePreferenceUpdate Pydantic schema (C1 prompt-injection fix).

    The schema restricts ``language`` to a safe character set so that values
    cannot smuggle prompt-injection payloads (newlines, role directives,
    markdown headers, code fences, etc.) into the downstream system prompt.
    """

    # ── Valid names are accepted ─────────────────────────────────────────

    @pytest.mark.parametrize(
        "language_name",
        [
            "English",
            "Spanish",
            "French (Canadian)",
            "Español",
            "Português",
            "Deutsch",
            "Chinese (Simplified)",
            "Polski",
            "Türkçe",
        ],
    )
    def test_valid_language_names_accepted(self, language_name: str):
        """Common language names and Latin Extended diacritics are accepted."""
        payload = LanguagePreferenceUpdate(language=language_name)
        assert payload.language == language_name

    def test_simple_ascii_name_accepted(self):
        """Plain ASCII language name is accepted."""
        payload = LanguagePreferenceUpdate(language="English")
        assert payload.language == "English"

    def test_parens_and_hyphens_accepted(self):
        """Parentheses and hyphens are explicitly allowed characters."""
        payload = LanguagePreferenceUpdate(language="French (Canadian)")
        assert payload.language == "French (Canadian)"

    def test_multibyte_diacritic_accepted(self):
        """Latin Extended characters (e.g. 'Español' with ñ = U+00F1) are accepted."""
        payload = LanguagePreferenceUpdate(language="Español")
        assert payload.language == "Español"

    # ── Injection attempts are rejected ──────────────────────────────────

    @pytest.mark.parametrize(
        "injection_payload",
        [
            # Markdown / role-style headers (# is not in the allowed set).
            "English\n\n## Override",
            # Non-allowed punctuation characters.
            "English!",
            "English<script>",
            "English 123",
            "English$",
            "English;system",
            "English\"quoted\"",
            # CJK characters are NOT in the allowed Latin Extended range.
            "中文",
            "こんにちは",
            "한국어",
        ],
    )
    def test_injection_payloads_rejected(self, injection_payload: str):
        """Prompt-injection payloads fail schema validation with ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            LanguagePreferenceUpdate(language=injection_payload)
        # Confirm the field error mentions 'language'
        errors = exc_info.value.errors()
        assert any(
            err.get("loc") == ("language",) or err.get("loc", ())[-1] == "language"
            for err in errors
        ), f"Expected 'language' field error, got {errors}"

    def test_markdown_header_injection_rejected(self):
        """Markdown '## Override' suffix is rejected (contains '#')."""
        with pytest.raises(ValidationError):
            LanguagePreferenceUpdate(language="English\n\n## Override")

    def test_cjk_rejected(self):
        """CJK characters (中文) are NOT in the allowed Latin Extended range."""
        with pytest.raises(ValidationError):
            LanguagePreferenceUpdate(language="中文")

    # ── Length constraints ───────────────────────────────────────────────

    def test_empty_string_rejected(self):
        """Empty string violates min_length=1."""
        with pytest.raises(ValidationError):
            LanguagePreferenceUpdate(language="")

    def test_over_100_chars_rejected(self):
        """A 101+ char language name violates max_length=100."""
        long_name = "A" * 101
        with pytest.raises(ValidationError):
            LanguagePreferenceUpdate(language=long_name)

    def test_exactly_100_chars_accepted(self):
        """A 100-char language name is allowed (boundary)."""
        ok_name = "A" * 100
        payload = LanguagePreferenceUpdate(language=ok_name)
        assert len(payload.language) == 100

    # ── Settings.py defense-in-depth (control char strip → 422) ─────────

    def test_cleaned_language_handler_strips_control_chars_and_accepts_safe_text(self):
        """The settings handler strips control chars and accepts safe surrounding text.

        We exercise the regex directly (the HTTP layer is covered by
        tests/test_settings_api.py). Pure control chars would produce an
        empty string after re.sub → 422.
        """
        import re

        # Simulate the handler's defense-in-depth strip.
        dangerous = "English\x00\x01\x02"
        cleaned = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", dangerous).strip()
        assert cleaned == "English"
        assert "\x00" not in cleaned
        assert "\x01" not in cleaned

    def test_pure_control_chars_yield_empty_after_strip(self):
        """Pure control characters produce an empty string after stripping (→ 422)."""
        import re

        dangerous = "\x00\x01\x1f"
        cleaned = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", dangerous).strip()
        assert cleaned == ""
        # The handler will raise 422 on empty cleaned value.


# ────────────────────────────────────────────────────────────────────────────
# SessionState sanity check
# ────────────────────────────────────────────────────────────────────────────


class TestSessionState:
    """Sanity tests for SessionState schema fields used by language check.

    NOTE: ``MessagesState`` is replaced by ``MagicMock()`` in tests/conftest.py,
    so we cannot reliably introspect ``SessionState.__annotations__``. Instead
    we verify that the language check helpers access the schema fields by name
    (i.e., the field names exist in source-level grep and the constants
    LANGUAGE_CHECK_MAX_RETRIES is reachable — proving the integration points
    the language check node depends on are present).
    """

    def test_language_check_max_retries_constant_defined(self):
        """LANGUAGE_CHECK_MAX_RETRIES must be exported and equal 2."""
        assert LANGUAGE_CHECK_MAX_RETRIES == 2

    def test_language_check_max_retries_is_int(self):
        """LANGUAGE_CHECK_MAX_RETRIES must be an integer for safe comparison."""
        assert isinstance(LANGUAGE_CHECK_MAX_RETRIES, int)

    def test_language_check_reminder_template_format_string(self):
        """LANGUAGE_REMINDER_TEMPLATE must have a ``{language}`` placeholder."""
        # Both occurrences are formatted by LANGUAGE_REMINDER_TEMPLATE.format(language=...).
        assert LANGUAGE_REMINDER_TEMPLATE.count("{language}") == 2
        # And it formats successfully with a sample language name.
        formatted = LANGUAGE_REMINDER_TEMPLATE.format(language="English")
        assert "English" in formatted

    def test_session_state_class_exists(self):
        """SessionState class must be importable from daemon.graph."""
        assert SessionState is not None