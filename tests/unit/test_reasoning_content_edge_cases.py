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

    Echo uses denylist semantics: every model echoes unless its name matches
    ``reasoning_echo_disabled_models``. Resetting the disabled list to empty
    guarantees "test-model" echoes regardless of state leaked by other tests.
    """
    original = list(ThinkingChatOpenAI.reasoning_echo_disabled_models)
    ThinkingChatOpenAI.reasoning_echo_disabled_models = []
    yield
    ThinkingChatOpenAI.reasoning_echo_disabled_models = original


class TestReasoningContentEdgeCases:
    """Edge case tests for _get_request_payload reasoning_content injection."""

    def test_system_message_in_mixed_conversation(self):
        """SystemMessage + HumanMessage + AIMessage(reasoning) + HumanMessage works correctly.

        The fix should only inject reasoning_content into assistant messages,
        leaving SystemMessage and HumanMessage unaffected.
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
        assert payload["messages"][2].get("reasoning_content") == "Greeting the user"
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

        Both reasoning contents should be correctly injected into their respective
        assistant messages, regardless of interleaved HumanMessages.
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

        # Verify first assistant message has its reasoning_content
        assert assistant_messages[0]["content"] == "Python is a programming language."
        assert assistant_messages[0].get("reasoning_content") == "Explaining Python basics"

        # Verify second assistant message has its reasoning_content
        assert assistant_messages[1]["content"] == "JavaScript is also a programming language, mainly for web."
        assert assistant_messages[1].get("reasoning_content") == "Explaining JavaScript basics"

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

        # Verify assistant messages have reasoning_content
        assistant_messages = [m for m in payload["messages"] if m.get("role") == "assistant"]
        assert len(assistant_messages) == 2
        assert assistant_messages[0].get("reasoning_content") == "Greeting"
        assert assistant_messages[1].get("reasoning_content") == "Responding to how are you"
