"""LLM stream-liveness watchdog (llm-stream-stall-hardening, L2).

Detects a stalled streaming LLM response — bytes stopped arriving on an
SSE response body — within ``stream_stall_threshold_seconds`` (default
45s) instead of waiting out the 610s HTTP read deadline, and force-aborts
the blocked read so the EXISTING tenacity retry + base_url failover
machinery recovers immediately.

Why the transport layer
-----------------------
The hot path is SYNC ``invoke`` on an executor thread
(``daemon/graph.py`` run_in_executor around ``current_llm.invoke``);
there is no daemon-owned chunk iterator. SSE ``:heartbeat`` comment
keep-alives are dropped by the openai SDK decoder
(``openai/_streaming.py``) before LangChain sees anything — only the
raw-byte transport layer observes them. So the liveness signal is
observed by wrapping the response's byte stream at the httpx transport
level (the same seam the gzip transport occupies —
``daemon/services/llm_gzip.py`` is the committed precedent; both share
the httpx 0.28.1 pin).

Wall-clock, not monotonic
-------------------------
Every byte-chunk arrival stamps ``time.time()`` (WALL clock) into the
per-response registry entry. The 2026-08-27 incident class included a
15.6-minute machine sleep during which macOS's monotonic clock froze —
a monotonic timestamp would silently extend the stall window. Wall
clock advances across sleep, so the threshold is honest.

Delivery vehicle — transport shutdown, NOT response.close()
-----------------------------------------------------------
SPIKE (2026-09-14, macOS, httpx 0.28.1, both plain HTTP and TLS with a
self-signed cert): calling ``response.close()`` from another thread does
NOT unblock a sync ``read()`` blocked inside httpcore's
``socket.recv()`` — the reader stayed blocked indefinitely. Raw
``socket.close()`` also does not wake it. ``sock.shutdown(SHUT_RDWR)``
on the underlying BSD socket unblocks the blocked recv IMMEDIATELY
(both transports), surfacing ``httpx.RemoteProtocolError`` to the
reader, which this wrapper re-types into :class:`StreamStalledError`.
The socket is reached via a pinned private-attribute chain (same
class of pin as ``llm_gzip``'s ``httpx._content.ByteStream`` import —
re-verify on any httpx bump):

    response.stream                          # BoundSyncStream (post-Client) or
                                             # httpx ResponseStream (transport level)
      ._stream / ._inner                     # unwrap wrappers
        ._httpcore_stream                    # PoolByteStream
          ._stream                           # HTTP11ConnectionByteStream
            ._connection                     # HTTP11Connection
              ._network_stream               # httpcore SyncStream
                ._sock                       # socket / ssl.SSLSocket

Retry routing — zero classifier change
--------------------------------------
:class:`StreamStalledError` subclasses ``httpx.ReadTimeout``, which is a
``TIMEOUT_EXCEPTIONS`` member (``daemon/llm_error_classifier.py``), so
the existing ``RetryByCategory`` predicate routes the forced abort into
the timeout retry budget (``llm_retry_timeout_attempts=3``, primary
slice 2, then in-place base_url failover) with zero classifier edits.
Bare ``httpx.ReadError`` would be NON-retryable — deliberately never
raised.

Scope
-----
ONLY LangChain chat traffic routed through ``daemon.graph.clean_llm_config``
(the single chokepoint). The transport gates on ``Content-Type:
text/event-stream`` — non-SSE responses pass through untouched (no
wrapper, no registry entry, zero overhead). Raw-SDK sites are
deliberately NOT wired (non-streaming; a stall threshold would
false-abort legitimate generations).

Always-on
---------
No disable flag (repo fix/flag policy): the watchdog is always-on with
one tuning knob, ``LLMConfig.stream_stall_threshold_seconds``
(default 45, floor 10). The tick thread is a daemon thread started by
the api.py lifespan and stopped via a ``threading.Event`` on shutdown.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
import typing
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

# SSE Content-Type marker (substring, case-insensitive value match).
_SSE_CONTENT_TYPE = "text/event-stream"

# Default tick cadence for the daemon-wide watchdog thread (seconds).
# Tuning-only; the stall THRESHOLD lives in config (below).
WATCHDOG_TICK_INTERVAL_SECONDS = 1.0


class StreamStalledError(httpx.ReadTimeout):
    """A watchdog force-abort of a stalled SSE stream.

    Subclasses ``httpx.ReadTimeout`` — a ``TIMEOUT_EXCEPTIONS`` member —
    so ``RetryByCategory`` routes it into the timeout retry budget with
    ZERO classifier change, and the log line greps cleanly
    ("stream stalled: no bytes for Ns"). Never raise bare
    ``httpx.ReadError`` for forced aborts: it is non-retryable.
    """


@dataclass
class WatchedStreamEntry:
    """Per-response registry entry (one live SSE stream)."""

    response: httpx.Response
    request: httpx.Request | None = None
    # WALL clock stamps — see module docstring (machine-sleep immunity).
    registered_at: float = field(default_factory=time.time)
    last_byte_ts: float = field(default_factory=time.time)
    # Set by the sweeper BEFORE the force-unblock fires, so the reader
    # thread's exception handler can distinguish "abort we caused" from
    # a genuine network error (only the former is re-typed).
    force_closed: bool = False


class StreamWatchdogRegistry:
    """Thread-safe, id-keyed registry of live watched streams.

    Per-response entries: each ``handle_request`` mints a fresh entry,
    so no stale-timestamp state can leak across retry attempts (a new
    attempt = a new response = a new entry). ``deregister`` runs in the
    wrapper stream's iteration ``finally`` — normal completion, reader
    exception, and forced abort all converge there — AND in
    ``close()``/``aclose()`` (W1: a stream closed without being fully
    read must not leak its entry into the sweep forever).
    """

    def __init__(self) -> None:
        self._entries: dict[int, WatchedStreamEntry] = {}
        self._lock = threading.Lock()

    def register(self, entry: WatchedStreamEntry) -> None:
        with self._lock:
            self._entries[id(entry)] = entry

    def deregister(self, entry: WatchedStreamEntry) -> None:
        with self._lock:
            self._entries.pop(id(entry), None)

    def claim(self, entry: WatchedStreamEntry) -> bool:
        """Pop ``entry`` and mark it force-closed — atomically.

        The membership re-check + removal happen UNDER THE LOCK, so the
        unblocker and the stream's own completion finalizer (which calls
        ``deregister``) are mutually exclusive: whichever gets the lock
        first wins, and the loser observes a non-member (W2). Returns
        ``True`` when this caller won the claim (the entry was still a
        live registry member) and it is safe to act on the stream;
        ``False`` means the stream already completed/closed — the
        watchdog must NOT touch its socket (it may already be back in
        the pool serving an innocent request).
        """
        with self._lock:
            if self._entries.pop(id(entry), None) is None:
                return False
            entry.force_closed = True
            return True

    def drain(self) -> None:
        """Remove every entry (test seam — see :func:`reset_registry`)."""
        with self._lock:
            self._entries.clear()

    def touch(self, entry: WatchedStreamEntry) -> None:
        """Stamp wall-clock arrival of a byte chunk (hot path)."""
        with self._lock:
            live = self._entries.get(id(entry))
            if live is not None:
                live.last_byte_ts = time.time()

    def collect_stale(self, threshold_seconds: float) -> list[WatchedStreamEntry]:
        """Return (still-registered) entries whose silence exceeds the
        threshold, oldest stamp first order not guaranteed.

        Read-only: entries stay registered here. The caller passes each
        candidate through :meth:`claim` at unblock time — the membership
        re-check under the lock — so a stream that completes between
        collection and unblock is skipped, never force-aborted after the
        fact (W2). The ``force_closed`` flag is stamped by :meth:`claim`,
        not here.
        """
        now = time.time()
        stale: list[WatchedStreamEntry] = []
        with self._lock:
            for entry in self._entries.values():
                if now - entry.last_byte_ts > threshold_seconds:
                    stale.append(entry)
        return stale

    def snapshot_ids(self) -> set[int]:
        """Test/observability seam: ids of currently-registered entries."""
        with self._lock:
            return set(self._entries.keys())

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


# Daemon-wide singleton. The transports (constructed inside the module
# client singletons) and the api.py tick thread MUST share THIS instance
# — a per-transport or per-thread registry would silently no-op the
# feature (same identity discipline as the LONG_TOOL_REGISTRY pin).
STREAM_WATCHDOG_REGISTRY = StreamWatchdogRegistry()


# ─── Watched stream wrappers ─────────────────────────────────────────


class _WatchedSyncStream(httpx.SyncByteStream):
    """Sync byte-stream wrapper: stamps arrivals, re-types forced aborts.

    Every chunk yielded by the inner stream refreshes the entry's
    wall-clock ``last_byte_ts`` — SSE heartbeat comment bytes count
    (they legitimately prove the transport path is alive). When the
    watchdog force-aborts the blocked read, the surfaced exception is
    re-typed to :class:`StreamStalledError`; every other exception
    propagates UNCHANGED. Deregisters in the iteration ``finally`` AND
    in ``close()`` (W1) — no stale entries on any exit path.
    """

    def __init__(
        self,
        inner: httpx.SyncByteStream,
        entry: WatchedStreamEntry,
        registry: StreamWatchdogRegistry,
    ) -> None:
        self._inner = inner
        self._entry = entry
        self._registry = registry

    def __iter__(self) -> typing.Iterator[bytes]:  # type: ignore[override]
        entry = self._entry
        try:
            for chunk in self._inner:
                self._registry.touch(entry)
                yield chunk
        except (httpx.HTTPError, httpx.StreamError) as exc:
            if entry.force_closed:
                silence = max(0.0, time.time() - entry.last_byte_ts)
                raise StreamStalledError(
                    f"stream stalled: no bytes for {silence:.0f}s",
                    request=entry.request,
                ) from exc
            raise
        finally:
            self._registry.deregister(entry)

    def close(self) -> None:
        # W1: a stream closed WITHOUT being fully iterated (caller
        # abandoned it, ``with client.stream(...)`` early exit) never
        # reaches the iteration ``finally`` — deregister here too, or
        # the entry survives every sweep forever (1 WARNING/s + unbounded
        # registry growth). Idempotent: normal completion already popped
        # the entry via the ``finally``.
        self._registry.deregister(self._entry)
        self._inner.close()


class _WatchedAsyncStream(httpx.AsyncByteStream):
    """Async mirror of :class:`_WatchedSyncStream`.

    The daemon's hot path is sync-only today (async client dormant) —
    this keeps the sync/async client symmetry of the gzip seam so a
    future async migration inherits the watchdog for free. Deregisters
    in the ``aiter`` ``finally`` AND in ``aclose()`` (W1 mirror).
    """

    def __init__(
        self,
        inner: httpx.AsyncByteStream,
        entry: WatchedStreamEntry,
        registry: StreamWatchdogRegistry,
    ) -> None:
        self._inner = inner
        self._entry = entry
        self._registry = registry

    async def aiter(self) -> typing.AsyncIterator[bytes]:  # type: ignore[override]
        entry = self._entry
        try:
            async for chunk in self._inner:
                self._registry.touch(entry)
                yield chunk
        except (httpx.HTTPError, httpx.StreamError) as exc:
            if entry.force_closed:
                silence = max(0.0, time.time() - entry.last_byte_ts)
                raise StreamStalledError(
                    f"stream stalled: no bytes for {silence:.0f}s",
                    request=entry.request,
                ) from exc
            raise
        finally:
            self._registry.deregister(entry)

    async def aclose(self) -> None:
        # W1 (async mirror): deregister on close-without-iteration —
        # idempotent alongside the ``aiter`` ``finally``.
        self._registry.deregister(self._entry)
        await self._inner.aclose()


# ─── Transports ──────────────────────────────────────────────────────


def _is_sse_response(response: httpx.Response) -> bool:
    content_type = response.headers.get("content-type", "")
    return _SSE_CONTENT_TYPE in content_type.lower()


class WatchdogHTTPTransport(httpx.BaseTransport):
    """Sync transport wrapper: watches SSE response streams for stalls.

    Wraps an inner transport (``httpx.HTTPTransport`` or the gzip
    transport). ``handle_request`` delegates, then — ONLY for
    ``text/event-stream`` responses — wraps the response's byte stream
    in a stamping wrapper and registers it with the shared registry.
    Non-SSE responses (embeddings, non-streaming completions) pass
    through untouched: no wrapper, no entry, zero overhead.
    """

    def __init__(
        self,
        inner: httpx.BaseTransport,
        registry: StreamWatchdogRegistry | None = None,
    ) -> None:
        # Default to the daemon-wide singleton; tests may inject a
        # private registry for isolation. NOTE: explicit ``is not None``
        # — ``registry or ...`` would silently swap in the singleton for
        # a FRESH (empty) registry, because ``StreamWatchdogRegistry``
        # defines ``__len__`` and an empty registry is falsy.
        self._inner = inner
        self._registry = (
            registry if registry is not None else STREAM_WATCHDOG_REGISTRY
        )

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self._inner.handle_request(request)
        if not _is_sse_response(response):
            return response
        entry = WatchedStreamEntry(response=response, request=request)
        response.stream = _WatchedSyncStream(
            response.stream, entry, self._registry
        )
        self._registry.register(entry)
        return response

    def close(self) -> None:
        self._inner.close()

    # Test/observability seam: expose the registry the transport
    # registers into (identity pin for the singleton wiring).
    @property
    def registry(self) -> StreamWatchdogRegistry:
        return self._registry


class WatchdogAsyncHTTPTransport(httpx.AsyncBaseTransport):
    """Async mirror of :class:`WatchdogHTTPTransport`."""

    def __init__(
        self,
        inner: httpx.AsyncBaseTransport,
        registry: StreamWatchdogRegistry | None = None,
    ) -> None:
        # ``is not None`` (not ``or``) — an empty registry is falsy via
        # ``__len__``; see the sync transport's comment.
        self._inner = inner
        self._registry = (
            registry if registry is not None else STREAM_WATCHDOG_REGISTRY
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._inner.handle_async_request(request)
        if not _is_sse_response(response):
            return response
        entry = WatchedStreamEntry(response=response, request=request)
        response.stream = _WatchedAsyncStream(
            response.stream, entry, self._registry
        )
        self._registry.register(entry)
        return response

    async def aclose(self) -> None:
        await self._inner.aclose()

    @property
    def registry(self) -> StreamWatchdogRegistry:
        return self._registry


# ─── Force-unblock (the spike-proven vehicle) ────────────────────────

# Private-attribute candidates for unwrapping toward the raw socket.
# Verified chain on httpx 0.28.1 / httpcore 1.0.9 (see module docstring);
# the ladder tolerates both the transport-level response (no
# BoundSyncStream yet) and the post-Client wrapped shape.
_SOCK_UNWRAP_ATTRS = (
    "_stream",        # BoundSyncStream / PoolByteStream / watchdog wrapper
    "_inner",         # watchdog wrapper
    "_httpcore_stream",  # httpx ResponseStream
    "_connection",    # HTTP11ConnectionByteStream
    "_network_stream",  # HTTP11Connection
    "stream",         # response -> stream
)
_SOCK_TERMINAL_ATTR = "_sock"  # httpcore SyncStream -> socket


def _find_network_sock(obj: object, max_depth: int = 12) -> socket.socket | None:
    """Walk the pinned private-attribute chain to the raw BSD socket.

    Returns the first object that looks socket-like (``shutdown`` +
    ``fileno``), or ``None`` when the chain doesn't resolve (unknown
    transport shape / connection already torn down) — callers fall back
    to ``response.close()``. Never raises.
    """
    current = obj
    for _ in range(max_depth):
        if current is None:
            return None
        if hasattr(current, "shutdown") and hasattr(current, "fileno"):
            return current  # type: ignore[return-value]
        nxt = None
        for attr in _SOCK_UNWRAP_ATTRS:
            if attr is _SOCK_TERMINAL_ATTR:
                continue
            candidate = getattr(current, attr, None)
            if candidate is not None:
                nxt = candidate
                break
        if nxt is None:
            nxt = getattr(current, _SOCK_TERMINAL_ATTR, None)
        current = nxt
    return None


def _force_unblock(
    entry: WatchedStreamEntry,
    threshold_seconds: float,
    registry: StreamWatchdogRegistry,
) -> bool:
    """Abort one stalled stream. Never raises (fail-open per tick).

    Returns ``True`` when this caller CLAIMED the entry (still a live
    registry member at unblock time) and acted on it; ``False`` when the
    membership re-check under the lock lost the race — the stream
    already completed/closed and its socket may already be back in the
    connection pool serving an innocent request (W2: never touch it).

    Primary vehicle: ``sock.shutdown(SHUT_RDWR)`` — spike-proven to
    wake a blocked ``recv()`` on macOS for both plain and TLS sockets
    (``response.close()`` and raw ``socket.close()`` do NOT). The
    reader's exception path then performs the normal httpcore/httpx
    cleanup (connection close + pool release), so the watchdog never
    closes the fd itself. Two distinct fallbacks (W3, operationally
    different): the socket chain doesn't resolve (unknown transport
    shape / already torn down) vs ``shutdown()`` itself raising OSError.
    """
    try:
        # W2: pop + force-close-mark atomically UNDER THE LOCK. The
        # stream's own completion/cleanup path calls ``deregister`` on
        # the same lock, so a stream that finished between sweep
        # collection and this call is detected here as a non-member and
        # skipped — its (possibly pooled, possibly reassigned) socket is
        # never shutdown()ed.
        if not registry.claim(entry):
            return False
        silence = max(0.0, time.time() - entry.last_byte_ts)
        sock = _find_network_sock(entry.response)
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
                logger.warning(
                    "[StreamWatchdog] stream stalled: no bytes for %.0fs "
                    "(threshold=%.0fs) — forced transport shutdown "
                    "(StreamStalledError will ride the timeout retry budget)",
                    silence,
                    threshold_seconds,
                )
                return True
            except OSError as exc:
                # W3: distinct from the unresolvable-socket case — the
                # transport WAS recognized but the shutdown syscall
                # failed (typically: fd already closed by the peer /
                # reader teardown racing the tick).
                logger.warning(
                    "[StreamWatchdog] stream stalled: no bytes for %.0fs "
                    "(threshold=%.0fs) — shutdown() failed (%s); "
                    "response.close() fallback applied",
                    silence,
                    threshold_seconds,
                    exc,
                )
        else:
            # W3: the pinned private chain didn't resolve to a socket —
            # unknown transport shape or the connection is already gone.
            logger.warning(
                "[StreamWatchdog] stream stalled: no bytes for %.0fs "
                "(threshold=%.0fs) — socket unresolvable; "
                "response.close() fallback applied",
                silence,
                threshold_seconds,
            )
        entry.response.close()
        return True
    except Exception:  # noqa: BLE001 — the tick must never raise
        logger.exception("[StreamWatchdog] force-unblock failed")
        return False


def sweep_once(
    registry: StreamWatchdogRegistry,
    threshold_seconds: float,
) -> int:
    """One sweep pass; returns how many stalled streams were CLAIMED and
    aborted (entries that lost the completion race are not counted)."""
    stale = registry.collect_stale(threshold_seconds)
    claimed = 0
    for entry in stale:
        if _force_unblock(entry, threshold_seconds, registry):
            claimed += 1
    return claimed


def run_stream_watchdog_loop(
    registry: StreamWatchdogRegistry,
    threshold_seconds: float,
    stop_event: threading.Event,
    interval_seconds: float = WATCHDOG_TICK_INTERVAL_SECONDS,
) -> None:
    """Daemon-wide tick loop (runs in a daemon thread owned by api.py).

    1s cadence; each tick collects entries whose last-byte silence
    exceeds the threshold and force-unblocks them. Every tick body is
    exception-guarded — a sweep failure logs and retries next tick.
    ``stop_event.wait(interval)`` is both the sleep and the shutdown
    signal (prompt, no partial-interval lag on shutdown).
    """
    logger.info(
        "[StreamWatchdog] loop started: threshold=%.0fs interval=%.1fs "
        "registry=%d live stream(s)",
        threshold_seconds,
        interval_seconds,
        len(registry),
    )
    while not stop_event.wait(interval_seconds):
        try:
            sweep_once(registry, threshold_seconds)
        except Exception:  # noqa: BLE001 — never kill the tick thread
            logger.exception("[StreamWatchdog] sweep tick failed")
    logger.info("[StreamWatchdog] loop stopped")


# ─── Client builders (llm_gzip.py precedent) ─────────────────────────

# Shared httpx configuration — matches the OpenAI SDK's built-in
# ``DefaultHttpxClient`` defaults exactly (see llm_gzip.py rationale):
# pool sizing 1000/100, 600s read fallback + 5s connect,
# follow_redirects=True. Keeps the watchdog-wrapped client a drop-in
# replacement with no silent divergence from the SDK defaults.
_WATCHDOG_HTTPX_LIMITS = httpx.Limits(
    max_connections=1000,
    max_keepalive_connections=100,
)
_WATCHDOG_HTTPX_TIMEOUT = httpx.Timeout(
    timeout=600.0,
    connect=5.0,
)
_WATCHDOG_FOLLOW_REDIRECTS = True


def make_watchdog_httpx_client(use_gzip: bool) -> httpx.Client:
    """Build a sync ``httpx.Client`` with the watchdog transport outermost.

    ``use_gzip=True`` composes
    ``WatchdogHTTPTransport(GzipRequestTransport(httpx.HTTPTransport()))``
    — the watchdog owns the response stream (outermost), gzip mutates
    request bytes only (inner), per the llm-stream-stall-hardening
    composition decision.
    """
    inner: httpx.BaseTransport = httpx.HTTPTransport()
    if use_gzip:
        from .llm_gzip import GzipRequestTransport

        inner = GzipRequestTransport(inner)
    return httpx.Client(
        transport=WatchdogHTTPTransport(inner),
        follow_redirects=_WATCHDOG_FOLLOW_REDIRECTS,
        limits=_WATCHDOG_HTTPX_LIMITS,
        timeout=_WATCHDOG_HTTPX_TIMEOUT,
    )


def make_watchdog_async_httpx_client(use_gzip: bool) -> httpx.AsyncClient:
    """Async mirror of :func:`make_watchdog_httpx_client`."""
    inner: httpx.AsyncBaseTransport = httpx.AsyncHTTPTransport()
    if use_gzip:
        from .llm_gzip import GzipAsyncRequestTransport

        inner = GzipAsyncRequestTransport(inner)
    return httpx.AsyncClient(
        transport=WatchdogAsyncHTTPTransport(inner),
        follow_redirects=_WATCHDOG_FOLLOW_REDIRECTS,
        limits=_WATCHDOG_HTTPX_LIMITS,
        timeout=_WATCHDOG_HTTPX_TIMEOUT,
    )


# Per-flag singleton cache (double-checked locking, llm_gzip pattern).
# Keyed by the ``use_gzip`` flag so a flag flip across tests / startup
# can never hand back a client built for the other composition.
_watchdog_clients: dict[bool, tuple[httpx.Client, httpx.AsyncClient]] = {}
_watchdog_lock = threading.Lock()


def get_or_build_watchdog_clients(
    use_gzip: bool,
) -> tuple[httpx.Client, httpx.AsyncClient]:
    """Return the module-level (sync, async) watchdog-wrapped clients.

    Lazily built per ``use_gzip`` composition; subsequent calls return
    the same pair so all LangChain construction sites share one
    connection pool. Reset only via :func:`reset_cached_clients`
    (test seam — production never calls it).
    """
    cached = _watchdog_clients.get(use_gzip)
    if cached is not None:
        return cached
    with _watchdog_lock:
        cached = _watchdog_clients.get(use_gzip)
        if cached is None:
            cached = (
                make_watchdog_httpx_client(use_gzip),
                make_watchdog_async_httpx_client(use_gzip),
            )
            _watchdog_clients[use_gzip] = cached
    return cached


def reset_cached_clients() -> None:
    """Close + clear the cached watchdog client singletons.

    Test-only seam (mirrors ``llm_gzip.reset_cached_clients``).
    Production code must NOT call this — the singletons live for the
    daemon's lifetime.
    """
    _watchdog_clients.clear()


def reset_registry() -> None:
    """Drain the daemon-wide ``STREAM_WATCHDOG_REGISTRY`` singleton.

    Test-only seam (W4): ``reset_cached_clients`` rebuilds the client
    singletons but the REGISTRY is process-lifetime — without this
    drain, entries leaked by one test (a mock stream that never
    iterates or closes) poison every later test that asserts on the
    shared singleton. Production code must NOT call this — the
    registry must survive across requests for the daemon's lifetime.
    """
    STREAM_WATCHDOG_REGISTRY.drain()


__all__ = [
    "STREAM_WATCHDOG_REGISTRY",
    "StreamStalledError",
    "StreamWatchdogRegistry",
    "WatchedStreamEntry",
    "WatchdogAsyncHTTPTransport",
    "WatchdogHTTPTransport",
    "get_or_build_watchdog_clients",
    "make_watchdog_async_httpx_client",
    "make_watchdog_httpx_client",
    "reset_cached_clients",
    "reset_registry",
    "run_stream_watchdog_loop",
    "sweep_once",
]
