"""Comprehensive unit tests for daemon/compaction.py."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

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
    CompactionConfig,
    CompactionContext,
    CompactionResult,
    ContextCompactor,
    MessageGroup,
    emergency_truncate,
    get_model_context_limit,
    identify_boundary_groups,
    select_compactable_groups,
    _truncate_batch_to_fit,
)
from daemon.config import CompactionConfig as CompactionConfigModel
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
        "context_window_override": 0,
        "target_ratio": 0.40,
        "summarization_model": "",
        "min_messages_before_compaction": 10,
        "summarization_chunk_threshold": 0.60,
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
        config = make_compaction_config(context_window_override=50000)
        assert get_model_context_limit("any-model", config=config) == 50000
        assert get_model_context_limit("totally-unknown-model") == DEFAULT_CONTEXT_LIMIT


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
            context_window_override=1000,  # Small context to reliably trigger
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
        assert result.compaction_type in ("summarization", "chunked_summarization", "truncation")
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
            context_window_override=1000,
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
            context_window_override=100,
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
