"""Comprehensive unit tests for daemon/compaction.py multimodal content handling.

These tests verify that compaction functions correctly handle multimodal content
(str + image_url blocks) without producing garbage output like raw list representations.
"""

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
    CompactionConfig,
    CompactionContext,
    CompactionResult,
    ContextCompactor,
    MessageGroup,
    _extract_text_from_content,
    _truncate_batch_to_fit,
    emergency_truncate,
    identify_boundary_groups,
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
        "context_window_overrides": {},
        "context_window_default": 0,
        "target_ratio": 0.40,
        "summarization_model": "",
        "min_messages_before_compaction": 10,
        "summarization_chunk_threshold": 0.60,
    }
    defaults.update(overrides)
    return CompactionConfigModel(**defaults)


def make_multimodal_content(text: str, with_image: bool = True) -> list[dict]:
    """Create multimodal content list with text and optional image_url block."""
    content = [{"type": "text", "text": text}]
    if with_image:
        content.append({
            "type": "image_url",
            "image_url": {"url": "http://example.com/image.png"}
        })
    return content


def make_messages(count: int, content_prefix: str = "Message") -> list[BaseMessage]:
    """Create alternating HumanMessage and AIMessage."""
    messages = []
    for i in range(count):
        if i % 2 == 0:
            messages.append(HumanMessage(content=f"{content_prefix} {i}", id=f"human-{i}"))
        else:
            messages.append(AIMessage(content=f"Response to {content_prefix} {i}", id=f"ai-{i}"))
    return messages


# =============================================================================
# Test Classes
# =============================================================================

class TestEmergencyTruncateMultimodal:
    """Tests for emergency_truncate with multimodal content (6 cases)."""

    def test_human_message_multimodal_content_truncated_cleanly(self):
        """Test that HumanMessage with multimodal content is truncated to clean text only."""
        multimodal_content = make_multimodal_content("User said hello")
        human_msg = HumanMessage(content=multimodal_content, id="human-1")
        
        estimate_fn = MagicMock(side_effect=[5000, 500])  # Over, then under
        result = emergency_truncate(
            [human_msg],
            max_tokens=1000,
            estimate_fn=estimate_fn,
            max_human_message_chars=4000,
        )
        
        # Result should have clean string content, not garbage like "[{'type': 'text'...
        assert isinstance(result[0].content, str)
        assert "[{'type':" not in result[0].content
        assert "image_url" not in result[0].content
        # Text should be preserved
        assert "hello" in result[0].content

    def test_tool_message_multimodal_content_truncated_cleanly(self):
        """Test that ToolMessage with multimodal content is handled correctly."""
        multimodal_content = make_multimodal_content("Tool result content")
        tool_msg = ToolMessage(
            content=multimodal_content,
            tool_call_id="call_1",
            name="test_tool",
            id="tool-1",
        )
        
        estimate_fn = MagicMock(side_effect=[5000, 500])
        result = emergency_truncate(
            [tool_msg],
            max_tokens=1000,
            estimate_fn=estimate_fn,
            max_tool_response_chars=2000,
        )
        
        # Content should be clean string
        assert isinstance(result[0].content, str)
        assert "[{'type':" not in result[0].content
        assert "image_url" not in result[0].content
        assert "Tool result content" in result[0].content

    def test_ai_message_multimodal_content_truncated_cleanly(self):
        """Test that AIMessage with multimodal content is handled correctly."""
        multimodal_content = make_multimodal_content("AI response text")
        ai_msg = AIMessage(content=multimodal_content, id="ai-1")
        
        # Estimate always returns high to trigger Pass 3
        estimate_fn = MagicMock(return_value=2000)
        result = emergency_truncate(
            [ai_msg],
            max_tokens=1000,
            estimate_fn=estimate_fn,
        )
        
        # Content should be clean string
        assert isinstance(result[0].content, str)
        assert "[{'type':" not in result[0].content
        assert "image_url" not in result[0].content

    def test_human_message_with_only_image_blocks_returns_empty(self):
        """Test that HumanMessage with only image_url blocks returns empty content."""
        content = [{"type": "image_url", "image_url": {"url": "http://example.com/image.png"}}]
        human_msg = HumanMessage(content=content, id="human-1")
        
        estimate_fn = MagicMock(return_value=100)
        result = emergency_truncate(
            [human_msg],
            max_tokens=1000,
            estimate_fn=estimate_fn,
        )
        
        # Should return clean empty content
        assert result[0].content == ""

    def test_mixed_messages_with_multimodal_content(self):
        """Test truncation with mix of string and multimodal content."""
        messages = [
            HumanMessage(content="Simple string", id="h1"),
            HumanMessage(content=make_multimodal_content("Multimodal text"), id="h2"),
            ToolMessage(content="Tool result", tool_call_id="c1", name="t", id="t1"),
            HumanMessage(content=make_multimodal_content("Another multimodal"), id="h3"),
        ]
        
        estimate_fn = MagicMock(return_value=5000)  # Always over
        result = emergency_truncate(
            messages,
            max_tokens=1000,
            estimate_fn=estimate_fn,
        )
        
        # All contents should be strings
        for msg in result:
            assert isinstance(msg.content, str)
            assert "[{'type':" not in msg.content
            assert "image_url" not in msg.content

    def test_truncated_content_does_not_leak_image_urls(self):
        """Test that image URLs are never leaked into truncated content."""
        long_text = "A" * 5000
        multimodal_content = make_multimodal_content(long_text)
        human_msg = HumanMessage(content=multimodal_content, id="human-1")
        
        estimate_fn = MagicMock(side_effect=[5000, 500])
        result = emergency_truncate(
            [human_msg],
            max_tokens=1000,
            estimate_fn=estimate_fn,
            max_human_message_chars=4000,
        )
        
        # Image URL should not appear in truncated content
        assert "http://" not in result[0].content
        assert "image.png" not in result[0].content
        assert "image_url" not in result[0].content


class TestTruncateBatchToFitMultimodal:
    """Tests for _truncate_batch_to_fit with multimodal content (5 cases)."""

    def test_batch_with_multimodal_messages_produces_clean_output(self):
        """Test that batch truncation produces clean text without garbage."""
        multimodal_content = make_multimodal_content("Batch message content")
        msg = ToolMessage(
            content=multimodal_content,
            tool_call_id="call_1",
            name="test_tool",
            id="tool-1",
        )
        group = MessageGroup(start_idx=0, end_idx=0, messages=[msg], group_type="single")
        
        def tokenizer_fn(msgs):
            return 100  # Under limit
        
        result = _truncate_batch_to_fit([group], max_tokens=1000, tokenizer_fn=tokenizer_fn)
        
        # Verify content is clean string
        for g in result:
            for m in g.messages:
                assert isinstance(m.content, str)
                assert "[{'type':" not in m.content
                assert "image_url" not in m.content

    def test_large_image_url_blocks_dont_affect_truncation(self):
        """Test that large image_url blocks are properly skipped in truncation."""
        # Create content with a very long image URL (simulating large data URI)
        large_image_url = "data:image/png;base64," + "A" * 10000
        multimodal_content = [
            {"type": "text", "text": "Important text"},
            {"type": "image_url", "image_url": {"url": large_image_url}},
        ]
        tool_msg = ToolMessage(
            content=multimodal_content,
            tool_call_id="call_1",
            name="test_tool",
            id="tool-1",
        )
        group = MessageGroup(start_idx=0, end_idx=0, messages=[tool_msg], group_type="single")
        
        def tokenizer_fn(msgs):
            return 5000  # Over limit
        
        result = _truncate_batch_to_fit(
            [group],
            max_tokens=1000,
            tokenizer_fn=tokenizer_fn,
            max_tool_response_chars=2000,
        )
        
        # Only text content should remain
        for g in result:
            for m in g.messages:
                assert isinstance(m.content, str)
                # Large image URL should not appear
                assert "data:image" not in m.content
                assert "base64" not in m.content
                # Text should be preserved
                assert "Important text" in m.content

    def test_multiple_groups_with_mixed_multimodal_content(self):
        """Test batch truncation with multiple groups containing mixed content."""
        groups = [
            MessageGroup(
                start_idx=0,
                end_idx=0,
                messages=[HumanMessage(content=make_multimodal_content("Human 1"), id="h1")],
                group_type="single",
            ),
            MessageGroup(
                start_idx=1,
                end_idx=1,
                messages=[AIMessage(content=make_multimodal_content("AI 1"), id="a1")],
                group_type="single",
            ),
            MessageGroup(
                start_idx=2,
                end_idx=2,
                messages=[ToolMessage(
                    content=make_multimodal_content("Tool 1"),
                    tool_call_id="c1",
                    name="t",
                    id="t1",
                )],
                group_type="single",
            ),
        ]
        
        def tokenizer_fn(msgs):
            return 5000
        
        result = _truncate_batch_to_fit(groups, max_tokens=1000, tokenizer_fn=tokenizer_fn)
        
        # All contents should be clean strings
        for g in result:
            for m in g.messages:
                assert isinstance(m.content, str)
                assert "[{'type':" not in m.content
                assert "image_url" not in m.content

    def test_truncation_preserves_message_structure(self):
        """Test that message types and structure are preserved after truncation."""
        groups = [
            MessageGroup(
                start_idx=0,
                end_idx=0,
                messages=[HumanMessage(content=make_multimodal_content("Keep me"), id="h1")],
                group_type="single",
            ),
        ]
        
        def tokenizer_fn(msgs):
            return 100
        
        result = _truncate_batch_to_fit(groups, max_tokens=1000, tokenizer_fn=tokenizer_fn)
        
        assert len(result) == 1
        assert isinstance(result[0].messages[0], HumanMessage)
        assert result[0].messages[0].id == "h1"

    def test_dropped_groups_due_to_size_still_produce_clean_output(self):
        """Test that remaining groups after dropping have clean content."""
        groups = [
            MessageGroup(
                start_idx=i,
                end_idx=i,
                messages=[HumanMessage(
                    content=make_multimodal_content(f"Message {i}"),
                    id=f"h{i}",
                )],
                group_type="single",
            )
            for i in range(5)
        ]
        
        def tokenizer_fn(msgs):
            return 5000  # All over limit
        
        result = _truncate_batch_to_fit(groups, max_tokens=1000, tokenizer_fn=tokenizer_fn)
        
        # Some groups may be dropped, but remaining should be clean
        for g in result:
            for m in g.messages:
                assert isinstance(m.content, str)
                assert "[{'type':" not in m.content


class TestContextCompactorMultimodal:
    """Tests for ContextCompactor summarization with multimodal content (5 cases)."""

    @pytest.fixture
    def mock_llm(self):
        """Mock ThinkingChatOpenAI and its invoke method."""
        mock_response = AIMessage(content="Summarized conversation with images.", id="mock-response")
        mock_llm_instance = MagicMock()
        mock_llm_instance.invoke = MagicMock(return_value=mock_response)
        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm_instance, create=True):
            yield mock_llm_instance

    @pytest.mark.asyncio
    async def test_summarize_batch_with_multimodal_messages(self, mock_llm):
        """Test that _summarize_single_batch properly extracts text from multimodal messages."""
        groups = [
            MessageGroup(
                start_idx=0,
                end_idx=0,
                messages=[HumanMessage(
                    content=make_multimodal_content("User asked about the image"),
                    id="h1",
                )],
                group_type="single",
            ),
            MessageGroup(
                start_idx=1,
                end_idx=1,
                messages=[AIMessage(
                    content=make_multimodal_content("I see the image you sent"),
                    id="a1",
                )],
                group_type="single",
            ),
        ]
        
        config = make_compaction_config(min_messages_before_compaction=2)
        context = CompactionContext(
            messages=[],
            system_prompt_tokens=0,
            model_name="gpt-4o",
            config=config,
            llm_config={},
        )
        
        compactor = ContextCompactor(config, {})
        summary = await compactor._summarize_single_batch(groups, context)
        
        # NEW str contract (a80767b9): _summarize_single_batch returns
        # plain text; the caller wraps into a message downstream. Verify
        # the contract here without re-introducing the SystemMessage
        # assertion that the refactor deliberately removed.
        assert isinstance(summary, str)
        # Summary content should not contain garbage from multimodal blocks
        assert "[{'type':" not in summary
        assert "image_url" not in summary

    @pytest.mark.asyncio
    async def test_summarization_prompt_contains_clean_text(self, mock_llm):
        """Test that the summarization prompt contains clean text (no garbage)."""
        groups = [
            MessageGroup(
                start_idx=0,
                end_idx=0,
                messages=[HumanMessage(
                    content=make_multimodal_content("Hello with image"),
                    id="h1",
                )],
                group_type="single",
            ),
        ]
        
        config = make_compaction_config()
        context = CompactionContext(
            messages=[],
            system_prompt_tokens=0,
            model_name="gpt-4o",
            config=config,
            llm_config={},
        )
        
        compactor = ContextCompactor(config, {})
        
        # Capture the prompt sent to the LLM
        captured_prompts = []
        original_invoke = mock_llm.invoke
        def capture_invoke(messages):
            captured_prompts.append(messages)
            return original_invoke(messages)
        mock_llm.invoke = MagicMock(side_effect=capture_invoke)
        
        await compactor._summarize_single_batch(groups, context)
        
        # Check the prompt content
        assert len(captured_prompts) == 1
        prompt_messages = captured_prompts[0]
        
        # Find the HumanMessage (contains the extracted conversation text)
        human_msg_found = False
        for msg in prompt_messages:
            if isinstance(msg, HumanMessage):
                human_msg_found = True
                # Prompt should contain clean text (no garbage)
                assert "[{'type':" not in msg.content
                assert "image_url" not in msg.content
                # But should contain the extracted text
                assert "Hello with image" in msg.content
                break
        
        assert human_msg_found, "HumanMessage not found in prompt"

    @pytest.mark.asyncio
    async def test_call_summarization_llm_handles_multimodal_response(self, mock_llm):
        """Test that _call_summarization_llm handles multimodal AIMessage content."""
        # Create a mock response with multimodal content
        mock_response = AIMessage(
            content=make_multimodal_content("Summary with image reference"),
            id="mock-response",
        )
        mock_llm.invoke = MagicMock(return_value=mock_response)
        
        config = make_compaction_config()
        context = CompactionContext(
            messages=[],
            system_prompt_tokens=0,
            model_name="gpt-4o",
            config=config,
            llm_config={},
        )
        
        compactor = ContextCompactor(config, {})
        result = await compactor._call_summarization_llm("Test prompt", context)
        
        # Result should be clean string, not multimodal list
        assert isinstance(result, str)
        assert "[{'type':" not in result
        assert "image_url" not in result
        # But should contain the text
        assert "Summary with image reference" in result

    @pytest.mark.asyncio
    async def test_compaction_preserves_no_garbage_in_output(self, mock_llm):
        """Test that compaction output contains no garbage from multimodal content."""
        # Create messages with multimodal content
        messages = [
            HumanMessage(
                content=make_multimodal_content("First user message with image"),
                id="h1",
            ),
            AIMessage(
                content=make_multimodal_content("First AI response with image"),
                id="a1",
            ),
            HumanMessage(
                content=make_multimodal_content("Second user message with image"),
                id="h2",
            ),
            AIMessage(
                content=make_multimodal_content("Second AI response with image"),
                id="a2",
            ),
        ]
        
        config = make_compaction_config(
            min_messages_before_compaction=2,
            threshold=0.01,
            recent_message_window=2,
            min_recent_window=1,
            context_window_overrides={"gpt-4o": 1000},
        )
        
        context = CompactionContext(
            messages=messages,
            system_prompt_tokens=0,
            model_name="gpt-4o",
            config=config,
            llm_config={},
        )
        
        compactor = ContextCompactor(config, {})
        result = await compactor.compact_state(context)
        
        if result:
            # Check all replacement messages for garbage
            for msg in result.replacement_messages:
                if hasattr(msg, 'content'):
                    if isinstance(msg.content, str):
                        assert "[{'type':" not in msg.content
                        assert "image_url" not in msg.content
                        assert "image.png" not in msg.content
                    elif isinstance(msg.content, list):
                        # If list, verify it's only text blocks (shouldn't happen in output)
                        for block in msg.content:
                            if isinstance(block, dict):
                                assert block.get("type") == "text"

    @pytest.mark.asyncio
    async def test_chunked_summarization_with_multimodal_content(self, mock_llm):
        """Test chunked summarization handles multimodal content correctly."""
        # Create enough messages to trigger chunking
        messages = []
        for i in range(50):
            messages.append(HumanMessage(
                content=make_multimodal_content(f"User message {i}"),
                id=f"h{i}",
            ))
            messages.append(AIMessage(
                content=make_multimodal_content(f"AI response {i}"),
                id=f"a{i}",
            ))
        
        config = make_compaction_config(
            min_messages_before_compaction=2,
            threshold=0.01,
            recent_message_window=5,
            min_recent_window=2,
            context_window_overrides={"gpt-4o": 500},
        )
        
        context = CompactionContext(
            messages=messages,
            system_prompt_tokens=0,
            model_name="gpt-4o",
            config=config,
            llm_config={},
        )
        
        compactor = ContextCompactor(config, {})
        result = await compactor.compact_state(context)
        
        if result:
            # Verify no garbage in any message content
            for msg in result.replacement_messages:
                if hasattr(msg, 'content') and isinstance(msg.content, str):
                    assert "[{'type':" not in msg.content
                    assert "image_url" not in msg.content


class TestGarbageOutputPrevention:
    """Tests for garbage output prevention in compaction (6 cases)."""

    def test_str_multimodal_produces_garbage_but_compaction_does_not(self):
        """Demonstrate that str() on multimodal produces garbage, but compaction handles it."""
        multimodal_content = [
            {"type": "text", "text": "Hello"},
            {"type": "image_url", "image_url": {"url": "http://example.com/image.png"}},
        ]
        
        # str() on multimodal produces garbage
        garbage = str(multimodal_content)
        assert "[{'type':" in garbage  # This is garbage
        assert "image_url" in garbage
        
        # But _extract_text_from_content produces clean output
        clean = _extract_text_from_content(multimodal_content)
        assert "[{'type':" not in clean
        assert "image_url" not in clean
        assert clean == "Hello"

    def test_emergency_truncate_never_produces_list_representation(self):
        """Test that emergency_truncate never produces list string representation."""
        test_cases = [
            make_multimodal_content("Text 1"),
            make_multimodal_content("Text 2"),
            [{"type": "text", "text": "Only text"}],
            [{"type": "image_url", "image_url": {"url": "http://x.com/a.png"}}],
            [{"type": "text", "text": "A"}, {"type": "image_url", "image_url": {"url": "http://x.com/b.png"}}],
        ]
        
        for content in test_cases:
            msg = HumanMessage(content=content, id="h1")
            estimate_fn = MagicMock(return_value=5000)
            result = emergency_truncate(
                [msg],
                max_tokens=1000,
                estimate_fn=estimate_fn,
            )
            
            # Content should never be a list representation
            assert not result[0].content.startswith("[{"), (
                f"Got garbage: {result[0].content[:100]}"
            )
            # Should be a proper string
            assert isinstance(result[0].content, str)

    def test_image_urls_do_not_leak_into_summaries(self):
        """Test that image URLs don't leak into summarization prompts."""
        content_with_image = [
            {"type": "text", "text": "User uploaded a screenshot"},
            {"type": "image_url", "image_url": {"url": "http://secrets.com/token.png"}},
        ]
        
        # Extract text for summarization
        extracted = _extract_text_from_content(content_with_image)
        
        # URL should not appear
        assert "secrets.com" not in extracted
        assert "http://" not in extracted
        assert "token.png" not in extracted
        # Only text should remain
        assert extracted == "User uploaded a screenshot"

    def test_large_base64_images_are_handled(self):
        """Test that large base64 image data is properly ignored."""
        large_base64 = "data:image/png;base64," + "A" * 50000
        content = [
            {"type": "text", "text": "Important text content"},
            {"type": "image_url", "image_url": {"url": large_base64}},
        ]
        
        extracted = _extract_text_from_content(content)
        
        # Large base64 should not appear
        assert "data:image" not in extracted
        assert "base64" not in extracted
        # Only text should remain
        assert extracted == "Important text content"

    def test_no_multimodal_garbage_in_any_compaction_path(self):
        """Test all compaction paths produce clean output."""
        multimodal_content = make_multimodal_content("Test content with image")
        
        # Test emergency_truncate
        msg = HumanMessage(content=multimodal_content, id="h1")
        estimate_fn = MagicMock(return_value=5000)
        truncated = emergency_truncate([msg], max_tokens=1000, estimate_fn=estimate_fn)
        assert isinstance(truncated[0].content, str)
        assert "[{'type':" not in truncated[0].content
        
        # Test _truncate_batch_to_fit
        group = MessageGroup(start_idx=0, end_idx=0, messages=[msg], group_type="single")
        batch_result = _truncate_batch_to_fit(
            [group],
            max_tokens=1000,
            tokenizer_fn=MagicMock(return_value=100),
        )
        assert isinstance(batch_result[0].messages[0].content, str)
        assert "[{'type':" not in batch_result[0].messages[0].content

    def test_unicode_and_special_chars_in_multimodal_preserved(self):
        """Test that unicode and special characters in text are preserved."""
        content = [
            {"type": "text", "text": "Hello 🌍 🎉 & <tag> \"quotes\""},
            {"type": "image_url", "image_url": {"url": "http://example.com/image.png"}},
        ]
        
        extracted = _extract_text_from_content(content)
        
        # Unicode and special chars should be preserved
        assert "Hello 🌍 🎉" in extracted
        # Image URL should not appear
        assert "example.com" not in extracted
        assert "image.png" not in extracted


class TestMultimodalEdgeCases:
    """Edge case tests for multimodal content handling (5 cases)."""

    def test_empty_multimodal_content_list(self):
        """Test handling of empty list content."""
        content = []
        result = _extract_text_from_content(content)
        assert result == ""

    def test_list_with_only_image_blocks(self):
        """Test handling of list with only image_url blocks."""
        content = [
            {"type": "image_url", "image_url": {"url": "http://a.com/1.png"}},
            {"type": "image_url", "image_url": {"url": "http://b.com/2.png"}},
        ]
        result = _extract_text_from_content(content)
        assert result == ""

    def test_malformed_dict_blocks_handled_gracefully(self):
        """Test that malformed dict blocks don't crash extraction."""
        content = [
            None,
            {"type": "text"},  # Missing 'text' key
            {"text": "has text but no type"},  # Missing 'type' key
            {"type": "text", "text": "Valid text"},
        ]
        result = _extract_text_from_content(content)
        assert result == "Valid text"

    def test_non_dict_items_in_list_ignored(self):
        """Test that non-dict items in content list are ignored."""
        content = [
            "string item",
            123,
            None,
            {"type": "text", "text": "valid"},
        ]
        result = _extract_text_from_content(content)
        assert result == "valid"

    def test_very_large_text_block_extracted_correctly(self):
        """Test extraction of very large text blocks."""
        large_text = "A" * 100000
        content = [
            {"type": "text", "text": large_text},
            {"type": "image_url", "image_url": {"url": "http://x.com/small.png"}},
        ]
        result = _extract_text_from_content(content)
        assert len(result) == 100000
        assert result.startswith("A" * 1000)
        assert "http://" not in result
        assert "x.com" not in result


# =============================================================================
# Integration Tests
# =============================================================================

class TestMultimodalIntegration:
    """Integration tests combining multiple compaction functions (3 cases)."""

    def test_full_compaction_cycle_with_multimodal_content(self):
        """Test a full compaction cycle with multimodal messages."""
        # Build conversation with multimodal content
        messages = []
        for i in range(10):
            messages.append(HumanMessage(
                content=make_multimodal_content(f"User turn {i}"),
                id=f"h{i}",
            ))
            messages.append(AIMessage(
                content=make_multimodal_content(f"AI turn {i}"),
                id=f"a{i}",
            ))
        
        # Identify groups
        groups = identify_boundary_groups(messages)
        
        # Extract text from all messages
        for group in groups:
            for msg in group.messages:
                content = _extract_text_from_content(msg.content)
                assert isinstance(content, str)
                assert "[{'type':" not in content
                assert "image_url" not in content

    def test_multimodal_content_round_trip(self):
        """Test that multimodal content survives full round trip through compaction."""
        original_content = [
            {"type": "text", "text": "Original message text"},
            {"type": "image_url", "image_url": {"url": "http://original.com/image.png"}},
        ]
        
        # Extract text
        extracted = _extract_text_from_content(original_content)
        
        # Create new message with extracted text
        new_msg = HumanMessage(content=extracted, id="new-msg")
        
        # Verify extracted content
        assert new_msg.content == "Original message text"
        assert "http://" not in new_msg.content
        assert "image.png" not in new_msg.content

    @pytest.mark.asyncio
    async def test_compactor_end_to_end_with_multimodal(self, mock_llm=None):
        """Test ContextCompactor end-to-end with multimodal messages."""
        if mock_llm is None:
            mock_response = AIMessage(content="Final summary.", id="mock-response")
            mock_llm_instance = MagicMock()
            mock_llm_instance.invoke = MagicMock(return_value=mock_response)
            mock_llm = mock_llm_instance
            patcher = patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm_instance, create=True)
            patcher.start()

        try:
            # Build conversation
            messages = [
                HumanMessage(
                    content=make_multimodal_content("User message 1"),
                    id="h1",
                ),
                AIMessage(
                    content=make_multimodal_content("AI response 1"),
                    id="a1",
                ),
                HumanMessage(
                    content=make_multimodal_content("User message 2"),
                    id="h2",
                ),
                AIMessage(
                    content=make_multimodal_content("AI response 2"),
                    id="a2",
                ),
            ]
            
            config = make_compaction_config(
                min_messages_before_compaction=2,
                threshold=0.01,
                recent_message_window=2,
                min_recent_window=1,
                context_window_overrides={"gpt-4o": 1000},
            )
            
            context = CompactionContext(
                messages=messages,
                system_prompt_tokens=0,
                model_name="gpt-4o",
                config=config,
                llm_config={},
            )
            
            compactor = ContextCompactor(config, {})
            result = await compactor.compact_state(context)
            
            if result:
                # Verify all contents are clean
                for msg in result.replacement_messages:
                    if hasattr(msg, 'content'):
                        if isinstance(msg.content, str):
                            assert "[{'type':" not in msg.content
                            assert "image_url" not in msg.content
        finally:
            if patcher:
                patcher.stop()
