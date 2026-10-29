"""P0 T1/T2/T11 — the metrics exposition listener (plan §3 / §4 / §6).

Covers the legs that do NOT need the generalized registry:

* the **injectable socket factory** — NO unit test ever binds a real
  port (the fake socket never reaches ``bind(2)``);
* the **loopback-only** bind assertion;
* the **origin guard** (403 ``origin_not_trusted``) at the handler
  level (no socket, no thread);
* the **bind-failure degradation** (listener disabled + logged, boot
  continues);
* the **degrade contract** (kill-switch OFF ⇒ HTTP 200 with a VALID
  EMPTY body, never 503).

The registry-dependent legs (the F0.1 families + the live render) live
in ``test_metrics_listener_registry.py`` — they run LAST because they
need the parallel coder's ``register_*`` API.
"""

from __future__ import annotations

import io
import logging
import socket

import pytest

from daemon.services import metrics_gate
from daemon.services import metrics_listener as ml


# ─── the fake socket (NO real bind, ever) ──────────────────────────────────


class _FakeSocket:
    """A socket stand-in — ``bind``/``listen`` are recorded, not called.

    ``fileno()`` raises on purpose: the stdlib selector cannot register
    it, so the serve loop exits immediately instead of spinning on a
    file descriptor that would never be readable.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.host = host
        self.port = port
        self.bound_to: tuple[str, int] | None = None
        self.listened = False
        self.closed = False
        self.bind_error: Exception | None = None

    def setsockopt(self, *_args: object) -> None:
        return

    def bind(self, address: tuple[str, int]) -> None:
        if self.bind_error is not None:
            raise self.bind_error
        self.bound_to = address

    def getsockname(self) -> tuple[str, int]:
        return (self.host, self.port)

    def listen(self, _backlog: int) -> None:
        self.listened = True

    def close(self) -> None:
        self.closed = True

    def fileno(self) -> int:
        raise OSError("fake socket has no file descriptor")


class _FakeSocketFactory:
    """Records the (host, port) the listener asked for."""

    def __init__(self, *, bind_error: Exception | None = None) -> None:
        self.calls: list[tuple[str, int]] = []
        self.sockets: list[_FakeSocket] = []
        self._bind_error = bind_error

    def __call__(self, host: str, port: int) -> _FakeSocket:
        self.calls.append((host, port))
        sock = _FakeSocket(host=host, port=port)
        if self._bind_error is not None:
            sock.bind_error = self._bind_error
        self.sockets.append(sock)
        return sock


@pytest.fixture(autouse=True)
def _clean_gate(monkeypatch):
    monkeypatch.delenv(metrics_gate.ENV_VAR, raising=False)
    metrics_gate.reset_for_tests()
    yield
    metrics_gate.reset_for_tests()


# ─── leg (a): the injectable socket factory ───────────────────────────────


def test_listener_uses_the_injected_socket_factory():
    factory = _FakeSocketFactory()
    listener = ml.MetricsListener(port=0, socket_factory=factory)
    assert listener.start() is True
    try:
        assert len(factory.calls) == 1
        host, port = factory.calls[0]
        assert host == "127.0.0.1"
        assert port == 0
        assert factory.sockets[0].bound_to == ("127.0.0.1", 0)
        assert factory.sockets[0].listened is True
    finally:
        shutdown = listener.stop()
        if shutdown is not None:
            shutdown.join(timeout=5.0)


def test_no_real_socket_is_ever_bound(monkeypatch):
    """The unit leg NEVER binds a real socket — the injected factory is
    the only source of the listening socket."""
    real_bind: list[object] = []
    real_socket = socket.socket

    class _TrackingSocket(real_socket):  # type: ignore[misc,valid-type]
        def bind(self, *args: object, **kwargs: object):
            real_bind.append(args)
            return super().bind(*args, **kwargs)

    monkeypatch.setattr(socket, "socket", _TrackingSocket)
    factory = _FakeSocketFactory()
    listener = ml.MetricsListener(port=0, socket_factory=factory)
    assert listener.start() is True
    try:
        assert real_bind == []  # no real bind(2) — the fake socket was used
        assert factory.sockets[0].bound_to == ("127.0.0.1", 0)
    finally:
        shutdown = listener.stop()
        if shutdown is not None:
            shutdown.join(timeout=5.0)


# ─── leg (b): the loopback-only bind assertion ────────────────────────────


def test_listener_binds_loopback_only():
    factory = _FakeSocketFactory()
    listener = ml.MetricsListener(port=0, socket_factory=factory)
    assert listener.start() is True
    try:
        assert ml.LISTENER_HOST == "127.0.0.1"
        assert listener.host == "127.0.0.1"
        assert factory.calls[0][0] == "127.0.0.1"
        assert factory.sockets[0].bound_to[0] == "127.0.0.1"
    finally:
        shutdown = listener.stop()
        if shutdown is not None:
            shutdown.join(timeout=5.0)


def test_listener_host_cannot_be_widened_by_the_default():
    """The default constructor binds loopback — there is no code path
    that binds 0.0.0.0."""
    listener = ml.MetricsListener()
    assert listener.host == "127.0.0.1"
    assert listener.port == ml.DEFAULT_LISTENER_PORT


# ─── leg (c): the main FastAPI app has NO /metrics route ──────────────────


def test_main_app_has_no_metrics_route():
    """The main app must NEVER expose /metrics (CR-2)."""
    from daemon.api import app as main_app

    paths = {getattr(route, "path", None) for route in main_app.routes}
    assert "/metrics" not in paths
    assert not any(
        getattr(route, "path", "").startswith("/metrics")
        for route in main_app.routes
    )


# ─── leg (d): the origin guard ────────────────────────────────────────────


@pytest.mark.parametrize(
    "origin",
    [
        "http://evil.example.com",
        "https://attacker.test:8443",
        "http://192.168.1.10:3000",
        "null",
    ],
)
def test_non_loopback_origin_is_refused(origin):
    listener = ml.MetricsListener(port=0, socket_factory=_FakeSocketFactory())
    response = listener.build_response("/metrics", {"Origin": origin})
    assert response.status == 403
    assert b"origin_not_trusted" in response.body


def test_non_loopback_referer_is_refused():
    listener = ml.MetricsListener(port=0, socket_factory=_FakeSocketFactory())
    response = listener.build_response(
        "/metrics", {"Referer": "http://evil.example.com/page"}
    )
    assert response.status == 403
    assert b"origin_not_trusted" in response.body


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:4199",
        "http://127.0.0.1:9798",
        "http://127.0.0.1:3000",
        "https://localhost",
        "http://[::1]:8080",
    ],
)
def test_loopback_origin_is_allowed(origin):
    listener = ml.MetricsListener(port=0, socket_factory=_FakeSocketFactory())
    response = listener.build_response("/metrics", {"Origin": origin})
    assert response.status == 200


def test_programmatic_scraper_without_origin_is_allowed():
    """No Origin and no Referer ⇒ allowed (curl / Prometheus)."""
    listener = ml.MetricsListener(port=0, socket_factory=_FakeSocketFactory())
    response = listener.build_response("/metrics", {})
    assert response.status == 200


def test_origin_guard_refusal_is_not_counted_as_a_scrape():
    """A refused request never reaches the exposition path."""
    listener = ml.MetricsListener(port=0, socket_factory=_FakeSocketFactory())
    response = listener.build_response(
        "/metrics", {"Origin": "http://evil.example.com"}
    )
    assert response.outcome == ""


# ─── leg (e): bind-failure degradation ────────────────────────────────────


def test_bind_failure_disables_the_listener_and_logs(caplog):
    factory = _FakeSocketFactory(bind_error=OSError("address in use"))
    listener = ml.MetricsListener(port=9798, socket_factory=factory)
    with caplog.at_level(logging.WARNING, logger=ml.logger.name):
        assert listener.start() is False
    assert listener.enabled is False
    assert listener.disabled_reason is not None
    assert "bind failed" in caplog.text
    assert "[MetricsListener] DISABLED" in caplog.text


def test_bind_failure_does_not_raise():
    """A bind failure NEVER crashes the boot (the daemon continues)."""

    def _boom(host: str, port: int):
        raise OSError("no socket for you")

    listener = ml.MetricsListener(port=9798, socket_factory=_boom)
    assert listener.start() is False


def test_bind_failure_leaves_the_stop_path_safe():
    factory = _FakeSocketFactory(bind_error=OSError("address in use"))
    listener = ml.MetricsListener(port=9798, socket_factory=factory)
    assert listener.start() is False
    # stop() on a never-started listener is a no-op (no exception).
    assert listener.stop() is None


# ─── leg (g): the degrade contract ────────────────────────────────────────


def test_switch_off_serves_an_empty_200(monkeypatch):
    monkeypatch.setenv(metrics_gate.ENV_VAR, "0")
    listener = ml.MetricsListener(port=0, socket_factory=_FakeSocketFactory())
    response = listener.build_response("/metrics", {})
    assert response.status == 200
    assert response.body == b""
    assert response.outcome == "ok"


def test_switch_off_never_serves_503(monkeypatch):
    monkeypatch.setenv(metrics_gate.ENV_VAR, "0")
    listener = ml.MetricsListener(port=0, socket_factory=_FakeSocketFactory())
    for _ in range(5):
        assert listener.build_response("/metrics", {}).status == 200


def test_switch_off_body_has_no_series(monkeypatch):
    monkeypatch.setenv(metrics_gate.ENV_VAR, "0")
    listener = ml.MetricsListener(port=0, socket_factory=_FakeSocketFactory())
    body = listener.build_response("/metrics", {}).body.decode()
    assert "ensemble_metrics_up" not in body
    assert "#" not in body


def test_empty_render_is_fast(monkeypatch):
    """The empty render must cost well under 1 ms (plan §6)."""
    import time

    monkeypatch.setenv(metrics_gate.ENV_VAR, "0")
    listener = ml.MetricsListener(port=0, socket_factory=_FakeSocketFactory())
    listener.build_response("/metrics", {})  # warm
    started = time.perf_counter()
    listener.build_response("/metrics", {})
    elapsed = time.perf_counter() - started
    assert elapsed < 0.001, f"empty render took {elapsed * 1000:.3f} ms"


def test_unknown_route_is_404(monkeypatch):
    monkeypatch.setenv(metrics_gate.ENV_VAR, "1")
    listener = ml.MetricsListener(port=0, socket_factory=_FakeSocketFactory())
    response = listener.build_response("/not-metrics", {})
    assert response.status == 404
    assert response.outcome == ""


# ─── the detached-listener edge (FIX m8) ───────────────────────────────────


def _detached_handler() -> tuple[object, io.BytesIO]:
    """A handler whose server has NO listener attached."""
    import types

    handler = ml._MetricsRequestHandler.__new__(ml._MetricsRequestHandler)
    handler.server = types.SimpleNamespace(listener=None)
    handler.request_version = "HTTP/1.1"
    handler.requestline = "GET /metrics HTTP/1.1"
    handler.command = "GET"
    handler.path = "/metrics"
    buffer = io.BytesIO()
    handler.wfile = buffer
    return handler, buffer


def test_a_detached_listener_never_serves_503():
    """MA-15 — a 503 reads as "target DOWN" to Prometheus and fires the
    up==0 / absent() alerts spuriously. A detached listener serves 200
    with a comment line."""
    handler, buffer = _detached_handler()
    handler.do_GET()
    raw = buffer.getvalue()
    assert raw.startswith(b"HTTP/1.1 200")
    assert b"listener_detached" in raw


def test_the_detached_listener_body_is_a_valid_prometheus_comment():
    handler, buffer = _detached_handler()
    handler.do_GET()
    raw = buffer.getvalue()
    assert b"503" not in raw.split(b"\r\n", 1)[0]
    assert b"# exposition unavailable: listener_detached" in raw


# ─── F5 — the Server header must not leak the Python version ────────────


def test_the_server_header_does_not_leak_the_python_version():
    """F5 — the stdlib default appends ``Python/3.x`` to the ``Server:``
    header. The product token stays; the interpreter version does not."""
    assert ml._MetricsRequestHandler.sys_version == ""
    handler, buffer = _detached_handler()
    handler.do_GET()
    raw = buffer.getvalue()
    assert b"Server: ensemble-metrics/1.0" in raw
    assert b"Python/" not in raw


# ─── F3 — the error body must not leak the exception message ────────────


def test_a_raising_render_does_not_leak_the_exception_message(monkeypatch):
    """F3 — the raw exception text (SQL, paths, operator data) must never
    reach the HTTP body: only the exception TYPE, bounded."""
    from daemon import checkpoint_metrics as cm

    secret = "postgresql://user:hunter2@db/prod SELECT * FROM secrets"
    monkeypatch.setenv(metrics_gate.ENV_VAR, "1")
    listener = ml.MetricsListener(port=0, socket_factory=_FakeSocketFactory())

    def _boom() -> str:
        raise RuntimeError(secret)

    monkeypatch.setattr(cm, "render_metrics", _boom)
    response = listener.build_response("/metrics", {})
    body = response.body.decode()
    assert response.status == 200
    assert secret not in body
    assert "hunter2" not in body
    assert "RuntimeError" in body
    # The reason is BOUNDED (a fixed-length prefix).
    reason = body.split("unavailable: ", 1)[1].split("\n", 1)[0]
    assert len(reason) <= 64


def test_the_error_body_is_bounded_for_a_long_exception_type(monkeypatch):
    """F3 — even an absurdly long exception type name is bounded."""
    from daemon import checkpoint_metrics as cm

    monkeypatch.setenv(metrics_gate.ENV_VAR, "1")
    listener = ml.MetricsListener(port=0, socket_factory=_FakeSocketFactory())

    class _VeryLongNameError(Exception):
        pass

    _VeryLongNameError.__name__ = "E" * 500
    monkeypatch.setattr(
        cm, "render_metrics", lambda: (_ for _ in ()).throw(_VeryLongNameError())
    )
    response = listener.build_response("/metrics", {})
    body = response.body.decode()
    reason = body.split("unavailable: ", 1)[1].split("\n", 1)[0]
    assert len(reason) <= 64


# ─── F6 — a non-loopback bind host is REFUSED ────────────────────────────


@pytest.mark.parametrize(
    "host", ["0.0.0.0", "::", "192.168.1.10", "example.com", "10.0.0.1"]
)
def test_a_non_loopback_host_is_refused(host):
    """F6 — the loopback bind is a SECURITY boundary (CR-2): a wildcard
    bind would expose an unauthenticated scrape surface to the LAN."""
    with pytest.raises(ValueError):
        ml.MetricsListener(port=0, host=host)


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2", "localhost", "::1"])
def test_a_loopback_host_is_accepted(host):
    listener = ml.MetricsListener(port=0, host=host)
    assert listener.host == host


def test_metrics_route_tolerates_a_query_string(monkeypatch):
    monkeypatch.setenv(metrics_gate.ENV_VAR, "0")
    listener = ml.MetricsListener(port=0, socket_factory=_FakeSocketFactory())
    response = listener.build_response("/metrics?collect=all", {})
    assert response.status == 200


# ─── the teardown contract ─────────────────────────────────────────────────


def test_stop_is_detached_and_never_blocks_the_caller():
    factory = _FakeSocketFactory()
    listener = ml.MetricsListener(port=0, socket_factory=factory)
    assert listener.start() is True
    shutdown = listener.stop()
    assert shutdown is not None
    assert shutdown.daemon is True
    assert shutdown.name == "metrics-listener-shutdown"
    shutdown.join(timeout=5.0)
    assert listener.enabled is False
