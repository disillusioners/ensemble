"""Phase-1 streaming tests for od.generate (plan od-generate-async-poll §6.1, §7).

The factory (:func:`daemon.plugin_subsystem.opendesign.generate._do_chat_call`)
issues ``stream=True`` + ``stream_options={"include_usage": True}`` and
consumes the SSE stream synchronously inside ONE factory attempt,
returning a ChatCompletion-shaped envelope. These tests prove the
survival pattern against a REAL-path ``httpx.MockTransport`` SSE upstream
(the ``test_llm_failover_v2.py`` pattern — a pure delayed-buffered mock
would re-prove the buffered 524 death, not streaming survival), with
COMPRESSED timelines: the CI stand-ins use sub-second sleeps; the
literal >120s duration proof is the §7.8 real-upstream smoke (tester
commission, post-merge pre-promote — NOT covered here).

Covers:
- §7 core survival: full content + finish_reason=stop + usage captured;
  envelope byte-identical vs a buffered fixture.
- §7.1 TTFB boundary pair: in-band SSE error envelope (guard fire)
  maps into the existing retry taxonomy (transient) — retried and
  failed-over, never a crash.
- §7.2 include_usage golden: streamed usage/finish_reason byte-identical
  vs buffered; streamed finish_reason="length" still fires Gate 2 —
  modeled on the Phase-0 probe's thinking-only truncation profile
  (8000-token budget consumed entirely by reasoning_content, ZERO
  answer chars).
- §7.3 mid-stream abort on primary → backup attempt (failover
  preserved; thread-local URL re-read per attempt).
- §7.4 (a) non-stream/wrong-content-type → taxonomy classification,
  no crash; (b) wire-format pin: stream=True +
  stream_options={"include_usage": True} on the create() call.
- §7.5 heartbeat tolerance with the REAL proxy tokens
  (``: connected`` / ``: heartbeat``).
- reasoning_content accumulates SEPARATELY from content (Phase-0
  probe: MiniMax-M3 interleaved thinking); the answer join NEVER
  mixes thinking tokens in.

Offline-first: every byte rides ``httpx.MockTransport``; no network.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List, Optional

import httpx
import pytest

from daemon.plugin_subsystem.opendesign.generate import (
    GenerateInput,
    OdGenerate,
)


# ---------------------------------------------------------------------------
# SSE fixture helpers — real openai SDK client over httpx.MockTransport
# ---------------------------------------------------------------------------

EVENT_STREAM_HEADERS = {"content-type": "text/event-stream"}


def _chunk(
    delta: Optional[Dict[str, Any]] = None,
    finish_reason: Optional[str] = None,
    *,
    usage: Optional[Dict[str, int]] = None,
    chunk_id: str = "chatcmpl-test",
    model: str = "vision",
) -> bytes:
    """Encode one OpenAI SSE data chunk (the probe's observed shape)."""
    body: Dict[str, Any] = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": 1739212800,
        "model": model,
    }
    if usage is not None:
        # Terminal usage chunk: stream_options.include_usage — arrives
        # with EMPTY choices (Phase-0 probe: usage after finish_reason,
        # before [DONE]).
        body["choices"] = []
        body["usage"] = usage
    else:
        body["choices"] = [
            {
                "index": 0,
                "delta": delta if delta is not None else {},
                "finish_reason": finish_reason,
            }
        ]
    return ("data: " + json.dumps(body) + "\n\n").encode()


def _raw_data_event(payload: Dict[str, Any]) -> bytes:
    """Encode a raw SSE data line (error envelopes, non-chunk shapes)."""
    return ("data: " + json.dumps(payload) + "\n\n").encode()


def _sse_bytes(events: List[Any]) -> Callable[[httpx.Request], httpx.Response]:
    """Build a MockTransport handler emitting SSE events from a list.

    Event forms:
    - ``bytes`` → emitted verbatim (comment lines, ``data: [DONE]``,
      raw error envelopes via :func:`_raw_data_event`)
    - ``("sleep", seconds)`` → compressed-timeline delay
    - ``("abort", exc)`` → raise mid-stream (connection abort)
    - dict → encoded as an SSE data chunk via :func:`_chunk`
    """

    def gen():
        for ev in events:
            if isinstance(ev, bytes):
                yield ev
            elif isinstance(ev, tuple) and ev[0] == "sleep":
                time.sleep(ev[1])
            elif isinstance(ev, tuple) and ev[0] == "abort":
                raise ev[1]
            elif isinstance(ev, dict):
                yield _chunk(**ev)
            else:  # pragma: no cover - fixture misuse guard
                raise ValueError(f"unsupported SSE fixture event: {ev!r}")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers=EVENT_STREAM_HEADERS, content=gen())

    return handler


def _buffered_handler(payload: Dict[str, Any]) -> Callable[[httpx.Request], httpx.Response]:
    """Non-stream fallback: a buffered application/json reply."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=json.dumps(payload).encode(),
        )

    return handler


def _patch_streaming_openai(monkeypatch, handler) -> List[httpx.URL]:
    """Route ``openai.OpenAI`` through MockTransport (real-path pattern).

    Only the TRANSPORT is mocked — the real openai client, real
    ``create()`` call, and real SSE ``Stream`` iteration all run, so the
    test exercises the production consumption path end-to-end. Returns
    the captured request URLs (host-level assertions for failover).
    """
    captured_urls: List[httpx.URL] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        captured_urls.append(request.url)
        return handler(request)

    import openai as real_openai

    original_openai_cls = real_openai.OpenAI

    def _patched_openai(**client_kwargs):
        client_kwargs["http_client"] = httpx.Client(
            transport=httpx.MockTransport(_handler)
        )
        return original_openai_cls(**client_kwargs)

    monkeypatch.setattr(real_openai, "OpenAI", _patched_openai)
    return captured_urls


ENV_PRIMARY = {
    "OPENAI_BASE_URL": "http://primary.test/v1",
    "OPENAI_API_KEY": "fake-key",
    "OPENAI_MODEL_VISION": "vision",
}
ENV_WITH_BACKUP = {
    **ENV_PRIMARY,
    "OPENAI_BASE_URL_BACKUP": "http://backup.test/v1",
}

GUARD_FIRE_WIRE = _raw_data_event(
    {
        "error": {
            "type": "stream_deadline",
            "message": "Request timeout - no response received",
        }
    }
)
COMPLETE_HTML = "<!doctype html><html><head></head><body>OK</body></html>"


def _happy_stream_events(
    content_pieces: Optional[List[str]] = None,
    *,
    usage: Optional[Dict[str, int]] = None,
    finish_reason: str = "stop",
) -> List[Any]:
    """A healthy stream: connected marker, content, finish, usage, [DONE]."""
    pieces = content_pieces if content_pieces is not None else [COMPLETE_HTML]
    events: List[Any] = [b": connected\n\n"]
    for piece in pieces:
        events.append({"delta": {"content": piece}})
    events.append({"delta": {}, "finish_reason": finish_reason})
    if usage is not None:
        events.append(_chunk(usage=usage))
    events.append(b"data: [DONE]\n\n")
    return events


# ---------------------------------------------------------------------------
# §7 core survival proof (compressed timeline)
# ---------------------------------------------------------------------------


class TestStreamingSurvival:
    def test_survival_pattern_full_stream_envelope_and_execute(
        self, monkeypatch
    ):
        """60 content chunks on a timer + real heartbeats → the factory
        joins ALL content, captures the terminal finish_reason + usage,
        and ``execute()`` returns the success dict. Compressed timeline
        (60 × 2ms) — the survival PATTERN is what CI proves; the literal
        >120s duration is the §7.8 real-upstream smoke."""
        from daemon.plugin_subsystem.opendesign import generate as gen_mod

        events: List[Any] = [b": connected\n\n"]
        pieces = [f"<p>{i}</p>" for i in range(59)]
        pieces.append("<body>done</body></html>")
        for i, piece in enumerate(pieces):
            if i == 30:
                events.append(b": heartbeat\n\n")  # real proxy token
            if i == 0:
                events.append(("sleep", 0.05))  # compressed TTFB stand-in
            events.append({"delta": {"content": piece}})
            events.append(("sleep", 0.002))
        events.append({"delta": {}, "finish_reason": "stop"})
        events.append(
            _chunk(usage={"prompt_tokens": 198, "completion_tokens": 8000,
                          "total_tokens": 8198})
        )
        events.append(b"data: [DONE]\n\n")

        _patch_streaming_openai(monkeypatch, _sse_bytes(events))

        result = OdGenerate.execute(
            GenerateInput(prompt="landing page", kind="prototype"),
            env=ENV_PRIMARY,
        )
        assert result["error"] is None
        assert result["truncated"] is False
        assert result["finish_reason"] == "stop"
        assert result["html"] == "".join(pieces)
        assert result["usage"] == {
            "prompt_tokens": 198,
            "completion_tokens": 8000,
            "total_tokens": 8198,
        }

    def test_envelope_byte_identical_streamed_vs_buffered(self, monkeypatch):
        """THE ENVELOPE IS THE INVARIANT (plan §6.1): for the same
        logical response, the streamed path and the buffered fixture
        produce byte-identical ``execute()`` output dicts."""
        from daemon.plugin_subsystem.opendesign import generate as gen_mod

        usage = {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}

        # Streamed path (real factory through MockTransport).
        _patch_streaming_openai(
            monkeypatch, _sse_bytes(_happy_stream_events(usage=usage))
        )
        streamed = OdGenerate.execute(
            GenerateInput(prompt="x", kind="prototype"), env=ENV_PRIMARY
        )

        # Buffered path: the pre-existing invoker-seam fixture returns a
        # ChatCompletion-shaped buffered object with the SAME logical
        # content / finish_reason / usage.
        class _Msg:
            content = COMPLETE_HTML

        class _Choice:
            finish_reason = "stop"
            message = _Msg()

        class _Usage:
            prompt_tokens = 10
            completion_tokens = 20
            total_tokens = 30
            completion_tokens_details = None

        class _Buffered:
            choices = [_Choice()]
            usage = _Usage()

        def _buffered_invoker(**kwargs):
            return _Buffered()

        OdGenerate._set_test_hooks(llm_invoker=_buffered_invoker)
        try:
            buffered = OdGenerate.execute(
                GenerateInput(prompt="x", kind="prototype"), env=ENV_PRIMARY
            )
        finally:
            OdGenerate._set_test_hooks(llm_invoker=gen_mod._invoke_chat_via_facade)

        assert streamed == buffered
        assert streamed["html"] == COMPLETE_HTML
        assert streamed["usage"]["total_tokens"] == 30


# ---------------------------------------------------------------------------
# §7.1 TTFB boundary pair — in-band SSE error envelope → retry taxonomy
# ---------------------------------------------------------------------------


class TestTTFBBoundaryPair:
    def test_ttfb_first_chunk_inside_guard_passes(self, monkeypatch):
        """~100s-to-first-chunk shape (compressed 50ms stand-in): the
        stream starts inside the proxy's no-forwardable-byte guard and
        completes normally."""
        events: List[Any] = [
            b": connected\n\n",
            ("sleep", 0.05),  # compressed stand-in for ~100s TTFB
            {"delta": {"content": COMPLETE_HTML}},
            {"delta": {}, "finish_reason": "stop"},
            _chunk(usage={"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}),
            b"data: [DONE]\n\n",
        ]
        _patch_streaming_openai(monkeypatch, _sse_bytes(events))
        result = OdGenerate.execute(
            GenerateInput(prompt="x", kind="prototype"), env=ENV_PRIMARY
        )
        assert result["error"] is None
        assert result["html"] == COMPLETE_HTML

    def test_ttfb_guard_fire_maps_into_retry_taxonomy(self, monkeypatch):
        """~125s shape (compressed stand-in): the proxy's live-mode
        0-byte StreamDeadline guard fires → in-band SSE error envelope.
        The consumption path MUST map it into the EXISTING retry
        taxonomy (transient) — the facade burns the transient budget on
        primary (3 attempts, no backup) and ``execute`` surfaces the
        typed upstream envelope. Zero crashes, no silent success."""
        events: List[Any] = [
            b": connected\n\n",
            ("sleep", 0.05),  # compressed stand-in for ~125s dead window
            GUARD_FIRE_WIRE,
        ]
        attempts = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            inner = _sse_bytes(events)
            return inner(request)

        _patch_streaming_openai(monkeypatch, handler)
        result = OdGenerate.execute(
            GenerateInput(prompt="x", kind="prototype"), env=ENV_PRIMARY
        )
        # Transient taxonomy engaged: exactly the 3-attempt transient
        # budget burned against primary (no backup configured).
        assert attempts["n"] == 3, (
            f"guard-fire envelope must classify TRANSIENT (3-attempt "
            f"budget); saw {attempts['n']} attempt(s)"
        )
        assert result["error"]["ok"] is False
        assert result["error"]["code"] == "upstream_http_error"

    def test_ttfb_guard_fire_fails_over_to_backup(self, monkeypatch):
        """Guard fire on primary → mapped transient → failover swap →
        backup streams healthy → success. Proves the in-band envelope
        mapping feeds the EXISTING failover ladder (not a dead end)."""
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "backup.test":
                inner = _sse_bytes(_happy_stream_events(
                    usage={"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}
                ))
                return inner(request)
            inner = _sse_bytes([b": connected\n\n", ("sleep", 0.02), GUARD_FIRE_WIRE])
            return inner(request)

        urls = _patch_streaming_openai(monkeypatch, handler)
        result = OdGenerate.execute(
            GenerateInput(prompt="x", kind="prototype"), env=ENV_WITH_BACKUP
        )
        assert result["error"] is None
        assert result["html"] == COMPLETE_HTML
        hosts = [u.host for u in urls]
        assert "primary.test" in hosts and "backup.test" in hosts
        # Every primary attempt precedes the first backup attempt
        # (thread-local URL re-read drives the swap).
        assert hosts.index("backup.test") > max(
            i for i, h in enumerate(hosts) if h == "primary.test"
        )


# ---------------------------------------------------------------------------
# §7.3 mid-stream abort on primary → backup attempt
# ---------------------------------------------------------------------------


class TestMidStreamFailover:
    def test_midstream_abort_on_primary_backup_completes(self, monkeypatch):
        """2 content chunks then a peer-close mid-stream → the abort
        rides the EXISTING taxonomy (httpx.RemoteProtocolError,
        conditional-retryable gate default-on) → backup attempt
        completes the artifact."""
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "backup.test":
                inner = _sse_bytes(_happy_stream_events(
                    usage={"prompt_tokens": 5, "completion_tokens": 6, "total_tokens": 11}
                ))
                return inner(request)
            inner = _sse_bytes(
                [
                    b": connected\n\n",
                    {"delta": {"content": "<html><bo"}},
                    {"delta": {"content": "dy>par"},
                     "finish_reason": None},
                    ("abort", httpx.RemoteProtocolError("peer closed connection")),
                ]
            )
            return inner(request)

        urls = _patch_streaming_openai(monkeypatch, handler)
        result = OdGenerate.execute(
            GenerateInput(prompt="x", kind="prototype"), env=ENV_WITH_BACKUP
        )
        assert result["error"] is None
        assert result["html"] == COMPLETE_HTML
        hosts = [u.host for u in urls]
        # 3 primary attempts (PRIMARY_TRANSIENT_MAX) then ONE backup attempt.
        assert hosts.count("primary.test") == 3, (
            f"expected the 3-attempt primary slice before the swap; got {hosts}"
        )
        assert hosts.count("backup.test") == 1


# ---------------------------------------------------------------------------
# §7.4 fallback + wire-format pins
# ---------------------------------------------------------------------------


class TestFallbackAndWireFormat:
    def test_non_stream_content_type_maps_to_taxonomy(self, monkeypatch):
        """§7.4a: a buffered ``application/json`` reply to the
        ``stream:true`` request classifies into the retry taxonomy (the
        SDK's stream iterator silently yields ZERO chunks on a buffered
        body — the consumer's Content-Type gate converts that silent
        empty into a retryable transient), burns the transient budget,
        and surfaces the typed upstream envelope. No crash."""
        buffered_payload = {
            "id": "x",
            "choices": [
                {"message": {"role": "assistant", "content": COMPLETE_HTML},
                 "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        }
        attempts = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            inner = _buffered_handler(buffered_payload)
            return inner(request)

        _patch_streaming_openai(monkeypatch, handler)
        result = OdGenerate.execute(
            GenerateInput(prompt="x", kind="prototype"), env=ENV_PRIMARY
        )
        assert attempts["n"] == 3, (
            f"non-streamed reply must classify TRANSIENT; saw {attempts['n']} attempt(s)"
        )
        assert result["error"]["ok"] is False
        assert result["error"]["code"] == "upstream_http_error"

    def test_wire_format_stream_kwargs_on_create_call(self, monkeypatch):
        """§7.4b: the create() call carries ``stream=True`` AND
        ``stream_options={"include_usage": True}`` on the wire."""
        from daemon.plugin_subsystem.opendesign import generate as gen_mod
        import openai as real_openai

        captured: List[Dict[str, Any]] = []

        class _FakeStreamResponse:
            headers = {"content-type": "text/event-stream"}

        class _FakeStream:
            response = _FakeStreamResponse()

            def __iter__(self):
                return iter([])

        class _Comps:
            def create(self, **kwargs):
                captured.append(kwargs)
                return _FakeStream()

        class _Chat:
            completions = _Comps()

        class _FakeOpenAI:
            def __init__(self, **kw):
                self.chat = _Chat()

        monkeypatch.setattr(real_openai, "OpenAI", _FakeOpenAI)
        monkeypatch.setattr(gen_mod, "current_failover_url", lambda: None)

        gen_mod._do_chat_call(
            model="vision",
            base_url="http://primary.test/v1",
            api_key="fake-key",
            system_prompt="sys",
            user_prompt="user",
            max_tokens=4096,
            temperature=0.7,
            timeout=120.0,
        )
        assert captured[0]["stream"] is True
        assert captured[0]["stream_options"] == {"include_usage": True}
        # The rest of the request shape is unchanged.
        assert captured[0]["max_tokens"] == 4096
        assert captured[0]["messages"][0]["role"] == "system"


# ---------------------------------------------------------------------------
# §7.5 heartbeat comment tolerance — REAL proxy tokens
# ---------------------------------------------------------------------------


class TestHeartbeatTolerance:
    def test_real_heartbeat_tokens_tolerated(self, monkeypatch):
        """``: connected`` at stream start + ``: heartbeat`` at cadence
        between content chunks (the REAL proxy tokens, heartbeat.go:65;
        probe: 30 heartbeats at 5.0s cadence over 154s) — the stream
        iterator ignores comment lines and the answer join is exact."""
        events: List[Any] = [
            b": connected\n\n",
            b": heartbeat\n\n",
            {"delta": {"content": "<!doctype html><html>"}},
            b": heartbeat\n\n",
            b": heartbeat\n\n",
            {"delta": {"content": "<head></head>"}},
            b": heartbeat\n\n",
            {"delta": {"content": "<body>OK</body></html>"}},
            {"delta": {}, "finish_reason": "stop"},
            _chunk(usage={"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}),
            b"data: [DONE]\n\n",
        ]
        _patch_streaming_openai(monkeypatch, _sse_bytes(events))
        result = OdGenerate.execute(
            GenerateInput(prompt="x", kind="prototype"), env=ENV_PRIMARY
        )
        assert result["error"] is None
        assert result["html"] == COMPLETE_HTML
        assert result["finish_reason"] == "stop"


# ---------------------------------------------------------------------------
# §7.2 include_usage golden + Gate-2 on streamed truncation
# ---------------------------------------------------------------------------


class TestStreamedTruncation:
    def test_streamed_length_truncation_thinking_only_fires_gate2(
        self, monkeypatch
    ):
        """§7.2 + Phase-0 probe profile: the ENTIRE budget consumed by
        ``delta.reasoning_content`` (thinking tokens), ZERO answer
        chars, ``finish_reason="length"`` — a typed refusal surfaces
        (Gate 1 empty_response precedes on zero content — existing gate
        ordering) and the usage chunk still lands (completion_tokens
        includes the thinking tokens, probe observation #3)."""
        thinking_bits = ["thinking ", "piece ", "three "]  # compressed stand-in
        events: List[Any] = [
            b": connected\n\n",
            b": heartbeat\n\n",
        ]
        for bit in thinking_bits:
            events.append({"delta": {"reasoning_content": bit}})
        events.append({"delta": {}, "finish_reason": "length"})
        events.append(
            _chunk(usage={"prompt_tokens": 198, "completion_tokens": 8000,
                          "total_tokens": 8198})
        )
        events.append(b"data: [DONE]\n\n")
        _patch_streaming_openai(monkeypatch, _sse_bytes(events))

        result = OdGenerate.execute(
            GenerateInput(prompt="x", kind="prototype"), env=ENV_PRIMARY
        )
        assert result["error"]["ok"] is False
        # Gate 1 (empty) wins on zero answer chars — the typed refusal
        # still carries the REAL finish_reason for the caller.
        assert result["error"]["code"] == "empty_response"
        assert result["truncated"] is True
        assert result["html"] == ""
        assert result["finish_reason"] == "length"
        assert result["usage"]["completion_tokens"] == 8000

    def test_streamed_length_truncation_partial_answer_gate2_exact(self, monkeypatch):
        """§7.2 Gate-2 pin: streamed ``finish_reason=\"length\"`` with a
        non-empty answer → the truncation gate fires EXACTLY
        (``truncation_detected``), never a partial success."""
        events: List[Any] = [
            b": connected\n\n",
            {"delta": {"content": "<html><body>partial"}},
            {"delta": {}, "finish_reason": "length"},
            _chunk(usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}),
            b"data: [DONE]\n\n",
        ]
        _patch_streaming_openai(monkeypatch, _sse_bytes(events))

        result = OdGenerate.execute(
            GenerateInput(prompt="x", kind="prototype"), env=ENV_PRIMARY
        )
        assert result["error"]["ok"] is False
        assert result["error"]["code"] == "truncation_detected"
        assert result["truncated"] is True
        assert result["finish_reason"] == "length"

    def test_reasoning_content_accumulates_separately_from_content(
        self, monkeypatch
    ):
        """Envelope invariant (probe addendum #1): thinking deltas and
        answer deltas both accumulate — ``message.reasoning_content``
        carries the thinking join, ``message.content`` the answer join
        ONLY. Extraction consumes ``.content``; the html is never
        polluted by thinking tokens."""
        from daemon.plugin_subsystem.opendesign import generate as gen_mod

        events: List[Any] = [
            b": connected\n\n",
            {"delta": {"reasoning_content": "plan the "}},
            {"delta": {"reasoning_content": "layout"}},
            {"delta": {"content": "<html>"}},
            {"delta": {"reasoning_content": " more thinking"}},
            {"delta": {"content": "<body>OK</body></html>"}},
            {"delta": {}, "finish_reason": "stop"},
            _chunk(usage={"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}),
            b"data: [DONE]\n\n",
        ]
        _patch_streaming_openai(monkeypatch, _sse_bytes(events))

        # Factory-level envelope: direct call (thread-local URL unset →
        # falls back to the base_url kwarg, same as the no-HA path).
        envelope = gen_mod._do_chat_call(
            model="vision",
            base_url="http://primary.test/v1",
            api_key="fake-key",
            system_prompt="sys",
            user_prompt="user",
            max_tokens=4096,
            temperature=0.7,
            timeout=120.0,
        )
        message = envelope.choices[0].message
        assert message.content == "<html><body>OK</body></html>"
        assert message.reasoning_content == "plan the layout more thinking"

        # And the execute() path consumes .content only — the html is
        # the answer join, never the thinking join.
        result = OdGenerate.execute(
            GenerateInput(prompt="x", kind="prototype"), env=ENV_PRIMARY
        )
        assert result["error"] is None
        assert result["html"] == "<html><body>OK</body></html>"


# ---------------------------------------------------------------------------
# Quota/blocklist precedence through the in-band mapping (hardening pin)
# ---------------------------------------------------------------------------


class TestInbandErrorPrecedence:
    def test_quota_shape_inband_envelope_stays_terminal(self, monkeypatch):
        """A quota-window shape riding the in-band SSE envelope must NOT
        be retried: the consumption path re-raises unchanged (quota
        precedence, facade's bare-APIError branch owns the terminal
        typing) — exactly ONE attempt."""
        quota_event = {
            "error": {
                "type": "usage_limit",
                "message": "Token Plan usage limit reached",
            }
        }  # encoded onto the wire via _raw_data_event in the handler
        attempts = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            inner = _sse_bytes([b": connected\n\n", _raw_data_event(quota_event)])
            return inner(request)

        _patch_streaming_openai(monkeypatch, handler)
        result = OdGenerate.execute(
            GenerateInput(prompt="x", kind="prototype"), env=ENV_WITH_BACKUP
        )
        assert attempts["n"] == 1, (
            f"quota shapes are terminal (no retry); saw {attempts['n']} attempt(s)"
        )
        assert result["error"]["ok"] is False
        assert result["error"]["code"] == "upstream_http_error"
