"""Tests for reasoning_content roundtrip in ThinkingChatOpenAI._get_request_payload.

These tests verify that the reasoning_content field from AIMessage.additional_kwargs
is preserved when converting messages to the API request payload.
"""

import pytest
from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from daemon.graph import ThinkingChatOpenAI


@pytest.fixture(autouse=True)
def enable_test_model_echo():
    """These tests use model="test-model" which should trigger echo.

    Echo uses denylist semantics: every model echoes unless its name matches
    ``reasoning_echo_disabled_models``. Resetting the disabled list to empty
    guarantees "test-model" echoes regardless of state leaked by other tests.
    """
    original = list(ThinkingChatOpenAI.reasoning_echo_disabled_models)
    ThinkingChatOpenAI.reasoning_echo_disabled_models = []
    yield
    ThinkingChatOpenAI.reasoning_echo_disabled_models = original


class TestGetRequestPayloadPreservesReasoningContent:
    """Tests for _get_request_payload preserving reasoning_content."""

    def test_single_message_with_reasoning_content_preserved(self):
        """AIMessage with reasoning_content in additional_kwargs is preserved in payload."""
        # Create instance with minimal config (we won't make actual API calls)
        llm = ThinkingChatOpenAI(model="test-model", api_key="test-key")

        # Create message with reasoning_content
        messages = [
            AIMessage(
                content="Answer here.",
                additional_kwargs={"reasoning_content": "I thought about this..."}
            )
        ]

        # Call _get_request_payload
        payload = llm._get_request_payload(messages)

        # Verify reasoning_content is in the payload
        assert "messages" in payload
        assert len(payload["messages"]) == 1

        assistant_msg = payload["messages"][0]
        assert assistant_msg["role"] == "assistant"
        assert assistant_msg.get("reasoning_content") == "I thought about this..."

    def test_multiple_assistant_messages_with_reasoning_content(self):
        """Multiple AIMessages with reasoning_content are all preserved in order."""
        llm = ThinkingChatOpenAI(model="test-model", api_key="test-key")

        messages = [
            AIMessage(
                content="First answer.",
                additional_kwargs={"reasoning_content": "First reasoning..."}
            ),
            AIMessage(
                content="Second answer.",
                additional_kwargs={"reasoning_content": "Second reasoning..."}
            ),
            AIMessage(
                content="Third answer.",
                additional_kwargs={"reasoning_content": "Third reasoning..."}
            ),
        ]

        payload = llm._get_request_payload(messages)

        assistant_messages = [m for m in payload["messages"] if m.get("role") == "assistant"]
        assert len(assistant_messages) == 3
        assert assistant_messages[0].get("reasoning_content") == "First reasoning..."
        assert assistant_messages[1].get("reasoning_content") == "Second reasoning..."
        assert assistant_messages[2].get("reasoning_content") == "Third reasoning..."

    def test_message_without_reasoning_content_no_extra_field(self):
        """AIMessage without reasoning_content does not get an extra field added."""
        llm = ThinkingChatOpenAI(model="test-model", api_key="test-key")

        messages = [
            AIMessage(content="Plain answer without reasoning.")
        ]

        payload = llm._get_request_payload(messages)

        assistant_msg = payload["messages"][0]
        assert assistant_msg.get("reasoning_content") is None

    def test_mixed_messages_selective_reasoning_content(self):
        """Only assistant messages with reasoning_content get the field, others don't."""
        llm = ThinkingChatOpenAI(model="test-model", api_key="test-key")

        messages = [
            HumanMessage(content="Hello, how are you?"),
            AIMessage(
                content="I'm doing well, thanks!",
                additional_kwargs={"reasoning_content": "Greeting response"}
            ),
            AIMessage(content="Just a plain response."),  # No reasoning
            AIMessage(
                content="Here is the info you requested.",
                additional_kwargs={"reasoning_content": "Providing information"}
            ),
        ]

        payload = llm._get_request_payload(messages)

        # Check each assistant message
        assistant_messages = [m for m in payload["messages"] if m.get("role") == "assistant"]

        assert len(assistant_messages) == 3
        assert assistant_messages[0].get("reasoning_content") == "Greeting response"
        assert assistant_messages[1].get("reasoning_content") is None
        assert assistant_messages[2].get("reasoning_content") == "Providing information"

    def test_conversation_with_tool_messages(self):
        """ToolMessages are handled correctly in mixed conversation."""
        llm = ThinkingChatOpenAI(model="test-model", api_key="test-key")

        messages = [
            HumanMessage(content="Run the ls command"),
            AIMessage(
                content="",
                additional_kwargs={"reasoning_content": "User wants to run ls"},
                tool_calls=[
                    {"id": "call_1", "name": "bash", "args": {"command": "ls"}}
                ]
            ),
            ToolMessage(content="file1.txt\nfile2.txt", tool_call_id="call_1"),
            AIMessage(
                content="Here are the files: file1.txt and file2.txt",
                additional_kwargs={"reasoning_content": "Presenting ls results"}
            ),
        ]

        payload = llm._get_request_payload(messages)

        assistant_messages = [m for m in payload["messages"] if m.get("role") == "assistant"]
        assert len(assistant_messages) == 2
        assert assistant_messages[0].get("reasoning_content") == "User wants to run ls"
        assert assistant_messages[1].get("reasoning_content") == "Presenting ls results"

    def test_empty_message_list(self):
        """Empty message list is handled without error."""
        llm = ThinkingChatOpenAI(model="test-model", api_key="test-key")

        payload = llm._get_request_payload([])

        assert "messages" in payload
        assert payload["messages"] == []

    def test_stop_parameter_preserved(self):
        """The stop parameter is passed through correctly."""
        llm = ThinkingChatOpenAI(model="test-model", api_key="test-key")

        messages = [
            HumanMessage(content="Stop at a certain point"),
            AIMessage(content="Response", additional_kwargs={"reasoning_content": "Thinking"})
        ]

        payload = llm._get_request_payload(messages, stop=["END"])

        assert payload.get("stop") == ["END"]
        assert payload["messages"][1].get("reasoning_content") == "Thinking"

    def test_empty_string_reasoning_content_preserved(self):
        """Empty string reasoning_content is preserved (not treated as falsy)."""
        llm = ThinkingChatOpenAI(model="test-model", api_key="test-key")

        messages = [
            AIMessage(
                content="Answer.",
                additional_kwargs={"reasoning_content": ""}
            )
        ]

        payload = llm._get_request_payload(messages)

        assistant_msg = payload["messages"][0]
        assert assistant_msg["role"] == "assistant"
        assert assistant_msg.get("reasoning_content") == ""



