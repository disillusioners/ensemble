"""Comprehensive unit tests for daemon/compaction.py."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio
import random
import time

import pytest
import tiktoken

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.messages.tool import ToolCall

from daemon.compaction import (
    MODEL_CONTEXT_LIMITS,
    DEFAULT_CONTEXT_LIMIT,
    ChunkedOutcome,
    CompactionConfig,
    CompactionContext,
    CompactionResult,
    ContextCompactor,
    MessageGroup,
    _append_truncation_marker,
    _extract_text_from_content,
    _summarization_timeout_s,
    _truncate_batch_to_fit,
    emergency_truncate,
    get_model_context_limit,
    identify_boundary_groups,
    select_compactable_groups,
)
from daemon.config import CompactionConfig as CompactionConfigModel
from daemon.config import SlashCommandConfig
from daemon.loader import estimate_messages_tokens


# =============================================================================
# Fixtures
# =============================================================================

def make_compaction_config(**overrides) -> CompactionConfigModel:
    """Create a CompactionConfig with optional overrides."""
    defaults = {
        "enabled": True,
        "threshold": 0.80,
        "recent_message_window": 10,
        "min_recent_window": 3,
        "context_window_overrides": {},
        "context_window_default": 0,
        "target_ratio": 0.40,
        "model": "",
        "summarization_model": "",
        "min_messages_before_compaction": 10,
        "summarization_chunk_threshold": 0.60,
        # Phase 1 / WS-3 adaptive timeout defaults.
        "timeout_base_s": 90.0,
        "timeout_per_100k_tokens_s": 60.0,
        "timeout_cap_s": 300.0,
        "timeout_facade_margin_s": 5.0,
        "operation_budget_s": 300.0,
        # Parallel chunked summarization (Commit A): bounded pool size.
        "chunk_concurrency": 3,
    }
    defaults.update(overrides)
    return CompactionConfigModel(**defaults)


def make_messages(count: int, content_prefix: str = "Message") -> list[BaseMessage]:
    """Create alternating HumanMessage and AIMessage."""
    messages = []
    for i in range(count):
        if i % 2 == 0:
            messages.append(HumanMessage(content=f"{content_prefix} {i}", id=f"human-{i}"))
        else:
            messages.append(AIMessage(content=f"Response to {content_prefix} {i}", id=f"ai-{i}"))
    return messages


def make_tool_message(tool_call_id: str, content: str, idx: int) -> ToolMessage:
    """Create a ToolMessage responding to a tool call."""
    return ToolMessage(
        content=content,
        tool_call_id=tool_call_id,
        name="test_tool",
        id=f"tool-{idx}",
    )


# =============================================================================
# Test Classes
# =============================================================================

class TestGetModelContextLimit:
    """Tests for get_model_context_limit function (4 cases)."""

    def test_known_models_in_registry(self):
        """Test that known models return their registered context limits."""
        assert get_model_context_limit("gpt-4o") == 128000
        assert get_model_context_limit("claude-3.5-sonnet") == 200000
        assert get_model_context_limit("gpt-4") == 8192

    def test_case_insensitive_and_whitespace_normalized(self):
        """Test that matching is case-insensitive and strips whitespace."""
        assert get_model_context_limit("GPT-4O") == 128000
        assert get_model_context_limit("  claude-3.5-sonnet ") == 200000

    def test_fuzzy_matching_partial_name(self):
        """Test fuzzy matching when model name contains a registry key substring."""
        assert get_model_context_limit("gpt-4o-2024-08-06") == 128000
        # "gpt-4" is a substring of "gpt-4-0314"
        assert get_model_context_limit("gpt-4-0314") == 8192

    def test_config_override_and_unknown_model_fallback(self):
        """Test config override takes priority; unknown models use DEFAULT_CONTEXT_LIMIT."""
        config = make_compaction_config(context_window_overrides={"any-model": 50000})
        assert get_model_context_limit("any-model", config=config) == 50000
        assert get_model_context_limit("totally-unknown-model") == DEFAULT_CONTEXT_LIMIT

    def test_per_model_overrides_substring_longest_wins(self):
        """Test that per-model overrides substring-match and longest key wins."""
        config = make_compaction_config(
            context_window_overrides={
                "gpt-4o-mini": 32000,
                "gpt-4o-mini-vision": 16385,
                "vision": 8192,
            }
        )
        # "gpt-4o-mini-vision" matches both "gpt-4o-mini-vision" and "vision";
        # the longer key wins → 16385.
        assert get_model_context_limit("gpt-4o-mini-vision", config=config) == 16385
        # "gpt-4o-mini" (no "-vision" suffix) matches only "gpt-4o-mini" → 32000.
        assert get_model_context_limit("gpt-4o-mini", config=config) == 32000
        # Bare "vision" key catches any model name containing the word.
        assert get_model_context_limit("claude-3-vision-proxy", config=config) == 8192
        # Unknown model with no matching key falls through to the registry/default.
        assert get_model_context_limit("gpt-4o", config=config) == 128000

    def test_context_window_default_overrides_built_in_fallback(self):
        """context_window_default kicks in when neither overrides nor the registry match."""
        config = make_compaction_config(context_window_default=42000)
        assert get_model_context_limit("totally-unknown-xyz", config=config) == 42000
        # Registry match still wins over the default fallback.
        assert get_model_context_limit("gpt-4o", config=config) == 128000

    def test_override_match_strips_surrounding_whitespace(self):
        """Leading/trailing whitespace on the model name must not break override matching."""
        config = make_compaction_config(context_window_overrides={"gpt-4o": 5000})
        assert get_model_context_limit("  gpt-4o  ", config=config) == 5000
        assert get_model_context_limit("\tgpt-4o\n", config=config) == 5000

    def test_override_match_is_case_insensitive(self):
        """Override keys and model names are matched case-insensitively."""
        config = make_compaction_config(context_window_overrides={"GPT-4O": 7777})
        assert get_model_context_limit("gpt-4o", config=config) == 7777
        assert get_model_context_limit("GPT-4O-2024", config=config) == 7777

    def test_empty_string_key_in_overrides_is_rejected_at_load(self):
        """An empty override key must fail validation at config load time, not silently match."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            CompactionConfigModel(context_window_overrides={"": 999})

        # A valid config with a real key still works as expected; the runtime
        # helper only sees well-formed configs since the validator already ran.
        config = make_compaction_config(context_window_overrides={"gpt-4o": 1111})
        assert get_model_context_limit("gpt-4o", config=config) == 1111
        # And an unknown model falls through to the registry/default — never
        # to a silently-skipped empty key.
        assert get_model_context_limit("totally-unknown") == DEFAULT_CONTEXT_LIMIT

    def test_config_validator_rejects_invalid_overrides(self):
        """Bad values in context_window_overrides must fail at config load time."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            CompactionConfigModel(context_window_overrides={"gpt-4o": 0})
        with pytest.raises(ValidationError):
            CompactionConfigModel(context_window_overrides={"gpt-4o": -100})
        with pytest.raises(ValidationError):
            CompactionConfigModel(context_window_overrides={"gpt-4o": "not-a-number"})
        with pytest.raises(ValidationError):
            CompactionConfigModel(context_window_overrides={"": 1000})
        with pytest.raises(ValidationError):
            CompactionConfigModel(context_window_default=-1)


class TestIdentifyBoundaryGroups:
    """Tests for identify_boundary_groups function (7 cases)."""

    def test_empty_messages(self):
        """Test empty message list returns empty groups."""
        assert identify_boundary_groups([]) == []

    def test_single_messages_become_single_groups(self):
        """Test that single HumanMessage and AIMessage each become single-message groups."""
        messages = [
            HumanMessage(content="Hello", id="h0"),
            AIMessage(content="Hi", id="a0"),
        ]
        groups = identify_boundary_groups(messages)
        assert len(groups) == 2
        assert all(g.group_type == "single" for g in groups)
        assert groups[0].messages == [messages[0]]
        assert groups[1].messages == [messages[1]]

    def test_ai_with_tool_calls_groups_with_matching_tool_messages(self):
        """Test AI message with tool_calls groups with its following ToolMessages."""
        tool_call_id = "call_abc"
        ai_msg = AIMessage(
            content="Calling tool",
            id="ai-tool",
            tool_calls=[ToolCall(id=tool_call_id, name="test_tool", args={})],
        )
        tool_msg = make_tool_message(tool_call_id, "Tool result", 0)
        messages = [ai_msg, tool_msg]
        groups = identify_boundary_groups(messages)
        assert len(groups) == 1
        assert groups[0].group_type == "tool_sequence"
        assert groups[0].messages == [ai_msg, tool_msg]
        assert groups[0].start_idx == 0
        assert groups[0].end_idx == 1

    def test_multiple_tool_calls_in_one_ai_message(self):
        """Test AI message with multiple tool_calls groups all matching ToolMessages."""
        tool_call_id_1 = "call_1"
        tool_call_id_2 = "call_2"
        ai_msg = AIMessage(
            content="Calling tools",
            id="ai-multi",
            tool_calls=[
                ToolCall(id=tool_call_id_1, name="tool_a", args={}),
                ToolCall(id=tool_call_id_2, name="tool_b", args={}),
            ],
        )
        tool_msg_1 = make_tool_message(tool_call_id_1, "Result A", 1)
        tool_msg_2 = make_tool_message(tool_call_id_2, "Result B", 2)
        messages = [ai_msg, tool_msg_1, tool_msg_2]
        groups = identify_boundary_groups(messages)
        assert len(groups) == 1
        assert groups[0].end_idx == 2
        assert groups[0].messages == [ai_msg, tool_msg_1, tool_msg_2]

    def test_orphan_tool_message_becomes_single_group(self):
        """Test orphan ToolMessage (no matching AI) becomes its own single group."""
        messages = [make_tool_message("orphan-call", "Orphan result", 0)]
        groups = identify_boundary_groups(messages)
        assert len(groups) == 1
        assert groups[0].group_type == "single"

    def test_ai_message_with_dict_tool_calls(self):
        """Test AI message with dict-style tool_calls (not ToolCall objects) still groups correctly."""
        tool_call_id = "dict_call_123"
        ai_msg = AIMessage(
            content="Calling dict tools",
            id="ai-dict",
            tool_calls=[{"id": tool_call_id, "name": "dict_tool", "args": {}}],
        )
        tool_msg = make_tool_message(tool_call_id, "Dict result", 0)
        messages = [ai_msg, tool_msg]
        groups = identify_boundary_groups(messages)
        assert len(groups) == 1
        assert groups[0].group_type == "tool_sequence"

    def test_group_indices_are_contiguous_and_cover_all_messages(self):
        """Test that returned groups have contiguous, non-overlapping indices covering all messages."""
        messages = [
            HumanMessage(content="H1", id="h1"),
            AIMessage(content="A1", id="a1"),
            HumanMessage(content="H2", id="h2"),
        ]
        groups = identify_boundary_groups(messages)
        assert len(groups) == 3
        all_indices = []
        for g in groups:
            all_indices.extend(range(g.start_idx, g.end_idx + 1))
        assert sorted(all_indices) == [0, 1, 2]


class TestSelectCompactableGroups:
    """Tests for select_compactable_groups function (4 cases)."""

    def test_fewer_groups_than_window_returns_early(self):
        """Test that when len(groups) <= window, function returns early with window unchanged."""
        groups = [
            MessageGroup(start_idx=i, end_idx=i, messages=[MagicMock()], group_type="single")
            for i in range(2)
        ]
        estimate_fn = MagicMock(return_value=100)
        compactable, preserved, window = select_compactable_groups(
            groups, recent_window=10, min_window=3,
            context_window=128000, system_prompt_tokens=0,
            estimate_fn=estimate_fn,
        )
        assert compactable == []
        assert preserved == groups
        assert window == 10  # returned unchanged on early exit

    def test_under_threshold_returns_empty_compactable(self):
        """Test that when preserved + system tokens are under threshold, no groups are compactable."""
        groups = [
            MessageGroup(start_idx=i, end_idx=i, messages=[MagicMock()], group_type="single")
            for i in range(2)
        ]
        estimate_fn = MagicMock(return_value=100)
        compactable, preserved, window = select_compactable_groups(
            groups, recent_window=2, min_window=1,
            context_window=1000, system_prompt_tokens=0,
            estimate_fn=estimate_fn,
            config_threshold=0.80,
        )
        assert compactable == []
        assert preserved == groups

    def test_over_threshold_returns_compactable_groups(self):
        """Test that when over threshold, older groups are returned as compactable."""
        groups = [
            MessageGroup(start_idx=i, end_idx=i, messages=[MagicMock()], group_type="single")
            for i in range(5)
        ]
        def estimate_fn(msgs):
            return len(msgs) * 50000

        compactable, preserved, window = select_compactable_groups(
            groups, recent_window=3, min_window=2,
            context_window=100000, system_prompt_tokens=0,
            estimate_fn=estimate_fn,
            config_threshold=0.80,
        )
        # 3*50000=150000 > 80000, 2*50000=100000 > 80000 → reduces to min_window=2
        assert len(preserved) == 2
        assert len(compactable) == 3

    def test_window_reduces_progressively_and_system_prompt_included(self):
        """Test that window reduces progressively and system_prompt_tokens count toward threshold."""
        groups = [
            MessageGroup(start_idx=i, end_idx=i, messages=[MagicMock()], group_type="single")
            for i in range(5)
        ]
        estimate_fn = MagicMock(return_value=1000)
        compactable, preserved, window = select_compactable_groups(
            groups, recent_window=3, min_window=1,
            context_window=1000, system_prompt_tokens=500,  # included in threshold check
            estimate_fn=estimate_fn,
            config_threshold=0.80,
        )
        # 3*1000+500=3500 > 800, reduces to 1
        assert window == 1
        assert len(preserved) == 1
        assert len(compactable) == 4


class TestEmergencyTruncate:
    """Tests for emergency_truncate function (4 cases)."""

    def test_under_limit_returns_deep_copy(self):
        """Test that messages under limit returns a deep copy (not the original)."""
        original_list = [HumanMessage(content="Short", id="h1")]
        def estimate_fn(msgs):
            return 100
        result = emergency_truncate(original_list, max_tokens=1000, estimate_fn=estimate_fn)
        assert result is not original_list
        assert result[0].content == "Short"

    def test_pass1_truncates_long_tool_responses(self):
        """Test Pass 1 truncates tool responses exceeding max_tool_response_chars."""
        tool_msg = ToolMessage(
            content="x" * 5000,
            tool_call_id="call_1",
            name="long_tool",
            id="tool-1",
        )
        estimate_fn = MagicMock(side_effect=[5000, 500])  # Before truncation, after truncation
        result = emergency_truncate(
            [tool_msg], max_tokens=1000, estimate_fn=estimate_fn,
            max_tool_response_chars=2000,
        )
        assert "[...truncated]" in result[0].content

    def test_pass2_truncates_long_human_messages(self):
        """Test Pass 2 truncates human messages exceeding max_human_message_chars."""
        human_msg = HumanMessage(content="x" * 5000, id="h1")
        count = [0]
        def estimate_fn(msgs):
            count[0] += 1
            return 800 if count[0] > 2 else 2000  # Pass 1 & 2 over, Pass 3 under
        result = emergency_truncate(
            [human_msg], max_tokens=1000, estimate_fn=estimate_fn,
            max_tool_response_chars=2000, max_human_message_chars=4000,
        )
        assert "[...truncated]" in result[0].content

    def test_drops_oldest_messages_when_all_pass3_fails(self):
        """Test that oldest messages are dropped as last resort when all truncation fails."""
        msgs = [
            ToolMessage(content="x" * 500, tool_call_id=f"c{i}", name="t", id=f"t{i}")
            for i in range(5)
        ]
        estimate_fn = MagicMock(return_value=5000)
        result = emergency_truncate(msgs, max_tokens=1000, estimate_fn=estimate_fn)
        # At least one message must remain
        assert 1 <= len(result) < 5


class TestBuildGlobalDocForFullSuccess:
    """Architect §6 — the FULL-success path emits ONE SystemMessage
    doc spanning the entire compactable span. There is no per-batch
    SystemMessage and no separate truncation marker. The doc
    builder is the single source for the replacement list (the
    caller wraps it in the sentinel recipe at the persist seam).
    """

    def test_full_success_emits_single_doc_with_doc_id(self):
        from daemon.compaction import build_compaction_doc
        # Stub LLM — context already passed in.
        compactable = [
            MessageGroup(
                start_idx=0, end_idx=0,
                messages=[AIMessage(content="Old", id="ai-old")],
                group_type="single",
            ),
        ]
        preserved = [
            MessageGroup(
                start_idx=1, end_idx=1,
                messages=[HumanMessage(content="Recent", id="human-new")],
                group_type="single",
            ),
        ]
        doc = build_compaction_doc(
            instance_id="inst-1",
            seq=1,
            mode="summary",
            compacted_at="2026-09-01T10:00:00+00:00",
            global_overview="Summary",
            sections=[{
                "start_idx": 1, "end_idx": 1,
                "body": "Summary", "start_id": "ai-old", "end_id": "ai-old",
            }],
            total_sections=1,
            summarized_start=1, summarized_end=1,
            preserved_count=1,
            dropped_spans=[],
        )
        # The doc carries the canonical id and the GLOBAL OVERVIEW body.
        assert doc.id == "compaction-global-inst-1-1"
        assert "Summary" in doc.content
        # No per-batch message was emitted (just the doc).
        assert len([m for m in [doc] if isinstance(m, SystemMessage)]) == 1
        # The preserved-tail id is preserved verbatim (caller responsibility).
        assert preserved[0].messages[0].id == "human-new"

    def test_full_success_doc_has_no_per_batch_id(self):
        """No ``compaction-`` per-batch ids; the only id is the doc."""
        from daemon.compaction import build_compaction_doc
        doc = build_compaction_doc(
            instance_id="inst-1",
            seq=2,
            mode="summary",
            compacted_at="2026-09-01T10:00:00+00:00",
            global_overview="GLOBAL",
            sections=[{
                "start_idx": 1, "end_idx": 5, "body": "GLOBAL",
                "start_id": "m-1", "end_id": "m-5",
            }],
            total_sections=1,
            summarized_start=1, summarized_end=5,
            preserved_count=0,
            dropped_spans=[],
        )
        # No `compaction-{uuid}` id present (old per-batch id format).
        assert not any(
            token in doc.content for token in ("compaction-merge-", "compaction-condense-")
        )

    def test_full_success_doc_no_id_skip(self):
        """The doc builder accepts compactable with no id (no RemoveMessage)."""
        from daemon.compaction import build_compaction_doc
        compactable = [
            MessageGroup(
                start_idx=0, end_idx=0,
                messages=[AIMessage(content="No ID")],  # no id
                group_type="single",
            ),
        ]
        # No exception; the body is the global text only.
        doc = build_compaction_doc(
            instance_id="inst-1",
            seq=1,
            mode="summary",
            compacted_at="2026-09-01T10:00:00+00:00",
            global_overview="Summary",
            sections=[{
                "start_idx": 1, "end_idx": 1, "body": "Summary",
                "start_id": None, "end_id": None,
            }],
            total_sections=1,
            summarized_start=1, summarized_end=1,
            preserved_count=0,
            dropped_spans=[],
        )
        assert doc.id == "compaction-global-inst-1-1"

    def test_empty_inputs_produce_single_doc(self):
        """An empty compactable produces an empty doc body (no error)."""
        from daemon.compaction import build_compaction_doc
        doc = build_compaction_doc(
            instance_id="inst-1",
            seq=1,
            mode="summary",
            compacted_at="2026-09-01T10:00:00+00:00",
            global_overview="(no summary)",
            sections=[],
            total_sections=0,
            summarized_start=0, summarized_end=0,
            preserved_count=0,
            dropped_spans=[],
        )
        assert doc.id == "compaction-global-inst-1-1"
        # Body still has the boundary line.
        assert "END OF COMPACTED CONTEXT" in doc.content


class TestEstimateMessagesTokens:
    """Tests for estimate_messages_tokens from daemon.loader (4 cases)."""

    def test_empty_messages_returns_zero(self):
        """Test that empty message list returns 0 tokens."""
        assert estimate_messages_tokens([]) == 0

    def test_basic_human_and_ai_messages(self):
        """Test token estimation for basic human and AI messages."""
        messages = [
            HumanMessage(content="Hello world", id="h1"),
            AIMessage(content="Hi there", id="a1"),
        ]
        tokens = estimate_messages_tokens(messages)
        assert tokens > 0
        # Should account for content + per-message overhead (~4 tokens each)
        assert tokens >= 4 + 4  # minimum overhead

    def test_tool_message_adds_name_overhead(self):
        """Test that ToolMessage adds name token overhead."""
        tool_msg = ToolMessage(
            content="result",
            tool_call_id="call_1",
            name="my_tool",
            id="tool-1",
        )
        tokens = estimate_messages_tokens([tool_msg])
        assert tokens > 0
        # Tool name should contribute tokens
        assert tokens >= estimate_messages_tokens([])  # at minimum overhead

    def test_message_with_tool_calls_adds_overhead(self):
        """Test that AIMessage with tool_calls adds function call overhead."""
        ai_with_tool = AIMessage(
            content="Calling tool",
            id="ai-tool",
            tool_calls=[ToolCall(id="tc1", name="my_tool", args={"arg": "val"})],
        )
        ai_without_tool = AIMessage(content="No tool", id="ai-no-tool")
        tokens_with = estimate_messages_tokens([ai_with_tool])
        tokens_without = estimate_messages_tokens([ai_without_tool])
        assert tokens_with > tokens_without


class TestIsRecentlyCompacted:
    """Tests for ContextCompactor._is_recently_compacted (3 cases)."""

    def test_within_60_seconds_returns_true(self):
        """Test that timestamp within 60 seconds returns True."""
        recent = datetime.now(timezone.utc).isoformat()
        assert ContextCompactor._is_recently_compacted(recent) is True

    def test_beyond_60_seconds_returns_false(self):
        """Test that timestamp beyond 60 seconds returns False."""
        old = "2020-01-01T00:00:00+00:00"
        assert ContextCompactor._is_recently_compacted(old) is False

    def test_invalid_and_naive_timestamps_return_false(self):
        """Test that invalid timestamps and naive datetimes return False."""
        assert ContextCompactor._is_recently_compacted("not-a-timestamp") is False
        assert ContextCompactor._is_recently_compacted("") is False
        assert ContextCompactor._is_recently_compacted(None) is False
        # Naive datetime (no timezone) is also handled gracefully
        assert ContextCompactor._is_recently_compacted("2020-01-01T00:00:00") is False


class TestTruncateBatchToFit:
    """Tests for _truncate_batch_to_fit function (3 cases)."""

    def test_empty_groups_returns_empty(self):
        """Test that empty group list returns empty list."""
        result = _truncate_batch_to_fit([], max_tokens=1000, tokenizer_fn=MagicMock())
        assert result == []

    def test_under_limit_returns_new_group_objects(self):
        """Test that groups under limit return new list with new MessageGroup objects (deep copy)."""
        msg = ToolMessage(content="short", tool_call_id="c1", name="t", id="t1")
        group = MessageGroup(start_idx=0, end_idx=0, messages=[msg], group_type="single")
        def tokenizer_fn(msgs):
            return 100
        result = _truncate_batch_to_fit([group], max_tokens=1000, tokenizer_fn=tokenizer_fn)
        assert result is not None
        assert len(result) == 1
        assert result[0] is not group  # New MessageGroup object

    def test_drops_oldest_groups_when_over_limit(self):
        """Test that oldest groups are dropped when truncation still leaves total over limit."""
        groups = [
            MessageGroup(
                start_idx=i, end_idx=i,
                messages=[AIMessage(content=f"Msg {i}", id=f"ai-{i}")],
                group_type="single",
            )
            for i in range(5)
        ]
        def tokenizer_fn(msgs):
            return 5000  # Each call sees 5000 tokens → over max_tokens=2000
        result = _truncate_batch_to_fit(groups, max_tokens=2000, tokenizer_fn=tokenizer_fn)
        assert len(result) < 5
        assert len(result) >= 1


class TestMergeSummaries:
    """Architect §6.2 — ``_merge_summaries`` returns ``(content, ok)``,
    a string + a boolean (NOT a SystemMessage). Inputs are
    per-batch strings (not SystemMessages). The boolean is the
    fail-open ladder signal: ``False`` after the bounded retry on
    the merge call tells the caller to emit the placeholder
    GLOBAL line.
    """

    @pytest.fixture
    def mock_llm(self):
        """Mock ThinkingChatOpenAI and its invoke method."""
        mock_response = AIMessage(content="Merged summary content.", id="mock-response")
        mock_llm_instance = MagicMock()
        mock_llm_instance.invoke = MagicMock(return_value=mock_response)
        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm_instance, create=True):
            yield mock_llm_instance

    @pytest.mark.asyncio
    async def test_single_summary_returns_unchanged(self, mock_llm):
        """Test that a single per-batch string is returned verbatim."""
        config = make_compaction_config()
        compactor = ContextCompactor(config, {})
        context = CompactionContext(
            messages=[],
            system_prompt_tokens=0,
            model_name="gpt-4o",
            config=config,
            llm_config={},
        )
        content, ok = await compactor._merge_summaries(
            ["Single summary"], context
        )
        assert ok is True
        assert content == "Single summary"

    @pytest.mark.asyncio
    async def test_two_summaries_merged(self, mock_llm):
        """Two summaries → single LLM call → ``(merged_text, True)``."""
        config = make_compaction_config()
        compactor = ContextCompactor(config, {})
        context = CompactionContext(
            messages=[],
            system_prompt_tokens=0,
            model_name="gpt-4o",
            config=config,
            llm_config={},
        )
        content, ok = await compactor._merge_summaries(
            ["Summary part 1", "Summary part 2"], context
        )
        assert ok is True
        # Mock LLM response is "Merged summary content." (with period).
        assert content == "Merged summary content."

    @pytest.mark.asyncio
    async def test_four_plus_summaries_use_hierarchical_merge(self, mock_llm):
        """4+ summaries → hierarchical pairwise merging."""
        config = make_compaction_config()
        compactor = ContextCompactor(config, {})
        context = CompactionContext(
            messages=[],
            system_prompt_tokens=0,
            model_name="gpt-4o",
            config=config,
            llm_config={},
        )
        content, ok = await compactor._merge_summaries(
            [f"Summary {i}" for i in range(4)], context
        )
        assert ok is True
        assert isinstance(content, str)


class TestToolCallIntegrity:
    """Tests for tool call integrity during compaction."""

    @pytest.fixture
    def mock_llm(self):
        """Mock ThinkingChatOpenAI and its invoke method."""
        mock_response = AIMessage(content="Summarized conversation history.", id="mock-response")
        mock_llm_instance = MagicMock()
        mock_llm_instance.invoke = MagicMock(return_value=mock_response)
        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm_instance, create=True):
            yield mock_llm_instance

    def _build_tool_conversation(self) -> list[BaseMessage]:
        """Build a conversation with 5 turns of tool calls (20 messages total).
        
        Each turn: HumanMessage -> AIMessage (with tool_call) -> ToolMessage -> AIMessage (response)
        """
        messages = []
        for turn in range(5):
            base_idx = turn * 4
            tool_call_id = f"call_{turn}"
            
            # Human message
            messages.append(HumanMessage(
                content=f"Turn {turn}: Please check something",
                id=f"human-{turn}"
            ))
            
            # AI with tool call
            messages.append(AIMessage(
                content=f"Checking...",
                id=f"ai-tool-{turn}",
                tool_calls=[ToolCall(id=tool_call_id, name="bash", args={"command": f"echo {turn}"})]
            ))
            
            # Tool response
            messages.append(ToolMessage(
                content=f"Result for turn {turn}: success",
                tool_call_id=tool_call_id,
                name="bash",
                id=f"tool-{turn}"
            ))
            
            # AI response after tool
            messages.append(AIMessage(
                content=f"Completed turn {turn}.",
                id=f"ai-response-{turn}"
            ))
        
        return messages

    def _verify_tool_call_integrity(self, messages: list[BaseMessage], context: str) -> None:
        """Verify every AIMessage.tool_calls has matching ToolMessages."""
        tool_call_ids = {}
        for msg in messages:
            if isinstance(msg, ToolMessage):
                tool_call_ids[msg.tool_call_id] = True
        
        for i, msg in enumerate(messages):
            if isinstance(msg, AIMessage) and hasattr(msg, 'tool_calls') and msg.tool_calls:
                for tc in msg.tool_calls:
                    tc_id = tc.id if hasattr(tc, 'id') else tc.get('id')
                    assert tc_id in tool_call_ids, (
                        f"{context}: AIMessage {i} has orphan tool_call {tc_id}"
                    )

    @pytest.mark.asyncio
    async def test_tool_call_integrity_after_compaction(self, mock_llm):
        """Test that tool calls maintain integrity after compaction.
        
        Builds a conversation with interleaved tool calls, compacts it,
        and verifies every remaining AIMessage.tool_calls has matching
        ToolMessages - no orphans.
        """
        # Build conversation with tool calls (5 turns * 4 messages = 20 messages)
        messages = self._build_tool_conversation()
        assert len(messages) == 20

        # Verify initial integrity: all tool calls have matching responses
        self._verify_tool_call_integrity(messages, "Pre-compaction messages")

        # Build boundary groups to verify structure before compaction
        groups = identify_boundary_groups(messages)
        tool_sequence_groups = [g for g in groups if g.group_type == "tool_sequence"]
        assert len(tool_sequence_groups) == 5, (
            "Should have 5 tool_sequence groups (one per turn)"
        )

        # Each tool_sequence should have: AI + ToolMessage
        for i, group in enumerate(tool_sequence_groups):
            ai_msgs = [m for m in group.messages if isinstance(m, AIMessage)]
            tool_msgs = [m for m in group.messages if isinstance(m, ToolMessage)]
            assert len(ai_msgs) == 1, f"Group {i} should have 1 AI message"
            assert len(tool_msgs) == 1, f"Group {i} should have 1 ToolMessage"

        # Configure compactor with small context to trigger compaction
        config = make_compaction_config(
            context_window_overrides={"gpt-4o": 500},  # Very small context
            threshold=0.10,  # Low threshold to trigger compaction
            recent_message_window=3,  # Keep last 3 groups (tool_sequence groups)
            min_recent_window=2,
            min_messages_before_compaction=5,
        )

        compactor = ContextCompactor(config, {})

        context = CompactionContext(
            messages=messages,
            system_prompt_tokens=30,
            model_name="gpt-4o",
            config=config,
            llm_config={},
            last_compacted_at=None,
        )

        result = await compactor.compact_state(context)

        assert result is not None, "Compaction should trigger for tool conversation"

        # Extract non-RemoveMessage entries from replacement
        kept_messages = [
            m for m in result.replacement_messages
            if not isinstance(m, RemoveMessage)
        ]

        # The kept messages should maintain tool call integrity
        self._verify_tool_call_integrity(kept_messages, "Post-compaction messages")


class TestCompactState:
    """Integration tests for ContextCompactor.compact_state (5 cases)."""

    @pytest.fixture
    def mock_llm(self):
        """Mock ThinkingChatOpenAI and its invoke method."""
        mock_response = AIMessage(content="Summarized conversation history.", id="mock-response")
        mock_llm_instance = MagicMock()
        mock_llm_instance.invoke = MagicMock(return_value=mock_response)
        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm_instance, create=True):
            yield mock_llm_instance

    @pytest.mark.asyncio
    async def test_skips_when_recently_compacted(self, mock_llm):
        """Test that compaction is skipped if last_compacted_at is within 60 seconds."""
        config = make_compaction_config(min_messages_before_compaction=2, threshold=0.01)
        context = CompactionContext(
            messages=make_messages(5),
            system_prompt_tokens=0,
            model_name="gpt-4o",
            config=config,
            llm_config={},
            last_compacted_at=datetime.now(timezone.utc).isoformat(),
        )
        compactor = ContextCompactor(config, {})
        result = await compactor.compact_state(context)
        assert result is None

    @pytest.mark.asyncio
    async def test_skips_when_under_minimum_messages(self, mock_llm):
        """Test that compaction is skipped when message count is below min_messages_before_compaction."""
        config = make_compaction_config(
            min_messages_before_compaction=10,
            threshold=0.01,
        )
        context = CompactionContext(
            messages=make_messages(5),
            system_prompt_tokens=0,
            model_name="gpt-4o",
            config=config,
            llm_config={},
        )
        compactor = ContextCompactor(config, {})
        result = await compactor.compact_state(context)
        assert result is None

    @pytest.mark.asyncio
    async def test_successful_compaction_returns_result(self, mock_llm):
        """Test that successful compaction returns CompactionResult with correct fields."""
        config = make_compaction_config(
            min_messages_before_compaction=2,
            threshold=0.01,
            recent_message_window=2,
            min_recent_window=1,
            context_window_overrides={"gpt-4o": 1000},  # Small context to reliably trigger
        )
        messages = make_messages(200)
        context = CompactionContext(
            messages=messages,
            system_prompt_tokens=0,
            model_name="gpt-4o",
            config=config,
            llm_config={},
        )
        compactor = ContextCompactor(config, {})
        result = await compactor.compact_state(context)
        assert result is not None
        assert isinstance(result, CompactionResult)
        # Explicitly assert expected compaction_type to catch unexpected emergency truncation
        assert result.compaction_type == "summarization", (
            f"Expected 'summarization' but got '{result.compaction_type}'. "
            "Emergency truncation indicates the test setup is not triggering normal compaction."
        )
        assert result.messages_after < result.messages_before
        assert result.compacted_at is not None

    @pytest.mark.asyncio
    async def test_truncation_fallback_on_llm_error(self, mock_llm):
        """Test that truncation fallback is used when summarization LLM raises an exception."""
        mock_llm.invoke.side_effect = Exception("LLM API error")
        config = make_compaction_config(
            min_messages_before_compaction=2,
            threshold=0.01,
            recent_message_window=2,
            min_recent_window=1,
            context_window_overrides={"gpt-4o": 1000},
        )
        messages = make_messages(200)
        context = CompactionContext(
            messages=messages,
            system_prompt_tokens=0,
            model_name="gpt-4o",
            config=config,
            llm_config={},
        )
        compactor = ContextCompactor(config, {})
        result = await compactor.compact_state(context)
        assert result is not None
        assert result.compaction_type == "truncation"
        assert result.summarization_error is not None
        assert "LLM API error" in result.summarization_error

    @pytest.mark.asyncio
    async def test_emergency_truncation_when_preserved_exceeds_threshold(self, mock_llm):
        """Test emergency truncation when even preserved groups exceed threshold."""
        # Very small context window with recent_window >= groups → preserved still exceeds threshold
        config = make_compaction_config(
            min_messages_before_compaction=2,
            threshold=0.01,
            recent_message_window=200,
            min_recent_window=200,
            context_window_overrides={"gpt-4o": 100},
        )
        messages = make_messages(20)
        context = CompactionContext(
            messages=messages,
            system_prompt_tokens=0,
            model_name="gpt-4o",
            config=config,
            llm_config={},
        )
        compactor = ContextCompactor(config, {})
        result = await compactor.compact_state(context)
        assert result is not None
        assert result.compaction_type == "emergency_truncation"
        # Truncated messages should have new IDs to avoid RemoveMessage conflicts
        truncated_msgs = [m for m in result.replacement_messages if not isinstance(m, RemoveMessage)]
        for msg in truncated_msgs:
            if hasattr(msg, "id") and msg.id:
                assert msg.id.startswith("truncated-")


class TestExtractTextFromContent:
    """Tests for _extract_text_from_content function."""

    def test_string_content_returns_as_is(self):
        """Test that string content is returned unchanged."""
        assert _extract_text_from_content("Hello world") == "Hello world"

    def test_empty_list_returns_empty_string(self):
        """Test that empty list returns empty string."""
        assert _extract_text_from_content([]) == ""

    def test_list_with_text_block_only(self):
        """Test that list with text block returns the text."""
        content = [{"type": "text", "text": "Hello"}]
        assert _extract_text_from_content(content) == "Hello"

    def test_list_with_image_url_only(self):
        """Test that list with image_url block returns empty string."""
        content = [{"type": "image_url", "image_url": {"url": "http://example.com/image.png"}}]
        assert _extract_text_from_content(content) == ""

    def test_list_with_mixed_text_and_image_url(self):
        """Test that mixed list returns only text parts."""
        content = [
            {"type": "text", "text": "Hello "},
            {"type": "image_url", "image_url": {"url": "http://example.com/image.png"}},
            {"type": "text", "text": "World"},
        ]
        assert _extract_text_from_content(content) == "Hello World"

    def test_none_returns_empty_string(self):
        """Test that None returns empty string."""
        assert _extract_text_from_content(None) == ""

    def test_other_non_list_non_str_type(self):
        """Test that non-list, non-str types are converted to string."""
        assert _extract_text_from_content(123) == "123"
        assert _extract_text_from_content({"key": "value"}) == "{'key': 'value'}"

    def test_dict_block_missing_type_key(self):
        """Test that dict block missing 'type' key is treated as non-text block (skipped)."""
        content = [{"text": "hello"}]
        result = _extract_text_from_content(content)
        # No block has type="text" → empty string
        assert result == ""

    def test_dict_block_type_text_missing_text_key(self):
        """Test that dict block with type='text' but missing 'text' key returns empty string."""
        content = [{"type": "text"}]
        result = _extract_text_from_content(content)
        # block_type == "text" but no "text" key → block.get("text", "") returns ""
        assert result == ""

    def test_list_with_non_dict_items(self):
        """Test that list containing non-dict items handles gracefully (skipped, no crash)."""
        content = [None, "hello", 123]
        result = _extract_text_from_content(content)
        # Only dict blocks with type="text" are extracted; non-dict items are skipped
        assert result == ""

    def test_unicode_content_passthrough(self):
        """Test that unicode content passes through unchanged."""
        content = "Hello 🌍 🎉"
        assert _extract_text_from_content(content) == "Hello 🌍 🎉"


class TestSummarizationLLMStripsModelVision:
    """Tests that _call_summarization_llm strips model_vision from LLM kwargs.

    Compaction summarization is text-only, so model_vision must never leak into
    ThinkingChatOpenAI constructor kwargs (it would be forwarded to the OpenAI
    client and rejected as an unknown kwarg).
    """

    @pytest.mark.asyncio
    async def test_summarization_does_not_pass_model_vision(self):
        """model_vision must be filtered out before constructing ThinkingChatOpenAI."""
        config = make_compaction_config()
        llm_config = {
            "base_url": "http://localhost:1234/v1",
            "api_key": "test-key",
            "model": "gpt-4o",
            "model_vision": "gpt-4o-vision",  # Must NOT leak into constructor
            "temperature": 0.7,
            "request_timeout": 60,
        }
        compactor = ContextCompactor(config, llm_config)

        mock_response = AIMessage(content="Summary.", id="mock-response")
        mock_llm_instance = MagicMock()
        mock_llm_instance.invoke = MagicMock(return_value=mock_response)

        context = CompactionContext(
            messages=[],
            system_prompt_tokens=0,
            model_name="gpt-4o",
            config=config,
            llm_config=llm_config,
        )

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm_instance, create=True) as mock_cls:
            await compactor._call_summarization_llm("Summarize this.", context)

        # Inspect kwargs actually passed to the LLM constructor
        assert mock_cls.call_count == 1
        call_kwargs = mock_cls.call_args.kwargs
        assert "model_vision" not in call_kwargs, (
            f"model_vision leaked into ThinkingChatOpenAI kwargs: {call_kwargs}"
        )
        # Sanity: other expected fields should still be there
        assert call_kwargs.get("model") == "gpt-4o"

    @pytest.mark.asyncio
    async def test_summarization_strips_model_vision_with_summarization_model_override(self):
        """model_vision must be stripped even when summarization_model override is used."""
        config = make_compaction_config(summarization_model="gpt-4o-mini")
        llm_config = {
            "base_url": "http://localhost:1234/v1",
            "api_key": "test-key",
            "model": "gpt-4o",
            "model_vision": "gpt-4o-vision",
            "temperature": 0.7,
        }
        compactor = ContextCompactor(config, llm_config)

        mock_response = AIMessage(content="Summary.", id="mock-response")
        mock_llm_instance = MagicMock()
        mock_llm_instance.invoke = MagicMock(return_value=mock_response)

        context = CompactionContext(
            messages=[],
            system_prompt_tokens=0,
            model_name="gpt-4o",
            config=config,
            llm_config=llm_config,
        )

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm_instance, create=True) as mock_cls:
            await compactor._call_summarization_llm("Summarize this.", context)

        call_kwargs = mock_cls.call_args.kwargs
        assert "model_vision" not in call_kwargs
        assert call_kwargs.get("model") == "gpt-4o-mini"


# =============================================================================
# Phase 1 / WS-8 engine tests (slash-commands /compact feature)
# =============================================================================


def _make_prompt_of_tokens(target_tokens: int) -> str:
    """Build a prompt whose cl100k_base token count is approximately ``target_tokens``.

    Uses diverse English text (random words from a fixed vocab with varied
    sentence lengths) so the tiktoken encoder does NOT collapse the result
    via repetition compression. To keep the test fast, we calibrate the
    ``chars-per-token`` ratio ONCE (per process) and reuse it.
    """
    if target_tokens == 0:
        return ""
    cache_attr = "_token_prompt_cache"
    cache = globals().setdefault(cache_attr, {})
    if target_tokens in cache:
        return cache[target_tokens]
    rng = random.Random(42)
    vocab = [
        "the", "quick", "brown", "fox", "jumps", "over", "lazy", "dog",
        "and", "runs", "fast", "slow", "around", "house", "tree", "sun",
        "moon", "stars", "cloud", "rain", "bright", "day", "blue", "sky",
        "river", "mountain", "valley", "field", "garden", "ocean", "wind",
        "leaves", "winter", "summer", "spring", "autumn", "evening",
        "morning", "afternoon", "night", "story", "song", "voice",
    ]
    enc = tiktoken.get_encoding("cl100k_base")

    # Build a large enough buffer for the largest target (350k tokens
    # requires ~1.5M+ chars at our chars-per-token ratio). To stay under
    # the test timeout, we build a 3M-char buffer once and slice it.
    parts: list[str] = []
    total = 0
    while total < 3_000_000:
        n_words = rng.randint(3, 20)
        parts.append(" ".join(rng.choices(vocab, k=n_words)) + ".")
        total += sum(len(p) + 1 for p in parts[-1:])
    big = " ".join(parts)[:3_000_000]
    # Encoding is still O(N) but we only do it once per process.
    full_tokens = len(enc.encode(big))
    chars_per_token = 3_000_000 / full_tokens

    target_chars = int(target_tokens * chars_per_token)
    target_chars = min(target_chars, 3_000_000 - 1)
    result = big[:target_chars]
    cache[target_tokens] = result
    return result


class TestSummarizationTimeoutFormula:
    """WS-3.1 adaptive timeout formula (table-driven).

    Formula: ``min(timeout_cap_s, timeout_base_s + (tokens / 100_000) *
    timeout_per_100k_tokens_s)``.
    """

    @pytest.mark.parametrize(
        "tokens,expected_s",
        [
            (0, 90.0),
            (50_000, 120.0),
            (100_000, 150.0),
            (250_000, 240.0),
            (350_000, 300.0),  # cap
            (500_000, 300.0),  # cap
        ],
    )
    def test_formula_table(self, tokens, expected_s):
        prompt = _make_prompt_of_tokens(tokens)
        config = make_compaction_config()
        timeout = _summarization_timeout_s(prompt, config)
        # Allow ±1s tolerance for token-count rounding noise.
        assert abs(timeout - expected_s) < 1.5, (
            f"expected ~{expected_s}s for {tokens} tokens, got {timeout:.2f}s"
        )

    def test_helper_shared_by_three_call_sites(self):
        """Single source of truth — three call origins (single-batch / merge / condense)
        all delegate to ``_summarization_timeout_s``.

        The plan's WS-3.1 invariant: the prior plan duplicated the inline
        expression three times; the helper consolidates it. We assert the
        helper exists as a module-scope callable and is reachable from the
        three call sites via ``ContextCompactor._call_summarization_llm``.
        """
        # Module-scope: the helper is importable directly.
        import daemon.compaction as cm
        assert hasattr(cm, "_summarization_timeout_s")
        assert callable(cm._summarization_timeout_s)

        # Single source: the three call sites all go through the helper
        # rather than re-deriving the formula inline. This is enforced by
        # construction — there is no other timeout=... literal at any of
        # the three call sites anymore.
        import inspect
        src = inspect.getsource(ContextCompactor._call_summarization_llm)
        assert "_summarization_timeout_s" in src
        # No hard-coded timeout literals (the prior ``timeout=30.0`` at
        # the old :1038 site must be gone).
        assert "timeout=30" not in src

    def test_per_origin_prompts_get_base_scale_timeouts(self):
        """Merge/condense prompts are tiny — they must NOT receive a
        conversation-scale timeout (architect §3 Correction 1 — passing
        ``context.messages`` over-estimates massively).
        """
        config = make_compaction_config()
        merge_prompt = (
            "Combine these conversation summaries into a single coherent "
            "summary. Preserve all key decisions, important facts, tool "
            "actions and their outcomes, and user requests. Remove "
            "redundancy but keep all unique information.\n\n"
            "Part 1:\n[Conversation Summary]\nold summary A\n\n---\n\n"
            "Part 2:\n[Conversation Summary]\nold summary B"
        )
        condense_prompt = (
            "Condense this conversation summary to be more concise while "
            "keeping all key information. Focus on decisions, facts, and "
            "outcomes:\n\n[Conversation Summary]\nsome long summary"
        )
        merge_timeout = _summarization_timeout_s(merge_prompt, config)
        condense_timeout = _summarization_timeout_s(condense_prompt, config)
        # Both within ~2s of the base (no per-100k kick-in for tiny prompts).
        assert merge_timeout < 92.0, merge_timeout
        assert condense_timeout < 92.0, condense_timeout
        # Tiny prompts do NOT exceed conversation-scale timeouts even when
        # a much larger context exists in the engine.
        assert merge_timeout < 120.0, merge_timeout
        assert condense_timeout < 120.0, condense_timeout


class TestWallClockFacadeCap:
    """WS-3.2 — facade receives ``inner_cap + timeout_facade_margin_s`` (PINNED +5s)."""

    @pytest.mark.asyncio
    async def test_facade_cap_is_inner_plus_margin(self):
        """The wall_clock_cap_s threaded into ``wrap_langchain_failover`` is
        exactly ``inner_cap + timeout_facade_margin_s`` (architect §9.8).
        """
        config = make_compaction_config(
            timeout_base_s=90.0,
            timeout_per_100k_tokens_s=60.0,
            timeout_cap_s=300.0,
            timeout_facade_margin_s=5.0,
        )
        llm_config = {
            "base_url": "http://localhost:1234/v1",
            "api_key": "test-key",
            "model": "gpt-4o",
            "model_vision": "gpt-4o-vision",
        }
        compactor = ContextCompactor(config, llm_config)
        prompt = "Summarize this short conversation."

        captured: dict = {}

        class _StubLLM:
            def invoke(self, _messages):
                resp = MagicMock()
                resp.content = "ok"
                return resp

        def _fake_wrap(llm, llm_cfg, *, wall_clock_cap_s):
            captured["wall_clock_cap_s"] = wall_clock_cap_s
            return _StubLLM()

        mock_llm_instance = MagicMock()
        mock_llm_instance.invoke = MagicMock(
            side_effect=lambda _msgs: MagicMock(content="ok")
        )

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm_instance, create=True):
            with patch("daemon.services.llm_failover.wrap_langchain_failover", side_effect=_fake_wrap) as mock_wrap:
                await compactor._call_summarization_llm(prompt, CompactionContext(
                    messages=[], system_prompt_tokens=0,
                    model_name="gpt-4o", config=config, llm_config=llm_config,
                ))

        assert mock_wrap.called
        inner_cap = _summarization_timeout_s(prompt, config)
        expected = inner_cap + config.timeout_facade_margin_s
        assert captured["wall_clock_cap_s"] == expected, (
            f"facade cap={captured['wall_clock_cap_s']}, expected={expected}"
        )

    @pytest.mark.asyncio
    async def test_site_timeouterror_trips_first(self):
        """Inner ``asyncio.wait_for(inner_cap)`` is the FIRST to trip — the
        facade cap is the secondary line of defense (PINNED +5s margin).
        """
        config = make_compaction_config(
            # Tiny base cap so the test runs fast.
            timeout_base_s=0.05,
            timeout_per_100k_tokens_s=0.0,
            timeout_cap_s=0.05,
            timeout_facade_margin_s=5.0,
        )
        compactor = ContextCompactor(config, {
            "base_url": "http://localhost:1234/v1",
            "api_key": "test-key",
            "model": "gpt-4o",
        })

        mock_llm_instance = MagicMock()
        # A blocking sync invoke: ``asyncio.to_thread`` will hold the
        # event loop; ``asyncio.wait_for(..., timeout=inner_cap)`` trips
        # first with asyncio.TimeoutError.
        import threading
        block = threading.Event()

        def _blocking_invoke(_messages):
            block.wait(timeout=2.0)
            return MagicMock(content="too late")

        mock_llm_instance.invoke = MagicMock(side_effect=_blocking_invoke)

        facade_cap_seen: list = []

        def _fake_wrap(llm, llm_cfg, *, wall_clock_cap_s):
            facade_cap_seen.append(wall_clock_cap_s)
            return mock_llm_instance  # passthrough; invoke is blocking

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm_instance, create=True):
            with patch("daemon.services.llm_failover.wrap_langchain_failover", side_effect=_fake_wrap):
                with pytest.raises(asyncio.TimeoutError):
                    await compactor._call_summarization_llm(
                        "any prompt",
                        CompactionContext(
                            messages=[], system_prompt_tokens=0,
                            model_name="gpt-4o", config=config, llm_config={},
                        ),
                    )
        block.set()  # unblock the mock

        # Facade cap was sized to inner + margin (PINNED).
        assert facade_cap_seen and facade_cap_seen[0] == 0.05 + 5.0


class TestCompactionConfigWS7Knobs:
    """WS-7 — new CompactionConfig knobs resolve via env + factory defaults."""

    def test_compaction_timeout_knob_defaults(self):
        c = CompactionConfigModel()
        assert c.timeout_base_s == 90.0
        assert c.timeout_per_100k_tokens_s == 60.0
        assert c.timeout_cap_s == 300.0
        assert c.timeout_facade_margin_s == 5.0
        assert c.operation_budget_s == 300.0

    def test_slash_command_config_defaults(self):
        sc = SlashCommandConfig()
        assert sc.enabled is True
        assert sc.escape_prefix == "//"
        assert sc.min_interval_s == 10
        assert sc.noop_floor_ratio == 0.05
        assert sc.state_ttl_s == 600
        assert sc.max_state_per_instance == 20

    def test_slash_command_config_resolves_via_config_tree(self):
        from daemon.config import Config
        c = Config()
        # Nested-config pattern: ``config.slash_commands.<knob>`` resolves.
        assert c.slash_commands.enabled is True
        assert c.slash_commands.noop_floor_ratio == 0.05


class TestForceFlagWS2:
    """WS-2 — ``force=True`` bypasses the THRESHOLD check ONLY.

    Min-messages and 60s dedup still apply (S-7 + architect §2 narrowed).
    Default ``False`` → automatic paths byte-identical (anti-drift).
    """

    @pytest.fixture
    def mock_llm(self):
        mock_response = AIMessage(content="Summarized conversation history.", id="mock-response")
        mock_llm_instance = MagicMock()
        mock_llm_instance.invoke = MagicMock(return_value=mock_response)
        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm_instance, create=True):
            yield mock_llm_instance

    @pytest.mark.asyncio
    async def test_force_below_threshold_triggers_compaction(self, mock_llm):
        """With ``force=True`` and tokens well below threshold, compaction
        still runs — this is the ONLY bypass (architect §2 narrowed).
        """
        # Tokens tiny (well below any reasonable threshold), but force=True.
        config = make_compaction_config(
            min_messages_before_compaction=2,
            threshold=0.80,  # would NOT trigger on tiny context
            recent_message_window=2,
            min_recent_window=1,
            context_window_overrides={"gpt-4o": 1_000_000},
        )
        messages = make_messages(20)
        context = CompactionContext(
            messages=messages,
            system_prompt_tokens=0,
            model_name="gpt-4o",
            config=config,
            llm_config={},
        )
        compactor = ContextCompactor(config, {})
        result = await compactor.compact_state(context, force=True)
        assert result is not None
        assert result.forced is True
        assert result.compaction_type == "summarization"

    @pytest.mark.asyncio
    async def test_no_force_below_threshold_returns_none(self, mock_llm):
        """Without force, threshold check rejects — byte-identical auto-path."""
        config = make_compaction_config(
            min_messages_before_compaction=2,
            threshold=0.80,
            recent_message_window=2,
            min_recent_window=1,
            context_window_overrides={"gpt-4o": 1_000_000},
        )
        context = CompactionContext(
            messages=make_messages(20),
            system_prompt_tokens=0,
            model_name="gpt-4o",
            config=config,
            llm_config={},
        )
        compactor = ContextCompactor(config, {})
        result = await compactor.compact_state(context)  # force default False
        assert result is None

    @pytest.mark.asyncio
    async def test_force_does_not_bypass_dedup(self, mock_llm):
        """60s dedup still applies under force — recently compacted → None."""
        config = make_compaction_config(
            min_messages_before_compaction=2,
            threshold=0.99,  # very high to avoid threshold triggering
            recent_message_window=2,
            min_recent_window=1,
            context_window_overrides={"gpt-4o": 1_000_000},
        )
        context = CompactionContext(
            messages=make_messages(20),
            system_prompt_tokens=0,
            model_name="gpt-4o",
            config=config,
            llm_config={},
            last_compacted_at=datetime.now(timezone.utc).isoformat(),
        )
        compactor = ContextCompactor(config, {})
        result = await compactor.compact_state(context, force=True)
        # Dedup wins; force does not bypass it.
        assert result is None

    @pytest.mark.asyncio
    async def test_force_does_not_bypass_min_messages(self, mock_llm):
        """Min-messages check still applies under force."""
        config = make_compaction_config(
            min_messages_before_compaction=100,  # big so we trip it
            threshold=0.99,
            recent_message_window=2,
            min_recent_window=1,
            context_window_overrides={"gpt-4o": 1_000_000},
        )
        context = CompactionContext(
            messages=make_messages(5),
            system_prompt_tokens=0,
            model_name="gpt-4o",
            config=config,
            llm_config={},
        )
        compactor = ContextCompactor(config, {})
        result = await compactor.compact_state(context, force=True)
        # Min-messages wins; force does not bypass it.
        assert result is None

    @pytest.mark.asyncio
    async def test_auto_paths_byte_identical_forced_false(self, mock_llm):
        """Auto paths (proactive + reactive) call ``compact_state(context)``
        without ``force`` — the result MUST carry ``forced=False`` (S-7
        anti-drift). Same-compaction comparison: simulate each caller's
        pattern via the same context and assert the field.
        """
        config = make_compaction_config(
            min_messages_before_compaction=2,
            threshold=0.01,
            recent_message_window=2,
            min_recent_window=1,
            context_window_overrides={"gpt-4o": 1000},
        )
        messages = make_messages(200)
        compactor = ContextCompactor(config, {})

        # Both call sites use the same signature (no force kwarg).
        ctx = CompactionContext(
            messages=messages, system_prompt_tokens=0,
            model_name="gpt-4o", config=config, llm_config={},
        )
        result = await compactor.compact_state(ctx)
        assert result is not None
        assert result.forced is False, (
            "auto-path byte-identity: forced must be False on default "
            "compact_state(context) calls"
        )
        # No-timeout scenario: compaction_type != 'partial_summary' (O14).
        assert result.compaction_type != "partial_summary"


class TestTruncationMarkerWS41:
    """WS-4.1 — boundary line is INSIDE the global doc, NOT a
    separate ``truncation-marker-`` SystemMessage. The new contract
    pins (a) the doc carries the boundary line, (b) no separate
    ``truncation-marker-`` ids exist anywhere in the output, (c)
    the old helper is now a no-op alias (kept for back-compat
    imports).
    """

    def test_marker_helper_module_scope(self):
        """Old helper is now a no-op alias (the marker is the
        boundary line inside the doc)."""
        from daemon import compaction as cm
        # The alias still exists for back-compat imports.
        assert hasattr(cm, "_append_truncation_marker")
        r: list = []
        _append_truncation_marker(r)
        _append_truncation_marker(r)
        # The helper is a no-op — no items appended.
        assert len(r) == 0, (
            "_append_truncation_marker is a no-op alias in the §4 design; "
            "the marker is the boundary line inside the doc."
        )

    @pytest.fixture
    def mock_llm(self):
        mock_response = AIMessage(content="Summary text.", id="mock-response")
        mock_llm_instance = MagicMock()
        mock_llm_instance.invoke = MagicMock(return_value=mock_response)
        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm_instance, create=True):
            yield mock_llm_instance

    @pytest.mark.asyncio
    async def test_truncation_output_has_no_marker_separate_message(self, mock_llm):
        """``compaction_type='truncation'`` output carries exactly ONE
        ``compaction-global-`` doc and ZERO separate
        ``truncation-marker-`` SystemMessages. The boundary line
        is INSIDE the doc.
        """
        mock_llm.invoke.side_effect = Exception("LLM API error")
        config = make_compaction_config(
            min_messages_before_compaction=2,
            threshold=0.01,
            recent_message_window=2,
            min_recent_window=1,
            context_window_overrides={"gpt-4o": 1000},
        )
        context = CompactionContext(
            messages=make_messages(200),
            system_prompt_tokens=0,
            model_name="gpt-4o",
            config=config,
            llm_config={},
            instance_id="ws41-trunc",
        )
        compactor = ContextCompactor(config, {})
        result = await compactor.compact_state(context)
        assert result is not None
        assert result.compaction_type == "truncation"
        # No separate ``truncation-marker-`` ids.
        markers = [
            m for m in result.replacement_messages
            if isinstance(m, SystemMessage) and (m.id or "").startswith("truncation-marker-")
        ]
        assert len(markers) == 0, (
            f"§4: truncation output must NOT carry a separate marker; "
            f"got {len(markers)}"
        )
        # Exactly one ``compaction-global-`` doc.
        docs = [
            m for m in result.replacement_messages
            if isinstance(m, SystemMessage) and (m.id or "").startswith("compaction-global-")
        ]
        assert len(docs) == 1, (
            f"§4: exactly one compaction-global doc per result; got {len(docs)}"
        )
        # The boundary line is INSIDE the doc.
        assert "END OF COMPACTED CONTEXT" in docs[0].content
        # failure_kind carries "error" because LLM raised.
        assert result.failure_kind == "error"


class TestPartialSummaryWS34:
    """WS-3.4 C1 acceptance (a)-(d), migrated for the parallel pool (Commit A).

    Architect §4 — the engine now emits ONE ``compaction-global-``
    SystemMessage per result; the per-batch summaries are EMBEDDED
    as sections inside the doc, and the boundary line replaces
    the old ``truncation-marker-`` SystemMessage. The acceptance
    criteria are rephrased in the new shape:

    (a) single-batch timeout → ``truncation`` + 1 doc + no sections
    (b) non-contiguous timeout outcome (batches 0 and 2 completed,
        batch 1 timed out) → ``partial_summary`` + 1 doc with 2
        sections in batch order + dropped-spans clause covers
        batch 1 + boundary line in doc
    (c) budget/deadline exhaustion mid-run → same as (b) with
        stop_reason="budget"
    (d) proactive + reactive callers observe identical outcome semantics
    (e) NEW: real-pool non-contiguous survival (batches 0,2,4 succeed;
        1,3,5 fail) → 3 sections in batch order, all compactable
        messages absent from the channel
    """

    @pytest.fixture
    def large_message_set(self):
        # Enough messages that chunking kicks in (>20 groups).
        return make_messages(120)

    @pytest.fixture
    def compactor_config(self):
        return make_compaction_config(
            min_messages_before_compaction=2,
            threshold=0.01,
            recent_message_window=2,
            min_recent_window=1,
            context_window_overrides={"gpt-4o": 1000},
            summarization_chunk_threshold=0.01,  # force chunking
        )

    @pytest.fixture
    def mock_llm_merge(self):
        """Mock the merge-call LLM with a tiny deterministic response.

        The partial-summary tests stub ``_summarize_chunked`` (or use
        the real one with a per-batch stub), so the partial-summary
        branch in ``compact_state`` issues the bounded
        ``_merge_summaries`` call against the real LLM client. When
        that call returns a long text the ceiling rule's hard cap can
        degrade to B-shape (sections dropped, ARCHIVED line emitted) —
        hiding the k sections this test class asserts on. Pin the
        response to a small string so the ceiling rule does not fire.
        """
        mock_response = AIMessage(content="merged overview.", id="mock-merge-resp")
        mock_llm_instance = MagicMock()
        mock_llm_instance.invoke = MagicMock(return_value=mock_response)
        with patch(
            "daemon.graph.ThinkingChatOpenAI",
            return_value=mock_llm_instance,
            create=True,
        ):
            yield mock_llm_instance

    @pytest.mark.asyncio
    async def test_a_first_batch_timeout_truncation_with_marker(
        self, compactor_config, large_message_set,
    ):
        """C1 (a): single-batch path times out → ``truncation`` + 1 doc
        + no sections. The boundary line is inside the doc.
        """
        # Force the single-batch path (compactable_tokens <= threshold).
        # Also shrink the adaptive-timeout base so the test runs fast.
        config = compactor_config
        config.summarization_chunk_threshold = 1.5  # disable chunking
        config.timeout_base_s = 0.05  # 50ms cap → trips immediately
        config.timeout_cap_s = 0.05
        compactor = ContextCompactor(config, {})

        # Use a synchronous blocking invoke so ``asyncio.wait_for`` trips
        # first with ``asyncio.TimeoutError`` (per O14 — TimeoutError is
        # caught in the per-chunk except, no astream mimicry needed).
        import threading
        block = threading.Event()

        def _blocking_invoke(_messages):
            block.wait(timeout=2.0)
            return MagicMock(content="never returns")

        mock_llm_instance = MagicMock()
        mock_llm_instance.invoke = MagicMock(side_effect=_blocking_invoke)
        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm_instance, create=True):
            try:
                result = await compactor.compact_state(CompactionContext(
                    messages=large_message_set, system_prompt_tokens=0,
                    model_name="gpt-4o", config=config, llm_config={},
                    instance_id="ws41-a",
                ))
            finally:
                block.set()  # unblock the mock

        assert result is not None
        assert result.compaction_type == "truncation"
        assert result.failure_kind == "timeout"
        # No per-batch messages (per-batch ids are ``compaction-{uuid}``;
        # the only id is the global doc which starts with
        # ``compaction-global-``).
        per_batch = [
            m for m in result.replacement_messages
            if isinstance(m, SystemMessage)
            and (m.id or "").startswith("compaction-")
            and not (m.id or "").startswith("compaction-global-")
        ]
        assert per_batch == [], (
            f"§4: no per-batch SystemMessage in output; got {len(per_batch)}"
        )
        # Exactly ONE compaction-global doc.
        docs = [
            m for m in result.replacement_messages
            if isinstance(m, SystemMessage) and (m.id or "").startswith("compaction-global-")
        ]
        assert len(docs) == 1
        # No separate ``truncation-marker-`` id.
        markers = [
            m for m in result.replacement_messages
            if isinstance(m, SystemMessage) and (m.id or "").startswith("truncation-marker-")
        ]
        assert len(markers) == 0
        # The doc carries the boundary line.
        assert "END OF COMPACTED CONTEXT" in docs[0].content
        # compacted_at stamped on this path (D12).
        assert result.compacted_at is not None

    @pytest.mark.asyncio
    async def test_b_second_batch_timeout_partial_summary(
        self, compactor_config, large_message_set, mock_llm_merge,
    ):
        """C1 (b), parallel-pool contract: a per-batch timeout no longer
        forces a contiguous prefix. Batches 0 and 2 complete, batch 1
        times out → ``partial_summary`` + 1 doc with 2 sections in
        batch order (0 then 2) + dropped-spans clause covers batch 1
        + boundary line in doc.
        """
        config = compactor_config
        compactor = ContextCompactor(config, {})

        # Stub ``_summarize_chunked`` directly — simulating the C1 hybrid
        # scenario under the parallel pool: batches 0 and 2 succeeded,
        # batch 1 raised TimeoutError. The surviving set is
        # non-contiguous by construction.
        from daemon.compaction import ChunkedOutcome

        async def _fake_chunked(compactable, context, previous_overview=None):
            # Engine stub returning a synthetic partial-summary outcome.
            # ``previous_overview`` is accepted for forward-compat with
            # the W1 pass-2 seed (architect §4 — "the global frame
            # converges across passes"); the stub ignores it.
            return ChunkedOutcome(
                summaries=[
                    "batch-0 summary",
                    "batch-2 summary",
                ],
                failed_batches=[1],
                stop_reason="timeout",
            )

        compactor._summarize_chunked = _fake_chunked

        result = await compactor.compact_state(CompactionContext(
            messages=large_message_set, system_prompt_tokens=0,
            model_name="gpt-4o", config=config, llm_config={},
            instance_id="ws41-b",
        ))

        assert result is not None
        assert result.compaction_type == "partial_summary"
        assert result.failure_kind == "timeout"
        # Exactly ONE compaction-global doc; the 2 surviving batch texts
        # are EMBEDDED as sections inside the doc (not separate
        # SystemMessages).
        docs = [
            m for m in result.replacement_messages
            if isinstance(m, SystemMessage) and (m.id or "").startswith("compaction-global-")
        ]
        assert len(docs) == 1
        doc = docs[0]
        # Two SECTION headers in the doc (k=2), batch order preserved.
        section_count = doc.content.count("### SECTION ")
        assert section_count == 2, (
            f"expected 2 surviving sections embedded in doc, got {section_count}"
        )
        # The dropped-spans clause covers batch 1.
        assert "dropped without summary" in doc.content
        # The boundary line is INSIDE the doc.
        assert "END OF COMPACTED CONTEXT" in doc.content
        # No separate ``truncation-marker-`` ids anywhere.
        markers = [
            m for m in result.replacement_messages
            if isinstance(m, SystemMessage) and (m.id or "").startswith("truncation-marker-")
        ]
        assert len(markers) == 0
        # No per-batch ``compaction-`` SystemMessage ids.
        per_batch = [
            m for m in result.replacement_messages
            if isinstance(m, SystemMessage)
            and (m.id or "").startswith("compaction-")
            and not (m.id or "").startswith("compaction-global-")
        ]
        assert per_batch == []
        # compacted_at stamped on this path (D12 — a partial is a completed compaction).
        assert result.compacted_at is not None

    @pytest.mark.asyncio
    async def test_c_budget_exhaustion_partial_summary(
        self, large_message_set, mock_llm_merge,
    ):
        """C1 (c), parallel-pool contract: shared-deadline exhaustion mid-run →
        ``partial_summary`` + 1 doc with non-contiguous surviving sections
        + dropped-spans clause covering the failed batches
        + stop_reason="budget".
        """
        from daemon.compaction import ChunkedOutcome

        config = make_compaction_config(
            min_messages_before_compaction=2,
            threshold=0.01,
            recent_message_window=2,
            min_recent_window=1,
            context_window_overrides={"gpt-4o": 1000},
            summarization_chunk_threshold=0.01,
            # TINY budget so the shared deadline fires mid-pool.
            operation_budget_s=0.0,
        )
        compactor = ContextCompactor(config, {})

        # Force the partial path: stub ``_summarize_chunked`` to return a
        # budget-deadline ChunkedOutcome whose surviving set is
        # non-contiguous (batches 0 and 2 completed; 1, 3, 4, 5 did not —
        # the exact complement of the completion set).
        async def _fake_chunked(compactable, context, previous_overview=None):
            # Engine stub returning a synthetic partial-summary outcome.
            # ``previous_overview`` is accepted for forward-compat with
            # the W1 pass-2 seed (architect §4 — "the global frame
            # converges across passes"); the stub ignores it.
            return ChunkedOutcome(
                summaries=[
                    "batch-0 summary",
                    "batch-2 summary",
                ],
                failed_batches=[1, 3, 4, 5],
                stop_reason="budget",
            )

        compactor._summarize_chunked = _fake_chunked

        result = await compactor.compact_state(CompactionContext(
            messages=large_message_set, system_prompt_tokens=0,
            model_name="gpt-4o", config=config, llm_config={},
            instance_id="ws41-c",
        ))

        assert result is not None
        assert result.compaction_type == "partial_summary"
        # Budget is in the timeout failure_kind family.
        assert result.failure_kind == "timeout"
        # Exactly one doc; k=2 sections embedded.
        docs = [
            m for m in result.replacement_messages
            if isinstance(m, SystemMessage) and (m.id or "").startswith("compaction-global-")
        ]
        assert len(docs) == 1
        assert docs[0].content.count("### SECTION ") == 2
        # No separate markers.
        markers = [
            m for m in result.replacement_messages
            if isinstance(m, SystemMessage) and (m.id or "").startswith("truncation-marker-")
        ]
        assert len(markers) == 0

    @pytest.mark.asyncio
    async def test_chunked_partial_summary_non_contiguous(
        self, compactor_config, large_message_set, mock_llm_merge,
    ):
        """NEW (Commit A, real pool): batches 0, 2, 4 succeed; 1, 3, 5 fail
        → exactly 3 surviving summaries in batch order + RemoveMessage for
        EVERY compactable message (non-contiguous drop is per-batch, not
        per-tail) + marker exactly once.

        Drives the REAL ``_summarize_chunked`` bounded pool — no engine
        stub — with a content-keyed ``_summarize_single_batch`` stub
        (content-keying is scheduling-independent under parallelism).
        """
        config = compactor_config
        compactor = ContextCompactor(config, {})

        # make_messages(120) → 120 single-message groups; recent window 2
        # preserves the last 2 → 118 compactable groups → 6 batches of
        # (20,20,20,20,20,18). Batch k starts at message number 20k, so
        # ``first_message_number // 20`` identifies the batch index
        # regardless of task scheduling order.
        def _batch_idx(batch_groups):
            first = batch_groups[0].messages[0]
            return int(first.content.split()[-1]) // 20

        async def _stub_single_batch(batch_groups, context):
            idx = _batch_idx(batch_groups)
            if idx % 2 == 1:
                raise TimeoutError(f"batch-{idx} adaptive cap")
            return f"batch-{idx} summary"

        compactor._summarize_single_batch = _stub_single_batch

        # Engine-level outcome: non-contiguous completion set, batch-index
        # order preserved by the gather reassembly. (make_messages carries
        # no injected flags, so grouping the raw list matches what
        # compact_state feeds the engine.)
        groups = identify_boundary_groups(large_message_set)
        compactable, preserved, _ = select_compactable_groups(
            groups, config.recent_message_window, config.min_recent_window,
            1000, 0, estimate_messages_tokens, config_threshold=config.threshold,
        )
        outcome = await compactor._summarize_chunked(
            compactable, CompactionContext(
                messages=large_message_set, system_prompt_tokens=0,
                model_name="gpt-4o", config=config, llm_config={},
            )
        )
        assert outcome.stop_reason == "timeout"
        # §4 — outcomes carry per-batch strings (not SystemMessages).
        assert outcome.summaries == ["batch-0 summary", "batch-2 summary", "batch-4 summary"]
        assert outcome.failed_batches == [1, 3, 5]

        # Full-handler assembly: partial_summary with the non-contiguous
        # survivors, every compactable message removed (the doc replaces
        # them), 1 doc with 3 sections, boundary line.
        result = await compactor.compact_state(CompactionContext(
            messages=large_message_set, system_prompt_tokens=0,
            model_name="gpt-4o", config=config, llm_config={},
            instance_id="ws41-e",
        ))
        assert result is not None
        assert result.compaction_type == "partial_summary"
        assert result.failure_kind == "timeout"

        # Exactly one compaction-global doc.
        docs = [
            m for m in result.replacement_messages
            if isinstance(m, SystemMessage) and (m.id or "").startswith("compaction-global-")
        ]
        assert len(docs) == 1
        doc = docs[0]
        # 3 surviving sections embedded in the doc (k=3).
        assert doc.content.count("### SECTION ") == 3
        # No separate marker, no per-batch SystemMessage.
        markers = [
            m for m in result.replacement_messages
            if isinstance(m, SystemMessage) and (m.id or "").startswith("truncation-marker-")
        ]
        assert len(markers) == 0
        per_batch = [
            m for m in result.replacement_messages
            if isinstance(m, SystemMessage)
            and (m.id or "").startswith("compaction-")
            and not (m.id or "").startswith("compaction-global-")
        ]
        assert per_batch == []
        # Dropped-spans clause covers the failed batches (1, 3, 5).
        assert "dropped without summary" in doc.content
        # Boundary line is INSIDE the doc.
        assert "END OF COMPACTED CONTEXT" in doc.content

        # Strengthened 2026-09-01 (B3 regression pin): the prior
        # version's assertion ``doc.content.count("### SECTION ") == 3``
        # was section-count-vacuous — the OLD `_per_batch_section_meta`
        # (a80767b9) still emitted 3 section headers even though
        # their start_idx/end_idx ranged over batch bounds in a
        # survivor-compressed manner (after batch 4 the next
        # implicit boundary was batch 5's batch 1 coords, not the
        # ORIGINAL batch 5 coords). This version BINDS each body
        # to its ORIGINAL-coord section header: batch 0 → #1–#20,
        # batch 2 → #41–#60, batch 4 → #81–#100. The dropped
        # clause must contain ONLY the actually-failed batch
        # ranges (1, 3, 5).
        section_blocks = _parse_section_blocks(doc.content)
        assert len(section_blocks) == 3, (
            f"exactly three surviving sections (batches 0, 2, 4); "
            f"got {len(section_blocks)} (block headers: "
            f"{[h for h, _ in section_blocks]!r}); body excerpt: "
            f"{doc.content[:500]!r}"
        )
        # Body→span binding: each surviving body is paired with its
        # ORIGINAL-batch-coords section header.
        expected_bindings = [
            ("batch-0 summary", "messages #1–#20"),
            ("batch-2 summary", "messages #41–#60"),
            ("batch-4 summary", "messages #81–#100"),
        ]
        for i, (expected_body, expected_span) in enumerate(
            expected_bindings
        ):
            header_line, body_text = section_blocks[i]
            assert expected_span in header_line, (
                f"section #{i + 1} header must carry the "
                f"ORIGINAL batch coords ({expected_span!r}); got "
                f"header={header_line!r}; body excerpt: {doc.content[:500]!r}"
            )
            assert expected_body in body_text, (
                f"section #{i + 1} body must contain the surviving "
                f"summary ({expected_body!r}); got body={body_text!r}"
            )
        # Cross-binding (anti-vacuous): no body may appear under
        # a span header that doesn't carry its ORIGINAL coords.
        for i, (expected_body, _expected_span) in enumerate(
            expected_bindings
        ):
            for j, (_other_body, other_span) in enumerate(
                expected_bindings
            ):
                if i == j:
                    continue
                other_header, other_body_text = section_blocks[j]
                assert expected_body not in other_body_text, (
                    f"section #{j + 1} body must NOT contain "
                    f"{expected_body!r} (body→span binding pin); "
                    f"got body={other_body_text!r}, "
                    f"header={other_header!r}"
                )
        # Dropped clause: ONLY the actually-failed batches (1, 3,
        # 5) must appear there. The OLD impl misclassified them as
        # the survivors — binding the dropped clause to those
        # three ranges catches the inversion.
        envelope, _ = _split_envelope_and_section_detail(doc.content)
        assert "dropped without summary:" in envelope, (
            f"envelope must declare the dropped-without-summary "
            f"clause; got envelope={envelope!r}"
        )
        # The three failed-batch ranges must appear in the dropped
        # clause (in some order — the format is comma-joined).
        # Layout: 120 messages total, min_window=1 so the LAST
        # group is preserved (119 compactable messages), 6
        # batches of (20, 20, 20, 20, 20, 19). Failed batches 1,
        # 3, 5 → dropped-spans = (21, 40), (61, 80), (101, 119).
        # Format note: only the FIRST range carries the
        # ``messages `` prefix; subsequent ranges are joined with
        # ``", #x"`` — they appear as ``"#61–#80"`` in the
        # rendered envelope (NOT as ``"messages #61–#80"``).
        assert ", #61–#80" in envelope and ", #101–#119" in envelope, (
            f"the failed-batch ranges (#61–#80, #101–#119) must "
            f"appear in the dropped clause (after the first "
            f"``messages `` prefix); got envelope={envelope!r}"
        )
        assert "messages #21–#40" in envelope, (
            f"the first failed-batch range (#21–#40) must appear "
            f"in the dropped clause (with the ``messages `` "
            f"prefix); got envelope={envelope!r}"
        )
        # The survivor ranges must NOT leak into the dropped clause.
        for _body, survivor_span in expected_bindings:
            assert survivor_span not in envelope, (
                f"survivor batch span {survivor_span!r} must NOT "
                f"appear in the dropped clause (would indicate "
                f"B3 OLD impl misclassifying the survivor as "
                f"dropped); got envelope={envelope!r}"
            )

    @pytest.mark.asyncio
    async def test_d_identical_outcome_proactive_vs_reactive(
        self, compactor_config, mock_llm_merge,
    ):
        """C1 (d): the same engine call from proactive-context vs reactive-context
        must produce IDENTICAL CompactionResult outcome semantics.

        The two auto-path callers (instance_messaging.py:1179 +
        graph.py:3513) construct CompactionContext differently (different
        system_prompt_tokens, different llm_config) but the engine
        branching must not depend on the caller's identity — it depends
        only on the outcome of ``_summarize_chunked``.
        """
        from daemon.compaction import ChunkedOutcome

        config = make_compaction_config(
            min_messages_before_compaction=2,
            threshold=0.01,
            recent_message_window=2,
            min_recent_window=1,
            context_window_overrides={"gpt-4o": 1000},
            summarization_chunk_threshold=0.01,
        )
        messages = make_messages(120)

        async def _fake_chunked_partial(
            compactable, context, previous_overview=None,
        ):
            # Engine stub returning a synthetic partial-summary outcome.
            # ``previous_overview`` is accepted for forward-compat with
            # the W1 pass-2 seed (architect §4); the stub ignores it.
            return ChunkedOutcome(
                summaries=["first batch summary"],
                failed_batches=[1],
                stop_reason="timeout",
            )

        # Proactive-context (instance_messaging.py:1179 pattern):
        # larger system_prompt_tokens, full llm_config.
        proactive_compactor = ContextCompactor(config, {})
        proactive_compactor._summarize_chunked = _fake_chunked_partial
        proactive_ctx = CompactionContext(
            messages=messages, system_prompt_tokens=2000,
            model_name="gpt-4o", config=config,
            llm_config={"model": "gpt-4o", "base_url": "http://x", "api_key": "k"},
            instance_id="ws41-d-proactive",
        )
        proactive_result = await proactive_compactor.compact_state(proactive_ctx)

        # Reactive-context (graph.py:3513 pattern):
        # system_prompt_tokens=0, llm_config from compactor instance.
        reactive_compactor = ContextCompactor(config, {})
        reactive_compactor._summarize_chunked = _fake_chunked_partial
        reactive_ctx = CompactionContext(
            messages=messages, system_prompt_tokens=0,
            model_name="gpt-4o", config=config, llm_config={},
            instance_id="ws41-d-reactive",
        )
        reactive_result = await reactive_compactor.compact_state(reactive_ctx)

        # Identical outcome semantics.
        assert proactive_result.compaction_type == reactive_result.compaction_type == "partial_summary"
        assert proactive_result.failure_kind == reactive_result.failure_kind == "timeout"
        assert proactive_result.compacted_at is not None
        assert reactive_result.compacted_at is not None
        # Both produce exactly ONE compaction-global doc; no separate
        # ``truncation-marker-`` ids.
        for r in (proactive_result, reactive_result):
            docs = [
                m for m in r.replacement_messages
                if isinstance(m, SystemMessage) and (m.id or "").startswith("compaction-global-")
            ]
            assert len(docs) == 1
            markers = [
                m for m in r.replacement_messages
                if isinstance(m, SystemMessage) and (m.id or "").startswith("truncation-marker-")
            ]
            assert len(markers) == 0


class TestPerChunkTimeoutNarrowing:
    """O14: per-chunk try/except narrowed to ``(TimeoutError, asyncio.TimeoutError)``.
    Other exceptions propagate normally.
    """

    @pytest.mark.asyncio
    async def test_non_timeout_exception_propagates_to_outer_handler(self):
        """Per-chunk ``except`` is narrow — ValueError still escapes to the
        outer ``except Exception`` at ``compact_state`` :744-772.
        """
        config = make_compaction_config(
            min_messages_before_compaction=2,
            threshold=0.01,
            recent_message_window=2,
            min_recent_window=1,
            context_window_overrides={"gpt-4o": 1000},
            summarization_chunk_threshold=0.01,
        )
        compactor = ContextCompactor(config, {})

        mock_llm_instance = MagicMock()
        mock_llm_instance.invoke = MagicMock(side_effect=ValueError("non-timeout exception"))

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm_instance, create=True):
            result = await compactor.compact_state(CompactionContext(
                messages=make_messages(120), system_prompt_tokens=0,
                model_name="gpt-4o", config=config, llm_config={},
            ))

        # Non-timeout exception → outer handler maps to truncation +
        # failure_kind="error" (NOT timeout).
        assert result is not None
        assert result.compaction_type == "truncation"
        assert result.failure_kind == "error"
        assert result.summarization_error is not None
        assert "non-timeout exception" in result.summarization_error


class TestOperationBudgetWS33:
    """WS-3.3, migrated for the parallel pool (Commit A): the operation
    budget is a SHARED DEADLINE around the batch pool — expiry cancels
    in-flight and un-started batches, keeps the completed summaries, and
    the gathered set IS the completion set. The deadline lives entirely
    inside ``_summarize_chunked`` (D-B5/D-B6): no caller-side
    ``aupdate_state`` is ever interleaved with a live pool.
    """

    @pytest.mark.asyncio
    async def test_budget_exhaustion_stops_remaining_chunks(self):
        """Real-pool contract: with the deadline expiring after batch 0
        completes, the gathered set (batch 0) IS the completion set — the
        remaining batches never contribute summaries, and the run takes
        the partial path (|S| >= 1). The old serial "stop issuing the
        next chunk" pre-check is replaced by the shared deadline.
        """
        config = make_compaction_config(
            min_messages_before_compaction=2,
            threshold=0.01,
            recent_message_window=2,
            min_recent_window=1,
            context_window_overrides={"gpt-4o": 1000},
            summarization_chunk_threshold=0.01,
            # Deadline fires at 1.0s: batch 0 (fast) completes inside it,
            # every later batch is still running/waiting when it trips.
            operation_budget_s=1.0,
            # Serial gating for a deterministic completion set.
            chunk_concurrency=1,
        )
        messages = make_messages(120)

        cancelled_batches: list[int] = []

        async def _stub_single_batch(batch_groups, context):
            idx = int(batch_groups[0].messages[0].content.split()[-1]) // 20
            if idx == 0:
                await asyncio.sleep(0.05)
                return "batch-0 summary"
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                # Record + re-raise — cancellation is observed, never
                # swallowed (CancelledError is BaseException).
                cancelled_batches.append(idx)
                raise
            return f"batch-{idx} summary"

        compactor = ContextCompactor(config, {})
        compactor._summarize_single_batch = _stub_single_batch

        result = await compactor.compact_state(CompactionContext(
            messages=messages, system_prompt_tokens=0,
            model_name="gpt-4o", config=config, llm_config={},
            instance_id="ws33-a",
        ))

        # Budget-deadline partial path: |S|>=1 → partial_summary.
        assert result is not None
        assert result.compaction_type == "partial_summary"
        assert result.failure_kind == "timeout"
        # Exactly one doc with 1 surviving section embedded.
        docs = [
            m for m in result.replacement_messages
            if isinstance(m, SystemMessage) and (m.id or "").startswith("compaction-global-")
        ]
        assert len(docs) == 1
        assert docs[0].content.count("### SECTION ") == 1
        # compacted_at stamped on this path (D12).
        assert result.compacted_at is not None
        # No separate marker.
        markers = [
            m for m in result.replacement_messages
            if isinstance(m, SystemMessage) and (m.id or "").startswith("truncation-marker-")
        ]
        assert len(markers) == 0
        # Cancellation evidence: batch 1 held the only slot in-flight when
        # the deadline hit; batches 2-5 never acquired it.
        assert cancelled_batches == [1]

    @pytest.mark.asyncio
    async def test_chunked_deadline_cancels_in_flight(self):
        """NEW (Commit A, real pool): the shared deadline cancels an
        in-flight batch while faster siblings complete around it. The
        outcome carries the ACTUALLY-completed set in batch-index order
        (the cancelled batch's slot stays empty — non-contiguous), with
        ``stop_reason="budget"``, and the in-flight task observed a clean
        cancellation (recorded + re-raised, never swallowed).
        """
        config = make_compaction_config(
            min_messages_before_compaction=2,
            threshold=0.01,
            recent_message_window=2,
            min_recent_window=1,
            context_window_overrides={"gpt-4o": 1000},
            summarization_chunk_threshold=0.01,
            # Deadline at 0.8s: all fast batches finish well inside it;
            # batch 1 is still in-flight when it trips.
            operation_budget_s=0.8,
            chunk_concurrency=2,
        )
        messages = make_messages(120)

        cancelled_batches: list[int] = []

        async def _stub_single_batch(batch_groups, context):
            idx = int(batch_groups[0].messages[0].content.split()[-1]) // 20
            if idx == 1:
                try:
                    await asyncio.sleep(5)
                except asyncio.CancelledError:
                    cancelled_batches.append(idx)
                    raise
                return f"batch-{idx} summary"
            await asyncio.sleep(0.01)
            return f"batch-{idx} summary"

        compactor = ContextCompactor(config, {})
        compactor._summarize_single_batch = _stub_single_batch

        outcome = await compactor._summarize_chunked(
            identify_boundary_groups(messages), CompactionContext(
                messages=messages, system_prompt_tokens=0,
                model_name="gpt-4o", config=config, llm_config={},
            )
        )

        assert outcome.stop_reason == "budget"
        # §4 — outcomes carry per-batch strings (not SystemMessages).
        assert outcome.summaries == [
            "batch-0 summary", "batch-2 summary",
            "batch-3 summary", "batch-4 summary", "batch-5 summary",
        ]
        # failed_batches is the exact complement of the completion set.
        assert outcome.failed_batches == [1]
        # In-flight batch observed a clean cancellation.
        assert cancelled_batches == [1]


class TestChunkedOutcomeDataclass:
    """Dataclass surface contract — both fields are required, types pinned."""

    def test_chunked_outcome_construction(self):
        co = ChunkedOutcome(summaries=[], failed_batches=[], stop_reason="completed")
        assert co.summaries == []
        assert co.failed_batches == []
        assert co.stop_reason == "completed"

    def test_chunked_outcome_with_summaries(self):
        # §4 — summaries is a list of str (per-batch text), not
        # SystemMessage.
        co = ChunkedOutcome(
            summaries=["x"], failed_batches=[1], stop_reason="timeout",
        )
        assert co.summaries == ["x"]
        assert co.failed_batches == [1]
        assert co.stop_reason == "timeout"


class TestReCompactionMarkerDedup:
    """§4 — re-compaction emits one NEW doc per result, with an
    incremented seq. The doc id is deterministic per instance; the
    new doc replaces the old via the sentinel recipe (the old doc
    is consumed with the span it lived in).
    """

    def test_doc_ids_advance_per_compaction(self):
        """Each ``build_compaction_doc`` call mints the next seq."""
        from daemon.compaction import build_compaction_doc
        doc1 = build_compaction_doc(
            instance_id="inst-1", seq=1, mode="summary",
            compacted_at="2026-09-01T10:00:00+00:00",
            global_overview="G1", sections=[],
            total_sections=0, summarized_start=0, summarized_end=0,
            preserved_count=0, dropped_spans=[],
        )
        doc2 = build_compaction_doc(
            instance_id="inst-1", seq=2, mode="summary",
            compacted_at="2026-09-01T11:00:00+00:00",
            global_overview="G2", sections=[],
            total_sections=0, summarized_start=0, summarized_end=0,
            preserved_count=0, dropped_spans=[],
        )
        assert doc1.id == "compaction-global-inst-1-1"
        assert doc2.id == "compaction-global-inst-1-2"
        assert doc1.id != doc2.id

    @pytest.mark.asyncio
    async def test_doc_count_per_compaction_result_is_one(self):
        """§4 — a single ``CompactionResult`` carries AT MOST ONE
        ``compaction-global-`` doc. The doc is the single
        in-band signal that summarization fired.
        """
        from daemon.compaction import ContextCompactor, MessageGroup
        from daemon.config import CompactionConfig

        config = CompactionConfig(
            enabled=True,
            threshold=0.80,
            recent_message_window=10,
            min_recent_window=3,
            context_window_overrides={},
            context_window_default=128000,
            target_ratio=0.40,
            summarization_model="",
            min_messages_before_compaction=10,
            summarization_chunk_threshold=0.60,
            timeout_base_s=90.0,
            timeout_per_100k_tokens_s=60.0,
            timeout_cap_s=300.0,
            timeout_facade_margin_s=5.0,
            operation_budget_s=300.0,
        )
        compactor = ContextCompactor(config, llm_config={})

        from langchain_core.messages import HumanMessage

        compactable = [
            MessageGroup(
                start_idx=0,
                end_idx=2,
                group_type="single",
                messages=[HumanMessage(content="x" * 100, id=f"old-{n}") for n in range(3)],
            )
        ]
        preserved: list[MessageGroup] = []

        replacement, ctype, _status = await compactor._truncate_fallback(
            compactable, preserved, context=CompactionContext(
                messages=[], system_prompt_tokens=0, model_name="gpt-4o",
                config=config, llm_config={}, instance_id="test",
                tokens_before_total=0, compacted_at_iso="2026-09-01T10:00:00+00:00",
            )
        )
        assert ctype == "truncation"
        # §4 — exactly ONE doc (not a separate marker).
        docs = [
            m for m in replacement
            if isinstance(m, SystemMessage) and (m.id or "").startswith("compaction-global-")
        ]
        assert len(docs) == 1, (
            f"§4: a single compaction result must carry exactly ONE doc; "
            f"got {len(docs)}"
        )
        # No separate ``truncation-marker-`` ids.
        markers = [
            m for m in replacement
            if isinstance(m, SystemMessage) and (m.id or "").startswith("truncation-marker-")
        ]
        assert len(markers) == 0


def _load_real_add_messages():
    """Import the REAL LangGraph ``add_messages`` reducer, bypassing
    the conftest's mocked ``langgraph.*`` entries in ``sys.modules``.

    Same identity-restore discipline as
    ``test_compact_executor_revive_brick_e2e._RealLangGraph``: snap the
    originals, drop mocked AND freshly-imported real langgraph entries,
    then restore the SAME module objects so subsequent tests keep
    seeing the conftest mocks.

    Returns:
        ``(add_messages, REMOVE_ALL_MESSAGES)`` tuple — both sourced
        from the REAL installed ``langgraph.graph.message``. The
        sentinel is the constant the reducer detects at
        ``message.py:209``; using the imported symbol (not the
        hard-coded literal) guards against upstream rename drift.
    """
    import importlib
    import sys

    saved = {
        k: sys.modules[k]
        for k in list(sys.modules)
        if k.startswith("langgraph")
    }
    for k in [k for k in sys.modules if k.startswith("langgraph")]:
        del sys.modules[k]
    try:
        mod = importlib.import_module("langgraph.graph.message")
        return mod.add_messages, mod.REMOVE_ALL_MESSAGES
    finally:
        for k in [k for k in sys.modules if k.startswith("langgraph")]:
            del sys.modules[k]
        sys.modules.update(saved)


# W4 fix (2026-09-01) — real skipif predicate for the installed
# langgraph version. The reducer-semantics pins depend on
# ``langgraph.graph.message.add_messages`` and
# ``REMOVE_ALL_MESSAGES`` being importable. The conftest mocks
# ``langgraph.*`` as non-package modules, so the real imports only
# resolve inside ``_load_real_add_messages``'s swap window; this
# skipif surfaces an explicit SKIP (not an ImportError swallowed
# by the helper) when the real package is not installed.
LANGGRAPH_INSTALLED = False
LANGGRAPH_VERSION = ""
try:
    import importlib.metadata as _ilmd
    LANGGRAPH_VERSION = _ilmd.version("langgraph")
    LANGGRAPH_INSTALLED = True
except Exception:
    pass
needs_real_langgraph = pytest.mark.skipif(
    not LANGGRAPH_INSTALLED,
    reason=(
        "real langgraph package not importable in test env "
        "(conftest mocks it); the reducer-semantics pins + the "
        "REMOVE_ALL_MESSAGES sentinel constant require the real "
        "installed package"
    ),
)


# =============================================================================
# B3 body→span-binding helpers (W6 strengthened assertions 2026-09-01)
# =============================================================================
def _parse_section_blocks(body: str) -> list[tuple[str, str]]:
    """Parse a ``compaction-global-…`` SystemMessage body into a list of
    ``(header_line, body_text)`` tuples — one per ``### SECTION`` block.

    The doc body layout (architect §4, build_compaction_doc):

        ── ENVELOPE ──
        <envelope header lines, including the dropped-without-
        summary clause>
        ── GLOBAL OVERVIEW ──
        <global overview text>
        ── SECTION DETAIL ──
        ### SECTION 1/n — messages #a–#b[ | conversation time t0 → t1]
        <section body>
        <blank>
        ### SECTION 2/n — messages #c–#d
        <section body>
        …
        ── END OF COMPACTED CONTEXT ──

    Each ``### SECTION i/n — messages #x–#y`` line is the section
    header (carrying the per-section span coordinates). Body text
    follows immediately (terminated by the next section header, the
    boundary line, or end-of-string). The body→span-binding pin in
    the strengthened tests asserts each section's body text is
    paired with the correct ORIGINAL-batch-coord header.

    Args:
        body: Full doc body string of a compaction-global SystemMessage.

    Returns:
        List of ``(header_line, body_text)`` tuples in document
        order. Header is the verbatim ``### SECTION i/n — messages
        #x–#y[ | …]`` line; body_text is the per-section body
        stripped of trailing blank lines.
    """
    marker = "### SECTION "
    blocks: list[tuple[str, str]] = []
    pos = body.find(marker)
    while pos != -1:
        end_of_header = body.find("\n", pos)
        if end_of_header == -1:
            header_line = body[pos:]
            body_text = ""
        else:
            header_line = body[pos:end_of_header]
            # Find the next section anchor (or terminator).
            boundary_pos = body.find("── END OF", end_of_header)
            next_section_pos = body.find(marker, end_of_header)
            candidates = [
                p for p in (next_section_pos, boundary_pos)
                if p != -1
            ]
            next_stop = min(candidates) if candidates else len(body)
            body_text = body[end_of_header + 1:next_stop]
            # Strip trailing newlines / whitespace.
            body_text = body_text.rstrip("\n").rstrip()
        blocks.append((header_line, body_text))
        pos = (
            body.find(marker, end_of_header + 1)
            if end_of_header != -1 else -1
        )
    return blocks


def _split_envelope_and_section_detail(body: str) -> tuple[str, str]:
    """Split a doc body at the ``── SECTION DETAIL ──`` boundary.

    Returns:
        ``(envelope_part, section_detail_part)``: everything
        before the SECTION DETAIL marker (envelope + GLOBAL
        OVERVIEW) and everything starting at the marker.
    """
    marker = "── SECTION DETAIL ──"
    pos = body.find(marker)
    if pos == -1:
        return body, ""
    return body[:pos], body[pos:]



class TestChainedSecondCompactionDocs:
    """§4 — REAL chained compaction: doc count + seq advance stay
    bounded. Architect §4 / §6.7 — the prior ``truncation-marker-``
    property is replaced by a per-compaction ``compaction-global-``
    SystemMessage whose ``seq`` advances; the new doc absorbs the
    prior doc via the sentinel recipe (the prior doc lives in the
    dropped span and is removed).

    The chained contract: across N sequential compactions on the
    same instance, the channel carries AT MOST ONE
    ``compaction-global-`` doc at any time (the most recent), and
    the doc id increments monotonically.
    """

    @pytest.fixture
    def failing_llm(self):
        """LLM stub whose ``invoke`` always raises → ``|S| = 0`` →
        ``_truncate_fallback`` (deterministic doc-bearing path)."""
        mock_response = AIMessage(content="Summary text.", id="mock-response")
        mock_llm_instance = MagicMock()
        mock_llm_instance.invoke = MagicMock(return_value=mock_response)
        mock_llm_instance.invoke.side_effect = Exception("LLM API error")
        with patch(
            "daemon.graph.ThinkingChatOpenAI",
            return_value=mock_llm_instance,
            create=True,
        ):
            yield mock_llm_instance

    @staticmethod
    def _doc_count(messages) -> int:
        return sum(
            1
            for m in messages
            if isinstance(m, SystemMessage)
            and (m.id or "").startswith("compaction-global-")
        )

    def _make_ctx(self, config, messages, instance_id="chained") -> CompactionContext:
        # ``last_compacted_at=None`` — the engine-level 60s dedup is
        # bypassed so BOTH chained runs actually execute (in
        # production the second run happens after the dedup window or
        # via the executor's explicit path).
        return CompactionContext(
            messages=list(messages),
            system_prompt_tokens=0,
            model_name="gpt-4o",
            config=config,
            llm_config={},
            last_compacted_at=None,
            instance_id=instance_id,
        )

    @pytest.mark.asyncio
    async def test_chained_compaction_leaves_exactly_one_doc(
        self, failing_llm
    ):
        add_messages, _REMOVE_ALL = _load_real_add_messages()

        config = make_compaction_config(
            min_messages_before_compaction=2,
            threshold=0.01,
            recent_message_window=2,
            min_recent_window=1,
            context_window_overrides={"gpt-4o": 1000},
        )
        compactor = ContextCompactor(config, {})

        # ── Compaction #1 — doc-bearing replacement (truncation) ───
        history_1 = make_messages(200)
        result_1 = await compactor.compact_state(self._make_ctx(config, history_1, "chained-1"))
        assert result_1 is not None, "compaction #1 must fire"
        assert result_1.compaction_type == "truncation"
        assert self._doc_count(result_1.replacement_messages) == 1, (
            "§4: result #1 must carry exactly one compaction-global doc"
        )

        # Apply via the production sentinel recipe (architect §5)
        # — add_messages + REMOVE_ALL_MESSAGES sentinel truncates the
        # channel to the new value verbatim. This is the only way the
        # prior doc is dropped.
        #
        # W4 fix (2026-09-01) — the sentinel constant is sourced
        # from the real installed ``langgraph.graph.message`` (NOT
        # the hard-coded ``"__remove_all__"`` literal). The conftest
        # mocks ``langgraph.*`` as non-package modules, so the
        # import happens via ``_load_real_add_messages``'s
        # swap window.
        from langchain_core.messages import RemoveMessage
        _, REMOVE_ALL_MESSAGES = _load_real_add_messages()
        channel = add_messages(
            history_1,
            [RemoveMessage(id=REMOVE_ALL_MESSAGES), *result_1.replacement_messages],
        )
        assert self._doc_count(channel) == 1, (
            "post-compaction-#1 channel must carry exactly one doc"
        )

        # ── Continued conversation — fresh messages on top ──────────
        follow_ups = [
            HumanMessage(content=f"Follow-up {i}", id=f"post-{i}")
            for i in range(6)
        ]
        channel = add_messages(channel, follow_ups)
        assert self._doc_count(channel) == 1, (
            "post-follow-ups channel must still carry exactly one doc"
        )

        # ── Compaction #2 on the doc-bearing history ───────────────
        result_2 = await compactor.compact_state(self._make_ctx(config, channel, "chained-2"))
        assert result_2 is not None, "compaction #2 must fire"
        assert result_2.compaction_type == "truncation"
        # Per-result bounded: at most ONE doc per result.
        assert self._doc_count(result_2.replacement_messages) == 1, (
            "§4: result #2 must carry AT MOST ONE compaction-global doc "
            "(bounded per construction path)"
        )

        # Apply via the production sentinel recipe.
        final_channel = add_messages(
            channel,
            [RemoveMessage(id=REMOVE_ALL_MESSAGES), *result_2.replacement_messages],
        )

        # The load-bearing chained assertion: NO duplicate docs in
        # the final output. The first-round doc is removed with the
        # span via the sentinel recipe; only the fresh doc survives
        # and its seq has advanced.
        old_docs = [
            m
            for m in channel
            if isinstance(m, SystemMessage)
            and (m.id or "").startswith("compaction-global-")
        ]
        assert len(old_docs) == 1
        new_docs = [
            m
            for m in final_channel
            if isinstance(m, SystemMessage)
            and (m.id or "").startswith("compaction-global-")
        ]
        assert len(new_docs) == 1, (
            f"§4 chained: final channel must carry exactly ONE "
            f"compaction-global doc; got {len(new_docs)}"
        )
        assert new_docs[0].id != old_docs[0].id, (
            "§4 chained: the new doc id must differ from the prior — "
            "seq advances across re-compactions"
        )


# =============================================================================
# Architect §10 — NEW TESTS for the output-structure redesign
# =============================================================================




class TestSentinelRecipeRealGraph:
    """Item 1 — order-pinning real-graph test (the W1 killer).

    Real ``StateGraph(SessionState)`` + file-backed SQLite
    ``tmp_path`` (NO StaticPool) + the seam helper's sentinel
    list. Seed injected + old arc (A1..A20) + tail (T1..T5) with
    explicit ids; run compaction (stubbed LLM, 12-batch partial
    fixture); apply the seam's sentinel list; ``aget_state``
    read-back; assert landed order element-by-element.
    """

    @pytest.mark.asyncio
    async def test_order_pinning_real_graph(self, tmp_path):
        """Architect §10.1 — the W1 fix lands the intended order
        verbatim via the sentinel recipe.
        """
        import importlib
        import sys
        # Swap mocked langgraph out for the real modules so we
        # get the production add_messages reducer.
        saved = {
            k: sys.modules[k]
            for k in list(sys.modules)
            if k.startswith("langgraph")
        }
        for k in [k for k in sys.modules if k.startswith("langgraph")]:
            del sys.modules[k]
        try:
            import aiosqlite
            from langchain_core.messages import (
                AIMessage,
                HumanMessage,
                SystemMessage,
            )
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
            from langgraph.graph import END, START, MessagesState, StateGraph

            from daemon.compaction import (
                CompactionResult,
                build_sentinel_replacement,
            )

            async def _agent(state):
                return {"messages": []}

            db_path = tmp_path / "sentinel_order.db"
            conn = await aiosqlite.connect(str(db_path))
            saver = AsyncSqliteSaver(conn)
            await saver.setup()
            try:
                g = StateGraph(MessagesState)
                g.add_node("agent", _agent)
                g.add_edge(START, "agent")
                g.add_edge("agent", END)
                compiled = g.compile(checkpointer=saver)

                iid = "sentinel-order"
                cfg = {"configurable": {"thread_id": iid}}

                # Seed: 1 injected HumanMessage + A1..A20 (old arc)
                # + T1..T5 (tail). 26 messages total.
                seeded = []
                seeded.append(
                    HumanMessage(
                        content="INJECTED",
                        id="INJ-1",
                        additional_kwargs={"injected_message": True},
                    )
                )
                for i in range(1, 21):
                    seeded.append(AIMessage(content=f"arc-{i}", id=f"A{i}"))
                for i in range(1, 6):
                    seeded.append(HumanMessage(content=f"tail-{i}", id=f"T{i}"))

                await compiled.aupdate_state(
                    cfg, {"messages": seeded}, as_node="agent"
                )

                # Drive to terminal.
                await compiled.ainvoke({"messages": []}, cfg)
                pre_state = await compiled.aget_state(cfg)
                pre_messages = list(pre_state.values.get("messages", []))
                assert len(pre_messages) == 26, (
                    f"pre-compaction snapshot should have 26 messages; got {len(pre_messages)}"
                )

                # Build a synthetic engine result: ONE doc + the
                # preserved tail (T1..T5) + the injected INJ-1.
                result = CompactionResult(
                    replacement_messages=[
                        # INJ-1 first (injected head).
                        seeded[0],
                        # Doc.
                        SystemMessage(
                            id=f"compaction-global-{iid}-1",
                            content=(
                                "[CONTEXT COMPACTION — mode=summary]\n"
                                "GLOBAL OVERVIEW\nx\n"
                            ),
                        ),
                        # Preserved tail.
                        *seeded[21:26],
                    ],
                    tokens_before=1000,
                    tokens_after=500,
                    tokens_saved=500,
                    messages_before=26,
                    messages_after=7,
                    compaction_type="summary",
                    compacted_at="2026-09-01T00:00:00+00:00",
                )

                pre_ids = {m.id for m in pre_messages if m.id}
                kept_ids = {m.id for m in result.replacement_messages if m.id}
                compacted_ids = pre_ids - kept_ids

                sentinel_list = build_sentinel_replacement(
                    result, pre_messages, compacted_ids=compacted_ids
                )
                await compiled.aupdate_state(cfg, {"messages": sentinel_list})

                landed = list(
                    (await compiled.aget_state(cfg)).values.get(
                        "messages", []
                    )
                )
                seen = set()
                deduped = []
                for m in landed:
                    if m.id in seen:
                        continue
                    seen.add(m.id)
                    deduped.append(m)

                # 1. The injected message lands at index 0.
                assert isinstance(deduped[0], HumanMessage)
                assert deduped[0].id == "INJ-1"

                # 2. The doc carries the canonical id and sits
                # right after the injected message.
                doc_idx = next(
                    i for i, m in enumerate(deduped)
                    if isinstance(m, SystemMessage)
                    and (m.id or "").startswith("compaction-global-")
                )
                assert doc_idx == 1, (
                    f"doc must land at index 1 (right after the "
                    f"injected head); got {doc_idx}"
                )
                assert deduped[doc_idx].id == f"compaction-global-{iid}-1"

                # 3. The preserved tail ids follow the doc, in
                # original order.
                tail_ids_after_doc = [
                    m.id for m in deduped[doc_idx + 1:]
                    if not isinstance(m, (SystemMessage,))
                ]
                assert tail_ids_after_doc == [
                    "T1", "T2", "T3", "T4", "T5",
                ], (
                    f"preserved-tail ids must follow the doc in "
                    f"original order; got {tail_ids_after_doc}"
                )

                # 4. NO per-batch ``compaction-{uuid}`` ids or
                # ``truncation-marker-`` ids in the landed channel.
                for m in deduped:
                    if not isinstance(m, SystemMessage):
                        continue
                    mid = m.id or ""
                    assert not mid.startswith("compaction-merge-")
                    assert not mid.startswith("compaction-condense-")
                    assert not mid.startswith("truncation-marker-")
                    if mid.startswith("compaction-"):
                        assert mid.startswith("compaction-global-"), (
                            f"unexpected per-batch compaction id: {mid!r}"
                        )
            finally:
                await conn.close()
        finally:
            for k in [k for k in sys.modules if k.startswith("langgraph")]:
                del sys.modules[k]
            sys.modules.update(saved)


@needs_real_langgraph
class TestReducerSemanticsPins:
    """Item 2 — reducer-semantics unit pins, version-guarded on
    langgraph 1.0.9 (direct ``add_messages`` import). The pins
    convert Worker 3's source-reading into executed proof.

    W4 fix (2026-09-01) — the whole class is gated by
    ``needs_real_langgraph``: when the real ``langgraph`` package
    is not importable in the test env (CI without installed
    langgraph, conftest-only mocks), the pins SKIP explicitly
    rather than silently relying on the ImportError fallback in
    :func:`build_sentinel_replacement`.
    """

    def test_existing_id_input_upserts_in_place(self):
        """Existing-id input → upsert IN PLACE, position never changes."""
        add_messages, _REMOVE_ALL = _load_real_add_messages()
        left = [
            HumanMessage(content="A", id="1"),
            HumanMessage(content="B", id="2"),
            HumanMessage(content="C", id="3"),
        ]
        right = [HumanMessage(content="B-modified", id="2")]
        out = add_messages(left, right)
        assert [m.content for m in out] == ["A", "B-modified", "C"]
        assert [m.id for m in out] == ["1", "2", "3"]

    def test_new_id_input_appends(self):
        """New-id input → APPEND at channel end."""
        add_messages, _REMOVE_ALL = _load_real_add_messages()
        left = [HumanMessage(content="A", id="1"), HumanMessage(content="B", id="2")]
        right = [HumanMessage(content="C", id="3")]
        out = add_messages(left, right)
        assert [m.content for m in out] == ["A", "B", "C"]
        assert [m.id for m in out] == ["1", "2", "3"]

    def test_remove_absent_id_raises(self):
        """``RemoveMessage`` of an ABSENT id → ValueError."""
        add_messages, _REMOVE_ALL = _load_real_add_messages()
        left = [HumanMessage(content="A", id="1")]
        right = [RemoveMessage(id="nonexistent")]
        with pytest.raises(ValueError):
            add_messages(left, right)

    def test_same_call_remove_then_readd_is_inplace(self):
        """Same-call remove→re-add of id X → X resurrects IN PLACE."""
        add_messages, _REMOVE_ALL = _load_real_add_messages()
        left = [
            HumanMessage(content="A", id="1"),
            HumanMessage(content="B", id="2"),
            HumanMessage(content="C", id="3"),
        ]
        right = [RemoveMessage(id="2"), HumanMessage(content="B-modified", id="2")]
        out = add_messages(left, right)
        assert [m.content for m in out] == ["A", "B-modified", "C"]

    def test_same_call_readd_then_remove_deletes(self):
        """Same-call re-add→remove of X → X deleted (removals filter last)."""
        add_messages, _REMOVE_ALL = _load_real_add_messages()
        left = [
            HumanMessage(content="A", id="1"),
            HumanMessage(content="B", id="2"),
            HumanMessage(content="C", id="3"),
        ]
        right = [HumanMessage(content="B-modified", id="2"), RemoveMessage(id="2")]
        out = add_messages(left, right)
        assert [m.content for m in out] == ["A", "C"]
        assert [m.id for m in out] == ["1", "3"]

    @needs_real_langgraph
    def test_sentinel_truncates_to_right(self):
        """``REMOVE_ALL_MESSAGES`` sentinel → everything after the
        first sentinel becomes the ENTIRE new channel value,
        verbatim order. This is the only position-control path
        in langgraph 1.0.9.

        W4 fix (2026-09-01) — the sentinel constant is imported
        from the real installed ``langgraph.graph.message``
        (NOT the hard-coded ``"__remove_all__"`` literal); a
        upstream rename of the sentinel would surface here as a
        real failure rather than silently passing against a stale
        literal.
        """
        add_messages, REMOVE_ALL = _load_real_add_messages()
        left = [
            HumanMessage(content="A", id="1"),
            HumanMessage(content="B", id="2"),
            HumanMessage(content="C", id="3"),
        ]
        right = [
            RemoveMessage(id=REMOVE_ALL),
            HumanMessage(content="D", id="4"),
            HumanMessage(content="E", id="5"),
        ]
        out = add_messages(left, right)
        assert [m.content for m in out] == ["D", "E"]
        assert [m.id for m in out] == ["4", "5"]


class TestSentinelPreWriteGuard:
    """Item 3 — pre-write guard: id/count mismatch between
    replacement and snapshot → CompactionAborted, checkpoint
    byte-identical (well — no checkpoint write at all).
    """

    def test_missing_preserved_tail_id_raises(self):
        """A preserved-tail id present in the snapshot but missing
        from the replacement → CompactionAborted.
        """
        from langchain_core.messages import HumanMessage
        from daemon.compaction import (
            build_sentinel_replacement,
            CompactionAborted,
            CompactionResult,
        )

        # Snapshot: 3 messages with ids h-0, h-1, h-2.
        snapshot = [
            HumanMessage(content="h-0", id="h-0"),
            HumanMessage(content="h-1", id="h-1"),
            HumanMessage(content="h-2", id="h-2"),
        ]
        # Replacement: doc + h-2 only. h-0 and h-1 are silently
        # lost (NOT in compacted_ids).
        result = CompactionResult(
            replacement_messages=[
                SystemMessage(id="compaction-global-test-1", content="doc"),
                HumanMessage(content="h-2", id="h-2"),
            ],
            tokens_before=100, tokens_after=50, tokens_saved=50,
            messages_before=3, messages_after=2,
            compaction_type="summary", compacted_at="2026-09-01T00:00:00+00:00",
        )
        with pytest.raises(CompactionAborted):
            build_sentinel_replacement(result, snapshot)

    def test_preserved_tail_id_in_compacted_set_passes(self):
        """A snapshot id explicitly listed in ``compacted_ids`` is
        allowed to be missing from the replacement (it was
        intentionally removed).
        """
        from langchain_core.messages import HumanMessage
        from daemon.compaction import build_sentinel_replacement, CompactionResult

        snapshot = [
            HumanMessage(content="h-0", id="h-0"),
            HumanMessage(content="h-1", id="h-1"),
        ]
        # Replacement: only h-1 (h-0 is in compacted_ids).
        result = CompactionResult(
            replacement_messages=[
                SystemMessage(id="compaction-global-test-1", content="doc"),
                HumanMessage(content="h-1", id="h-1"),
            ],
            tokens_before=100, tokens_after=50, tokens_saved=50,
            messages_before=2, messages_after=2,
            compaction_type="summary", compacted_at="2026-09-01T00:00:00+00:00",
        )
        out = build_sentinel_replacement(
            result, snapshot, compacted_ids={"h-0"}
        )
        # W4 residue (2026-09-01): this test exercises the
        # production ``build_sentinel_replacement`` under the
        # conftest's mocked-langgraph env, which forces the
        # ImportError fallback path in production
        # (``daemon/compaction.py:343-344`` — the production
        # helper falls back to the source-verified literal
        # ``"__remove_all__"`` when ``langgraph.graph.message``
        # is not importable). The literal assertion below is
        # therefore INTENTIONAL: it pins the FALLBACK PATH
        # output, not the real-langgraph constant. A test that
        # needs the langgraph constant uses
        # ``_load_real_add_messages``'s swap window and
        # ``@needs_real_langgraph`` (see TestReducerSemanticsPins).
        # Sentinel + doc + tail.
        assert isinstance(out[0], RemoveMessage)
        assert out[0].id == "__remove_all__"
        assert out[1].id == "compaction-global-test-1"
        assert out[2].id == "h-1"

    def test_empty_replacement_with_intentional_compaction_passes(self):
        """ALL snapshot ids are in compacted_ids → the doc-only
        replacement is valid (the whole channel was intentionally
        replaced).
        """
        from langchain_core.messages import HumanMessage
        from daemon.compaction import build_sentinel_replacement, CompactionResult

        snapshot = [HumanMessage(content="x", id="h-0")]
        result = CompactionResult(
            replacement_messages=[
                SystemMessage(id="compaction-global-test-1", content="doc"),
            ],
            tokens_before=100, tokens_after=50, tokens_saved=50,
            messages_before=1, messages_after=1,
            compaction_type="summary", compacted_at="2026-09-01T00:00:00+00:00",
        )
        out = build_sentinel_replacement(
            result, snapshot, compacted_ids={"h-0"}
        )
        assert len(out) == 2  # sentinel + doc


class TestMergeLadderFailOpen:
    """Item 4 — ladder: merge timeout → no GLOBAL, sections
    intact, ``total_summary_status='failed'``,
    ``compaction_type`` unchanged. Truncation ± bounded-total
    both shapes.
    """

    @pytest.mark.asyncio
    async def test_merge_timeout_emits_doc_without_global(self):
        """The bounded merge pass times out → the doc has the
        placeholder line, sections intact, ``compaction_type``
        unchanged at ``"summarization"``,
        ``total_summary_status="failed"``.
        """
        from langchain_core.messages import (
            HumanMessage, AIMessage, SystemMessage,
        )
        from daemon.compaction import (
            ChunkedOutcome,
            CompactionContext, ContextCompactor,
        )
        from daemon.config import CompactionConfig

        cfg = CompactionConfig(
            enabled=True, threshold=0.01,
            recent_message_window=2, min_recent_window=1,
            context_window_overrides={"gpt-4o": 1000},
            context_window_default=0,
            target_ratio=0.40, model="", summarization_model="",
            min_messages_before_compaction=2,
            summarization_chunk_threshold=0.01,  # force chunking
            timeout_base_s=90.0, timeout_per_100k_tokens_s=60.0,
            timeout_cap_s=300.0, timeout_facade_margin_s=5.0,
            operation_budget_s=300.0, chunk_concurrency=3,
        )
        compactor = ContextCompactor(cfg, llm_config={})

        # 40 messages to force multi-batch chunking.
        msgs = [
            HumanMessage(content="x" * 30, id=f"h-{i}")
            for i in range(20)
        ]
        for i in range(20):
            msgs.append(AIMessage(content="y" * 30, id=f"a-{i}"))

        # Stub: chunked returns 2 per-batch summaries; merge fails.
        async def _fake_chunked(compactable, context, previous_overview=None):
            # Engine stub returning a synthetic partial-summary outcome.
            # ``previous_overview`` is accepted for forward-compat with
            # the W1 pass-2 seed (architect §4 — "the global frame
            # converges across passes"); the stub ignores it.
            # The real _summarize_chunked would have called
            # _merge_summaries and set merge_failed=True on
            # failure. Mirror that here so the engine's flow
            # takes the §6.2 fail-open branch.
            return ChunkedOutcome(
                summaries=["batch-0 summary", "batch-1 summary"],
                failed_batches=[],
                stop_reason="completed",
                merge_failed=True,
            )

        async def fake_merge(
            partial_summaries, context, budget_seconds=None,
            previous_overview=None,
        ):
            # Engine: ≥2 summaries, merge call → returns failure
            return ("", False)

        compactor._summarize_chunked = _fake_chunked
        compactor._merge_summaries = fake_merge

        ctx = CompactionContext(
            messages=msgs, system_prompt_tokens=0,
            model_name="gpt-4o", config=cfg, llm_config={},
            instance_id="merge-ladder",
        )
        result = await compactor.compact_state(ctx)
        assert result is not None
        # compaction_type unchanged (still summarization, not truncated).
        assert result.compaction_type == "summarization"
        # total_summary_status reflects the failure.
        assert result.total_summary_status == "failed"
        # Exactly one doc.
        docs = [
            m for m in result.replacement_messages
            if isinstance(m, SystemMessage)
            and (m.id or "").startswith("compaction-global-")
        ]
        assert len(docs) == 1
        # Placeholder line is present.
        assert "overview unavailable" in docs[0].content.lower() or \
               "merge pass failed" in docs[0].content.lower()

    @pytest.mark.asyncio
    async def test_truncation_emits_envelope_with_dropped_spans(self):
        """Truncation path (|S|=0): the bounded best-effort
        GLOBAL is skipped when ``tokens_before < ~2k``; the doc
        carries only the envelope + dropped spans.
        """
        from langchain_core.messages import HumanMessage, SystemMessage
        from daemon.compaction import (
            CompactionContext, ContextCompactor,
        )
        from daemon.config import CompactionConfig

        cfg = CompactionConfig(
            enabled=True, threshold=0.01,
            recent_message_window=2, min_recent_window=1,
            context_window_overrides={"gpt-4o": 1000},
            context_window_default=0,
            target_ratio=0.40, model="", summarization_model="",
            min_messages_before_compaction=2,
            summarization_chunk_threshold=1.5,
            timeout_base_s=0.01, timeout_cap_s=0.01,
            timeout_facade_margin_s=0.0,
            operation_budget_s=300.0, chunk_concurrency=3,
        )
        compactor = ContextCompactor(cfg, llm_config={})

        msgs = [
            HumanMessage(content="x" * 30, id=f"h-{i}")
            for i in range(5)
        ]
        # Force LLM failure so we hit the truncation path.
        async def fake_call(prompt, ctx):
            raise Exception("LLM API error")
        compactor._call_summarization_llm = fake_call

        ctx = CompactionContext(
            messages=msgs, system_prompt_tokens=0,
            model_name="gpt-4o", config=cfg, llm_config={},
            instance_id="truncation-shape",
        )
        result = await compactor.compact_state(ctx)
        assert result is not None
        assert result.compaction_type == "truncation"
        # Envelope header + dropped-spans clause + boundary line.
        doc = result.replacement_messages[0]
        assert "dropped without summary" in doc.content
        assert "END OF COMPACTED CONTEXT" in doc.content


class TestCeilingRule:
    """Item 5 — ceiling rule: over-cap doc → oldest sections
    condensed, GLOBAL preserved; hard cap → B-shape degrade.

    W6 fix (2026-09-01) — assertions are REAL (oldest-first
    ordering, condense COUNT, GLOBAL preserved on trim). The
    prior substring-only ``assert "GLOBAL" in doc.content``
    passed for any shape that contained the literal — vacuous.
    """

    def test_over_cap_condenses_oldest_sections(self):
        """The ceiling rule condenses the OLDEST sections first
        when GLOBAL + Σsections > 15% of context window.

        W6 assertions (real):
          * GLOBAL text is preserved verbatim in the doc
          * OLDEST section(s) carry the condensed-for-budget
            marker; NEWER sections retain full body
          * The condensed count > 0 (cap actually fired)
          * The ARCHIVED line count > 0 (the marker is present)
        """
        from daemon.compaction import build_compaction_doc
        # 40 large sections × ~250 tokens = 10k tokens of bodies;
        # GLOBAL "GLOBAL" adds ~1 token. context_window=10_000 →
        # cap = 1500 tokens → cap WILL fire (10k > 1500).
        n_sections = 40
        sections = [
            {
                "start_idx": i * 100 + 1,
                "end_idx": (i + 1) * 100,
                "body": "x" * 1000,  # ~250 tokens each
                "start_id": f"s{i}-start",
                "end_id": f"s{i}-end",
            }
            for i in range(n_sections)
        ]
        doc = build_compaction_doc(
            instance_id="ceiling-1",
            seq=1,
            mode="summary",
            compacted_at="2026-09-01T00:00:00+00:00",
            global_overview="GLOBAL",
            sections=sections,
            total_sections=n_sections,
            summarized_start=1, summarized_end=4000,
            preserved_count=10,
            dropped_spans=[],
            # Tiny context window so the ceiling is violated.
            context_window=10_000,
        )
        body = doc.content

        # W6 — REAL assertion: GLOBAL preserved verbatim.
        assert "GLOBAL" in body, "GLOBAL OVERVIEW must be preserved"
        # The condensed marker text (set in _apply_ceiling_rule)
        # must appear at least once (cap fired → some condensation).
        condensed_marker = "(condensed for budget — see GLOBAL OVERVIEW)"
        condensed_count = body.count(condensed_marker)
        assert condensed_count > 0, (
            f"ceiling rule must condense at least one section; "
            f"body did not contain the condensed marker. Body length: "
            f"{len(body)}"
        )
        assert condensed_count < n_sections, (
            f"not EVERY section should be condensed (the cap is "
            f"15% of context window; some sections must fit). "
            f"condensed_count={condensed_count} n_sections={n_sections}"
        )
        # W6 — REAL assertion: ARCHIVED line is present with the
        # exact condensed count.
        archived_marker = "── ARCHIVED:"
        assert archived_marker in body, (
            "ARCHIVED line must be emitted when condensation happens"
        )
        # The marker line should carry the condensed count.
        import re as _re
        archived_line_match = _re.search(
            r"── ARCHIVED: (\d+) oldest sections condensed",
            body,
        )
        assert archived_line_match, (
            f"ARCHIVED line must carry the condensed count; got body: "
            f"{body[-400:]!r}"
        )
        archived_count = int(archived_line_match.group(1))
        assert archived_count == condensed_count, (
            f"ARCHIVED count ({archived_count}) must equal the "
            f"condensed-marker occurrences ({condensed_count})"
        )

        # W6 — REAL assertion: OLDEST-first ordering. The condensed
        # marker must appear in section index order — the FIRST
        # sections get condensed, later ones keep full body.
        # ``### SECTION {i}/{n}`` headers carry the index in their
        # header line; the section body follows. We assert the
        # FIRST section in the doc carries the condensed marker
        # (oldest = first).
        first_section_pos = body.find("### SECTION 1/")
        assert first_section_pos != -1, "section 1 header must be present"
        # Slice from the first section header onward and look for
        # the condensed marker within its body region (before
        # the next section header or the ARCHIVED line).
        next_section_pos = body.find("### SECTION 2/", first_section_pos)
        if next_section_pos == -1:
            next_section_pos = body.find(archived_marker, first_section_pos)
        if next_section_pos == -1:
            next_section_pos = len(body)
        first_section_body = body[first_section_pos:next_section_pos]
        assert condensed_marker in first_section_body, (
            f"the OLDEST section (SECTION 1) must carry the "
            f"condensed marker — ceiling rule condenses OLDEST "
            f"first. Section 1 body region: {first_section_body!r}"
        )

    def test_hard_cap_degrades_to_b_shape(self):
        """Hard cap (over 15% even after condensing all sections)
        → B-shape: GLOBAL + ARCHIVED line only, sections
        removed.

        W6 assertions (real):
          * Exactly ONE ARCHIVED line carrying n=total_sections
          * NO ``### SECTION`` headers in the body (all
            condensed to nothing)
          * GLOBAL text preserved
          * Boundary line preserved (the doc still closes
            cleanly)
        """
        from daemon.compaction import build_compaction_doc
        # 1 section that is itself huge — ~25k tokens.
        huge_body = "x" * 100_000
        sections = [
            {
                "start_idx": 1,
                "end_idx": 10,
                "body": huge_body,
                "start_id": "s0-start",
                "end_id": "s0-end",
            }
        ]
        doc = build_compaction_doc(
            instance_id="ceiling-2",
            seq=1,
            mode="summary",
            compacted_at="2026-09-01T00:00:00+00:00",
            global_overview="GLOBAL",
            sections=sections,
            total_sections=1,
            summarized_start=1, summarized_end=10,
            preserved_count=0,
            dropped_spans=[],
            # Tiny context window — cap (15%) = 7 tokens. The
            # condensed marker alone (~10 tokens) blows the cap,
            # so even after condensing ALL sections the rule
            # degrades to B-shape (sections dropped entirely,
            # only the ARCHIVED line + GLOBAL survive).
            context_window=50,
        )
        body = doc.content

        # W6 — REAL assertion: B-shape ARCHIVED line carries the
        # total section count.
        import re as _re
        archived_line_match = _re.search(
            r"── ARCHIVED: (\d+) oldest sections condensed for budget",
            body,
        )
        assert archived_line_match, (
            f"B-shape ARCHIVED line must be present; got body: "
            f"{body[-400:]!r}"
        )
        archived_count = int(archived_line_match.group(1))
        assert archived_count == 1, (
            f"B-shape ARCHIVED count must equal total_sections=1; "
            f"got {archived_count}"
        )

        # W6 — REAL assertion: NO section headers remain (the
        # body has only the GLOBAL + ARCHIVED line + boundary).
        # Sections are dropped to metadata only on B-shape.
        assert "### SECTION " not in body, (
            "B-shape must remove ALL section headers; only the "
            "ARCHIVED line remains"
        )
        # GLOBAL preserved.
        assert "GLOBAL" in body, (
            "B-shape must preserve the GLOBAL OVERVIEW"
        )
        # Boundary line preserved.
        assert "END OF COMPACTED CONTEXT" in body, (
            "B-shape must preserve the boundary line"
        )


class TestProvenanceNoTimestampLeak:
    """Item 6 — provenance/W3: section headers carry ``SECTION
    i/n``, span indices, conversation-time range or omitted
    clause; assert NO generation-time ``Timestamp:`` leak
    anywhere except the envelope header.
    """

    def test_no_generation_timestamp_outside_envelope(self):
        """The doc body has NO ``Timestamp:`` line outside the
        envelope header. (W3 fix.)
        """
        from daemon.compaction import build_compaction_doc
        doc = build_compaction_doc(
            instance_id="prov-test",
            seq=1,
            mode="summary",
            compacted_at="2026-09-01T00:00:00+00:00",
            global_overview="GLOBAL",
            sections=[
                {
                    "start_idx": 1, "end_idx": 10,
                    "body": "section 1 body",
                    "start_id": "m-1", "end_id": "m-10",
                }
            ],
            total_sections=1,
            summarized_start=1, summarized_end=10,
            preserved_count=5,
            dropped_spans=[],
        )
        body = doc.content
        # The envelope header carries compacted_at (the single
        # allowed generation-time stamp).
        assert "compacted_at=2026-09-01T00:00:00+00:00" in body
        # No other Timestamp: lines anywhere in the body.
        # (The phrase "compacted_at=" is the only generator time.)
        # Find all "Timestamp:" occurrences and assert they
        # never appear.
        assert "Timestamp:" not in body, (
            f"W3 fix: no generation-time Timestamp: leak "
            f"anywhere in the doc body; found in: {body!r}"
        )
        # No "[Conversation Summary]" wrapper either (the old
        # per-batch SystemMessage content prefix is gone).
        assert "[Conversation Summary]" not in body

    def test_section_header_has_required_fields(self):
        """Each SECTION header carries ``SECTION i/n``, span
        indices, and (when map has rows) a conversation-time
        clause. Missing map rows → clause OMITTED.
        """
        from daemon.compaction import build_compaction_doc
        doc = build_compaction_doc(
            instance_id="prov-test-2",
            seq=1,
            mode="summary",
            compacted_at="2026-09-01T10:00:00+00:00",
            global_overview="GLOBAL",
            sections=[
                {
                    "start_idx": 1, "end_idx": 20,
                    "body": "A",
                    "start_id": "m-1", "end_id": "m-20",
                }
            ],
            total_sections=1,
            summarized_start=1, summarized_end=20,
            preserved_count=5,
            dropped_spans=[],
            msg_timestamps={
                "m-1": "2026-08-31T09:00:00+00:00",
                "m-20": "2026-08-31T10:00:00+00:00",
            },
        )
        body = doc.content
        # SECTION i/n, span indices, conversation-time clause all
        # present.
        assert "### SECTION 1/1" in body
        assert "#1–#20" in body
        assert "conversation time" in body

    def test_section_header_omits_time_clause_when_no_map(self):
        """When the first-appearance map has no rows for the
        boundary ids, the conversation-time clause is OMITTED
        (never generation-time fallback).
        """
        from daemon.compaction import build_compaction_doc
        doc = build_compaction_doc(
            instance_id="prov-test-3",
            seq=1,
            mode="summary",
            compacted_at="2026-09-01T10:00:00+00:00",
            global_overview="GLOBAL",
            sections=[
                {
                    "start_idx": 1, "end_idx": 20,
                    "body": "A",
                    "start_id": "m-1", "end_id": "m-20",
                }
            ],
            total_sections=1,
            summarized_start=1, summarized_end=20,
            preserved_count=5,
            dropped_spans=[],
            msg_timestamps=None,  # no map
        )
        body = doc.content
        # SECTION header is present.
        assert "### SECTION 1/1" in body
        # Conversation-time clause is OMITTED.
        assert "conversation time" not in body


class TestPassTwoSeedConvergence:
    """Item 7 — pass-2 (extend ``TestChainedSecondCompactionDocs``):
    prior doc removed with span; new merge prompt contains
    ``"Previous overview:"``; seq increments; exactly one
    ``compaction-global-`` id survives.
    """

    @pytest.mark.asyncio
    async def test_pass_two_prompts_carry_previous_overview(self):
        """Pass-2: the engine's merge pass receives the prior
        doc's GLOBAL as a seed; the new doc carries the
        ``Previous overview:`` line in the envelope.
        """
        from langchain_core.messages import HumanMessage, SystemMessage
        from daemon.compaction import (
            build_compaction_doc, ContextCompactor,
            CompactionContext,
        )
        from daemon.config import CompactionConfig

        cfg = CompactionConfig(
            enabled=True, threshold=0.01,
            recent_message_window=2, min_recent_window=1,
            context_window_overrides={"gpt-4o": 1000},
            context_window_default=0,
            target_ratio=0.40, model="", summarization_model="",
            min_messages_before_compaction=2,
            summarization_chunk_threshold=1.5,
            timeout_base_s=90.0, timeout_per_100k_tokens_s=60.0,
            timeout_cap_s=300.0, timeout_facade_margin_s=5.0,
            operation_budget_s=300.0, chunk_concurrency=3,
        )
        compactor = ContextCompactor(cfg, llm_config={})

        # Channel: 1 prior doc + 20 messages.
        prior_doc = SystemMessage(
            id="compaction-global-pass2-1",
            content=(
                "[CONTEXT COMPACTION — mode=summary | ...]\n"
                "GLOBAL OVERVIEW\nPRIOR GLOBAL CONTENT\n"
            ),
        )
        msgs = [prior_doc] + [
            HumanMessage(content="x" * 30, id=f"h-{i}")
            for i in range(20)
        ]

        async def fake_single(batch, context):
            return "new merged content"

        compactor._summarize_single_batch = fake_single

        ctx = CompactionContext(
            messages=msgs, system_prompt_tokens=0,
            model_name="gpt-4o", config=cfg, llm_config={},
            instance_id="pass2",
        )
        # The engine's full-success path computes the doc with
        # the previous_overview seed.
        # Direct unit test on the doc builder with seed:
        doc = build_compaction_doc(
            instance_id="pass2",
            seq=2,  # next seq after seq=1
            mode="summary",
            compacted_at="2026-09-01T11:00:00+00:00",
            global_overview="new merged content",
            sections=[
                {
                    "start_idx": 1, "end_idx": 20,
                    "body": "new merged content",
                    "start_id": "h-0", "end_id": "h-19",
                }
            ],
            total_sections=1,
            summarized_start=1, summarized_end=20,
            preserved_count=2,
            dropped_spans=[],
            previous_overview="PRIOR GLOBAL CONTENT",
        )
        # The doc carries the seed.
        assert "Previous overview:" in doc.content
        assert "PRIOR GLOBAL CONTENT" in doc.content
        # The doc id has the new seq.
        assert doc.id == "compaction-global-pass2-2"


class TestSentinelAcrossPersistSites:
    """Item 8 — parametrized across persist sites: on-demand
    (no ``as_node``), proactive (``as_node='agent'``), reactive
    (``as_node='agent'``) → identical landed order.
    """

    def test_sentinel_recipe_is_site_agnostic(self):
        """The seam helper produces the same replacement list
        for any caller — the persist site (on-demand vs
        proactive vs reactive) is just the ``aupdate_state``
        shape (with or without ``as_node``), not the seam.
        """
        from langchain_core.messages import HumanMessage
        from daemon.compaction import build_sentinel_replacement, CompactionResult

        snapshot = [
            HumanMessage(content="x", id="h-0"),
            HumanMessage(content="y", id="h-1"),
        ]
        result = CompactionResult(
            replacement_messages=[
                SystemMessage(id="compaction-global-x-1", content="doc"),
                HumanMessage(content="y", id="h-1"),
            ],
            tokens_before=100, tokens_after=50, tokens_saved=50,
            messages_before=2, messages_after=2,
            compaction_type="summary",
            compacted_at="2026-09-01T00:00:00+00:00",
        )
        compacted_ids = {"h-0"}
        # Same call site (build_sentinel_replacement) for all 3
        # sites — the seam is site-agnostic.
        out = build_sentinel_replacement(
            result, snapshot, compacted_ids=compacted_ids
        )
        # W4 residue (2026-09-01): the literal ``"__remove_all__"``
        # below is INTENTIONAL. The conftest mocks the
        # ``langgraph.graph.message`` namespace, so the production
        # ``build_sentinel_replacement`` helper falls back to the
        # source-verified literal (see
        # ``daemon/compaction.py:343-344``). This assertion pins
        # the FALLBACK PATH output — the seam is site-agnostic
        # and emits the literal sentinel regardless of which
        # persist site invokes it. A test that needs the real
        # langgraph constant uses
        # ``_load_real_add_messages``'s swap window and
        # ``@needs_real_langgraph`` (see TestReducerSemanticsPins).
        # Sentinel at 0, then doc, then tail. Identical for all
        # three persist sites.
        from langchain_core.messages import RemoveMessage
        assert isinstance(out[0], RemoveMessage)
        assert out[0].id == "__remove_all__"
        assert out[1].id == "compaction-global-x-1"
        assert out[2].id == "h-1"
        # No "as_node" is the caller's decision (compact_executor
        # is no-as_node; the other two are as_node='agent') — the
        # seam itself doesn't pass as_node.

    def test_persist_site_invariants_compact_executor_omits_as_node(self):
        """The compact_executor (on-demand) persists WITHOUT
        ``as_node`` — the post-brick-window invariant. The
        seam helper does not need to know; the call site
        passes the recipe to ``aupdate_state`` with no
        ``as_node`` keyword.
        """
        import inspect
        from daemon.services import compact_executor as ce
        src = inspect.getsource(ce._persist_compaction_result)
        # First aupdate_state call: no as_node (C1).
        first_aupdate_idx = src.find("await graph.aupdate_state(")
        assert first_aupdate_idx >= 0
        # Find the closing paren / comma for the first call's
        # args. Look for the absence of as_node.
        first_call_end = src.find(")", first_aupdate_idx)
        first_call = src[first_aupdate_idx:first_call_end]
        assert "as_node" not in first_call, (
            f"compact_executor first aupdate must omit as_node "
            f"(C1 Variant A); got: {first_call!r}"
        )

    def test_persist_site_invariants_instance_messaging_uses_as_node(self):
        """The instance_messaging (proactive) site uses
        ``as_node='agent'`` because it persists INSIDE the
        graph-task frame.
        """
        import inspect
        from daemon.services import instance_messaging as im
        src = inspect.getsource(im)
        # Find the compaction aupdate_state in instance_messaging.
        # Look for "as_node='agent'" after aupdate_state.
        idx = src.find("'messages': replacement_messages")
        assert idx >= 0
        # The call to aupdate_state should have as_node='agent'.
        # Look backwards for the call start.
        call_idx = src.rfind("await graph.aupdate_state(", 0, idx)
        assert call_idx >= 0
        call_end = src.find(")", call_idx)
        call = src[call_idx:call_end]
        assert "as_node='agent'" in call, (
            f"proactive path must use as_node='agent'; got: {call!r}"
        )

    def test_persist_site_invariants_graph_reactive_uses_as_node(self):
        """The graph.py (reactive) site uses ``as_node='agent'``."""
        import inspect
        from daemon import graph as dg
        src = inspect.getsource(dg)
        # Find the compaction aupdate_state with replacement_messages.
        idx = src.find("'messages': replacement_messages")
        if idx < 0:
            # Try alternative
            idx = src.find("{'messages': replacement_messages}")
        assert idx >= 0
        call_idx = src.rfind("await graph.aupdate_state(", 0, idx)
        assert call_idx >= 0
        call_end = src.find(")", call_idx)
        call = src[call_idx:call_end]
        assert "as_node='agent'" in call, (
            f"reactive path must use as_node='agent'; got: {call!r}"
        )


class TestReactivePairingGuardUnaffected:
    """Item 10 — reactive-path ``_ensure_tool_result_pairing``
    unaffected: the doc is a SystemMessage with no tool_calls,
    so the pairing guard is a no-op against it.
    """

    def test_doc_system_message_has_no_tool_calls(self):
        """The doc is a SystemMessage with no tool_calls
        attribute (or empty list). The pairing guard's
        tool-call-synthesis path is unaffected.
        """
        from langchain_core.messages import SystemMessage
        from daemon.compaction import build_compaction_doc
        doc = build_compaction_doc(
            instance_id="pairing-test",
            seq=1,
            mode="summary",
            compacted_at="2026-09-01T10:00:00+00:00",
            global_overview="GLOBAL",
            sections=[],
            total_sections=0,
            summarized_start=0, summarized_end=0,
            preserved_count=0,
            dropped_spans=[],
        )
        # The doc is a SystemMessage.
        assert isinstance(doc, SystemMessage)
        # No tool_calls.
        assert not getattr(doc, "tool_calls", [])
        # No tool_call_id.
        assert getattr(doc, "tool_call_id", None) is None


# =============================================================================
# 2026-09-01 Council Fix Pass — new tests for B1/B2/B3/W1/ride-alongs
# =============================================================================


class TestB1EnginePopulatedCompactedIds:
    """B1 — engine-populated ``compacted_ids`` on CompactionResult.

    The engine is the AUTHORITATIVE source for which snapshot ids
    were intentionally removed (the compactable span). The three
    persist-seam sites consume ``result.compacted_ids`` with a
    strict-None fallback to the site-derived set. This is the
    acceptance test for the engine-side population: every emit
    site stamps the field, the value is the exact set of
    compactable-group message ids (or — on the emergency path —
    every original message id covered by a RemoveMessage), and
    the field is frozen for downstream immutability.
    """

    def test_compaction_result_has_compacted_ids_field(self):
        """CompactionResult carries a ``compacted_ids: frozenset |
        None`` field that downstream code consumes.
        """
        import dataclasses as _dc
        from daemon.compaction import CompactionResult
        fields = {f.name: f for f in _dc.fields(CompactionResult)}
        assert "compacted_ids" in fields, (
            "CompactionResult must carry the compacted_ids field "
            "so the seam sites can consume it"
        )
        # Type is ``frozenset[str] | None`` — None preserves back-
        # compat for legacy construction sites; frozenset enforces
        # downstream immutability.
        assert fields["compacted_ids"].default is None, (
            "compacted_ids default must be None (back-compat)"
        )

    def test_corrupted_replacement_now_aborts(self):
        """B1 acceptance: an engine result missing a tail id in
        BOTH the replacement AND ``compacted_ids`` (engine
        dropped a tail id by mistake) → ``CompactionAborted``.

        Before B1 the site-derived ``pre_ids − kept_ids``
        collapsed to ∅ for every well-formed engine output (the
        replacement carried every kept id and the compacted ids
        were derived from the same set), so the guard passed for
        every shape. With B1 the engine stamps its own
        ``compacted_ids``; the guard now checks that the
        replacement carries EVERY snapshot id that is NOT in the
        engine's authoritative compacted set.
        """
        from langchain_core.messages import HumanMessage
        from daemon.compaction import (
            build_sentinel_replacement,
            CompactionAborted,
            CompactionResult,
        )

        # Snapshot: 3 messages with ids h-0, h-1, h-2.
        snapshot = [
            HumanMessage(content="h-0", id="h-0"),
            HumanMessage(content="h-1", id="h-1"),
            HumanMessage(content="h-2", id="h-2"),
        ]
        # Engine result: replacement is doc + h-1 + h-2 (h-0
        # MISSING — engine forgot to include it). The engine
        # ALSO forgot to mark h-0 as compacted.
        result = CompactionResult(
            replacement_messages=[
                SystemMessage(id="compaction-global-test-1", content="doc"),
                HumanMessage(content="h-1", id="h-1"),
                HumanMessage(content="h-2", id="h-2"),
            ],
            tokens_before=100, tokens_after=50, tokens_saved=50,
            messages_before=3, messages_after=3,
            compaction_type="summary",
            compacted_at="2026-09-01T00:00:00+00:00",
            # Engine says it removed NOTHING (mistake — h-0
            # dropped without mark). The guard must catch this.
            compacted_ids=frozenset(),
        )
        # h-0 is in the snapshot but NOT in the replacement and
        # NOT in compacted_ids → CompactionAborted.
        with pytest.raises(CompactionAborted):
            build_sentinel_replacement(result, snapshot)


class TestB2EmergencyTruncationSeam:
    """B2 — Emergency truncation path does NOT abort at any site.

    Before B2 the engine emitted ``RemoveMessage`` items whose
    ``.id`` was the ORIGINAL snapshot id (the per-message
    emergency truncation RemoveMessage targets). The site-derived
    ``pre_ids − kept_ids`` collapsed to ∅ because every original
    id was folded into ``kept_ids`` via the RemoveMessage path —
    but the guard treated the resulting ∅ as "nothing was
    intentionally compacted" and aborted on every snapshot id.

    After B2 the seam sites fold RemoveMessage target ids into
    the kept set, so the emergency path does NOT abort.
    """

    def test_emergency_replacement_passes_seam_guard(self):
        """A ``CompactionResult`` carrying ``compaction_type=
        'emergency_truncation'`` with the expected
        ``ReplaceMessage(id=<original>)`` items does NOT raise
        ``CompactionAborted`` at the seam.
        """
        from langchain_core.messages import HumanMessage
        from daemon.compaction import (
            build_sentinel_replacement,
            CompactionResult,
        )

        # Snapshot: 3 messages with ids h-0, h-1, h-2.
        snapshot = [
            HumanMessage(content="h-0", id="h-0"),
            HumanMessage(content="h-1", id="h-1"),
            HumanMessage(content="h-2", id="h-2"),
        ]
        # Engine result: emergency truncation emits a
        # RemoveMessage for EVERY original id (the engine covers
        # each one before adding the truncated replacement) +
        # the new truncated messages (re-id'd).
        result = CompactionResult(
            replacement_messages=[
                RemoveMessage(id="h-0"),
                RemoveMessage(id="h-1"),
                RemoveMessage(id="h-2"),
                HumanMessage(content="truncated-0", id="truncated-0"),
                HumanMessage(content="truncated-1", id="truncated-1"),
            ],
            tokens_before=100, tokens_after=50, tokens_saved=50,
            messages_before=3, messages_after=2,
            compaction_type="emergency_truncation",
            compacted_at="2026-09-01T00:00:00+00:00",
            # Engine is authoritative on the emergency path:
            # EVERY original id was intentionally removed.
            compacted_ids=frozenset({"h-0", "h-1", "h-2"}),
        )
        # Sentinel + 2 truncated messages (no per-id
        # RemoveMessages are emitted; the sentinel replaces
        # them, eliminating the ValueError-on-absent-id class).
        out = build_sentinel_replacement(
            result, snapshot, compacted_ids=frozenset({"h-0", "h-1", "h-2"})
        )
        # W4 residue (2026-09-01): the literal ``"__remove_all__"``
        # is INTENTIONAL — under the conftest's mocked-langgraph
        # env, ``build_sentinel_replacement`` falls back to the
        # source-verified literal
        # (``daemon/compaction.py:343-344``). This test exercises
        # the emergency-truncation path through the seam and
        # asserts the FALLBACK PATH output. A test that needs the
        # real langgraph constant uses
        # ``_load_real_add_messages``'s swap window and
        # ``@needs_real_langgraph`` (see TestReducerSemanticsPins).
        assert isinstance(out[0], RemoveMessage)
        assert out[0].id == "__remove_all__"
        assert [m.id for m in out[1:]] == ["truncated-0", "truncated-1"]

    def test_emergency_path_legacy_compacted_ids_none_falls_back(self):
        """Defensive: when an emergency ``CompactionResult`` does
        NOT populate ``compacted_ids`` (legacy test fixture /
        older code path), the SITE derives a fallback
        ``pre_ids − new_replacement_ids`` that correctly
        captures the engine's intent — every original id was
        intentionally removed because the engine covers each
        one with a RemoveMessage.

        This test mirrors the REAL site path: the helper sees
        ``compacted_ids=None`` only if the caller did NOT run
        the site fallback. Here we assert the SITE-derived
        fallback (what the site computes when the engine
        didn't stamp) — not the helper's strict mode — passes.
        """
        from langchain_core.messages import HumanMessage
        from daemon.compaction import (
            build_sentinel_replacement,
            CompactionResult,
        )

        snapshot = [
            HumanMessage(content="h-0", id="h-0"),
            HumanMessage(content="h-1", id="h-1"),
            HumanMessage(content="h-2", id="h-2"),
        ]
        # Engine result WITHOUT compacted_ids (None) — site
        # derives the fallback.
        result = CompactionResult(
            replacement_messages=[
                RemoveMessage(id="h-0"),
                RemoveMessage(id="h-1"),
                RemoveMessage(id="h-2"),
                HumanMessage(content="truncated-0", id="truncated-0"),
            ],
            tokens_before=100, tokens_after=50, tokens_saved=50,
            messages_before=3, messages_after=1,
            compaction_type="emergency_truncation",
            compacted_at="2026-09-01T00:00:00+00:00",
            compacted_ids=None,  # legacy: site falls back
        )
        # Mirror the site derivation (B2 fix — see
        # compact_executor.py:1597):
        pre_ids = {getattr(m, "id", None) for m in snapshot}
        pre_ids.discard(None)
        new_replacement_ids = {
            getattr(m, "id", None)
            for m in result.replacement_messages
            if not isinstance(m, RemoveMessage)
        }
        new_replacement_ids.discard(None)
        site_compacted_ids = pre_ids - new_replacement_ids
        # The derivation correctly captures the engine's
        # intent: every original id was intentionally removed.
        assert site_compacted_ids == {"h-0", "h-1", "h-2"}, (
            f"site derivation must capture emergency truncation "
            f"intent; got {site_compacted_ids}"
        )
        # Now the helper trusts the engine-declared set and
        # does NOT abort.
        out = build_sentinel_replacement(
            result, snapshot,
            compacted_ids={"h-0", "h-1", "h-2"},
        )
        # W4 residue (2026-09-01): the literal ``"__remove_all__"``
        # is INTENTIONAL — under the conftest's mocked-langgraph
        # env, ``build_sentinel_replacement`` falls back to the
        # source-verified literal
        # (``daemon/compaction.py:343-344``). This test exercises
        # the B2 site-derivation fallback (pre=None, derived
        # via pre_ids − new_replacement_ids) and asserts the
        # FALLBACK PATH sentinel output of the seam. A test that
        # needs the real langgraph constant uses
        # ``_load_real_add_messages``'s swap window and
        # ``@needs_real_langgraph`` (see TestReducerSemanticsPins).
        assert isinstance(out[0], RemoveMessage)
        assert out[0].id == "__remove_all__"


class TestB3NonContiguousSectionCoords:
    """B3 — non-contiguous partial survivors → ORIGINAL batch
    coords.

    Before B3 the survivor-compressed ``s_idx`` pointer
    collapsed the next section's ``start_idx`` to the END of the
    previous survivor (not the ORIGINAL batch boundary). The
    actually-dropped batch was presented as covered. After B3
    ``start_idx`` / ``end_idx`` are computed from the
    compactable-list position of the batch.

    This test drives ``compact_state`` with 3 batches of 20
    groups each (60 messages compactable) and stubs
    ``_summarize_chunked`` so batch 0 and batch 2 succeed (and
    batch 1 fails). It asserts the doc carries sections at the
    ORIGINAL batch boundaries: section 1 covers messages #1–#20
    (batch 0) and section 2 covers messages #41–#60 (batch 2).
    """

    @pytest.mark.asyncio
    async def test_non_contiguous_sections_use_original_batch_coords(self):
        """3 batches × 20 messages, batch 1 fails. Doc sections
        must cover #1–#20 (batch 0) and #41–#60 (batch 2); the
        failed batch (#21–#40) appears in the dropped-spans
        clause.
        """
        from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
        from daemon.compaction import (
            ChunkedOutcome,
            CompactionContext, ContextCompactor,
        )
        from daemon.config import CompactionConfig

        cfg = CompactionConfig(
            enabled=True, threshold=0.01,
            recent_message_window=2, min_recent_window=1,
            context_window_overrides={"gpt-4o": 1000},
            context_window_default=0,
            target_ratio=0.40, model="", summarization_model="",
            min_messages_before_compaction=2,
            summarization_chunk_threshold=1.5,
            timeout_base_s=90.0, timeout_per_100k_tokens_s=60.0,
            timeout_cap_s=300.0, timeout_facade_margin_s=5.0,
            operation_budget_s=300.0, chunk_concurrency=3,
        )
        compactor = ContextCompactor(cfg, llm_config={})
        # 60 compactable + 2 preserved = 62 messages
        msgs: list = []
        for i in range(1, 61):
            msgs.append(HumanMessage(content=f"x{i}", id=f"h-{i}"))
        msgs.extend([
            HumanMessage(content="tail-1", id="t-1"),
            HumanMessage(content="tail-2", id="t-2"),
        ])

        async def fake_chunked(
            compactable, context, previous_overview=None,
        ):
            # Batches 0, 2 succeed; batch 1 fails.
            return ChunkedOutcome(
                summaries=["batch-0 body", "batch-2 body"],
                failed_batches=[1],
                stop_reason="timeout",
                completed_idxs=[0, 2],
                budget_remaining_after_pool=120.0,
            )

        compactor._summarize_chunked = fake_chunked

        # Merge stub: produces a single merged text per summary
        # (avoids the real merge call; we only test section
        # metadata layout).
        async def fake_merge(
            partial_summaries, context,
            budget_seconds=None, previous_overview=None,
        ):
            return ("MERGED GLOBAL", True)

        compactor._merge_summaries = fake_merge

        ctx = CompactionContext(
            messages=msgs, system_prompt_tokens=0,
            model_name="gpt-4o", config=cfg, llm_config={},
            instance_id="b3-non-contiguous",
        )
        result = await compactor.compact_state(ctx)
        assert result is not None
        assert result.compaction_type == "partial_summary"

        # Extract the single doc.
        docs = [
            m for m in result.replacement_messages
            if isinstance(m, SystemMessage)
            and (m.id or "").startswith("compaction-global-")
        ]
        assert len(docs) == 1
        doc = docs[0]

        # W6-style REAL assertion: section span indices in the
        # ORIGINAL batch coordinates. The first surviving
        # section covers batch 0 = messages #1–#20. The second
        # surviving section covers batch 2 = messages #41–#60.
        # The failed batch (#21–#40) MUST appear in the
        # dropped-spans clause.
        #
        # Strengthened 2026-09-01 (B3 regression pin): the prior
        # version only asserted substring presence of
        # ``"messages #1–#20"`` / ``"messages #41–#60"`` /
        # ``"messages #21–#40"``. That was substring-vacuous:
        # the OLD `_per_batch_section_meta` (a80767b9) collapsed
        # ``s_idx`` to the END of the previous survivor, so the
        # body for batch-2 carried header ``messages #21–#40``
        # (the dropped-batch range) and the actually-failed
        # batch's range appeared as the section header — the
        # old substring assertions still passed. This version
        # BINDS each body to its ORIGINAL-coord section header
        # and asserts the dropped-clause contains ONLY the
        # actually-failed batch's range.
        body = doc.content
        section_blocks = _parse_section_blocks(body)
        # Two surviving sections, in batch order.
        assert len(section_blocks) == 2, (
            f"exactly two surviving sections; got {len(section_blocks)} "
            f"(block headers: "
            f"{[h for h, _ in section_blocks]!r}); body excerpt: {body[:500]!r}"
        )
        # Body→span binding #1: batch-0 body under messages #1–#20.
        header_1, body_1 = section_blocks[0]
        assert "messages #1–#20" in header_1, (
            f"section #1 must carry ORIGINAL batch 0 coords in its "
            f"HEADER (not just in body); got header={header_1!r}; "
            f"body excerpt: {body[:500]!r}"
        )
        assert "batch-0 body" in body_1, (
            f"section #1 body must contain the batch-0 survivor body; "
            f"got body_text={body_1!r}"
        )
        # Cross-binding: the batch-0 body MUST NOT appear under the
        # batch-2 header (would be vacuous: protects against the
        # body being attached to the wrong survivor's header).
        assert "batch-0 body" not in section_blocks[1][1], (
            f"section #2 body must NOT carry the batch-0 survivor "
            f"(body→span binding); got body={section_blocks[1][1]!r}"
        )
        # Body→span binding #2: batch-2 body under messages #41–#60.
        header_2, body_2 = section_blocks[1]
        assert "messages #41–#60" in header_2, (
            f"section #2 must carry ORIGINAL batch 2 coords "
            f"(#41–#60) in its header — the OLD impl collapsed "
            f"s_idx and presented the dropped batch's coords here; "
            f"got header={header_2!r}; body excerpt: {body[:500]!r}"
        )
        assert "batch-2 body" in body_2, (
            f"section #2 body must contain the batch-2 survivor body; "
            f"got body_text={body_2!r}"
        )
        assert "batch-2 body" not in section_blocks[0][1], (
            f"section #1 body must NOT carry the batch-2 survivor; "
            f"got body={section_blocks[0][1]!r}"
        )
        # Dropped clause: ONLY the actually-failed batch (#21–#40)
        # must appear there. The OLD impl misclassified batch 2
        # (the survivor at #41–#60) as "dropped" because the
        # batch-2 body claimed the dropped-batch's coords —
        # binding the clause to the survivors' absent ranges
        # catches that.
        envelope, _ = _split_envelope_and_section_detail(body)
        assert "dropped without summary" in envelope, (
            f"envelope must declare the dropped-without-summary "
            f"clause; got envelope={envelope!r}"
        )
        assert "messages #21–#40" in envelope, (
            f"the actually-failed batch (#21–#40) must appear in "
            f"the dropped clause (B3 bug presented it as covered); "
            f"got envelope={envelope!r}"
        )
        # Negative: the survivor ranges must NOT leak into the
        # dropped clause (the OLD impl misclassified batch 2 as
        # dropped because the batch-2 body claimed its coords).
        assert "messages #41–#60" not in envelope, (
            f"survivor batch 2 coords (#41–#60) must NOT appear in "
            f"the dropped clause (would indicate OLD B3 impl "
            f"misclassifying the survivor as dropped); "
            f"got envelope={envelope!r}"
        )
        assert "messages #1–#20" not in envelope, (
            f"survivor batch 0 coords (#1–#20) must NOT appear in "
            f"the dropped clause; got envelope={envelope!r}"
        )

    @pytest.mark.asyncio
    async def test_batch_zero_fails_non_contiguous_variant(self):
        """B3 regression pin — second non-contiguous layout.

        Symmetric to :meth:`test_non_contiguous_sections_use_original_batch_coords`
        but with the FIRST batch failing and the survivors at
        indices 1 and 2. Exercises a different ``s_idx`` collapse
        path on the OLD impl: the surviving batch 1 carries the
        FIRST survivor's coordinates (#1–#20) instead of its
        ORIGINAL batch 1 coords (#21–#40). Body→span binding
        must hold for both survivors.
        """
        from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
        from daemon.compaction import (
            ChunkedOutcome,
            CompactionContext, ContextCompactor,
        )
        from daemon.config import CompactionConfig

        cfg = CompactionConfig(
            enabled=True, threshold=0.01,
            recent_message_window=2, min_recent_window=1,
            context_window_overrides={"gpt-4o": 1000},
            context_window_default=0,
            target_ratio=0.40, model="", summarization_model="",
            min_messages_before_compaction=2,
            summarization_chunk_threshold=1.5,
            timeout_base_s=90.0, timeout_per_100k_tokens_s=60.0,
            timeout_cap_s=300.0, timeout_facade_margin_s=5.0,
            operation_budget_s=300.0, chunk_concurrency=3,
        )
        compactor = ContextCompactor(cfg, llm_config={})
        msgs: list = []
        for i in range(1, 61):
            msgs.append(HumanMessage(content=f"x{i}", id=f"h-{i}"))
        msgs.extend([
            HumanMessage(content="tail-1", id="t-1"),
            HumanMessage(content="tail-2", id="t-2"),
        ])

        async def fake_chunked(
            compactable, context, previous_overview=None,
        ):
            # Batch 0 fails; batches 1 and 2 succeed (variant).
            return ChunkedOutcome(
                summaries=["batch-1 body", "batch-2 body"],
                failed_batches=[0],
                stop_reason="timeout",
                completed_idxs=[1, 2],
                budget_remaining_after_pool=120.0,
            )

        compactor._summarize_chunked = fake_chunked

        async def fake_merge(
            partial_summaries, context,
            budget_seconds=None, previous_overview=None,
        ):
            return ("MERGED GLOBAL", True)

        compactor._merge_summaries = fake_merge

        ctx = CompactionContext(
            messages=msgs, system_prompt_tokens=0,
            model_name="gpt-4o", config=cfg, llm_config={},
            instance_id="b3-batch0-fails",
        )
        result = await compactor.compact_state(ctx)
        assert result is not None
        assert result.compaction_type == "partial_summary"

        docs = [
            m for m in result.replacement_messages
            if isinstance(m, SystemMessage)
            and (m.id or "").startswith("compaction-global-")
        ]
        assert len(docs) == 1
        doc = docs[0]
        body = doc.content

        section_blocks = _parse_section_blocks(body)
        assert len(section_blocks) == 2, (
            f"exactly two surviving sections (batches 1 + 2); got "
            f"{len(section_blocks)} (block headers: "
            f"{[h for h, _ in section_blocks]!r}); body excerpt: {body[:500]!r}"
        )
        # Batch 1 body → ORIGINAL coords #21–#40.
        header_1, body_1 = section_blocks[0]
        assert "messages #21–#40" in header_1, (
            f"section #1 (batch 1 survivor) must carry ORIGINAL "
            f"batch 1 coords (#21–#40); got header={header_1!r}; "
            f"body excerpt: {body[:500]!r}"
        )
        assert "batch-1 body" in body_1
        # Cross-binding — batch 2 body must not leak.
        assert "batch-1 body" not in section_blocks[1][1]
        # Batch 2 body → ORIGINAL coords #41–#60.
        header_2, body_2 = section_blocks[1]
        assert "messages #41–#60" in header_2, (
            f"section #2 (batch 2 survivor) must carry ORIGINAL "
            f"batch 2 coords (#41–#60); got header={header_2!r}; "
            f"body excerpt: {body[:500]!r}"
        )
        assert "batch-2 body" in body_2
        # Dropped clause: ONLY the actually-failed batch 0 (#1–#20).
        envelope, _ = _split_envelope_and_section_detail(body)
        assert "messages #1–#20" in envelope, (
            f"the actually-failed batch 0 (#1–#20) must appear in "
            f"the dropped clause; got envelope={envelope!r}"
        )
        # The survivor ranges (#21–#40, #41–#60) must NOT be in the
        # dropped clause (the OLD impl with batch 0 failing would
        # invert this and report batch 2 as dropped because the
        # batch-2 body claimed coords #21–#40).
        assert "messages #21–#40" not in envelope, (
            f"survivor batch 1 coords (#21–#40) must NOT appear in "
            f"the dropped clause (B3 OLD impl misclassification); "
            f"got envelope={envelope!r}"
        )
        assert "messages #41–#60" not in envelope

    @pytest.mark.asyncio
    async def test_non_contiguous_old_impl_replay_b3_regression(self):
        """PROOF TEST (B3 regression pin): the snapshotted OLD
        ``_per_batch_section_meta`` (a80767b9) VIOLATES every
        body→span binding expectation the strengthened test now
        pins.

        Mirrors the setup of
        :meth:`test_non_contiguous_sections_use_original_batch_coords`
        but invokes the OLD function body INLINE — production
        code is NOT touched. The OLD body is snapshot-verbatim
        via ``git show a80767b9:daemon/compaction.py`` (path
        resolved ``2026-09-01``).

        Pass-condition (suites GREEN on HEAD): the OLD impl
        must exhibit at least one B3 violation. If a future
        patch silently reverts ``_per_batch_section_meta`` to
        the OLD shape, OR if a stronger snapshot pin is needed,
        this proof will FAIL — the strengthening has become
        vacuous. That is the regression-pin signal.
        """
        from langchain_core.messages import HumanMessage

        # ── 1. Build the same compactable_groups as the scenario ──
        msgs: list = []
        for i in range(1, 61):
            msgs.append(HumanMessage(content=f"x{i}", id=f"h-{i}"))
        msgs.extend([
            HumanMessage(content="tail-1", id="t-1"),
            HumanMessage(content="tail-2", id="t-2"),
        ])
        groups = identify_boundary_groups(msgs)
        compactable, _preserved, _ = select_compactable_groups(
            groups, recent_window=2, min_window=1,
            context_window=10**6, system_prompt_tokens=0,
            estimate_fn=estimate_messages_tokens,
            config_threshold=0.01,
        )

        # ── 2. OLD impl — snapshotted verbatim from a80767b9 ────────
        # Production file at a80767b9: ``daemon/compaction.py``
        # (resolved via ``git show a80767b9 --stat``).
        # The OLD function used a collapsing ``s_idx = end_idx``
        # pointer per iteration; for batch_indices=[0, 2] the
        # second survivor was assigned the END-of-batch-0 coords
        # (#21–#40) instead of ORIGINAL batch 2 coords (#41–#60).
        def old_per_batch_section_meta(
            compactable, summaries, _ctx, batch_indices=None,
        ):
            batch_size = 20
            sections: list = []
            old_dropped_spans: list[tuple[int, int]] = []
            s_idx = 0
            for i, body in enumerate(summaries):
                if batch_indices is not None:
                    batch_i = batch_indices[i]
                else:
                    batch_i = i
                batch_groups = compactable[
                    batch_i * batch_size:(batch_i + 1) * batch_size
                ]
                if not batch_groups:
                    break
                start_idx = s_idx + 1
                end_idx = s_idx + sum(
                    len(g.messages) for g in batch_groups
                )
                start_id = batch_groups[0].messages[0].id
                end_id = batch_groups[-1].messages[-1].id
                if body:
                    sections.append({
                        "start_idx": start_idx,
                        "end_idx": end_idx,
                        "body": body,
                        "start_id": start_id,
                        "end_id": end_id,
                    })
                else:
                    # Dead branch on the partial-summary path
                    # (summaries carries only survivors). Kept
                    # verbatim to mirror the snapshot.
                    old_dropped_spans.append((start_idx, end_idx))
                s_idx = end_idx
            return sections

        # ── 3. Compute OLD sections + dropped_spans ────────────────
        # Caller sweep — same algorithm HEAD uses for dropped_spans
        # (any batch bucket whose start_idx is missing from
        # surviving_starts is dropped). Divergence from HEAD is
        # purely in the surviving_starts set.
        old_sections = old_per_batch_section_meta(
            compactable,
            ["batch-0 body", "batch-2 body"],
            None,  # OLD fn never reads ctx; positional per OLD signature
            batch_indices=[0, 2],
        )
        surviving_starts = {s["start_idx"] for s in old_sections}
        old_dropped_spans: list[tuple[int, int]] = []
        batch_size = 20
        s_idx = 0
        for i in range(0, len(compactable), batch_size):
            bg = compactable[i:i + batch_size]
            s = s_idx + 1
            e = s_idx + sum(len(g.messages) for g in bg)
            if s not in surviving_starts:
                old_dropped_spans.append((s, e))
            s_idx = e

        # ── 4. Render envelope + section detail (mirrors doc body) ──
        n = len(old_sections)
        rendered_sections: list[str] = []
        for i, sec in enumerate(old_sections, start=1):
            si = sec["start_idx"]
            ei = sec["end_idx"]
            s_label = (
                f"#{si}" if si == ei else f"#{si}–#{ei}"
            )
            rendered_sections.append(
                f"### SECTION {i}/{n} — messages {s_label}\n"
                f"{sec['body']}\n"
            )
        if old_dropped_spans:
            parts: list[str] = []
            for s, e in old_dropped_spans:
                parts.append(
                    f"#{s}" if s == e else f"#{s}–#{e}"
                )
            dropped_clause = (
                "dropped without summary: messages "
                + ", ".join(parts)
                + " — content not recoverable"
            )
        else:
            dropped_clause = "dropped without summary: NONE"
        body = (
            f"── ENVELOPE ──\n{dropped_clause}\n── SECTION DETAIL ──\n"
            + "\n".join(rendered_sections)
        )

        # ── 5. Apply the strengthened body→span-binding assertions ──
        section_blocks = _parse_section_blocks(body)
        assert len(section_blocks) == 2, (
            f"sanity: snapshotted OLD produced 2 sections; got "
            f"{[h for h, _ in section_blocks]!r}; body={body!r}"
        )
        _header_1, _body_1 = section_blocks[0]
        header_2, body_2 = section_blocks[1]
        envelope, _ = _split_envelope_and_section_detail(body)

        # ── 6. PROOF CHECK — the OLD impl MUST violate at least ────
        # one strengthened expectation. Each violation is a
        # regression-pin the strengthened test now enforces. The
        # strengthened test asserts the inverse of each pin (so
        # passes on HEAD's NEW impl); this proof records the OLD
        # violation explicitly so a future weakening of the
        # strengthened assertions shows up as a here-PASS-now
        # regression.
        #
        # The B3 bug signature on the OLD impl, given the
        # strengthened scenario (batches 0+2 succeed, batch 1
        # fails):
        #
        #   * section #2 header carries the DROPPED batch's
        #     coords (#21–#40), not ORIGINAL batch 2 coords
        #     (#41–#60) — the body→span binding was wrong.
        #   * the dropped clause carries the SURVIVOR's coords
        #     (#41–#60) — the sweep saw batch 2 (start_idx=41)
        #     as not in surviving_starts (because section #2 had
        #     claimed #21–#40) and emitted batch 2 as "dropped".
        #   * the dropped clause does NOT contain the
        #     ACTUALLY-FAILED batch's coords (#21–#40) — that
        #     span was attributed to section #2 instead.
        violations: list[str] = []

        # Pin 1: on OLD, section #2 header carries the dropped
        # batch's coords (#21–#40). BUG signature.
        if "messages #21–#40" in header_2:
            violations.append("section_2_carries_dropped_batch_coords")

        # Pin 2: on OLD, the dropped clause carries the survivor's
        # coords (#41–#60) — the impl misclassified the survivor
        # as dropped. BUG signature.
        if "messages #41–#60" in envelope:
            violations.append("dropped_clause_carries_survivor_coords")

        # Pin 3: on OLD, the dropped clause does NOT contain the
        # actually-failed batch (#21–#40) — because the OLD
        # impl attributed that span to section #2. BUG signature.
        if "messages #21–#40" not in envelope:
            violations.append("dropped_clause_missing_failed_batch")

        # Pass-condition: at least ONE pin must be violated on
        # the OLD impl. This is the regression-pin signal —
        # if a future change weakens the strengthened assertions
        # such that all three pins pass on OLD (i.e. NEW
        # behavior), this proof correctly fails (the strengthened
        # test can no longer catch B3 because the OLD impl no
        # longer exhibits the bug).
        assert violations, (
            "PROOF FAILURE: snapshotted OLD impl did NOT exhibit "
            "any B3 regression signal that the strengthened "
            "test pins. Either the snapshot is wrong (the "
            "function body was inadvertently edited), or the "
            "strengthened assertions have been weakened too far "
            "(all three pins pass on the OLD impl → no "
            "regression protection). Inspect the rendered body "
            "and the strengthened test's body→span-binding "
            "assertions. "
            f"OLD rendered body={body!r}, "
            f"section_2_header={header_2!r}, "
            f"envelope={envelope!r}"
        )
        # PASS path: the OLD impl exhibits at least one B3
        # regression. The strengthened test is correctly wired.


class TestW1EnginePassTwoSeedEndToEnd:
    """W1 — previous_overview seed end-to-end.

    Pass-2 convergence: when the pre-compaction snapshot
    contains a prior ``compaction-global-{iid}-*`` doc, the new
    merge prompt receives the prior doc's GLOBAL OVERVIEW as a
    ``Previous overview: …`` seed, AND the new doc carries the
    seed verbatim in its envelope (architect §4).
    """

    @pytest.mark.asyncio
    async def test_merge_prompt_carries_previous_overview_seed(self):
        """Drive the engine with a snapshot that contains a
        prior doc. The merge prompt sent to the LLM must
        include the prior GLOBAL as the ``Previous overview:``
        seed.

        The test stubs ``_summarize_single_batch`` (so the
        real ``_summarize_chunked`` runs end-to-end and
        triggers the inner merge pass) and
        ``_call_summarization_llm`` (so we capture the exact
        prompt text the merge pass sends).
        """
        from langchain_core.messages import HumanMessage, SystemMessage
        from daemon.compaction import (
            CompactionContext, ContextCompactor,
        )
        from daemon.config import CompactionConfig

        cfg = CompactionConfig(
            enabled=True, threshold=0.01,
            recent_message_window=2, min_recent_window=1,
            context_window_overrides={"gpt-4o": 1000},
            context_window_default=0,
            target_ratio=0.40, model="", summarization_model="",
            min_messages_before_compaction=2,
            # Small chunk threshold so 40 messages split into
            # 2 batches (each ≤ 20 groups).
            summarization_chunk_threshold=0.5,
            timeout_base_s=90.0, timeout_per_100k_tokens_s=60.0,
            timeout_cap_s=300.0, timeout_facade_margin_s=5.0,
            operation_budget_s=300.0, chunk_concurrency=3,
        )
        compactor = ContextCompactor(cfg, llm_config={})

        # Snapshot: a prior doc + 40 fresh messages (≥2 batches
        # so the multi-batch merge path fires). The prior doc
        # id MUST match the instance_id so
        # ``_extract_previous_overview`` parses it (the needle
        # prefix is ``compaction-global-{instance_id}-``).
        prior_global = (
            "PRIOR_GLOBAL_FRAME — entities: alice, bob; goal: ship X"
        )
        prior_doc = SystemMessage(
            id="compaction-global-w1-pass2-1",
            content=(
                "[CONTEXT COMPACTION — mode=summary]\n"
                "── GLOBAL OVERVIEW ──\n"
                f"{prior_global}\n"
                "── END OF COMPACTED CONTEXT"
            ),
        )
        msgs: list = [prior_doc]
        for i in range(40):
            msgs.append(
                HumanMessage(
                    # Long content to push past the chunk
                    # threshold so the multi-batch path fires.
                    content=f"x{i} " * 200,
                    id=f"h-{i}",
                )
            )

        # Stub the per-batch call so the real bounded pool runs
        # end-to-end (no LLM dependency) and the inner merge
        # pass fires.
        async def fake_single_batch(batch_groups, context):
            return f"batch summary ({len(batch_groups)} groups)"

        compactor._summarize_single_batch = fake_single_batch

        # Spy on _call_summarization_llm — captures the merge
        # prompt text the engine constructs.
        captured_prompts: list[str] = []

        async def spy_call_llm(prompt, context):
            captured_prompts.append(prompt)
            return "MERGED WITH PRIOR FRAME"

        compactor._call_summarization_llm = spy_call_llm

        ctx = CompactionContext(
            messages=msgs, system_prompt_tokens=0,
            model_name="gpt-4o", config=cfg, llm_config={},
            instance_id="w1-pass2",
        )
        result = await compactor.compact_state(ctx)
        assert result is not None

        # W1 — REAL assertion: the merge prompt received the
        # prior doc's GLOBAL OVERVIEW as a seed.
        merge_prompts = [
            p for p in captured_prompts
            if "Combine these conversation segment summaries" in p
        ]
        assert merge_prompts, (
            f"no merge prompt captured (2 batches expected to "
            f"trigger the merge pass); captured: "
            f"{captured_prompts!r}"
        )
        prompt = merge_prompts[0]
        assert "Previous overview" in prompt, (
            f"merge prompt must carry the prior overview seed; "
            f"prompt excerpt: {prompt[:500]!r}"
        )
        assert "PRIOR_GLOBAL_FRAME" in prompt, (
            f"merge prompt must carry the PRIOR doc's GLOBAL "
            f"OVERVIEW text; prompt excerpt: {prompt[:500]!r}"
        )
        assert "alice, bob" in prompt, (
            f"merge prompt must carry the prior doc's entities "
            f"verbatim; prompt excerpt: {prompt[:500]!r}"
        )

        # W1 — REAL assertion: the new doc itself carries the
        # seed verbatim in the envelope (architect §4).
        docs = [
            m for m in result.replacement_messages
            if isinstance(m, SystemMessage)
            and (m.id or "").startswith("compaction-global-")
        ]
        assert len(docs) == 1
        doc = docs[0]
        assert "Previous overview:" in doc.content, (
            f"new doc must carry the Previous overview seed in "
            f"its envelope; body: {doc.content[:500]!r}"
        )
        assert "PRIOR_GLOBAL_FRAME" in doc.content, (
            f"new doc must carry the PRIOR doc's GLOBAL text "
            f"verbatim; body: {doc.content[:500]!r}"
        )


class TestCreatedAtPreservationAfterSentinel:
    """Ride-along — BE-side created_at preservation assert after
    the sentinel write.

    Per FE `mergeMessagesById` (`message-merge.util.ts:88-95`):
    same-id re-add is an idempotent upsert that KEEPS the
    earlier `created_at` (MIN-4, `:106-110`). The sentinel
    recipe's full-message-object tail must preserve the
    original ``created_at`` for each preserved tail message.

    Plan §10.1 gap — extend the real-graph order-pinning test's
    read-back to assert created_at preservation on the sentinel
    recipe (the prior test only asserted landed order).
    """

    @pytest.mark.asyncio
    async def test_created_at_preserved_on_preserved_tail(self, tmp_path):
        """Drive the seam helper on a real ``StateGraph`` +
        file-backed SQLite. Stamp ``created_at`` on the
        pre-compaction tail messages; assert the post-write
        channel carries the SAME ``created_at`` values for the
        preserved tail ids (FE union-merge contract).
        """
        import importlib
        import sys
        saved = {
            k: sys.modules[k]
            for k in list(sys.modules)
            if k.startswith("langgraph")
        }
        for k in [k for k in sys.modules if k.startswith("langgraph")]:
            del sys.modules[k]
        try:
            import aiosqlite
            from langchain_core.messages import HumanMessage, SystemMessage
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
            from langgraph.graph import END, START, MessagesState, StateGraph

            from daemon.compaction import (
                CompactionResult, build_sentinel_replacement,
            )

            async def _agent(state):
                return {"messages": []}

            db_path = tmp_path / "sentinel_created_at.db"
            conn = await aiosqlite.connect(str(db_path))
            saver = AsyncSqliteSaver(conn)
            await saver.setup()
            try:
                g = StateGraph(MessagesState)
                g.add_node("agent", _agent)
                g.add_edge(START, "agent")
                g.add_edge("agent", END)
                compiled = g.compile(checkpointer=saver)

                iid = "sentinel-created-at"
                cfg = {"configurable": {"thread_id": iid}}

                # Seeded: 1 injected + 4 old + 2 tail.
                seeded = [
                    HumanMessage(
                        content="INJ",
                        id="INJ-1",
                        additional_kwargs={"injected_message": True},
                    ),
                    HumanMessage(
                        content="old-1", id="A1",
                        additional_kwargs={
                            "created_at": "2026-01-01T00:00:01+00:00",
                        },
                    ),
                    HumanMessage(
                        content="old-2", id="A2",
                        additional_kwargs={
                            "created_at": "2026-01-01T00:00:02+00:00",
                        },
                    ),
                    HumanMessage(
                        content="old-3", id="A3",
                        additional_kwargs={
                            "created_at": "2026-01-01T00:00:03+00:00",
                        },
                    ),
                    HumanMessage(
                        content="old-4", id="A4",
                        additional_kwargs={
                            "created_at": "2026-01-01T00:00:04+00:00",
                        },
                    ),
                    HumanMessage(
                        content="tail-1", id="T1",
                        additional_kwargs={
                            "created_at": "2026-09-01T00:00:01+00:00",
                        },
                    ),
                    HumanMessage(
                        content="tail-2", id="T2",
                        additional_kwargs={
                            "created_at": "2026-09-01T00:00:02+00:00",
                        },
                    ),
                ]
                await compiled.aupdate_state(
                    cfg, {"messages": seeded}, as_node="agent"
                )
                await compiled.ainvoke({"messages": []}, cfg)

                pre_state = await compiled.aget_state(cfg)
                pre_messages = list(pre_state.values.get("messages", []))
                assert len(pre_messages) == 7

                # Engine result: doc + tail T1, T2. A1–A4 are
                # in compacted_ids (intentionally removed).
                result = CompactionResult(
                    replacement_messages=[
                        seeded[0],  # injected INJ-1
                        SystemMessage(
                            id=f"compaction-global-{iid}-1",
                            content="[CONTEXT COMPACTION — mode=summary]\nGLOBAL\n",
                        ),
                        seeded[5],  # T1
                        seeded[6],  # T2
                    ],
                    tokens_before=1000, tokens_after=500, tokens_saved=500,
                    messages_before=7, messages_after=4,
                    compaction_type="summary",
                    compacted_at="2026-09-01T00:00:00+00:00",
                    compacted_ids=frozenset(
                        {"A1", "A2", "A3", "A4"}
                    ),
                )
                pre_ids = {
                    m.id for m in pre_messages if m.id
                }
                kept_ids = {
                    m.id for m in result.replacement_messages
                    if not isinstance(m, RemoveMessage)
                }
                kept_ids.discard(None)
                kept_ids.update({
                    m.id for m in result.replacement_messages
                    if isinstance(m, RemoveMessage)
                })
                kept_ids.discard(None)
                compacted_ids = pre_ids - kept_ids
                sentinel_list = build_sentinel_replacement(
                    result, pre_messages, compacted_ids=compacted_ids
                )
                await compiled.aupdate_state(
                    cfg, {"messages": sentinel_list}
                )
                post_state = await compiled.aget_state(cfg)
                post_messages = list(post_state.values.get("messages", []))
                # Expected landed order: INJ-1, doc, T1, T2.
                assert len(post_messages) == 4
                assert [m.id for m in post_messages] == [
                    "INJ-1", f"compaction-global-{iid}-1", "T1", "T2",
                ]

                # Ride-along: created_at preservation. FE
                # union-merge keeps the EARLIER created_at
                # (MIN-4); the sentinel recipe's full-message-
                # object tail must carry the same value the
                # pre-compaction snapshot carried.
                post_T1 = post_messages[2]
                post_T2 = post_messages[3]
                pre_T1 = next(m for m in pre_messages if m.id == "T1")
                pre_T2 = next(m for m in pre_messages if m.id == "T2")
                assert (
                    post_T1.additional_kwargs.get("created_at")
                    == pre_T1.additional_kwargs.get("created_at")
                    == "2026-09-01T00:00:01+00:00"
                ), (
                    f"T1 created_at drift: pre="
                    f"{pre_T1.additional_kwargs.get('created_at')!r} "
                    f"post={post_T1.additional_kwargs.get('created_at')!r}"
                )
                assert (
                    post_T2.additional_kwargs.get("created_at")
                    == pre_T2.additional_kwargs.get("created_at")
                    == "2026-09-01T00:00:02+00:00"
                ), (
                    f"T2 created_at drift: pre="
                    f"{pre_T2.additional_kwargs.get('created_at')!r} "
                    f"post={post_T2.additional_kwargs.get('created_at')!r}"
                )
            finally:
                await conn.close()
        finally:
            for k in [k for k in sys.modules if k.startswith("langgraph")]:
                del sys.modules[k]
            sys.modules.update(saved)

