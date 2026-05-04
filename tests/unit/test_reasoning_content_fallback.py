"""Tests for reasoning_content fallback chain and edge cases in ThinkingChatOpenAI.

These tests verify the 4 bug fixes in ThinkingChatOpenAI:
1. Fallback chain in _generate uses `is None` checks (reasoning_content → reasoning → response_metadata)
2. Store guard in _convert_delta_to_message_chunk uses `is not None` (preserves empty strings)
3. Added `reasoning` key fallback in streaming path
4. Logging wrapped with str() to prevent TypeError
"""

import pytest
from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.messages.ai import AIMessageChunk

from daemon.graph import ThinkingChatOpenAI


class TestReasoningContentFallbackChain:
    """Tests for the fallback chain in _generate method."""

    def test_empty_string_reasoning_content_preserved_from_primary(self):
        """Bug fix #2: Empty string reasoning_content should be preserved (not overwritten).

        When reasoning_content="" is set in additional_kwargs, the store guard should
        use `is not None` check so empty strings are NOT overwritten by fallback.
        """
        llm = ThinkingChatOpenAI(model="test-model", api_key="test-key")

        # Create mock response with reasoning_content="" (empty string)
        mock_message = MagicMock()
        mock_message.additional_kwargs = {"reasoning_content": ""}
        mock_message.response_metadata = {}

        mock_generation = MagicMock()
        mock_generation.message = mock_message

        mock_result = MagicMock()
        mock_result.generations = [mock_generation]

        # Patch parent _generate to return our mock
        with patch.object(
            ThinkingChatOpenAI.__bases__[0], '_generate', return_value=mock_result
        ):
            messages = [AIMessage(content="Test response")]
            result = llm._generate(messages)

            # Verify the reasoning_content is still empty string (not overwritten)
            # The fix should NOT have replaced "" with None from fallback
            assert result.generations[0].message.additional_kwargs.get("reasoning_content") == ""

    def test_fallback_chain_reasoning_key(self):
        """Bug fix #1: reasoning key should be picked up when reasoning_content is absent.

        When reasoning_content is not in additional_kwargs but reasoning="..." is present,
        the fallback should pick up the 'reasoning' key.
        """
        llm = ThinkingChatOpenAI(model="test-model", api_key="test-key")

        # Create mock response with 'reasoning' key but no 'reasoning_content'
        mock_message = MagicMock()
        mock_message.additional_kwargs = {"reasoning": "via-reasoning-key"}
        mock_message.response_metadata = {}

        mock_generation = MagicMock()
        mock_generation.message = mock_message

        mock_result = MagicMock()
        mock_result.generations = [mock_generation]

        with patch.object(
            ThinkingChatOpenAI.__bases__[0], '_generate', return_value=mock_result
        ):
            messages = [AIMessage(content="Test response")]
            result = llm._generate(messages)

            # Verify the fallback chain worked: reasoning_content should now be set
            assert result.generations[0].message.additional_kwargs.get("reasoning_content") == "via-reasoning-key"

    def test_fallback_chain_response_metadata(self):
        """Bug fix #1: response_metadata should be last fallback source.

        When neither reasoning_content nor reasoning is in additional_kwargs,
        but response_metadata has reasoning_content, it should be picked up.
        """
        llm = ThinkingChatOpenAI(model="test-model", api_key="test-key")

        # Create mock response with reasoning_content ONLY in response_metadata
        mock_message = MagicMock()
        mock_message.additional_kwargs = {}  # No reasoning_content or reasoning
        mock_message.response_metadata = {"reasoning_content": "from-metadata"}

        mock_generation = MagicMock()
        mock_generation.message = mock_message

        mock_result = MagicMock()
        mock_result.generations = [mock_generation]

        with patch.object(
            ThinkingChatOpenAI.__bases__[0], '_generate', return_value=mock_result
        ):
            messages = [AIMessage(content="Test response")]
            result = llm._generate(messages)

            # Verify the fallback chain worked: reasoning_content from metadata was picked up
            assert result.generations[0].message.additional_kwargs.get("reasoning_content") == "from-metadata"


class TestStreamingFallback:
    """Tests for streaming path (_convert_delta_to_message_chunk).

    Note: Streaming deltas produce AIMessageChunk objects, not AIMessage.
    """

    def test_streaming_empty_string_preserved(self):
        """Bug fix #2: Empty string reasoning_content should be preserved in streaming.

        When streaming delta has reasoning_content="", the key should not be dropped
        (the store guard should use `is not None`).
        """
        llm = ThinkingChatOpenAI(model="test-model", api_key="test-key")

        # Mock delta with reasoning_content=""
        delta = {"reasoning_content": ""}

        # Use AIMessageChunk (what streaming produces)
        result = llm._convert_delta_to_message_chunk(delta, AIMessageChunk)

        # The result should have reasoning_content="" preserved
        assert result.additional_kwargs.get("reasoning_content") == ""

    def test_streaming_reasoning_key_fallback(self):
        """Bug fix #3: 'reasoning' key fallback should work in streaming path.

        When streaming delta has 'reasoning' key but no 'reasoning_content',
        the fallback should pick it up.
        """
        llm = ThinkingChatOpenAI(model="test-model", api_key="test-key")

        # Mock delta with 'reasoning' key only
        delta = {"reasoning": "stream-reason"}

        # Use AIMessageChunk (what streaming produces)
        result = llm._convert_delta_to_message_chunk(delta, AIMessageChunk)

        # The fallback should have picked up the 'reasoning' key
        assert result.additional_kwargs.get("reasoning_content") == "stream-reason"


class TestLoggingEdgeCases:
    """Tests for logging edge cases (str() wrapping)."""

    def test_non_string_reasoning_content_no_crash(self):
        """Bug fix #4: Non-string reasoning_content should not cause TypeError in logging.

        When reasoning_content is a non-string type (e.g., dict), the str() wrapping
        in logging should prevent TypeError.
        """
        llm = ThinkingChatOpenAI(model="test-model", api_key="test-key")

        # Create mock response with non-string reasoning_content
        mock_message = MagicMock()
        mock_message.additional_kwargs = {"reasoning_content": {"nested": "dict"}}
        mock_message.response_metadata = {}

        mock_generation = MagicMock()
        mock_generation.message = mock_message

        mock_result = MagicMock()
        mock_result.generations = [mock_generation]

        # This should NOT raise TypeError
        with patch.object(
            ThinkingChatOpenAI.__bases__[0], '_generate', return_value=mock_result
        ):
            messages = [AIMessage(content="Test response")]
            # Should complete without raising
            result = llm._generate(messages)
            # The value should still be stored (as-is, since logging worked)
            assert result.generations[0].message.additional_kwargs.get("reasoning_content") == {"nested": "dict"}

    def test_non_string_reasoning_in_streaming_no_crash(self):
        """Bug fix #4: Non-string reasoning_content should not crash in streaming logging.

        When streaming delta has non-string reasoning_content, str() wrapping
        should prevent any logging-related errors.
        """
        llm = ThinkingChatOpenAI(model="test-model", api_key="test-key")

        # Mock delta with non-string reasoning_content
        delta = {"reasoning_content": 12345}

        # Use AIMessageChunk (what streaming produces)
        result = llm._convert_delta_to_message_chunk(delta, AIMessageChunk)

        # Should complete without raising and store the value
        assert result.additional_kwargs.get("reasoning_content") == 12345
