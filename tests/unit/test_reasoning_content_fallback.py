"""Tests for reasoning_content fallback chain and edge cases in ThinkingChatOpenAI.

These tests verify the bug fixes in ThinkingChatOpenAI:
1. Fallback chain in _generate uses `is None` checks (reasoning_content → reasoning → response_metadata)
2. Store guard in _convert_delta_to_message_chunk uses `is not None` (preserves empty strings)
3. Added `reasoning` key fallback in streaming path
4. Logging wrapped with str() to prevent TypeError
5. _create_chat_result extracts reasoning_content from raw OpenAI response
   message dict (LangChain's _convert_dict_to_message drops it, which broke
   the web UI "show thinking" toggle for non-streaming GLM/DeepSeek responses)
"""

import pytest
from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
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


class TestCreateChatResultReasoningExtraction:
    """Tests for the _create_chat_result override.

    LangChain's stock _convert_dict_to_message() silently drops the
    ``reasoning_content`` (or ``reasoning``) field from non-streaming
    OpenAI-compatible responses. The override re-extracts it from the raw
    response message dict and stores it on additional_kwargs so the web UI
    can render the model's thinking.
    """

    def _make_response(self, choices: list[dict]) -> dict:
        """Build a minimal OpenAI chat.completion response dict."""
        return {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 1,
            "model": "glm-5",
            "choices": choices,
        }

    def test_reasoning_content_extracted_from_raw_response(self):
        """reasoning_content at the top level of the message dict must end
        up on additional_kwargs after _create_chat_result runs.
        """
        llm = ThinkingChatOpenAI(model="glm-5", api_key="test-key")
        response = self._make_response([{
            "index": 0,
            "finish_reason": "stop",
            "message": {
                "role": "assistant",
                "content": "Hello world",
                "reasoning_content": "an AI, the user wants a greeting...",
            },
        }])

        result = llm._create_chat_result(response)

        assert len(result.generations) == 1
        reasoning = result.generations[0].message.additional_kwargs.get("reasoning_content")
        assert reasoning == "an AI, the user wants a greeting..."

    def test_reasoning_key_fallback_in_create_chat_result(self):
        """When the response uses the ``reasoning`` key (DeepSeek style)
        instead of ``reasoning_content``, it should still be extracted.
        """
        llm = ThinkingChatOpenAI(model="deepseek", api_key="test-key")
        response = self._make_response([{
            "index": 0,
            "finish_reason": "stop",
            "message": {
                "role": "assistant",
                "content": "Sure",
                "reasoning": "user wants confirmation",
            },
        }])

        result = llm._create_chat_result(response)

        assert result.generations[0].message.additional_kwargs.get(
            "reasoning_content"
        ) == "user wants confirmation"

    def test_no_reasoning_content_unchanged(self):
        """When the response has no reasoning_content/reasoning, the message
        additional_kwargs should not gain a spurious empty key.
        """
        llm = ThinkingChatOpenAI(model="gpt-4", api_key="test-key")
        response = self._make_response([{
            "index": 0,
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": "Hello"},
        }])

        result = llm._create_chat_result(response)

        assert result.generations[0].message.additional_kwargs.get("reasoning_content") is None

    def test_existing_reasoning_content_not_clobbered(self):
        """If reasoning_content is already set on additional_kwargs (e.g. by
        the streaming path), the override should not overwrite it.
        """
        llm = ThinkingChatOpenAI(model="glm-5", api_key="test-key")

        # Pre-populate by patching the parent's _create_chat_result to return
        # a ChatResult that already has reasoning_content set.
        from langchain_core.outputs import ChatGeneration, ChatResult
        from langchain_core.messages import AIMessage

        existing = ChatResult(generations=[
            ChatGeneration(message=AIMessage(
                content="Hello",
                additional_kwargs={"reasoning_content": "preserved"},
            ))
        ])

        response = self._make_response([{
            "index": 0,
            "finish_reason": "stop",
            "message": {
                "role": "assistant",
                "content": "Hello",
                "reasoning_content": "from raw response",
            },
        }])

        with patch.object(
            ThinkingChatOpenAI.__bases__[0],
            "_create_chat_result",
            return_value=existing,
        ):
            result = llm._create_chat_result(response)

        assert result.generations[0].message.additional_kwargs.get(
            "reasoning_content"
        ) == "preserved"

    def test_multiple_choices_reasoning_extracted_per_choice(self):
        """n>1 choices (n-best) should each get their own reasoning_content
        attached to the corresponding generation.
        """
        llm = ThinkingChatOpenAI(model="glm-5", api_key="test-key")
        response = self._make_response([
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": "First",
                    "reasoning_content": "thinking one",
                },
            },
            {
                "index": 1,
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": "Second",
                    "reasoning_content": "thinking two",
                },
            },
        ])

        result = llm._create_chat_result(response)

        assert len(result.generations) == 2
        assert result.generations[0].message.additional_kwargs.get(
            "reasoning_content"
        ) == "thinking one"
        assert result.generations[1].message.additional_kwargs.get(
            "reasoning_content"
        ) == "thinking two"

    def test_basemodel_response_supported(self):
        """OpenAI's client returns a Pydantic BaseModel for non-streaming
        responses, not a dict. The override should call model_dump() and
        still find reasoning_content.
        """
        llm = ThinkingChatOpenAI(model="glm-5", api_key="test-key")
        response = MagicMock()
        response.model_dump.return_value = self._make_response([{
            "index": 0,
            "finish_reason": "stop",
            "message": {
                "role": "assistant",
                "content": "Hello",
                "reasoning_content": "model dump path",
            },
        }])

        result = llm._create_chat_result(response)

        assert result.generations[0].message.additional_kwargs.get(
            "reasoning_content"
        ) == "model dump path"


class TestReasoningEchoGating:
    """Tests for the reasoning_echo_models model-name gating.

    ``_get_request_payload`` must only inject ``reasoning_content`` for models
    whose name matches one of the patterns in ``reasoning_echo_models``
    (default: ``["deepseek"]``). For all other models, the payload must match
    stock ChatOpenAI behavior — i.e. reasoning_content is NOT injected, even
    when present on AIMessage.additional_kwargs.

    Why this matters:
      - DeepSeek's thinking-mode API requires reasoning_content in the
        assistant history whenever the prior turn had a tool call.
      - Raw OpenAI rejects unknown fields with a 400 error.
      - Some proxies silently ignore unknown fields, in which case echo is
        harmless but wastes payload bytes.
    """

    def _make_llm(self, model: str) -> ThinkingChatOpenAI:
        return ThinkingChatOpenAI(model=model, api_key="test-key")

    def test_default_echo_for_deepseek_model_name(self):
        """Default config echoes for any model whose name contains 'deepseek'."""
        llm = self._make_llm("deepseek-chat")
        messages = [AIMessage(
            content="Answer.",
            additional_kwargs={"reasoning_content": "thinking..."},
        )]
        payload = llm._get_request_payload(messages)
        assert payload["messages"][0].get("reasoning_content") == "thinking..."

    def test_default_echo_for_deepseek_case_insensitive(self):
        """Substring match must be case-insensitive ('DeepSeek-R1' → match)."""
        llm = self._make_llm("DeepSeek-R1")
        messages = [AIMessage(
            content="Answer.",
            additional_kwargs={"reasoning_content": "thinking..."},
        )]
        payload = llm._get_request_payload(messages)
        assert payload["messages"][0].get("reasoning_content") == "thinking..."

    def test_no_echo_for_openai_model(self):
        """GPT-4o and other OpenAI models must NOT receive reasoning_content
        echo (raw OpenAI rejects unknown fields).
        """
        llm = self._make_llm("gpt-4o")
        messages = [AIMessage(
            content="Answer.",
            additional_kwargs={"reasoning_content": "thinking..."},
        )]
        payload = llm._get_request_payload(messages)
        assert "reasoning_content" not in payload["messages"][0]
        assert payload["messages"][0].get("reasoning_content") is None

    def test_no_echo_for_glm_model(self):
        """GLM models must not have reasoning_content echoed (not a DeepSeek-style API)."""
        llm = self._make_llm("glm-5")
        messages = [AIMessage(
            content="Answer.",
            additional_kwargs={"reasoning_content": "thinking..."},
        )]
        payload = llm._get_request_payload(messages)
        assert "reasoning_content" not in payload["messages"][0]

    def test_no_echo_for_claude_model(self):
        """Claude models (via proxy) must not have reasoning_content echoed
        (Anthropic's API doesn't accept the field; it uses its own thinking
        blocks).
        """
        llm = self._make_llm("claude-3-5-sonnet-20241022")
        messages = [AIMessage(
            content="Answer.",
            additional_kwargs={"reasoning_content": "thinking..."},
        )]
        payload = llm._get_request_payload(messages)
        assert "reasoning_content" not in payload["messages"][0]

    def test_custom_echo_list_adds_pattern(self):
        """Adding a pattern to reasoning_echo_models enables echo for matching models."""
        original = list(ThinkingChatOpenAI.reasoning_echo_models)
        try:
            ThinkingChatOpenAI.reasoning_echo_models = original + ["glm"]
            llm = self._make_llm("glm-5")
            messages = [AIMessage(
                content="Answer.",
                additional_kwargs={"reasoning_content": "thinking..."},
            )]
            payload = llm._get_request_payload(messages)
            assert payload["messages"][0].get("reasoning_content") == "thinking..."
        finally:
            ThinkingChatOpenAI.reasoning_echo_models = original

    def test_empty_echo_list_disables_all_echo(self):
        """Setting reasoning_echo_models=[] disables echo for every model,
        including DeepSeek. Useful for operators who hit API issues and want
        to opt out.
        """
        original = list(ThinkingChatOpenAI.reasoning_echo_models)
        try:
            ThinkingChatOpenAI.reasoning_echo_models = []
            llm = self._make_llm("deepseek-chat")
            messages = [AIMessage(
                content="Answer.",
                additional_kwargs={"reasoning_content": "thinking..."},
            )]
            payload = llm._get_request_payload(messages)
            assert "reasoning_content" not in payload["messages"][0]
        finally:
            ThinkingChatOpenAI.reasoning_echo_models = original

    def test_echo_skips_non_assistant_messages(self):
        """Even when echo is enabled, only assistant messages get
        reasoning_content injected. Human/System/Tool messages are untouched.
        """
        llm = self._make_llm("deepseek-chat")
        messages = [
            SystemMessage(content="sys"),
            HumanMessage(content="hi"),
            AIMessage(
                content="Answer.",
                additional_kwargs={"reasoning_content": "thinking..."},
            ),
            HumanMessage(content="follow up"),
        ]
        payload = llm._get_request_payload(messages)
        for m in payload["messages"]:
            if m.get("role") == "assistant":
                assert m.get("reasoning_content") == "thinking..."
            else:
                assert "reasoning_content" not in m

    def test_echo_preserves_empty_string(self):
        """Empty-string reasoning_content must be preserved (not dropped)."""
        llm = self._make_llm("deepseek-chat")
        messages = [AIMessage(
            content="Answer.",
            additional_kwargs={"reasoning_content": ""},
        )]
        payload = llm._get_request_payload(messages)
        assert payload["messages"][0].get("reasoning_content") == ""

    def test_echo_with_tool_calls(self):
        """Multi-turn conversation with tool calls must echo reasoning_content
        on every assistant message (DeepSeek requires it for tool-calling turns).
        """
        llm = self._make_llm("deepseek-chat")
        messages = [
            HumanMessage(content="Run ls"),
            AIMessage(
                content="",
                tool_calls=[{"id": "c1", "name": "bash", "args": {"command": "ls"}}],
                additional_kwargs={"reasoning_content": "user wants ls"},
            ),
            ToolMessage(content="file1\nfile2", tool_call_id="c1"),
            AIMessage(
                content="Here are the files.",
                additional_kwargs={"reasoning_content": "presenting results"},
            ),
        ]
        payload = llm._get_request_payload(messages)
        assistants = [m for m in payload["messages"] if m.get("role") == "assistant"]
        assert assistants[0].get("reasoning_content") == "user wants ls"
        assert assistants[1].get("reasoning_content") == "presenting results"

    def test_no_echo_fast_path_matches_parent_output(self):
        """When echo is disabled for a model, the payload must be byte-identical
        to what the parent ChatOpenAI would produce (no extra fields, no
        removed fields). This is a regression guard against accidentally
        mutating the payload in the gating path.
        """
        llm = self._make_llm("gpt-4o")
        messages = [
            HumanMessage(content="hi"),
            AIMessage(
                content="hello",
                additional_kwargs={"reasoning_content": "should not be sent"},
            ),
        ]
        # Patch the superclass _get_request_payload to return a known-good payload
        from unittest.mock import patch
        import langchain_openai.chat_models.base as lc_base

        # Run the gated path
        gated_payload = llm._get_request_payload(messages)

        # Run the parent's path on the same LLM and same messages
        parent = ThinkingChatOpenAI.__bases__[0]
        with patch.object(parent, "_get_request_payload", wraps=parent._get_request_payload) as spy:
            parent_payload = spy(llm, messages)
            # gated payload should equal the parent payload (no echo branch entered)
            assert gated_payload["messages"] == parent_payload["messages"]
            assert "reasoning_content" not in gated_payload["messages"][1]


class TestShouldEchoReasoningMethod:
    """Direct tests for the _should_echo_reasoning() helper."""

    def test_matches_deepseek_substring(self):
        llm = ThinkingChatOpenAI(model="deepseek-chat", api_key="test-key")
        assert llm._should_echo_reasoning() is True

    def test_matches_deepseek_capitalized(self):
        llm = ThinkingChatOpenAI(model="DeepSeek-V3", api_key="test-key")
        assert llm._should_echo_reasoning() is True

    def test_does_not_match_gpt(self):
        llm = ThinkingChatOpenAI(model="gpt-4o", api_key="test-key")
        assert llm._should_echo_reasoning() is False

    def test_empty_model_name_returns_false(self):
        llm = ThinkingChatOpenAI(model="", api_key="test-key")
        assert llm._should_echo_reasoning() is False

    def test_custom_patterns_applied(self):
        original = list(ThinkingChatOpenAI.reasoning_echo_models)
        try:
            ThinkingChatOpenAI.reasoning_echo_models = ["custom-pattern"]
            llm_match = ThinkingChatOpenAI(model="custom-pattern-x", api_key="test-key")
            llm_no_match = ThinkingChatOpenAI(model="deepseek-chat", api_key="test-key")
            assert llm_match._should_echo_reasoning() is True
            assert llm_no_match._should_echo_reasoning() is False
        finally:
            ThinkingChatOpenAI.reasoning_echo_models = original
