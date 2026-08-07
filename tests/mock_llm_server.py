#!/usr/bin/env python3
"""
Mock LLM Server - OpenAI-compatible API for testing.
Listens on configurable port (default 4123), returns mock responses.
Supports /v1/chat/completions endpoint.
"""

import os
import time
import json
import argparse
from datetime import datetime
from typing import Generator, Any
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Mock LLM Server")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class Message(BaseModel):
    role: str
    content: str | None = None
    tool_calls: Any = None
    tool_call_id: Any = None


class ChatCompletionRequest(BaseModel):
    model: str = "mock-model"
    messages: list[Message]
    temperature: float = 0.7
    max_tokens: int | None = None
    stream: bool = False
    stream_delay: float = 0.1
    mock_response: str | None = None


MOCK_CONTENT_RESPONSES = [
    "This is a mock response from the LLM server. The server is working correctly.",
    "Mock LLM response: I received your message and processed it successfully.",
    "Hello! I'm a mock LLM server. Your message has been received and I'm generating this response.",
    "Response from mock server: Testing the LLM integration is working as expected.",
    "Mock response: The system is functioning correctly with the configured upstream URL.",
]

MOCK_THINKING_RESPONSES = [
    "Let me think about this... The user is asking me to respond to their message. I should provide a helpful and accurate response.",
    "Processing the request... Analyzing the input and formulating an appropriate reply based on the context provided.",
    "I'll help you with that. First, let me consider the key points of your question before generating a response.",
    "Thinking through the best way to answer... I need to provide accurate information while being concise and clear.",
    "Analyzing your message... I'll generate a response that addresses your query in a helpful manner.",
]


class MockLLMState:
    def __init__(self):
        self.request_count = 0
        self.response_index = 0

    def get_response(self, custom: str | None = None) -> tuple[str, str]:
        if custom:
            return custom, ""
        content = MOCK_CONTENT_RESPONSES[self.response_index % len(MOCK_CONTENT_RESPONSES)]
        thinking = MOCK_THINKING_RESPONSES[self.response_index % len(MOCK_THINKING_RESPONSES)]
        self.response_index += 1
        return content, thinking

    def increment(self):
        self.request_count += 1


state = MockLLMState()


# ---------------------------------------------------------------------------
# Watchover test support
# ---------------------------------------------------------------------------

# Canonical markdown guardrails returned by the watcher context builder.
BUILDER_GUARDRAILS = """## Agent Activity
The instance is performing Kubernetes cluster operations — checking node status and pod health.

## Available Tools
- bash: execute shell commands
- read_file: read file contents

## Allowed
- kubectl get, kubectl describe, kubectl top — read-only cluster inspection
- reading files within the working tree

## Forbidden
- kubectl delete, kubectl apply, kubectl edit — mutating operations
- kubectl exec into production pods
- modifying system configuration files

## Requirement
no destructive operations; read-only cluster inspection only"""


class WatchoverTestState:
    """Deterministic, scenario-driven state for watchover test responses."""

    def __init__(self):
        self.watcher_call_count: int = 0
        self.builder_call_count: int = 0
        self.agent_call_count: int = 0
        self.scenario: str = "allow"
        self.active: bool = False  # set True when a scenario is activated via /scenario
        self._watcher_call_index: int = 0  # internal counter within scenario
        self._agent_call_index: int = 0

    def set_scenario(self, scenario: str) -> str:
        """Switch scenario and reset internal counters. Returns old scenario."""
        old = self.scenario
        self.scenario = scenario
        self.active = True
        self.watcher_call_count = 0
        self.builder_call_count = 0
        self.agent_call_count = 0
        self._watcher_call_index = 0
        self._agent_call_index = 0
        return old


watchover_state = WatchoverTestState()


def reset_watchover_state() -> None:
    """Reset the watchover test state to defaults (useful between tests)."""
    watchover_state.scenario = "allow"
    watchover_state.active = False
    watchover_state.watcher_call_count = 0
    watchover_state.builder_call_count = 0
    watchover_state.agent_call_count = 0
    watchover_state._watcher_call_index = 0
    watchover_state._agent_call_index = 0


def build_watchover_response(
    content: str | None,
    tool_calls: list | None,
    model: str,
    finish_reason: str = "stop",
) -> JSONResponse:
    """Build an OpenAI-compatible chat completion response for watchover tests.

    Supports both text-only (verdict/builder) responses and tool_call responses.
    """
    message: dict = {"role": "assistant"}
    if content is not None:
        message["content"] = content
    else:
        message["content"] = None
    message["reasoning_content"] = None  # backward compat
    if tool_calls is not None:
        message["tool_calls"] = tool_calls

    completion_tokens = 0
    if content:
        completion_tokens = len(content.split())
    if tool_calls:
        completion_tokens += sum(
            len(str(tc.get("function", {}).get("arguments", "")).split())
            for tc in tool_calls
        )

    return JSONResponse(
        content={
            "id": f"mockchat-{int(time.time() * 1000)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": finish_reason,
                }
            ],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": completion_tokens,
                "total_tokens": completion_tokens,
            },
        }
    )


def _extract_message_text(req: ChatCompletionRequest) -> str:
    """Concatenate all message contents into a single lowercased string for detection."""
    parts = []
    for msg in req.messages:
        if msg.content:
            parts.append(msg.content)
    return "\n".join(parts).lower()


def _detect_call_type(req: ChatCompletionRequest) -> str:
    """Classify an incoming LLM call into one of: watcher, builder, agent, snapshot.

    Detection priority:
      1. '[CONVERSATION SNAPSHOT]' in any message AND 'summarize' in system
         message -> snapshot (snapshot regeneration call). Checked FIRST
         because snapshot payloads may also carry watchover-related text.
      2. Any message contains '[WATCHOVER CHECK]' -> watcher evaluator
      3. Any message contains '[WATCHOVER CONTEXT]' -> watcher evaluator
      4. System message contains 'security-profile compiler',
         'Watcher Context Builder', or 'Build the watchover context' -> builder
      5. Otherwise -> agent
    """
    text = _extract_message_text(req)

    # Snapshot detection: requires BOTH the layer-3 marker AND a 'summarize'
    # cue in the system message. Checked first so watchover text inside a
    # snapshot payload does not get misclassified as a watcher call.
    has_snapshot_marker = "[conversation snapshot]" in text
    has_summarize_cue = False
    for msg in req.messages:
        if msg.role == "system" and msg.content and "summarize" in msg.content.lower():
            has_summarize_cue = True
            break
    if has_snapshot_marker and has_summarize_cue:
        return "snapshot"

    if "[watchover check]" in text:
        return "watcher"
    if "[watchover context]" in text:
        return "watcher"

    for msg in req.messages:
        if msg.role == "system" and msg.content:
            sys_lower = msg.content.lower()
            if (
                "security-profile compiler" in sys_lower
                or "watcher context builder" in sys_lower
                or "build the watchover context" in sys_lower
            ):
                return "builder"

    return "agent"


def _agent_tool_call(name: str, args_json: str, call_id: str = "call_mock_001") -> dict:
    """Build a single OpenAI tool_call object."""
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": args_json},
    }


def _has_watchover_markers(req: ChatCompletionRequest) -> bool:
    """Decide whether a request should be routed to the watchover handler.

    True when:
      - The request carries explicit watchover markers (watcher evaluator,
        context builder, or snapshot regeneration calls), OR
      - A scenario has been explicitly activated via ``POST /scenario`` and
        the request is an agent call (no markers but scenario expects agent
        responses).

    This keeps generic (non-watchover) tests working: when no scenario is
    active and there are no markers, requests fall through to the generic
    mock path.
    """
    call_type = _detect_call_type(req)
    if call_type in ("watcher", "builder", "snapshot"):
        return True
    # Agent calls route to the watchover handler only while a scenario is active
    return watchover_state.active


def _handle_watchover_request(req: ChatCompletionRequest) -> JSONResponse | StreamingResponse:
    """Route a detected watchover call to its scenario-driven response."""
    call_type = _detect_call_type(req)
    scenario = watchover_state.scenario

    # ---- Snapshot regeneration -------------------------------------------
    # Snapshot calls summarize recent conversation history into a fresh
    # guardrail payload. Always deterministic regardless of scenario.
    if call_type == "snapshot":
        content = (
            "Instance is performing kubectl operations on k3s cluster. "
            "Previous commands: get nodes, get pods. All read-only so far."
        )
        if req.stream:
            return StreamingResponse(
                stream_response(req.model, "", content, "", req),
                media_type="text/event-stream",
            )
        return build_watchover_response(content, None, req.model, "stop")

    # ---- Watcher evaluator ----------------------------------------------
    if call_type == "watcher":
        watchover_state.watcher_call_count += 1
        idx = watchover_state._watcher_call_index
        watchover_state._watcher_call_index += 1

        if scenario == "deny_then_correct":
            if idx == 0:
                content = (
                    "Deny: kubectl delete is a mutating operation. "
                    "Use kubectl get or kubectl describe instead."
                )
            else:
                content = "Allowed"
        elif scenario == "three_strikes":
            content = "Deny: destructive operation not permitted under watchover."
        elif scenario == "infra_error":
            if idx == 0:
                # First call fails so the fail-open path executes
                return JSONResponse(
                    status_code=500,
                    content={"error": "Mock infra error (watchover infra_error scenario)"},
                )
            content = "Allowed"
        else:
            # allow / builder_quality / unknown -> always allow
            content = "Allowed"

        if req.stream:
            return StreamingResponse(
                stream_response(req.model, "", content, "", req),
                media_type="text/event-stream",
            )
        return build_watchover_response(content, None, req.model, "stop")

    # ---- Watcher context builder ----------------------------------------
    if call_type == "builder":
        watchover_state.builder_call_count += 1
        content = BUILDER_GUARDRAILS

        if req.stream:
            return StreamingResponse(
                stream_response(req.model, "", content, "", req),
                media_type="text/event-stream",
            )
        return build_watchover_response(content, None, req.model, "stop")

    # ---- Agent -----------------------------------------------------------
    # fallthrough: agent call
    watchover_state.agent_call_count += 1
    idx = watchover_state._agent_call_index
    watchover_state._agent_call_index += 1

    if scenario == "deny_then_correct":
        if idx == 0:
            tc = _agent_tool_call(
                "bash", '{"command": "kubectl delete pod old-pod"}'
            )
        else:
            tc = _agent_tool_call("bash", '{"command": "kubectl get pods"}')
    elif scenario == "three_strikes":
        tc = _agent_tool_call(
            "bash", '{"command": "kubectl delete deployment prod-app"}'
        )
    else:
        # allow / infra_error / builder_quality / unknown -> safe command
        tc = _agent_tool_call("bash", '{"command": "kubectl top nodes"}')

    # Streaming tool_calls is complex and not needed for tests — return
    # non-streaming regardless of req.stream.
    return build_watchover_response(
        None, [tc], req.model, "tool_calls"
    )


def format_sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "request_count": state.request_count,
    }


@app.get("/stats")
async def stats():
    """Return server statistics."""
    return {
        "total_requests": state.request_count,
        "response_index": state.response_index,
        "available_responses": MOCK_CONTENT_RESPONSES,
        "available_thinking": MOCK_THINKING_RESPONSES,
        "watcher_call_count": watchover_state.watcher_call_count,
        "builder_call_count": watchover_state.builder_call_count,
        "agent_call_count": watchover_state.agent_call_count,
        "current_scenario": watchover_state.scenario,
    }


@app.post("/scenario")
async def set_scenario(req: Request):
    """Set the watchover test scenario and reset internal counters.

    Body: ``{"scenario": "allow" | "deny_then_correct" | "three_strikes" |
    "infra_error" | "builder_quality"}``
    """
    body = await req.json()
    scenario = body.get("scenario", "allow")
    old = watchover_state.set_scenario(scenario)
    return {"scenario": watchover_state.scenario, "previous": old}


@app.post("/v1/chat/completions")
async def chat_completions(req: ChatCompletionRequest):
    """
    OpenAI-compatible /v1/chat/completions endpoint.
    Supports both streaming and non-streaming responses.
    """
    state.increment()

    # ---- Watchover detection --------------------------------------------
    # If the request carries watchover markers (watcher evaluator, context
    # builder), delegate to the scenario-driven handler. Otherwise fall
    # through to the generic mock path so existing tests keep working.
    if _has_watchover_markers(req):
        return _handle_watchover_request(req)

    user_message = ""
    for msg in reversed(req.messages):
        if msg.role == "user":
            user_message = msg.content or ""
            break

    if req.mock_response:
        response_content = req.mock_response.replace("{user_message}", user_message)
        thinking_content = ""
    else:
        response_content, thinking_content = state.get_response()
        response_content = response_content.replace("{user_message}", user_message)

    if req.stream:
        return StreamingResponse(
            stream_response(req.model, user_message, response_content, thinking_content, req),
            media_type="text/event-stream",
        )
    else:
        return build_non_stream_response(req.model, user_message, response_content, thinking_content)


def build_non_stream_response(
    model: str, user_message: str, content: str, thinking: str
) -> JSONResponse:
    """Build a non-streaming chat completion response."""
    return JSONResponse(
        content={
            "id": f"mockchat-{int(time.time() * 1000)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": content,
                        "reasoning_content": thinking or None,
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": len(user_message.split()),
                "completion_tokens": len(content.split()) + len(thinking.split()) if thinking else len(content.split()),
                "total_tokens": len(user_message.split()) + len(content.split()) + (len(thinking.split()) if thinking else 0),
            },
        }
    )


def stream_response(
    model: str, user_message: str, content: str, thinking: str, req: ChatCompletionRequest
) -> Generator[str, None, None]:
    """Yield streaming response chunks (GLM-style with reasoning_content before content)."""
    content_chunk_size = max(1, len(content) // 5)
    content_chunks = []
    for i in range(0, len(content), content_chunk_size):
        content_chunks.append(content[i : i + content_chunk_size])

    # Split thinking into word-level chunks to mimic GLM streaming
    thinking_words = thinking.split() if thinking else []
    thinking_chunks = []
    current_thinking = ""
    for word in thinking_words:
        current_thinking += word + " "
        if len(current_thinking) >= 5 or word == thinking_words[-1]:
            thinking_chunks.append(current_thinking)
            current_thinking = ""

    # Send first chunk with role
    yield format_sse(
        "chunk",
        {
            "id": f"mockchat-{int(time.time() * 1000)}",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant"},
                    "finish_reason": None,
                }
            ],
        },
    )

    # Send reasoning_content chunks (like GLM extended thinking)
    for tc in thinking_chunks:
        time.sleep(req.stream_delay)
        yield format_sse(
            "chunk",
            {
                "id": f"mockchat-{int(time.time() * 1000)}",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"reasoning_content": tc},
                        "finish_reason": None,
                    }
                ],
            },
        )

    # Send content chunks
    for cc in content_chunks:
        time.sleep(req.stream_delay)
        yield format_sse(
            "chunk",
            {
                "id": f"mockchat-{int(time.time() * 1000)}",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": cc},
                        "finish_reason": None,
                    }
                ],
            },
        )

    # Send final chunk with finish_reason
    yield format_sse(
        "chunk",
        {
            "id": f"mockchat-{int(time.time() * 1000)}",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": len(user_message.split()),
                "completion_tokens": len(content.split()) + len(thinking.split()) if thinking else len(content.split()),
                "total_tokens": len(user_message.split()) + len(content.split()) + (len(thinking.split()) if thinking else 0),
            },
        },
    )


@app.post("/v1/completions")
async def completions(req: Request):
    """Simple /v1/completions endpoint for backward compatibility."""
    body = await req.json()
    prompt = body.get("prompt", "")
    state.increment()

    return JSONResponse(
        content={
            "id": f"mockcomp-{int(time.time() * 1000)}",
            "object": "text_completion",
            "created": int(time.time()),
            "model": body.get("model", "mock-model"),
            "choices": [
                {
                    "text": f"Mock completion for: {prompt[:50]}...",
                    "index": 0,
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": len(prompt.split()),
                "completion_tokens": 10,
                "total_tokens": len(prompt.split()) + 10,
            },
        }
    )


def main():
    parser = argparse.ArgumentParser(description="Mock LLM Server")
    parser.add_argument(
        "--host", default=os.getenv("MOCK_HOST", "0.0.0.0"), help="Host to bind to"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("MOCK_PORT", "4124")),
        help="Port to bind to",
    )
    args = parser.parse_args()

    print(f"Starting Mock LLM Server on {args.host}:{args.port}")
    print(f"OpenAI-compatible endpoint: http://{args.host}:{args.port}/v1/chat/completions")
    print(f"Health check: http://{args.host}:{args.port}/health")

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
