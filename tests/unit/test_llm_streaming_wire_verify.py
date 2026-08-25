"""Wire-level SSE verification for LLM streaming activation (CF 524 fix).

Supplement to ``tests/unit/test_llm_streaming_activation.py`` (the config /
payload-dict suite). That suite stops at the config level — it never sends
bytes through an HTTP transport, never feeds SSE-shaped bytes through the
streaming decode path, and never runs ``.invoke()`` end-to-end. This file
closes that gap with REAL behavior: the REAL ``ThinkingChatOpenAI`` and the
REAL ``daemon.graph.clean_llm_config`` chokepoint, talking to an in-process
``httpx.MockTransport`` (no network, no ports). The transport handler captures
the serialized JSON POST body — the true wire boundary — and returns
REAL-SHAPED OpenAI-compatible responses (SSE ``data:`` chunk streams for
``stream: true`` requests, single-JSON ChatCompletion otherwise).

Verification map (each V-item is at least one test):

* V1 (G1+G7) — ``stream: true`` in the outgoing POST body for the three
  production flows: (a) plain agent-style invoke, (b) thinking/reasoning
  model, (c) tool-calling flow.
* V2 (G2)   — semantic equivalence: the same logical completion expressed as
  an SSE chunk stream vs a single JSON body must aggregate into the same
  final AIMessage (content / tool_calls / usage_metadata / reasoning).
* V3 (G4)   — tool-call delta aggregation: arguments split across >=3 chunks
  must reassemble into the exact parsed args dict.
* V4 (G5+G6)— operator opt-out (``OPENAI_STREAMING=false``) through BOTH
  startup wiring paths (``daemon/__main__.py::main`` and
  ``daemon/api.py::lifespan``) down to the class var, the chokepoint and the
  wire payload.
* V5 (G9)   — env coercion edge cases on ``LLMConfig.streaming``.
* V6        — clobber-safety at the wire level: explicit ``streaming`` in the
  caller config always wins over the class var (both directions).

Wire shape (verified live against llm.ensem.dev, mirrored here)::

    data: {"id":"chatcmpl-...","object":"chat.completion.chunk","created":...,
           "model":"...","choices":[{"index":0,"delta":{"reasoning_content":"..."},
           "finish_reason":null}]}

    data: {"choices":[{"index":0,"delta":{"content":"..."}}]}
    data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"...",
           "type":"function","function":{"name":"...","arguments":"..."}}]}}]}
    data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{...}}
    data: [DONE]

with ``Content-Type: text/event-stream``.

⚠ FINDING W-1 (found by this suite — see ``_invoke_or_w1``)
--------------------------------------------------------------------------------
``daemon/graph.py::ThinkingChatOpenAI._convert_chunk_to_generation_chunk``
uses the name ``ChatGenerationChunk`` (graph.py:2041 and :2079) but never
imports it. The streaming decode path therefore raises
``NameError: name 'ChatGenerationChunk' is not defined`` on EVERY streamed
response, AFTER the request has already gone out on the wire. This was latent
until the CF-125s fix defaulted ``streaming=True`` for every construction
site (previously the streaming decode path was never entered in production).
The config-level activation suite cannot catch it because it never feeds
chunks through ``_stream``. Tests below use ``_invoke_or_w1`` so that:

* the wire-capture assertions still run and PASS (the POST body — the thing
  under test — was genuinely transmitted with the right flag), and
* the test then FAILS with an explicit ``W-1`` marker so the decode defect
  cannot hide behind a green suite.

Once the one-line import fix lands in production code, every ``W-1`` guard
becomes a no-op and the semantic assertions behind it engage automatically.

⚠ FINDING W-2 (V5) — FIXED by S1 (``_coerce_streaming_empty_to_default``)
--------------------------------------------------------------------------------
``OPENAI_STREAMING=""`` (empty string) and a bare YAML ``streaming:`` (None)
used to crash daemon boot with a pydantic ``ValidationError`` (bool parsing
of ''). S1 adds a ``field_validator("streaming", mode="before")`` that
coerces both to ``True`` (the default), so ``.env`` lines with empty values
and stripped YAML keys now fall back to the streaming-on default instead of
failing the daemon at config load. The test below documents the NEW
behavior (see ``test_v5_empty_string_coerces_to_true``).
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from langchain_core.messages import HumanMessage

PROXY_BASE_URL = "https://llm.test.local/v1"

# Shared logical completion used by V2 (semantic equivalence) — the SSE
# fixture and the JSON fixture below must express EXACTLY this completion.
V2_CONTENT = "Let me check."
V2_REASONING = "User greeted; I should call the weather tool."
V2_TOOL_NAME = "get_weather"
V2_TOOL_ARGS = {"city": "Hanoi", "unit": "c"}
V2_TOOL_ID = "call_w2"
V2_USAGE = {"prompt_tokens": 12, "completion_tokens": 34, "total_tokens": 46}

WEATHER_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get the current weather for a city.",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string"},
                "unit": {"type": "string", "enum": ["c", "f"]},
            },
            "required": ["city"],
        },
    },
}


# ---------------------------------------------------------------------------
# SSE wire-shape helpers (OpenAI-compatible, verified against llm.ensem.dev)
# ---------------------------------------------------------------------------


def _chunk(delta: dict, finish_reason: str | None = None, usage: dict | None = None,
           model: str = "test-model", cid: str = "chatcmpl-wire-1") -> dict:
    """One chat.completion.chunk in the raw dict form backends put on the wire."""
    c = {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": 1735689600,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    if usage is not None:
        c["usage"] = usage
    return c


def _sse_bytes(chunks: list[dict]) -> bytes:
    """Serialize chunks into the exact SSE wire format: ``data: {...}\\n\\n``
    per event, terminated by ``data: [DONE]\\n\\n``."""
    out = []
    for c in chunks:
        out.append("data: " + json.dumps(c, separators=(",", ":")) + "\n\n")
    out.append("data: [DONE]\n\n")
    return "".join(out).encode("utf-8")


# --- SSE fixtures for each flow ---------------------------------------------

# V1a: plain content, split across 3 chunks.
SSE_PLAIN_CONTENT = [
    _chunk({"role": "assistant"}),
    _chunk({"content": "Hello"}),
    _chunk({"content": " wor"}),
    _chunk({"content": "ld"}),
    _chunk({}, finish_reason="stop", usage=V2_USAGE),
]

# V1b: thinking model — reasoning_content deltas then content deltas.
SSE_REASONING = [
    _chunk({"role": "assistant", "reasoning_content": "User greeted; I should "}),
    _chunk({"reasoning_content": "call the weather tool."}),
    _chunk({"content": "Hello"}),
    _chunk({"content": " world"}),
    _chunk({}, finish_reason="stop", usage=V2_USAGE),
]

# V1c/V3: tool-call deltas, arguments split across >=3 chunks.
_TOOL_ARGS_JSON = json.dumps(V2_TOOL_ARGS, separators=(",", ":"))  # {"city":"Hanoi","unit":"c"}
# split points: '{"city":' + '"Hanoi",' + '"unit":"c"}'  (3 fragments)
_TOOL_FRAGMENTS = ['{"city":', '"Hanoi",', '"unit":"c"}']
SSE_TOOL_CALL = [
    _chunk({"role": "assistant"}),
    _chunk({"tool_calls": [{"index": 0, "id": V2_TOOL_ID, "type": "function",
                            "function": {"name": V2_TOOL_NAME, "arguments": _TOOL_FRAGMENTS[0]}}]}),
    _chunk({"tool_calls": [{"index": 0, "function": {"arguments": _TOOL_FRAGMENTS[1]}}]}),
    _chunk({"tool_calls": [{"index": 0, "function": {"arguments": _TOOL_FRAGMENTS[2]}}]}),
    _chunk({}, finish_reason="stop", usage=V2_USAGE),
]

# V2 streaming twin: reasoning + content + tool call, all in one stream.
SSE_V2_FULL = [
    _chunk({"role": "assistant", "reasoning_content": "User greeted; I should "}),
    _chunk({"reasoning_content": "call the weather tool."}),
    _chunk({"content": "Let me "}),
    _chunk({"content": "check."}),
    _chunk({"tool_calls": [{"index": 0, "id": V2_TOOL_ID, "type": "function",
                            "function": {"name": V2_TOOL_NAME, "arguments": _TOOL_FRAGMENTS[0]}}]}),
    _chunk({"tool_calls": [{"index": 0, "function": {"arguments": _TOOL_FRAGMENTS[1]}}]}),
    _chunk({"tool_calls": [{"index": 0, "function": {"arguments": _TOOL_FRAGMENTS[2]}}]}),
    _chunk({}, finish_reason="stop", usage=V2_USAGE),
]


def _non_streaming_completion(model: str = "test-model", content: str = "Hello world",
                              reasoning: str | None = None,
                              tool_calls: list[dict] | None = None,
                              usage: dict = V2_USAGE) -> dict:
    """A single-JSON chat.completion expressing the same logical completion."""
    message: dict = {"role": "assistant", "content": content}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {
        "id": "chatcmpl-wire-1",
        "object": "chat.completion",
        "created": 1735689600,
        "model": model,
        "choices": [{
            "index": 0,
            "message": message,
            "finish_reason": "tool_calls" if tool_calls else "stop",
        }],
        "usage": usage,
    }


# ---------------------------------------------------------------------------
# WireTap — capture the serialized POST body at the transport boundary
# ---------------------------------------------------------------------------


class WireTap:
    """httpx transport handler that records every serialized request body and
    answers with REAL-SHAPED responses: an SSE chunk stream when the request
    body carries ``stream: true`` (what a real OpenAI-compatible backend does
    for a streaming request), a single-JSON ChatCompletion otherwise."""

    def __init__(self, sse_chunks: list[dict], json_payload: dict | None = None,
                 model: str = "test-model"):
        self.bodies: list[dict] = []
        self._sse = sse_chunks
        self._json = json_payload if json_payload is not None else _non_streaming_completion(
            model=model,
            content="Hello world",
            reasoning="Let me think about it.",
            usage=V2_USAGE,
        )
        self.model = model

    @property
    def last(self) -> dict:
        return self.bodies[-1]

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        if body.get("stream"):
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=_sse_bytes(self._sse),
                request=request,
            )
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            content=json.dumps(self._json).encode("utf-8"),
            request=request,
        )


def _make_llm(tap: WireTap, model: str = "test-model", streaming: bool | None = None):
    """Construct a REAL ThinkingChatOpenAI through the REAL clean_llm_config
    chokepoint, with the mock transport injected as http_client. Returns
    ``(llm, http_client)`` — caller must close the client."""
    from daemon.graph import ThinkingChatOpenAI, clean_llm_config

    client = httpx.Client(
        transport=httpx.MockTransport(tap.handler), base_url=PROXY_BASE_URL
    )
    cfg: dict = {
        "model": model,
        "api_key": "test-key",
        "base_url": PROXY_BASE_URL,
        "http_client": client,
    }
    if streaming is not None:
        cfg["streaming"] = streaming
    cleaned = clean_llm_config(cfg)  # REAL chokepoint under test
    return ThinkingChatOpenAI(**cleaned), client


def _invoke_or_w1(llm, messages):
    """``.invoke()`` with the W-1 decode defect translated into a loud marker.

    See module docstring (FINDING W-1): the streaming decode path currently
    raises ``NameError: name 'ChatGenerationChunk' is not defined`` from
    daemon/graph.py AFTER the request has been transmitted. This helper
    returns ``(message, None)`` on success and ``(None, exc)`` when the W-1
    NameError fires, so tests can still assert the WIRE evidence (the body
    was captured by the transport before the crash) and then fail loudly.
    """
    try:
        return llm.invoke(messages), None
    except NameError as e:
        if "ChatGenerationChunk" in str(e):
            return None, e
        raise


def _fail_w1(exc: Exception) -> None:
    pytest.fail(
        "W-1 (production defect found by this suite): streaming decode crashed "
        f"with NameError after the wire request was sent: {exc}. Fix: import "
        "ChatGenerationChunk in daemon/graph.py (from langchain_core.outputs "
        "import ChatGenerationChunk). Wire-flag assertions above this line "
        "already passed — the defect is purely in the response-decode path."
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _restore_default_streaming():
    """Protect the class var every other suite depends on (default True)."""
    from daemon.graph import ThinkingChatOpenAI

    original = ThinkingChatOpenAI.default_streaming
    yield
    ThinkingChatOpenAI.default_streaming = original


@pytest.fixture(autouse=True)
def _clean_streaming_env(monkeypatch):
    """No test in this file may inherit an ambient OPENAI_STREAMING value
    (V4/V5 manage it explicitly)."""
    monkeypatch.delenv("OPENAI_STREAMING", raising=False)


# ---------------------------------------------------------------------------
# V1 (G1+G7) — wire flag in the outgoing POST body, three flows
# ---------------------------------------------------------------------------


class TestV1WireFlag:
    def test_v1a_plain_invoke_streams_on_wire(self):
        """Plain agent-style call: the serialized POST body the transport
        receives must carry ``stream: true`` (CF-125s fix), and the SSE round
        trip must aggregate into the expected AIMessage."""
        tap = WireTap(SSE_PLAIN_CONTENT, model="test-model")
        llm, client = _make_llm(tap, model="test-model")
        try:
            msg, w1 = _invoke_or_w1(llm, [HumanMessage(content="hi")])

            # Wire evidence — the POST body the transport actually received.
            assert len(tap.bodies) == 1
            assert tap.last["stream"] is True, (
                "outgoing POST body must carry stream:true for a default "
                "clean_llm_config-constructed LLM (Cloudflare ~125s 524 fix)"
            )
            assert tap.last["model"] == "test-model"
            # W1 fix: clean_llm_config now injects ``stream_usage=True`` for any
            # default-constructed LLM (unless the caller passes it explicitly),
            # so langchain-openai serializes the wire-level ``stream_options``
            # block requesting the backend to emit a final usage chunk.
            # Without this injection, OpenAI-spec-compliant backends omit
            # ``usage`` from the SSE stream entirely and ``usage_metadata``
            # comes back as None — silent token-count loss. Assert the exact
            # shape langchain-openai emits (``stream_options.include_usage``
            # must be True).
            assert tap.last.get("stream_options") == {"include_usage": True}, (
                "default clean_llm_config must inject stream_usage=True so the "
                "wire carries stream_options: {include_usage: True} (W1 fix); "
                "backends that omit usage on the SSE stream would otherwise "
                "leave usage_metadata=None end-to-end"
            )

            if w1 is not None:
                _fail_w1(w1)

            assert msg.content == "Hello world"
            assert msg.usage_metadata is not None
        finally:
            client.close()

    def test_v1b_reasoning_model_streams_and_captures_reasoning(self):
        """Thinking/reasoning model: ``stream: true`` on the wire AND the
        reasoning_content deltas must survive aggregation into
        ``additional_kwargs`` (ThinkingChatOpenAI merges them)."""
        tap = WireTap(SSE_REASONING)
        llm, client = _make_llm(tap, model="glm-5.3")
        try:
            msg, w1 = _invoke_or_w1(llm, [HumanMessage(content="hi")])

            assert tap.last["stream"] is True
            assert tap.last["model"] == "glm-5.3"

            if w1 is not None:
                _fail_w1(w1)

            assert msg.content == "Hello world"
            reasoning = msg.additional_kwargs.get("reasoning_content")
            assert reasoning == "User greeted; I should call the weather tool.", (
                "streaming reasoning_content deltas must be concatenated into "
                f"additional_kwargs (observed: {reasoning!r})"
            )
        finally:
            client.close()

    def test_v1c_tool_calling_flow_streams_on_wire(self):
        """Tool-calling flow (the agent-node hot path): ``stream: true`` in
        the POST body, tools serialized alongside, tool-call deltas decoded
        into ``.tool_calls``."""
        tap = WireTap(SSE_TOOL_CALL)
        llm, client = _make_llm(tap, model="test-model")
        try:
            bound = llm.bind_tools([WEATHER_TOOL_SCHEMA])
            msg, w1 = _invoke_or_w1(bound, [HumanMessage(content="weather in Hanoi?")])

            assert tap.last["stream"] is True, (
                "tool-calling flow must go out streaming too (agent-node hot path)"
            )
            assert tap.last.get("tools"), "bound tool schema must be serialized"
            assert tap.last["tools"][0]["function"]["name"] == "get_weather"

            if w1 is not None:
                _fail_w1(w1)

            assert msg.tool_calls, "tool-call deltas must decode into .tool_calls"
        finally:
            client.close()


# ---------------------------------------------------------------------------
# V2 (G2) — semantic equivalence: streaming vs non-streaming
# ---------------------------------------------------------------------------


class TestV2SemanticEquivalence:
    def test_v2_same_completion_streaming_vs_non_streaming(self):
        """The user-visible promise of the CF fix: a streaming round trip
        aggregates SSE chunks into the SAME final AIMessage a non-streaming
        call produces for the identical logical completion — content,
        tool_calls, usage_metadata and reasoning_content all preserved."""
        ns_tap = WireTap(
            [],
            json_payload=_non_streaming_completion(
                content=V2_CONTENT,
                reasoning=V2_REASONING,
                tool_calls=[{
                    "id": V2_TOOL_ID,
                    "type": "function",
                    "function": {"name": V2_TOOL_NAME,
                                 "arguments": json.dumps(V2_TOOL_ARGS)},
                }],
                usage=V2_USAGE,
            ),
        )
        st_tap = WireTap(SSE_V2_FULL)

        ns_llm, ns_client = _make_llm(ns_tap, streaming=False)
        st_llm, st_client = _make_llm(st_tap, streaming=True)
        try:
            ns_msg = ns_llm.invoke([HumanMessage(content="hi")])  # non-streaming path is healthy
            assert ns_tap.last["stream"] is False

            st_msg, w1 = _invoke_or_w1(st_llm, [HumanMessage(content="hi")])
            assert st_tap.last["stream"] is True

            if w1 is not None:
                _fail_w1(w1)

            # --- content ---
            assert st_msg.content == ns_msg.content == V2_CONTENT, (
                f"streaming content {st_msg.content!r} != non-streaming {ns_msg.content!r}"
            )
            # --- tool_calls ---
            expected_tc = [{"name": V2_TOOL_NAME, "args": V2_TOOL_ARGS,
                            "id": V2_TOOL_ID, "type": "tool_call"}]
            assert st_msg.tool_calls == expected_tc, (
                f"streaming tool_calls {st_msg.tool_calls!r} != expected {expected_tc!r}"
            )
            assert st_msg.tool_calls == ns_msg.tool_calls
            # --- usage_metadata: both paths must provide it, and equal ---
            if st_msg.usage_metadata != ns_msg.usage_metadata:
                pytest.fail(
                    "[V2-FINDING] usage_metadata differs across paths — "
                    f"streaming={st_msg.usage_metadata!r} "
                    f"non-streaming={ns_msg.usage_metadata!r}. A semantic "
                    "difference here is exactly the regression this suite exists to catch."
                )
            assert ns_msg.usage_metadata is not None
            # --- reasoning_content ---
            assert st_msg.additional_kwargs.get("reasoning_content") == V2_REASONING
            assert (st_msg.additional_kwargs.get("reasoning_content")
                    == ns_msg.additional_kwargs.get("reasoning_content"))
        finally:
            ns_client.close()
            st_client.close()


# ---------------------------------------------------------------------------
# V3 (G4) — tool-call delta aggregation
# ---------------------------------------------------------------------------


class TestV3ToolCallAggregation:
    def test_v3_tool_args_split_across_three_chunks_aggregate_exactly(self):
        """One tool call whose ``arguments`` JSON string arrives split across
        3 chunks must reassemble into the exact parsed args dict."""
        tap = WireTap(SSE_TOOL_CALL)
        llm, client = _make_llm(tap)
        try:
            msg, w1 = _invoke_or_w1(llm, [HumanMessage(content="weather?")])
            assert tap.last["stream"] is True
            if w1 is not None:
                _fail_w1(w1)
            assert msg.tool_calls == [{
                "name": "get_weather",
                "args": {"city": "Hanoi", "unit": "c"},
                "id": V2_TOOL_ID,
                "type": "tool_call",
            }], f"aggregated tool_calls mismatch: {msg.tool_calls!r}"
            # The finish chunk carried usage — verify it survived too.
            assert msg.usage_metadata is not None
            assert msg.usage_metadata["total_tokens"] == 46
        finally:
            client.close()


# ---------------------------------------------------------------------------
# V4 (G5+G6) — operator opt-out through both startup wiring paths
# ---------------------------------------------------------------------------

# Honest record of what each V4 test exercises (surfaced in assert + report):
V4_PATH1_LEVEL = (
    "full main() body executed (run_preflight=False) with load_config "
    "replaced by an in-memory Config(llm=LLMConfig()) built under "
    "OPENAI_STREAMING=false (real pydantic env coercion), uvicorn.run "
    "patched to a recorder (no server), DB preflight skipped"
)
V4_PATH2_LEVEL = (
    "real lifespan(app) executed from entry through the wiring block and the "
    "post-wiring warn step, halted at the first post-wiring external "
    "dependency (RAG auto-test) via a sentinel; load_config stubbed the same "
    "way as Path 1; ENSEMBLE_DATA_DIR pointed at a tmp dir"
)


class _StopLifespan(Exception):
    """Sentinel raised at the first post-wiring external dependency."""


class TestV4StartupOptOutPaths:
    def _assert_downstream(self):
        """After a wiring path ran: class var False → chokepoint injects
        False → wire payload dict carries stream False."""
        from daemon.graph import ThinkingChatOpenAI, clean_llm_config

        assert ThinkingChatOpenAI.default_streaming is False, (
            "startup wiring must set ThinkingChatOpenAI.default_streaming=False "
            "when OPENAI_STREAMING=false"
        )
        assert clean_llm_config({})["streaming"] is False
        llm = ThinkingChatOpenAI(**clean_llm_config({"model": "m", "api_key": "k"}))
        payload = llm._get_request_payload([HumanMessage(content="hi")])
        assert payload["stream"] is False

    def test_v4_path1_daemon_main_wiring(self, monkeypatch):
        """daemon/__main__.py — the ``python -m daemon`` entry point wiring."""
        from daemon import __main__ as du
        from daemon.config import Config, LLMConfig

        monkeypatch.setenv("OPENAI_STREAMING", "false")
        monkeypatch.setattr(du, "load_config", lambda: Config(llm=LLMConfig()))
        uvicorn_calls: list = []
        monkeypatch.setattr(
            du.uvicorn, "run", lambda *a, **k: uvicorn_calls.append((a, k))
        )
        assert V4_PATH1_LEVEL  # level recorded verbatim for the report
        du.main(run_preflight=False)
        assert uvicorn_calls, "main() must reach the uvicorn.run call (patched)"
        self._assert_downstream()

    def test_v4_path2_daemon_api_lifespan_wiring(self, monkeypatch, tmp_path):
        """daemon/api.py — the ``uvicorn daemon.api:app`` entry point wiring
        (lifespan startup)."""
        import daemon.api as dapi
        import daemon.rag as drag
        from daemon.config import Config, LLMConfig

        monkeypatch.setenv("OPENAI_STREAMING", "false")
        monkeypatch.setenv("ENSEMBLE_DATA_DIR", str(tmp_path))
        monkeypatch.setattr("daemon.config.load_config",
                            lambda: Config(llm=LLMConfig()))

        async def _halt_at_rag():
            raise _StopLifespan("halted at RAG auto-test (post-wiring)")

        monkeypatch.setattr(drag, "auto_test_rag", _halt_at_rag)
        app = SimpleNamespace(state=SimpleNamespace())
        assert V4_PATH2_LEVEL  # level recorded verbatim for the report

        # lifespan is an @asynccontextmanager: entering runs the startup body
        # (through the wiring block) up to the first yield / exception.
        async def _enter_lifespan():
            async with dapi.lifespan(app):
                pass

        with pytest.raises(_StopLifespan):
            asyncio.run(_enter_lifespan())

        self._assert_downstream()


# ---------------------------------------------------------------------------
# V5 (G9) — env coercion on LLMConfig.streaming
# ---------------------------------------------------------------------------


class TestV5EnvCoercion:
    @pytest.mark.parametrize("raw,expected", [
        ("false", False),
        ("0", False),
        ("no", False),
        ("true", True),
        ("1", True),
    ])
    def test_v5_coercion_table(self, monkeypatch, raw, expected):
        from daemon.config import LLMConfig

        monkeypatch.setenv("OPENAI_STREAMING", raw)
        assert LLMConfig().streaming is expected

    def test_v5_unset_defaults_true(self, monkeypatch):
        from daemon.config import LLMConfig

        monkeypatch.delenv("OPENAI_STREAMING", raising=False)
        assert LLMConfig().streaming is True

    def test_v5_empty_string_coerces_to_true(self, monkeypatch):
        """S1 fix — ``OPENAI_STREAMING=""`` (empty string) now coerces to
        the default (``True``) instead of raising ``ValidationError``.

        Pre-S1, an empty ``OPENAI_STREAMING=`` line in ``.env`` (a common
        operator foot-gun: paste-the-key-then-fill-the-value-later) crashed
        daemon boot at config load. ``LLMConfig._coerce_streaming_empty_to_default``
        (``daemon/config.py``, ``field_validator("streaming", mode="before")``)
        short-circuits empty / whitespace strings and ``None`` (YAML null
        from a stripped ``streaming:`` key) to ``True``. Real bools and
        pydantic-parseable strings (``"true"`` / ``"false"`` / ``"1"`` /
        ``"0"``) pass through untouched.
        """
        from daemon.config import LLMConfig

        monkeypatch.setenv("OPENAI_STREAMING", "")
        cfg = LLMConfig()
        assert cfg.streaming is True, (
            "S1 validator must coerce OPENAI_STREAMING='' to the default "
            "(True); the .env-empty foot-gun must no longer crash daemon boot"
        )

    def test_v5_none_yaml_key_coerces_to_true(self):
        """S1 also covers the YAML-null case: a stripped ``streaming:`` key
        arrives at the validator as ``None`` (pydantic-settings default for a
        missing key). It must coerce to ``True`` rather than fail bool parsing
        on the subsequent path. Constructed directly because ``monkeypatch``
        cannot synthesize a YAML-key-None from an env var."""
        from daemon.config import LLMConfig

        cfg = LLMConfig.model_validate({"streaming": None})
        assert cfg.streaming is True, (
            "S1 validator must coerce streaming=None (YAML null) to the default"
        )


# ---------------------------------------------------------------------------
# V6 — clobber-safety at the wire level
# ---------------------------------------------------------------------------


class TestV6ClobberSafety:
    def test_v6a_explicit_streaming_false_not_clobbered_on_wire(self):
        """Caller explicitly passes streaming=False through clean_llm_config
        → the captured POST body must say ``stream: false`` (helper must not
        overwrite caller intent with the class var). Non-streaming branch,
        full round trip (this decode path is healthy)."""
        tap = WireTap([], json_payload=_non_streaming_completion(content="Hello world"))
        llm, client = _make_llm(tap, streaming=False)
        try:
            msg = llm.invoke([HumanMessage(content="hi")])
            assert tap.last["stream"] is False, (
                "explicit streaming=False must reach the wire verbatim"
            )
            assert msg.content == "Hello world"
        finally:
            client.close()

    def test_v6b_explicit_streaming_true_wins_over_class_var_false(self, monkeypatch):
        """Class var set False (as if OPENAI_STREAMING=false had been applied
        at startup) but the caller explicitly passes streaming=True → the
        wire must stay ``stream: true`` (explicit config wins over class var,
        both directions of clobber-safety)."""
        from daemon.graph import ThinkingChatOpenAI, clean_llm_config

        monkeypatch.setattr(ThinkingChatOpenAI, "default_streaming", False)
        tap = WireTap(SSE_PLAIN_CONTENT)
        llm, client = _make_llm(tap, streaming=True)
        try:
            # Chokepoint level: explicit value preserved.
            cfg = clean_llm_config({"model": "test-model", "api_key": "k",
                                    "streaming": True})
            assert cfg["streaming"] is True
            # Payload-dict level.
            payload = llm._get_request_payload([HumanMessage(content="hi")])
            assert payload["stream"] is True

            # Wire level — the POST body the transport received.
            msg, w1 = _invoke_or_w1(llm, [HumanMessage(content="hi")])
            assert tap.last["stream"] is True, (
                "explicit streaming=True must win over class var False on the wire"
            )
            if w1 is not None:
                _fail_w1(w1)
            assert msg.content == "Hello world"
        finally:
            client.close()
