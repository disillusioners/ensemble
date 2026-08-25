"""Tests for outbound LLM streaming activation (Cloudflare ~125s 524 fix).

Background
----------
Ensemble's LLM calls go out through a Cloudflare-proxied base_url
(llm.ensem.dev / llm.daoduc.org, CF anycast). CF drops connections
with zero response bytes for ~100-125s and returns 524 on every
generation longer than that window. The fix is to send
``stream: True`` on the wire so chunked bytes flow back through CF
before its read timeout can kill the connection. LightRAG survives the
same CF wall via the same mechanism.

This file verifies the activation is wired end-to-end:

* ``daemon.graph.clean_llm_config`` — the single choke point for every
  LangChain ``ThinkingChatOpenAI`` construction site — injects the
  streaming default (CF-125s fix).
* The injected flag reaches the wire payload (BaseChatOpenAI
  serializes ``"stream": self.streaming``).
* The HA facade (``wrap_langchain_failover``) still composes correctly
  on top of a streaming-enabled client.
* The proxy headers (``default_headers``) survive ``clean_llm_config``
  unchanged at all construction sites.
* ``LLMConfig.streaming`` is exposed with the right default and env
  hook (precedent: ``OPENAI_REASONING_ECHO_DISABLED_MODELS``).

Why this matters
----------------
The non-streaming path sends a single silent POST that produces zero
response bytes until the model finishes generating. Any generation
longer than ~125s gets killed by Cloudflare's anycast read timeout with
a 524. Streaming keeps bytes flowing — every token is a heartbeat —
so the connection survives arbitrary generation lengths. LangChain's
``invoke()`` aggregates the chunks back into the same ``AIMessage``
(content / tool_calls / usage / reasoning_content all preserved), so
callers see identical final results.
"""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage


# ---------------------------------------------------------------------------
# clean_llm_config — streaming default injection
# ---------------------------------------------------------------------------


class TestCleanLlmConfigStreamingDefault:
    """``clean_llm_config`` is the single choke point for every LangChain
    ``ThinkingChatOpenAI`` construction site. The CF-125s fix lives here:
    callers that don't opt in/out get ``streaming=True`` automatically."""

    def test_injects_streaming_true_when_absent(self):
        """Default path: caller did not specify streaming — the helper
        injects True so the wire sees ``stream: True``."""
        from daemon.graph import clean_llm_config

        cleaned = clean_llm_config({"model": "gpt-4o", "api_key": "k"})
        assert cleaned["streaming"] is True, (
            "clean_llm_config must inject streaming=True when absent "
            "(Cloudflare ~125s 524 fix)"
        )

    def test_preserves_explicit_streaming_false(self):
        """Sites that genuinely need non-streaming (debugging, exotic
        backend) opt out by setting ``streaming=False`` explicitly — the
        helper must NOT silently override them."""
        from daemon.graph import clean_llm_config

        cleaned = clean_llm_config(
            {"model": "gpt-4o", "api_key": "k", "streaming": False}
        )
        assert cleaned["streaming"] is False, (
            "explicit streaming=False must survive clean_llm_config "
            "unchanged — do not silently coerce non-streaming callers"
        )

    def test_preserves_explicit_streaming_true(self):
        """Idempotent: explicit streaming=True passes through unchanged."""
        from daemon.graph import clean_llm_config

        cleaned = clean_llm_config(
            {"model": "gpt-4o", "api_key": "k", "streaming": True}
        )
        assert cleaned["streaming"] is True

    def test_still_strips_base_url_backup(self):
        """F1 lesson regression guard: ``base_url_backup`` MUST NOT leak
        into ChatOpenAI(**cfg) — clean_llm_config must continue to
        strip it (otherwise it crashes ``Completions.create()`` with an
        unexpected-kwarg TypeError)."""
        from daemon.graph import clean_llm_config

        cleaned = clean_llm_config(
            {"model": "g", "base_url_backup": "https://backup.example.com"}
        )
        assert "base_url_backup" not in cleaned

    def test_still_strips_model_vision(self):
        """``model_vision`` is a daemon-internal routing hint — must not
        leak into ChatOpenAI(**cfg)."""
        from daemon.graph import clean_llm_config

        cleaned = clean_llm_config({"model": "g", "model_vision": "gpt-4o"})
        assert "model_vision" not in cleaned

    def test_preserves_default_headers(self):
        """The hardcoded proxy header pair MUST survive clean_llm_config
        at every LangChain construction site — see
        graph.py:5191-5194 / compaction.py:593-595 / title_generation.py:96-99
        / keyword_extraction.py:369-371 / child_reports.py:758-761,1383-1386.
        Without these headers, the proxy rejects the request with 403.
        """
        from daemon.graph import clean_llm_config

        headers = {
            "x-proxy-app": "ensemble",
            "x-proxy-interleaved-thinking": "True",
        }
        cleaned = clean_llm_config(
            {"model": "g", "default_headers": headers}
        )
        assert cleaned["default_headers"] == headers, (
            "proxy headers must survive clean_llm_config unchanged "
            "(Cloudflare proxy auth relies on these)"
        )


# ---------------------------------------------------------------------------
# ThinkingChatOpenAI / wire payload — stream:true reaches the wire
# ---------------------------------------------------------------------------


class TestWirePayloadStreamFlag:
    """The wire payload (the JSON body LangChain sends to
    ``chat.completions``) must carry ``stream: True`` when the helper
    injects the streaming default. This is the actual CF-125s fix —
    without ``stream: True`` on the wire, Cloudflare kills the
    connection at ~125s."""

    def test_default_cleaned_config_serializes_stream_true(self):
        """A ThinkingChatOpenAI built from a default-cleaned config
        serializes ``stream: True`` on the wire."""
        from daemon.graph import ThinkingChatOpenAI, clean_llm_config

        cfg = clean_llm_config({"model": "test-model", "api_key": "k"})
        llm = ThinkingChatOpenAI(**cfg)

        # _get_request_payload is the parent-class method that builds the
        # wire body. It sets ``"stream": self.streaming`` at the wire
        # boundary (langchain-openai >=1.x, BaseChatOpenAI).
        payload = llm._get_request_payload([HumanMessage(content="hi")])
        assert payload["stream"] is True, (
            "wire payload must carry stream: True to survive the "
            "Cloudflare ~125s read timeout (524)"
        )

    def test_explicit_streaming_false_serializes_stream_false(self):
        """Opt-out path: an explicit streaming=False site still gets
        stream: False on the wire (verify the helper did NOT override
        the caller's intent)."""
        from daemon.graph import ThinkingChatOpenAI, clean_llm_config

        cfg = clean_llm_config(
            {"model": "test-model", "api_key": "k", "streaming": False}
        )
        llm = ThinkingChatOpenAI(**cfg)
        payload = llm._get_request_payload([HumanMessage(content="hi")])
        assert payload["stream"] is False

    def test_thinking_chat_openai_instance_has_streaming_true(self):
        """The instance-level flag — verified directly. This is what
        LangChain's ``_should_stream`` consults at ``invoke()`` time to
        decide whether to use the streaming API path internally."""
        from daemon.graph import ThinkingChatOpenAI, clean_llm_config

        cfg = clean_llm_config({"model": "test-model", "api_key": "k"})
        llm = ThinkingChatOpenAI(**cfg)
        assert llm.streaming is True


# ---------------------------------------------------------------------------
# LLMConfig — operator-tunable streaming knob
# ---------------------------------------------------------------------------


class TestLLMConfigStreamingField:
    """The operator-facing knob (env var + YAML) that the helper pulls
    from. Mirrors the ``OPENAI_REASONING_ECHO_DISABLED_MODELS``
    denylist-style precedent documented at the field."""

    def test_default_is_true(self):
        """Default streaming on (CF-125s fix): operators must explicitly
        opt out, not opt in."""
        os.environ.pop("OPENAI_STREAMING", None)

        from daemon.config import LLMConfig

        cfg = LLMConfig()
        assert cfg.streaming is True

    def test_env_var_false_overrides(self):
        """OPENAI_STREAMING=false flips it off (debugging / exotic
        backend). The base-url prefix scheme matches the env-prefix on
        LLMConfig (``SettingsConfigDict(env_prefix="OPENAI_")``)."""
        os.environ["OPENAI_STREAMING"] = "false"
        try:
            from daemon.config import LLMConfig

            cfg = LLMConfig()
            assert cfg.streaming is False
        finally:
            os.environ.pop("OPENAI_STREAMING", None)

    def test_env_var_empty_string_falls_back_to_true(self):
        """S1 fix: ``OPENAI_STREAMING=""`` (empty) — survives shell
        interpolation patterns like ``${OPENAI_STREAMING:-true}`` when
        operators paste a bare ``OPENAI_STREAMING=`` line into ``.env``
        — must coerce to the default (True) instead of crashing pydantic
        bool parsing at boot."""
        os.environ["OPENAI_STREAMING"] = ""
        try:
            from daemon.config import LLMConfig

            cfg = LLMConfig()
            assert cfg.streaming is True, (
                "OPENAI_STREAMING=\"\" must coerce to True (default) — "
                "see S1 (empty-guard validator mirrors the "
                "reasoning_echo_disabled_models precedent)"
            )
        finally:
            os.environ.pop("OPENAI_STREAMING", None)

    def test_yaml_null_streaming_falls_back_to_true(self):
        """S1 fix: ``streaming:`` (YAML null) — operators delete the
        value but leave the key — must coerce to the default (True)
        instead of None propagating into LLMConfig and crashing
        downstream pydantic bool checks."""
        from daemon.config import LLMConfig

        cfg = LLMConfig.model_validate({"streaming": None})
        assert cfg.streaming is True, (
            "YAML-null streaming must coerce to True (default) — see S1"
        )

    def test_explicit_streaming_false_passes_through(self):
        """S1 guard verification: explicit False must still pass through
        as False — the empty-guard validator must NOT silently override
        a deliberate opt-out (mirror of the streaming False case in
        ``TestCleanLlmConfigStreamingDefault``)."""
        from daemon.config import LLMConfig

        cfg = LLMConfig.model_validate({"streaming": False})
        assert cfg.streaming is False, (
            "explicit streaming=False must pass through the S1 validator "
            "unchanged — empty-guard is only for empty/None, not False"
        )


# ---------------------------------------------------------------------------
# End-to-end wiring — class var propagates to clean_llm_config + wire payload
# ---------------------------------------------------------------------------


class TestStreamingDefaultClassVarPropagation:
    """The operator-facing knob (LLMConfig.streaming → OPENAI_STREAMING env
    var) MUST reach the wire payload end-to-end through the class-var
    propagation that startup sites use (mirrors the
    ``reasoning_echo_disabled_models`` pattern). This is the wiring
    proof — without it, ``OPENAI_STREAMING=false`` would be a dead knob
    (the chokepoint would still hardcode ``True``)."""

    def test_class_var_false_injects_streaming_false_in_chokepoint(self):
        """Set the class var to False (the post-startup state when
        ``OPENAI_STREAMING=false`` was applied) — verify
        ``clean_llm_config`` injects False, not True."""
        from daemon.graph import ThinkingChatOpenAI, clean_llm_config

        original_value = ThinkingChatOpenAI.default_streaming
        ThinkingChatOpenAI.default_streaming = False
        try:
            cleaned = clean_llm_config({"model": "gpt-4o", "api_key": "k"})
            assert cleaned["streaming"] is False, (
                "clean_llm_config must inject ThinkingChatOpenAI.default_streaming, "
                "not a hardcoded True — OPENAI_STREAMING=false would otherwise "
                "be a dead knob"
            )
        finally:
            ThinkingChatOpenAI.default_streaming = original_value

    def test_class_var_false_reaches_wire_payload(self):
        """Wire-payload proof: the injected False must propagate to the
        JSON body LangChain sends to ``chat.completions``. Without
        ``stream: False`` on the wire, the backend would still get
        ``stream: True`` (default) and the CF-125s fix would not be
        disengageable."""
        from daemon.graph import ThinkingChatOpenAI, clean_llm_config

        original_value = ThinkingChatOpenAI.default_streaming
        ThinkingChatOpenAI.default_streaming = False
        try:
            cfg = clean_llm_config({"model": "test-model", "api_key": "k"})
            llm = ThinkingChatOpenAI(**cfg)
            payload = llm._get_request_payload([HumanMessage(content="hi")])
            assert payload["stream"] is False, (
                "wire payload must reflect ThinkingChatOpenAI.default_streaming "
                "so OPENAI_STREAMING=false reaches the backend"
            )
        finally:
            ThinkingChatOpenAI.default_streaming = original_value


# ---------------------------------------------------------------------------
# HA facade composition — wrap_langchain_failover still works
# ---------------------------------------------------------------------------


class TestFacadeWithStreamingClient:
    """The LangChain HA facade (``wrap_langchain_failover``) wraps
    ``invoke()`` with retry. It must compose correctly on top of a
    streaming-enabled client — the streaming happens INSIDE
    LangChain's ``invoke()`` (which routes via ``_stream`` + chunk
    aggregation), and the facade wraps that single ``invoke`` call as
    a unit. This test verifies the wrapper still builds and exposes
    ``invoke`` on a streaming client."""

    def test_wrap_langchain_failover_accepts_streaming_client(self):
        """A ChatOpenAI constructed with streaming=True must be
        acceptable to ``wrap_langchain_failover`` — the facade must
        NOT require a non-streaming client."""
        from langchain_openai import ChatOpenAI

        from daemon.services.llm_failover import (
            ChatFailoverBinding,
            wrap_langchain_failover,
        )

        # Use a clean streaming-enabled client — the production path
        # after the CF-125s fix is exactly this shape.
        llm = ChatOpenAI(
            api_key="test",
            base_url="https://primary.example/v1",
            model="gpt-4o",
            max_retries=0,
            streaming=True,
        )
        binding = wrap_langchain_failover(
            llm,
            {
                "base_url": "https://primary.example/v1",
                "base_url_backup": "https://backup.example/v1",
                "api_key": "test",
                "model": "gpt-4o",
            },
        )
        assert isinstance(binding, ChatFailoverBinding)
        # The facade surfaces the same HA knob surface regardless of
        # streaming — verify the streaming flag is still on the
        # underlying client (the facade does not proxy it).
        assert llm.streaming is True
        # invoke is exposed; calling it is the caller's job (the
        # wrapper retries on top of invoke, the streaming happens
        # inside invoke's _stream path).
        assert hasattr(binding, "invoke")

    def test_wrap_langchain_failover_invoke_routes_to_classified_client(self):
        """Smoke: binding.invoke() must delegate to the underlying
        client's invoke (LangChain's invoke aggregates chunks when
        streaming=True). Verify with a mock client that records the
        call."""
        from daemon.services.llm_failover import wrap_langchain_failover

        # Mock client with streaming=True flag (LangChain's _should_stream
        # would consult this in production; the wrapper doesn't care).
        mock_client = MagicMock()
        mock_client.streaming = True
        mock_client.invoke = MagicMock(
            return_value=AIMessage(content="aggregated")
        )

        binding = wrap_langchain_failover(
            mock_client,
            {
                "base_url": "https://primary.example/v1",
                "api_key": "test",
                "model": "gpt-4o",
            },
        )
        result = binding.invoke([HumanMessage(content="hi")])

        # The wrapper's invoke delegates to the client's invoke, which
        # in production returns the aggregated AIMessage (chunks joined
        # inside LangChain's invoke). Mock returns the same shape.
        assert result.content == "aggregated"
        mock_client.invoke.assert_called_once()


# ---------------------------------------------------------------------------
# Real end-to-end SSE round-trip — wires config → ChatOpenAI → invoke → AIMessage
# ---------------------------------------------------------------------------


def _sse_chunk(delta, finish_reason=None, usage=None,
               model="test-model", cid="chatcmpl-e2e-1"):
    """One OpenAI-compatible chat.completion.chunk in raw wire dict form."""
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


def _sse_frame_bytes(chunks):
    """SSE wire format: ``data: {...}\\n\\n`` per event, ``data: [DONE]\\n\\n`` terminator.
    CRITICAL: frames separated by \n\n (NOT single \\n) — single-newline frames
    produce a misleading JSONDecodeError instead of the real decode error.
    """
    out = []
    for c in chunks:
        out.append("data: " + json.dumps(c, separators=(",", ":")) + "\n\n")
    out.append("data: [DONE]\n\n")
    return "".join(out).encode("utf-8")


class TestStreamingInvokeEndToEnd:
    """REAL end-to-end SSE round-trip on the production chokepoint.

    Closes the gap that let C1 (missing ``ChatGenerationChunk`` import)
    ship green: the config-level suite never sent bytes through an HTTP
    transport, never fed SSE-shaped bytes through the streaming decode
    path, and never ran ``.invoke()`` end-to-end. This class does all
    three, against an in-process ``httpx.MockTransport`` (no network,
    no ports).

    The flow exercises the EXACT production paths:
      clean_llm_config → ThinkingChatOpenAI(**cfg) → httpx.MockTransport
      → real OpenAI-compatible SSE → _stream() → _convert_chunk_to_generation_chunk
      → chunk aggregation → AIMessage.

    The handler captures the serialized POST body — the true wire boundary —
    so we can assert ``stream: true`` was sent. SSE frames use
    ``data: {...}\\n\\n`` (the \n\n separator is the council-confirmed gotcha).
    """

    # Fixed fixtures (deliberately deterministic; ordering is part of the
    # semantic claim — reasoning fragments must aggregate in delta order).
    _REASONING_FRAGMENT_A = "User greeted; I should "
    _REASONING_FRAGMENT_B = "call the weather tool."
    _CONTENT_FRAGMENT_A = "Let me "
    _CONTENT_FRAGMENT_B = "check."
    _TOOL_NAME = "get_weather"
    _TOOL_ARGS = {"city": "Hanoi", "unit": "c"}
    _TOOL_ID = "call_e2e_1"
    _USAGE = {"prompt_tokens": 12, "completion_tokens": 34, "total_tokens": 46}

    @pytest.fixture
    def wire_tap(self):
        """Build the SSE stream fixture + capture-only handler."""

        class _Tap:
            def __init__(self):
                self.bodies: list[dict] = []

        tap = _Tap()

        # Build SSE chunks — must include:
        #   - reasoning_content across ≥2 chunks (different fragments)
        #   - content across ≥2 chunks
        #   - ONE tool_call streamed as partial chunks
        #       (id+name on first chunk, argument fragments across ≥2 chunks)
        #   - final chunk carrying usage
        #   - finish_reason on the last content/tool chunk
        tool_args_json = json.dumps(self._TOOL_ARGS, separators=(",", ":"))
        # Split into ≥2 fragments (council requires ≥2 — we use 3).
        arg_fragments = [
            tool_args_json[: tool_args_json.index('"Hanoi"') + len('"Hanoi"')],
            tool_args_json[tool_args_json.index('"Hanoi"') + len('"Hanoi"'):],
        ]

        sse_chunks = [
            # 1) role + first reasoning fragment
            _sse_chunk({"role": "assistant", "reasoning_content":
                        self._REASONING_FRAGMENT_A}),
            # 2) second reasoning fragment
            _sse_chunk({"reasoning_content": self._REASONING_FRAGMENT_B}),
            # 3) first content fragment
            _sse_chunk({"content": self._CONTENT_FRAGMENT_A}),
            # 4) second content fragment
            _sse_chunk({"content": self._CONTENT_FRAGMENT_B}),
            # 5) tool-call first chunk: id + name + first arg fragment
            _sse_chunk({"tool_calls": [{
                "index": 0,
                "id": self._TOOL_ID,
                "type": "function",
                "function": {
                    "name": self._TOOL_NAME,
                    "arguments": arg_fragments[0],
                },
            }]}),
            # 6) tool-call second chunk: remaining arg fragments
            _sse_chunk({"tool_calls": [{
                "index": 0,
                "function": {"arguments": arg_fragments[1]},
            }]}),
            # 7) final chunk: finish_reason + usage
            _sse_chunk({}, finish_reason="stop", usage=self._USAGE),
        ]

        def handler(request):
            tap.bodies.append(json.loads(request.content))
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=_sse_frame_bytes(sse_chunks),
                request=request,
            )

        tap.handler = handler
        return tap

    @pytest.fixture
    def llm_and_client(self, wire_tap):
        """Construct the REAL ThinkingChatOpenAI through the REAL
        ``clean_llm_config`` chokepoint, with the mock transport injected
        as ``http_client`` (the way ensemble wires custom clients)."""
        from daemon.graph import ThinkingChatOpenAI, clean_llm_config

        client = httpx.Client(
            transport=httpx.MockTransport(wire_tap.handler),
            base_url="https://llm.test.local/v1",
        )
        cfg = {
            "model": "test-model",
            "api_key": "test-key",
            "base_url": "https://llm.test.local/v1",
            "http_client": client,
        }
        cleaned = clean_llm_config(cfg)
        llm = ThinkingChatOpenAI(**cleaned)
        yield llm, client
        client.close()

    def test_real_invoke_round_trip_aggregates_all_features(
        self, llm_and_client, wire_tap,
    ):
        """End-to-end SSE round-trip: invoke() must aggregate the SSE
        stream into an AIMessage carrying (a) content (b) reasoning_content
        (c) one fully-assembled tool_call (d) usage_metadata, AND the
        outgoing POST body must carry ``stream: true`` on the wire."""
        llm, _ = llm_and_client
        result = llm.invoke([HumanMessage(content="hi")])

        # (a) aggregated content
        assert isinstance(result.content, str), (
            f"AIMessage.content must be the concatenated content string; "
            f"got {type(result.content).__name__}"
        )
        assert self._CONTENT_FRAGMENT_A + self._CONTENT_FRAGMENT_B in result.content, (
            "aggregated content must contain both fragments in delta order"
        )

        # (b) reasoning_content preserved in additional_kwargs
        assert "reasoning_content" in result.additional_kwargs, (
            "streaming path must preserve reasoning_content in "
            "additional_kwargs — see graph.py::_convert_delta_to_message_chunk"
        )
        assert result.additional_kwargs["reasoning_content"] == (
            self._REASONING_FRAGMENT_A + self._REASONING_FRAGMENT_B
        ), "reasoning_content must concatenate fragments in delta order"

        # (c) tool_calls assembled with parsed args
        tool_calls = result.tool_calls
        assert len(tool_calls) == 1, (
            f"expected exactly one tool_call (reassembled from partial "
            f"chunks); got {len(tool_calls)}"
        )
        tc = tool_calls[0]
        assert tc["name"] == self._TOOL_NAME, (
            f"tool_call name must survive the id+name-on-first-chunk + "
            f"args-across-subsequent-chunks assembly; got {tc['name']!r}"
        )
        assert tc["id"] == self._TOOL_ID, (
            f"tool_call id must come from the first chunk; got {tc['id']!r}"
        )
        assert tc["args"] == self._TOOL_ARGS, (
            f"tool_call args must parse back to the original dict; "
            f"got {tc['args']!r}"
        )

        # (d) usage_metadata populated — requires W1 (stream_usage=True)
        # If this assertion fails after C1 lands, that's the W1 signal.
        assert result.usage_metadata is not None, (
            "usage_metadata must be populated from the streamed usage "
            "chunk — requires W1 (stream_usage=True injection in "
            "clean_llm_config) so langchain emits "
            "stream_options: {include_usage: true}"
        )

        # Wire payload: outgoing POST body must carry stream: true
        assert wire_tap.bodies, "transport handler received no requests"
        payload = wire_tap.bodies[-1]
        assert payload.get("stream") is True, (
            f"outgoing POST body must carry stream: true (CF-125s fix); "
            f"got stream={payload.get('stream')!r}"
        )