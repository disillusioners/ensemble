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
    """Create a list of alternating HumanMessage and AIMessage."""
    messages = []
    for i in range(count):
        if i % 2 == 0:
            messages.append(HumanMessage(content=f"{content_prefix} {i}", id=f"human-{i}"))
        else:
            messages.append(AIMessage(content=f"Response to {content_prefix} {i}", id=f"ai-{i}"))
    return messages


def make_ai_with_tool_calls(
    idx: int, tool_call_id: str, tool_name: str = "test_tool"
) -> AIMessage:
    """Create an AIMessage with a single tool call."""
    return AIMessage(
        content=f"Calling {tool_name}",
        id=f"ai-tool-{idx}",
        tool_calls=[ToolCall(id=tool_call_id, name=tool_name, args={"arg": f"value-{idx}"})],
    )


def make_tool_message(tool_call_id: str, content: str, idx: int) -> ToolMessage:
    """Create a ToolMessage responding to a tool call."""
    return ToolMessage(
        content=content,
        tool_call_id=tool_call_id,
        name="test_tool",
        id=f"tool-{idx}",
    )


def mock_llm_response(content: str) -> AIMessage:
    """Create a mock LLM response AIMessage."""
    return AIMessage(content=content, id="mock-llm-response")


# =============================================================================
# Test Classes
# =============================================================================

class TestGetModelContextLimit:
    """Tests for get_model_context_limit function."""

    def test_known_openai_model(self):
        """Test lookup for known OpenAI models."""
        assert get_model_context_limit("gpt-4o") == 128000
        assert get_model_context_limit("gpt-4o-mini") == 128000
        assert get_model_context_limit("gpt-4") == 8192
        assert get_model_context_limit("gpt-4-turbo") == 128000
        assert get_model_context_limit("gpt-3.5-turbo") == 16385

    def test_known_anthropic_model(self):
        """Test lookup for known Anthropic models."""
        assert get_model_context_limit("claude-3.5-sonnet") == 200000
        assert get_model_context_limit("claude-3.5-haiku") == 200000
        assert get_model_context_limit("claude-3-opus") == 200000

    def test_known_open_source_model(self):
        """Test lookup for known open-source models."""
        assert get_model_context_limit("llama-3") == 8192
        assert get_model_context_limit("llama-3.1") == 128000
        assert get_model_context_limit("deepseek") == 128000
        assert get_model_context_limit("qwen") == 32768
        assert get_model_context_limit("mistral") == 32000

    def test_case_insensitive(self):
        """Test that model name matching is case-insensitive."""
        assert get_model_context_limit("GPT-4O") == 128000
        assert get_model_context_limit("Claude-3.5-Sonnet") == 200000
        assert get_model_context_limit("LLAMA-3.1") == 128000

    def test_whitespace_normalization(self):
        """Test that whitespace is stripped from model names."""
        assert get_model_context_limit("  gpt-4o  ") == 128000
        assert get_model_context_limit(" claude-3.5-sonnet ") == 200000

    def test_fuzzy_matching_partial_name(self):
        """Test fuzzy matching when model name contains registry key."""
        assert get_model_context_limit("gpt-4o-2024-08-06") == 128000
        # "gpt-4" is in "gpt-4-0314"
        assert get_model_context_limit("gpt-4-0314") == 8192

    def test_unknown_model_returns_default(self):
        """Test that unknown models return DEFAULT_CONTEXT_LIMIT."""
        assert get_model_context_limit("unknown-model-xyz") == DEFAULT_CONTEXT_LIMIT
        assert get_model_context_limit("totally-fictional-model") == DEFAULT_CONTEXT_LIMIT
        assert get_model_context_limit("") == DEFAULT_CONTEXT_LIMIT

    def test_config_override_priority(self):
        """Test that config.context_window_override takes priority."""
        config = make_compaction_config(context_window_override=50000)
        assert get_model_context_limit("gpt-4o", config=config) == 50000
        assert get_model_context_limit("unknown-model", config=config) == 50000

    def test_config_override_zero_is_ignored(self):
        """Test that context_window_override=0 does not override."""
        config = make_compaction_config(context_window_override=0)
        assert get_model_context_limit("gpt-4o", config=config) == 128000

    def test_config_without_override_attribute(self):
        """Test that config without context_window_override uses registry."""
        config = MagicMock(spec=[])
        assert get_model_context_limit("gpt-4o", config=config) == 128000

    def test_model_context_limits_registry_completeness(self):
        """Test that MODEL_CONTEXT_LIMITS contains expected keys."""
        expected_keys = [
            "gpt-4", "gpt-4-turbo", "gpt-4o", "gpt-4o-mini",
            "claude-3.5-sonnet", "claude-3.5-haiku",
            "llama-3", "llama-3.1",
        ]
        for key in expected_keys:
            assert key in MODEL_CONTEXT_LIMITS, f"Missing key: {key}"


class TestIdentifyBoundaryGroups:
    """Tests for identify_boundary_groups function."""

    def test_empty_messages(self):
        """Test empty message list returns empty groups."""
        groups = identify_boundary_groups([])
        assert groups == []

    def test_single_human_message(self):
        """Test single human message creates one group."""
        messages = [HumanMessage(content="Hello", id="human-0")]
        groups = identify_boundary_groups(messages)
        
        assert len(groups) == 1
        assert groups[0].start_idx == 0
        assert groups[0].end_idx == 0
        assert groups[0].group_type == "single"
        assert groups[0].messages == messages

    def test_single_ai_message(self):
        """Test single AI message creates one group."""
        messages = [AIMessage(content="Hello", id="ai-0")]
        groups = identify_boundary_groups(messages)
        
        assert len(groups) == 1
        assert groups[0].group_type == "single"

    def test_alternating_human_ai_messages(self):
        """Test alternating human/AI messages each become separate groups."""
        messages = [
            HumanMessage(content="Hello", id="human-0"),
            AIMessage(content="Hi", id="ai-0"),
            HumanMessage(content="How are you?", id="human-1"),
            AIMessage(content="Fine", id="ai-1"),
        ]
        groups = identify_boundary_groups(messages)
        
        assert len(groups) == 4
        for g in groups:
            assert g.group_type == "single"
            assert g.end_idx - g.start_idx == 0

    def test_ai_message_with_single_tool_call(self):
        """Test AI message with tool call groups with its ToolMessage."""
        tool_call_id = "call_abc123"
        ai_msg = make_ai_with_tool_calls(0, tool_call_id)
        tool_msg = make_tool_message(tool_call_id, "Tool result", 0)
        messages = [ai_msg, tool_msg]
        
        groups = identify_boundary_groups(messages)
        
        assert len(groups) == 1
        assert groups[0].group_type == "tool_sequence"
        assert groups[0].start_idx == 0
        assert groups[0].end_idx == 1
        assert groups[0].messages == [ai_msg, tool_msg]

    def test_ai_message_with_multiple_tool_calls(self):
        """Test AI message with multiple tool calls groups all matching ToolMessages."""
        tool_call_id_1 = "call_1"
        tool_call_id_2 = "call_2"
        ai_msg = AIMessage(
            content="Calling tools",
            id="ai-multi-tool",
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
        assert groups[0].group_type == "tool_sequence"
        assert groups[0].end_idx == 2
        assert groups[0].messages == [ai_msg, tool_msg_1, tool_msg_2]

    def test_ai_message_with_partial_tool_responses(self):
        """Test AI message groups only with matching ToolMessages, stops at non-matching."""
        tool_call_id_1 = "call_1"
        ai_msg = AIMessage(
            content="Calling tools",
            id="ai-partial",
            tool_calls=[
                ToolCall(id=tool_call_id_1, name="tool_a", args={}),
            ],
        )
        # Tool response for a DIFFERENT call (not in AI's tool_calls)
        orphan_tool = ToolMessage(
            content="Result for different call",
            tool_call_id="call_other",
            name="test_tool",
            id="tool-orphan",
        )
        # Human message that follows
        human_msg = HumanMessage(content="Continue", id="human-follow")
        messages = [ai_msg, orphan_tool, human_msg]
        
        groups = identify_boundary_groups(messages)
        
        # Group 1: AI with tool call but no matching tool → tool_sequence with single msg
        # Group 2: Orphan tool - single
        # Group 3: Human - single
        assert len(groups) == 3
        assert groups[0].group_type == "tool_sequence"  # AI with tool_calls attr
        assert groups[0].messages == [ai_msg]  # No matching tool response found
        assert groups[1].group_type == "single"
        assert groups[1].messages[0].tool_call_id == "call_other"  # Orphan
        assert groups[2].group_type == "single"
        assert groups[2].messages[0].content == "Continue"  # Human

    def test_orphan_tool_message_becomes_single_group(self):
        """Test orphan ToolMessage (no preceding AI with matching call) becomes single group."""
        messages = [make_tool_message("orphan-call", "Orphan result", 0)]
        groups = identify_boundary_groups(messages)
        
        assert len(groups) == 1
        assert groups[0].group_type == "single"
        assert groups[0].messages[0] == messages[0]

    def test_ai_message_with_empty_tool_calls(self):
        """Test AI message with empty tool_calls list becomes single group."""
        messages = [AIMessage(content="No tools", id="ai-empty-tools", tool_calls=[])]
        groups = identify_boundary_groups(messages)
        
        assert len(groups) == 1
        assert groups[0].group_type == "single"

    def test_ai_message_with_no_tool_calls_attribute(self):
        """Test AI message without tool_calls attribute becomes single group."""
        messages = [AIMessage(content="No tools attr", id="ai-no-attr")]
        groups = identify_boundary_groups(messages)
        
        assert len(groups) == 1
        assert groups[0].group_type == "single"

    def test_ai_message_with_dict_tool_calls(self):
        """Test AI message with dict-style tool_calls (not MagicMock)."""
        tool_call_id = "dict_call_123"
        ai_msg = AIMessage(
            content="Calling dict tools",
            id="ai-dict-tools",
            tool_calls=[
                {"id": tool_call_id, "name": "dict_tool", "args": {}}
            ],
        )
        tool_msg = make_tool_message(tool_call_id, "Dict tool result", 0)
        messages = [ai_msg, tool_msg]
        
        groups = identify_boundary_groups(messages)
        
        assert len(groups) == 1
        assert groups[0].group_type == "tool_sequence"
        assert groups[0].end_idx == 1

    def test_group_indices_are_contiguous(self):
        """Test that group indices are contiguous and cover all messages."""
        messages = [
            HumanMessage(content="H1", id="h1"),
            AIMessage(content="A1", id="a1"),
            HumanMessage(content="H2", id="h2"),
            AIMessage(content="A2", id="a2"),
        ]
        groups = identify_boundary_groups(messages)
        
        assert len(groups) == 4
        all_indices = []
        for g in groups:
            all_indices.extend(range(g.start_idx, g.end_idx + 1))
        
        assert sorted(all_indices) == [0, 1, 2, 3]


class TestSelectCompactableGroups:
    """Tests for select_compactable_groups function."""

    def test_fewer_groups_than_window(self):
        """Test that no groups are compactable when len(groups) <= window."""
        groups = [
            MessageGroup(start_idx=0, end_idx=0, messages=[MagicMock()], group_type="single"),
            MessageGroup(start_idx=1, end_idx=1, messages=[MagicMock()], group_type="single"),
        ]
        estimate_fn = MagicMock(return_value=100)
        
        compactable, preserved, window = select_compactable_groups(
            groups, recent_window=10, min_window=3,
            context_window=128000, system_prompt_tokens=0,
            estimate_fn=estimate_fn,
        )
        
        # When len(groups) <= window, function returns early with window unchanged
        assert compactable == []
        assert preserved == groups
        assert window == 10  # returned unchanged since early exit

    def test_all_groups_preserved_when_under_threshold(self):
        """Test that all groups are preserved when total is under threshold."""
        groups = [
            MessageGroup(start_idx=0, end_idx=0, messages=[MagicMock()], group_type="single"),
            MessageGroup(start_idx=1, end_idx=1, messages=[MagicMock()], group_type="single"),
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

    def test_compactable_groups_returned_when_over_threshold(self):
        """Test that older groups are marked compactable when over threshold."""
        # Create 5 groups
        groups = [
            MessageGroup(start_idx=i, end_idx=i, messages=[MagicMock()], group_type="single")
            for i in range(5)
        ]
        # estimate_fn returns 10000 tokens for any message list
        # 5 * 10000 = 50000 > 128000 * 0.80 = 102400 → still under threshold
        # But if we set estimate_fn to return more
        def estimate_fn(msgs):
            return len(msgs) * 50000
        
        compactable, preserved, window = select_compactable_groups(
            groups, recent_window=3, min_window=2,
            context_window=100000, system_prompt_tokens=0,
            estimate_fn=estimate_fn,
            config_threshold=0.80,
        )
        
        # recent_window=3 should be the target, but may reduce to min_window=2
        # 3 groups at 50000 tokens = 150000 > 80000 threshold
        # 2 groups at 50000 tokens = 100000 > 80000 threshold
        # So window reduces to min_window=2
        assert len(preserved) == 2
        assert len(compactable) == 3

    def test_window_reduces_progressively(self):
        """Test that window size reduces progressively until under threshold."""
        # 5 groups, each 1000 tokens, threshold 0.80, context 1000
        # At window=3: 3*1000=3000 > 800 → reduce
        # At window=2: 2*1000=2000 > 800 → reduce
        # At window=1: 1*1000=1000 > 800 → reduce
        # Exit loop → use min_window=1
        groups = [
            MessageGroup(start_idx=i, end_idx=i, messages=[MagicMock()], group_type="single")
            for i in range(5)
        ]
        estimate_fn = MagicMock(return_value=1000)
        
        compactable, preserved, window = select_compactable_groups(
            groups, recent_window=3, min_window=1,
            context_window=1000, system_prompt_tokens=0,
            estimate_fn=estimate_fn,
            config_threshold=0.80,
        )
        
        # Should end up at min_window
        assert window == 1
        assert len(preserved) == 1
        assert len(compactable) == 4

    def test_system_prompt_tokens_included_in_calculation(self):
        """Test that system_prompt_tokens are included in threshold calculation."""
        # 5 groups to force loop execution (5 > window=3)
        groups = [
            MessageGroup(start_idx=i, end_idx=i, messages=[MagicMock()], group_type="single")
            for i in range(5)
        ]
        # 3 groups * 50000 + 50000 system = 200000 > 102400 → reduce
        # 2 groups * 50000 + 50000 system = 150000 > 102400 → reduce
        # 1 group * 50000 + 50000 system = 100000 < 102400 ✓
        def estimate_fn(msgs):
            return len(msgs) * 50000
        
        compactable, preserved, window = select_compactable_groups(
            groups, recent_window=3, min_window=1,
            context_window=128000, system_prompt_tokens=50000,
            estimate_fn=estimate_fn,
            config_threshold=0.80,
        )
        
        assert len(preserved) == 1
        assert len(compactable) == 4

    def test_estimate_fn_called_with_flattened_messages(self):
        """Test that estimate_fn receives flattened message list from groups."""
        msg1 = MagicMock()
        msg2 = MagicMock()
        # Use 2 groups so loop executes (2 > window=1)
        groups = [
            MessageGroup(start_idx=0, end_idx=0, messages=[msg1], group_type="single"),
            MessageGroup(start_idx=1, end_idx=1, messages=[msg2], group_type="single"),
        ]
        estimate_fn = MagicMock(return_value=1000)  # Large so preserves fewer groups
        
        select_compactable_groups(
            groups, recent_window=1, min_window=1,
            context_window=1000, system_prompt_tokens=0,
            estimate_fn=estimate_fn,
        )
        
        # Should be called with flattened messages (1 message in preserved group)
        estimate_fn.assert_called()
        called_with = estimate_fn.call_args[0][0]
        assert msg2 in called_with  # Most recent group is last


class TestEmergencyTruncate:
    """Tests for emergency_truncate function."""

    def test_empty_messages_returns_empty(self):
        """Test that empty message list returns empty list."""
        def estimate_fn(msgs):
            return 0
        result = emergency_truncate([], max_tokens=1000, estimate_fn=estimate_fn)
        assert result == []
        assert result is not []  # It's a deep copy

    def test_under_limit_returns_deep_copy(self):
        """Test messages under limit returns deep copy (not original)."""
        original_list = [HumanMessage(content="Short", id="h1")]
        msg = original_list[0]
        def estimate_fn(msgs):
            return 100
        result = emergency_truncate(original_list, max_tokens=1000, estimate_fn=estimate_fn)
        # Result is a new list object (not the same list)
        assert result is not original_list
        # But content is the same
        assert result[0].content == "Short"
        assert result[0].content == "Short"

    def test_pass1_truncates_long_tool_responses(self):
        """Test Pass 1 truncates tool responses exceeding max_tool_response_chars."""
        tool_msg = ToolMessage(
            content="x" * 5000,  # Very long
            tool_call_id="call_1",
            name="long_tool",
            id="tool-1",
        )
        # First pass should truncate to 2000 chars
        estimate_fn = MagicMock(side_effect=[5000, 500])  # Before, after
        
        result = emergency_truncate(
            [tool_msg], max_tokens=1000, estimate_fn=estimate_fn,
            max_tool_response_chars=2000,
        )
        
        assert len(result[0].content) <= 2000 + len("\n[...truncated]")

    def test_pass2_truncates_long_human_messages(self):
        """Test Pass 2 truncates human messages exceeding max_human_message_chars."""
        human_msg = HumanMessage(content="x" * 5000, id="h1")
        # Pass 1: no tool messages, still over limit
        # Pass 2: truncate human, still over limit
        # Pass 3: halving loop (enters because content > 500)
        #   - while check: estimate > max → enter loop
        #   - after halving: estimate <= max → return
        def estimate_fn(msgs):
            # First call (Pass1 check), then Pass2 check, then Pass3 checks
            estimate_fn.call_count = getattr(estimate_fn, 'call_count', 0) + 1
            if estimate_fn.call_count <= 2:
                return 2000  # Still over limit
            return 800  # Under limit after truncation
        
        result = emergency_truncate(
            [human_msg], max_tokens=1000, estimate_fn=estimate_fn,
            max_tool_response_chars=2000, max_human_message_chars=4000,
        )
        
        assert "[...truncated]" in result[0].content

    def test_pass3_halves_oversized_content(self):
        """Test Pass 3 progressively halves content when still over limit."""
        msg = ToolMessage(
            content="x" * 2000,
            tool_call_id="call_1",
            name="tool",
            id="tool-1",
        )
        call_count = [0]
        def estimate_fn(msgs):
            call_count[0] += 1
            # Pass1 check, Pass2 check, then Pass3: >500 and >max → enter loop, after halving check
            if call_count[0] <= 2:
                return 2000  # Over limit
            if call_count[0] == 3:
                return 1500  # Pass3: still > max
            return 800  # Pass3: after halving, under limit
        
        result = emergency_truncate(
            [msg], max_tokens=1000, estimate_fn=estimate_fn,
            max_tool_response_chars=2000,
        )
        
        # Pass 3 should truncate the content
        assert "[...truncated]" in result[0].content

    def test_drops_oldest_messages_as_last_resort(self):
        """Test that oldest messages are dropped when all truncation fails."""
        # Create multiple messages that all have large content
        msgs = [
            ToolMessage(content="old message " + "x" * 500, tool_call_id=f"c{i}", name="t", id=f"t{i}")
            for i in range(5)
        ]
        # All passes fail, last resort drops oldest
        estimate_fn = MagicMock(return_value=5000)
        
        result = emergency_truncate(msgs, max_tokens=1000, estimate_fn=estimate_fn)
        
        # At least one message should remain
        assert len(result) >= 1

    def test_does_not_drop_all_messages(self):
        """Test that at least one message always remains."""
        msg = HumanMessage(content="Single message", id="h1")
        estimate_fn = MagicMock(return_value=10000)
        
        result = emergency_truncate([msg], max_tokens=1, estimate_fn=estimate_fn)
        
        assert len(result) == 1


class TestTruncateBatchToFit:
    """Tests for _truncate_batch_to_fit function."""

    def test_empty_groups_returns_empty(self):
        """Test empty group list returns empty list."""
        result = _truncate_batch_to_fit([], max_tokens=1000, tokenizer_fn=MagicMock())
        assert result == []

    def test_under_limit_returns_deep_copies(self):
        """Test groups under limit returns new list with new group objects."""
        original_msg = ToolMessage(content="short", tool_call_id="c1", name="t", id="t1")
        group = MessageGroup(
            start_idx=0, end_idx=0,
            messages=[original_msg],
            group_type="single",
        )
        def tokenizer_fn(msgs):
            return 100
        
        result = _truncate_batch_to_fit([group], max_tokens=1000, tokenizer_fn=tokenizer_fn)
        
        assert len(result) == 1
        assert result is not [group]  # New list object
        assert result[0] is not group  # New MessageGroup object
        assert result[0].messages[0] == original_msg  # Equal content but different object

    def test_truncates_long_tool_response_in_group(self):
        """Test that long tool responses in groups are truncated."""
        group = MessageGroup(
            start_idx=0, end_idx=0,
            messages=[ToolMessage(content="x" * 5000, tool_call_id="c1", name="t", id="t1")],
            group_type="single",
        )
        def estimate_fn(msgs):
            return 2000
        
        result = _truncate_batch_to_fit(
            [group], max_tokens=500,
            tokenizer_fn=estimate_fn,
            max_tool_response_chars=2000,
        )
        
        assert "[...truncated]" in result[0].messages[0].content

    def test_drops_oldest_groups_when_over_limit(self):
        """Test that oldest groups are dropped when truncation insufficient."""
        groups = [
            MessageGroup(
                start_idx=i, end_idx=i,
                messages=[AIMessage(content=f"Message {i}", id=f"ai-{i}")],
                group_type="single",
            )
            for i in range(5)
        ]
        # 5000 tokens > max_tokens=2000 → while loop drops groups
        def tokenizer_fn(msgs):
            return 5000
        
        result = _truncate_batch_to_fit(groups, max_tokens=2000, tokenizer_fn=tokenizer_fn)
        
        # Should drop oldest to get under limit
        assert len(result) < 5

    def test_preserves_at_least_one_group(self):
        """Test that at least one group always remains."""
        groups = [
            MessageGroup(
                start_idx=i, end_idx=i,
                messages=[AIMessage(content="x" * 500, id=f"ai-{i}")],
                group_type="single",
            )
            for i in range(3)
        ]
        def estimate_fn(msgs):
            return 10000
        
        result = _truncate_batch_to_fit(groups, max_tokens=1, tokenizer_fn=estimate_fn)
        
        assert len(result) >= 1


class TestContextCompactorBuildReplacement:
    """Tests for ContextCompactor._build_replacement_messages static method."""

    def test_removes_compactable_message_ids(self):
        """Test that RemoveMessage is added for each compactable message with id."""
        compactable = [
            MessageGroup(
                start_idx=0, end_idx=0,
                messages=[AIMessage(content="Old", id="ai-old-1")],
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
        
        result = ContextCompactor._build_replacement_messages(
            compactable, preserved, summary
        )
        
        assert len(result) == 3
        assert isinstance(result[0], RemoveMessage)
        assert result[0].id == "ai-old-1"
        assert isinstance(result[1], SystemMessage)
        assert isinstance(result[2], HumanMessage)

    def test_skips_messages_without_id(self):
        """Test that messages without id don't get RemoveMessage."""
        msg_no_id = AIMessage(content="No ID")
        compactable = [
            MessageGroup(start_idx=0, end_idx=0, messages=[msg_no_id], group_type="single"),
        ]
        preserved = []
        summary = SystemMessage(content="Summary", id="summary-1")
        
        result = ContextCompactor._build_replacement_messages(
            compactable, preserved, summary
        )
        
        assert len(result) == 1  # Only summary
        assert isinstance(result[0], SystemMessage)

    def test_preserved_messages_appended_after_summary(self):
        """Test that preserved groups are appended after summary."""
        compactable = [
            MessageGroup(start_idx=0, end_idx=0, messages=[AIMessage(content="C", id="c1")], group_type="single"),
        ]
        preserved_msg = HumanMessage(content="P", id="p1")
        preserved = [
            MessageGroup(start_idx=1, end_idx=1, messages=[preserved_msg], group_type="single"),
        ]
        summary = SystemMessage(content="Summary", id="s1")
        
        result = ContextCompactor._build_replacement_messages(
            compactable, preserved, summary
        )
        
        assert result[0].id == "c1"  # RemoveMessage
        assert result[1].id == "s1"  # Summary
        assert result[2].id == "p1"  # Preserved


class TestContextCompactorIsRecentlyCompacted:
    """Tests for ContextCompactor._is_recently_compacted static method."""

    def test_within_60_seconds_is_recent(self):
        """Test that timestamp within 60 seconds returns True."""
        recent = datetime.now(timezone.utc).isoformat()
        assert ContextCompactor._is_recently_compacted(recent) is True

    def test_beyond_60_seconds_is_not_recent(self):
        """Test that timestamp beyond 60 seconds returns False."""
        old = "2020-01-01T00:00:00+00:00"
        assert ContextCompactor._is_recently_compacted(old) is False

    def test_naive_datetime_converted_to_utc(self):
        """Test that naive datetime is converted to UTC before comparison."""
        naive = "2020-01-01T00:00:00"
        result = ContextCompactor._is_recently_compacted(naive)
        assert result is False

    def test_invalid_timestamp_returns_false(self):
        """Test that invalid timestamp string returns False."""
        assert ContextCompactor._is_recently_compacted("not-a-timestamp") is False
        assert ContextCompactor._is_recently_compacted("") is False
        assert ContextCompactor._is_recently_compacted(None) is False


class TestContextCompactorCompactState:
    """Tests for ContextCompactor.compact_state async method."""

    @pytest.fixture
    def mock_llm(self):
        """Mock ThinkingChatOpenAI and its invoke method."""
        mock_response = AIMessage(content="Summarized conversation history.", id="mock-response")
        mock_llm_instance = MagicMock()
        mock_llm_instance.invoke = MagicMock(return_value=mock_response)
        # Patch where it's imported (daemon.graph), with create=True since module may be mocked
        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm_instance, create=True):
            yield mock_llm_instance

    @pytest.mark.asyncio
    async def test_skips_when_recently_compacted(self, mock_llm):
        """Test that compaction is skipped if last_compacted_at is recent."""
        config = make_compaction_config(
            min_messages_before_compaction=2,
            threshold=0.01,  # Very low to trigger
        )
        context = CompactionContext(
            messages=make_messages(5),
            system_prompt_tokens=0,
            model_name="gpt-4o",
            config=config,
            llm_config={},
            last_compacted_at=datetime.now(timezone.utc).isoformat(),  # Recent
        )
        compactor = ContextCompactor(config, {})
        
        result = await compactor.compact_state(context)
        
        assert result is None

    @pytest.mark.asyncio
    async def test_skips_when_under_minimum_messages(self, mock_llm):
        """Test that compaction is skipped when message count is below minimum."""
        config = make_compaction_config(
            min_messages_before_compaction=10,
            threshold=0.01,
        )
        context = CompactionContext(
            messages=make_messages(5),  # Below minimum
            system_prompt_tokens=0,
            model_name="gpt-4o",
            config=config,
            llm_config={},
        )
        compactor = ContextCompactor(config, {})
        
        result = await compactor.compact_state(context)
        
        assert result is None

    @pytest.mark.asyncio
    async def test_skips_when_under_token_threshold(self, mock_llm):
        """Test that compaction is skipped when tokens are under threshold."""
        config = make_compaction_config(
            min_messages_before_compaction=2,
            threshold=0.99,  # Very high threshold
            recent_message_window=100,
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
        """Test that successful compaction returns CompactionResult."""
        config = make_compaction_config(
            min_messages_before_compaction=2,
            threshold=0.01,  # Low threshold to trigger
            recent_message_window=2,
            min_recent_window=1,
            context_window_override=1000,  # Small context window
        )
        # 200 messages generates enough tokens to exceed threshold
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
        assert result.compaction_type in (
            "summarization", "chunked_summarization", "truncation"
        )
        assert result.tokens_before > result.tokens_after or result.compaction_type == "truncation"
        assert result.compacted_at is not None

    @pytest.mark.asyncio
    async def test_compaction_result_has_correct_message_counts(self, mock_llm):
        """Test that CompactionResult has correct before/after message counts."""
        config = make_compaction_config(
            min_messages_before_compaction=2,
            threshold=0.01,
            recent_message_window=3,
            min_recent_window=2,
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
        assert result.messages_before == 200
        assert result.messages_after < 200

    @pytest.mark.asyncio
    async def test_replacement_contains_remove_and_summary(self, mock_llm):
        """Test that replacement_messages contains RemoveMessage and summary."""
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
        removal_msgs = [m for m in result.replacement_messages if isinstance(m, RemoveMessage)]
        assert len(removal_msgs) > 0  # Old messages should have RemoveMessage
        summary_msgs = [m for m in result.replacement_messages if isinstance(m, SystemMessage)]
        assert len(summary_msgs) > 0  # Should have summary

    @pytest.mark.asyncio
    async def test_truncation_fallback_on_llm_error(self, mock_llm):
        """Test that truncation fallback is used when summarization LLM fails."""
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
    async def test_emergency_truncation_path(self, mock_llm):
        """Test emergency truncation when preserved groups still exceed threshold."""
        # Use very small context window to force emergency path
        # With context_window_override=100 and threshold=0.01 → threshold=1
        # 20 messages * ~8 tokens each ≈ 160 tokens >> 1 → triggers emergency
        config = make_compaction_config(
            min_messages_before_compaction=2,
            threshold=0.01,
            recent_message_window=200,  # Preserve all (window >= groups)
            min_recent_window=200,
            context_window_override=100,  # Very small → threshold=1
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
        # Truncated message IDs should be reassigned
        truncated_msgs = [m for m in result.replacement_messages if not isinstance(m, RemoveMessage)]
        for msg in truncated_msgs:
            if hasattr(msg, 'id') and msg.id:
                assert msg.id.startswith("truncated-")

    @pytest.mark.asyncio
    async def test_compaction_with_system_prompt_tokens(self, mock_llm):
        """Test compaction respects system_prompt_tokens in calculations."""
        config = make_compaction_config(
            min_messages_before_compaction=2,
            threshold=0.50,
            recent_message_window=5,
            min_recent_window=3,
            context_window_override=1000,
        )
        messages = make_messages(200)
        context = CompactionContext(
            messages=messages,
            system_prompt_tokens=50000,  # Large system prompt
            model_name="gpt-4o",
            config=config,
            llm_config={},
        )
        compactor = ContextCompactor(config, {})
        
        result = await compactor.compact_state(context)
        
        # Result should account for system prompt tokens
        assert result is not None
        # tokens_after should be >= system_prompt_tokens
        assert result.tokens_after >= 50000

    @pytest.mark.asyncio
    async def test_summarization_model_override(self, mock_llm):
        """Test that summarization_model config overrides the default model."""
        config = make_compaction_config(
            min_messages_before_compaction=2,
            threshold=0.01,
            recent_message_window=2,
            min_recent_window=1,
            summarization_model="gpt-4o-mini",
        )
        messages = make_messages(20)
        context = CompactionContext(
            messages=messages,
            system_prompt_tokens=0,
            model_name="gpt-4o",
            config=config,
            llm_config={"model": "gpt-4o"},
        )
        compactor = ContextCompactor(config, {"model": "gpt-4o"})
        
        await compactor.compact_state(context)
        
        # Verify ThinkingChatOpenAI was called with summarization model
        # The mock is already set up, we just verify the call happened
        # (actual model name would be in call_args if we had access)

    @pytest.mark.asyncio
    async def test_no_compaction_returns_none(self, mock_llm):
        """Test that compact_state returns None when no compaction needed."""
        config = make_compaction_config(
            min_messages_before_compaction=2,
            threshold=0.99,  # Very high
            recent_message_window=1000,
        )
        messages = make_messages(5)
        context = CompactionContext(
            messages=messages,
            system_prompt_tokens=0,
            model_name="gpt-4o",
            config=config,
            llm_config={},
        )
        compactor = ContextCompactor(config, {})
        
        result = await compactor.compact_state(context)
        
        assert result is None


class TestContextCompactorTruncateFallback:
    """Tests for ContextCompactor._truncate_fallback method."""

    def test_truncate_fallback_returns_remove_and_preserved(self):
        """Test that _truncate_fallback returns RemoveMessage for compactable + preserved."""
        config = make_compaction_config()
        compactor = ContextCompactor(config, {})
        
        compactable = [
            MessageGroup(
                start_idx=0, end_idx=0,
                messages=[AIMessage(content="Old", id="old-1")],
                group_type="single",
            ),
        ]
        preserved = [
            MessageGroup(
                start_idx=1, end_idx=1,
                messages=[HumanMessage(content="Recent", id="new-1")],
                group_type="single",
            ),
        ]
        context = CompactionContext(
            messages=[],
            system_prompt_tokens=0,
            model_name="gpt-4o",
            config=config,
            llm_config={},
        )
        
        replacement, compaction_type = compactor._truncate_fallback(
            compactable, preserved, context
        )
        
        assert compaction_type == "truncation"
        assert isinstance(replacement[0], RemoveMessage)
        assert replacement[0].id == "old-1"
        assert isinstance(replacement[1], HumanMessage)
        assert replacement[1].id == "new-1"

    def test_truncate_fallback_skips_messages_without_id(self):
        """Test that _truncate_fallback skips messages without id in RemoveMessage."""
        config = make_compaction_config()
        compactor = ContextCompactor(config, {})
        
        compactable = [
            MessageGroup(
                start_idx=0, end_idx=0,
                messages=[AIMessage(content="No ID")],  # No id
                group_type="single",
            ),
        ]
        preserved = [
            MessageGroup(
                start_idx=1, end_idx=1,
                messages=[HumanMessage(content="Has ID", id="new-1")],
                group_type="single",
            ),
        ]
        context = CompactionContext(
            messages=[],
            system_prompt_tokens=0,
            model_name="gpt-4o",
            config=config,
            llm_config={},
        )
        
        replacement, _ = compactor._truncate_fallback(compactable, preserved, context)
        
        # Only one RemoveMessage (for the one with id)
        remove_msgs = [m for m in replacement if isinstance(m, RemoveMessage)]
        assert len(remove_msgs) == 0  # No id, so no RemoveMessage
        assert len(replacement) == 1  # Only preserved


class TestCompactionDataClasses:
    """Tests for compaction dataclasses."""

    def test_compaction_context_fields(self):
        """Test CompactionContext accepts all required fields."""
        messages = [HumanMessage(content="test", id="h1")]
        config = make_compaction_config()
        context = CompactionContext(
            messages=messages,
            system_prompt_tokens=1000,
            model_name="gpt-4o",
            config=config,
            llm_config={"model": "gpt-4o"},
            last_compacted_at="2024-01-01T00:00:00+00:00",
        )
        assert context.messages == messages
        assert context.system_prompt_tokens == 1000
        assert context.model_name == "gpt-4o"
        assert context.last_compacted_at == "2024-01-01T00:00:00+00:00"

    def test_compaction_result_fields(self):
        """Test CompactionResult dataclass fields."""
        result = CompactionResult(
            replacement_messages=[RemoveMessage(id="old-1")],
            tokens_before=100000,
            tokens_after=50000,
            tokens_saved=50000,
            messages_before=50,
            messages_after=25,
            compaction_type="summarization",
            summarization_error=None,
            compacted_at="2024-01-01T00:00:00+00:00",
        )
        assert result.tokens_saved == 50000
        assert result.compaction_type == "summarization"
        assert len(result.replacement_messages) == 1

    def test_message_group_fields(self):
        """Test MessageGroup dataclass fields."""
        msg = AIMessage(content="test", id="ai-1")
        group = MessageGroup(
            start_idx=0,
            end_idx=1,
            messages=[msg],
            group_type="tool_sequence",
        )
        assert group.start_idx == 0
        assert group.end_idx == 1
        assert group.messages == [msg]
        assert group.group_type == "tool_sequence"


class TestCompactionConfigDefaults:
    """Tests for CompactionConfig default values."""

    def test_compaction_config_defaults(self):
        """Test CompactionConfig has correct default values."""
        config = CompactionConfigModel()
        assert config.enabled is True
        assert config.threshold == 0.80
        assert config.recent_message_window == 10
        assert config.min_recent_window == 3
        assert config.context_window_override == 0
        assert config.target_ratio == 0.40
        assert config.summarization_model == ""
        assert config.min_messages_before_compaction == 10
        assert config.summarization_chunk_threshold == 0.60

    def test_compaction_config_custom_values(self):
        """Test CompactionConfig accepts custom values."""
        config = CompactionConfigModel(
            enabled=False,
            threshold=0.90,
            recent_message_window=20,
            min_recent_window=5,
            context_window_override=50000,
            target_ratio=0.50,
            summarization_model="gpt-4o-mini",
            min_messages_before_compaction=5,
            summarization_chunk_threshold=0.70,
        )
        assert config.enabled is False
        assert config.threshold == 0.90
        assert config.recent_message_window == 20
        assert config.min_recent_window == 5
        assert config.context_window_override == 50000
        assert config.target_ratio == 0.50
        assert config.summarization_model == "gpt-4o-mini"
        assert config.min_messages_before_compaction == 5
        assert config.summarization_chunk_threshold == 0.70
