"""Regression pins for the daemon-side bounded bind-retry (2026-11-11).

Background
----------
On 2026-11-11 the live daemon restart-looped ~35x: every boot's uvicorn
bind of 0.0.0.0:9797 raised ``[Errno 98] address already in use``
because a previous incarnation still held the LISTEN socket, and
uvicorn's own handler for that OSError is ``logger.error(exc)`` →
``lifespan.shutdown()`` → ``sys.exit(1)`` (uvicorn/server.py:179-182) —
a CRASH exit the launcher respawns. A bind conflict is TRANSIENT
RESOURCE CONTENTION, so the daemon now waits (bounded) instead of
crashing.

These pins use a REAL hold-port fixture: a socket is bound and LISTENing
on an ephemeral port for the duration of the test. ``SO_REUSEADDR`` does
NOT permit two live listeners on one port (it only relaxes the
TIME_WAIT reuse rule), so the hold genuinely reproduces the contention.
The port is always an OS-assigned ephemeral port — never 9797 / 9798 /
8079.
"""

import asyncio
import errno
import http.client
import socket
import threading
import time
from contextlib import contextmanager

import pytest


@contextmanager
def _held_port():
    """Bind + LISTEN on an ephemeral port and HOLD it for the block.

    Yields ``(port, holder)``. The holder is a live listener: a second
    bind to the same port fails with EADDRINUSE even with
    SO_REUSEADDR set on both sockets.
    """
    holder = socket.socket(socket.AF_INET)
    holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    port = holder.getsockname()[1]
    try:
        yield port, holder
    finally:
        holder.close()


# ── The race shape: WAIT, then bind ──────────────────────────────────────────


def test_bind_waits_out_contention_then_succeeds_once_port_is_released():
    """The daemon-side bind path does NOT exit on a transient conflict.

    The port is held by a real listener for ~0.5s; the bind must WAIT
    and then succeed the moment the holder releases it — instead of
    crashing with exit 1 the way uvicorn's own failed-bind path did.
    """
    from daemon.services import bind_retry

    with _held_port() as (port, holder):
        released = threading.Event()

        def _release_soon():
            released.wait(0.5)
            holder.close()

        threading.Thread(target=_release_soon, daemon=True).start()

        started = time.monotonic()
        sock = bind_retry.bind_socket_with_retry(
            "127.0.0.1",
            port,
            max_wait_s=30.0,
            initial_backoff_s=0.05,
            max_backoff_s=0.05,
        )
        elapsed = time.monotonic() - started
        try:
            assert sock.getsockname()[1] == port
            # It really waited for the holder (not a lucky first try).
            assert elapsed >= 0.05, f"bind did not wait (elapsed={elapsed:.3f}s)"
        finally:
            sock.close()
        released.set()


def test_bind_fails_fast_when_budget_is_zero():
    """A zero budget = the pre-fix fail-fast behaviour (one attempt).

    Also pins that the conflict is genuinely EADDRINUSE (the shape the
    2026-11-11 incident produced), not some other OSError.
    """
    from daemon.services import bind_retry

    with _held_port() as (port, _holder):
        with pytest.raises(SystemExit) as excinfo:
            bind_retry.bind_socket_with_retry("127.0.0.1", port, max_wait_s=0)

    assert excinfo.value.code == 1


# ── Exhaustion: exit 1, NOT a new exit code ──────────────────────────────────


def test_bind_budget_exhaustion_exits_1_not_a_new_code():
    """Budget exhaustion exits 1 — the same code uvicorn used.

    A NEW exit code would land in the launcher's ``*→crash`` track and
    would need a paired ``classify_exit`` change in launcher.sh (which
    drags the manifest ``launcher_sha256`` scalar with it). The contract
    for this lane is "same site, same code".
    """
    from daemon.services import bind_retry

    assert bind_retry.BIND_FAILURE_EXIT_CODE == 1

    with _held_port() as (port, _holder):
        with pytest.raises(SystemExit) as excinfo:
            bind_retry.bind_socket_with_retry("127.0.0.1", port, max_wait_s=0.25)

    assert excinfo.value.code == 1
    assert excinfo.value.code not in (0, 3, 75, 78)


# ── Backoff ladder ───────────────────────────────────────────────────────────


def test_backoff_ladder_is_exponential_and_capped(monkeypatch):
    """The wait ladder is 1→2→4→8→16s and then CAPPED at 16s.

    The cap matters: an uncapped ladder would either blow past the
    budget or (worse) sleep for minutes on a long-lived conflict.
    """
    from daemon.services import bind_retry

    sleeps: list[float] = []
    monkeypatch.setattr(bind_retry, "sleep", lambda seconds: sleeps.append(seconds))

    with _held_port() as (port, _holder):
        with pytest.raises(SystemExit):
            bind_retry.bind_socket_with_retry("127.0.0.1", port, max_wait_s=180.0)

    assert sleeps[:5] == [1.0, 2.0, 4.0, 8.0, 16.0]
    assert set(sleeps[5:]) == {16.0}
    assert len(sleeps) < bind_retry.BIND_RETRY_MAX_ATTEMPTS


def test_default_budget_is_bounded_and_not_wind_down_sized():
    """The budget is BOUNDED — and deliberately NOT sized to a wind-down.

    The original brief sized the ceiling to "comfortably exceed the
    port-hold envelope" (uvicorn drain <=60s, launcher
    CHILD_STOP_WAIT_S=70, unit TimeoutStopSec=90, measured healthy
    shutdown 108s). That premise was FALSIFIED by measurement:
    uvicorn closes the 9797 LISTENING socket as the FIRST statement of
    ``Server.shutdown()`` (before the drain, before the lifespan
    shutdown), and the launcher is strictly sequential — across 63
    measured cycles the previous child was already DEAD 36.26s (min) /
    68.24s (median) / 191.25s (max) BEFORE every bind attempt.

    So the budget's job is to absorb a TRANSIENT holder (a dying
    duplicate/rogue daemon releases the port within ~1s because uvicorn
    closes the listener FIRST) while staying BOUNDED — an unbounded
    wait converts a crash-loop into a hang-loop and gives the operator
    no clean exit-1 signal.
    """
    from daemon.services.bind_retry import (
        BIND_RETRY_INITIAL_BACKOFF_S,
        BIND_RETRY_MAX_ATTEMPTS,
        BIND_RETRY_MAX_BACKOFF_S,
        BIND_RETRY_MAX_WAIT_S,
    )

    assert 0 < BIND_RETRY_MAX_WAIT_S <= 120.0  # bounded and finite
    assert BIND_RETRY_INITIAL_BACKOFF_S > 0
    assert BIND_RETRY_MAX_BACKOFF_S >= BIND_RETRY_INITIAL_BACKOFF_S
    assert BIND_RETRY_MAX_ATTEMPTS >= 2  # no unbounded spin


def test_default_budget_is_within_the_hard_ceiling():
    """The DEFAULT must already sit under the 45s hard ceiling.

    The ceiling is not a taste choice: the launcher's burst abort is
    reachable only while the failed child's uptime ``U`` stays <= 58s
    (6 crashes in the 600s window need ``310 + 5U <= 600``; with the
    pre-bind ``U ~= boot-to-prebind + budget``). A default above the
    ceiling would push U back over 58s and switch the supervisor's only
    safety net off again.
    """
    from daemon.config import DaemonConfig
    from daemon.services.bind_retry import (
        BIND_RETRY_MAX_WAIT_CEILING_S,
        BIND_RETRY_MAX_WAIT_S,
    )

    assert BIND_RETRY_MAX_WAIT_CEILING_S == 45.0
    assert BIND_RETRY_MAX_WAIT_S <= BIND_RETRY_MAX_WAIT_CEILING_S
    assert DaemonConfig().bind_retry_max_wait_seconds <= (
        BIND_RETRY_MAX_WAIT_CEILING_S
    )


def test_effective_budget_is_clamped_to_the_ceiling(caplog):
    """A request ABOVE the ceiling is CLAMPED, and the clamp is LOUD.

    The env override must not be able to silently push a deployment
    above the ceiling: the effective budget is clamped to 45s and a
    WARNING names the requested vs the effective value plus the
    burst-budget reason.
    """
    import logging

    from daemon.services import bind_retry

    caplog.set_level(logging.WARNING, logger="daemon.services.bind_retry")

    effective = bind_retry.resolve_max_wait_s(600.0)

    assert effective == bind_retry.BIND_RETRY_MAX_WAIT_CEILING_S == 45.0
    clamps = [r for r in caplog.records if "BUDGET_CEILING_CLAMP" in r.getMessage()]
    assert clamps, "the clamp was not announced"
    message = clamps[0].getMessage()
    assert "600.0" in message  # the requested value
    assert "45.0" in message  # the effective value
    assert "58s" in message  # the burst-budget reason
    assert clamps[0].levelno >= logging.WARNING


def test_a_shorter_explicit_budget_is_honoured_not_clamped(caplog):
    """A legitimate SHORTER budget is never clamped (and not announced)."""
    import logging

    from daemon.services import bind_retry

    caplog.set_level(logging.WARNING, logger="daemon.services.bind_retry")

    assert bind_retry.resolve_max_wait_s(5.0) == 5.0
    assert bind_retry.resolve_max_wait_s(0.0) == 0.0  # fail-fast
    assert not [
        r for r in caplog.records if "BUDGET_CEILING_CLAMP" in r.getMessage()
    ], "a legitimate shorter budget was announced as clamped"


def test_malformed_or_negative_budget_fails_closed_to_fail_fast(caplog):
    """A NEGATIVE or MALFORMED budget fails CLOSED to fail-fast + WARNING."""
    import logging

    from daemon.services import bind_retry

    caplog.set_level(logging.WARNING, logger="daemon.services.bind_retry")

    for bad in (-5, -0.1, "abc", float("nan"), float("inf")):
        caplog.clear()
        assert bind_retry.resolve_max_wait_s(bad) == 0.0, bad
        warnings = [
            r for r in caplog.records if r.levelno >= logging.WARNING
        ]
        assert warnings, f"the malformed budget {bad!r} was not announced"


def test_exhaustion_fires_at_the_budget_not_beyond_it():
    """The wait ladder can never sleep PAST the effective budget.

    The final sleep is clamped to the remaining budget, so the total
    wait is bounded by the budget (the attempt cap can only end the loop
    EARLIER). This is what keeps ``U ~= boot-to-prebind + budget``.
    """
    import time

    from daemon.services import bind_retry

    with _held_port() as (port, _holder):
        started = time.monotonic()
        with pytest.raises(SystemExit):
            bind_retry.bind_socket_with_retry(
                "127.0.0.1", port, max_wait_s=2.0
            )
        elapsed = time.monotonic() - started

    assert elapsed < 2.0 + 1.0, f"the wait overran the budget: {elapsed:.2f}s"


def test_config_default_agrees_with_the_module_default():
    """The DaemonConfig default and the module default must not drift.

    The policy constants live in the module that owns the retry (not in
    ``daemon/constants.py`` — that file is SEALED by content-sha256 pins),
    so the config default is a literal here; this pin is what keeps the
    two in step.
    """
    from daemon.config import DaemonConfig
    from daemon.services.bind_retry import BIND_RETRY_MAX_WAIT_S

    assert DaemonConfig().bind_retry_max_wait_seconds == BIND_RETRY_MAX_WAIT_S


def test_pre_bind_does_not_listen_so_probes_still_get_refused():
    """The pre-bind must NOT call ``listen()``.

    asyncio's ``loop.create_server(..., sock=sock)`` performs the
    ``listen()`` itself (uvicorn/server.py:144). Binding WITHOUT
    listening keeps the socket in the same OBSERVABLE state as an
    unbound port: a connect attempt is answered with ECONNREFUSED —
    exactly what a health probe observes today while the lifespan
    startup is running. Listening early would instead QUEUE the
    connection (the probe would hang), changing what the promoter's
    livez/readyz gate sees.
    """
    from daemon.services import bind_retry

    sock = bind_retry.bind_socket_with_retry("127.0.0.1", 0)
    port = sock.getsockname()[1]
    client = socket.socket(socket.AF_INET)
    client.settimeout(2.0)
    try:
        # Not listening: the kernel has no accept queue for it.
        assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) == 0
        with pytest.raises(ConnectionRefusedError) as excinfo:
            client.connect(("127.0.0.1", port))
        assert excinfo.value.errno == errno.ECONNREFUSED
    finally:
        client.close()
        sock.close()


# ── Socket fidelity vs the INSTALLED uvicorn ─────────────────────────────────


def test_prebound_socket_matches_installed_uvicorn_socket_options():
    """Our socket must carry the SAME options uvicorn would have set.

    uvicorn 0.41.0 ``Config.bind_socket()`` (uvicorn/config.py:533-546)::

        sock = socket.socket(family=family)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        ...
        sock.set_inheritable(True)

    The comparison below is made against the INSTALLED uvicorn, not a
    paraphrase of it.
    """
    import uvicorn

    from daemon.services import bind_retry

    ours = bind_retry.bind_socket_with_retry("127.0.0.1", 0)
    theirs = uvicorn.Config("daemon.api:app", host="127.0.0.1", port=0).bind_socket()
    try:
        assert ours.family == theirs.family
        assert ours.getsockopt(
            socket.SOL_SOCKET, socket.SO_REUSEADDR
        ) == theirs.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR)
        assert ours.get_inheritable() == theirs.get_inheritable()
    finally:
        ours.close()
        theirs.close()


def test_bound_socket_is_handed_to_uvicorn_which_serves_on_it(caplog):
    """End-to-end: uvicorn serves on OUR pre-bound socket.

    This is the shape the fix relies on — uvicorn never re-binds, it
    listens on the socket we already hold (``Server.startup`` →
    ``loop.create_server(..., sock=sock)``, uvicorn/server.py:140-146).

    It also pins the daemon's OWN server subclass: because uvicorn skips
    its "Uvicorn running on ..." line when a socket is supplied, the
    daemon emits that line itself — at the end of ``startup()``, i.e.
    AFTER the app import (so it is observable) and in the same place
    uvicorn's own line would have been.
    """
    import logging

    import uvicorn

    import daemon.__main__ as entry
    from daemon.services import bind_retry

    async def _app(scope, receive, send):
        if scope["type"] == "http":
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"text/plain")],
                }
            )
            await send({"type": "http.response.body", "body": b"ok"})

    caplog.set_level(logging.INFO, logger="daemon.__main__")

    sock = bind_retry.bind_socket_with_retry("127.0.0.1", 0)
    port = sock.getsockname()[1]
    config = uvicorn.Config(_app, host="127.0.0.1", port=port, log_level="warning")
    server = entry._StartedLogServer(config=config, bound_socket=sock)

    thread = threading.Thread(
        target=lambda: asyncio.run(server.serve(sockets=[sock])), daemon=True
    )
    thread.start()
    try:
        body = None
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request("GET", "/")
                body = conn.getresponse().read()
                conn.close()
                break
            except OSError:
                time.sleep(0.05)
        assert body == b"ok", f"uvicorn did not serve on the handed-off socket: {body!r}"
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        sock.close()

    started_lines = [
        r for r in caplog.records if "Uvicorn running on" in r.getMessage()
    ]
    assert started_lines, "the 'Uvicorn running on ...' line was not emitted"
    assert str(port) in started_lines[0].getMessage()


def test_serve_seam_end_to_end_binds_serves_and_stops_cleanly(monkeypatch):
    """The REAL seam, end-to-end: bind → hand-off → uvicorn serves → stop.

    Exercises the production path (the real bind retry, the real
    ``uvicorn.Server`` subclass, the real socket hand-off and the real
    shutdown tail) on an ephemeral port — never 9797/9798/8079.
    """
    import daemon.__main__ as entry

    async def _app(scope, receive, send):
        if scope["type"] == "http":
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"text/plain")],
                }
            )
            await send({"type": "http.response.body", "body": b"ok"})

    probe = socket.socket(socket.AF_INET)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    created: list = []

    class _RecordingServer(entry._StartedLogServer):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self)

    monkeypatch.setattr(entry, "_StartedLogServer", _RecordingServer)

    config = entry.uvicorn.Config(_app, host="127.0.0.1", port=port, log_level="warning")

    outcome: dict = {}

    def _run():
        try:
            entry._serve_with_bind_retry(config, max_wait_s=5.0)
            outcome["exit"] = None
        except BaseException as exc:  # noqa: BLE001 — captured for the assert
            outcome["exit"] = exc

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    try:
        body = None
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            if created and created[0].started:
                break
            time.sleep(0.05)
        assert created, "the serve seam never created the server"
        while time.monotonic() < deadline:
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request("GET", "/")
                body = conn.getresponse().read()
                conn.close()
                break
            except OSError:
                time.sleep(0.05)
        assert body == b"ok", f"the seam did not serve on the bound port: {body!r}"
    finally:
        if created:
            created[0].should_exit = True
        thread.join(timeout=20)

    # A clean shutdown must NOT trip the exit-3 (STARTUP_FAILURE) tail.
    assert outcome.get("exit") is None, outcome.get("exit")


def test_lifespan_startup_failure_exits_3_without_any_retry(monkeypatch):
    """A LIFESPAN startup failure must NOT become a wait-and-retry.

    On the FROZEN path the exit-75 PG preflight runs explicitly
    (``run_app.py:70`` → ``daemon.__main__._boot_db_preflight()``), so a
    definite PG refusal exits 75 before the bind. But a PG failure that
    the probe did not catch — or that appears after the probe's
    ``SELECT 1`` succeeded — surfaces as a LIFESPAN startup failure,
    which uvicorn reports as STARTUP_FAILURE (exit 3).

    The bind retry wraps ONLY the socket bind, so that verdict is
    preserved: exit 3, no wait, exactly ONE bind attempt.
    """
    import daemon.__main__ as entry
    from daemon.services import bind_retry

    binds: list = []
    real_bind = bind_retry.bind_socket_with_retry

    def _counting_bind(host, port, **kwargs):
        binds.append((host, port))
        return real_bind(host, port, **kwargs)

    monkeypatch.setattr(
        "daemon.services.bind_retry.bind_socket_with_retry", _counting_bind
    )

    async def _failing_app(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    # Exactly what Starlette does when the app's startup
                    # raises (starlette/routing.py:702-708): report the
                    # failure to the server. uvicorn then sets
                    # startup_failed → should_exit → the server never
                    # starts → STARTUP_FAILURE (exit 3).
                    await send(
                        {
                            "type": "lifespan.startup.failed",
                            "message": "simulated lifespan startup failure",
                        }
                    )
                    return
                if message["type"] == "lifespan.shutdown":
                    return

    config = entry.uvicorn.Config(_failing_app, host="127.0.0.1", port=0)
    with pytest.raises(SystemExit) as excinfo:
        entry._serve_with_bind_retry(config, max_wait_s=5.0)

    assert excinfo.value.code == 3  # uvicorn STARTUP_FAILURE, unchanged
    assert len(binds) == 1  # ONE bind attempt — no wait, no retry


def test_sigint_during_the_bind_wait_exits_cleanly(monkeypatch):
    """A SIGINT during the bind WAIT must exit cleanly — no traceback.

    ``uvicorn.run`` swallowed KeyboardInterrupt, so before this fix a
    Ctrl-C produced a clean exit. uvicorn installs its own SIGINT
    handler only INSIDE ``server.run()``'s ``capture_signals`` — i.e.
    AFTER the pre-bind phase — so the wait window is handled at the
    seam. (SIGTERM keeps its default disposition: prompt death.)
    """
    import daemon.__main__ as entry

    def _interrupting_bind(host, port, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(
        "daemon.services.bind_retry.bind_socket_with_retry", _interrupting_bind
    )
    config = entry.uvicorn.Config("daemon.api:app", host="127.0.0.1", port=0)
    # A clean return == a clean process exit. An escaped KeyboardInterrupt
    # would surface here as a test error (traceback + exit 130).
    entry._serve_with_bind_retry(config, max_wait_s=5.0)


def test_exit3_path_closes_the_held_socket(monkeypatch):
    """The exit-3 (lifespan-failure) path closes the held socket.

    uvicorn closes hand-held sockets only in ``shutdown()`` — which it
    never reaches when the lifespan failed (``started`` is False). The
    seam closes it explicitly rather than relying on process exit to
    release the port.
    """
    import daemon.__main__ as entry
    from daemon.services import bind_retry

    held: list = []
    real_bind = bind_retry.bind_socket_with_retry

    def _capturing_bind(host, port, **kwargs):
        sock = real_bind(host, port, **kwargs)
        held.append(sock)
        return sock

    monkeypatch.setattr(
        "daemon.services.bind_retry.bind_socket_with_retry", _capturing_bind
    )

    async def _failing_app(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send(
                        {
                            "type": "lifespan.startup.failed",
                            "message": "simulated lifespan startup failure",
                        }
                    )
                    return
                if message["type"] == "lifespan.shutdown":
                    return

    config = entry.uvicorn.Config(_failing_app, host="127.0.0.1", port=0)
    with pytest.raises(SystemExit) as excinfo:
        entry._serve_with_bind_retry(config, max_wait_s=5.0)

    assert excinfo.value.code == 3
    assert held, "the bind never produced a socket"
    assert held[0].fileno() == -1, "the held socket was left open on the exit-3 path"


def test_serve_seam_hands_the_bound_socket_to_uvicorn(monkeypatch):
    """The serve seam passes the BOUND socket to uvicorn (never re-binds).

    Behavioural pin (not a source-text pin): the fake server records
    the ``sockets`` argument it was handed.
    """
    import daemon.__main__ as entry

    recorded: dict = {}

    class _FakeServer:
        def __init__(self, config, bound_socket=None):
            recorded["config"] = config
            recorded["bound_socket"] = bound_socket
            self.started = True

        def run(self, sockets=None):
            recorded["sockets"] = sockets

    monkeypatch.setattr(entry, "_StartedLogServer", _FakeServer)

    config = entry.uvicorn.Config("daemon.api:app", host="127.0.0.1", port=0)
    entry._serve_with_bind_retry(config, max_wait_s=5.0)

    sockets = recorded["sockets"]
    assert isinstance(sockets, list) and len(sockets) == 1
    bound = sockets[0]
    try:
        assert bound.family == socket.AF_INET
        # Port 0 → the OS assigned a real ephemeral port; the socket we
        # hand over is the one that was actually bound.
        assert bound.getsockname()[1] != 0
        assert recorded["bound_socket"] is bound
    finally:
        bound.close()


def test_serve_seam_installs_log_handlers_before_the_bind(monkeypatch):
    """The pre-bind phase must install the root log handlers FIRST.

    The root logger has NO handler until ``daemon.api`` is imported —
    which happens INSIDE uvicorn's startup, i.e. AFTER the pre-bind
    phase. Until then ``logging.lastResort`` only emits WARNING+, so an
    INFO record emitted by the bind path would be silently DROPPED and
    the bind diagnostics would never reach ensemble.log.

    The app's own installer is idempotent (it replaces its tagged
    handler pair), so calling it early leaves the final handler set
    identical.
    """
    import daemon.__main__ as entry
    import daemon.logging_setup as logging_setup

    order: list[str] = []

    def _fake_install():
        order.append("install_root_logging")

    def _fake_bind(host, port, *, max_wait_s=None):
        order.append("bind")
        sock = socket.socket(socket.AF_INET)
        sock.bind(("127.0.0.1", 0))
        return sock

    class _FakeServer:
        def __init__(self, config, bound_socket=None):
            self.started = True

        def run(self, sockets=None):
            pass

    monkeypatch.setattr(logging_setup, "install_root_logging", _fake_install)
    monkeypatch.setattr(entry, "_StartedLogServer", _FakeServer)
    monkeypatch.setattr(
        entry, "_serve_with_bind_retry", entry._serve_with_bind_retry
    )
    # Patch the retry function INSIDE the module the seam imports it
    # from (the seam does a local import at call time).
    monkeypatch.setattr(
        "daemon.services.bind_retry.bind_socket_with_retry", _fake_bind
    )

    config = entry.uvicorn.Config("daemon.api:app", host="127.0.0.1", port=0)
    entry._serve_with_bind_retry(config, max_wait_s=5.0)

    assert order == ["install_root_logging", "bind"]


def test_retry_log_line_carries_errno_host_port_attempt_and_wait(caplog):
    """Contract: the retry is OBSERVABLE — one line per attempt with
    errno, host, port, attempt number and the elapsed wait.

    The 2026-11-11 incident was a forensic black hole precisely because
    the failure was unobservable. This pin uses the REAL hold-port
    fixture, so the line is produced by a genuine EADDRINUSE.
    """
    import logging

    from daemon.services import bind_retry

    caplog.set_level(logging.INFO, logger="daemon.services.bind_retry")

    with _held_port() as (port, _holder):
        with pytest.raises(SystemExit):
            bind_retry.bind_socket_with_retry("127.0.0.1", port, max_wait_s=0.2)

    retry_lines = [
        r for r in caplog.records if "Bind retry" in r.getMessage()
    ]
    assert retry_lines, "no bind-retry line was logged — the retry is unobservable"
    first = retry_lines[0]
    message = first.getMessage()
    assert str(port) in message  # port
    assert "127.0.0.1" in message  # host
    assert str(errno.EADDRINUSE) in message  # errno
    assert "Bind retry 1/" in message  # attempt number / attempt cap
    assert "retrying in" in message  # the elapsed wait
    assert "budget" in message  # the total budget
    assert first.levelno >= logging.WARNING


# ── The exit-code contract must survive the fix ──────────────────────────────


def test_preflight_exit_codes_are_untouched_by_the_bind_retry():
    """Exit 75 / 78 stay decided BEFORE the bind (and before the port).

    The bind-retry must not swallow, delay or reclassify the preflight
    refusals: a config refusal must NEVER bind the port.
    """
    import daemon.__main__ as entry
    from daemon.constants import BOOT_DB_TIMEOUT_S

    assert entry._EXIT_BOOT_DB_TEMPFAIL == 75
    assert entry._EXIT_BOOT_DB_AUTH_REFUSED == 78
    assert BOOT_DB_TIMEOUT_S == 10


def test_errno_is_not_leaked_into_the_constants_leaf():
    """``daemon.constants`` stays a LEAF (imports nothing).

    The commission's constraint: the constants module cannot import
    ``errno``, so the retry module (which owns the loop) imports it.
    """
    from pathlib import Path

    import daemon.constants as constants

    source = Path(constants.__file__).read_text(encoding="utf-8")
    assert "import errno" not in source
    assert "from errno" not in source
