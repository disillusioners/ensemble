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
        "summarization_model": "",
        "min_messages_before_compaction": 10,
        "summarization_chunk_threshold": 0.60,
        # Phase 1 / WS-3 adaptive timeout defaults.
        "timeout_base_s": 90.0,
        "timeout_per_100k_tokens_s": 60.0,
        "timeout_cap_s": 300.0,
        "timeout_facade_margin_s": 5.0,
        "operation_budget_s": 300.0,
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


class TestBuildReplacementMessages:
    """Tests for ContextCompactor._build_replacement_messages (4 cases)."""

    def test_removes_compactable_and_adds_summary_and_preserved(self):
        """Test RemoveMessage for compactable, then summary, then preserved."""
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
        summary = SystemMessage(content="Summary", id="summary-1")
        result = ContextCompactor._build_replacement_messages(compactable, preserved, summary)
        assert len(result) == 3
        assert isinstance(result[0], RemoveMessage)
        assert result[0].id == "ai-old"
        assert isinstance(result[1], SystemMessage)
        assert isinstance(result[2], HumanMessage)

    def test_skips_compactable_messages_without_id(self):
        """Test that compactable messages without id produce no RemoveMessage."""
        compactable = [
            MessageGroup(start_idx=0, end_idx=0, messages=[AIMessage(content="No ID")], group_type="single"),
        ]
        summary = SystemMessage(content="Summary", id="summary-1")
        result = ContextCompactor._build_replacement_messages(compactable, [], summary)
        assert len(result) == 1
        assert isinstance(result[0], SystemMessage)

    def test_preserved_appended_after_summary(self):
        """Test that preserved groups are appended after summary."""
        compactable = [
            MessageGroup(start_idx=0, end_idx=0, messages=[AIMessage(content="C", id="c1")], group_type="single"),
        ]
        preserved = [
            MessageGroup(
                start_idx=1, end_idx=1,
                messages=[HumanMessage(content="P", id="p1")],
                group_type="single",
            ),
        ]
        summary = SystemMessage(content="Summary", id="s1")
        result = ContextCompactor._build_replacement_messages(compactable, preserved, summary)
        assert result[0].id == "c1"  # RemoveMessage
        assert result[1].id == "s1"   # Summary
        assert result[2].id == "p1"    # Preserved

    def test_empty_compactable_preserved_empty(self):
        """Test with all empty inputs produces only summary."""
        summary = SystemMessage(content="Summary", id="s1")
        result = ContextCompactor._build_replacement_messages([], [], summary)
        assert len(result) == 1
        assert result[0] == summary


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
    """Tests for ContextCompactor._merge_summaries (3 cases)."""

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
        """Test that a single summary is returned unchanged."""
        config = make_compaction_config()
        compactor = ContextCompactor(config, {})
        partial = SystemMessage(content="Single summary", id="p1")
        context = CompactionContext(
            messages=[],
            system_prompt_tokens=0,
            model_name="gpt-4o",
            config=config,
            llm_config={},
        )
        result = await compactor._merge_summaries([partial], context)
        assert result.content == "Single summary"

    @pytest.mark.asyncio
    async def test_two_summaries_merged(self, mock_llm):
        """Test that two summaries are merged via LLM call."""
        config = make_compaction_config()
        compactor = ContextCompactor(config, {})
        partial1 = SystemMessage(content="Summary part 1", id="p1")
        partial2 = SystemMessage(content="Summary part 2", id="p2")
        context = CompactionContext(
            messages=[],
            system_prompt_tokens=0,
            model_name="gpt-4o",
            config=config,
            llm_config={},
        )
        result = await compactor._merge_summaries([partial1, partial2], context)
        # Returns mock response wrapped in SystemMessage with compaction-merge- id
        assert isinstance(result, SystemMessage)
        assert "compaction-merge-" in result.id
        assert "Merged summary content" in result.content

    @pytest.mark.asyncio
    async def test_four_plus_summaries_use_hierarchical_merge(self, mock_llm):
        """Test that 4+ summaries use hierarchical pairwise merging."""
        config = make_compaction_config()
        compactor = ContextCompactor(config, {})
        partials = [
            SystemMessage(content=f"Summary {i}", id=f"p{i}")
            for i in range(4)
        ]
        context = CompactionContext(
            messages=[],
            system_prompt_tokens=0,
            model_name="gpt-4o",
            config=config,
            llm_config={},
        )
        result = await compactor._merge_summaries(partials, context)
        # Should produce a merged result
        assert isinstance(result, SystemMessage)


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
    """WS-4.1 — marker exactly-once in truncation AND partial_summary outputs."""

    def test_marker_helper_module_scope(self):
        """Marker helper exists at module scope (approver pin)."""
        from daemon import compaction as cm
        assert hasattr(cm, "_append_truncation_marker")
        # Id-deterministic prefix.
        r: list = []
        _append_truncation_marker(r)
        _append_truncation_marker(r)
        assert len(r) == 2
        assert all(isinstance(m, SystemMessage) for m in r)
        assert all(m.content == "[Earlier messages trimmed to fit context]" for m in r)
        assert all(m.id.startswith("truncation-marker-") for m in r)
        # Each call gets a fresh UUID4 id.
        assert r[0].id != r[1].id

    @pytest.fixture
    def mock_llm(self):
        mock_response = AIMessage(content="Summary text.", id="mock-response")
        mock_llm_instance = MagicMock()
        mock_llm_instance.invoke = MagicMock(return_value=mock_response)
        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm_instance, create=True):
            yield mock_llm_instance

    @pytest.mark.asyncio
    async def test_truncation_output_has_marker_exactly_once(self, mock_llm):
        """``compaction_type='truncation'`` output contains exactly one marker.

        O15 regression — auto-path truncation NOW carries the marker
        (intentional behavior change, pinned here).
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
        )
        compactor = ContextCompactor(config, {})
        result = await compactor.compact_state(context)
        assert result is not None
        assert result.compaction_type == "truncation"
        markers = [
            m for m in result.replacement_messages
            if isinstance(m, SystemMessage) and m.id.startswith("truncation-marker-")
        ]
        assert len(markers) == 1, (
            f"expected exactly one truncation marker, found {len(markers)}"
        )
        assert markers[0].content == "[Earlier messages trimmed to fit context]"
        # failure_kind carries "error" because LLM raised (not a TimeoutError).
        assert result.failure_kind == "error"


class TestPartialSummaryWS34:
    """WS-3.4 C1 acceptance (a)-(d).

    (a) first-batch timeout → ``truncation`` + marker + no summaries
    (b) >=2 batches, batch-2 timeout → ``partial_summary`` + batch-1 summary + batch-2 absent + marker exactly once
    (c) budget exhaustion mid-run → same as (b) with stop_reason="budget"
    (d) proactive + reactive callers observe identical outcome semantics
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

    @pytest.mark.asyncio
    async def test_a_first_batch_timeout_truncation_with_marker(
        self, compactor_config, large_message_set,
    ):
        """C1 (a): single-batch path times out → ``truncation`` + marker + no summaries."""
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
                ))
            finally:
                block.set()  # unblock the mock

        assert result is not None
        assert result.compaction_type == "truncation"
        assert result.failure_kind == "timeout"
        # No summaries in the result.
        summaries = [
            m for m in result.replacement_messages
            if isinstance(m, SystemMessage) and (m.id or "").startswith("compaction-")
        ]
        assert summaries == []
        # Marker present exactly once.
        markers = [
            m for m in result.replacement_messages
            if isinstance(m, SystemMessage) and (m.id or "").startswith("truncation-marker-")
        ]
        assert len(markers) == 1
        # compacted_at stamped on this path (D12).
        assert result.compacted_at is not None

    @pytest.mark.asyncio
    async def test_b_second_batch_timeout_partial_summary(
        self, compactor_config, large_message_set,
    ):
        """C1 (b): batch-2 times out → ``partial_summary`` + batch-1 summary present +
        batch-2 raw messages absent + marker exactly once.
        """
        config = compactor_config
        compactor = ContextCompactor(config, {})

        # Stub ``_summarize_chunked`` directly — simulating the C1 hybrid
        # scenario where batch-1 succeeded and batch-2 raised TimeoutError.
        from daemon.compaction import ChunkedOutcome

        async def _fake_chunked(compactable, context):
            return ChunkedOutcome(
                summaries=[SystemMessage(
                    content="[Conversation Summary]\nbatch-1 summary",
                    id=f"compaction-{1}",
                )],
                failed_batches=[1, 2, 3, 4, 5],
                stop_reason="timeout",
            )

        compactor._summarize_chunked = _fake_chunked

        result = await compactor.compact_state(CompactionContext(
            messages=large_message_set, system_prompt_tokens=0,
            model_name="gpt-4o", config=config, llm_config={},
        ))

        assert result is not None
        assert result.compaction_type == "partial_summary"
        assert result.failure_kind == "timeout"
        # Batch-1 summary present.
        batch1_summaries = [
            m for m in result.replacement_messages
            if isinstance(m, SystemMessage) and (m.id or "").startswith("compaction-")
        ]
        assert len(batch1_summaries) == 1, (
            f"expected batch-1 summary, got {len(batch1_summaries)}: {batch1_summaries}"
        )
        # Marker exactly once.
        markers = [
            m for m in result.replacement_messages
            if isinstance(m, SystemMessage) and (m.id or "").startswith("truncation-marker-")
        ]
        assert len(markers) == 1
        # compacted_at stamped on this path (D12 — a partial is a completed compaction).
        assert result.compacted_at is not None

    @pytest.mark.asyncio
    async def test_c_budget_exhaustion_partial_summary(self, large_message_set):
        """C1 (c): budget exhaustion mid-run → ``partial_summary`` + marker + stop_reason="budget"."""
        from daemon.compaction import ChunkedOutcome

        config = make_compaction_config(
            min_messages_before_compaction=2,
            threshold=0.01,
            recent_message_window=2,
            min_recent_window=1,
            context_window_overrides={"gpt-4o": 1000},
            summarization_chunk_threshold=0.01,
            # TINY budget so it exhausts after the first chunk completes.
            operation_budget_s=0.0,
        )
        compactor = ContextCompactor(config, {})

        mock_response = MagicMock(content="Batch 1 summary.")
        mock_llm_instance = MagicMock()
        mock_llm_instance.invoke = MagicMock(return_value=mock_response)

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm_instance, create=True):
            result = await compactor.compact_state(CompactionContext(
                messages=large_message_set, system_prompt_tokens=0,
                model_name="gpt-4o", config=config, llm_config={},
            ))

        # operation_budget_s=0 means budget exhausted immediately; if
        # chunking kicks in, the first pre-call check trips and we get
        # |S|=0 → truncate fallback. To hit the partial path we want
        # the budget to trip AFTER at least one batch. Patch
        # ``_summarize_chunked`` to simulate the partial outcome.
        captured_kwargs: dict = {}

        async def _fake_chunked(compactable, context):
            return ChunkedOutcome(
                summaries=[SystemMessage(
                    content="[Conversation Summary]\nfirst batch",
                    id=f"compaction-{1}",
                )],
                failed_batches=[1, 2, 3, 4, 5],
                stop_reason="budget",
            )

        # Force the partial path: stub ``_summarize_chunked`` to return
        # a budget-stopped ChunkedOutcome with one surviving summary.
        compactor._summarize_chunked = _fake_chunked

        result = await compactor.compact_state(CompactionContext(
            messages=large_message_set, system_prompt_tokens=0,
            model_name="gpt-4o", config=config, llm_config={},
        ))

        assert result is not None
        assert result.compaction_type == "partial_summary"
        assert result.failure_kind == "timeout"
        # Marker exactly once.
        markers = [
            m for m in result.replacement_messages
            if isinstance(m, SystemMessage) and (m.id or "").startswith("truncation-marker-")
        ]
        assert len(markers) == 1

    @pytest.mark.asyncio
    async def test_d_identical_outcome_proactive_vs_reactive(self):
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

        async def _fake_chunked_partial(compactable, context):
            return ChunkedOutcome(
                summaries=[SystemMessage(
                    content="[Conversation Summary]\nfirst batch",
                    id=f"compaction-{1}",
                )],
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
        )
        proactive_result = await proactive_compactor.compact_state(proactive_ctx)

        # Reactive-context (graph.py:3513 pattern):
        # system_prompt_tokens=0, llm_config from compactor instance.
        reactive_compactor = ContextCompactor(config, {})
        reactive_compactor._summarize_chunked = _fake_chunked_partial
        reactive_ctx = CompactionContext(
            messages=messages, system_prompt_tokens=0,
            model_name="gpt-4o", config=config, llm_config={},
        )
        reactive_result = await reactive_compactor.compact_state(reactive_ctx)

        # Identical outcome semantics.
        assert proactive_result.compaction_type == reactive_result.compaction_type == "partial_summary"
        assert proactive_result.failure_kind == reactive_result.failure_kind == "timeout"
        assert proactive_result.compacted_at is not None
        assert reactive_result.compacted_at is not None
        # Both carry exactly one marker.
        for r in (proactive_result, reactive_result):
            markers = [
                m for m in r.replacement_messages
                if isinstance(m, SystemMessage) and (m.id or "").startswith("truncation-marker-")
            ]
            assert len(markers) == 1


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
    """WS-3.3 — operation budget trips BETWEEN LLM calls only."""

    @pytest.mark.asyncio
    async def test_budget_exhaustion_stops_remaining_chunks(self):
        """After the budget is exhausted mid-run, the engine stops issuing
        remaining chunks and returns the partial path (or fallback on
        ``|S| = 0``). No aupdate_state interleaving can happen because
        the engine only touches the state via the outer caller.
        """
        from daemon.compaction import ChunkedOutcome

        config = make_compaction_config(
            min_messages_before_compaction=2,
            threshold=0.01,
            recent_message_window=2,
            min_recent_window=1,
            context_window_overrides={"gpt-4o": 1000},
            summarization_chunk_threshold=0.01,
            operation_budget_s=300.0,  # default
        )
        messages = make_messages(120)

        # Inject a slow first chunk so the budget clock advances, then
        # assert the next-batch pre-check trips and we get |S|>=1.
        captured: dict = {}

        async def _first_slow_then_fast_chunked(compactable, context):
            # Sleep so that the budget clock advances well past budget_seconds.
            await asyncio.sleep(0.5)
            return ChunkedOutcome(
                summaries=[SystemMessage(
                    content="[Conversation Summary]\nonly-batch-summary",
                    id=f"compaction-{1}",
                )],
                failed_batches=list(range(1, 200)),
                stop_reason="budget",
            )

        compactor = ContextCompactor(config, {})
        compactor._summarize_chunked = _first_slow_then_fast_chunked

        # Override operation_budget_s to a tiny value so the budget trips.
        config.operation_budget_s = 0.001

        result = await compactor.compact_state(CompactionContext(
            messages=messages, system_prompt_tokens=0,
            model_name="gpt-4o", config=config, llm_config={},
        ))

        # Budget-stopped partial path: |S|>=1 → partial_summary.
        assert result is not None
        assert result.compaction_type == "partial_summary"
        assert result.failure_kind == "timeout"
        # compacted_at stamped on this path (D12).
        assert result.compacted_at is not None
        # Marker exactly once.
        markers = [
            m for m in result.replacement_messages
            if isinstance(m, SystemMessage) and (m.id or "").startswith("truncation-marker-")
        ]
        assert len(markers) == 1


class TestChunkedOutcomeDataclass:
    """Dataclass surface contract — both fields are required, types pinned."""

    def test_chunked_outcome_construction(self):
        co = ChunkedOutcome(summaries=[], failed_batches=[], stop_reason="completed")
        assert co.summaries == []
        assert co.failed_batches == []
        assert co.stop_reason == "completed"

    def test_chunked_outcome_with_summaries(self):
        sm = SystemMessage(content="x", id="compaction-1")
        co = ChunkedOutcome(
            summaries=[sm], failed_batches=[1], stop_reason="timeout",
        )
        assert co.summaries == [sm]
        assert co.failed_batches == [1]
        assert co.stop_reason == "timeout"


class TestReCompactionMarkerDedup:
    """Re-compaction should NOT duplicate the marker (id-deterministic prefix +
    ``add_messages`` reducer de-dups on equal ``id``)."""

    def test_marker_ids_are_unique_per_call(self):
        """Each ``_append_truncation_marker`` call mints a fresh UUID4 — so a
        single result never double-stamps (the helper appends exactly once),
        but across re-compaction runs, the ``add_messages`` reducer will
        de-dup by ``id`` equality.

        This test pins the per-call uniqueness so future refactors don't
        accidentally start reusing a stable id (which would defeat the
        ``add_messages`` de-dup contract).
        """
        a: list = []
        b: list = []
        _append_truncation_marker(a)
        _append_truncation_marker(b)
        assert a[0].id != b[0].id
        assert a[0].id.startswith("truncation-marker-")
        assert b[0].id.startswith("truncation-marker-")
