"""Localhost-bound Prometheus exposition listener (P0 T1 / T2 / T11).

THE SHAPE (plan §3 / §4 P0 T1, catalog §0.7, arch §1 Option A)
---------------------------------------------------------------

* a **separate** listener on its **own** port — NEVER a route on the
  main FastAPI app (``daemon/api.py`` binds 0.0.0.0 with permissive
  CORS; an unauthenticated ``/metrics`` there would be an unbounded
  scrape surface — CR-2);
* bound to **127.0.0.1 only**;
* one route: ``GET /metrics``;
* a **dedicated daemon thread** running stdlib
  ``http.server.ThreadingHTTPServer`` — NOT the shared event loop (a
  listener on the main loop inherits the CR-1 failure it exists to
  mitigate) and NOT the census thread;
* **bind failure ⇒ disabled + logged, boot CONTINUES** (the
  resource-monitor boot degradation block in the lifespan — the
  ``except Exception as resource_monitor_boot_exc`` pattern);
* teardown: stop event → ``shutdown()`` → **detached** join → close
  (the resource-monitor stop block in the lifespan — the house
  detached-join precedent, never a blocking join on the event loop
  thread).

TESTABILITY (arch §8.3)
------------------------

The socket is created by an **injectable socket factory**, so a test
can bind an ephemeral port, bind nothing at all, or inject a failing
bind — **no unit test ever binds a real port**. The request handling
itself lives in a PURE function (``build_response``) that takes
``(path, headers)`` and returns a response object: the handler-level
tests (origin guard, degrade contract) need neither a socket nor a
thread.

THE KILL-SWITCH (P0 T3)
-----------------------

``daemon/services/metrics_gate.py`` owns the decision; this module only
applies it:

* switch OFF ⇒ **HTTP 200 with a VALID EMPTY body** — no series at
  all, INCLUDING no ``ensemble_metrics_up`` (NB-11);
* the registry KEEPS ACCUMULATING while the switch is off (the observe
  path is ungated, the RENDER path is gated) — switching ON later
  loses no history;
* the scrape counter increments on EVERY served scrape (``ok`` for the
  empty body, ``error`` when a family render raised);
* the exposition histogram records EVERY render, including the empty
  one — that is how the operator sees "listener alive, switch off".

NEVER 503 (MA-15): a 503 reads as "target DOWN" to Prometheus and
fires ``up==0`` / ``absent()`` alerts spuriously. Every failure mode
here degrades to 200 + a comment line.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Protocol
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

#: The exposition listener binds the LOOPBACK address ONLY.
LISTENER_HOST = "127.0.0.1"

#: The default listener port (``limits.metrics_listener_port``).
DEFAULT_LISTENER_PORT = 9798

#: The Prometheus classic text exposition content type (MA-16 — NOT
#: OpenMetrics).
CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

#: The exposition histogram's bucket set — catalog §8.11, verbatim.
EXPOSITION_BUCKETS: tuple[float, ...] = (
    0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0,
)

#: The exposition outcome vocabulary — catalog §8.3 (2 values).
OUTCOME_VALUES: frozenset[str] = frozenset({"ok", "error"})

#: The body of a refused (non-loopback origin) request.
ORIGIN_REFUSED_BODY = b"origin_not_trusted\n"

#: The comment marker the registry emits for a degraded family
#: (``# <family> unavailable: <reason>``). The listener scans the
#: rendered body for it to decide the scrape's outcome.
UNAVAILABLE_MARKER = "unavailable:"


# ─── F0.1 registration (the registry is the pinned API) ────────────────────

_f0_1_lock = threading.Lock()
_f0_1_registered = False
_up_handle = None
_scrape_handle = None
_exposition_handle = None


def ensure_f0_1_registered() -> bool:
    """Register the F0.1 exposition families (idempotent).

    Registered LAZILY (never at import time — the module must stay
    import-side-effect-free for the PyInstaller freeze rule A27 and for
    the listener's "importable without side effects" contract).

    Returns ``True`` when the families are registered (already included
    when this call is a repeat).
    """
    global _f0_1_registered, _up_handle, _scrape_handle, _exposition_handle
    with _f0_1_lock:
        if _f0_1_registered:
            return True
        from daemon import checkpoint_metrics as cm

        _up_handle = cm.register_gauge(
            "ensemble_metrics_up",
            label_keys=(),
            help=(
                "1 while the metrics exposition listener is live (the "
                "series is emitted only when the listener serves)."
            ),
        )
        _scrape_handle = cm.register_counter(
            "ensemble_metrics_scrape_requests_total",
            label_keys=("outcome",),
            allowed_values={"outcome": OUTCOME_VALUES},
            help=(
                "Exposition scrapes served by the metrics listener, by "
                "outcome (ok | error)."
            ),
        )
        _exposition_handle = cm.register_histogram(
            "ensemble_metrics_exposition_seconds",
            label_keys=(),
            buckets=EXPOSITION_BUCKETS,
            help="Seconds spent rendering one /metrics exposition.",
        )
        _f0_1_registered = True
        return True


def f0_1_handles() -> tuple[object | None, object | None, object | None]:
    """The registered F0.1 handles ``(up, scrape_counter, exposition)``.

    ``None`` until :func:`ensure_f0_1_registered` has run.
    """
    return _up_handle, _scrape_handle, _exposition_handle


def reset_f0_1_for_tests() -> None:
    """Forget the registration state (test-only)."""
    global _f0_1_registered, _up_handle, _scrape_handle, _exposition_handle
    with _f0_1_lock:
        _f0_1_registered = False
        _up_handle = None
        _scrape_handle = None
        _exposition_handle = None


# ─── origin guard (T2 — mirrors daemon/routers/maintenance_origin_guard) ──

#: The localhost-family exact spellings (the guard's rule 3).
_LOCALHOST_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _host_is_localhost(host: str) -> bool:
    """True for the localhost family (mirrors the maintenance guard).

    ``localhost`` / ``127.0.0.1`` / ``::1`` exactly, plus the whole
    ``127.0.0.0/8`` IPv4 loopback range.
    """
    h = (host or "").strip().lower()
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    if h in _LOCALHOST_HOSTS:
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False
    return isinstance(ip, ipaddress.IPv4Address) and ip.is_loopback


class _HeaderLookup(Protocol):
    """The header carrier the listener reads.

    Both carriers the listener meets satisfy it: a plain ``dict`` (the
    unit legs) and ``http.client.HTTPMessage`` (the live handler's
    ``self.headers``). The live carrier's ``get`` is CASE-INSENSITIVE;
    the dict's is not — the header names below are spelled in the
    canonical form.
    """

    def get(self, name: str, default: Any = None) -> Any: ...


def origin_is_trusted(headers: _HeaderLookup) -> bool:
    """The browser-origin guard (T2).

    Rule order (fail-closed at the end), mirroring
    ``daemon/routers/maintenance_origin_guard.py``:

    1. no ``Origin`` AND no ``Referer`` header → ALLOW (curl, systemd,
       Prometheus — a programmatic scraper carries neither);
    2. the origin host is in the localhost family → ALLOW;
    3. anything else → REFUSE (403 ``origin_not_trusted``).

    The ``Referer`` fallback exists because a browser navigation can
    carry a ``Referer`` without an ``Origin``; both are browser-borne
    evidence and are treated the same way.
    """
    origin = headers.get("Origin") or headers.get("Referer")
    if not origin:
        return True
    candidate = origin.strip()
    if not candidate:
        # ``Origin: null`` (sandboxed iframe) / ``file://`` → fail closed.
        return False
    host = (urlparse(candidate).hostname or "").strip().lower()
    if not host:
        return False
    return _host_is_localhost(host)


# ─── the response object ───────────────────────────────────────────────────


@dataclass(frozen=True)
class MetricsResponse:
    """One rendered response (the pure half of the listener)."""

    status: int
    content_type: str
    body: bytes
    #: ``"ok"`` / ``"error"`` for a served scrape; ``""`` when the
    #: request never reached the exposition path (404 / 403).
    outcome: str


# ─── the HTTP server ───────────────────────────────────────────────────────


class _MetricsServer(ThreadingHTTPServer):
    """``ThreadingHTTPServer`` with an injectable socket factory.

    The factory is called as ``factory(host, port)`` and MUST return an
    unbound socket; the server binds it. A factory that raises — or a
    socket whose ``bind`` raises — is the injected bind failure the
    degradation test drives.
    """

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        socket_factory: Callable[[str, int], socket.socket] | None = None,
    ) -> None:
        self._socket_factory = socket_factory
        super().__init__(server_address, handler_class)

    def server_bind(self) -> None:
        if self._socket_factory is not None:
            factory = self._socket_factory
            self.socket.close()
            self.socket = factory(self.server_address[0], self.server_address[1])
        super().server_bind()
        try:
            # Reflect the ACTUAL bound address (port 0 ⇒ the OS-assigned
            # ephemeral port).
            self.server_address = self.socket.getsockname()[:2]
        except Exception:  # pragma: no cover — defensive
            pass


class _MetricsRequestHandler(BaseHTTPRequestHandler):
    """The single-route handler (``GET /metrics``)."""

    server_version = "ensemble-metrics/1.0"
    # F5 — the default ``sys_version`` leaks the Python version into the
    # ``Server:`` header ("ensemble-metrics/1.0 Python/3.14.6"). The
    # product token is kept; the interpreter version is not disclosed.
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 — stdlib naming
        listener = getattr(self.server, "listener", None)
        if listener is None:
            # MA-15 — NEVER a 503: a 503 reads as "target DOWN" to
            # Prometheus and fires the up==0 / absent() alerts
            # spuriously. A detached listener serves 200 with a
            # comment line (the degrade contract's shape).
            self._respond(
                MetricsResponse(
                    200,
                    CONTENT_TYPE,
                    b"# exposition unavailable: listener_detached\n",
                    "error",
                )
            )
            return
        self._respond(listener.build_response(self.path, self.headers))

    def _respond(self, response: MetricsResponse) -> None:
        """Write one response (the single write path)."""
        self.send_response(response.status)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(response.body)))
        self.end_headers()
        if response.body:
            self.wfile.write(response.body)

    def log_message(self, fmt: str, *args: object) -> None:
        """Silence the stdlib per-request stderr logging."""
        return


# ─── the listener ──────────────────────────────────────────────────────────


class MetricsListener:
    """The localhost-bound exposition listener (P0 T1).

    Args:
        port: the listen port (``limits.metrics_listener_port``).
        host: the bind address — loopback ONLY. A non-loopback host is
            REFUSED in the constructor (F6): the loopback bind is a
            SECURITY boundary (CR-2 — the main app binds 0.0.0.0 with
            permissive CORS), so it must not be a convention that a
            caller can quietly break.
        socket_factory: the injectable socket factory (tests).
        gate_enabled: the kill-switch callable (defaults to the shared
            ``metrics_gate``).

    Raises:
        ValueError: when ``host`` is not a loopback address.
    """

    def __init__(
        self,
        port: int = DEFAULT_LISTENER_PORT,
        host: str = LISTENER_HOST,
        *,
        socket_factory: Callable[[str, int], socket.socket] | None = None,
        gate_enabled: Callable[[], bool] | None = None,
    ) -> None:
        # F6 — FAIL CLOSED: only the loopback family may be bound. There
        # is no escape hatch (the plan sanctions none): a wildcard bind
        # would expose an unauthenticated scrape surface to the LAN.
        if not _host_is_localhost(host):
            raise ValueError(
                f"metrics listener refuses to bind a non-loopback host: "
                f"{host!r} (the exposition surface is loopback-only)"
            )
        self._port = int(port)
        self._host = host
        self._socket_factory = socket_factory
        self._gate_enabled = gate_enabled
        self._server: _MetricsServer | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._shutdown_thread: threading.Thread | None = None
        self._started = False
        self._disabled_reason: str | None = None

    # ─── introspection ────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        """True when the listener is live (bound + serving)."""
        return self._started and self._server is not None

    @property
    def disabled_reason(self) -> str | None:
        """Why the listener is disabled (``None`` when live)."""
        return self._disabled_reason

    @property
    def port(self) -> int:
        """The bound port (the OS-assigned one when port 0 was asked)."""
        if self._server is not None:
            try:
                return int(self._server.server_address[1])
            except Exception:  # pragma: no cover — defensive
                pass
        return self._port

    @property
    def host(self) -> str:
        return self._host

    # ─── the kill-switch ───────────────────────────────────────────────

    def _gate(self) -> bool:
        if self._gate_enabled is not None:
            return bool(self._gate_enabled())
        from daemon.services import metrics_gate

        # The request path NEVER touches the DB: it only SIGNALS the
        # refresher thread (which performs the ONE read per TTL) and
        # reads the last-known values. With ENSEMBLE_METRICS_ENABLED
        # EXPLICITLY set the signal is skipped entirely — the env tier
        # wins and the emergency-off path pays nothing.
        metrics_gate.request_managed_values_refresh()
        return metrics_gate.metrics_enabled()

    # ─── the request path (PURE — no socket, no thread) ────────────────

    def build_response(
        self, path: str, headers: _HeaderLookup | None = None
    ) -> MetricsResponse:
        """Build the response for one request (the testable core).

        ``headers`` is the live handler's ``self.headers`` (an
        ``http.client.HTTPMessage``) or a plain dict in the unit legs —
        see :class:`_HeaderLookup`.
        """
        headers = headers if headers is not None else {}
        route = (path or "").split("?", 1)[0]
        if route.endswith("/") and route != "/":
            route = route.rstrip("/")
        if route != "/metrics":
            return MetricsResponse(
                404, "text/plain; charset=utf-8", b"not_found\n", ""
            )
        if not origin_is_trusted(headers):
            return MetricsResponse(
                403, "text/plain; charset=utf-8", ORIGIN_REFUSED_BODY, ""
            )
        return self.serve_exposition()

    def serve_exposition(self) -> MetricsResponse:
        """Render one exposition (the degrade contract lives here)."""
        started = time.perf_counter()
        enabled = self._gate()
        if not enabled:
            # Kill-switch OFF ⇒ HTTP 200 with a VALID EMPTY body — no
            # series at all, INCLUDING no ``ensemble_metrics_up``.
            body_text = ""
            outcome = "ok"
        else:
            up_handle, scrape_handle, exposition_handle = f0_1_handles()
            try:
                if up_handle is not None:
                    up_handle.set(1)
                from daemon import checkpoint_metrics as cm

                body_text = cm.render_metrics()
                outcome = "error" if UNAVAILABLE_MARKER in body_text else "ok"
            except Exception as exc:  # noqa: BLE001 — never 503
                # F3 — the exception MESSAGE never reaches the HTTP
                # body (it can carry SQL text, paths or operator data).
                # Only the exception TYPE is disclosed, bounded to a
                # fixed length.
                reason = type(exc).__name__[:64]
                body_text = f"# exposition unavailable: {reason}\n"
                outcome = "error"
            del scrape_handle, exposition_handle
        elapsed = time.perf_counter() - started
        self._record_scrape(outcome, elapsed)
        return MetricsResponse(
            200, CONTENT_TYPE, body_text.encode("utf-8"), outcome
        )

    def _record_scrape(self, outcome: str, elapsed: float) -> None:
        """Observe the scrape (the OBSERVE path is ungated).

        The counter is incremented AFTER the render so the recorded
        outcome is the scrape's REAL outcome (one increment per scrape;
        the rendered body therefore lags the counter by one scrape).
        """
        _up, scrape_handle, exposition_handle = f0_1_handles()
        if scrape_handle is not None:
            try:
                scrape_handle.inc(value=1, labels={"outcome": outcome})
            except Exception:  # noqa: BLE001 — a metric must never break a scrape
                pass
        if exposition_handle is not None:
            try:
                exposition_handle.observe(elapsed)
            except Exception:  # noqa: BLE001
                pass

    # ─── lifecycle ─────────────────────────────────────────────────────

    def start(self) -> bool:
        """Bind + serve. Returns ``False`` when the listener is DISABLED.

        A bind failure NEVER raises: the listener degrades to disabled
        (logged) and the daemon continues (plan §3 / §4 T1).

        The F0.1 families are registered by the CALLER (the lifespan
        boot calls :func:`ensure_f0_1_registered`) — the listener never
        registers on its own, so a registry that predates the F0.1
        families cannot stop the socket from binding.
        """
        if self._started:
            return True
        try:
            server = _MetricsServer(
                (self._host, self._port),
                _MetricsRequestHandler,
                socket_factory=self._socket_factory,
            )
        except Exception as exc:  # noqa: BLE001 — bind failure ⇒ disabled
            self._disabled_reason = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "[MetricsListener] DISABLED — bind failed on %s:%s (%s)",
                self._host,
                self._port,
                self._disabled_reason,
            )
            return False
        server.listener = self  # type: ignore[attr-defined]
        self._server = server
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._serve,
            args=(server,),
            name="metrics-listener",
            daemon=True,
        )
        self._thread.start()
        self._started = True
        logger.info(
            "[MetricsListener] started on %s:%d", self._host, self.port
        )
        return True

    def _serve(self, server: "_MetricsServer") -> None:
        """The serve loop (a crash is logged, never a raw traceback)."""
        try:
            server.serve_forever(poll_interval=0.2)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[MetricsListener] serve loop stopped: %s", exc)

    def stop(self) -> threading.Thread | None:
        """Stop the listener — DETACHED (never blocks the event loop).

        The stop event is set, then ``shutdown()`` + the join + the
        close all happen on a DETACHED daemon thread (the house
        precedent: the resource-monitor stop block in the lifespan).
        The returned thread is the detached shutdown thread (tests may
        join it).
        """
        self._stop_event.set()
        server = self._server
        thread = self._thread
        if server is None:
            self._started = False
            return None

        def _shutdown() -> None:
            try:
                server.shutdown()
            except Exception as exc:  # noqa: BLE001
                logger.warning("[MetricsListener] shutdown error: %s", exc)
            if thread is not None:
                try:
                    thread.join(timeout=5.0)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[MetricsListener] join error: %s", exc)
            try:
                server.server_close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("[MetricsListener] close error: %s", exc)

        try:
            self._shutdown_thread = threading.Thread(
                target=_shutdown,
                name="metrics-listener-shutdown",
                daemon=True,
            )
            self._shutdown_thread.start()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[MetricsListener] detached shutdown failed: %s", exc)
            self._shutdown_thread = None
        self._started = False
        return self._shutdown_thread
