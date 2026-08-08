"""Unit tests for unhappy-path report repair in :class:`ChildReportsService`.

Covers:
    1. ``_is_likely_truncated_report`` — heuristic detection of truncated
       reports (size-ratio word-count check).
    2. ``_repair_report_with_llm`` — LLM call to compose a repaired report.
    3. ``_combine_messages`` — fallback combiner.
    4. ``_get_last_assistant_message_raw`` — end-to-end integration of the
       three stages (happy path, unhappy path with LLM success, timeout
       fallback, exception fallback, disabled, edge cases).

The service is constructed via ``__new__`` with a mock manager — mirrors the
pattern in ``tests/test_dependency_bus.py`` and ``tests/unit/test_root_instance_completion.py``.

The LLM is patched at ``daemon.services.child_reports.ThinkingChatOpenAI``
(the symbol imported in this module), NOT ``daemon.graph.ThinkingChatOpenAI``.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from daemon.config import ReportRepairConfig
from daemon.services.child_reports import ChildReportsService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service(
    *,
    messages: list[dict] | None = None,
    report_repair: ReportRepairConfig | None = None,
) -> ChildReportsService:
    """Build a ChildReportsService with a mock manager.

    The mock manager exposes:
      * ``config`` — a MagicMock with ``.report_repair`` and ``.llm``
      * ``_checkpointer`` — a MagicMock adapter with ``raw_saver`` set

    ``messages`` is the list ``get_instance_messages`` will return.

    Args:
        messages: Messages to return from the checkpointer. If None, an
            empty list is used (service falls back to the checkpointer mock).
        report_repair: The ReportRepairConfig to use. Defaults to enabled.

    Returns:
        A ChildReportsService with mocked dependencies.
    """
    from daemon.config import Config

    manager = MagicMock(name="InstanceManager")

    # Use a real Config if no override, so report_repair defaults are correct.
    if report_repair is not None:
        # Build a real config but replace report_repair
        config = Config()
        config.report_repair = report_repair
        manager.config = config
    else:
        manager.config = Config()

    # Set up the checkpointer mock so _checkpointer property returns something
    # non-None; get_instance_messages is patched at call sites.
    checkpointer_adapter = MagicMock(name="CheckpointerAdapter")
    checkpointer_adapter.raw_saver = MagicMock(name="RawSaver")
    manager._checkpointer = checkpointer_adapter

    service = ChildReportsService.__new__(ChildReportsService)
    service._manager = manager
    service._events_service = None
    return service


def _assistant_msg(content: str) -> dict:
    """Build a real assistant message dict."""
    return {"role": "assistant", "content": content}


def _user_msg(content: str) -> dict:
    """Build a user message dict (should be filtered out)."""
    return {"role": "user", "content": content}


def _make_llm_mock(text: str = "Repaired report content.") -> MagicMock:
    """Build a mock that mimics ThinkingChatOpenAI.

    The constructor returns a mock instance whose ``.invoke(messages)``
    returns a ``MagicMock(content=text)``.
    """
    llm_instance = MagicMock(name="LLMInstance")
    llm_instance.invoke = MagicMock(
        return_value=MagicMock(content=text)
    )
    llm_class = MagicMock(name="ThinkingChatOpenAI", return_value=llm_instance)
    return llm_class


def _make_slow_llm_mock(delay: float = 5.0) -> MagicMock:
    """Build a mock LLM whose .invoke sleeps (simulates timeout)."""
    def _slow_invoke(*args, **kwargs):
        import time
        time.sleep(delay)
        return MagicMock(content="should not reach here")

    llm_instance = MagicMock(name="SlowLLMInstance")
    llm_instance.invoke = MagicMock(side_effect=_slow_invoke)
    llm_class = MagicMock(name="ThinkingChatOpenAI", return_value=llm_instance)
    return llm_class


def _make_failing_llm_mock(exc: Exception = None) -> MagicMock:
    """Build a mock LLM whose .invoke raises."""
    if exc is None:
        exc = RuntimeError("LLM exploded")
    llm_instance = MagicMock(name="FailingLLMInstance")
    llm_instance.invoke = MagicMock(side_effect=exc)
    llm_class = MagicMock(name="ThinkingChatOpenAI", return_value=llm_instance)
    return llm_class


_LONG_1 = "word " * 50  # 50 words
_LONG_2 = "word " * 40  # 40 words
_SHORT = "done"  # 1 word
_MEDIUM = "word " * 10  # 10 words


# ---------------------------------------------------------------------------
# 1. _is_likely_truncated_report
# ---------------------------------------------------------------------------


class TestIsLikelyTruncatedReport:
    """Tests for ``ChildReportsService._is_likely_truncated_report``.

    W5 tuning (2026-08-08): default ratio 2.0→3.0; ``last_wc >= 5`` early-exit
    (5+ word last message is unlikely to be a truncation); ``earlier_wc >= 20``
    floor (tiny earlier messages don't false-positive).
    """

    def test_n_minus_1_much_larger_triggers(self):
        """n-1 (50 words) >> n (1 word): 50 > 3×1 → returns True."""
        messages = [
            _assistant_msg(_LONG_1),
            _assistant_msg(_SHORT),
        ]
        assert ChildReportsService._is_likely_truncated_report(messages) is True

    def test_n_minus_1_similar_does_not_trigger(self):
        """Two MEDIUM messages (10 words each): last_wc=10≥5 floor → False."""
        messages = [
            _assistant_msg(_MEDIUM),
            _assistant_msg(_MEDIUM),
        ]
        assert ChildReportsService._is_likely_truncated_report(messages) is False

    def test_n_minus_2_larger_triggers(self):
        """n-2 (50 words) >> n (1 word): 50 > 3×1 → returns True (n-1 is MEDIUM, below floor)."""
        messages = [
            _assistant_msg(_LONG_1),  # n-2: 50 words
            _assistant_msg(_MEDIUM),  # n-1: 10 words (<2× of 1 = 2)
            _assistant_msg(_SHORT),   # n: 1 word
        ]
        # n-2 (50) > 3× n (1) → True
        assert ChildReportsService._is_likely_truncated_report(messages) is True

    def test_exactly_2x_boundary_does_not_trigger(self):
        """last_wc=10≥5 floor → returns False (early exit before ratio check)."""
        last = "a " * 10  # 10 words
        prev = "a " * 20   # 20 words
        messages = [
            _assistant_msg(prev),
            _assistant_msg(last),
        ]
        assert ChildReportsService._is_likely_truncated_report(messages) is False

    def test_fewer_than_2_messages_returns_false(self):
        """Fewer than 2 messages → returns False."""
        messages = [_assistant_msg(_SHORT)]
        assert ChildReportsService._is_likely_truncated_report(messages) is False

    def test_empty_last_content_returns_true(self):
        """Empty last content → returns True (definitely truncated)."""
        messages = [
            _assistant_msg(_LONG_1),
            _assistant_msg(""),
        ]
        assert ChildReportsService._is_likely_truncated_report(messages) is True

    def test_whitespace_only_last_content_returns_true(self):
        """Whitespace-only last content → returns True."""
        messages = [
            _assistant_msg(_LONG_1),
            _assistant_msg("   \n\t  "),
        ]
        assert ChildReportsService._is_likely_truncated_report(messages) is True

    def test_empty_message_list_returns_false(self):
        """Empty list → returns False."""
        assert ChildReportsService._is_likely_truncated_report([]) is False

    def test_custom_ratio(self):
        """Custom ratio parameter is respected (with last_wc floor and earlier_wc floor)."""
        # last=1 word (passes last_wc<5), prev=25 words (passes earlier_wc>=20).
        last = "a"
        prev = "word " * 25
        messages = [_assistant_msg(prev), _assistant_msg(last)]
        # 25 > 30×1 → False (ratio too high for the early message)
        assert ChildReportsService._is_likely_truncated_report(messages, ratio=30.0) is False
        # 25 > 1.5×1 → True (ratio is well below 25)
        assert ChildReportsService._is_likely_truncated_report(messages, ratio=1.5) is True

    def test_last_wc_floor_blocks_legit_concise_last(self):
        """W5: last_wc >= 5 → return False (don't trigger on legitimately-concise last message)."""
        # last = 6 words (≥5 floor), prev = 30 words (>3×6).
        # Without the floor: 30 > 3×6 → True. With floor: False.
        last = "a " * 6
        prev = "word " * 30
        messages = [_assistant_msg(prev), _assistant_msg(last)]
        assert ChildReportsService._is_likely_truncated_report(messages) is False

    def test_earlier_wc_floor_blocks_tiny_earlier(self):
        """W5: earlier_wc < 20 → don't trigger (small earlier message is not 'substantive')."""
        # last = 1 word, prev = 15 words (≥1 but <20 floor).
        # Without earlier floor: 15 > 3×1 → True. With floor: False.
        last = "a"
        prev = "word " * 15
        messages = [_assistant_msg(prev), _assistant_msg(last)]
        assert ChildReportsService._is_likely_truncated_report(messages) is False


# ---------------------------------------------------------------------------
# 2. _combine_messages
# ---------------------------------------------------------------------------


class TestCombineMessages:
    """Tests for ``ChildReportsService._combine_messages``."""

    def test_combine_two_messages(self):
        messages = [_assistant_msg("first"), _assistant_msg("second")]
        result = ChildReportsService._combine_messages(messages)
        assert "first" in result
        assert "second" in result
        assert "---" in result

    def test_combine_skips_empty(self):
        messages = [_assistant_msg("first"), _assistant_msg(""), _assistant_msg("third")]
        result = ChildReportsService._combine_messages(messages)
        assert "first" in result
        assert "third" in result
        assert result.count("---") == 1  # only one separator between first and third

    def test_combine_empty_list(self):
        result = ChildReportsService._combine_messages([])
        assert result == ""

    def test_combine_all_empty(self):
        messages = [_assistant_msg(""), _assistant_msg("  ")]
        result = ChildReportsService._combine_messages(messages)
        assert result == ""


# ---------------------------------------------------------------------------
# 3. _repair_report_with_llm
# ---------------------------------------------------------------------------


class TestRepairReportWithLlm:
    """Tests for ``ChildReportsService._repair_report_with_llm``."""

    @pytest.mark.asyncio
    async def test_llm_success_returns_text(self):
        """LLM returns repaired text."""
        service = _make_service()
        messages = [
            _assistant_msg(_LONG_1),
            _assistant_msg(_LONG_2),
            _assistant_msg(_SHORT),
        ]
        cfg = ReportRepairConfig()

        mock_llm_class = _make_llm_mock("This is the repaired report.")
        with patch(
            "daemon.services.child_reports.ThinkingChatOpenAI",
            mock_llm_class,
        ):
            result = await service._repair_report_with_llm(
                messages, cfg, instance_id="test-instance-id"
            )

        assert result == "This is the repaired report."
        mock_llm_class.assert_called_once()

    @pytest.mark.asyncio
    async def test_llm_handles_list_content(self):
        """LLM returns multimodal list content → _extract_text_from_content extracts text."""
        service = _make_service()
        messages = [_assistant_msg(_LONG_1), _assistant_msg(_SHORT)]

        llm_instance = MagicMock()
        llm_instance.invoke = MagicMock(
            return_value=MagicMock(
                content=[{"type": "text", "text": "Extracted from list"}]
            )
        )
        mock_llm_class = MagicMock(return_value=llm_instance)
        with patch(
            "daemon.services.child_reports.ThinkingChatOpenAI",
            mock_llm_class,
        ):
            result = await service._repair_report_with_llm(
                messages, ReportRepairConfig(), instance_id="test-instance-id"
            )

        assert result == "Extracted from list"

    @pytest.mark.asyncio
    async def test_llm_timeout_returns_none(self):
        """LLM times out → returns None."""
        service = _make_service()
        messages = [_assistant_msg(_LONG_1), _assistant_msg(_SHORT)]

        cfg = ReportRepairConfig(timeout_seconds=0)  # 0s timeout → immediate

        mock_llm_class = _make_slow_llm_mock(delay=2.0)
        with patch(
            "daemon.services.child_reports.ThinkingChatOpenAI",
            mock_llm_class,
        ):
            result = await service._repair_report_with_llm(
                messages, cfg, instance_id="test-instance-id"
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_llm_exception_returns_none(self):
        """LLM raises → returns None."""
        service = _make_service()
        messages = [_assistant_msg(_LONG_1), _assistant_msg(_SHORT)]

        mock_llm_class = _make_failing_llm_mock(RuntimeError("boom"))
        with patch(
            "daemon.services.child_reports.ThinkingChatOpenAI",
            mock_llm_class,
        ):
            result = await service._repair_report_with_llm(
                messages, ReportRepairConfig(), instance_id="test-instance-id"
            )

        assert result is None


# ---------------------------------------------------------------------------
# 4. _get_last_assistant_message_raw — end-to-end
# ---------------------------------------------------------------------------


class TestGetLastAssistantMessageRaw:
    """Tests for the augmented ``_get_last_assistant_message_raw``."""

    @pytest.mark.asyncio
    async def test_happy_path_returns_last_message_no_llm(self):
        """All messages similar size → returns last message, LLM NOT called."""
        messages = [
            _user_msg("do the task"),
            _assistant_msg("working on it step one"),
            _assistant_msg("working on it step two"),
            _assistant_msg("I completed the task successfully."),
        ]
        service = _make_service(messages=messages)

        mock_llm_class = _make_llm_mock()
        with (
            patch("daemon.services.child_reports.get_instance_messages", new=AsyncMock(return_value=messages)),
            patch("daemon.services.child_reports.ThinkingChatOpenAI", mock_llm_class),
        ):
            result = await service._get_last_assistant_message_raw("test-instance-id")

        assert result == "I completed the task successfully."
        mock_llm_class.assert_not_called()

    @pytest.mark.asyncio
    async def test_disabled_returns_last_message_no_llm(self):
        """report_repair.enabled=False → returns last message, LLM NOT called."""
        # Even if messages would trigger the heuristic
        messages = [
            _assistant_msg(_LONG_1),
            _assistant_msg(_LONG_2),
            _assistant_msg(_SHORT),
        ]
        service = _make_service(report_repair=ReportRepairConfig(enabled=False))

        mock_llm_class = _make_llm_mock()
        with (
            patch("daemon.services.child_reports.get_instance_messages", new=AsyncMock(return_value=messages)),
            patch("daemon.services.child_reports.ThinkingChatOpenAI", mock_llm_class),
        ):
            result = await service._get_last_assistant_message_raw("test-instance-id")

        assert result == "done"  # the SHORT content
        mock_llm_class.assert_not_called()

    @pytest.mark.asyncio
    async def test_truncated_triggers_llm_repair_success(self):
        """Last message truncated → LLM repair succeeds → returns repaired text."""
        messages = [
            _assistant_msg(_LONG_1),
            _assistant_msg(_LONG_2),
            _assistant_msg(_SHORT),
        ]
        service = _make_service()

        mock_llm_class = _make_llm_mock("This is the full repaired report content.")
        with (
            patch("daemon.services.child_reports.get_instance_messages", new=AsyncMock(return_value=messages)),
            patch("daemon.services.child_reports.ThinkingChatOpenAI", mock_llm_class),
        ):
            result = await service._get_last_assistant_message_raw("test-instance-id")

        assert result == "This is the full repaired report content."
        mock_llm_class.assert_called_once()

    @pytest.mark.asyncio
    async def test_truncated_llm_timeout_falls_back_to_combine(self):
        """Last message truncated → LLM times out → combines messages."""
        # W5: earlier messages must be >=20 words to pass the earlier_wc floor.
        earlier_a = "alpha content " + "extra padding word " * 20  # ~25 words
        earlier_b = "beta content " + "extra padding word " * 20   # ~25 words
        messages = [
            _assistant_msg(earlier_a),
            _assistant_msg(earlier_b),
            _assistant_msg("done"),
        ]
        service = _make_service(report_repair=ReportRepairConfig(timeout_seconds=0))

        mock_llm_class = _make_slow_llm_mock(delay=2.0)
        with (
            patch("daemon.services.child_reports.get_instance_messages", new=AsyncMock(return_value=messages)),
            patch("daemon.services.child_reports.ThinkingChatOpenAI", mock_llm_class),
        ):
            result = await service._get_last_assistant_message_raw("test-instance-id")

        # Should be the combined messages
        assert "alpha content" in result
        assert "beta content" in result
        assert "done" in result

    @pytest.mark.asyncio
    async def test_truncated_llm_exception_falls_back_to_combine(self):
        """Last message truncated → LLM raises → combines messages."""
        # W5: earlier messages must be >=20 words to pass the earlier_wc floor.
        earlier_a = "alpha content " + "extra padding word " * 20  # ~25 words
        earlier_b = "beta content " + "extra padding word " * 20   # ~25 words
        messages = [
            _assistant_msg(earlier_a),
            _assistant_msg(earlier_b),
            _assistant_msg("done"),
        ]
        service = _make_service()

        mock_llm_class = _make_failing_llm_mock(RuntimeError("network error"))
        with (
            patch("daemon.services.child_reports.get_instance_messages", new=AsyncMock(return_value=messages)),
            patch("daemon.services.child_reports.ThinkingChatOpenAI", mock_llm_class),
        ):
            result = await service._get_last_assistant_message_raw("test-instance-id")

        assert "alpha content" in result
        assert "beta content" in result
        assert "done" in result

    @pytest.mark.asyncio
    async def test_no_messages_returns_none(self):
        """Empty message list → returns None."""
        service = _make_service()
        with patch(
            "daemon.services.child_reports.get_instance_messages",
            new=AsyncMock(return_value=[]),
        ):
            result = await service._get_last_assistant_message_raw("test-instance-id")
        assert result is None

    @pytest.mark.asyncio
    async def test_no_assistant_messages_returns_none(self):
        """All non-assistant messages → returns None."""
        messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "do task"},
        ]
        service = _make_service()
        with patch(
            "daemon.services.child_reports.get_instance_messages",
            new=AsyncMock(return_value=messages),
        ):
            result = await service._get_last_assistant_message_raw("test-instance-id")
        assert result is None

    @pytest.mark.asyncio
    async def test_synthetic_messages_filtered_returns_none(self):
        """Only synthetic/context_kind messages → returns None."""
        messages = [
            {"role": "assistant", "content": "synthetic", "is_synthetic": True},
            {"role": "assistant", "content": "context", "context_kind": "project"},
        ]
        service = _make_service()
        with patch(
            "daemon.services.child_reports.get_instance_messages",
            new=AsyncMock(return_value=messages),
        ):
            result = await service._get_last_assistant_message_raw("test-instance-id")
        assert result is None

    @pytest.mark.asyncio
    async def test_two_messages_truncated_triggers_repair(self):
        """Exactly 2 messages with truncation → LLM repair triggered."""
        messages = [
            _assistant_msg(_LONG_1),
            _assistant_msg(_SHORT),
        ]
        service = _make_service()

        mock_llm_class = _make_llm_mock("Repaired from two messages.")
        with (
            patch("daemon.services.child_reports.get_instance_messages", new=AsyncMock(return_value=messages)),
            patch("daemon.services.child_reports.ThinkingChatOpenAI", mock_llm_class),
        ):
            result = await service._get_last_assistant_message_raw("test-instance-id")

        assert result == "Repaired from two messages."
        mock_llm_class.assert_called_once()

    @pytest.mark.asyncio
    async def test_single_message_returns_it(self):
        """Only one assistant message → returns it (no truncation possible)."""
        messages = [_assistant_msg("only message")]
        service = _make_service()

        mock_llm_class = _make_llm_mock()
        with (
            patch("daemon.services.child_reports.get_instance_messages", new=AsyncMock(return_value=messages)),
            patch("daemon.services.child_reports.ThinkingChatOpenAI", mock_llm_class),
        ):
            result = await service._get_last_assistant_message_raw("test-instance-id")

        assert result == "only message"
        mock_llm_class.assert_not_called()

    @pytest.mark.asyncio
    async def test_combine_fallback_empty_falls_back_to_last(self):
        """Combine fallback produces empty → falls back to last_content."""
        # Edge case: recent messages all stripped to empty after .strip()
        # but they passed the initial filter, so combine returns "".
        messages = [
            {"role": "assistant", "content": "long " * 50},  # long
            {"role": "assistant", "content": "x"},  # 1 word
        ]
        service = _make_service()
        # Make LLM return empty string so we fall through to combine
        # But combine will have actual content, so test it differently:
        # Make _combine_messages return empty by mocking it.
        mock_llm_class = _make_llm_mock("")
        with (
            patch("daemon.services.child_reports.get_instance_messages", new=AsyncMock(return_value=messages)),
            patch("daemon.services.child_reports.ThinkingChatOpenAI", mock_llm_class),
            patch.object(ChildReportsService, "_combine_messages", return_value=""),
        ):
            result = await service._get_last_assistant_message_raw("test-instance-id")

        # LLM returned empty → combine returned empty → falls back to last_content
        assert result == "x"

    @pytest.mark.asyncio
    async def test_no_checkpointer_returns_none(self):
        """No checkpointer → messages = [] → returns None."""
        manager = MagicMock(name="InstanceManager")
        from daemon.config import Config
        manager.config = Config()
        # No _checkpointer set → adapter is None → _checkpointer returns None
        manager._checkpointer = None

        service = ChildReportsService.__new__(ChildReportsService)
        service._manager = manager
        service._events_service = None

        result = await service._get_last_assistant_message_raw("test-instance-id")
        assert result is None


# ---------------------------------------------------------------------------
# 5. W1 fix — n=2 indexing regression test
# ---------------------------------------------------------------------------


class TestN2IndexingRegression:
    """W1 (CRITICAL) regression: n=2 prompt must include BOTH messages.

    The pre-fix code used ``_get(1)`` for ``msg_n_minus_1`` and
    ``_get(n - 1)`` for ``msg_n``. When n=2, both resolved to index 1
    (the LAST message) — so the LLM saw the short sign-off twice and
    never saw the substantive earlier message. The fix switches to
    negative indexing (``_get(-2)`` / ``_get(-1)``).
    """

    @pytest.mark.asyncio
    async def test_n_2_prompt_includes_long_earlier_and_short_last(self):
        """n=2 prompt: msg_n_minus_1 = long earlier, msg_n = short last (not duplicated)."""
        service = _make_service()

        long_earlier = "word " * 50  # 50 words — the substantive earlier message
        short_last = "done"          # 1 word — the sign-off

        messages = [
            _assistant_msg(long_earlier),
            _assistant_msg(short_last),
        ]

        # Capture the messages array passed to LLM.invoke
        captured_llm_input: list[list] = []

        def _capture_invoke(llm_input):
            captured_llm_input.append(llm_input)
            return MagicMock(content="Repaired content")

        llm_instance = MagicMock()
        llm_instance.invoke = MagicMock(side_effect=_capture_invoke)
        mock_llm_class = MagicMock(return_value=llm_instance)

        with patch(
            "daemon.services.child_reports.ThinkingChatOpenAI",
            mock_llm_class,
        ):
            await service._repair_report_with_llm(
                messages, ReportRepairConfig(), instance_id="test-instance-id"
            )

        # Prompt is the HumanMessage (index 1 in [SystemMessage, HumanMessage]).
        assert len(captured_llm_input) == 1
        prompt_content = captured_llm_input[0][1].content

        # Both messages must appear in the prompt (NOT the short one duplicated).
        assert long_earlier in prompt_content, (
            "msg_n_minus_1 must contain the long earlier message — W1 fix"
        )
        assert short_last in prompt_content, "msg_n must contain the short last message"

        # Bug-detector: if msg_n_minus_1 == msg_n == "done", the prompt
        # would only have the short message and the long one would be missing.
        # Stronger assertion: the prompt must have TWO distinct message sections
        # containing the long message and "done" respectively.
        assert prompt_content.count("--- Message") == 3, (
            "prompt must include Message n_minus_2, n_minus_1, and n sections"
        )

    @pytest.mark.asyncio
    async def test_n_2_prompt_msg_n_minus_2_is_empty(self):
        """n=2 prompt: msg_n_minus_2 section is empty (only n-1 and n exist)."""
        service = _make_service()

        messages = [
            _assistant_msg(_LONG_1),
            _assistant_msg(_SHORT),
        ]

        captured_llm_input: list[list] = []

        def _capture_invoke(llm_input):
            captured_llm_input.append(llm_input)
            return MagicMock(content="Repaired content")

        llm_instance = MagicMock()
        llm_instance.invoke = MagicMock(side_effect=_capture_invoke)
        mock_llm_class = MagicMock(return_value=llm_instance)

        with patch(
            "daemon.services.child_reports.ThinkingChatOpenAI",
            mock_llm_class,
        ):
            await service._repair_report_with_llm(
                messages, ReportRepairConfig(), instance_id="test-instance-id"
            )

        prompt_content = captured_llm_input[0][1].content

        # Word count for n_minus_2 must be 0 (no such message exists)
        assert "(word count: 0)" in prompt_content, (
            "n_minus_2 word count must be 0 when n=2 (only 2 messages exist)"
        )

    @pytest.mark.asyncio
    async def test_n_3_prompt_indices_correct(self):
        """n=3 prompt: msg_n_minus_2 = first, msg_n_minus_1 = middle, msg_n = last."""
        service = _make_service()

        msg_a = "alpha " * 30  # 30 words (earliest)
        msg_b = "beta " * 25   # 25 words (middle)
        msg_c = "done"         # 1 word (last)

        messages = [_assistant_msg(msg_a), _assistant_msg(msg_b), _assistant_msg(msg_c)]

        captured_llm_input: list[list] = []

        def _capture_invoke(llm_input):
            captured_llm_input.append(llm_input)
            return MagicMock(content="Repaired content")

        llm_instance = MagicMock()
        llm_instance.invoke = MagicMock(side_effect=_capture_invoke)
        mock_llm_class = MagicMock(return_value=llm_instance)

        with patch(
            "daemon.services.child_reports.ThinkingChatOpenAI",
            mock_llm_class,
        ):
            await service._repair_report_with_llm(
                messages, ReportRepairConfig(), instance_id="test-instance-id"
            )

        prompt_content = captured_llm_input[0][1].content

        # All three distinct messages should be in the prompt, each in its slot.
        assert msg_a in prompt_content, "msg_n_minus_2 = first message (alpha)"
        assert msg_b in prompt_content, "msg_n_minus_1 = middle message (beta)"
        assert msg_c in prompt_content, "msg_n = last message (done)"

    @pytest.mark.asyncio
    async def test_n_1_prompt_msg_n_minus_1_and_2_empty(self):
        """n=1 prompt: msg_n_minus_1 and msg_n_minus_2 sections are empty."""
        service = _make_service()

        messages = [_assistant_msg(_SHORT)]  # just one message

        captured_llm_input: list[list] = []

        def _capture_invoke(llm_input):
            captured_llm_input.append(llm_input)
            return MagicMock(content="Repaired content")

        llm_instance = MagicMock()
        llm_instance.invoke = MagicMock(side_effect=_capture_invoke)
        mock_llm_class = MagicMock(return_value=llm_instance)

        with patch(
            "daemon.services.child_reports.ThinkingChatOpenAI",
            mock_llm_class,
        ):
            await service._repair_report_with_llm(
                messages, ReportRepairConfig(), instance_id="test-instance-id"
            )

        prompt_content = captured_llm_input[0][1].content

        # Two "(word count: 0)" entries for n_minus_1 and n_minus_2
        assert prompt_content.count("(word count: 0)") == 2


# ---------------------------------------------------------------------------
# 6. W3 fix — combined-report truncation
# ---------------------------------------------------------------------------


class TestCombineMessagesTruncation:
    """W3 fix: combined report caps at ``MAX_COMBINED_REPORT_CHARS`` chars."""

    def test_combine_under_cap_returns_full(self):
        """Combined length below cap → no truncation suffix."""
        small = "hello world"
        messages = [_assistant_msg(small), _assistant_msg(small)]
        result = ChildReportsService._combine_messages(messages)
        assert "hello world" in result
        assert "…[truncated]…" not in result

    def test_combine_at_cap_no_truncation(self):
        """Combined length exactly at cap → no truncation suffix."""
        # Build content that, joined with the "\n\n---\n\n" separator,
        # exactly equals MAX_COMBINED_REPORT_CHARS.
        # Use two messages that are exactly half the cap minus the separator length.
        cap = 10_000
        sep = "\n\n---\n\n"  # 7 chars
        each = "a" * ((cap - len(sep)) // 2)
        messages = [_assistant_msg(each), _assistant_msg(each)]
        result = ChildReportsService._combine_messages(messages)
        # Either no truncation or truncation depending on arithmetic; the
        # important invariant is that when length ≤ cap the suffix is absent.
        if len(result) <= cap:
            assert "…[truncated]…" not in result

    def test_combine_over_cap_truncates_with_suffix(self):
        """Combined length above cap → truncates with ``…[truncated]…`` suffix."""
        cap = 10_000
        big = "a" * (cap + 1000)
        messages = [_assistant_msg(big), _assistant_msg("end")]
        result = ChildReportsService._combine_messages(messages)
        assert len(result) == cap + len("…[truncated]…")
        assert result.endswith("…[truncated]…")
        # Beginning of big should still be in the result.
        assert result.startswith("a" * 100)

    def test_combine_max_cap_preserves_beginning(self):
        """Truncation preserves the BEGINNING of the combined content."""
        cap = 10_000
        # Single big message that overflows the cap by a lot.
        big = "x" * (cap * 2)
        messages = [_assistant_msg(big)]
        result = ChildReportsService._combine_messages(messages)
        # Beginning of big should still be in the result.
        assert result.startswith("x" * 100)
        # Truncation suffix appended.
        assert result.endswith("…[truncated]…")
        # Length is exactly cap + suffix length.
        assert len(result) == cap + len("…[truncated]…")


# ---------------------------------------------------------------------------
# 7. W4 fix — end-to-end persistence path
# ---------------------------------------------------------------------------


class TestEndToEndPersistence:
    """W4 fix: repaired content flows through to the parent-facing report string.

    The full ``_process_child_completion_and_notify_parent`` is heavyweight
    (touches many repositories/services). The pragmatic proxy is the
    wrapping step that turns ``_get_last_assistant_message_raw`` output
    into the final report string. The same string is what gets written
    into ``MessageQueue.content`` for the parent (which is what
    ``ReportInjection.content`` reads back).
    """

    @pytest.mark.asyncio
    async def test_repaired_content_reaches_parent_report_string(self):
        """Truncated child → parent report contains repaired content, not the short sign-off."""
        from daemon.config import Config

        long_1 = "word " * 50
        long_2 = "step " * 40
        short_signoff = "done"

        messages = [
            _assistant_msg(long_1),
            _assistant_msg(long_2),
            _assistant_msg(short_signoff),
        ]

        manager = MagicMock(name="InstanceManager")
        manager.config = Config()
        checkpointer_adapter = MagicMock()
        checkpointer_adapter.raw_saver = MagicMock()
        manager._checkpointer = checkpointer_adapter

        service = ChildReportsService.__new__(ChildReportsService)
        service._manager = manager
        service._events_service = None

        mock_llm_instance = MagicMock()
        mock_llm_instance.invoke = MagicMock(
            return_value=MagicMock(content="Repaired: full report content delivered.")
        )
        mock_llm_class = MagicMock(return_value=mock_llm_instance)

        with (
            patch(
                "daemon.services.child_reports.get_instance_messages",
                new=AsyncMock(return_value=messages),
            ),
            patch(
                "daemon.services.child_reports.ThinkingChatOpenAI",
                mock_llm_class,
            ),
        ):
            raw = await service._get_last_assistant_message_raw("child-instance-abc123")

            # The wrap step is what writes to MessageQueue.content (the
            # parent-facing persistence). Same path used by
            # _process_child_completion_and_notify_parent at line 1309.
            wrapped = await service._get_last_assistant_message(
                "child-instance-abc123", "worker"
            )

        # Raw repaired content (from LLM) is what flows to persistence.
        assert raw is not None
        assert raw != short_signoff
        assert raw == "Repaired: full report content delivered."

        # The wrapped form (prefix + raw) is what gets written to
        # MessageQueue.content → ReportInjection.content. Split on the
        # "below is the response:" separator to verify the post-prefix body
        # is the repaired text (the prefix contains the literal "done" in
        # "has done, below is the response:" so we can't check raw
        # substring inclusion directly).
        assert wrapped is not None
        assert "below is the response:" in wrapped
        body = wrapped.split("below is the response:", 1)[1].strip()
        assert body == "Repaired: full report content delivered.", (
            "wrapped report body must be the repaired content, not the raw "
            f"short sign-off (got: {body!r})"
        )

        # LLM was called at least once (twice because we call both raw +
        # wrapped in sequence — the wrapper calls raw internally).
        assert mock_llm_class.call_count >= 1

    @pytest.mark.asyncio
    async def test_combine_fallback_reaches_parent_report_string(self):
        """Truncated child + LLM fails → parent report contains combined content."""
        from daemon.config import Config

        # W5: earlier messages must be >=20 words to pass the earlier_wc floor.
        long_1 = "alpha detailed findings report " + "padding word " * 20  # ~25 words
        long_2 = "beta implementation details report " + "padding word " * 20  # ~25 words
        short_signoff = "ok"

        messages = [
            _assistant_msg(long_1),
            _assistant_msg(long_2),
            _assistant_msg(short_signoff),
        ]

        manager = MagicMock(name="InstanceManager")
        manager.config = Config()
        checkpointer_adapter = MagicMock()
        checkpointer_adapter.raw_saver = MagicMock()
        manager._checkpointer = checkpointer_adapter

        service = ChildReportsService.__new__(ChildReportsService)
        service._manager = manager
        service._events_service = None

        mock_llm_class = MagicMock(side_effect=RuntimeError("LLM unavailable"))

        with (
            patch(
                "daemon.services.child_reports.get_instance_messages",
                new=AsyncMock(return_value=messages),
            ),
            patch(
                "daemon.services.child_reports.ThinkingChatOpenAI",
                mock_llm_class,
            ),
        ):
            raw = await service._get_last_assistant_message_raw("child-instance-def456")
            wrapped = await service._get_last_assistant_message(
                "child-instance-def456", "worker"
            )

        # Raw is the combined content (LLM failed → fallback).
        # earlier_wc must be >= 20 for the heuristic to fire.
        assert raw is not None
        assert raw != short_signoff
        assert "alpha detailed findings" in raw
        assert "beta implementation details" in raw

        # Wrapped form (which is what gets persisted) contains the combined content.
        assert wrapped is not None
        assert "alpha detailed findings" in wrapped
        assert "beta implementation details" in wrapped


# ---------------------------------------------------------------------------
# 8. W5 / W2 config defaults
# ---------------------------------------------------------------------------


class TestConfigDefaults:
    """W5/W2 config default tests (ratio 3.0, timeout 30, lookback 3)."""

    def test_size_ratio_threshold_default_is_3(self):
        """W5: default size_ratio_threshold is 3.0 (was 2.0)."""
        cfg = ReportRepairConfig()
        assert cfg.size_ratio_threshold == 3.0

    def test_timeout_default_is_30(self):
        """W2: default timeout_seconds is 30 (was 120)."""
        cfg = ReportRepairConfig()
        assert cfg.timeout_seconds == 30

    def test_lookback_default_is_3(self):
        cfg = ReportRepairConfig()
        assert cfg.lookback_messages == 3

    def test_lookback_validator_rejects_zero(self):
        """S2: lookback_messages must be >= 1."""
        import pydantic
        with pytest.raises(pydantic.ValidationError):
            ReportRepairConfig(lookback_messages=0)

    def test_size_ratio_validator_rejects_below_one(self):
        """S2: size_ratio_threshold must be >= 1.0."""
        import pydantic
        with pytest.raises(pydantic.ValidationError):
            ReportRepairConfig(size_ratio_threshold=0.5)
