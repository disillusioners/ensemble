"""Edge case tests for reasoning_content roundtrip in ThinkingChatOpenAI._get_request_payload.

These tests cover additional scenarios beyond the basic roundtrip tests:
- SystemMessage in mixed conversations
- Alternate 'reasoning' key in additional_kwargs
- Multi-turn with HumanMessages interleaved
- Conversations with only HumanMessages
"""

import pytest

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from daemon.graph import ThinkingChatOpenAI


@pytest.fixture(autouse=True)
def enable_test_model_echo():
    """These tests use model="test-model" which should trigger echo.

    The default echo list is ["deepseek"], so we add "test-model" so the
    model-name gate alone never suppresses echo here; any no-echo outcome in
    these tests is attributable solely to the tool-call gate (3949b8a7).
    """
    original = list(ThinkingChatOpenAI.reasoning_echo_models)
    ThinkingChatOpenAI.reasoning_echo_models = list(
        set(original) | {"test-model"}
    )
    yield
    ThinkingChatOpenAI.reasoning_echo_models = original


class TestReasoningContentEdgeCases:
    """Edge case tests for _get_request_payload reasoning_content injection."""

    def test_system_message_in_mixed_conversation(self):
        """SystemMessage + HumanMessage + AIMessage(reasoning) + HumanMessage works correctly.

        reasoning_content is NOT injected: this plain conversational turn has
        no tool calls (tool-call gate, 3949b8a7). SystemMessage and
        HumanMessage remain unaffected.
        """
        llm = ThinkingChatOpenAI(model="test-model", api_key="test-key")

        messages = [
            SystemMessage(content="You are a helpful assistant."),
            HumanMessage(content="Hello!"),
            AIMessage(
                content="Hi there! How can I help?",
                additional_kwargs={"reasoning_content": "Greeting the user"}
            ),
            HumanMessage(content="Can you explain reasoning?")
        ]

        payload = llm._get_request_payload(messages)

        assert "messages" in payload
        assert len(payload["messages"]) == 4

        # Verify message order is preserved
        assert payload["messages"][0]["role"] == "system"
        assert payload["messages"][0]["content"] == "You are a helpful assistant."
        assert payload["messages"][1]["role"] == "user"
        assert payload["messages"][1]["content"] == "Hello!"
        assert payload["messages"][2]["role"] == "assistant"
        # tool-call gate (3949b8a7): echo only on tool-call rounds — this
        # plain greeting turn must NOT carry reasoning_content
        assert payload["messages"][2].get("reasoning_content") is None
        assert payload["messages"][3]["role"] == "user"
        assert payload["messages"][3]["content"] == "Can you explain reasoning?"

    def test_additional_kwargs_reasoning_key_not_injected(self):
        """Test that 'reasoning' key in additional_kwargs is NOT injected (known gap).

        The current fix only checks for 'reasoning_content' in additional_kwargs.
        Some providers may use 'reasoning' as the key instead.
        This test documents the current behavior where 'reasoning' is NOT injected.
        """
        llm = ThinkingChatOpenAI(model="test-model", api_key="test-key")

        messages = [
            AIMessage(
                content="Answer with reasoning.",
                additional_kwargs={"reasoning": "I thought about this using reasoning key"}
            )
        ]

        payload = llm._get_request_payload(messages)

        assistant_msg = payload["messages"][0]
        assert assistant_msg["role"] == "assistant"

        # Current behavior: 'reasoning' key is NOT injected, only 'reasoning_content'
        # This is a known gap - the fix should be updated to check both keys
        assert assistant_msg.get("reasoning_content") is None
        assert assistant_msg.get("reasoning") is None

    def test_multi_turn_with_human_message_after_assistant(self):
        """SystemMessage + HumanMessage + AIMessage(reasoning) + HumanMessage(follow-up) + AIMessage(reasoning_2).

        Neither assistant turn carries reasoning_content in the payload:
        both are plain answers with no tool calls (tool-call gate, 3949b8a7),
        regardless of interleaved HumanMessages.
        """
        llm = ThinkingChatOpenAI(model="test-model", api_key="test-key")

        messages = [
            SystemMessage(content="You are a helpful assistant."),
            HumanMessage(content="What is Python?"),
            AIMessage(
                content="Python is a programming language.",
                additional_kwargs={"reasoning_content": "Explaining Python basics"}
            ),
            HumanMessage(content="And what about JavaScript?"),
            AIMessage(
                content="JavaScript is also a programming language, mainly for web.",
                additional_kwargs={"reasoning_content": "Explaining JavaScript basics"}
            )
        ]

        payload = llm._get_request_payload(messages)

        assert "messages" in payload
        assert len(payload["messages"]) == 5

        # Find assistant messages
        assistant_messages = [m for m in payload["messages"] if m.get("role") == "assistant"]
        assert len(assistant_messages) == 2

        # tool-call gate (3949b8a7): echo only on tool-call rounds — these
        # plain answer turns must NOT carry reasoning_content
        assert assistant_messages[0]["content"] == "Python is a programming language."
        assert assistant_messages[0].get("reasoning_content") is None

        assert assistant_messages[1]["content"] == "JavaScript is also a programming language, mainly for web."
        assert assistant_messages[1].get("reasoning_content") is None

        # Verify human messages are unchanged
        human_messages = [m for m in payload["messages"] if m.get("role") == "user"]
        assert len(human_messages) == 2
        assert human_messages[0]["content"] == "What is Python?"
        assert human_messages[1]["content"] == "And what about JavaScript?"

    def test_conversation_with_only_human_messages(self):
        """Conversation with only HumanMessages (no AIMessages) should not cause errors.

        When there are no AIMessages, nothing needs to be injected and the
        _get_request_payload should handle this gracefully.
        """
        llm = ThinkingChatOpenAI(model="test-model", api_key="test-key")

        messages = [
            SystemMessage(content="You are a helpful assistant."),
            HumanMessage(content="First message"),
            HumanMessage(content="Second message"),
            HumanMessage(content="Third message"),
        ]

        payload = llm._get_request_payload(messages)

        assert "messages" in payload
        assert len(payload["messages"]) == 4

        # All messages should be present and correct
        assert payload["messages"][0]["role"] == "system"
        assert payload["messages"][1]["role"] == "user"
        assert payload["messages"][2]["role"] == "user"
        assert payload["messages"][3]["role"] == "user"

        # No reasoning_content fields should be present (no assistant messages)
        for msg in payload["messages"]:
            assert "reasoning_content" not in msg
            assert msg.get("reasoning_content") is None

    def test_system_message_only(self):
        """Conversation with only a SystemMessage should not cause errors."""
        llm = ThinkingChatOpenAI(model="test-model", api_key="test-key")

        messages = [
            SystemMessage(content="You are a helpful assistant.")
        ]

        payload = llm._get_request_payload(messages)

        assert "messages" in payload
        assert len(payload["messages"]) == 1
        assert payload["messages"][0]["role"] == "system"

    def test_multiple_system_messages_in_conversation(self):
        """Multiple SystemMessages mixed with AIMessages work correctly.

        All SystemMessages should remain untouched, only AIMessages get
        reasoning_content injected.
        """
        llm = ThinkingChatOpenAI(model="test-model", api_key="test-key")

        messages = [
            SystemMessage(content="You are a helpful assistant."),
            SystemMessage(content="Be concise."),
            HumanMessage(content="Hello"),
            AIMessage(
                content="Hi!",
                additional_kwargs={"reasoning_content": "Greeting"}
            ),
            HumanMessage(content="How are you?"),
            AIMessage(
                content="Doing well, thanks!",
                additional_kwargs={"reasoning_content": "Responding to how are you"}
            )
        ]

        payload = llm._get_request_payload(messages)

        assert "messages" in payload
        assert len(payload["messages"]) == 6

        # Verify system messages unchanged
        assert payload["messages"][0]["role"] == "system"
        assert payload["messages"][0]["content"] == "You are a helpful assistant."
        assert payload["messages"][1]["role"] == "system"
        assert payload["messages"][1]["content"] == "Be concise."

        # tool-call gate (3949b8a7): echo only on tool-call rounds — these
        # plain conversational turns must NOT carry reasoning_content
        assistant_messages = [m for m in payload["messages"] if m.get("role") == "assistant"]
        assert len(assistant_messages) == 2
        assert assistant_messages[0].get("reasoning_content") is None
        assert assistant_messages[1].get("reasoning_content") is None
