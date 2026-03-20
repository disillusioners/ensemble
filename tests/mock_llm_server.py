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
from typing import Generator
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
    content: str


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
    }


@app.post("/v1/chat/completions")
async def chat_completions(req: ChatCompletionRequest):
    """
    OpenAI-compatible /v1/chat/completions endpoint.
    Supports both streaming and non-streaming responses.
    """
    state.increment()

    user_message = ""
    for msg in reversed(req.messages):
        if msg.role == "user":
            user_message = msg.content
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
