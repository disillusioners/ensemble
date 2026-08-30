"""Outbound LLM HTTP request-body gzip compression middleware.

Opt-in via ``LLMConfig.request_gzip`` (``OPENAI_REQUEST_GZIP`` env var,
default false). When enabled, every LLM HTTP request body is
gzip-compressed on the wire, ``Content-Encoding: gzip`` is stamped,
and ``Content-Length`` is auto-corrected to the compressed size.

Scope
-----
ONLY outbound LLM traffic. The compression sits on the httpx transport
inside the LLM client's own httpx.Client/AsyncClient — NEVER on the
Plane HTTP client (``daemon/clients/plane_http_client.py``), MCP
transports, source-adapter HTTP, or any other non-LLM traffic. See
the integration points below for the call sites that attach a
gzip-enabled client.

Response handling is COMPLETELY untouched — we never set
``Accept-Encoding: gzip`` on the response side, never decompress
response bodies, never alter streaming-response semantics. The
compression is request-side only and only touches the bytes going
out, not the bytes coming back.

Construction seam (additive; no refactor)
-----------------------------------------
For LangChain sites (``ThinkingChatOpenAI``), the seam is
``daemon.graph.clean_llm_config`` — same pattern as the
``streaming`` / ``stream_usage`` flag injection: when the operator
knob is on, ``clean_llm_config`` attaches ``http_client`` /
``http_async_client`` kwargs to the ChatOpenAI constructor call so
the langchain-openai client wraps the gzip transport around its
underlying httpx transport.

For raw-SDK sites (skill search / embedding / evolution, all of
which route through ``daemon.services.llm_failover`` and build
``openai.OpenAI`` / ``openai.AsyncOpenAI`` per attempt), the seam
is the four ``_do_chat_call`` / ``_do_embed_call`` module-level
helpers in those services — they call ``make_gzip_httpx_client()``
when the LLMConfig flag is on and pass the resulting client as
``http_client=`` to ``openai.OpenAI(...)``.

When the flag is OFF: no transport is attached, no client is built,
no headers are injected. The daemon's LLM traffic is byte-identical
to the pre-feature state.

Hard constraints honored
------------------------
1. Default DISABLED — zero behavior change unless the env var enables it.
   When disabled: pure passthrough, no custom transport, no headers
   injected. Verified by ``tests/unit/test_llm_request_gzip.py`` —
   ``test_disabled_passthrough_*``.
2. Response handling untouched — no Accept-Encoding behavior changes,
   no response-body decompression, streaming semantics unchanged.
3. Only outbound LLM traffic is compressed — the Plane HTTP client
   and any other non-LLM traffic constructs its own plain
   ``httpx.AsyncClient`` and is unaffected.
4. Additive — no refactor of other layers; ``clean_llm_config`` gains
   one extra additive branch.
5. Stale Content-Length hazard — after replacing the request body,
   ``Content-Length`` is set to ``len(compressed)`` so the on-wire
   length header matches the on-wire body bytes (a mismatch would
   cause HTTP/1.1 400 from strict proxies / some LLM front-ends).
6. Double-compression guard — the transport checks for an existing
   ``content-encoding`` header BEFORE compressing. httpx re-sends the
   SAME ``Request`` object through the transport on redirects /
   retries (the canonical rewind pattern in httpx's
   ``BaseTransport.handle_request`` docs); without this guard a
   compressed-then-redirected request would get compressed twice,
   yielding ``Content-Encoding: gzip, gzip`` and a body that the
   proxy cannot decode.

Why a custom transport (not an event hook)
------------------------------------------
httpx has two customization surfaces:

* ``httpx.Client(event_hooks=...)`` — runs hooks AFTER the request
  is fully serialized but BEFORE the transport sends. We'd have to
  re-serialize the body to mutate the wire bytes, which means
  re-implementing the request body iterator logic and re-stamping
  Content-Length manually. Brittle.
* ``httpx.Client(transport=...)`` — wraps the inner transport and
  sees the exact ``httpx.Request`` object httpx is about to send.
  Mutating ``request._content`` + ``request.headers`` here is the
  documented seam; the transport is allowed to do per-request work
  before delegating to the inner transport. This is the right
  surface for "modify the bytes going out, do nothing with the
  bytes coming back".
"""

from __future__ import annotations

import gzip
import threading

import httpx

# ``httpx._content.ByteStream`` is a PRIVATE httpx API (no public
# equivalent exposes ``request.stream`` for re-binding). Pinned on
# httpx 0.28.1 — verified available and behaves as documented
# (a thin ``ByteStream`` wrapper around ``bytes`` that httpx's
# transport reads on send). If we ever bump httpx, re-verify that
# the transport still serializes body bytes from ``request.stream``
# (the historical contract since httpx 0.x — see ``httpx._content``
# module). The compression is request-side only; we never modify the
# response stream.
from httpx._content import ByteStream

# Methods that carry a request body in the OpenAI / OpenAI-compatible
# chat / embeddings / completions API surface. The transport skips
# body compression for any other method (GET, HEAD, DELETE, OPTIONS)
# — those have no body to compress, and ``request.content`` is the
# empty bytes string ``b""`` for them.
_METHODS_WITH_BODY = frozenset({"POST", "PUT", "PATCH"})

# Module-level singletons (lazily created on first use) — sharing one
# gzip-enabled httpx.Client / AsyncClient across all LLM construction
# sites (LangChain chat, raw-SDK skill services) keeps the connection
# pool consolidated. The httpx client is transport-only (the openai
# SDK passes absolute URL on every call; per-request timeout comes
# via ``request.extensions['timeout']`` and takes precedence over
# the client's Timeout fallback), so it composes correctly with the
# wrapping langchain / openai clients. ``sync httpx.Client`` is
# thread-safe for concurrent requests, so the singleton is safe to
# share across ``asyncio.to_thread`` call sites. We re-build the
# singletons across test runs via ``reset_cached_clients`` —
# production code never calls that.
_gzip_sync_client: httpx.Client | None = None
_gzip_async_client: httpx.AsyncClient | None = None
# Module-level lock guarding the singleton builds. Under the GIL the
# None-check + assignment IS atomic for a single attribute write, but
# NOT for the check-then-build pattern (two threads can each see
# ``None`` and both construct their own client before either assignment
# becomes visible to the other — the race the regression test for W1
# surfaced empirically with 10 concurrent threads producing 10 distinct
# AsyncClient instances). Double-checked locking keeps the hot path
# lock-free once warmed up while serializing the actual construction.
_gzip_lock = threading.Lock()

# Shared httpx configuration for both gzip-enabled client builders.
# Values match the OpenAI SDK's built-in ``DefaultHttpxClient`` /
# ``DefaultAsyncHttpxClient`` defaults (``openai._base_client``) so
# the gzip wrapper is a drop-in replacement on the disabled path —
# no silent divergence in connection-pool sizing, timeout, or
# redirect behavior.
#
# * ``Limits(max_connections=1000, max_keepalive_connections=100)``
#   matches the SDK hardcoded default; httpx itself defaults to
#   ``max_connections=100`` which would silently throttle the LLM
#   path under load.
# * ``Timeout(timeout=600.0, connect=5.0)`` matches the SDK's 600s
#   read/write/pool default (the SDK reads ``HTTPX_DEFAULT_TIMEOUT``
#   only when that env var is set; explicit beats implicit).
# * ``follow_redirects=True`` matches the SDK's ``_DefaultHttpxClient``
#   (``kwargs.setdefault("follow_redirects", True)``); httpx itself
#   defaults to ``False``, so without this explicit kwarg the gzip
#   wrapper would silently diverge from the SDK default on any
#   HTTP 3xx response.
_GZIP_HTTPX_LIMITS = httpx.Limits(
    max_connections=1000,
    max_keepalive_connections=100,
)
_GZIP_HTTPX_TIMEOUT = httpx.Timeout(
    timeout=600.0,
    connect=5.0,
)
_GZIP_FOLLOW_REDIRECTS = True


def _compress_request_body(request: httpx.Request) -> None:
    """Mutate ``request`` in place to gzip its body, if applicable.

    Skip conditions (each is a no-op when met):

    * Method has no body semantics (GET / HEAD / DELETE / OPTIONS).
    * ``request.content`` is empty (no body bytes).
    * An existing ``Content-Encoding`` header is already present —
      the double-compression guard. httpx re-sends the same
      ``Request`` object through the transport on redirect / retry
      (``httpx.BaseTransport.handle_request`` docs); without this
      guard the body would be compressed twice and the proxy would
      see ``Content-Encoding: gzip, gzip`` on a doubly-compressed
      body that no longer decodes.
    * Compressed size is >= original size (gzip overhead on tiny
      payloads can exceed the savings — wire it through uncompressed
      in that edge case so the wire payload stays at original size).

    On success: sets ``Content-Encoding: gzip`` and overwrites
    ``Content-Length`` to the compressed byte count. Replaces
    BOTH ``request._content`` (httpx's private content cache — the
    ``request.content`` property reads from this slot) AND
    ``request.stream`` (the ``ByteStream`` httpx's transport reads
    on send). Both MUST be updated — on httpx 0.28.1 the transport
    serializes body bytes from ``request.stream``, not from
    ``request._content``; mutating only the cache while leaving the
    stream pointing at the ORIGINAL uncompressed bytes yields the
    "headers declare gzip + compressed length, wire carries original
    uncompressed bytes" failure mode (h11 aborts with
    ``LocalProtocolError: Too much data for declared Content-Length``
    and zero body bytes reach the proxy).
    """
    if request.method not in _METHODS_WITH_BODY:
        return
    if not request.content:
        return
    # Double-compression guard: if ANY existing content-coding header
    # is present the body has already been encoded somewhere upstream
    # and we MUST NOT compress again. Case-insensitive header lookup
    # matches httpx's ``Headers.get`` semantics.
    if request.headers.get("Content-Encoding"):
        return
    original = request.content
    compressed = gzip.compress(original)
    # Only swap in the compressed body if it's actually smaller — the
    # tiny-payload case (a few hundred bytes) can grow under gzip
    # framing overhead. Send uncompressed in that edge case so the
    # wire payload stays at original size.
    if len(compressed) >= len(original):
        return
    request.headers["Content-Encoding"] = "gzip"
    # Stale-Content-Length guard: replace the length header so it
    # matches the bytes that will go on the wire. httpx serializes
    # ``Content-Length`` from this header when no chunked transfer
    # encoding is in play — a mismatch (header says N, body is M)
    # would cause a 400 from a strict proxy / some LLM front-ends.
    request.headers["Content-Length"] = str(len(compressed))
    # Update BOTH the content cache AND the stream. On httpx 0.28.1
    # the transport serializes body bytes from ``request.stream``
    # (not from ``request._content``), so mutating only the cache
    # leaves the on-wire body uncompressed while the headers claim
    # ``Content-Encoding: gzip`` + a compressed ``Content-Length`` —
    # which causes h11 to abort with
    # ``LocalProtocolError: Too much data for declared Content-Length``
    # and zero body bytes reach the proxy. ``ByteStream`` is a thin
    # bytes wrapper exposed via the ``httpx._content`` private API
    # (no public alternative for re-binding ``request.stream``).
    request._content = compressed
    request.stream = ByteStream(compressed)


class GzipRequestTransport(httpx.BaseTransport):
    """Sync httpx transport that gzip-compresses request bodies.

    Wraps an inner transport (typically ``httpx.HTTPTransport``) and
    delegates everything except request-body modification to it.
    Response handling is COMPLETELY untouched — we never decode,
    re-encode, or modify the response bytes; we just return what
    the inner transport produced.

    Lifecycle
    ---------
    ``close()`` propagates to the inner transport so a wrapped
    ``Client.close()`` cleans up connection pools symmetrically.
    """

    def __init__(self, inner: httpx.BaseTransport) -> None:
        # Use object.__setattr__ to bypass pydantic / dataclass-style
        # descriptors if any subclass is ever added; this is the
        # standard idiom for httpx transport wrappers.
        object.__setattr__(self, "_inner", inner)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        _compress_request_body(request)
        return self._inner.handle_request(request)

    def close(self) -> None:
        self._inner.close()


class GzipAsyncRequestTransport(httpx.AsyncBaseTransport):
    """Async mirror of :class:`GzipRequestTransport`.

    Shares the same ``_compress_request_body`` helper via the
    module-level function above so the compression logic is defined
    in exactly one place; the async wrapper just provides the
    async transport seam.
    """

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        object.__setattr__(self, "_inner", inner)

    async def handle_async_request(
        self, request: httpx.Request
    ) -> httpx.Response:
        _compress_request_body(request)
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


def make_gzip_httpx_client() -> httpx.Client:
    """Build a sync ``httpx.Client`` whose transport gzips request bodies.

    The shared ``_GZIP_HTTPX_LIMITS`` / ``_GZIP_HTTPX_TIMEOUT`` /
    ``_GZIP_FOLLOW_REDIRECTS`` module constants pin connection-pool
    sizing, timeout, and redirect behavior to the OpenAI SDK's
    built-in ``DefaultHttpxClient`` defaults — see the constants'
    docstring for the rationale (no silent divergence from the SDK
    default on the disabled path).

    Note: per-request timeouts (the SDK passes ``timeout=...`` on
    every call via ``request.extensions['timeout']``) still take
    precedence — the wrapped client's Timeout is a fallback.
    """
    inner = httpx.HTTPTransport()
    transport = GzipRequestTransport(inner)
    return httpx.Client(
        transport=transport,
        follow_redirects=_GZIP_FOLLOW_REDIRECTS,
        limits=_GZIP_HTTPX_LIMITS,
        timeout=_GZIP_HTTPX_TIMEOUT,
    )


def make_gzip_async_httpx_client() -> httpx.AsyncClient:
    """Build an async ``httpx.AsyncClient`` whose transport gzips request bodies.

    Mirror of :func:`make_gzip_httpx_client`. Used by LangChain's
    ``http_async_client`` kwarg (see langchain-openai ``BaseChatOpenAI``
    constructor docs — ``http_async_client`` accepts an arbitrary
    httpx-compatible async client; the gzip transport is a drop-in
    for the langchain default).

    Same shared ``_GZIP_HTTPX_LIMITS`` / ``_GZIP_HTTPX_TIMEOUT`` /
    ``_GZIP_FOLLOW_REDIRECTS`` constants as the sync builder so the
    async path has identical pool sizing, timeout, and redirect
    behavior to the sync path (and to the OpenAI SDK's
    ``DefaultAsyncHttpxClient`` defaults).
    """
    inner = httpx.AsyncHTTPTransport()
    transport = GzipAsyncRequestTransport(inner)
    return httpx.AsyncClient(
        transport=transport,
        follow_redirects=_GZIP_FOLLOW_REDIRECTS,
        limits=_GZIP_HTTPX_LIMITS,
        timeout=_GZIP_HTTPX_TIMEOUT,
    )


def get_or_build_gzip_clients() -> tuple[httpx.Client, httpx.AsyncClient]:
    """Return the module-level (sync, async) gzip-enabled httpx clients.

    Lazily constructs both clients on first call; subsequent calls
    return the same pair so all LLM construction sites share one
    connection pool. The two singletons are reset only via
    :func:`reset_cached_clients` (test seam).

    Thread-safe via double-checked locking: a module-level
    ``threading.Lock`` serializes the actual construction while the
    initial ``is None`` check stays lock-free so the hot path
    (post-warmup) pays no lock cost. Both sync and async builds share
    the same lock — only one client is constructed at a time, but
    they are independent state slots so this only matters for the
    construction phase (post-warmup reads are O(1) and unsynchronized).
    """
    global _gzip_sync_client, _gzip_async_client
    # Double-checked locking: hot path is a bare None-check (lock-free
    # once warmed up); the lock guards only the actual build. The
    # second None-check inside the lock closes the TOCTOU window where
    # two threads both saw ``None`` in the outer check before either
    # assignment became visible.
    if _gzip_sync_client is None:
        with _gzip_lock:
            if _gzip_sync_client is None:
                _gzip_sync_client = make_gzip_httpx_client()
    if _gzip_async_client is None:
        with _gzip_lock:
            if _gzip_async_client is None:
                _gzip_async_client = make_gzip_async_httpx_client()
    return _gzip_sync_client, _gzip_async_client


def resolve_gzip_client(enabled: bool) -> httpx.Client | None:
    """Return the gzip-enabled sync ``httpx.Client`` if ``enabled``, else ``None``.

    Single entry point for the four raw-SDK call sites in
    ``daemon/services/skill_search_service.py``,
    ``daemon/services/skill_embedding_service.py``, and
    ``daemon/services/skill_evolution_service.py``. Each site builds
    ``openai.OpenAI(http_client=...)`` (or its async sibling) and
    wants to attach the gzip-enabled singleton when the operator
    knob is on — passing ``None`` when it's off so the openai SDK
    falls back to its built-in default httpx client (zero
    behavior change).

    Reuses the module-level ``get_or_build_gzip_clients`` singleton
    so the connection pool stays consolidated across the chat /
    embedding / evolution / search sites. The gzip transport is
    stateless and ``httpx.Client`` is thread-safe for concurrent
    requests, so the singleton is safe to share across
    ``asyncio.to_thread`` call sites. We re-build the singleton
    across test runs via :func:`reset_cached_clients` — production
    code never calls that.

    When ``enabled`` is ``False`` (the default; ``OPENAI_REQUEST_GZIP``
    unset / ``false``), the helper returns ``None`` WITHOUT
    touching ``get_or_build_gzip_clients`` — the disabled path must
    not build any transport it won't use (zero behavior change,
    byte-identical wire format to pre-feature). Flag-OFF
    behavior is preserved by this early return.
    """
    if not enabled:
        return None
    sync_client, _ = get_or_build_gzip_clients()
    return sync_client


def reset_cached_clients() -> None:
    """Close the sync client + clear both cached singletons.

    Test-only seam. Production code must NOT call this — the
    singletons live for the daemon's lifetime.

    Sync client is closed via ``Client.close()`` (synchronous; closes
    the underlying connection pool cleanly).

    Async client is dropped + cleared, NOT closed — ``httpx.AsyncClient``
    exposes ``aclose()`` (async coroutine), NOT a synchronous
    ``close()``. This is a sync test seam so we cannot ``await``
    ``aclose()``. Under CPython's refcounting the client object is
    reclaimed immediately when its last reference drops (no need to
    wait for the cyclic-GC sweep); the ``AsyncClient`` ``__del__`` /
    finalizer then tears down the connection pool. Acceptable for a
    test-only seam — each test session creates one async client and
    the OS reaps sockets when the process exits. Production code
    never calls this; production client lifetime = daemon lifetime.
    """
    global _gzip_sync_client, _gzip_async_client
    if _gzip_sync_client is not None:
        _gzip_sync_client.close()
        _gzip_sync_client = None
    # Async client: drop + clear (no sync close path exists on
    # ``httpx.AsyncClient`` — see docstring above). CPython refcount
    # reclaim + ``AsyncClient`` finalizer handle pool teardown.
    _gzip_async_client = None


__all__ = [
    "GzipAsyncRequestTransport",
    "GzipRequestTransport",
    "get_or_build_gzip_clients",
    "make_gzip_async_httpx_client",
    "make_gzip_httpx_client",
    "reset_cached_clients",
    "resolve_gzip_client",
]
