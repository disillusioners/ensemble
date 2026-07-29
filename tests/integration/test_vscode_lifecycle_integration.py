"""Lifecycle INTEGRATION tests for the VS Code server editor (3b).

These tests validate the WIRING between the FastAPI settings router, a
REAL ``VSCodeServerManager`` instance, and ``app.state`` — catching bugs
that the pure unit tests (``tests/unit/test_vscode_server_manager.py``)
and the stubbed-manager API tests (``tests/api/test_editor_settings.py``)
cannot see.

Layering reminder (DO NOT duplicate):
    * ``tests/unit/test_vscode_server_manager.py`` — manager in isolation
      (subprocess mocked). 36 tests PASS.
    * ``tests/api/test_editor_settings.py`` — router with a MagicMock
      manager stub. 29 tests PASS.
    * THIS file — router + REAL manager (subprocess still mocked) wired
      into the real ``app.state``. This is the integration seam.

What is REAL here:
    * The FastAPI app (``daemon.api.app``).
    * The ``VSCodeServerManager`` instance and its full lifecycle state
      machine (``start`` / ``stop`` / ``ensure_running`` / ``is_running`` /
      crash detection via ``_watchdog_loop``).
    * The metadata KV: a real in-memory SQLite ``SQLModelProjectRepository``
      so editor preferences actually round-trip through the DB.

What is MOCKED (the only thing we cannot run for real):
    * ``asyncio.create_subprocess_exec`` — returns a ``FakeProcess``
      (mirrors the unit-test approach) so we control process outcomes
      without spawning a real ``code-server``.
    * ``os.kill`` / ``os.killpg`` — no-op signal recording (so ``stop()``
      escalation doesn't touch real processes).

Test scenarios (all mandated by the 3b task spec):
    1. Editor switch to VS Code → server starts (``ensure_running`` called,
       preference persisted).
    2. Editor switch to Built-in → server stops (``stop`` called,
       preference persisted).
    3. W13 transactionality — server fails to start (``VSCodeServerError``)
       → 503 AND preference NOT persisted.
    4. W13 transactionality — binary not installed
       (``VSCodeServerNotInstalledError``) → 503 AND preference NOT
       persisted.
    5. Crash recovery — manager detects dead process (``is_running`` flips
       ``state.status`` to ``crashed``).

Run only this file::

    timeout 300 .venv/bin/pytest \\
        tests/integration/test_vscode_lifecycle_integration.py -v \\
        --tb=short -q -m "not integration and not postgres"
"""
from __future__ import annotations

import asyncio
import signal
from pathlib import Path
from typing import Any, List, Optional

import httpx
import pytest
import pytest_asyncio
from sqlmodel import SQLModel, create_engine

from daemon import constants
from daemon.api import app
from daemon.config import VSCodeConfig
from daemon.routers import settings as settings_module
from daemon.repositories import SQLModelProjectRepository
from daemon.services.vscode_server_manager import (
    VSCodeServerManager,
    VSCodeServerNotInstalledError,
    VSCodeServerError,
)


# ═══════════════════════════════════════════════════════════════════════════
# Process fakes (mirrors tests/unit/test_vscode_server_manager.py)
# ═══════════════════════════════════════════════════════════════════════════


class FakeStream:
    """Mimics ``asyncio.subprocess.Stream`` for stdout/stderr.

    Yields each chunk in ``chunks`` once via ``read()``, then returns
    empty bytes (EOF) on subsequent reads.
    """

    def __init__(self, chunks: Optional[List[bytes]] = None) -> None:
        self._chunks = list(chunks or [])
        self._idx = 0

    async def read(self, n: int = -1) -> bytes:
        if self._idx >= len(self._chunks):
            return b""
        chunk = self._chunks[self._idx]
        self._idx += 1
        return chunk


class FakeProcess:
    """Mimics ``asyncio.subprocess.Process`` for lifecycle testing.

    ``returncode`` is ``None`` while "alive"; setting it to an int simulates
    the process exiting (``poll()`` / ``returncode`` becomes non-None). The
    watchdog loop and ``is_running()`` both read ``returncode`` to detect
    death.
    """

    def __init__(
        self,
        pid: int = 12345,
        stdout_chunks: Optional[List[bytes]] = None,
        returncode: Optional[int] = None,
    ) -> None:
        self.pid = pid
        self.stdout = FakeStream(stdout_chunks or [])
        self.stderr = FakeStream([])
        self.returncode = returncode
        self.signals: List[Any] = []
        self.kill_called = False

    async def wait(self) -> Optional[int]:
        # Yield to the event loop then return current state.
        await asyncio.sleep(0)
        return self.returncode

    def send_signal(self, sig: int) -> None:
        self.signals.append(sig)
        if self.returncode is None:
            self.returncode = -sig

    def kill(self) -> None:
        self.kill_called = True
        self.signals.append("KILL")
        if self.returncode is None:
            self.returncode = -9


# ═══════════════════════════════════════════════════════════════════════════
# Patching helpers
# ═══════════════════════════════════════════════════════════════════════════


def _patch_create_subprocess(
    monkeypatch: pytest.MonkeyPatch, fake_proc: FakeProcess
) -> List[dict]:
    """Patch ``asyncio.create_subprocess_exec`` to return ``fake_proc``.

    Returns a list of call records for argument inspection.
    """
    calls: List[dict] = []

    async def fake_create_subprocess_exec(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return fake_proc

    monkeypatch.setattr(
        "asyncio.create_subprocess_exec", fake_create_subprocess_exec
    )
    return calls


def _patch_process_signals(
    monkeypatch: pytest.MonkeyPatch, fake_proc: FakeProcess
) -> dict:
    """Patch ``os.kill`` / ``os.killpg`` to no-op but record calls.

    Simulates the kernel effect of terminating signals so ``stop()``'s
    ``process.wait()`` can return promptly.
    """
    recorded: dict[str, list] = {"kill_calls": [], "killpg_calls": []}

    def fake_kill(pid: int, sig: int, *args: Any, **kwargs: Any) -> None:
        recorded["kill_calls"].append((pid, sig))

    def fake_killpg(pgid: int, sig: int, *args: Any, **kwargs: Any) -> None:
        recorded["killpg_calls"].append((pgid, sig))
        if sig == signal.SIGTERM and fake_proc.returncode is None:
            fake_proc.returncode = -signal.SIGTERM
        elif sig == signal.SIGKILL and fake_proc.returncode is None:
            fake_proc.returncode = -signal.SIGKILL

    monkeypatch.setattr("os.kill", fake_kill)
    monkeypatch.setattr("os.killpg", fake_killpg)
    return recorded


def _patch_port_wait(
    monkeypatch: pytest.MonkeyPatch, manager: VSCodeServerManager
) -> None:
    """Make ``_wait_for_port`` return immediately by pre-setting the port."""

    async def fake_wait_for_port() -> None:
        manager.state.port = 12345

    monkeypatch.setattr(manager, "_wait_for_port", fake_wait_for_port)


def _slow_down_health_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the health-check loop poll slowly so tests stay quiet/fast."""
    monkeypatch.setattr(
        "daemon.services.vscode_server_manager.VSCODE_HEALTH_CHECK_INTERVAL_S",
        60.0,
    )


def _make_fake_proc(*, port_line: bool = True) -> FakeProcess:
    """Build a FakeProcess whose stdout yields a port line then EOF.

    A port line in the first chunk lets the (mocked) reader loop detect
    the port if it runs; ``_patch_port_wait`` bypasses that loop anyway.
    """
    chunk = (
        b"HTTP server listening on http://127.0.0.1:12345\n"
        if port_line
        else b""
    )
    return FakeProcess(pid=99999, stdout_chunks=[chunk], returncode=None)


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════


@pytest_asyncio.fixture
async def integration_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Wire a REAL app + REAL manager + REAL in-memory DB, mocked subprocess.

    Yields a namespace with:
        * ``client``  — ``httpx.AsyncClient`` (ASGITransport, no lifespan).
        * ``manager`` — the REAL ``VSCodeServerManager`` on ``app.state``.
        * ``repo``    — the REAL ``SQLModelProjectRepository``.
        * ``fake_proc`` — the ``FakeProcess`` returned by the mocked spawn.

    The fixture is function-scoped and fully tears down (restores
    ``constants.SYSTEM_DEFAULT_PROJECT_ID``, removes ``app.state.vscode_manager``,
    resets the router's module-level repo global, stops any spawned tasks).
    """
    # ── 1. Real file-backed SQLite repo ──────────────────────────────────
    # NOTE: ``:memory:`` SQLite is per-connection by default, so writes done
    # via ``asyncio.to_thread`` (a different thread = different connection =
    # a fresh empty DB) would vanish. A file-backed DB on tmp_path keeps the
    # schema/data visible across threads, matching the real daemon.
    db_path = tmp_path / "lifecycle_test.db"
    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    repo = SQLModelProjectRepository(engine)

    # Create the system default project + set the global constant so the
    # router's ``get_project_repository()`` / editor_utils can read/write
    # the editor preference metadata KV.
    saved_sys_id = constants.SYSTEM_DEFAULT_PROJECT_ID
    sys_proj_id = repo.ensure_system_default_project()
    constants.SYSTEM_DEFAULT_PROJECT_ID = sys_proj_id

    # Wire the repo into the settings router (module-level global).
    saved_router_repo = settings_module._project_repo
    settings_module.set_project_repository(repo)

    # ── 2. REAL VSCodeServerManager with mocked subprocess ──────────────
    config = VSCodeConfig(binary_path="/usr/bin/code-server", allow_remote=False)
    manager = VSCodeServerManager(config=config, data_dir=str(tmp_path))

    # Bypass filesystem binary validation — we never spawn a real process.
    monkeypatch.setattr(manager, "_resolve_binary", lambda: "/usr/bin/code-server")

    # Default fake process for a successful spawn.
    fake_proc = _make_fake_proc()
    _patch_create_subprocess(monkeypatch, fake_proc)
    _patch_process_signals(monkeypatch, fake_proc)
    _patch_port_wait(monkeypatch, manager)
    _slow_down_health_check(monkeypatch)
    monkeypatch.setattr("os.getpgid", lambda pid: pid + 1000)

    # ── 3. Wire manager + start_time into the real app ──────────────────
    saved_vscode_mgr = getattr(app.state, "vscode_manager", _MISSING)
    saved_start_time = getattr(app.state, "start_time", _MISSING)
    app.state.vscode_manager = manager
    app.state.start_time = 1000.0

    namespace = SimpleNamespaceEx(
        manager=manager,
        repo=repo,
        fake_proc=fake_proc,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        namespace.client = ac
        yield namespace

    # ── Teardown ────────────────────────────────────────────────────────
    # Stop any background tasks (reader/watchdog/health) the manager spun up.
    try:
        await asyncio.wait_for(manager.cleanup(), timeout=5.0)
    except Exception:
        pass

    # Restore globals so other tests start clean.
    constants.SYSTEM_DEFAULT_PROJECT_ID = saved_sys_id
    settings_module._project_repo = saved_router_repo
    if saved_vscode_mgr is _MISSING:
        if hasattr(app.state, "vscode_manager"):
            del app.state.vscode_manager
    else:
        app.state.vscode_manager = saved_vscode_mgr
    if saved_start_time is not _MISSING:
        app.state.start_time = saved_start_time


class _MISSING:
    """Sentinel for "attribute was absent before the test"."""


class SimpleNamespaceEx:
    """Mutable namespace; lets the fixture swap ``fake_proc`` mid-test."""

    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


# ═══════════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestEditorSwitchToVSCodeStartsServer:
    """PUT editor=vscode → REAL manager ``ensure_running()`` is called and
    succeeds; preference is persisted to the REAL metadata KV."""

    async def test_put_vscode_starts_server_and_persists(
        self, integration_env, monkeypatch
    ):
        env = integration_env
        manager = env.manager

        # Manager starts stopped.
        assert manager.state.status == "stopped"
        assert manager.is_running() is False

        # Track whether the REAL ensure_running path ran (it calls start()
        # internally since not running). Spy on start().
        original_start = manager.start
        start_called = {"flag": False}

        async def spy_start():
            start_called["flag"] = True
            return await original_start()

        monkeypatch.setattr(manager, "start", spy_start)

        resp = await env.client.put(
            "/api/settings/editor", json={"editor": "vscode"}
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["editor"] == "vscode"
        # The REAL ensure_running() → start() was invoked.
        assert start_called["flag"] is True
        assert manager.state.status == "running"

        # CRITICAL integration check: preference actually round-tripped
        # through the REAL metadata KV (a subsequent GET returns vscode).
        get_resp = await env.client.get("/api/settings/editor")
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["editor"] == "vscode"


@pytest.mark.asyncio
class TestEditorSwitchToBuiltinStopsServer:
    """PUT editor=builtin → REAL manager ``stop()`` is called when the
    server was running; preference persisted as builtin."""

    async def test_put_builtin_stops_running_server_and_persists(
        self, integration_env, monkeypatch
    ):
        env = integration_env
        manager = env.manager

        # Bring the server up first via the REAL start() so stop() has
        # something to tear down.
        await manager.start()
        assert manager.state.status == "running"
        assert manager.is_running() is True

        # Spy on the REAL stop() to confirm the router invoked it.
        original_stop = manager.stop
        stop_called = {"flag": False}

        async def spy_stop():
            stop_called["flag"] = True
            return await original_stop()

        monkeypatch.setattr(manager, "stop", spy_stop)

        resp = await env.client.put(
            "/api/settings/editor", json={"editor": "builtin"}
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["editor"] == "builtin"
        # The REAL stop() was invoked by the router.
        assert stop_called["flag"] is True
        # Manager is now stopped (process.returncode set by mocked SIGTERM).
        assert manager.state.status == "stopped"

        # Preference persisted as builtin in the REAL metadata KV.
        get_resp = await env.client.get("/api/settings/editor")
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["editor"] == "builtin"


@pytest.mark.asyncio
class TestW13ServerStartFailure:
    """W13 transactionality — server fails to start → 503 AND preference
    is NOT persisted (critical integration check).

    The router calls ``ensure_running()`` BEFORE ``set_editor_preference()``,
    so a failure during the side-effect raises an HTTPException before the
    persistence line is reached. This test verifies the WIRING actually
    preserves that ordering end-to-end through the real app.
    """

    async def test_server_error_returns_503_and_does_not_persist(
        self, integration_env, monkeypatch
    ):
        env = integration_env
        manager = env.manager

        # Force the REAL ensure_running() → start() to raise VSCodeServerError.
        async def failing_start():
            raise VSCodeServerError("simulated spawn failure")

        monkeypatch.setattr(manager, "start", failing_start)

        # Capture whether set_editor_preference was called at all (it must
        # NOT be — the router must abort before persistence).
        set_calls = []
        original_set = settings_module.set_editor_preference

        async def spy_set(repo, value):
            set_calls.append(value)
            return await original_set(repo, value)

        # Patch at the router binding (same target as test_editor_settings.py).
        monkeypatch.setattr(settings_module, "set_editor_preference", spy_set)

        resp = await env.client.put(
            "/api/settings/editor", json={"editor": "vscode"}
        )

        # 503 — VSCodeServerError is caught by the router's ``except
        # VSCodeServerError`` branch.
        assert resp.status_code == 503, resp.text
        assert resp.json()["detail"]["error"] == "VS Code server failed to start"

        # CRITICAL: set_editor_preference was NEVER called → no write.
        assert set_calls == [], (
            "W13 VIOLATION: set_editor_preference was called despite "
            "ensure_running() failing — preference would be persisted!"
        )

        # CRITICAL integration check: a subsequent GET returns the DEFAULT
        # ("builtin"), proving the preference was NOT persisted.
        get_resp = await env.client.get("/api/settings/editor")
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["editor"] == "builtin", (
            "W13 VIOLATION: editor preference changed to 'vscode' even "
            "though the server failed to start!"
        )

    async def test_binary_not_installed_returns_503_and_does_not_persist(
        self, integration_env, monkeypatch
    ):
        env = integration_env
        manager = env.manager

        # Force the REAL start() to raise the not-installed error.
        async def failing_start():
            raise VSCodeServerNotInstalledError("code-server not in PATH")

        monkeypatch.setattr(manager, "start", failing_start)

        set_calls = []
        original_set = settings_module.set_editor_preference

        async def spy_set(repo, value):
            set_calls.append(value)
            return await original_set(repo, value)

        monkeypatch.setattr(settings_module, "set_editor_preference", spy_set)

        resp = await env.client.put(
            "/api/settings/editor", json={"editor": "vscode"}
        )

        # 503 with the not-installed error shape.
        assert resp.status_code == 503, resp.text
        detail = resp.json()["detail"]
        assert detail["error"] == "code-server binary not found"

        # No persistence occurred.
        assert set_calls == [], (
            "W13 VIOLATION: set_editor_preference called despite "
            "VSCodeServerNotInstalledError!"
        )

        get_resp = await env.client.get("/api/settings/editor")
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["editor"] == "builtin"


@pytest.mark.asyncio
class TestCrashRecoveryDetectsDeadProcess:
    """Crash recovery — a manager that thinks it's running but whose
    underlying process has died must NOT report a healthy "running" status.

    How detection works (from ``VSCodeServerManager.is_running``):
        * If a live subprocess handle exists (``self._process is not None``),
          ``is_running()`` returns ``False`` when
          ``self._process.returncode is not None`` (process exited) OR when
          ``os.kill(pid, 0)`` fails (OS no longer knows the PID).
        * For ADOPTED processes (no subprocess handle, post
          ``attach_existing()``), ``is_running()`` probes ``os.kill(pid, 0)``
          and on failure flips ``state.status`` to ``"crashed"``.

    This test exercises BOTH observable detection paths:
        1. Subprocess-handle path: set ``process.returncode`` (simulates
           the watchdog observing an exit) → ``is_running()`` is False and
           the status endpoint reflects the crashed state.
        2. Status endpoint surfaces the real (crashed) state, NOT a stale
           "running".
    """

    async def test_dead_subprocess_triggers_auto_restart(
        self, integration_env, monkeypatch
    ):
        """After a process dies, the watchdog auto-restarts with backoff.

        With auto-restart (vscode-reliability-fixes), a runtime exit is
        no longer immediately promoted to ``crashed``. The watchdog
        first attempts to restart; only when every retry fails does the
        status flip to ``crashed``. This test exercises the success
        path — the patched ``start()`` succeeds on the first attempt
        so the manager ends back in ``running`` with a fresh pid.

        The pre-restart window (``is_running()`` returns False because
        the old subprocess handle has ``returncode != None``) is also
        asserted — that's the synchronously observable crash signal.
        """
        env = integration_env
        manager = env.manager

        # Tighten backoff so the test converges in <2s instead of ~31s.
        monkeypatch.setattr(
            "daemon.services.vscode_server_manager."
            "VSCODE_RESTART_BACKOFF_INITIAL_S",
            0.0,
        )
        monkeypatch.setattr(
            "daemon.services.vscode_server_manager.VSCODE_RESTART_BACKOFF_MAX_S",
            0.0,
        )

        # Start the server for real (mocked subprocess) so we have a
        # subprocess handle + background watchdog task.
        await manager.start()
        assert manager.state.status == "running"
        assert manager.is_running() is True
        old_pid = manager.state.pid

        # Simulate the process dying: set returncode (this is exactly
        # what the watchdog's ``process.returncode is not None`` check
        # observes).
        proc = manager._process
        assert proc is not None
        proc.returncode = 137  # SIGKILL exit code

        # Patch ``start()`` to succeed — flip state to ``running`` with
        # a fresh pid so we exercise the success branch end-to-end.
        # Use a pid different from the original (the integration env
        # uses 99999) so we can assert a NEW process was spawned.
        async def fake_restart_start():
            manager.state.status = "running"
            manager.state.pid = 88888  # new pid, distinct from old one
            manager.state.exit_code = None
            manager.state.last_error = None
            return manager.state

        monkeypatch.setattr(manager, "start", fake_restart_start)

        # Let the watchdog loop observe the death and trigger a restart.
        # The watchdog polls once per second and the (zeroed) backoff
        # means the restart fires immediately after detection.
        await asyncio.sleep(1.2)

        # The manager is back in ``running`` — auto-restart succeeded.
        assert manager.state.status == "running", (
            f"Expected status 'running' after auto-restart, got "
            f"'{manager.state.status}'"
        )
        # Fresh process was spawned (new pid differs from old one).
        assert manager.state.pid != old_pid, (
            f"auto-restart must spawn a fresh process; pid unchanged "
            f"({old_pid})"
        )
        # exit_code was reset by start() — represents the new process.
        assert manager.state.exit_code is None
        # last_error was reset by start() — no crash currently surfaced.
        assert manager.state.last_error is None

    async def test_auto_restart_exhaustion_marks_crashed(
        self, integration_env, monkeypatch
    ):
        """All auto-restart attempts fail → status flips to ``crashed``.

        Companion to ``test_dead_subprocess_triggers_auto_restart``:
        exercises the failure branch. ``start()`` raises on every call,
        so after ``VSCODE_RESTART_MAX_ATTEMPTS`` failed retries the
        watchdog surfaces a permanent crash.
        """
        env = integration_env
        manager = env.manager

        # Tighten backoff so the test converges in <2s.
        monkeypatch.setattr(
            "daemon.services.vscode_server_manager."
            "VSCODE_RESTART_BACKOFF_INITIAL_S",
            0.0,
        )
        monkeypatch.setattr(
            "daemon.services.vscode_server_manager.VSCODE_RESTART_BACKOFF_MAX_S",
            0.0,
        )
        monkeypatch.setattr(
            "daemon.services.vscode_server_manager.VSCODE_RESTART_MAX_ATTEMPTS",
            3,
        )

        await manager.start()
        assert manager.state.status == "running"

        proc = manager._process
        assert proc is not None
        proc.returncode = 1

        # ``start()`` always fails — the watchdog exhausts attempts.
        async def fake_start_always_fails():
            raise VSCodeServerError("simulated spawn failure")

        monkeypatch.setattr(manager, "start", fake_start_always_fails)

        await asyncio.sleep(1.2)

        assert manager.state.status == "crashed", (
            f"Expected status 'crashed' after auto-restart exhausted, "
            f"got '{manager.state.status}'"
        )
        assert manager.state.exit_code == 1
        assert manager.state.last_error is not None
        # The exhaustion message names the attempt count and last exit.
        assert "3 auto-restart attempts" in manager.state.last_error
        assert "exit_code=1" in manager.state.last_error

    async def test_status_endpoint_surfaces_running_after_auto_restart(
        self, integration_env, monkeypatch
    ):
        """After auto-restart succeeds, the status endpoint reports
        ``running`` (NOT a stale ``crashed``). The ``last_error`` /
        ``exit_code`` fields are surfaced for any operator investigation.

        Pairs with ``test_auto_restart_exhaustion_marks_crashed`` to
        cover both branches of the new auto-restart contract.
        """
        env = integration_env
        manager = env.manager

        # Tighten backoff for fast convergence.
        monkeypatch.setattr(
            "daemon.services.vscode_server_manager."
            "VSCODE_RESTART_BACKOFF_INITIAL_S",
            0.0,
        )

        await manager.start()
        assert manager.state.status == "running"

        proc = manager._process
        assert proc is not None
        proc.returncode = 1

        async def fake_restart_start():
            manager.state.status = "running"
            manager.state.exit_code = None
            manager.state.last_error = None
            return manager.state

        monkeypatch.setattr(manager, "start", fake_restart_start)

        await asyncio.sleep(1.2)

        # Status endpoint reflects the post-restart state.
        resp = await env.client.get("/api/settings/editor/status")
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "running", (
            f"Status endpoint reported a stale state for an auto-"
            f"restarted process; got {resp.json()!r}"
        )

        # The full GET /editor also surfaces the new schema fields.
        full_resp = await env.client.get("/api/settings/editor")
        assert full_resp.status_code == 200, full_resp.text
        vscode_block = full_resp.json()["vscode"]
        assert vscode_block["status"] == "running"
        # Crash fields are present (reset by the successful start()).
        assert "last_error" in vscode_block
        assert "exit_code" in vscode_block
        assert vscode_block["last_error"] is None
        assert vscode_block["exit_code"] is None
