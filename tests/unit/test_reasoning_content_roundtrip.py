"""Tests for reasoning_content roundtrip in ThinkingChatOpenAI._get_request_payload.

These tests verify the spec-compliant echo behavior for ``reasoning_content``
in multi-turn assistant messages. Per DeepSeek thinking-mode
(https://api-docs.deepseek.com/guides/thinking_mode) ``reasoning_content``
MUST only be echoed on assistant turns that included at least one tool call.
Echoing it on plain final-answer turns can cause 400 errors on strict
endpoints, so the daemon's ``_get_request_payload`` gates injection on
tool-call presence in addition to the model-name match.

The tests below split into three groups:

  * ``TestGetRequestPayloadPreservesReasoningContent`` — happy-path
    coverage: when conditions are met (matching model + tool-call turn +
    stored ``reasoning_content``), the field is preserved.
  * ``TestReasoningEchoToolCallGate`` — gating coverage: the tool-call
    requirement, mixed-history behavior, the non-matching-model /
    no-reasoning-stored regression pins, the AIMessageChunk streaming
    variant, the empty-``tool_calls`` semantics, and the checkpoint
    serialization round-trip invariant.
"""

import pytest
from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_core.load import dumps, loads

from daemon.graph import ThinkingChatOpenAI


# A reusable tool-call spec for AIMessages we want the echo gate to pass.
_TOOL_CALL = {"id": "call_1", "name": "bash", "args": {"command": "ls"}}


@pytest.fixture(autouse=True)
def enable_test_model_echo():
    """These tests use model="test-model" which should trigger echo.

    The default echo list is ["deepseek"], so we add "test-model" so the
    pre-existing test assertions (which expect echo to happen) still hold.
    """
    original = list(ThinkingChatOpenAI.reasoning_echo_models)
    ThinkingChatOpenAI.reasoning_echo_models = list(
        set(original) | {"test-model"}
    )
    yield
    ThinkingChatOpenAI.reasoning_echo_models = original


class TestGetRequestPayloadPreservesReasoningContent:
    """Tests for _get_request_payload preserving reasoning_content.

    Per DeepSeek thinking-mode spec, ``reasoning_content`` is echoed ONLY
    when the original assistant turn issued a tool call, so each AIMessage
    below carries ``tool_calls=[...]`` to satisfy the gate.
    """

    def test_single_message_with_reasoning_content_preserved(self):
        """AIMessage with reasoning_content + tool_calls is preserved in payload."""
        # Create instance with minimal config (we won't make actual API calls)
        llm = ThinkingChatOpenAI(model="test-model", api_key="test-key")

        # Create message with reasoning_content AND tool_calls (spec requirement)
        messages = [
            AIMessage(
                content="Answer here.",
                additional_kwargs={"reasoning_content": "I thought about this..."},
                tool_calls=[_TOOL_CALL],
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
        """Multiple AIMessages with reasoning_content + tool_calls are all preserved."""
        llm = ThinkingChatOpenAI(model="test-model", api_key="test-key")

        messages = [
            AIMessage(
                content="First answer.",
                additional_kwargs={"reasoning_content": "First reasoning..."},
                tool_calls=[_TOOL_CALL],
            ),
            AIMessage(
                content="Second answer.",
                additional_kwargs={"reasoning_content": "Second reasoning..."},
                tool_calls=[_TOOL_CALL],
            ),
            AIMessage(
                content="Third answer.",
                additional_kwargs={"reasoning_content": "Third reasoning..."},
                tool_calls=[_TOOL_CALL],
            ),
        ]

        payload = llm._get_request_payload(messages)

        assistant_messages = [m for m in payload["messages"] if m.get("role") == "assistant"]
        assert len(assistant_messages) == 3
        assert assistant_messages[0].get("reasoning_content") == "First reasoning..."
        assert assistant_messages[1].get("reasoning_content") == "Second reasoning..."
        assert assistant_messages[2].get("reasoning_content") == "Third reasoning..."

    def test_tool_call_message_without_stored_reasoning_content_no_injection(self):
        """Tool-call AIMessage with NO stored reasoning_content → not echoed.

        Regression-pin on the existing presence gate: even when the tool-call
        gate passes, the field must NOT be synthesized from nothing. Guards
        against a future change that fabricates a ``reasoning_content`` key
        on every assistant turn.
        """
        llm = ThinkingChatOpenAI(model="test-model", api_key="test-key")

        messages = [
            AIMessage(content="Plain answer without reasoning.", tool_calls=[_TOOL_CALL])
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
                additional_kwargs={"reasoning_content": "Greeting response"},
                tool_calls=[_TOOL_CALL],
            ),
            # Plain assistant (no reasoning, no tool_calls) — must NOT gain a field
            AIMessage(content="Just a plain response."),
            AIMessage(
                content="Here is the info you requested.",
                additional_kwargs={"reasoning_content": "Providing information"},
                tool_calls=[_TOOL_CALL],
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
        """ToolMessages are handled correctly in mixed conversation.

        The first assistant turn carries tool_calls and is echoed; the final
        answer turn does NOT carry tool_calls and is therefore NOT echoed
        (per DeepSeek thinking-mode spec).
        """
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
            # Final-answer turn — no tool_calls → reasoning_content must NOT echo.
            AIMessage(
                content="Here are the files: file1.txt and file2.txt",
                additional_kwargs={"reasoning_content": "Presenting ls results"},
            ),
        ]

        payload = llm._get_request_payload(messages)

        assistant_messages = [m for m in payload["messages"] if m.get("role") == "assistant"]
        assert len(assistant_messages) == 2
        assert assistant_messages[0].get("reasoning_content") == "User wants to run ls"
        assert assistant_messages[1].get("reasoning_content") is None

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
            AIMessage(
                content="Response",
                additional_kwargs={"reasoning_content": "Thinking"},
                tool_calls=[_TOOL_CALL],
            ),
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
                additional_kwargs={"reasoning_content": ""},
                tool_calls=[_TOOL_CALL],
            )
        ]

        payload = llm._get_request_payload(messages)

        assistant_msg = payload["messages"][0]
        assert assistant_msg["role"] == "assistant"
        assert assistant_msg.get("reasoning_content") == ""


class TestReasoningEchoToolCallGate:
    """Tests for the tool-call gate on ``reasoning_content`` echo.

    Per DeepSeek thinking-mode spec, ``reasoning_content`` is echoed ONLY for
    assistant turns that included a tool call. The gate lives in
    ``ThinkingChatOpenAI._get_request_payload`` and is additive to the
    existing presence gate (the field must be set on
    ``additional_kwargs``).
    """

    def test_tool_call_assistant_echoes_reasoning_content(self):
        """Assistant with tool_calls + reasoning_content → echoed."""
        llm = ThinkingChatOpenAI(model="test-model", api_key="test-key")
        messages = [
            AIMessage(
                content="",
                additional_kwargs={"reasoning_content": "thinking-then-call"},
                tool_calls=[_TOOL_CALL],
            )
        ]
        payload = llm._get_request_payload(messages)
        assistant_msg = payload["messages"][0]
        assert assistant_msg.get("reasoning_content") == "thinking-then-call"

    def test_plain_assistant_without_tool_calls_does_not_echo(self):
        """Assistant WITHOUT tool_calls + reasoning_content → NOT echoed.

        Regression for the spec violation: previously the loop re-injected
        ``reasoning_content`` into every assistant payload dict whose
        original AIMessage had the field stored, which can cause 400s on
        strict endpoints.
        """
        llm = ThinkingChatOpenAI(model="test-model", api_key="test-key")
        messages = [
            AIMessage(
                content="Final answer.",
                additional_kwargs={"reasoning_content": "thoughts"},
            )
        ]
        payload = llm._get_request_payload(messages)
        assistant_msg = payload["messages"][0]
        assert "reasoning_content" not in assistant_msg

    def test_mixed_history_only_tool_call_turn_echoes(self):
        """Mixed history: tool-call assistant + plain assistant → only the
        tool-call turn carries ``reasoning_content``."""
        llm = ThinkingChatOpenAI(model="test-model", api_key="test-key")
        messages = [
            HumanMessage(content="Run ls"),
            AIMessage(
                content="",
                additional_kwargs={"reasoning_content": "tool-call reasoning"},
                tool_calls=[_TOOL_CALL],
            ),
            ToolMessage(content="file1\nfile2", tool_call_id="call_1"),
            AIMessage(
                content="Done.",
                additional_kwargs={"reasoning_content": "final-answer reasoning"},
            ),
        ]
        payload = llm._get_request_payload(messages)
        assistant_messages = [m for m in payload["messages"] if m.get("role") == "assistant"]
        assert len(assistant_messages) == 2
        assert assistant_messages[0].get("reasoning_content") == "tool-call reasoning"
        assert assistant_messages[1].get("reasoning_content") is None

    def test_non_matching_model_does_not_echo_even_with_tool_calls(self):
        """Non-matching model name (e.g. gpt-4o) → nothing echoed regardless.

        Regression-pin: the model-name fast path in ``_should_echo_reasoning``
        must short-circuit before the tool-call gate runs.
        """
        # "gpt-4o" is NOT in the default ["deepseek"] list and not added by
        # the autouse fixture (which adds only "test-model").
        llm = ThinkingChatOpenAI(model="gpt-4o", api_key="test-key")
        messages = [
            AIMessage(
                content="",
                additional_kwargs={"reasoning_content": "should be skipped"},
                tool_calls=[_TOOL_CALL],
            )
        ]
        payload = llm._get_request_payload(messages)
        assistant_msg = payload["messages"][0]
        assert "reasoning_content" not in assistant_msg

    def test_tool_call_assistant_without_reasoning_content_does_not_echo(self):
        """Tool-call assistant with NO stored reasoning_content → not echoed.

        Regression-pin: the existing presence gate (the field must be set
        on ``additional_kwargs``) is preserved. Adding the tool-call gate
        does not weaken the original guard.
        """
        llm = ThinkingChatOpenAI(model="test-model", api_key="test-key")
        messages = [
            AIMessage(
                content="",
                tool_calls=[_TOOL_CALL],
                # No reasoning_content in additional_kwargs
            )
        ]
        payload = llm._get_request_payload(messages)
        assistant_msg = payload["messages"][0]
        assert "reasoning_content" not in assistant_msg

    def test_empty_tool_calls_list_does_not_echo(self):
        """AIMessage with explicit empty ``tool_calls=[]`` + reasoning_content → NOT echoed.

        Pins the ``bool()`` semantics of the gate. An empty list is falsy,
        so the gate must reject it — guards against a regression where
        someone weakens the gate from ``bool(... or ...)`` to
        ``... is not None`` and accidentally lets through messages whose
        ``tool_calls`` attribute is set but empty.
        """
        llm = ThinkingChatOpenAI(model="test-model", api_key="test-key")
        messages = [
            AIMessage(
                content="Answer.",
                # Explicit empty list — distinct from "no tool_calls kwarg".
                tool_calls=[],
                additional_kwargs={"reasoning_content": "should be skipped"},
            )
        ]
        payload = llm._get_request_payload(messages)
        assistant_msg = payload["messages"][0]
        assert "reasoning_content" not in assistant_msg

    def test_ai_message_chunk_with_tool_call_chunks_echoes(self):
        """AIMessageChunk with ``tool_call_chunks`` + reasoning_content → echoed.

        Coverage for the streaming / chunk path of the tool-call gate.
        ``AIMessageChunk`` is a subclass of ``AIMessage`` (so it passes
        ``isinstance(m, AIMessage)``) and exposes ``tool_call_chunks``
        instead of (or in addition to) ``tool_calls``. The gate's
        ``or getattr(original, "tool_call_chunks", None)`` clause covers
        this variant.

        ToolCallChunk shape follows langchain_core's streaming protocol:
        ``name``, ``args`` (JSON-encoded string), ``id``, ``index``.
        """
        from langchain_core.messages import AIMessageChunk

        llm = ThinkingChatOpenAI(model="test-model", api_key="test-key")
        chunk = AIMessageChunk(
            content="",
            tool_call_chunks=[
                {
                    "name": "bash",
                    "args": '{"command":"ls"}',
                    "id": "call_1",
                    "index": 0,
                }
            ],
            additional_kwargs={"reasoning_content": "thinking-then-call"},
        )
        payload = llm._get_request_payload([chunk])
        assistant_msg = payload["messages"][0]
        assert assistant_msg.get("reasoning_content") == "thinking-then-call"

    def test_roundtrip_via_checkpoint_serialization_still_echoes(self):
        """Tool-call AIMessage roundtripped through ``langchain_core.load``
        (the same path LangGraph checkpointer uses) still echoes.

        Regression-pin: the gate inspects ``tool_calls`` on the original
        ``AIMessage`` — if a future LangChain upgrade drops ``tool_calls``
        during serialization (or strips them on load), this test catches
        it. Guards the invariant that ``tool_calls`` survives the
        checkpoint round-trip.
        """
        llm = ThinkingChatOpenAI(model="test-model", api_key="test-key")
        original = AIMessage(
            content="",
            additional_kwargs={"reasoning_content": "thinking-then-call"},
            tool_calls=[_TOOL_CALL],
        )
        # Round-trip through the canonical LangChain serialization path.
        restored = loads(dumps(original))
        # Sanity: type + attributes survived.
        assert isinstance(restored, AIMessage)
        assert restored.tool_calls, "tool_calls must survive serialization"
        assert restored.additional_kwargs.get("reasoning_content") == "thinking-then-call"

        payload = llm._get_request_payload([restored])
        assistant_msg = payload["messages"][0]
        assert assistant_msg.get("reasoning_content") == "thinking-then-call"


