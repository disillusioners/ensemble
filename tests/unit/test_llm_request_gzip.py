"""Outbound LLM request-body gzip compression (OPENAI_REQUEST_GZIP).

Wire-level tests verifying that the opt-in gzip compression middleware
behaves correctly across the 6 contract cases:

1. ENABLED — request body gzipped + ``Content-Encoding: gzip``
   stamped + ``Content-Length`` corrected to the compressed size.
2. DISABLED — pure passthrough, no ``http_client`` / ``http_async_client``
   kwarg attached by ``clean_llm_config``, no transport wrapper
   constructed, no headers injected. The pre-feature wire format is
   preserved byte-identically.
3. STREAMING PATH — when ``streaming=True`` and ``request_gzip=True``,
   the request body is still gzipped on the wire AND the response is
   consumed normally (streaming semantics unchanged).
4. CONFIG PARSING — ``LLMConfig.request_gzip`` defaults False, accepts
   ``"true"`` / ``"1"`` / ``"false"`` / ``""`` / YAML ``None`` per
   project conventions (same shape as ``OPENAI_STREAMING`` /
   ``OPENAI_BUFFER_RESPONSE_HEADER``).
5. GET / NO-BODY requests — the gzip transport is a NO-OP on
   methods without a body (GET / HEAD / DELETE / OPTIONS) and on
   empty-body POSTs. No ``Content-Encoding`` is stamped.
6. WIRE LEVEL (REAL SOCKET SERVER) — the previous mock-transport tests
   inspect ``request.content`` (httpx's mutated content cache) which
   can pass while the actual wire payload is still uncompressed (the
   ``request._content`` / ``request.stream`` split bug). The wire-level
   tests drive a real ``httpx.HTTPTransport`` / ``AsyncHTTPTransport``
   into an in-process localhost socket server and assert on the
   SERVER-RECEIVED bytes — proving the bytes going out on the TCP
   socket are actually gzip-compressed.

The first 5 test classes use ``httpx.MockTransport`` (precedent:
``tests/unit/test_llm_streaming_wire_verify.py``) — a real
``ThinkingChatOpenAI`` is constructed through ``clean_llm_config``,
talks to the in-process mock transport (no network, no ports), and
the handler captures the wire-level request bytes so we can assert
the gzip-encoded body + ``Content-Encoding`` + ``Content-Length`` in
the same shape the proxy will see.

Configuration propagation mirrors the ``default_streaming`` pattern
(see ``daemon/__main__.py`` + ``daemon/api.py`` + the
``test_llm_streaming_activation`` suite): ``clean_llm_config`` reads
the ``ThinkingChatOpenAI.default_request_gzip`` ClassVar, and the
startup wiring (``__main__.py`` + ``api.py``) propagates
``LLMConfig.request_gzip`` to the ClassVar before any LLM is
constructed.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import httpx
import pytest
from langchain_core.messages import HumanMessage

# ─── Test infrastructure (mirrors test_llm_streaming_wire_verify.py) ───

PROXY_BASE_URL = "https://llm.test.local/v1"


def _non_streaming_completion(content: str = "Hello world") -> dict:
    """A minimal non-streaming chat completion body (used by tests that
    don't care about response decoding)."""
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1735689600,
        "model": "test-model",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": 12,
            "completion_tokens": 7,
            "total_tokens": 19,
        },
    }


def _sse_bytes(chunks: list[dict]) -> bytes:
    """Serialize chunks into SSE wire format (data: {json}\\n\\n chunks)."""
    out = ["data: " + json.dumps(c, separators=(",", ":")) + "\n\n" for c in chunks]
    out.append("data: [DONE]\n\n")
    return "".join(out).encode("utf-8")


class _WireTap:
    """httpx transport handler that records every serialized request.

    Captures the raw request bytes (including the post-compression body)
    so the wire-level gzip assertions can read the actual on-wire
    payload. Answers non-streaming JSON completions for simple
    request paths; SSE-shaped responses for streaming requests.

    Pattern mirrors ``tests/unit/test_llm_streaming_wire_verify.py::
    WireTap`` — same handler shape, different focus (gzip middleware
    vs streaming flag).
    """

    def __init__(
        self,
        stream: bool = False,
        sse_chunks: list[dict] | None = None,
        content: str = "Hello world",
        model: str = "test-model",
    ) -> None:
        self.requests: list[httpx.Request] = []
        self.bodies: list[dict] = []
        self._stream = stream
        self._sse = sse_chunks or []
        self._content = content
        self._model = model

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        # ``request.content`` reflects the post-transport-mutation
        # bytes (i.e. what hits the wire). For gzip-enabled transports
        # these are the COMPRESSED bytes — we capture both
        # ``request.content`` (wire bytes) AND a JSON-decoded view of
        # the ORIGINAL uncompressed body (via the gzip-decompressed
        # bytes) so the per-test assertions can choose their angle.
        body_bytes = request.content
        ce_header = request.headers.get("content-encoding")
        if ce_header == "gzip":
            # The transport wraps the body in gzip; decompress here
            # so per-test assertions can check the LOGICAL payload.
            body_bytes = gzip.decompress(body_bytes)
        try:
            self.bodies.append(json.loads(body_bytes))
        except json.JSONDecodeError:
            # Body wasn't JSON — record the raw bytes for byte-level
            # assertions in the GET / no-body tests.
            self.bodies.append({"_raw_bytes": body_bytes})
        if self._stream:
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=_sse_bytes(self._sse),
                request=request,
            )
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            content=json.dumps(
                _non_streaming_completion(content=self._content)
            ).encode("utf-8"),
            request=request,
        )

    @property
    def last(self) -> dict:
        return self.bodies[-1]

    @property
    def last_request(self) -> httpx.Request:
        return self.requests[-1]


def _make_gzip_wrapped_mock(tap: _WireTap) -> httpx.Client:
    """Build a gzip-wrapped mock client for the ENABLED test path.

    When ``request_gzip=True``, the production code path uses the
    module-level gzip singleton — but in tests we need to swap the
    REAL inner ``HTTPTransport`` for a ``MockTransport`` so the wire
    bytes are observable. Builds a fresh client so the singleton
    isn't mutated (production code never reads the test mock).

    Returns the single ``httpx.Client`` whose gzip-wrapped transport
    fronts the mock. The caller is responsible for ``client.close()``
    — that closes the gzip transport wrapper, which in turn closes
    the underlying MockTransport (httpx walks the transport chain on
    ``Client.close()``).
    """
    from daemon.services.llm_gzip import GzipRequestTransport

    mock_inner = httpx.MockTransport(tap.handler)
    gzip_transport = GzipRequestTransport(mock_inner)
    return httpx.Client(transport=gzip_transport)


def _make_llm_with_tap(
    tap: _WireTap,
    *,
    streaming: bool | None = False,
    request_gzip: bool = False,
) -> tuple[Any, httpx.Client]:
    """Construct a REAL ``ThinkingChatOpenAI`` through the REAL
    ``clean_llm_config`` chokepoint with the mock transport injected
    as ``http_client``.

    The ``streaming`` flag defaults to ``False`` here so the mock
    tap's non-streaming JSON response works without SSE decoding.
    Callers that want streaming semantics pass ``streaming=True``
    AND provide SSE chunks via the tap (``_WireTap(stream=True,
    sse_chunks=[...])``).

    The ``request_gzip`` flag manipulates the
    ``ThinkingChatOpenAI.default_request_gzip`` ClassVar BEFORE
    ``clean_llm_config`` runs — same shape as the production wiring
    (``daemon/__main__.py`` + ``daemon/api.py`` propagate
    ``LLMConfig.request_gzip`` to that ClassVar at startup).

    For the gzip-ENABLED path we build a FRESH gzip-wrapped mock
    client (with a ``MockTransport`` inner) so the wire bytes are
    observable. The module-level singleton is NOT used here —
    production code reads that singleton; tests need to swap the
    inner transport to a mock so we can capture the wire payload.
    """
    from daemon.graph import ThinkingChatOpenAI, clean_llm_config

    # Save the class var so we can restore it; autouse fixture below
    # also resets after each test.
    saved = ThinkingChatOpenAI.default_request_gzip
    ThinkingChatOpenAI.default_request_gzip = request_gzip

    if request_gzip:
        # Gzip path: build a fresh gzip-wrapped mock client so the
        # wire bytes are observable. ``clean_llm_config`` then sees
        # ``http_client`` already present and DOES NOT attach the
        # singleton gzip client (the ``http_client not in cleaned``
        # guard preserves the test's chosen client — same path as
        # the test-injection / explicit-overrides path documented
        # in clean_llm_config's docstring).
        http_client = _make_gzip_wrapped_mock(tap)
    else:
        # Disabled path: plain mock transport client. The disabled
        # branch's contract is "no custom transport, no headers
        # injected" — so we bypass clean_llm_config's gzip injection
        # by passing a plain mock client explicitly (same shape as
        # the existing streaming wire-verify tests).
        http_client = httpx.Client(
            transport=httpx.MockTransport(tap.handler),
            base_url=PROXY_BASE_URL,
        )

    try:
        cfg: dict = {
            "model": "test-model",
            "api_key": "test-key",
            "base_url": PROXY_BASE_URL,
            "http_client": http_client,
        }
        if streaming is not None:
            cfg["streaming"] = streaming
        cleaned = clean_llm_config(cfg)
        return ThinkingChatOpenAI(**cleaned), http_client
    finally:
        # Restore immediately so a failure inside the helper doesn't
        # leak the ClassVar override into the next test.
        ThinkingChatOpenAI.default_request_gzip = saved


# A large user message so the wire body is big enough that gzip
# actually shrinks it. A ~45-byte JSON body (the default "hi")
# gzip-compresses to ~64 bytes because gzip's per-stream header
# overhead (~20 bytes for magic + flags + metadata) exceeds the
# savings on tiny inputs. The production guard ``only swap if
# smaller`` is correct — the test simply needs a body large
# enough that gzip wins. ~1KB of redundant JSON text is more than
# enough.
_LARGE_USER_MESSAGE = (
    "Please analyze the following scenario in detail, listing all "
    "trade-offs, edge cases, and operational considerations. We need "
    "a thorough write-up that covers correctness, performance, "
    "maintainability, and the impact on downstream consumers. The "
    "team will use this analysis to make an architectural decision "
    "and we want every angle covered. This is the canonical "
    "scenario: a system processes user requests and needs to "
    "balance latency, throughput, and resource utilization across "
    "many concurrent users. The user expects high-quality output "
    "in a reasonable time. We need to discuss compression, caching, "
    "rate limiting, back-pressure, error handling, and observability. "
    "Please write a comprehensive analysis covering all these areas "
    "with concrete recommendations and clear next steps."
)


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _protect_class_vars():
    """Reset ``ThinkingChatOpenAI.default_request_gzip`` AND the
    gzip-client singleton around every test so a class-var leak
    from one test cannot poison the next.

    The ``clean_env`` autouse fixture (tests/conftest.py) already
    clears ``OPENAI_*`` env vars; this fixture additionally scrubs
    the ClassVar + module-level httpx clients.
    """
    from daemon.graph import ThinkingChatOpenAI
    from daemon.services.llm_gzip import reset_cached_clients

    saved = ThinkingChatOpenAI.default_request_gzip
    reset_cached_clients()
    try:
        yield
    finally:
        ThinkingChatOpenAI.default_request_gzip = saved
        reset_cached_clients()


# ═══════════════════════════════════════════════════════════════════
# Test class 1: ENABLED — body gzipped + Content-Encoding: gzip +
# Content-Length matches compressed size
# ═══════════════════════════════════════════════════════════════════


class TestEnabledGzipRequestBody:
    """When ``request_gzip=True`` the gzip transport must:

    * gzip-compress the request body,
    * stamp ``Content-Encoding: gzip``,
    * correct ``Content-Length`` to the COMPRESSED size (NOT the
      uncompressed size — a mismatch would cause a 400 from a strict
      proxy / some LLM front-ends).
    """

    def test_body_gzipped_and_content_encoding_set(self):
        """The wire bytes are gzip-compressed and the header is stamped.

        Decompresses the captured body, parses the JSON, and asserts
        the logical chat-completion payload survived the round-trip
        (the gzip transport is lossless from the proxy's perspective).

        Uses ``_LARGE_USER_MESSAGE`` (~1KB) so gzip actually wins
        on the body — the production guard skips compression when
        gzip framing overhead exceeds the savings on tiny inputs
        (a ~45-byte body gzip-compresses to ~64 bytes).
        """
        tap = _WireTap()
        llm, http_client = _make_llm_with_tap(tap, request_gzip=True)
        try:
            llm.invoke([HumanMessage(content=_LARGE_USER_MESSAGE)])

            assert len(tap.requests) == 1, (
                "exactly one request must hit the wire (no retries in this path)"
            )
            req = tap.last_request
            assert req.headers.get("Content-Encoding") == "gzip", (
                f"Content-Encoding: gzip must be stamped when gzip is enabled "
                f"(observed headers: {dict(req.headers)!r})"
            )
            # Body-level: the wire bytes are gzip-compressed, so they
            # must NOT equal the logical JSON. Decompress + re-parse
            # to confirm the payload survives the round-trip.
            decompressed = gzip.decompress(req.content)
            payload = json.loads(decompressed)
            assert payload.get("model") == "test-model"
            assert payload.get("messages"), "messages must survive gzip round-trip"
            assert payload["messages"][0]["content"] == _LARGE_USER_MESSAGE
        finally:
            http_client.close()

    def test_content_length_matches_compressed_size(self):
        """``Content-Length`` must equal the compressed size — the
        Stale-Content-Length hazard documented in
        ``daemon.services.llm_gzip``. A mismatch (header says N, body
        is M) would cause a 400 from a strict proxy / some LLM
        front-ends.

        Uses ``_LARGE_USER_MESSAGE`` (~1KB) so gzip actually wins
        on the body — see :meth:`test_body_gzipped_and_content_encoding_set`
        for the rationale.
        """
        tap = _WireTap()
        llm, http_client = _make_llm_with_tap(tap, request_gzip=True)
        try:
            llm.invoke([HumanMessage(content=_LARGE_USER_MESSAGE)])

            req = tap.last_request
            # The wire bytes are the COMPRESSED body.
            assert req.headers.get("Content-Encoding") == "gzip"
            compressed_size = len(req.content)
            content_length_header = req.headers.get("Content-Length")
            assert content_length_header is not None, (
                "Content-Length must be stamped when the transport "
                "mutates the body (stale-Content-Length guard)"
            )
            assert int(content_length_header) == compressed_size, (
                f"Content-Length={content_length_header!r} must equal the "
                f"compressed body size={compressed_size} — a mismatch "
                f"would 400 on a strict proxy"
            )
            # Sanity: the compressed size should be smaller than the
            # uncompressed JSON (a typical chat-completion body of a
            # few hundred bytes compresses 5-10x). If they're equal,
            # the transport's "only swap if smaller" guard kicked in
            # and we did NOT actually gzip — fail loud.
            decompressed_size = len(gzip.decompress(req.content))
            assert compressed_size < decompressed_size, (
                f"compressed={compressed_size} must be < "
                f"decompressed={decompressed_size} for the test to be "
                f"meaningful (if the body didn't shrink, gzip was a no-op)"
            )
        finally:
            http_client.close()


# ═══════════════════════════════════════════════════════════════════
# Test class 2: DISABLED — untouched passthrough (no header, no
# compression, no transport attached)
# ═══════════════════════════════════════════════════════════════════


class TestDisabledPassthrough:
    """When ``request_gzip=False`` (default) the wire must be
    byte-identical to the pre-feature state:

    * No ``Content-Encoding`` header is stamped (the default httpx
      Client does not add it on a vanilla POST).
    * The request body is the original JSON, uncompressed.
    * ``clean_llm_config`` does NOT attach ``http_client`` or
      ``http_async_client`` kwargs — the langchain-openai client
      uses its built-in default httpx clients (zero behavior change).
    """

    def test_disabled_no_content_encoding_header(self):
        """No ``Content-Encoding`` header on the wire when disabled."""
        tap = _WireTap()
        llm, http_client = _make_llm_with_tap(tap, request_gzip=False)
        try:
            llm.invoke([HumanMessage(content="hi")])

            req = tap.last_request
            assert "Content-Encoding" not in req.headers, (
                f"Content-Encoding must be ABSENT when gzip is disabled "
                f"(observed headers: {dict(req.headers)!r})"
            )
            # Body: must be the original uncompressed JSON. The wire
            # bytes should be parseable directly as JSON without
            # gzip decompression.
            body = json.loads(req.content)
            assert body.get("model") == "test-model"
            assert body["messages"][0]["content"] == "hi"
        finally:
            http_client.close()

    def test_disabled_clean_llm_config_attaches_no_gzip_client(self):
        """With ``default_request_gzip=False``, ``clean_llm_config``
        must NOT inject ``http_client`` or ``http_async_client``
        kwargs — even when the caller passes ``http_client=None``
        explicitly. The langchain-openai client then uses its
        built-in default httpx clients, so the wire path is
        byte-identical to the pre-feature state.
        """
        from daemon.graph import ThinkingChatOpenAI, clean_llm_config

        original = ThinkingChatOpenAI.default_request_gzip
        ThinkingChatOpenAI.default_request_gzip = False
        try:
            cleaned = clean_llm_config(
                {"model": "gpt-4o", "api_key": "test", "base_url": PROXY_BASE_URL}
            )
            assert "http_client" not in cleaned, (
                "clean_llm_config must NOT inject http_client when "
                "default_request_gzip is False (zero behavior change contract)"
            )
            assert "http_async_client" not in cleaned, (
                "clean_llm_config must NOT inject http_async_client when "
                "default_request_gzip is False (zero behavior change contract)"
            )
        finally:
            ThinkingChatOpenAI.default_request_gzip = original

    def test_disabled_no_gzip_transport_constructed(self):
        """The gzip module-level singletons must NOT be built when
        gzip is disabled — we never construct a transport we won't
        use (zero behavior change contract).

        Verifies by reading the module's private globals BEFORE
        and AFTER a disabled-path invocation; the singletons stay
        None on the disabled path.
        """
        import daemon.services.llm_gzip as gzip_mod

        gzip_mod.reset_cached_clients()
        assert gzip_mod._gzip_sync_client is None
        assert gzip_mod._gzip_async_client is None

        tap = _WireTap()
        llm, http_client = _make_llm_with_tap(tap, request_gzip=False)
        try:
            llm.invoke([HumanMessage(content="hi")])
        finally:
            http_client.close()

        # Still None — the disabled path must not have built the
        # gzip clients.
        assert gzip_mod._gzip_sync_client is None, (
            "gzip singleton must not be built on the disabled path"
        )
        assert gzip_mod._gzip_async_client is None, (
            "gzip singleton must not be built on the disabled path"
        )


# ═══════════════════════════════════════════════════════════════════
# Test class 3: STREAMING PATH — request gzipped, response consumed
# normally
# ═══════════════════════════════════════════════════════════════════


class TestStreamingWithGzipEnabled:
    """When ``streaming=True`` AND ``request_gzip=True``:

    * Request body is still gzip-compressed (the gzip transport
      sits BELOW streaming — it operates on the wire bytes
      regardless of response handling).
    * Response is consumed normally: SSE chunks aggregate into the
      same final AIMessage a non-gzip streaming call produces.
    * Streaming semantics unchanged — no Accept-Encoding behavior
      on the response side, no decompression of the response body.
    """

    def test_streaming_request_gzipped_response_consumed(self):
        """Streaming + gzip: request body is gzipped, response SSE
        chunks aggregate into the expected AIMessage."""
        from daemon.graph import ThinkingChatOpenAI, clean_llm_config

        # Drive a streaming SSE round trip through the gzip transport.
        sse_chunks = [
            {"id": "s1", "object": "chat.completion.chunk", "created": 1,
             "model": "test-model",
             "choices": [{"index": 0, "delta": {"role": "assistant"}}]},
            {"id": "s2", "object": "chat.completion.chunk", "created": 1,
             "model": "test-model",
             "choices": [{"index": 0, "delta": {"content": "Hello"}}]},
            {"id": "s3", "object": "chat.completion.chunk", "created": 1,
             "model": "test-model",
             "choices": [{"index": 0, "delta": {"content": " world"}}]},
            {"id": "s4", "object": "chat.completion.chunk", "created": 1,
             "model": "test-model",
             "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
             "usage": {"prompt_tokens": 5, "completion_tokens": 2,
                       "total_tokens": 7}},
        ]

        tap = _WireTap(stream=True, sse_chunks=sse_chunks)
        # Build a FRESH gzip-wrapped mock client instead of swapping
        # the cached singleton's transport. ``clean_llm_config`` sees
        # ``http_client`` already present and does NOT attach the
        # singleton (the ``http_client not in cleaned`` guard
        # preserves the test's chosen client — same path as the
        # explicit-overrides branch). No singleton mutation, no
        # finally-restore required, no risk of leaking a MockTransport
        # into a later test via a stale reference.
        from daemon.services.llm_gzip import GzipRequestTransport

        http_client = _make_gzip_wrapped_mock(tap)
        try:
            original = ThinkingChatOpenAI.default_request_gzip
            ThinkingChatOpenAI.default_request_gzip = True
            try:
                cfg = clean_llm_config(
                    {"model": "test-model", "api_key": "test",
                     "base_url": PROXY_BASE_URL,
                     "http_client": http_client, "streaming": True}
                )
                llm = ThinkingChatOpenAI(**cfg)
                msg = llm.invoke([HumanMessage(content=_LARGE_USER_MESSAGE)])
            finally:
                ThinkingChatOpenAI.default_request_gzip = original

            # Wire-level: request was gzipped.
            assert len(tap.requests) == 1
            req = tap.last_request
            assert req.headers.get("Content-Encoding") == "gzip", (
                f"streaming request must still be gzipped when gzip is "
                f"enabled (observed headers: {dict(req.headers)!r})"
            )
            assert int(req.headers.get("Content-Length", "0")) == len(
                req.content
            ), "stale-Content-Length guard must hold on the streaming path"

            # Response-level: SSE chunks aggregated into the expected
            # AIMessage (streaming semantics unchanged — the gzip
            # transport only touches the request bytes).
            assert msg.content == "Hello world", (
                f"streaming SSE response must aggregate normally with "
                f"gzip transport (got {msg.content!r})"
            )
        finally:
            http_client.close()


# ═══════════════════════════════════════════════════════════════════
# Test class 4: CONFIG PARSING — defaults + env coercion edge cases
# ═══════════════════════════════════════════════════════════════════


class TestConfigParsing:
    """``LLMConfig.request_gzip`` env-var + YAML-null parsing.

    Mirrors the ``OPENAI_STREAMING`` / ``OPENAI_BUFFER_RESPONSE_HEADER``
    patterns documented in ``daemon/config.py``. Pydantic-settings
    raises ``ValidationError`` on bool parsing of an empty string,
    and a bare YAML ``request_gzip:`` is parsed as None — both must
    coerce to the default (False) instead of crashing daemon boot.

    Same shape as ``tests/unit/test_llm_buffer_response_header.py::
    TestLLMConfigBufferResponseHeader``.
    """

    def test_default_is_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No env var set → ``request_gzip`` defaults to False."""
        monkeypatch.delenv("OPENAI_REQUEST_GZIP", raising=False)
        from daemon.config import LLMConfig

        cfg = LLMConfig(_env_file=None)
        assert cfg.request_gzip is False, (
            "default must be False (zero behavior change contract)"
        )

    def test_env_true_enables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_REQUEST_GZIP", "true")
        from daemon.config import LLMConfig

        cfg = LLMConfig(_env_file=None)
        assert cfg.request_gzip is True

    def test_env_false_disables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_REQUEST_GZIP", "false")
        from daemon.config import LLMConfig

        cfg = LLMConfig(_env_file=None)
        assert cfg.request_gzip is False

    def test_env_one_enables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``"1"`` is the standard "truthy" coercion — accept it for
        operator convenience (``OPENAI_REQUEST_GZIP=1``)."""
        monkeypatch.setenv("OPENAI_REQUEST_GZIP", "1")
        from daemon.config import LLMConfig

        cfg = LLMConfig(_env_file=None)
        assert cfg.request_gzip is True

    def test_empty_string_coerces_to_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``OPENAI_REQUEST_GZIP=""`` must NOT crash daemon boot.

        Mirrors the ``_coerce_streaming_empty_to_default`` /
        ``_coerce_buffer_response_header_empty_to_default`` pattern:
        an empty value that pastes through the
        ``${OPENAI_REQUEST_GZIP:-false}`` shell interpolation falls
        back to the default (False) instead of failing pydantic bool
        parsing.
        """
        monkeypatch.setenv("OPENAI_REQUEST_GZIP", "")
        from daemon.config import LLMConfig

        cfg = LLMConfig(_env_file=None)
        assert cfg.request_gzip is False

    def test_yaml_null_coerces_to_false(self) -> None:
        """YAML ``request_gzip:`` (None) coerces to the default False."""
        from daemon.config import LLMConfig

        cfg = LLMConfig(request_gzip=None, _env_file=None)
        assert cfg.request_gzip is False


# ═══════════════════════════════════════════════════════════════════
# Test class 5: GET / no-body requests unaffected
# ═══════════════════════════════════════════════════════════════════


class TestGetAndNoBodyRequests:
    """Methods without a body (GET / HEAD / DELETE / OPTIONS) and
    POSTs with empty content must NOT be gzipped — the transport is
    a no-op on those paths so:

    * No ``Content-Encoding`` header is stamped.
    * The body is unchanged (empty stays empty).
    * The wire is byte-identical to a non-gzip path.

    Verified directly against the ``GzipRequestTransport`` so we
    test the middleware contract without spinning up a full LLM
    stack (no LangChain / openai client required for the
    no-body-method branch — the gzip transport is below that layer).
    """

    def test_get_request_uncompressed(self):
        """GET / HEAD / DELETE / OPTIONS: no ``Content-Encoding``
        header, no body bytes, no compression applied."""
        from daemon.services.llm_gzip import GzipRequestTransport

        # Use a MockTransport that records the request and answers
        # with a 200 — we only care about the request side.
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, request=request)

        inner = httpx.MockTransport(handler)
        transport = GzipRequestTransport(inner)

        for method in ("GET", "HEAD", "DELETE", "OPTIONS"):
            # Build a request without a body (httpx auto-sets
            # ``request.content`` to ``b""`` for body-less methods).
            req = httpx.Request(method, "https://example.com/v1/models")
            transport.handle_request(req)
            assert "Content-Encoding" not in req.headers, (
                f"{method} request must NOT receive Content-Encoding "
                f"header (observed: {dict(req.headers)!r})"
            )
            assert req.content == b"", (
                f"{method} request must remain empty (observed "
                f"{req.content!r})"
            )

    def test_post_with_empty_body_not_compressed(self):
        """A POST with an empty body (rare but possible — e.g. some
        embedding endpoint flavors) must NOT be compressed. There's
        no payload to compress; gzip framing overhead on empty
        bytes would just inflate the wire.
        """
        from daemon.services.llm_gzip import GzipRequestTransport

        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, request=request)

        inner = httpx.MockTransport(handler)
        transport = GzipRequestTransport(inner)

        # POST with explicitly empty content. httpx sets
        # ``request.content`` to ``b""`` for an empty body and the
        # transport's body-emptiness guard must skip compression.
        req = httpx.Request(
            "POST", "https://example.com/v1/embeddings", content=b""
        )
        transport.handle_request(req)
        assert "Content-Encoding" not in req.headers, (
            "POST with empty body must NOT receive Content-Encoding "
            f"header (observed: {dict(req.headers)!r})"
        )
        assert req.content == b"", (
            f"empty POST body must remain empty (observed {req.content!r})"
        )

    def test_double_compression_guard(self):
        """If a request already carries a ``Content-Encoding`` header
        (e.g. httpx re-sent it after redirect), the transport must
        NOT compress again — that's the double-compression guard
        (hard constraint #6).

        httpx's documented rewind pattern (``BaseTransport.handle_request``
        docs) re-sends the SAME ``Request`` object after redirect /
        retry, so without this guard the body would be gzipped twice
        and the proxy would see ``Content-Encoding: gzip, gzip`` on
        a doubly-compressed body.
        """
        from daemon.services.llm_gzip import GzipRequestTransport

        # A pre-compressed payload — simulating a request that
        # was already gzipped upstream (or by a prior retry).
        original_payload = b'{"messages": [{"role": "user", "content": "hi"}]}'
        already_gzipped = gzip.compress(original_payload)

        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, request=request)

        inner = httpx.MockTransport(handler)
        transport = GzipRequestTransport(inner)

        # Construct a request that already carries Content-Encoding:
        # gzip + a pre-gzipped body. The transport must NOT compress
        # again (it would yield Content-Encoding: gzip, gzip and a
        # body the proxy cannot decode).
        req = httpx.Request(
            "POST",
            "https://example.com/v1/chat/completions",
            content=already_gzipped,
            headers={"Content-Encoding": "gzip"},
        )
        transport.handle_request(req)

        # The header must NOT have been added again.
        assert req.headers.get("Content-Encoding") == "gzip", (
            f"double-compression guard must preserve existing "
            f"Content-Encoding exactly (observed: "
            f"{req.headers.get('Content-Encoding')!r})"
        )
        # The body must NOT have been re-gzipped — its bytes
        # should still decompress back to the ORIGINAL payload,
        # not to gzip(plaintext).
        assert req.content == already_gzipped, (
            "double-compression guard must leave the body bytes "
            "untouched (the existing Content-Encoding already encodes them)"
        )
        assert gzip.decompress(req.content) == original_payload


# ═══════════════════════════════════════════════════════════════════
# Test class 6: WIRE-LEVEL — real socket server, server-side
# assertions on bytes that actually crossed the TCP socket. These
# tests are the ground-truth guard against the ``request._content`` /
# ``request.stream`` split bug: a buggy transport that updates only
# ``_content`` (the property cache) but not ``stream`` (the bytes
# httpx actually serializes on send) would let ALL the tests in the
# classes above pass while the wire payload remains the ORIGINAL
# uncompressed body. h11 then aborts with
# ``LocalProtocolError: Too much data for declared Content-Length``
# because the headers declare a small compressed length but the
# stream yields a large uncompressed body.
#
# These tests prove the bytes the SERVER reads from its socket are
# gzip-compressed and Content-Length matches the actual body length.
# ═══════════════════════════════════════════════════════════════════


class _LocalServer:
    """In-process HTTP server that captures raw request bytes.

    Backed by ``http.server.HTTPServer`` + a thread. The handler
    captures the EXACT bytes received from the socket (via
    ``self.rfile.read(content_length)``) plus the headers — these are
    the bytes httpx serialized onto the TCP socket.

    Reply: a minimal valid non-streaming chat-completion JSON so the
    langchain-openai client's ``invoke()`` call completes. The
    response body is JSON-encoded and well-formed so the
    ``ThinkingChatOpenAI`` client parses it as an ``AIMessage`` (the
    test doesn't care about response decoding — only the wire-side
    request bytes, which are captured server-side before the response
    is constructed).
    """

    # Per-instance capture list — each ``_LocalServer`` has its OWN
    # list (assigned in ``__init__`` below) so multiple server
    # instances in the same test never cross-contaminate captures.
    # The autouse fixture does NOT reset this; tests that need a
    # fresh capture list reset it explicitly via
    # ``server.captured.clear()``. Each instance has its OWN server
    # bound to a UNIQUE free port.

    def __init__(self) -> None:
        # Per-instance capture list — fresh slate for this server.
        self.captured: list[dict] = []
        # Find a free port: bind, get the port, release.
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        sock.close()
        self._server = HTTPServer(("127.0.0.1", self.port), self._make_handler())
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name=f"gzip-wire-test-server-{self.port}",
            daemon=True,
        )
        self._thread.start()

    def _make_handler(self):
        outer = self  # capture port via closure

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 — http.server convention
                content_length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(content_length)
                # Capture EVERYTHING the server saw — body bytes plus
                # headers. These are the bytes that crossed the TCP
                # socket (not the mutated in-memory request object).
                outer.captured.append({
                    "body": body,
                    "headers": dict(self.headers),
                    "content_length_header": self.headers.get(
                        "Content-Length"
                    ),
                    "content_encoding_header": self.headers.get(
                        "Content-Encoding"
                    ),
                })
                # Reply with a minimal valid chat-completion JSON
                # so the langchain-openai client parses cleanly.
                resp = json.dumps(
                    _non_streaming_completion(content="ok")
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)

            def log_message(self, *args, **kwargs):
                pass  # silence test output

        return _Handler

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        # Wait briefly for the thread to exit so the socket is released.
        self._thread.join(timeout=1.0)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def captures(self) -> list[dict]:
        return self.captured


class TestWireLevelRealSocketServer:
    """End-to-end wire-level tests using a real socket server.

    Drives a real ``httpx.HTTPTransport`` / ``AsyncHTTPTransport``
    (NOT ``MockTransport``) and a real ``ThinkingChatOpenAI`` (NOT
    a mock langchain client) into a localhost HTTP server bound to
    a free port. The server reads the EXACT bytes httpx serialized
    onto the socket — there is no ``request.content`` introspection;
    the only assertions are on bytes the SERVER received.

    RED-GREEN contract (verified during implementation):
    Disabling ONLY the ``request.stream = ByteStream(compressed)``
    line in ``_compress_request_body`` makes ALL these tests fail
    with ``h11._util.LocalProtocolError: Too much data for declared
    Content-Length`` — proving these tests catch the
    ``_content``-only bug that the previous ``MockTransport`` tests
    let through. Re-enabling that line makes them pass.
    """

    def test_wire_body_gunzips_to_original_payload(self):
        """Server-received bytes gunzip to the ORIGINAL payload.

        No ``request.content`` introspection — only bytes read from
        the TCP socket by ``BaseHTTPRequestHandler.rfile.read()``.
        """
        from daemon.services.llm_gzip import GzipRequestTransport

        server = _LocalServer()
        try:
            body_bytes = json.dumps({
                "model": "wire-test-model",
                "messages": [{"role": "user", "content": _LARGE_USER_MESSAGE}],
            }).encode("utf-8")
            with httpx.Client(
                transport=GzipRequestTransport(httpx.HTTPTransport())
            ) as client:
                resp = client.post(
                    f"{server.base_url}/v1/chat/completions",
                    content=body_bytes,
                )
            assert resp.status_code == 200, (
                f"server rejected the request — likely a wire-format "
                f"mismatch (status={resp.status_code})"
            )
            assert len(server.captures) == 1
            wire = server.captures[0]
            # (a) wire body gunzips to the original payload
            assert gzip.decompress(wire["body"]) == body_bytes, (
                f"wire bytes must gunzip to the ORIGINAL payload "
                f"(server-received {len(wire['body'])} bytes; "
                f"decompressed should be {len(body_bytes)} bytes)"
            )
        finally:
            server.close()

    def test_wire_content_length_header_matches_wire_body_length(self):
        """``Content-Length`` header the SERVER received equals the
        actual server-received body byte length.

        On httpx 0.28.1 the transport serializes from
        ``request.stream`` — if the middleware updates only
        ``request._content`` (the property cache) and forgets
        ``request.stream``, the body bytes sent on the wire are the
        ORIGINAL uncompressed bytes while the header advertises the
        COMPRESSED length. h11 detects the mismatch and raises
        ``LocalProtocolError: Too much data for declared Content-Length``
        BEFORE the bytes reach the server. With the fix in place,
        ``Content-Length`` matches the actual wire body length.
        """
        from daemon.services.llm_gzip import GzipRequestTransport

        server = _LocalServer()
        try:
            body_bytes = json.dumps({
                "model": "wire-test-model",
                "messages": [{"role": "user", "content": _LARGE_USER_MESSAGE}],
            }).encode("utf-8")
            with httpx.Client(
                transport=GzipRequestTransport(httpx.HTTPTransport())
            ) as client:
                resp = client.post(
                    f"{server.base_url}/v1/chat/completions",
                    content=body_bytes,
                )
            assert resp.status_code == 200
            wire = server.captures[0]
            # (b) Content-Length header == len(wire_bytes)
            cl_header = int(wire["content_length_header"])
            assert cl_header == len(wire["body"]), (
                f"Content-Length header (={cl_header}) must equal "
                f"server-received body length (={len(wire['body'])}); "
                f"a mismatch means h11 should have aborted but "
                f"httpx shipped the wire bytes anyway (bug)."
            )
        finally:
            server.close()

    def test_wire_content_encoding_header_is_gzip(self):
        """``Content-Encoding`` header the SERVER received is
        ``gzip``.

        Pure server-side header assertion — proves the wire
        advertisement matches the wire encoding.
        """
        from daemon.services.llm_gzip import GzipRequestTransport

        server = _LocalServer()
        try:
            body_bytes = json.dumps({
                "model": "wire-test-model",
                "messages": [{"role": "user", "content": _LARGE_USER_MESSAGE}],
            }).encode("utf-8")
            with httpx.Client(
                transport=GzipRequestTransport(httpx.HTTPTransport())
            ) as client:
                resp = client.post(
                    f"{server.base_url}/v1/chat/completions",
                    content=body_bytes,
                )
            assert resp.status_code == 200
            wire = server.captures[0]
            # (c) Content-Encoding == gzip
            assert wire["content_encoding_header"] == "gzip", (
                f"server-received Content-Encoding must be 'gzip' "
                f"(observed: {wire['content_encoding_header']!r})"
            )
        finally:
            server.close()

    def test_wire_disabled_path_carries_uncompressed_body(self):
        """Wire-level control: with gzip DISABLED (no wrapping
        transport), the bytes on the wire are the original
        uncompressed JSON and ``Content-Encoding`` is ABSENT.

        Confirms the wire server harness is sensitive enough to
        detect the difference between compressed and uncompressed
        payloads (so the assertions in the gzip-ENABLED tests above
        are meaningful — they're not all passing trivially).
        """
        server = _LocalServer()
        try:
            body_bytes = json.dumps({
                "model": "wire-test-model",
                "messages": [{"role": "user", "content": _LARGE_USER_MESSAGE}],
            }).encode("utf-8")
            with httpx.Client(transport=httpx.HTTPTransport()) as client:
                resp = client.post(
                    f"{server.base_url}/v1/chat/completions",
                    content=body_bytes,
                )
            assert resp.status_code == 200
            wire = server.captures[0]
            assert wire["body"] == body_bytes, (
                f"disabled path must carry the ORIGINAL uncompressed "
                f"body bytes (server got {len(wire['body'])} bytes; "
                f"expected {len(body_bytes)} bytes)"
            )
            assert wire["content_encoding_header"] is None, (
                f"disabled path must NOT stamp Content-Encoding "
                f"(observed: {wire['content_encoding_header']!r})"
            )
            assert int(wire["content_length_header"]) == len(body_bytes)
        finally:
            server.close()

    def test_wire_langchain_chat_path_end_to_end(self):
        """Full end-to-end: ``ThinkingChatOpenAI`` through
        ``clean_llm_config`` with ``default_request_gzip=True`` →
        real gzip-wrapped httpx → real socket server.

        Proves the LangChain injection seam (``clean_llm_config``)
        attaches a transport that ACTUALLY gzips on the wire (not
        just on the in-memory cache). The langchain client receives
        the server's valid chat-completion JSON, parses it into an
        ``AIMessage``, and returns cleanly — proving the response
        handling is untouched.
        """
        from daemon.graph import ThinkingChatOpenAI, clean_llm_config

        server = _LocalServer()
        try:
            saved = ThinkingChatOpenAI.default_request_gzip
            ThinkingChatOpenAI.default_request_gzip = True
            try:
                cfg = clean_llm_config({
                    "model": "wire-test-model",
                    "api_key": "test-key",
                    "base_url": server.base_url,
                    "streaming": False,
                })
                llm = ThinkingChatOpenAI(**cfg)
                msg = llm.invoke(
                    [HumanMessage(content=_LARGE_USER_MESSAGE)]
                )
            finally:
                ThinkingChatOpenAI.default_request_gzip = saved

            # The langchain-openai client appended /chat/completions
            # to the base URL — server captured the wire bytes.
            assert len(server.captures) == 1
            wire = server.captures[0]
            assert wire["content_encoding_header"] == "gzip"
            cl_header = int(wire["content_length_header"])
            assert cl_header == len(wire["body"]), (
                f"Content-Length ({cl_header}) must equal wire body "
                f"length ({len(wire['body'])})"
            )
            # Gunzip the wire body — must parse as valid JSON with
            # the test-model field and the LARGE_USER_MESSAGE content.
            payload = json.loads(gzip.decompress(wire["body"]))
            assert payload["model"] == "wire-test-model"
            assert payload["messages"][0]["content"] == _LARGE_USER_MESSAGE
            # Response-side: the langchain client parsed the JSON
            # reply into an AIMessage — proves the gzip wrapper
            # doesn't touch response handling.
            assert msg.content == "ok"
        finally:
            server.close()

    def test_wire_async_path_end_to_end(self):
        """Async mirror of the sync end-to-end test: drives a real
        ``httpx.AsyncHTTPTransport`` + ``AsyncClient`` into the real
        socket server. Same three assertions (gunzip / CL / CE)
        applied to the server-received bytes.
        """
        from daemon.services.llm_gzip import GzipAsyncRequestTransport

        async def _drive() -> dict:
            body_bytes = json.dumps({
                "model": "wire-test-model",
                "messages": [{"role": "user", "content": _LARGE_USER_MESSAGE}],
            }).encode("utf-8")
            async with httpx.AsyncClient(
                transport=GzipAsyncRequestTransport(httpx.AsyncHTTPTransport())
            ) as client:
                resp = await client.post(
                    f"{server.base_url}/v1/chat/completions",
                    content=body_bytes,
                )
            assert resp.status_code == 200
            return server.captures[0]

        server = _LocalServer()
        try:
            wire = asyncio.run(_drive())
            assert wire["content_encoding_header"] == "gzip"
            cl_header = int(wire["content_length_header"])
            assert cl_header == len(wire["body"]), (
                f"async path: Content-Length ({cl_header}) must "
                f"equal wire body length ({len(wire['body'])})"
            )
            # Recover original payload via gunzip.
            recovered = gzip.decompress(wire["body"])
            payload = json.loads(recovered)
            assert payload["model"] == "wire-test-model"
            assert payload["messages"][0]["content"] == _LARGE_USER_MESSAGE
        finally:
            server.close()


# ═══════════════════════════════════════════════════════════════════
# Test class 7: RAW-SDK SEAM — wire-level verification of the
# ``openai.OpenAI(http_client=gzip_client)`` integration used by the
# skill_search / skill_embedding / skill_evolution services. These
# tests close the coverage gap left by classes 1–6, which only drive
# the LangChain path through ``clean_llm_config``. The raw-SDK seam
# routes through ``make_gzip_httpx_client`` → ``openai.OpenAI`` →
# ``client.chat.completions.create`` (or ``client.embeddings.create``)
# — a different code path that the previous mock-transport tests
# never exercised.
#
# Same wire-level harness as class 6 — assertions are on bytes the
# server actually received from the TCP socket, NOT on
# ``request.content`` (which the gzip middleware mutates in-memory
# BEFORE the transport serializes; a buggy transport could mutate
# the cache without serializing the mutated bytes).
# ═══════════════════════════════════════════════════════════════════


class TestRawSDKGzipWire:
    """Wire-level verification of the raw OpenAI SDK path.

    Constructs ``openai.OpenAI(api_key=..., base_url=..., http_client=<gzip>)``
    — the EXACT shape the skill services build at the raw-SDK seam —
    and drives a real chat-completion call into the local socket
    server. All assertions are on bytes the SERVER read from the TCP
    socket via ``BaseHTTPRequestHandler.rfile.read`` (the ground-truth
    guard, same pattern as :class:`TestWireLevelRealSocketServer`).
    """

    def test_raw_sdk_chat_completion_gzipped_on_wire(self):
        """``openai.OpenAI(http_client=gzip_client)`` ships the chat-
        completion request body gzip-compressed and stamps
        ``Content-Encoding: gzip`` — proven by server-received bytes.

        The ``openai`` SDK posts to ``{base_url}/chat/completions``;
        the server captures ``rfile.read(content_length)`` and the
        ``Content-Encoding`` / ``Content-Length`` headers verbatim
        from the wire. A buggy gzip wrapper that updates only the
        in-memory cache (the ``request._content`` / ``request.stream``
        split bug) would let the SDK post the ORIGINAL uncompressed
        JSON; this test catches that regression at the raw-SDK seam
        which the previous LangChain-only tests never reached.
        """
        import openai

        from daemon.services.llm_gzip import make_gzip_httpx_client

        server = _LocalServer()
        try:
            gzip_http = make_gzip_httpx_client()
            try:
                # ``max_retries=0`` so a wire-format mismatch surfaces
                # as a clean error instead of an internal SDK retry loop.
                client = openai.OpenAI(
                    api_key="test-key",
                    base_url=server.base_url,
                    http_client=gzip_http,
                    max_retries=0,
                )
                resp = client.chat.completions.create(
                    model="wire-test-model",
                    messages=[
                        {"role": "user", "content": _LARGE_USER_MESSAGE},
                    ],
                )
            finally:
                gzip_http.close()

            # The SDK parses the server's reply into a ChatCompletion
            # object — proves the response path is untouched.
            assert resp.choices[0].message.content == "ok"

            # Server-received bytes — the ground truth.
            assert len(server.captures) == 1
            wire = server.captures[0]
            assert wire["content_encoding_header"] == "gzip", (
                f"raw-SDK chat-completion must carry Content-Encoding: "
                f"gzip on the wire (observed: "
                f"{wire['content_encoding_header']!r})"
            )
            # Server received the COMPRESSED body. Gunzip back to the
            # ORIGINAL JSON the SDK serialized before the gzip
            # transport wrapped it.
            decoded = gzip.decompress(wire["body"])
            payload = json.loads(decoded)
            assert payload["model"] == "wire-test-model"
            assert payload["messages"][0]["content"] == _LARGE_USER_MESSAGE
        finally:
            server.close()

    def test_raw_sdk_disabled_path_sends_uncompressed_body(self):
        """When the flag is OFF (no gzip client passed), the raw-SDK
        client uses its built-in httpx client and ships the ORIGINAL
        uncompressed JSON to the server — byte-identical to the
        pre-feature state.

        This is the raw-SDK counterpart to
        :meth:`TestWireLevelRealSocketServer.test_wire_disabled_path_carries_uncompressed_body`
        (which covered the LangChain path). Together they prove the
        "flag-OFF byte-identical" hard constraint holds across BOTH
        LLM construction seams.
        """
        import openai

        server = _LocalServer()
        try:
            # No ``http_client`` kwarg → SDK uses its built-in default
            # httpx client (NOTE: openai SDK's ``DefaultHttpxClient``
            # sets ``follow_redirects=True`` automatically — that's
            # exactly the behavior we want to preserve on the
            # disabled path).
            client = openai.OpenAI(
                api_key="test-key",
                base_url=server.base_url,
                max_retries=0,
            )
            resp = client.chat.completions.create(
                model="wire-test-model",
                messages=[{"role": "user", "content": _LARGE_USER_MESSAGE}],
            )
            assert resp.choices[0].message.content == "ok"

            assert len(server.captures) == 1
            wire = server.captures[0]
            assert wire["content_encoding_header"] is None, (
                f"disabled path must NOT stamp Content-Encoding on "
                f"the raw-SDK seam either (observed: "
                f"{wire['content_encoding_header']!r})"
            )
            # Body is the original uncompressed JSON — parse directly
            # (no gunzip).
            payload = json.loads(wire["body"])
            assert payload["model"] == "wire-test-model"
            assert payload["messages"][0]["content"] == _LARGE_USER_MESSAGE
        finally:
            server.close()


# ═══════════════════════════════════════════════════════════════════
# Test class 8: THREAD SAFETY — ``get_or_build_gzip_clients``
# under N concurrent threads must produce exactly one client per
# type. Regression test for W1: the unsynchronized version was
# empirically observed to build N distinct AsyncClient instances
# when N threads raced on the None-check.
# ═══════════════════════════════════════════════════════════════════


class TestSingletonThreadSafety:
    """``get_or_build_gzip_clients`` must produce exactly one
    ``httpx.Client`` and one ``httpx.AsyncClient`` across N
    concurrent callers. Verified via ``ThreadPoolExecutor`` with a
    ``threading.Barrier`` so all threads race into the builder at the
    same instant — maximizes the chance of catching the
    check-then-build race that motivated the double-checked locking
    fix.
    """

    def test_concurrent_threads_produce_single_sync_client(self):
        """N concurrent threads → exactly one ``httpx.Client`` built."""
        from daemon.services import llm_gzip as gzip_mod
        from daemon.services.llm_gzip import (
            get_or_build_gzip_clients,
            make_gzip_httpx_client,
            reset_cached_clients,
        )

        reset_cached_clients()
        try:
            # Patch ``make_gzip_httpx_client`` to count invocations so
            # we can prove exactly ONE sync client was constructed
            # across N concurrent callers.
            call_count = {"sync": 0, "async": 0}
            original_sync = gzip_mod.make_gzip_httpx_client
            original_async = gzip_mod.make_gzip_async_httpx_client

            def counting_sync() -> httpx.Client:
                call_count["sync"] += 1
                return original_sync()

            def counting_async() -> httpx.AsyncClient:
                call_count["async"] += 1
                return original_async()

            gzip_mod.make_gzip_httpx_client = counting_sync
            gzip_mod.make_gzip_async_httpx_client = counting_async

            N = 10
            barrier = threading.Barrier(N)
            results: dict[int, tuple[httpx.Client, httpx.AsyncClient]] = {}

            def worker(idx: int) -> None:
                # All threads rendezvous at the barrier so they hit
                # the None-check as simultaneously as the OS allows.
                barrier.wait()
                results[idx] = get_or_build_gzip_clients()

            try:
                with ThreadPoolExecutor(max_workers=N) as pool:
                    futures = [pool.submit(worker, i) for i in range(N)]
                    for f in futures:
                        f.result()
            finally:
                gzip_mod.make_gzip_httpx_client = original_sync
                gzip_mod.make_gzip_async_httpx_client = original_async

            # The race that motivated the fix: WITHOUT double-checked
            # locking, multiple threads each see ``None`` before any
            # assignment lands and each build their own client. WITH
            # the lock, exactly one build happens for each type.
            assert call_count["sync"] == 1, (
                f"thread-safe builder must construct exactly ONE sync "
                f"client across {N} concurrent threads; observed "
                f"{call_count['sync']} builds (race regression)"
            )
            assert call_count["async"] == 1, (
                f"thread-safe builder must construct exactly ONE async "
                f"client across {N} concurrent threads; observed "
                f"{call_count['async']} builds (race regression)"
            )

            # All N callers must observe the SAME client identity
            # (singleton semantics — same-config callers share).
            sync_clients = {id(results[i][0]) for i in range(N)}
            async_clients = {id(results[i][1]) for i in range(N)}
            assert len(sync_clients) == 1, (
                f"all {N} concurrent callers must observe the SAME "
                f"sync client instance; observed {len(sync_clients)} "
                f"distinct ids (singleton regression)"
            )
            assert len(async_clients) == 1, (
                f"all {N} concurrent callers must observe the SAME "
                f"async client instance; observed {len(async_clients)} "
                f"distinct ids (singleton regression)"
            )

            # Reset uses the patched module attribute — the production
            # reset closes the original client, not the patched one,
            # because reset_cached_clients closes ``_gzip_sync_client``
            # which IS the original. So no extra close needed here.
        finally:
            reset_cached_clients()


# ═══════════════════════════════════════════════════════════════════
# Test class 9: SDK-PARITY — the gzip-wrapped clients must match the
# OpenAI SDK's built-in ``DefaultHttpxClient`` settings, including
# the ``follow_redirects=True`` default (httpx itself defaults to
# ``False``; the SDK explicitly opts in). Without this parity, an
# LLM front-end that issues a 3xx redirect would not be followed by
# the gzip wrapper even though it WOULD be followed by the SDK's
# built-in client — a silent behavioral divergence.
# ═══════════════════════════════════════════════════════════════════


class TestSDKParityFollowRedirects:
    """``follow_redirects=True`` parity with OpenAI SDK's
    ``_DefaultHttpxClient`` / ``_DefaultAsyncHttpxClient``."""

    def test_sync_builder_follows_redirects(self):
        """``make_gzip_httpx_client().follow_redirects is True`` —
        matches the SDK's ``_DefaultHttpxClient`` default.
        """
        from daemon.services.llm_gzip import make_gzip_httpx_client

        client = make_gzip_httpx_client()
        try:
            assert client.follow_redirects is True, (
                f"sync gzip client must have follow_redirects=True "
                f"to match the OpenAI SDK's _DefaultHttpxClient "
                f"(observed: {client.follow_redirects!r})"
            )
        finally:
            client.close()

    def test_async_builder_follows_redirects(self):
        """``make_gzip_async_httpx_client().follow_redirects is True`` —
        matches the SDK's ``_DefaultAsyncHttpxClient`` default.
        """
        from daemon.services.llm_gzip import make_gzip_async_httpx_client

        client = make_gzip_async_httpx_client()
        try:
            assert client.follow_redirects is True, (
                f"async gzip client must have follow_redirects=True "
                f"to match the OpenAI SDK's _DefaultAsyncHttpxClient "
                f"(observed: {client.follow_redirects!r})"
            )
        finally:
            # Sync close path on AsyncClient is absent — same rationale
            # as ``reset_cached_clients``. We drop the reference here
            # and let GC reclaim.
            del client
