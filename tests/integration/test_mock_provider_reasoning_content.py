"""Integration tests for reasoning_content via real HTTP calls.

These tests verify that reasoning_content survives round-trips through actual HTTP
requests to a mock LLM server with the ORIGINAL ThinkingChatOpenAI class.

Key behavior of original code:
1. _generate() makes 1 HTTP request (no fallback to raw response)
2. _get_request_payload() injects reasoning_content from AIMessage.additional_kwargs
3. For reasoning_content to be preserved across turns, it must be in additional_kwargs

These tests verify:
- Multi-turn conversations where reasoning_content is explicitly in AIMessage.additional_kwargs
- First requests have no prior reasoning_content
- Mixed messages (some with, some without reasoning_content)

Run with:
    pytest tests/integration/test_mock_provider_reasoning_content.py -v
"""

import json
import pytest
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from daemon.graph import ThinkingChatOpenAI

pytestmark = pytest.mark.integration


# =============================================================================
# Mock LLM Server (Real HTTP Server)
# =============================================================================


class MockServerState:
    """Tracks requests received by the mock server."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.request_history: list[dict[str, Any]] = []
        self.response_index = 0

    def record_request(self, payload: dict[str, Any]):
        """Record an incoming request payload."""
        self.request_history.append(payload)

    def get_next_response(self) -> tuple[str, str]:
        """Get the next mock response content and reasoning."""
        responses = [
            ("This is response 1.", "Thinking about the user's question for response 1."),
            ("This is response 2.", "Reasoning through the second question."),
            ("This is response 3.", "Final reasoning before answering."),
        ]
        idx = self.response_index % len(responses)
        self.response_index += 1
        return responses[idx]


# Global state for the mock server
server_state = MockServerState()


class MockLLMHandler(BaseHTTPRequestHandler):
    """HTTP request handler for mock LLM server."""

    def log_message(self, format, *args):
        """Suppress noisy logging."""
        pass

    def do_POST(self):
        """Handle POST requests to /v1/chat/completions or /chat/completions."""
        # Handle both /v1/chat/completions and /chat/completions
        if self.path in ("/v1/chat/completions", "/chat/completions"):
            self._handle_chat_completions()
        else:
            self.send_error(404, "Not Found")

    def _handle_chat_completions(self):
        """Handle /v1/chat/completions endpoint."""
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        payload = json.loads(body.decode("utf-8"))

        # Record the request payload
        server_state.record_request(payload)

        # Get response content and reasoning
        content, reasoning = server_state.get_next_response()

        response = {
            "id": f"mockchat-{int(time.time() * 1000)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": payload.get("model", "mock-model"),
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": content,
                        "reasoning_content": reasoning,
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 10,
                "total_tokens": 20,
            },
        }

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(response).encode("utf-8"))


class MockServer:
    """Threaded HTTP server for mock LLM."""

    def __init__(self, host="127.0.0.1", port=0):
        self.host = host
        self.port = port
        self.server = None
        self.thread = None
        self._actual_port = None

    def start(self):
        """Start the server on a random available port."""
        self.server = HTTPServer((self.host, self.port), MockLLMHandler)
        self._actual_port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.daemon = True
        self.thread.start()

    def stop(self):
        """Stop the server."""
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        if self.thread:
            self.thread.join(timeout=5)

    @property
    def base_url(self) -> str:
        """Get the base URL of the server."""
        return f"http://{self.host}:{self._actual_port}"


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(scope="function")
def mock_http_server():
    """Start a real HTTP server for the duration of the test."""
    server = MockServer(host="127.0.0.1", port=0)
    server.start()
    yield server
    server.stop()


@pytest.fixture(autouse=True)
def reset_server_state():
    """Reset server state before each test."""
    server_state.reset()
    yield
    server_state.reset()


# =============================================================================
# Integration Tests
# =============================================================================


class TestReasoningContentViaHTTP:
    """Test reasoning_content survives HTTP round-trips through actual API calls."""

    @pytest.mark.asyncio
    async def test_explicit_reasoning_content_injection(
        self, mock_http_server
    ):
        """Verify reasoning_content is injected when explicitly provided in AIMessage.

        The _get_request_payload() method extracts reasoning_content from
        AIMessage.additional_kwargs and injects it into the request payload.
        This test verifies that behavior works correctly.
        """
        llm = ThinkingChatOpenAI(
            model="mock-deepseek",
            api_key="test-key",
            base_url=mock_http_server.base_url,
            timeout=30.0,
        )

        # Turn 1: Initial request
        messages = [HumanMessage(content="Hello!")]
        response1 = await llm.ainvoke(messages)
        assert response1.content, "Should receive a response"

        # Verify Turn 1 request has only user message
        assert len(server_state.request_history) == 1
        req1 = server_state.request_history[0]
        assert len(req1["messages"]) == 1
        assert req1["messages"][0]["role"] == "user"
        assert "reasoning_content" not in req1["messages"][0]

        # Turn 2: Manually set reasoning_content in the response's additional_kwargs
        # This simulates what would happen if reasoning_content was extracted by some other means
        response1_with_reasoning = AIMessage(
            content=response1.content,
            additional_kwargs={"reasoning_content": "I thought about this in turn 1"}
        )

        messages = [
            HumanMessage(content="Hello!"),
            response1_with_reasoning,
            HumanMessage(content="Tell me more."),
        ]
        response2 = await llm.ainvoke(messages)
        assert response2.content, "Should receive a response"

        # Verify Turn 2 request has reasoning_content in assistant message
        assert len(server_state.request_history) == 2
        req2 = server_state.request_history[1]
        assistant_msgs = [m for m in req2["messages"] if m.get("role") == "assistant"]
        assert len(assistant_msgs) == 1
        assert assistant_msgs[0].get("reasoning_content") == "I thought about this in turn 1"

        print(f"\n[Explicit Injection] reasoning_content verified: {assistant_msgs[0].get('reasoning_content')}")

    @pytest.mark.asyncio
    async def test_first_request_no_prior_reasoning(self, mock_http_server):
        """First request should not have reasoning_content (no prior assistant messages)."""
        llm = ThinkingChatOpenAI(
            model="mock-deepseek",
            api_key="test-key",
            base_url=mock_http_server.base_url,
            timeout=30.0,
        )

        messages = [HumanMessage(content="Hello!")]
        response = await llm.ainvoke(messages)

        assert response.content, "Should receive a response"

        # First request should only have the human message
        assert len(server_state.request_history) == 1
        req1 = server_state.request_history[0]
        assert len(req1["messages"]) == 1
        assert req1["messages"][0]["role"] == "user"
        assert "reasoning_content" not in req1["messages"][0]

        print("\n[First Request] Verified: No reasoning_content in initial user message")

    @pytest.mark.asyncio
    async def test_multi_turn_with_manual_reasoning_injection(
        self, mock_http_server
    ):
        """3-turn conversation with explicit reasoning_content injection.

        This verifies the reasoning_content preservation pipeline:
        1. Turn 1: Plain request
        2. Turn 2: Inject reasoning_content from Turn 1's response
        3. Turn 3: Inject reasoning_content from Turn 2's response
        """
        llm = ThinkingChatOpenAI(
            model="mock-deepseek",
            api_key="test-key",
            base_url=mock_http_server.base_url,
            timeout=30.0,
        )

        # Turn 1
        messages = [HumanMessage(content="Turn 1: Hello!")]
        r1 = await llm.ainvoke(messages)
        assert r1.content

        # Turn 2: Inject reasoning_content
        r1_with_reasoning = AIMessage(
            content=r1.content,
            additional_kwargs={"reasoning_content": "Reasoning from turn 1"}
        )
        messages = [
            HumanMessage(content="Turn 1: Hello!"),
            r1_with_reasoning,
            HumanMessage(content="Turn 2: Tell me more."),
        ]
        r2 = await llm.ainvoke(messages)
        assert r2.content

        # Verify Turn 2 request
        req2 = server_state.request_history[1]
        assistant_msgs_2 = [m for m in req2["messages"] if m.get("role") == "assistant"]
        assert len(assistant_msgs_2) == 1
        assert assistant_msgs_2[0].get("reasoning_content") == "Reasoning from turn 1"

        # Turn 3: Inject reasoning_content from turn 2
        r2_with_reasoning = AIMessage(
            content=r2.content,
            additional_kwargs={"reasoning_content": "Reasoning from turn 2"}
        )
        messages.append(r2_with_reasoning)
        messages.append(HumanMessage(content="Turn 3: Final question."))
        r3 = await llm.ainvoke(messages)
        assert r3.content

        # Verify Turn 3 request
        req3 = server_state.request_history[2]
        assistant_msgs_3 = [m for m in req3["messages"] if m.get("role") == "assistant"]
        assert len(assistant_msgs_3) >= 1
        # The latest assistant message should have reasoning_content
        assert assistant_msgs_3[-1].get("reasoning_content") == "Reasoning from turn 2"

        print(f"\n[Multi-Turn] Total requests: {len(server_state.request_history)}")
        print(f"[Turn 2] reasoning_content: {assistant_msgs_2[0].get('reasoning_content')}")
        print(f"[Turn 3] Latest reasoning: {assistant_msgs_3[-1].get('reasoning_content')}")


class TestReasoningContentSyncInvoke:
    """Test sync invoke() method also preserves reasoning_content via HTTP."""

    def test_sync_invoke_with_reasoning_content(self, mock_http_server):
        """Test sync invoke() method with explicit reasoning_content injection."""
        llm = ThinkingChatOpenAI(
            model="mock-deepseek",
            api_key="test-key",
            base_url=mock_http_server.base_url,
            timeout=30.0,
        )

        # Turn 1
        messages = [HumanMessage(content="Hello!")]
        response1 = llm.invoke(messages)
        assert response1.content

        # Turn 2: Inject reasoning_content
        response1_with_reasoning = AIMessage(
            content=response1.content,
            additional_kwargs={"reasoning_content": "Sync thinking from turn 1"}
        )
        messages = [
            HumanMessage(content="Hello!"),
            response1_with_reasoning,
            HumanMessage(content="Continue."),
        ]
        response2 = llm.invoke(messages)
        assert response2.content

        # Verify the server received reasoning_content
        assert len(server_state.request_history) == 2
        req2 = server_state.request_history[1]
        assistant_msgs = [m for m in req2["messages"] if m.get("role") == "assistant"]
        assert len(assistant_msgs) == 1
        assert assistant_msgs[0].get("reasoning_content") == "Sync thinking from turn 1"

        print(f"\n[Sync Invoke] Verified: reasoning_content preserved via sync path")
        print(f"[Server Request] reasoning_content: {assistant_msgs[0].get('reasoning_content')}")

    def test_sync_multi_turn_reasoning_content_roundtrip(self, mock_http_server):
        """3-turn conversation via sync invoke() with reasoning_content injection."""
        llm = ThinkingChatOpenAI(
            model="mock-deepseek",
            api_key="test-key",
            base_url=mock_http_server.base_url,
            timeout=30.0,
        )

        # Turn 1
        messages = [HumanMessage(content="Turn 1")]
        r1 = llm.invoke(messages)

        # Turn 2: Inject reasoning
        r1_with_reasoning = AIMessage(content=r1.content, additional_kwargs={"reasoning_content": "Turn 1 reasoning"})
        messages = [
            HumanMessage(content="Turn 1"),
            r1_with_reasoning,
            HumanMessage(content="Turn 2"),
        ]
        r2 = llm.invoke(messages)

        # Turn 3: Inject reasoning
        r2_with_reasoning = AIMessage(content=r2.content, additional_kwargs={"reasoning_content": "Turn 2 reasoning"})
        messages.append(r2_with_reasoning)
        messages.append(HumanMessage(content="Turn 3"))
        r3 = llm.invoke(messages)

        # Verify all turns
        req1 = server_state.request_history[0]
        assert len(req1["messages"]) == 1  # Only user message

        req2 = server_state.request_history[1]
        assistant_msgs_2 = [m for m in req2["messages"] if m.get("role") == "assistant"]
        assert len(assistant_msgs_2) == 1
        assert assistant_msgs_2[0].get("reasoning_content") == "Turn 1 reasoning"

        req3 = server_state.request_history[2]
        assistant_msgs_3 = [m for m in req3["messages"] if m.get("role") == "assistant"]
        assert len(assistant_msgs_3) >= 1
        assert assistant_msgs_3[-1].get("reasoning_content") == "Turn 2 reasoning"

        print("\n[Sync Multi-Turn] Verified: All 3 turns preserve reasoning_content")


class TestReasoningContentEdgeCases:
    """Edge case tests for reasoning_content via HTTP."""

    @pytest.mark.asyncio
    async def test_mixed_reasoning_and_non_reasoning_via_http(
        self, mock_http_server
    ):
        """Only messages with reasoning_content get the field injected."""
        llm = ThinkingChatOpenAI(
            model="mock-deepseek",
            api_key="test-key",
            base_url=mock_http_server.base_url,
            timeout=30.0,
        )

        messages = [
            HumanMessage(content="Hello!"),
            # With reasoning
            AIMessage(
                content="Hi there!",
                additional_kwargs={"reasoning_content": "Greeting response"}
            ),
            # Without reasoning
            AIMessage(content="Just a regular response."),
            # With reasoning again
            AIMessage(
                content="Here's the info.",
                additional_kwargs={"reasoning_content": "Providing information"}
            ),
        ]

        response = await llm.ainvoke(messages)
        assert response.content

        # Verify the request received the correct payloads
        assert len(server_state.request_history) == 1
        req = server_state.request_history[0]

        assistant_msgs = [m for m in req["messages"] if m.get("role") == "assistant"]
        assert len(assistant_msgs) == 3
        assert assistant_msgs[0].get("reasoning_content") == "Greeting response"
        assert assistant_msgs[1].get("reasoning_content") is None  # No reasoning
        assert assistant_msgs[2].get("reasoning_content") == "Providing information"

        print("\n[Mixed Test] Verified: Only reasoning messages have the field")

    @pytest.mark.asyncio
    async def test_empty_string_reasoning_content_via_http(self, mock_http_server):
        """Empty string reasoning_content should be preserved (not treated as falsy)."""
        llm = ThinkingChatOpenAI(
            model="mock-deepseek",
            api_key="test-key",
            base_url=mock_http_server.base_url,
            timeout=30.0,
        )

        messages = [
            HumanMessage(content="Test."),
            AIMessage(
                content="Response.",
                additional_kwargs={"reasoning_content": ""}  # Empty string
            ),
            HumanMessage(content="Continue."),
        ]

        response = await llm.ainvoke(messages)
        assert response.content

        # Verify the request received reasoning_content (empty string should still be included)
        assert len(server_state.request_history) == 1
        req = server_state.request_history[0]

        assistant_msgs = [m for m in req["messages"] if m.get("role") == "assistant"]
        # Empty string reasoning_content should still be present (not treated as falsy)
        assert assistant_msgs[0].get("reasoning_content") == "", (
            "Empty string reasoning_content should still be present in payload"
        )

        print("\n[Empty String Test] Verified: Empty reasoning_content preserved")

    def test_conversation_without_reasoning_content_via_http(
        self, mock_http_server
    ):
        """Conversations should round-trip reasoning_content end-to-end through
        the non-streaming path (sync invoke).

        Previously this test asserted that reasoning_content was dropped from
        the second request's assistant message — that was a symptom of the
        bug where LangChain's _convert_dict_to_message() discarded the field
        and ThinkingChatOpenAI's _create_chat_result() didn't re-extract it.
        With the fix, reasoning_content is now correctly preserved across
        turns.
        """
        llm = ThinkingChatOpenAI(
            model="mock-deepseek",
            api_key="test-key",
            base_url=mock_http_server.base_url,
            timeout=30.0,
        )

        # First turn
        messages = [HumanMessage(content="Hello!")]
        r1 = llm.invoke(messages)

        # Verify r1 got the reasoning_content from the raw response
        assert r1.additional_kwargs.get("reasoning_content") == (
            "Thinking about the user's question for response 1."
        ), "Non-streaming invoke should now preserve reasoning_content"

        # Second turn
        messages.extend([r1, HumanMessage(content="How are you?")])
        r2 = llm.invoke(messages)

        # Verify both responses work
        assert r1.content
        assert r2.content

        # Verify requests
        assert len(server_state.request_history) == 2

        # First request should only have user message
        req1 = server_state.request_history[0]
        assert len(req1["messages"]) == 1

        # Second request should have user + assistant with reasoning_content
        # preserved via _get_request_payload injection
        req2 = server_state.request_history[1]
        assistant_msgs = [m for m in req2["messages"] if m.get("role") == "assistant"]
        assert len(assistant_msgs) == 1
        assert assistant_msgs[0].get("reasoning_content") == (
            "Thinking about the user's question for response 1."
        ), "reasoning_content from turn 1 should be preserved in turn 2 payload"

        print("\n[No Reasoning Test] Verified: Conversations correctly round-trip reasoning_content")
