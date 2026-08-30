"""Edge-case coverage audit + ADDENDUM gaps for OPENAI_REQUEST_GZIP.

This file adds genuinely-ABSENT edge-case coverage for the
``OPENAI_REQUEST_GZIP`` feature (env var → ``LLMConfig.request_gzip`` →
httpx transport wrappers in ``daemon/services/llm_gzip.py``; seams =
``clean_llm_config`` in ``daemon/graph.py`` + ``resolve_gzip_client`` in
3 skill services).

Coverage map
------------

STEP-1 (audit gaps; production feature file already has 26 tests, all
passing — see ``tests/unit/test_llm_request_gzip.py``):

  1. Wire-level empty POST body (real socket server) .... TestEdgeCaseEmptyBodyWireLevel
  2. Wire-level tiny body / skip-if-not-smaller ......... TestEdgeCaseTinyBodyWireLevel
  3. Wire-level GET / no-body round-trip ................. TestEdgeCaseGetNoBodyWireLevel
  4. Wire-level double-compression guard ................. TestEdgeCaseDoubleCompressionWireLevel
  6. Flag flip mid-process ............................... TestEdgeCaseFlagFlipMidProcess
  8. config.yaml / ${OPENAI_REQUEST_GZIP:-false} ........ TestEdgeCaseConfigYAMLInterpolation

ADDENDUM (mock-fidelity audit gaps):

  1+2. Streaming round-trip wire-level + singleton-injection
        .................................................. TestEdgeCaseStreamingRoundTripWireLevel
  3. Plumbing audit: 4 production call sites invoke
     ``resolve_gzip_client`` with the correct argument
        .................................................. TestEdgeCaseResolveGzipClientPlumbing

Existing tests cover (no NEW coverage needed in this file):

  STEP-1 #5 (thread-safety under concurrent access):
    ``TestSingletonThreadSafety::test_concurrent_threads_produce_single_sync_client``
    uses ``ThreadPoolExecutor`` with N=10 threads + ``threading.Barrier`` to
    exercise CONCURRENT threads through ``get_or_build_gzip_clients``
    (verified by counting exactly one sync + one async build across N=10).
  STEP-1 #4 (double-compression guard, MockTransport level):
    ``TestGetAndNoBodyRequests::test_double_compression_guard`` — wires
    the guard correctly but only against ``MockTransport``. This file's
    ``TestEdgeCaseDoubleCompressionWireLevel`` adds the wire-level
    confirmation that the guard actually fires on the TCP socket.
  STEP-1 #1 (empty body, direct transport call):
    ``TestGetAndNoBodyRequests::test_post_with_empty_body_not_compressed``
    — verifies the middleware contract without traversing TCP. This file's
    ``TestEdgeCaseEmptyBodyWireLevel`` adds the wire-level confirmation.
  STEP-1 #3 (GET/no-body, direct transport call):
    ``TestGetAndNoBodyRequests::test_get_request_uncompressed`` — same
    pattern as above. This file's ``TestEdgeCaseGetNoBodyWireLevel`` adds
    the wire-level confirmation.
  STEP-1 #8 (config parsing, all 6 sub-cases):
    ``TestConfigParsing`` covers ``default`` / ``"true"`` / ``"false"`` /
    ``"1"`` / ``""`` / YAML ``None``. The ``config.yaml / ${ENV}``
    interpolation path is NOT in this suite — see
    ``TestEdgeCaseConfigYAMLInterpolation``.
  STEP-1 #2 (streaming with gzip, MockTransport only):
    ``TestStreamingWithGzipEnabled`` runs through ``MockTransport`` only.
    The mock-transport path can pass on a buggy transport that mutates
    only ``request._content`` (the property cache) without updating
    ``request.stream`` — see ``test_llm_request_gzip.py:1181-1196`` for
    the rationale. This file's ``TestEdgeCaseStreamingRoundTripWireLevel``
    closes the gap by driving a real LangChain + real socket server.

Wire-level discipline
---------------------

Wire-level assertions operate on bytes the SERVER read from the TCP
socket via ``BaseHTTPRequestHandler.rfile.read(content_length)`` — the
same pattern as ``tests/unit/test_llm_request_gzip.py::_LocalServer``.
Server capture fields:

  * ``body`` — raw bytes received on the socket (gzip-compressed if
    ``content_encoding_header == "gzip"``).
  * ``content_encoding_header`` — verbatim HTTP header.
  * ``content_length_header`` — verbatim HTTP header.
  * ``headers`` — full header dict.

Port discipline
--------------

Ports are picked from ``socket.bind((127.0.0.1, 0))`` then released; the
test binds a new HTTPServer to that exact port. The OS allocates an
ephemeral port (typically 10000-65535 — never 8088 [ensemble self-system]
or 8079 [dev daemon]). Server is shut down via
``HTTPServer.shutdown()`` + ``server_close()`` + thread ``join(timeout=1.0)``
in a ``finally`` block; each server owns its own thread, and the OS
reclaims the port on close. No port-scan / name-based kill.

Real-seam discipline
--------------------

All tests construct through the REAL production seam:

  * httpx wire tests: ``GzipRequestTransport(httpx.HTTPTransport())``.
  * LangChain tests: ``clean_llm_config(...)`` + ``ThinkingChatOpenAI(**cleaned)``.
  * Raw-SDK plumbing audit: the production ``_do_chat_call`` /
    ``_do_embed_call`` helpers + ``invoke_raw_with_failover`` are mocked
    ONLY to short-circuit before any real HTTP is attempted; the seam
    call ``resolve_gzip_client(...)`` itself is the real
    ``daemon.services.llm_gzip.resolve_gzip_client`` (re-bound to a
    recording mock).
"""

from __future__ import annotations

import asyncio
import gzip
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
from langchain_core.messages import HumanMessage


# ─── Shared helpers ────────────────────────────────────────────────────


def _large_user_message() -> str:
    """~1KB user message — gzip compresses well below the 20-byte framing overhead.

    Mirrors ``_LARGE_USER_MESSAGE`` in tests/unit/test_llm_request_gzip.py.
    Use whenever the wire test needs the body to actually shrink under gzip;
    for the skip-if-not-smaller test use a much smaller payload.
    """
    return (
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


# ─── _LocalServer: in-process HTTP server on ephemeral port ────────────


class _LocalServer:
    """In-process HTTP server that captures raw request bytes.

    Mirrors the feature file's ``_LocalServer``
    (``tests/unit/test_llm_request_gzip.py:829``) but extended with:

    * ``sse_chunks`` — when set, the POST handler decodes the request
      body (gzip-decoded if needed), inspects ``stream`` field, and returns
      SSE-shaped bytes for streaming requests or JSON for non-streaming.
      Same shape as a real OpenAI-compatible backend.
    * GET handler — captures request bytes (always empty) + headers.

    Per-instance capture list (each ``_LocalServer`` has its OWN list) so
    multiple server instances in the same test never cross-contaminate.
    """

    def __init__(
        self,
        sse_chunks: list[dict] | None = None,
        non_streaming_json: dict | None = None,
    ) -> None:
        self.captured: list[dict] = []
        # Find a free port: bind, get the port, release.
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        sock.close()
        self._sse_chunks = sse_chunks
        self._non_streaming_json = non_streaming_json or {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 1735689600,
            "model": "test-model",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "ok"},
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
            },
        }
        self._server = HTTPServer(("127.0.0.1", self.port), self._make_handler())
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name=f"gzip-edge-case-server-{self.port}",
            daemon=True,
        )
        self._thread.start()

    def _make_handler(self):
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 — http.server convention
                content_length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(content_length)
                outer.captured.append({
                    "body": body,
                    "headers": dict(self.headers),
                    "content_length_header": self.headers.get("Content-Length"),
                    "content_encoding_header": self.headers.get("Content-Encoding"),
                    "method": "POST",
                    "path": self.path,
                })
                # Determine response shape: if sse_chunks is set AND the
                # request body asks for stream=true, return SSE.
                should_stream = False
                if outer._sse_chunks is not None:
                    decoded_body = body
                    if self.headers.get("Content-Encoding") == "gzip":
                        try:
                            decoded_body = gzip.decompress(body)
                        except OSError:
                            decoded_body = body
                    try:
                        parsed = json.loads(decoded_body)
                        should_stream = bool(parsed.get("stream"))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        pass

                if should_stream:
                    sse = b""
                    for chunk in outer._sse_chunks:
                        sse += (
                            "data: " + json.dumps(chunk, separators=(",", ":")) + "\n\n"
                        ).encode("utf-8")
                    sse += b"data: [DONE]\n\n"
                    resp = sse
                    content_type = "text/event-stream"
                else:
                    resp = json.dumps(outer._non_streaming_json).encode("utf-8")
                    content_type = "application/json"

                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)

            def do_GET(self):  # noqa: N802
                # Read request headers, no body for GET.
                outer.captured.append({
                    "body": b"",
                    "headers": dict(self.headers),
                    "content_length_header": self.headers.get("Content-Length"),
                    "content_encoding_header": self.headers.get("Content-Encoding"),
                    "method": "GET",
                    "path": self.path,
                })
                # Reply with a minimal JSON for GET (model listing shape).
                resp = json.dumps({"object": "list", "data": []}).encode("utf-8")
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
        self._thread.join(timeout=1.0)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def captures(self) -> list[dict]:
        return self.captured


# ─── Autouse fixtures (mirror the feature file's discipline) ───────────


@pytest.fixture(autouse=True)
def _protect_class_vars_and_singleton():
    """Reset ``ThinkingChatOpenAI.default_request_gzip`` AND the gzip-client
    singleton around every test so a class-var leak from one test cannot
    poison the next. Mirrors ``tests/unit/test_llm_request_gzip.py:290``.
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


# ═══════════════════════════════════════════════════════════════════════
# Edge case 1: Empty POST body (wire-level)
# ═══════════════════════════════════════════════════════════════════════


class TestEdgeCaseEmptyBodyWireLevel:
    """Empty POST body (b"") must NOT be compressed on the wire.

    Wire-level confirmation that an empty POST body does NOT receive
    ``Content-Encoding: gzip`` and does NOT have its (empty) body bytes
    altered as they traverse the TCP socket. The existing
    ``TestGetAndNoBodyRequests::test_post_with_empty_body_not_compressed``
    verifies the middleware contract via direct ``transport.handle_request``
    call (no actual TCP traversal) — this test closes the wire-level gap.
    """

    def test_post_empty_body_no_compression_on_wire(self):
        from daemon.services.llm_gzip import GzipRequestTransport

        server = _LocalServer()
        try:
            with httpx.Client(
                transport=GzipRequestTransport(httpx.HTTPTransport())
            ) as client:
                resp = client.post(
                    f"{server.base_url}/v1/embeddings",
                    content=b"",
                )
            assert resp.status_code == 200, (
                f"server rejected the empty POST (status={resp.status_code})"
            )
            assert len(server.captures) == 1
            wire = server.captures[0]
            # Empty body: NO Content-Encoding on the wire.
            assert wire["content_encoding_header"] is None, (
                f"empty POST body must NOT carry Content-Encoding "
                f"(observed: {wire['content_encoding_header']!r})"
            )
            # Empty body: bytes are still empty.
            assert wire["body"] == b"", (
                f"empty POST body must remain empty on the wire "
                f"(observed {wire['body']!r})"
            )
            # Content-Length is "0" (or absent) — not the gzip-framed size.
            cl = wire["content_length_header"]
            assert cl in (None, "0"), (
                f"empty POST Content-Length must be 0 or absent "
                f"(observed: {cl!r})"
            )
        finally:
            server.close()


# ═══════════════════════════════════════════════════════════════════════
# Edge case 2: Tiny body — skip-if-not-smaller guard (wire-level)
# ═══════════════════════════════════════════════════════════════════════


class TestEdgeCaseTinyBodyWireLevel:
    """Tiny body must NOT be compressed when gzip would inflate it.

    The production ``_compress_request_body`` helper has a
    ``if len(compressed) >= len(original): return`` guard at
    ``daemon/services/llm_gzip.py:216``. Tiny payloads (~30 bytes)
    gzip-compress to ~50 bytes — gzip's per-stream header overhead
    (magic + flags + metadata) exceeds the savings. The transport must
    skip compression in that case so the wire payload stays at the
    original (smaller) size.
    """

    def test_tiny_body_skipped_when_compression_inflates(self):
        """~30-byte body: gzip would inflate it; transport must skip."""
        from daemon.services.llm_gzip import GzipRequestTransport

        server = _LocalServer()
        try:
            tiny_body = b'{"model":"x","messages":[{"role":"user","content":"hi"}]}'
            # Sanity: confirm the test premise — gzip DOES inflate this body.
            inflated_size = len(gzip.compress(tiny_body))
            assert inflated_size >= len(tiny_body), (
                f"test premise: tiny body must NOT shrink under gzip "
                f"(observed: original={len(tiny_body)}, "
                f"compressed={inflated_size})"
            )

            with httpx.Client(
                transport=GzipRequestTransport(httpx.HTTPTransport())
            ) as client:
                resp = client.post(
                    f"{server.base_url}/v1/chat/completions",
                    content=tiny_body,
                )
            assert resp.status_code == 200
            assert len(server.captures) == 1
            wire = server.captures[0]
            # Wire body must be the ORIGINAL (not re-compressed/inflated).
            assert wire["body"] == tiny_body, (
                f"tiny body: wire must carry the ORIGINAL uncompressed "
                f"bytes (server got {len(wire['body'])} bytes; "
                f"expected {len(tiny_body)} bytes). If the skip-if-not-"
                f"smaller guard failed, the body would be "
                f"gzip(tiny_body) which is ~{inflated_size} bytes."
            )
            # Content-Encoding must NOT be stamped (no compression applied).
            assert wire["content_encoding_header"] is None, (
                f"tiny body: wire must NOT carry Content-Encoding "
                f"(skip-if-not-smaller guard at llm_gzip.py:216; "
                f"observed: {wire['content_encoding_header']!r})"
            )
            # Content-Length matches the uncompressed body size.
            cl = int(wire["content_length_header"])
            assert cl == len(tiny_body), (
                f"tiny body: Content-Length ({cl}) must equal original "
                f"body size ({len(tiny_body)})"
            )
        finally:
            server.close()


# ═══════════════════════════════════════════════════════════════════════
# Edge case 3: GET / no-body round-trip (wire-level)
# ═══════════════════════════════════════════════════════════════════════


class TestEdgeCaseGetNoBodyWireLevel:
    """GET request round-trips through the gzip transport untouched.

    Wire-level confirmation that a GET request (no body) through the
    REAL gzip transport is transmitted cleanly to the server with NO
    ``Content-Encoding`` stamped and NO body bytes on the wire. The
    existing ``TestGetAndNoBodyRequests::test_get_request_uncompressed``
    verifies the middleware contract by calling
    ``transport.handle_request(req)`` directly (no TCP traversal) —
    this test confirms the wire transmission.
    """

    def test_get_request_uncompressed_on_wire(self):
        from daemon.services.llm_gzip import GzipRequestTransport

        server = _LocalServer()
        try:
            with httpx.Client(
                transport=GzipRequestTransport(httpx.HTTPTransport())
            ) as client:
                resp = client.get(f"{server.base_url}/v1/models")
            assert resp.status_code == 200
            assert len(server.captures) == 1
            wire = server.captures[0]
            # Method is GET, no body.
            assert wire.get("method") == "GET", (
                f"server must observe GET method "
                f"(observed: {wire.get('method')!r})"
            )
            assert wire["body"] == b"", (
                f"GET must NOT carry a body on the wire "
                f"(observed {wire['body']!r})"
            )
            # No Content-Encoding on a no-body request.
            assert wire["content_encoding_header"] is None, (
                f"GET must NOT carry Content-Encoding on the wire "
                f"(observed: {wire['content_encoding_header']!r})"
            )
        finally:
            server.close()


# ═══════════════════════════════════════════════════════════════════════
# Edge case 4: Double-compression guard (wire-level)
# ═══════════════════════════════════════════════════════════════════════


class TestEdgeCaseDoubleCompressionWireLevel:
    """Request already carrying Content-Encoding: gzip must NOT be re-compressed.

    Wire-level confirmation of the double-compression guard at
    ``daemon/services/llm_gzip.py:208``. A request that already carries
    ``Content-Encoding: gzip`` (e.g., after a retry that re-sends the
    SAME Request object through the transport) must NOT be re-compressed
    — the body bytes the server receives must decompress back to the
    ORIGINAL payload (NOT ``gzip(gzip(original))``), and the
    ``Content-Encoding`` header must remain a single ``gzip`` value
    (NOT ``gzip, gzip``).
    """

    def test_pre_gzipped_body_not_recompressed_on_wire(self):
        from daemon.services.llm_gzip import GzipRequestTransport

        server = _LocalServer()
        try:
            original_payload = (
                b'{"messages":[{"role":"user","content":"hello world"}]}'
            )
            already_gzipped = gzip.compress(original_payload)
            # Sanity: confirm the test premise — a doubly-gzipped body
            # would be substantially larger than the single-gzip body.
            double_gzipped = gzip.compress(already_gzipped)
            assert len(double_gzipped) > len(already_gzipped), (
                f"test premise: re-compression would inflate the body "
                f"(single={len(already_gzipped)}, double={len(double_gzipped)})"
            )

            with httpx.Client(
                transport=GzipRequestTransport(httpx.HTTPTransport())
            ) as client:
                # Drive a POST with already-CAE-gzip header + pre-gzipped body.
                resp = client.post(
                    f"{server.base_url}/v1/chat/completions",
                    content=already_gzipped,
                    headers={"Content-Encoding": "gzip"},
                )
            assert resp.status_code == 200
            assert len(server.captures) == 1
            wire = server.captures[0]
            # Content-Encoding must remain "gzip" (single value, not "gzip, gzip").
            ce = wire["content_encoding_header"]
            assert ce == "gzip", (
                f"double-compression guard: Content-Encoding must remain "
                f"'gzip' (observed: {ce!r}); 'gzip, gzip' would mean the "
                f"transport re-compressed an already-encoded body"
            )
            # Body must NOT be re-compressed — server received the
            # EXACT bytes we sent (no inflation from re-gzipping).
            assert wire["body"] == already_gzipped, (
                f"double-compression guard: server-received body must "
                f"equal the pre-gzipped input (got {len(wire['body'])} bytes; "
                f"expected {len(already_gzipped)} bytes). If the guard "
                f"failed, the body would be gzip(already_gzipped) which "
                f"is {len(double_gzipped)} bytes."
            )
            # The body gunzips back to the ORIGINAL payload.
            recovered = gzip.decompress(wire["body"])
            assert recovered == original_payload, (
                f"server-received body must gunzip to the ORIGINAL payload "
                f"(recovered {len(recovered)} bytes; expected "
                f"{len(original_payload)} bytes)"
            )
            # Content-Length matches the bytes on the wire.
            cl = int(wire["content_length_header"])
            assert cl == len(wire["body"]), (
                f"Content-Length ({cl}) must equal server-received "
                f"body length ({len(wire['body'])})"
            )
        finally:
            server.close()


# ═══════════════════════════════════════════════════════════════════════
# Edge case 6: Flag flip mid-process (clean_llm_config + singleton)
# ═══════════════════════════════════════════════════════════════════════


class TestEdgeCaseFlagFlipMidProcess:
    """Behavior of clean_llm_config + singleton lifecycle under flag flip.

    Documents the actual contract:

    * ``clean_llm_config`` reads ``ThinkingChatOpenAI.default_request_gzip``
      at the time of the call (``daemon/graph.py:2391``) — flipping the
      class var affects FUTURE constructions but not past ones.
    * The module-level singleton (``_gzip_sync_client``) is built LAZILY
      on the first call AFTER the flag flips to True.
    * The singleton is NEVER torn down when the flag flips back to False
      (production code never calls ``reset_cached_clients`` — that's a
      test-only seam). The singleton, once built, lives for the daemon's
      process lifetime.
    """

    def test_flag_off_clean_does_not_attach_http_client(self):
        """Flag=False initially: clean_llm_config returns cfg WITHOUT http_client."""
        from daemon.services import llm_gzip as gzip_mod
        from daemon.graph import ThinkingChatOpenAI, clean_llm_config

        gzip_mod.reset_cached_clients()
        original = ThinkingChatOpenAI.default_request_gzip
        try:
            ThinkingChatOpenAI.default_request_gzip = False
            cleaned = clean_llm_config({
                "model": "test",
                "api_key": "test",
                "base_url": "https://test.local/v1",
            })
            assert "http_client" not in cleaned, (
                "flag=False: clean_llm_config must NOT attach http_client "
                "(zero-behavior-change contract)"
            )
            assert "http_async_client" not in cleaned, (
                "flag=False: clean_llm_config must NOT attach http_async_client"
            )
            # Singleton must NOT have been built on the disabled path.
            assert gzip_mod._gzip_sync_client is None, (
                "flag=False: singleton must not be built "
                "(disabled path is zero-behavior-change)"
            )
            assert gzip_mod._gzip_async_client is None
        finally:
            ThinkingChatOpenAI.default_request_gzip = original
            gzip_mod.reset_cached_clients()

    def test_flag_on_clean_attaches_singleton(self):
        """Flag=True: clean_llm_config attaches the singleton gzip client."""
        from daemon.services import llm_gzip as gzip_mod
        from daemon.graph import ThinkingChatOpenAI, clean_llm_config

        gzip_mod.reset_cached_clients()
        original = ThinkingChatOpenAI.default_request_gzip
        try:
            ThinkingChatOpenAI.default_request_gzip = True
            cleaned = clean_llm_config({
                "model": "test",
                "api_key": "test",
                "base_url": "https://test.local/v1",
            })
            assert "http_client" in cleaned, (
                "flag=True: clean_llm_config must attach http_client"
            )
            assert "http_async_client" in cleaned, (
                "flag=True: clean_llm_config must attach http_async_client"
            )
            # The attached client IS the module-level singleton.
            assert cleaned["http_client"] is gzip_mod._gzip_sync_client, (
                "flag=True: clean_llm_config must attach the singleton "
                "instance (connection-pool consolidation contract)"
            )
            assert cleaned["http_async_client"] is gzip_mod._gzip_async_client, (
                "flag=True: clean_llm_config must attach the singleton "
                "async instance"
            )
        finally:
            ThinkingChatOpenAI.default_request_gzip = original
            gzip_mod.reset_cached_clients()

    def test_flag_flip_off_after_singleton_built_does_not_detach(self):
        """Flag=False AFTER the singleton was built: future cleans do NOT
        attach ``http_client``, but the singleton still lives in memory
        (production never tears it down — it's process-lifetime).

        Documents the contract: flag flips affect FUTURE construction,
        not PAST state. The singleton, once built, persists.
        """
        from daemon.services import llm_gzip as gzip_mod
        from daemon.graph import ThinkingChatOpenAI, clean_llm_config

        gzip_mod.reset_cached_clients()
        original = ThinkingChatOpenAI.default_request_gzip
        try:
            # Phase 1: flag=True builds the singleton.
            ThinkingChatOpenAI.default_request_gzip = True
            cleaned_on = clean_llm_config({
                "model": "test",
                "api_key": "test",
                "base_url": "https://test.local/v1",
            })
            assert "http_client" in cleaned_on
            singleton_after_phase1 = gzip_mod._gzip_sync_client
            assert singleton_after_phase1 is not None, (
                "phase 1: singleton must be built on first flag=True call"
            )

            # Phase 2: flag=False — singleton must STILL exist (production
            # never calls reset_cached_clients; the singleton is
            # process-lifetime).
            ThinkingChatOpenAI.default_request_gzip = False
            cleaned_off = clean_llm_config({
                "model": "test",
                "api_key": "test",
                "base_url": "https://test.local/v1",
            })
            assert "http_client" not in cleaned_off, (
                "flag=False: subsequent clean_llm_config must NOT "
                "attach http_client (class var is the gate at call time)"
            )
            assert "http_async_client" not in cleaned_off
            # Singleton still lives in memory (process-lifetime).
            assert gzip_mod._gzip_sync_client is singleton_after_phase1, (
                "flag=False: singleton must persist (process-lifetime) "
                "even after the gate flips off — the flag flip affects "
                "FUTURE construction, not PAST state"
            )
        finally:
            ThinkingChatOpenAI.default_request_gzip = original
            gzip_mod.reset_cached_clients()


# ═══════════════════════════════════════════════════════════════════════
# Edge case 8: config.yaml / ${OPENAI_REQUEST_GZIP:-default} interpolation
# ═══════════════════════════════════════════════════════════════════════


class TestEdgeCaseConfigYAMLInterpolationPath:
    """config.yaml interpolation path: ``request_gzip: ${OPENAI_REQUEST_GZIP:-false}``.

    Sub-case missing from the existing ``TestConfigParsing``: the YAML
    interpolation route via ``daemon.config.substitute_env_vars``. The
    ``tests/unit/test_llm_request_gzip.py::TestConfigParsing`` covers the
    direct ``LLMConfig(_env_file=None)`` paths (default / true / false /
    1 / empty / None) but not the full YAML → substitute → ``LLMConfig``
    round-trip that the actual daemon goes through at boot
    (``daemon/__main__.py::main`` calls ``load_config(...)`` which
    invokes ``substitute_env_vars`` then ``LLMConfig(**config_dict)``).
    """

    def _write_yaml(self, path, request_gzip_line: str) -> None:
        path.write_text(
            "llm:\n"
            "  model: test\n"
            "  base_url: http://test.local/v1\n"
            "  api_key: test\n"
            f"  {request_gzip_line}\n"
            "daemon:\n"
            "  host: 127.0.0.1\n"
            "  port: 8088\n"
        )

    def test_yaml_interpolation_with_env_true(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OPENAI_REQUEST_GZIP=true → interpolation → request_gzip=True."""
        monkeypatch.setenv("OPENAI_REQUEST_GZIP", "true")
        config_yaml = tmp_path / "config.yaml"
        self._write_yaml(config_yaml, "request_gzip: ${OPENAI_REQUEST_GZIP:-false}")

        from daemon.config import load_config

        cfg = load_config(str(config_yaml))
        assert cfg.llm.request_gzip is True, (
            f"config.yaml ${{OPENAI_REQUEST_GZIP}} interpolation with "
            f"env='true' must yield request_gzip=True "
            f"(got: {cfg.llm.request_gzip!r})"
        )

    def test_yaml_interpolation_unset_uses_default(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OPENAI_REQUEST_GZIP unset → interpolation default → request_gzip=False."""
        monkeypatch.delenv("OPENAI_REQUEST_GZIP", raising=False)
        config_yaml = tmp_path / "config.yaml"
        self._write_yaml(config_yaml, "request_gzip: ${OPENAI_REQUEST_GZIP:-false}")

        from daemon.config import load_config

        cfg = load_config(str(config_yaml))
        assert cfg.llm.request_gzip is False, (
            f"config.yaml ${{OPENAI_REQUEST_GZIP}} interpolation with "
            f"env unset must fall back to the ':-false' default "
            f"(got: {cfg.llm.request_gzip!r})"
        )

    def test_yaml_interpolation_with_env_false_overrides_default(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OPENAI_REQUEST_GZIP=false → request_gzip=False (negative override).

        Uses ``:-true`` as the YAML default to prove the env value
        overrides the YAML-side default (the operator can explicitly
        disable even if the YAML template defaults to true).
        """
        monkeypatch.setenv("OPENAI_REQUEST_GZIP", "false")
        config_yaml = tmp_path / "config.yaml"
        self._write_yaml(config_yaml, "request_gzip: ${OPENAI_REQUEST_GZIP:-true}")

        from daemon.config import load_config

        cfg = load_config(str(config_yaml))
        assert cfg.llm.request_gzip is False, (
            f"OPENAI_REQUEST_GZIP='false' must override the ':-true' "
            f"YAML default (got: {cfg.llm.request_gzip!r})"
        )

    def test_yaml_interpolation_with_env_one(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OPENAI_REQUEST_GZIP=1 → request_gzip=True (truthy-coercion path)."""
        monkeypatch.setenv("OPENAI_REQUEST_GZIP", "1")
        config_yaml = tmp_path / "config.yaml"
        self._write_yaml(config_yaml, "request_gzip: ${OPENAI_REQUEST_GZIP:-false}")

        from daemon.config import load_config

        cfg = load_config(str(config_yaml))
        assert cfg.llm.request_gzip is True, (
            f"OPENAI_REQUEST_GZIP='1' must coerce to request_gzip=True "
            f"via the YAML interpolation path "
            f"(got: {cfg.llm.request_gzip!r})"
        )


# ═══════════════════════════════════════════════════════════════════════
# ADDENDUM #1 + #2: Streaming round-trip wire-level + singleton-injection
# ═══════════════════════════════════════════════════════════════════════


class TestEdgeCaseStreamingRoundTripWireLevel:
    """Streaming + gzip ON through REAL LangChain + REAL socket server.

    Closes TWO gaps from the mock-fidelity audit:

    ADDENDUM #1: the existing ``TestStreamingWithGzipEnabled`` runs
    streaming through ``MockTransport`` only — it never confirms the
    bytes that ACTUALLY hit the wire (the ``_content`` / ``stream``
    split bug could let it pass with an uncompressed wire body).

    ADDENDUM #2: the existing wire-level tests (``TestWireLevelRealSocketServer``)
    use ``streaming=False``. The singleton-injection branch at
    ``daemon/graph.py:2390-2404`` is therefore untested with
    ``streaming=True`` — this test exercises both at once.

    Drives a real ``ThinkingChatOpenAI.invoke()`` (built through
    ``clean_llm_config`` WITHOUT explicit ``http_client`` — so the
    singleton-injection branch fires) with ``streaming=True`` and
    ``request_gzip=True``, against a real socket server returning
    SSE-shaped ``data:`` frames + ``[DONE]``.

    Asserts:
      (a) Server-received request bytes are gzip-compressed (gzip magic
          + ``Content-Encoding: gzip`` + ``Content-Length`` matches
          compressed size + gunzip round-trip).
      (b) SSE response chunks are consumed through the real LangChain
          decode path to a final AIMessage with the expected content.
    """

    def test_streaming_round_trip_with_gzip_singleton_injection(self):
        # SSE-shaped chunks for the streaming response. Same shape as
        # ``tests/unit/test_llm_streaming_wire_verify.py`` — verified
        # against the live OpenAI-compatible backend.
        sse_chunks = [
            {
                "id": "s1", "object": "chat.completion.chunk", "created": 1,
                "model": "test-model",
                "choices": [{"index": 0, "delta": {"role": "assistant"}}],
            },
            {
                "id": "s2", "object": "chat.completion.chunk", "created": 1,
                "model": "test-model",
                "choices": [{"index": 0, "delta": {"content": "Hello"}}],
            },
            {
                "id": "s3", "object": "chat.completion.chunk", "created": 1,
                "model": "test-model",
                "choices": [{"index": 0, "delta": {"content": " world"}}],
            },
            {
                "id": "s4", "object": "chat.completion.chunk", "created": 1,
                "model": "test-model",
                "choices": [
                    {"index": 0, "delta": {}, "finish_reason": "stop"}
                ],
                "usage": {
                    "prompt_tokens": 5,
                    "completion_tokens": 2,
                    "total_tokens": 7,
                },
            },
        ]

        server = _LocalServer(sse_chunks=sse_chunks)
        try:
            from daemon.graph import ThinkingChatOpenAI, clean_llm_config

            saved = ThinkingChatOpenAI.default_request_gzip
            try:
                # Singleton-injection path: NO explicit http_client /
                # http_async_client in cfg — so the ``if ``http_client``
                # not in cleaned`` branch at daemon/graph.py:2390-2404
                # fires and attaches the singleton gzip client.
                ThinkingChatOpenAI.default_request_gzip = True
                cleaned = clean_llm_config({
                    "model": "test-model",
                    "api_key": "test-key",
                    "base_url": server.base_url,
                    "streaming": True,
                    # NOTE: NO "http_client" / "http_async_client" here.
                })
                llm = ThinkingChatOpenAI(**cleaned)
                # Drive a real invoke with a large body (gzip wins).
                msg = llm.invoke(
                    [HumanMessage(content=_large_user_message())]
                )
            finally:
                ThinkingChatOpenAI.default_request_gzip = saved

            # ── (a) wire-level: request was gzipped ──
            assert len(server.captures) == 1
            wire = server.captures[0]
            assert wire["content_encoding_header"] == "gzip", (
                f"streaming + gzip ON: wire Content-Encoding must be "
                f"'gzip' (observed: {wire['content_encoding_header']!r})"
            )
            cl_header = int(wire["content_length_header"])
            compressed_size = len(wire["body"])
            assert cl_header == compressed_size, (
                f"streaming + gzip ON: Content-Length ({cl_header}) must "
                f"equal compressed body size ({compressed_size}) — "
                f"a mismatch would cause h11 to abort the request"
            )
            # gzip magic check (0x1f8b at offset 0).
            assert wire["body"][:2] == b"\x1f\x8b", (
                f"wire body must start with gzip magic 0x1f8b "
                f"(observed first 2 bytes: {wire['body'][:2]!r})"
            )
            # Decompression round-trip — must yield the JSON payload
            # with stream=True and the original user message.
            recovered = gzip.decompress(wire["body"])
            payload = json.loads(recovered)
            assert payload.get("stream") is True, (
                f"streaming request body must carry stream=True on the "
                f"wire (observed: {payload!r})"
            )
            assert payload["messages"][0]["content"] == _large_user_message(), (
                f"gunzipped body must contain the original user message "
                f"(observed content: {payload['messages'][0]['content']!r})"
            )

            # ── (b) response-level: SSE chunks aggregated correctly ──
            assert msg.content == "Hello world", (
                f"streaming SSE response must aggregate through the "
                f"real LangChain decode path to AIMessage "
                f"(got {msg.content!r})"
            )
        finally:
            server.close()


# ═══════════════════════════════════════════════════════════════════════
# ADDENDUM #3: Plumbing audit for the 4 raw-SDK ``resolve_gzip_client`` sites
# ═══════════════════════════════════════════════════════════════════════


def _make_resolve_gzip_recorder() -> tuple[Any, list[bool]]:
    """Build a recording mock that replaces ``resolve_gzip_client``.

    Records every call's ``enabled`` argument (as a bool). Returns a
    sentinel ``httpx.Client``-like MagicMock so the calling code path
    proceeds through the inner ``_do_chat_call`` / ``_do_embed_call``
    stubs without crashing.

    Use as:

        recorder, calls = _make_resolve_gzip_recorder()
        with patch("daemon.services.llm_gzip.resolve_gzip_client", recorder):
            ... drive the service method ...
        assert calls == [True]  # or [False]
    """
    calls: list[bool] = []

    def recorder(enabled: bool):
        calls.append(bool(enabled))
        return MagicMock(name=f"gzip-client-stub(enabled={bool(enabled)})")

    return recorder, calls


class TestEdgeCaseResolveGzipClientPlumbing:
    """Each of the 4 production call sites invokes ``resolve_gzip_client``
    with the correct ``enabled`` argument derived from ``llm_config``.

    ADDENDUM #3 (mock-fidelity audit gap): monkeypatch
    ``daemon.services.llm_gzip.resolve_gzip_client`` with a recording
    mock and drive each of the 4 call sites to verify the boolean
    argument correctly reflects the service's ``llm_config['request_gzip']``:

      * ``daemon/services/skill_search_service.py:797-801``
        inside ``SkillSearchService._llm_select`` (line 706)
      * ``daemon/services/skill_embedding_service.py:347-356``
        inside ``SkillEmbeddingService.generate_trigger_queries`` (line 282)
      * ``daemon/services/skill_embedding_service.py:429-433``
        inside ``SkillEmbeddingService.embed_text`` (line 384)
      * ``daemon/services/skill_evolution_service.py:1550-1558``
        inside ``SkillEvolutionService._call_llm`` (line 1507)

    Unit-level — no real LLM / socket needed. The deeper layers
    (``invoke_raw_with_failover`` + ``_do_chat_call`` / ``_do_embed_call``)
    are mocked to short-circuit BEFORE any HTTP is attempted; the seam
    call happens BEFORE the inner HTTP call so the recording mock fires
    unconditionally.
    """

    def test_skill_search_service_llm_select_calls_resolve_gzip_client(self):
        """``SkillSearchService._llm_select`` invokes ``resolve_gzip_client``
        with the boolean derived from ``llm_config['request_gzip']``."""
        from daemon.services.skill_search_service import SkillSearchService
        from daemon.services.skill_embedding_service import (
            SkillEmbeddingService as _Embedding,  # spec only — for the constructor
        )

        skill = MagicMock()
        skill.name = "test-skill"
        skill.description = "Test description"
        skill.content = "Test content"

        # ── True case ──
        recorder_true, calls_true = _make_resolve_gzip_recorder()
        skill_search_true = SkillSearchService(
            skill_repo=MagicMock(),
            embedding_repo=MagicMock(),
            embedding_service=MagicMock(spec=_Embedding),
            llm_config={
                "model": "test",
                "base_url": "http://test.local/v1",
                "api_key": "test",
                "request_gzip": True,
            },
            config=MagicMock(),
        )
        with patch(
            "daemon.services.llm_gzip.resolve_gzip_client", recorder_true
        ), patch(
            "daemon.services.skill_search_service.invoke_raw_with_failover"
        ) as mock_failover, patch(
            "daemon.services.skill_search_service._do_chat_call"
        ):
            # Return a fake response that satisfies the parser.
            fake = MagicMock()
            fake.choices = [MagicMock(message=MagicMock(content="{}"))]
            mock_failover.return_value = fake
            try:
                asyncio.run(
                    skill_search_true._llm_select(
                        "query",
                        [(skill, 0.5)],
                        client=None,
                    )
                )
            except (ValueError, Exception):
                # Parse may fail on the stub response — that's OK; we
                # only care about the seam call which fires BEFORE the
                # inner HTTP layer is invoked.
                pass

        assert len(calls_true) >= 1, (
            "resolve_gzip_client must be called by SkillSearchService."
            "_llm_select when client=None (production path)"
        )
        assert all(c is True for c in calls_true), (
            f"with llm_config['request_gzip']=True, _llm_select must "
            f"invoke resolve_gzip_client(True); observed calls={calls_true}"
        )

        # ── False case ──
        recorder_false, calls_false = _make_resolve_gzip_recorder()
        skill_search_false = SkillSearchService(
            skill_repo=MagicMock(),
            embedding_repo=MagicMock(),
            embedding_service=MagicMock(spec=_Embedding),
            llm_config={
                "model": "test",
                "base_url": "http://test.local/v1",
                "api_key": "test",
                "request_gzip": False,
            },
            config=MagicMock(),
        )
        with patch(
            "daemon.services.llm_gzip.resolve_gzip_client", recorder_false
        ), patch(
            "daemon.services.skill_search_service.invoke_raw_with_failover"
        ) as mock_failover, patch(
            "daemon.services.skill_search_service._do_chat_call"
        ):
            fake = MagicMock()
            fake.choices = [MagicMock(message=MagicMock(content="{}"))]
            mock_failover.return_value = fake
            try:
                asyncio.run(
                    skill_search_false._llm_select(
                        "query",
                        [(skill, 0.5)],
                        client=None,
                    )
                )
            except (ValueError, Exception):
                pass

        assert len(calls_false) >= 1, (
            "resolve_gzip_client must be called by _llm_select"
        )
        assert all(c is False for c in calls_false), (
            f"with llm_config['request_gzip']=False, _llm_select must "
            f"invoke resolve_gzip_client(False); observed "
            f"calls={calls_false}"
        )

    def test_skill_embedding_service_generate_trigger_queries(self):
        """``SkillEmbeddingService.generate_trigger_queries`` invokes
        ``resolve_gzip_client`` with the boolean derived from
        ``llm_config['request_gzip']``."""
        from daemon.services.skill_embedding_service import SkillEmbeddingService

        skill = MagicMock()
        skill.name = "test-skill"
        skill.description = "Test description"
        skill.content = "Test content"

        # Embedding-service config: needs ``embedding_model`` for the
        # chat-model resolver fallback. Use a real dict-shaped config
        # so ``getattr(self.config, "embedding_model", None)`` works.
        config = MagicMock()
        config.embedding_model = "text-embedding-3-small"

        # ── True case ──
        recorder_true, calls_true = _make_resolve_gzip_recorder()
        svc_true = SkillEmbeddingService(
            config=config,
            embedding_repo=MagicMock(),
            llm_config={
                "model": "test",
                "base_url": "http://test.local/v1",
                "api_key": "test",
                "request_gzip": True,
            },
        )
        with patch(
            "daemon.services.llm_gzip.resolve_gzip_client", recorder_true
        ), patch(
            "daemon.services.skill_embedding_service.invoke_raw_with_failover"
        ) as mock_failover, patch(
            "daemon.services.skill_embedding_service._do_chat_call"
        ):
            fake = MagicMock()
            fake.choices = [MagicMock(message=MagicMock(content='["q1","q2","q3"]'))]
            mock_failover.return_value = fake
            try:
                asyncio.run(svc_true.generate_trigger_queries(skill))
            except (ValueError, Exception):
                pass

        assert len(calls_true) >= 1, (
            "resolve_gzip_client must be called by "
            "SkillEmbeddingService.generate_trigger_queries"
        )
        assert all(c is True for c in calls_true), (
            f"with llm_config['request_gzip']=True, "
            f"generate_trigger_queries must invoke "
            f"resolve_gzip_client(True); observed calls={calls_true}"
        )

        # ── False case ──
        recorder_false, calls_false = _make_resolve_gzip_recorder()
        svc_false = SkillEmbeddingService(
            config=config,
            embedding_repo=MagicMock(),
            llm_config={
                "model": "test",
                "base_url": "http://test.local/v1",
                "api_key": "test",
                "request_gzip": False,
            },
        )
        with patch(
            "daemon.services.llm_gzip.resolve_gzip_client", recorder_false
        ), patch(
            "daemon.services.skill_embedding_service.invoke_raw_with_failover"
        ) as mock_failover, patch(
            "daemon.services.skill_embedding_service._do_chat_call"
        ):
            fake = MagicMock()
            fake.choices = [MagicMock(message=MagicMock(content='["q1","q2","q3"]'))]
            mock_failover.return_value = fake
            try:
                asyncio.run(svc_false.generate_trigger_queries(skill))
            except (ValueError, Exception):
                pass

        assert len(calls_false) >= 1
        assert all(c is False for c in calls_false), (
            f"with llm_config['request_gzip']=False, "
            f"generate_trigger_queries must invoke "
            f"resolve_gzip_client(False); observed calls={calls_false}"
        )

    def test_skill_embedding_service_embed_text(self):
        """``SkillEmbeddingService.embed_text`` invokes ``resolve_gzip_client``
        with the boolean derived from ``llm_config['request_gzip']``."""
        from daemon.services.skill_embedding_service import SkillEmbeddingService

        config = MagicMock()
        config.embedding_model = "text-embedding-3-small"
        config.embedding_dimensions = 4
        # Embedding-endpoint override absent → uses llm_config base_url.

        # ── True case ──
        recorder_true, calls_true = _make_resolve_gzip_recorder()
        svc_true = SkillEmbeddingService(
            config=config,
            embedding_repo=MagicMock(),
            llm_config={
                "model": "test",
                "base_url": "http://test.local/v1",
                "api_key": "test",
                "request_gzip": True,
            },
        )
        with patch(
            "daemon.services.llm_gzip.resolve_gzip_client", recorder_true
        ), patch(
            "daemon.services.skill_embedding_service.invoke_raw_with_failover"
        ) as mock_failover, patch(
            "daemon.services.skill_embedding_service._do_embed_call"
        ):
            # Return a fake embedding response.
            item = MagicMock()
            item.embedding = [0.1, 0.2, 0.3, 0.4]
            fake_response = MagicMock()
            fake_response.data = [item]
            mock_failover.return_value = fake_response
            try:
                asyncio.run(svc_true.embed_text("hello world"))
            except (ValueError, Exception):
                pass

        assert len(calls_true) >= 1, (
            "resolve_gzip_client must be called by "
            "SkillEmbeddingService.embed_text"
        )
        assert all(c is True for c in calls_true), (
            f"with llm_config['request_gzip']=True, embed_text must "
            f"invoke resolve_gzip_client(True); observed "
            f"calls={calls_true}"
        )

        # ── False case ──
        recorder_false, calls_false = _make_resolve_gzip_recorder()
        svc_false = SkillEmbeddingService(
            config=config,
            embedding_repo=MagicMock(),
            llm_config={
                "model": "test",
                "base_url": "http://test.local/v1",
                "api_key": "test",
                "request_gzip": False,
            },
        )
        with patch(
            "daemon.services.llm_gzip.resolve_gzip_client", recorder_false
        ), patch(
            "daemon.services.skill_embedding_service.invoke_raw_with_failover"
        ) as mock_failover, patch(
            "daemon.services.skill_embedding_service._do_embed_call"
        ):
            item = MagicMock()
            item.embedding = [0.1, 0.2, 0.3, 0.4]
            fake_response = MagicMock()
            fake_response.data = [item]
            mock_failover.return_value = fake_response
            try:
                asyncio.run(svc_false.embed_text("hello world"))
            except (ValueError, Exception):
                pass

        assert len(calls_false) >= 1
        assert all(c is False for c in calls_false), (
            f"with llm_config['request_gzip']=False, embed_text must "
            f"invoke resolve_gzip_client(False); observed "
            f"calls={calls_false}"
        )

    def test_skill_evolution_service_call_llm(self):
        """``SkillEvolutionService._call_llm`` invokes ``resolve_gzip_client``
        with the boolean derived from ``llm_config['request_gzip']``."""
        from daemon.services.skill_evolution_service import SkillEvolutionService

        config = MagicMock()
        # All resolvers fall through to llm_config when these are None.

        # ── True case ──
        recorder_true, calls_true = _make_resolve_gzip_recorder()
        svc_true = SkillEvolutionService(
            skill_repo=MagicMock(),
            lineage_repo=MagicMock(),
            usage_repo=MagicMock(),
            embedding_service=MagicMock(),
            metrics_service=MagicMock(),
            ab_test_repo=MagicMock(),
            config=config,
            llm_config={
                "model": "test",
                "base_url": "http://test.local/v1",
                "api_key": "test",
                "request_gzip": True,
            },
        )
        with patch(
            "daemon.services.llm_gzip.resolve_gzip_client", recorder_true
        ), patch(
            "daemon.services.skill_evolution_service.invoke_raw_with_failover"
        ) as mock_failover, patch(
            "daemon.services.skill_evolution_service._do_chat_call"
        ):
            fake = MagicMock()
            fake.choices = [MagicMock(message=MagicMock(content="ok"))]
            mock_failover.return_value = fake
            try:
                asyncio.run(svc_true._call_llm("test prompt"))
            except (ValueError, Exception):
                pass

        assert len(calls_true) >= 1, (
            "resolve_gzip_client must be called by "
            "SkillEvolutionService._call_llm"
        )
        assert all(c is True for c in calls_true), (
            f"with llm_config['request_gzip']=True, _call_llm must "
            f"invoke resolve_gzip_client(True); observed "
            f"calls={calls_true}"
        )

        # ── False case ──
        recorder_false, calls_false = _make_resolve_gzip_recorder()
        svc_false = SkillEvolutionService(
            skill_repo=MagicMock(),
            lineage_repo=MagicMock(),
            usage_repo=MagicMock(),
            embedding_service=MagicMock(),
            metrics_service=MagicMock(),
            ab_test_repo=MagicMock(),
            config=config,
            llm_config={
                "model": "test",
                "base_url": "http://test.local/v1",
                "api_key": "test",
                "request_gzip": False,
            },
        )
        with patch(
            "daemon.services.llm_gzip.resolve_gzip_client", recorder_false
        ), patch(
            "daemon.services.skill_evolution_service.invoke_raw_with_failover"
        ) as mock_failover, patch(
            "daemon.services.skill_evolution_service._do_chat_call"
        ):
            fake = MagicMock()
            fake.choices = [MagicMock(message=MagicMock(content="ok"))]
            mock_failover.return_value = fake
            try:
                asyncio.run(svc_false._call_llm("test prompt"))
            except (ValueError, Exception):
                pass

        assert len(calls_false) >= 1
        assert all(c is False for c in calls_false), (
            f"with llm_config['request_gzip']=False, _call_llm must "
            f"invoke resolve_gzip_client(False); observed "
            f"calls={calls_false}"
        )
