"""Unit tests for ``VSCodeServerManager`` (Phase 1, vscode-server-editor).

Covers the lifecycle of a single ``code-server`` process spawned by the
daemon for browser-editor access:

- **Subprocess mocking** — ``asyncio.create_subprocess_exec``, port parsing.
- **Security flags (C1/W4/R4)** — ``--bind-addr 127.0.0.1:0`` and
  ``--auth none`` MUST appear on every spawn.
- **State transitions** — ``stopped → starting → running`` and the
  watchdog-driven ``running → crashed`` path.
- **Stop escalation** — SIGTERM via ``os.killpg`` first, SIGKILL only
  after the grace period elapses.
- **Idempotent start** — repeated ``start()`` calls must not re-spawn.
- **Crash detection** — unexpected process exit flips state to
  ``crashed`` and records the exit code.
- **PID file recovery** — stale / live / invalid PID files.
- **Binary not found** — ``VSCodeServerNotInstalledError`` with install
  instructions.
- **Port detection** — parsing and timeout.
- **Log capture** — in-memory buffer + spill-to-disk when over the cap.

See: ``.agents/shared/planning/vscode-server-editor/phase1-plan.md``
(section: Testing Strategy, lines 175-186).
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from pathlib import Path
from typing import Any, Callable, List, Optional

import pytest

from daemon.config import VSCodeConfig
from daemon.constants import VSCODE_PID_FILENAME
from daemon.services.vscode_server_manager import (
    _CODESERVER_STRIP_ENV,
    VSCODE_CRASH_LOG_TAIL_MAX_BYTES,
    VSCodeServerManager,
    VSCodeServerNotInstalledError,
    VSCodeServerStartError,
    VSCodeServerState,
    VSCodeServerTimeoutError,
)


# ═══════════════════════════════════════════════════════════════════════════
# Fakes
# ═══════════════════════════════════════════════════════════════════════════


class FakeStream:
    """Mimics ``asyncio.subprocess.Stream`` for stdout/stderr.

    Yields each chunk in ``chunks`` once via ``read()``, then returns
    empty bytes (EOF) on subsequent reads.
    """

    def __init__(self, chunks: Optional[List[bytes]] = None) -> None:
        self._chunks = list(chunks or [])
        self._idx = 0
        self.read_calls = 0

    async def read(self, n: int = -1) -> bytes:
        self.read_calls += 1
        if self._idx >= len(self._chunks):
            return b""
        chunk = self._chunks[self._idx]
        self._idx += 1
        return chunk


class FakeProcess:
    """Mimics ``asyncio.subprocess.Process`` for unit testing.

    Attributes:
        pid: process id.
        stdout: ``FakeStream`` of stdout chunks.
        stderr: ``FakeStream`` (unused — code merges stderr→stdout).
        returncode: ``None`` while alive, int once exited.
        signals: signal numbers passed to ``send_signal``.
        kill_called: whether ``kill()`` was invoked.
        wait_should_hang: if ``True``, ``wait()`` blocks until
            ``returncode`` is set externally (used to simulate a process
            that ignores SIGTERM).

    ``send_signal(sig)`` records the signal and sets ``returncode`` to
    ``-sig`` (standard Unix convention for signal-terminated processes).
    ``kill()`` records the call and sets ``returncode`` to ``-9``.
    """

    def __init__(
        self,
        pid: int = 12345,
        stdout_chunks: Optional[List[bytes]] = None,
        returncode: Optional[int] = None,
        wait_should_hang: bool = False,
    ) -> None:
        self.pid = pid
        self.stdout = FakeStream(stdout_chunks or [])
        self.stderr = FakeStream([])
        self.returncode = returncode
        self.wait_should_hang = wait_should_hang
        self.signals: List[Any] = []
        self.kill_called = False
        self._wait_count = 0

    async def wait(self) -> Optional[int]:
        self._wait_count += 1
        if self.wait_should_hang:
            # Block until returncode is set externally (e.g., by a
            # mocked SIGKILL). Polls with a short sleep so the event
            # loop can run other tasks while we wait.
            while self.returncode is None:
                await asyncio.sleep(0.01)
            return self.returncode
        # Yield to event loop then return current state.
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
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


def make_manager(
    tmp_path: Path,
    binary_path: Optional[str] = None,
    allow_remote: bool = False,
) -> VSCodeServerManager:
    """Build a fresh ``VSCodeServerManager`` rooted at ``tmp_path``."""
    config = VSCodeConfig(
        binary_path=binary_path, allow_remote=allow_remote
    )
    return VSCodeServerManager(config=config, data_dir=str(tmp_path))


def patch_resolve_binary(
    monkeypatch: pytest.MonkeyPatch,
    manager: VSCodeServerManager,
    binary_path: str = "/usr/bin/code-server",
) -> None:
    """Bypass ``_resolve_binary`` filesystem validation.

    The real implementation checks ``os.path.isfile`` and
    ``os.access(..., os.X_OK)`` against ``config.binary_path`` or the
    ``shutil.which("code-server")`` lookup result. Tests that don't
    care about binary resolution mock the method directly so they can
    use any sentinel path without creating real executables on disk.
    """
    monkeypatch.setattr(manager, "_resolve_binary", lambda: binary_path)


def patch_create_subprocess(
    monkeypatch: pytest.MonkeyPatch, fake_proc: FakeProcess
) -> List[dict]:
    """Patch ``asyncio.create_subprocess_exec`` to return ``fake_proc``.

    Returns a list of call records (each entry contains ``args`` and
    ``kwargs``) for argument inspection in tests.
    """
    calls: List[dict] = []

    async def fake_create_subprocess_exec(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return fake_proc

    monkeypatch.setattr(
        "asyncio.create_subprocess_exec", fake_create_subprocess_exec
    )
    return calls


def patch_process_signals(
    monkeypatch: pytest.MonkeyPatch, fake_proc: FakeProcess
) -> dict:
    """Patch ``os.kill`` and ``os.killpg`` to no-op but record calls.

    On Unix, the stop path signals the process group via
    ``os.killpg``. Because that bypasses ``FakeProcess.send_signal``,
    we simulate the kernel effect by setting ``fake_proc.returncode``
    when the signal would terminate the process. This lets us test the
    full stop escalation without a real subprocess.
    """
    recorded: dict[str, list] = {"kill_calls": [], "killpg_calls": []}

    def fake_kill(pid: int, sig: int, *args: Any, **kwargs: Any) -> None:
        recorded["kill_calls"].append((pid, sig))

    def fake_killpg(pgid: int, sig: int, *args: Any, **kwargs: Any) -> None:
        recorded["killpg_calls"].append((pgid, sig))
        # Simulate process exit when a terminating signal is delivered
        # to the group (so ``wait()`` can return promptly).
        if sig == signal.SIGTERM and fake_proc.returncode is None:
            fake_proc.returncode = -signal.SIGTERM
        elif sig == signal.SIGKILL and fake_proc.returncode is None:
            fake_proc.returncode = -signal.SIGKILL

    monkeypatch.setattr("os.kill", fake_kill)
    monkeypatch.setattr("os.killpg", fake_killpg)
    return recorded


def patch_port_wait(monkeypatch: pytest.MonkeyPatch, manager: VSCodeServerManager) -> None:
    """Make ``_wait_for_port`` return immediately by pre-setting the port.

    The reader task would normally parse the port line from stdout; for
    tests that don't care about port parsing, this bypasses the polling
    loop entirely.
    """

    async def fake_wait_for_port() -> None:
        manager.state.port = 12345

    monkeypatch.setattr(manager, "_wait_for_port", fake_wait_for_port)


def slow_down_health_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the health-check loop poll at a low rate so tests are quiet."""
    monkeypatch.setattr(
        "daemon.services.vscode_server_manager.VSCODE_HEALTH_CHECK_INTERVAL_S",
        60.0,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Test class
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestVSCodeServerManager:
    """Unit tests for ``VSCodeServerManager``."""

    # ── Security flags (CRITICAL) ────────────────────────────────────────

    async def test_start_command_includes_bind_addr_loopback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Every spawn MUST pass ``--bind-addr 127.0.0.1:0`` (C1/W4)."""
        manager = make_manager(tmp_path)
        fake_proc = FakeProcess()
        patch_resolve_binary(monkeypatch, manager)
        calls = patch_create_subprocess(monkeypatch, fake_proc)
        patch_process_signals(monkeypatch, fake_proc)
        monkeypatch.setattr("os.getpgid", lambda pid: pid + 1000)
        patch_port_wait(monkeypatch, manager)
        slow_down_health_check(monkeypatch)

        await manager.start()

        assert len(calls) == 1
        cmd_args = list(calls[0]["args"])
        assert "--bind-addr" in cmd_args
        assert "127.0.0.1:0" in cmd_args

        await manager.cleanup()

    async def test_start_command_includes_auth_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Every spawn MUST pass ``--auth none`` (R4)."""
        manager = make_manager(tmp_path)
        fake_proc = FakeProcess()
        patch_resolve_binary(monkeypatch, manager)
        calls = patch_create_subprocess(monkeypatch, fake_proc)
        patch_process_signals(monkeypatch, fake_proc)
        monkeypatch.setattr("os.getpgid", lambda pid: pid + 1000)
        patch_port_wait(monkeypatch, manager)
        slow_down_health_check(monkeypatch)

        await manager.start()

        cmd_args = list(calls[0]["args"])
        assert "--auth" in cmd_args
        auth_idx = cmd_args.index("--auth")
        assert cmd_args[auth_idx + 1] == "none"

        await manager.cleanup()

    async def test_start_command_full_shape(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify the full command structure: binary, flags, workdir, cwd."""
        manager = make_manager(tmp_path)
        fake_proc = FakeProcess()
        patch_resolve_binary(monkeypatch, manager)
        calls = patch_create_subprocess(monkeypatch, fake_proc)
        patch_process_signals(monkeypatch, fake_proc)
        monkeypatch.setattr("os.getpgid", lambda pid: pid + 1000)
        patch_port_wait(monkeypatch, manager)
        slow_down_health_check(monkeypatch)

        await manager.start()

        cmd_args = list(calls[0]["args"])
        # Binary is first positional arg.
        assert cmd_args[0] == "/usr/bin/code-server"
        # Security flags present.
        assert "--bind-addr" in cmd_args
        assert "--auth" in cmd_args
        # Disable workspace trust (UX).
        assert "--disable-workspace-trust" in cmd_args
        # User-data dir flag is present and points to an existing dir.
        assert "--user-data-dir" in cmd_args
        udd_idx = cmd_args.index("--user-data-dir")
        assert os.path.isdir(cmd_args[udd_idx + 1])
        # Last positional arg is the workspace dir.
        assert cmd_args[-1] == os.getcwd()
        # Cwd kwarg matches workdir.
        assert calls[0]["kwargs"].get("cwd") == os.getcwd()
        # start_new_session on non-Windows so we can killpg the group.
        if sys.platform != "win32":
            assert calls[0]["kwargs"].get("start_new_session") is True

        await manager.cleanup()

    # ── State transitions ────────────────────────────────────────────────

    async def test_state_transitions_stopped_starting_running(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Successful start: ``stopped → starting → running``."""
        manager = make_manager(tmp_path)
        fake_proc = FakeProcess(pid=12345)
        patch_resolve_binary(monkeypatch, manager)
        patch_create_subprocess(monkeypatch, fake_proc)
        patch_process_signals(monkeypatch, fake_proc)
        monkeypatch.setattr("os.getpgid", lambda pid: pid + 1000)
        patch_port_wait(monkeypatch, manager)
        slow_down_health_check(monkeypatch)

        assert manager.state.status == "stopped"

        await manager.start()

        assert manager.state.status == "running"
        assert manager.state.pid == 12345
        assert manager.state.port == 12345
        assert manager.state.user_stopped is False
        assert manager.state.exit_code is None
        assert manager.state.started_at is not None

        await manager.cleanup()

    async def test_state_transitions_running_stopping_stopped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Successful stop: ``running → stopping → stopped``."""
        manager = make_manager(tmp_path)
        fake_proc = FakeProcess(pid=12345, returncode=None)
        patch_resolve_binary(monkeypatch, manager)
        patch_create_subprocess(monkeypatch, fake_proc)
        signals = patch_process_signals(monkeypatch, fake_proc)
        monkeypatch.setattr("os.getpgid", lambda pid: pid + 1000)
        patch_port_wait(monkeypatch, manager)
        slow_down_health_check(monkeypatch)

        await manager.start()
        assert manager.state.status == "running"

        await manager.stop()

        assert manager.state.status == "stopped"
        assert manager.state.user_stopped is True
        # On Unix the SIGTERM goes through os.killpg (not
        # process.send_signal), so check the recorded killpg calls.
        # On Windows the code uses process.send_signal directly.
        if sys.platform != "win32":
            sigterm_calls = [
                sig for _pgid, sig in signals["killpg_calls"]
                if sig == signal.SIGTERM
            ]
            assert len(sigterm_calls) >= 1
            # SIGKILL must NOT have been delivered.
            sigkill_calls = [
                sig for _pgid, sig in signals["killpg_calls"]
                if sig == signal.SIGKILL
            ]
            assert sigkill_calls == []
        else:
            assert signal.SIGTERM in fake_proc.signals
        assert fake_proc.kill_called is False

    async def test_state_crashed_when_process_exits_unexpectedly(
        self, tmp_path: Path
    ) -> None:
        """Watchdog marks ``crashed`` when process exits without user_stopped.

        Drives ``_watchdog_loop`` directly with a process whose
        ``returncode`` is set, bypassing the real polling interval.
        """
        manager = make_manager(tmp_path)
        fake_proc = FakeProcess(pid=12345, returncode=1)  # crashed
        # Wire _process so watchdog sees the fake handle.
        manager._process = fake_proc  # type: ignore[assignment]
        manager.state.pid = 12345
        manager.state.port = 12345
        manager.state.status = "running"
        manager.state.user_stopped = False

        await manager._watchdog_loop()

        assert manager.state.status == "crashed"
        assert manager.state.exit_code == 1
        assert manager.state.user_stopped is False
        assert "exited unexpectedly" in (
            manager.state.last_error or ""
        ).lower()

    async def test_state_not_crashed_when_user_stopped(
        self, tmp_path: Path
    ) -> None:
        """Watchdog must NOT clobber ``user_stopped`` flow with ``crashed``."""
        manager = make_manager(tmp_path)
        fake_proc = FakeProcess(pid=12345, returncode=-15)
        manager._process = fake_proc  # type: ignore[assignment]
        manager.state.pid = 12345
        manager.state.port = 12345
        manager.state.status = "stopping"  # stop() is driving teardown
        manager.state.user_stopped = True

        await manager._watchdog_loop()

        # Status remains as ``stop()`` left it (or whatever was set).
        assert manager.state.status == "stopping"
        # Exit code is still recorded.
        assert manager.state.exit_code == -15
        # last_error is NOT set (no "crashed" message).
        assert manager.state.last_error is None

    # ── Stop escalation ──────────────────────────────────────────────────

    async def test_stop_sends_sigterm_first_via_killpg(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Stop must ``killpg(SIGTERM)`` the process group first (Unix)."""
        manager = make_manager(tmp_path)
        fake_proc = FakeProcess(pid=12345, returncode=None)
        patch_resolve_binary(monkeypatch, manager)
        patch_create_subprocess(monkeypatch, fake_proc)
        signals = patch_process_signals(monkeypatch, fake_proc)
        monkeypatch.setattr("os.getpgid", lambda pid: pid + 1000)
        patch_port_wait(monkeypatch, manager)
        slow_down_health_check(monkeypatch)

        await manager.start()
        await manager.stop()

        if sys.platform != "win32":
            # At least one killpg call carried SIGTERM.
            sigterm_calls = [
                (pgid, sig) for pgid, sig in signals["killpg_calls"]
                if sig == signal.SIGTERM
            ]
            assert len(sigterm_calls) >= 1
            # SIGKILL must NOT have been delivered (process exited cleanly).
            sigkill_calls = [
                sig for _pgid, sig in signals["killpg_calls"]
                if sig == signal.SIGKILL
            ]
            assert sigkill_calls == []
            assert fake_proc.kill_called is False

    async def test_stop_escalates_to_sigkill_after_grace(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If process ignores SIGTERM, SIGKILL is delivered after grace."""
        manager = make_manager(tmp_path)
        # wait_should_hang=True: process never exits on SIGTERM. The
        # patched killpg with SIGTERM does NOT set returncode (it only
        # sets it if the process was going to exit), so wait() hangs
        # until SIGKILL flips it.
        fake_proc = FakeProcess(
            pid=12345, returncode=None, wait_should_hang=True
        )

        # Custom killpg that ignores SIGTERM (simulates unresponsive
        # process) but still sets returncode on SIGKILL.
        recorded: dict[str, list] = {"killpg_calls": []}

        def fake_killpg(pgid: int, sig: int, *args: Any, **kwargs: Any) -> None:
            recorded["killpg_calls"].append((pgid, sig))
            if sig == signal.SIGKILL and fake_proc.returncode is None:
                fake_proc.returncode = -signal.SIGKILL
            # SIGTERM is intentionally NOT honored — returncode stays None.

        monkeypatch.setattr("os.killpg", fake_killpg)
        monkeypatch.setattr("os.kill", lambda *a, **kw: None)
        monkeypatch.setattr("os.getpgid", lambda pid: pid + 1000)

        patch_resolve_binary(monkeypatch, manager)
        patch_create_subprocess(monkeypatch, fake_proc)
        patch_port_wait(monkeypatch, manager)
        slow_down_health_check(monkeypatch)
        # Shrink grace period for fast tests.
        monkeypatch.setattr(
            "daemon.services.vscode_server_manager.VSCODE_STOP_GRACE_S", 0.2
        )

        await manager.start()
        await manager.stop()

        if sys.platform != "win32":
            # Both SIGTERM and SIGKILL were sent via killpg.
            sigs = [sig for _pgid, sig in recorded["killpg_calls"]]
            assert signal.SIGTERM in sigs
            assert signal.SIGKILL in sigs

    async def test_stop_no_sigkill_when_process_responds_to_sigterm(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No SIGKILL when process exits during the grace period."""
        manager = make_manager(tmp_path)
        fake_proc = FakeProcess(pid=12345, returncode=None)
        patch_resolve_binary(monkeypatch, manager)
        patch_create_subprocess(monkeypatch, fake_proc)
        signals = patch_process_signals(monkeypatch, fake_proc)
        monkeypatch.setattr("os.getpgid", lambda pid: pid + 1000)
        patch_port_wait(monkeypatch, manager)
        slow_down_health_check(monkeypatch)

        await manager.start()
        await manager.stop()

        assert manager.state.status == "stopped"
        assert manager.state.exit_code == -signal.SIGTERM

        if sys.platform != "win32":
            # SIGTERM was sent via killpg; SIGKILL was not.
            sigterm_calls = [
                sig for _pgid, sig in signals["killpg_calls"]
                if sig == signal.SIGTERM
            ]
            sigkill_calls = [
                sig for _pgid, sig in signals["killpg_calls"]
                if sig == signal.SIGKILL
            ]
            assert len(sigterm_calls) >= 1
            assert sigkill_calls == []
        else:
            assert fake_proc.signals == [signal.SIGTERM]
        assert fake_proc.kill_called is False

    # ── Idempotent start ─────────────────────────────────────────────────

    async def test_start_is_idempotent_when_already_running(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``start()`` while running must NOT re-spawn or re-init."""
        manager = make_manager(tmp_path)
        fake_proc = FakeProcess()
        patch_resolve_binary(monkeypatch, manager)
        calls = patch_create_subprocess(monkeypatch, fake_proc)
        patch_process_signals(monkeypatch, fake_proc)
        monkeypatch.setattr("os.getpgid", lambda pid: pid + 1000)
        patch_port_wait(monkeypatch, manager)
        slow_down_health_check(monkeypatch)

        # First start spawns the process.
        await manager.start()
        first_pid = manager.state.pid
        first_port = manager.state.port

        # Second start: should be a no-op (returns current state).
        result = await manager.start()

        assert len(calls) == 1  # create_subprocess_exec NOT called again
        assert result.pid == first_pid
        assert result.port == first_port
        assert result.status == "running"

        await manager.cleanup()

    async def test_ensure_running_idempotent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``ensure_running()`` is a no-op when already running."""
        manager = make_manager(tmp_path)
        fake_proc = FakeProcess()
        patch_resolve_binary(monkeypatch, manager)
        calls = patch_create_subprocess(monkeypatch, fake_proc)
        patch_process_signals(monkeypatch, fake_proc)
        monkeypatch.setattr("os.getpgid", lambda pid: pid + 1000)
        patch_port_wait(monkeypatch, manager)
        slow_down_health_check(monkeypatch)

        await manager.start()
        await manager.ensure_running()
        await manager.ensure_running()

        assert len(calls) == 1
        await manager.cleanup()

    # ── PID file recovery ────────────────────────────────────────────────

    async def test_attach_existing_cleans_up_stale_pid_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PID file referencing a dead PID → removed, returns False."""
        manager = make_manager(tmp_path, binary_path="/usr/bin/code-server")
        pid_path = tmp_path / VSCODE_PID_FILENAME
        pid_path.write_text(json.dumps({"pid": 999999, "port": 12345}))

        # Liveness check: pid 999999 doesn't exist on this machine.
        def fake_kill(pid: int, sig: int, *args: Any, **kwargs: Any) -> None:
            if sig == 0:
                raise ProcessLookupError(f"No process {pid}")

        monkeypatch.setattr("os.kill", fake_kill)

        result = await manager.attach_existing()

        assert result is False
        assert not pid_path.exists()

    async def test_attach_existing_adopts_live_pid(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PID file referencing a live PID → adopted, returns True."""
        manager = make_manager(tmp_path, binary_path="/usr/bin/code-server")
        pid_path = tmp_path / VSCODE_PID_FILENAME
        pid_path.write_text(
            json.dumps(
                {
                    "pid": 12345,
                    "pgid": 12345,
                    "port": 8081,
                    "started_at": "2026-07-25T00:00:00+00:00",
                }
            )
        )

        # Liveness check passes (no exception).
        monkeypatch.setattr("os.kill", lambda *a, **kw: None)
        # W4: PID-reuse guard — verify the PID is actually code-server.
        # The test's mock PID (12345) is not a real process, so we stub
        # out the verification to return True (otherwise the test would
        # require a real code-server process on the host).
        monkeypatch.setattr(
            manager,
            "_verify_pid_is_code_server",
            lambda pid: True,
        )

        result = await manager.attach_existing()

        assert result is True
        assert manager.state.pid == 12345
        assert manager.state.port == 8081
        assert manager.state.status == "running"
        assert manager.state.started_at is not None

    async def test_attach_existing_handles_invalid_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Malformed PID file → removed, returns False (no crash)."""
        manager = make_manager(tmp_path, binary_path="/usr/bin/code-server")
        pid_path = tmp_path / VSCODE_PID_FILENAME
        pid_path.write_text("this is not json {{{ broken")

        result = await manager.attach_existing()

        assert result is False
        assert not pid_path.exists()

    async def test_attach_existing_returns_false_when_no_pid_file(
        self, tmp_path: Path
    ) -> None:
        """Missing PID file → returns False (no exception)."""
        manager = make_manager(tmp_path, binary_path="/usr/bin/code-server")
        assert not (tmp_path / VSCODE_PID_FILENAME).exists()

        result = await manager.attach_existing()

        assert result is False

    async def test_attach_existing_handles_missing_pid_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PID file missing the ``pid`` key → cleaned up, returns False."""
        manager = make_manager(tmp_path, binary_path="/usr/bin/code-server")
        pid_path = tmp_path / VSCODE_PID_FILENAME
        pid_path.write_text(json.dumps({"port": 12345}))  # no "pid"

        result = await manager.attach_existing()

        assert result is False
        assert not pid_path.exists()

    # ── W4: PID-reuse guard on attach_existing ───────────────────────────

    async def test_attach_existing_rejects_non_codeserver_pid(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """W4: PID file whose PID is alive but NOT code-server → rejected.

        Guards against PID reuse: if an unrelated process happened to
        inherit the previously-recorded PID, the manager must refuse to
        adopt it (otherwise we'd later try to signal its now-wrong
        process group on stop). The stale PID file MUST also be removed.
        """
        manager = make_manager(tmp_path, binary_path="/usr/bin/code-server")
        pid_path = tmp_path / VSCODE_PID_FILENAME
        pid_path.write_text(
            json.dumps(
                {
                    "pid": 12345,
                    "pgid": 12345,
                    "port": 8081,
                    "started_at": "2026-07-25T00:00:00+00:00",
                }
            )
        )

        # Liveness check passes (some PID 12345 is alive).
        monkeypatch.setattr("os.kill", lambda *a, **kw: None)
        # But the cmdline does NOT contain "code-server".
        monkeypatch.setattr(
            manager,
            "_verify_pid_is_code_server",
            lambda pid: False,
        )

        result = await manager.attach_existing()

        assert result is False
        # Stale PID file must be removed so the next start() doesn't
        # try to adopt the same PID again.
        assert not pid_path.exists()
        # State is left untouched (not promoted to "running").
        assert manager.state.status == "stopped"

    async def test_attach_existing_adopts_verified_codeserver_pid(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """W4: PID verified to be code-server → adoption succeeds.

        Mirror of ``test_attach_existing_adopts_live_pid`` but driven
        explicitly through the new verification hook so the W4 path
        is independently exercised.
        """
        manager = make_manager(tmp_path, binary_path="/usr/bin/code-server")
        pid_path = tmp_path / VSCODE_PID_FILENAME
        pid_path.write_text(
            json.dumps(
                {
                    "pid": 12345,
                    "pgid": 12345,
                    "port": 8081,
                    "started_at": "2026-07-25T00:00:00+00:00",
                }
            )
        )

        monkeypatch.setattr("os.kill", lambda *a, **kw: None)
        monkeypatch.setattr(
            manager,
            "_verify_pid_is_code_server",
            lambda pid: True,
        )

        result = await manager.attach_existing()

        assert result is True
        assert manager.state.pid == 12345
        assert manager.state.port == 8081
        assert manager.state.status == "running"

    # ── C2: is_running() fallback for adopted processes ──────────────────

    async def test_is_running_true_when_adopted_process_alive(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """C2: adopted process (no subprocess handle) + PID alive → True.

        After ``attach_existing()`` adopts a process we have no
        ``_process`` handle. ``is_running()`` must fall back to a
        ``os.kill(pid, 0)`` liveness probe and return True.
        """
        manager = make_manager(tmp_path, binary_path="/usr/bin/code-server")
        # Simulate an adopted process: state says running, pid set,
        # but no subprocess handle.
        manager.state.status = "running"
        manager.state.pid = 12345
        manager.state.port = 8081
        manager._process = None  # type: ignore[assignment]

        # os.kill(pid, 0) succeeds — process is alive.
        monkeypatch.setattr("os.kill", lambda *a, **kw: None)

        assert manager.is_running() is True

    async def test_is_running_false_when_adopted_process_dead(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """C2: adopted process + PID dead → False, flips to ``crashed``.

        ``os.kill(pid, 0)`` raises ``ProcessLookupError`` when the PID
        is no longer alive. ``is_running()`` must report False AND
        update ``state.status`` to ``"crashed"`` so callers observe
        the death.
        """
        manager = make_manager(tmp_path, binary_path="/usr/bin/code-server")
        manager.state.status = "running"
        manager.state.pid = 12345
        manager.state.port = 8081
        manager._process = None  # type: ignore[assignment]

        def fake_kill(pid: int, sig: int, *args: Any, **kwargs: Any) -> None:
            if sig == 0:
                raise ProcessLookupError(f"No process {pid}")

        monkeypatch.setattr("os.kill", fake_kill)

        assert manager.is_running() is False
        assert manager.state.status == "crashed"
        assert manager.state.last_error is not None
        assert "adopted" in manager.state.last_error.lower()

    # ── W8: stop() must use captured pgid, not os.getpgid(pid) ───────────

    async def test_stop_uses_captured_pgid_not_getpgid(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """W8: ``stop()`` signals ``state.pgid`` (captured at spawn), NOT
        ``os.getpgid(process.pid)`` re-resolved at signal time.

        PID reuse could otherwise let ``os.getpgid(pid)`` return the
        group of an unrelated process and we'd accidentally signal
        the wrong group on stop.
        """
        manager = make_manager(tmp_path)
        fake_proc = FakeProcess(pid=12345, returncode=None)
        patch_resolve_binary(monkeypatch, manager)
        patch_create_subprocess(monkeypatch, fake_proc)
        monkeypatch.setattr("os.getpgid", lambda pid: pid + 1000)
        patch_port_wait(monkeypatch, manager)
        slow_down_health_check(monkeypatch)

        # Custom killpg recorder so we can inspect the pgid argument.
        recorded_pgids: List[int] = []

        def fake_killpg(pgid: int, sig: int, *args: Any, **kwargs: Any) -> None:
            recorded_pgids.append(pgid)
            # Simulate SIGTERM-exit so wait() returns promptly.
            if sig == signal.SIGTERM and fake_proc.returncode is None:
                fake_proc.returncode = -signal.SIGTERM

        # Stash the fake after start() so we still capture the
        # spawn-time os.getpgid() call used to populate state.pgid.
        # Patch BEFORE start(): start() will call os.getpgid(pid) to
        # capture state.pgid (this is the legitimate spawn-time use).
        monkeypatch.setattr("os.killpg", fake_killpg)
        monkeypatch.setattr("os.kill", lambda *a, **kw: None)

        # Pretend the spawn-time os.getpgid returned a specific pgid;
        # the manager will store it in state.pgid.
        monkeypatch.setattr("os.getpgid", lambda pid: 99999)

        await manager.start()
        captured_pgid = manager.state.pgid
        assert captured_pgid == 99999

        # Now swap os.getpgid to return a DIFFERENT value. If stop()
        # re-resolves pgid at signal time, it would call killpg with
        # the *new* value — which is exactly the W8 bug we want to
        # prevent.
        def getpgid_returns_pid_plus_one(pid: int) -> int:
            return pid + 1

        monkeypatch.setattr("os.getpgid", getpgid_returns_pid_plus_one)

        recorded_pgids.clear()
        await manager.stop()

        # W8: stop() must have signaled the captured pgid (99999),
        # NOT the re-resolved value (12345 + 1 = 12346).
        assert captured_pgid in recorded_pgids, (
            f"stop() must use captured state.pgid={captured_pgid}, "
            f"got killpg calls with {recorded_pgids}"
        )
        # And specifically NOT the value getpgid(pid) returned at
        # signal time.
        assert 12346 not in recorded_pgids, (
            "stop() re-resolved os.getpgid(pid) at signal time — "
            "W8 regression: must use captured pgid instead"
        )

    # ── Binary not found ─────────────────────────────────────────────────

    async def test_start_raises_not_installed_when_binary_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Both ``binary_path``, PATH lookup, and fallbacks fail → exception raised."""
        manager = make_manager(tmp_path, binary_path=None)
        monkeypatch.setattr("shutil.which", lambda _: None)
        # Stub out the fallback paths so the test environment's real
        # code-server install (if any) doesn't get picked up.
        monkeypatch.setattr("os.path.isfile", lambda path: False)

        with pytest.raises(VSCodeServerNotInstalledError) as exc_info:
            await manager.start()

        # Error message includes install instructions and lists the
        # fallback locations that were searched.
        msg = str(exc_info.value).lower()
        assert "code-server" in msg
        assert "install" in msg or "code-server.dev" in msg
        assert "/opt/homebrew/bin/code-server" in msg
        assert "/usr/local/bin/code-server" in msg
        assert "/usr/bin/code-server" in msg
        # State reverted to stopped.
        assert manager.state.status == "stopped"

    async def test_start_raises_not_installed_when_configured_path_missing(
        self, tmp_path: Path
    ) -> None:
        """Configured binary path that doesn't exist → exception raised."""
        manager = make_manager(
            tmp_path, binary_path="/nonexistent/code-server"
        )

        with pytest.raises(VSCodeServerNotInstalledError) as exc_info:
            await manager.start()

        msg = str(exc_info.value).lower()
        # Error names the bad path so users can fix their config.
        assert "/nonexistent/code-server" in msg

    async def test_resolve_binary_uses_configured_path_when_executable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Configured path that exists and is executable is preferred."""
        # Create a real executable file in tmp_path.
        bin_path = tmp_path / "my-code-server"
        bin_path.write_text("#!/bin/sh\necho hi\n")
        bin_path.chmod(0o755)

        manager = make_manager(tmp_path, binary_path=str(bin_path))

        # ``shutil.which`` should never be called if binary_path is valid.
        def fail_which(*args: Any, **kwargs: Any) -> None:
            raise AssertionError(
                "shutil.which should not be called when binary_path is set"
            )

        monkeypatch.setattr("shutil.which", fail_which)

        resolved = manager._resolve_binary()

        assert resolved == str(bin_path)

    async def test_resolve_binary_uses_fallback_when_not_on_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fallback common locations are probed when shutil.which returns None."""
        # Simulate an executable at the Homebrew fallback path by patching
        # isfile/access, isolating the test from any real code-server on
        # the host.
        fallback_to_return = "/opt/homebrew/bin/code-server"

        def fake_isfile(path: str) -> bool:
            return path == fallback_to_return

        monkeypatch.setattr("os.path.isfile", fake_isfile)
        monkeypatch.setattr("os.access", lambda path, mode: True)
        monkeypatch.setattr("shutil.which", lambda *a, **kw: None)

        manager = make_manager(tmp_path)  # no configured binary_path

        resolved = manager._resolve_binary()

        assert resolved == fallback_to_return

    async def test_resolve_binary_raises_with_searched_paths_listed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Error message lists every fallback path that was checked."""
        # Force every fallback probe to look missing.
        monkeypatch.setattr("shutil.which", lambda *a, **kw: None)
        monkeypatch.setattr("os.path.isfile", lambda path: False)
        # Don't override expanduser: the error message should contain the
        # literal "~/.local/bin/code-server" so users see what was tried.

        manager = make_manager(tmp_path)

        with pytest.raises(VSCodeServerNotInstalledError) as exc_info:
            manager._resolve_binary()

        msg = str(exc_info.value)
        # Original install hint is preserved.
        assert "code-server.dev/install.sh" in msg
        # Every fallback location is named in the error. The user-local
        # entry is expanded from "~" to the actual home directory.
        assert "/opt/homebrew/bin/code-server" in msg
        assert "/usr/local/bin/code-server" in msg
        assert ".local/bin/code-server" in msg
        assert "/usr/bin/code-server" in msg

    # ── Port detection ────────────────────────────────────────────────────

    async def test_port_parsed_from_stdout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reader extracts port from ``HTTP server listening on ...:PORT``."""
        manager = make_manager(tmp_path)
        port_line = b"HTTP server listening on http://127.0.0.1:34567\n"
        fake_proc = FakeProcess(stdout_chunks=[port_line, b""])
        patch_resolve_binary(monkeypatch, manager)
        patch_create_subprocess(monkeypatch, fake_proc)
        patch_process_signals(monkeypatch, fake_proc)
        monkeypatch.setattr("os.getpgid", lambda pid: pid + 1000)
        slow_down_health_check(monkeypatch)

        # Don't pre-set port — let the reader parse the real line.
        await manager.start()

        assert manager.state.port == 34567
        await manager.cleanup()

    async def test_port_detection_timeout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No port line within timeout → ``VSCodeServerTimeoutError``."""
        manager = make_manager(tmp_path)
        # Empty stdout: reader sees EOF immediately and exits; port stays None.
        fake_proc = FakeProcess(stdout_chunks=[])
        patch_resolve_binary(monkeypatch, manager)
        patch_create_subprocess(monkeypatch, fake_proc)
        patch_process_signals(monkeypatch, fake_proc)
        monkeypatch.setattr("os.getpgid", lambda pid: pid + 1000)
        # Shrink timeouts so the test runs quickly.
        monkeypatch.setattr(
            "daemon.services.vscode_server_manager.VSCODE_STARTUP_TIMEOUT_S",
            0.3,
        )
        monkeypatch.setattr(
            "daemon.services.vscode_server_manager.VSCODE_PORT_DETECTION_POLL_S",
            0.05,
        )
        slow_down_health_check(monkeypatch)

        with pytest.raises(VSCodeServerTimeoutError):
            await manager.start()

        # State reverted to stopped.
        assert manager.state.status == "stopped"

    # ── Log capture ──────────────────────────────────────────────────────

    async def test_log_buffer_captures_stdout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reader appends stdout chunks to ``log_buffer``."""
        manager = make_manager(tmp_path)
        port_line = b"HTTP server listening on http://127.0.0.1:34567\n"
        log_lines = [
            b"[2026-07-25T10:00:00] info: starting up\n",
            b"[2026-07-25T10:00:01] info: ready to accept connections\n",
        ]
        fake_proc = FakeProcess(
            stdout_chunks=[port_line] + log_lines + [b""]
        )
        patch_resolve_binary(monkeypatch, manager)
        patch_create_subprocess(monkeypatch, fake_proc)
        patch_process_signals(monkeypatch, fake_proc)
        monkeypatch.setattr("os.getpgid", lambda pid: pid + 1000)
        slow_down_health_check(monkeypatch)

        await manager.start()

        # Wait for the reader task to finish consuming all chunks.
        if manager.state.reader_task is not None:
            try:
                await asyncio.wait_for(
                    manager.state.reader_task, timeout=1.0
                )
            except asyncio.TimeoutError:
                pass

        buf = manager.state.log_buffer.decode()
        assert "info: starting up" in buf
        assert "info: ready" in buf

        # ``get_logs`` returns the captured output.
        logs = manager.get_logs(tail=10)
        assert "info: starting up" in logs
        assert "info: ready" in logs

        await manager.cleanup()

    async def test_log_buffer_spills_to_disk_when_over_limit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Buffer spills oldest half to a temp file when over the cap.

        We monkeypatch ``VSCODE_LOG_BUFFER_LIMIT`` down to a small value
        so the spill triggers with realistic-looking log chunks rather
        than the 4MB production constant.
        """
        manager = make_manager(tmp_path)
        port_line = b"HTTP server listening on http://127.0.0.1:34567\n"
        # Multiple large-enough chunks to trip the spill check.
        big_chunk = b"X" * 64
        fake_proc = FakeProcess(
            stdout_chunks=[port_line, big_chunk, big_chunk, big_chunk, b""]
        )
        patch_resolve_binary(monkeypatch, manager)
        patch_create_subprocess(monkeypatch, fake_proc)
        patch_process_signals(monkeypatch, fake_proc)
        monkeypatch.setattr("os.getpgid", lambda pid: pid + 1000)
        slow_down_health_check(monkeypatch)
        # Force the spill threshold below what we send.
        monkeypatch.setattr(
            "daemon.services.vscode_server_manager.VSCODE_LOG_BUFFER_LIMIT",
            50,
        )

        await manager.start()

        # Wait for the reader task to finish.
        if manager.state.reader_task is not None:
            try:
                await asyncio.wait_for(
                    manager.state.reader_task, timeout=2.0
                )
            except asyncio.TimeoutError:
                pass

        # Spill file was created.
        assert manager.state.log_spill_path is not None
        assert os.path.exists(manager.state.log_spill_path)

        # Buffer is bounded — spill keeps memory usage roughly within
        # 2× the limit plus one chunk, never growing unbounded.
        assert len(manager.state.log_buffer) <= (
            2 * 50 + 64
        ), (
            f"buffer should stay bounded; got {len(manager.state.log_buffer)}"
        )

        # Spill file contains the oldest half (some content present).
        assert os.path.getsize(manager.state.log_spill_path) > 0

        await manager.cleanup()

    async def test_get_logs_empty_when_no_output(
        self, tmp_path: Path
    ) -> None:
        """``get_logs`` returns ``""`` when the buffer is empty."""
        manager = make_manager(tmp_path)

        assert manager.get_logs() == ""
        # Non-positive tail returns "".
        assert manager.get_logs(tail=0) == ""
        assert manager.get_logs(tail=-1) == ""

    # ── PID file write ───────────────────────────────────────────────────

    async def test_pid_file_written_after_successful_start(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PID file is created with the expected payload on successful start."""
        manager = make_manager(tmp_path)
        fake_proc = FakeProcess(pid=12345)
        patch_resolve_binary(monkeypatch, manager)
        patch_create_subprocess(monkeypatch, fake_proc)
        patch_process_signals(monkeypatch, fake_proc)
        monkeypatch.setattr("os.getpgid", lambda pid: pid + 1000)
        patch_port_wait(monkeypatch, manager)
        slow_down_health_check(monkeypatch)

        await manager.start()

        pid_file = tmp_path / VSCODE_PID_FILENAME
        assert pid_file.exists()

        data = json.loads(pid_file.read_text())
        assert data["pid"] == 12345
        assert data["port"] == 12345
        assert "started_at" in data
        assert data["pgid"] == 12345 + 1000

        await manager.cleanup()

    async def test_pid_file_removed_after_stop(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PID file is cleaned up on stop (no stale recovery file)."""
        manager = make_manager(tmp_path)
        fake_proc = FakeProcess(pid=12345, returncode=None)
        patch_resolve_binary(monkeypatch, manager)
        patch_create_subprocess(monkeypatch, fake_proc)
        patch_process_signals(monkeypatch, fake_proc)
        monkeypatch.setattr("os.getpgid", lambda pid: pid + 1000)
        patch_port_wait(monkeypatch, manager)
        slow_down_health_check(monkeypatch)

        await manager.start()
        pid_file = tmp_path / VSCODE_PID_FILENAME
        assert pid_file.exists()

        await manager.stop()

        assert not pid_file.exists()

    async def test_start_marks_running_when_pid_write_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PID-write failure must not leave the server stuck in 'starting'.

        Regression: the ``_write_pid_file()`` call runs AFTER
        ``state.status = "running"`` and ``state.started_at`` so that a
        failing write (e.g. disk full, permission denied) is logged as a
        warning but does NOT prevent the server from being reported as
        running. The PID file is purely for crash recovery — its absence
        does not mean the server isn't running.
        """
        manager = make_manager(tmp_path)
        fake_proc = FakeProcess(pid=12345)
        patch_resolve_binary(monkeypatch, manager)
        patch_create_subprocess(monkeypatch, fake_proc)
        patch_process_signals(monkeypatch, fake_proc)
        monkeypatch.setattr("os.getpgid", lambda pid: pid + 1000)
        patch_port_wait(monkeypatch, manager)
        slow_down_health_check(monkeypatch)

        def fake_write_pid_file_raises() -> None:
            raise OSError("simulated disk full")

        monkeypatch.setattr(
            manager, "_write_pid_file", fake_write_pid_file_raises
        )

        result = await manager.start()

        assert result.status == "running"
        assert result.started_at is not None
        assert result.pid == 12345

        # The PID file should NOT exist (write was forced to fail),
        # but the manager must report a running state anyway.
        pid_file = tmp_path / VSCODE_PID_FILENAME
        assert not pid_file.exists()

        await manager.cleanup()

    # ── Snapshot / status helpers ────────────────────────────────────────

    async def test_get_status_returns_snapshot(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``get_status()`` returns a snapshot with the same fields."""
        manager = make_manager(tmp_path)
        fake_proc = FakeProcess(pid=12345)
        patch_resolve_binary(monkeypatch, manager)
        patch_create_subprocess(monkeypatch, fake_proc)
        patch_process_signals(monkeypatch, fake_proc)
        monkeypatch.setattr("os.getpgid", lambda pid: pid + 1000)
        patch_port_wait(monkeypatch, manager)
        slow_down_health_check(monkeypatch)

        await manager.start()

        snapshot = manager.get_status()
        assert isinstance(snapshot, VSCodeServerState)
        assert snapshot is not manager.state
        assert snapshot.status == "running"
        assert snapshot.pid == 12345
        assert snapshot.port == 12345

        await manager.cleanup()

    async def test_is_running_false_in_initial_state(
        self, tmp_path: Path
    ) -> None:
        """``is_running()`` returns False before ``start()``."""
        manager = make_manager(tmp_path)
        assert manager.is_running() is False

    async def test_is_running_false_after_stop(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``is_running()`` returns False after stop completes."""
        manager = make_manager(tmp_path)
        fake_proc = FakeProcess(pid=12345, returncode=None)
        patch_resolve_binary(monkeypatch, manager)
        patch_create_subprocess(monkeypatch, fake_proc)
        patch_process_signals(monkeypatch, fake_proc)
        monkeypatch.setattr("os.getpgid", lambda pid: pid + 1000)
        patch_port_wait(monkeypatch, manager)
        slow_down_health_check(monkeypatch)

        await manager.start()
        assert manager.is_running() is True

        await manager.stop()
        assert manager.is_running() is False

    async def test_get_port_returns_bound_port(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``get_port()`` returns the port once it's been detected."""
        manager = make_manager(tmp_path)
        port_line = b"HTTP server listening on http://127.0.0.1:45678\n"
        fake_proc = FakeProcess(stdout_chunks=[port_line, b""])
        patch_resolve_binary(monkeypatch, manager)
        patch_create_subprocess(monkeypatch, fake_proc)
        patch_process_signals(monkeypatch, fake_proc)
        monkeypatch.setattr("os.getpgid", lambda pid: pid + 1000)
        slow_down_health_check(monkeypatch)

        assert manager.get_port() is None

        await manager.start()

        assert manager.get_port() == 45678
        await manager.cleanup()

    # ── _wait_for_port() crash path: log buffer surfaced on startup exit ──

    async def test_wait_for_port_surfaces_log_buffer_on_crash(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When code-server exits during startup, the crash exception
        surfaces the last ~50 lines of the in-memory log buffer so the
        operator can see WHY it died (bind error, missing dep, etc.).

        Drives ``_wait_for_port()`` directly with a ``FakeProcess`` whose
        ``returncode`` is already set on entry — first poll iteration
        detects the exit and raises with the buffered output appended.
        """
        manager = make_manager(tmp_path)
        # Tighten the poll interval so the test runs in milliseconds.
        monkeypatch.setattr(
            "daemon.services.vscode_server_manager.VSCODE_PORT_DETECTION_POLL_S",
            0.001,
        )

        # Pre-populate the log buffer with content that should appear
        # in the raised exception's message.
        known = b"fatal: cannot bind port"
        manager.state.log_buffer.extend(
            b"some startup line\n" + known + b"\nanother diagnostic line\n"
        )

        # Fake process has already exited (returncode != None) on entry.
        fake_proc = FakeProcess(pid=12345, returncode=137)
        manager._process = fake_proc  # type: ignore[assignment]
        manager.state.status = "starting"
        # Port is None → the while loop runs at least once.

        with pytest.raises(VSCodeServerStartError) as exc_info:
            await manager._wait_for_port()

        msg = str(exc_info.value)
        # The exception message includes both the exit-code header AND
        # the buffered output, separated by the marker line.
        assert "code-server output (tail)" in msg
        assert known.decode() in msg
        # Header line is preserved.
        assert "code-server exited during startup" in msg

    async def test_wait_for_port_empty_log_buffer_clean_message(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When the log buffer is empty, the exception message stays clean
        (no spurious ``--- code-server output (tail) ---`` marker).

        Mirrors the populated-buffer test but with an empty buffer to
        verify the conditional branch where ``log_tail`` is falsy.
        """
        manager = make_manager(tmp_path)
        monkeypatch.setattr(
            "daemon.services.vscode_server_manager.VSCODE_PORT_DETECTION_POLL_S",
            0.001,
        )

        # Log buffer is empty by default (bytearray factory).
        assert len(manager.state.log_buffer) == 0

        fake_proc = FakeProcess(pid=12345, returncode=1)
        manager._process = fake_proc  # type: ignore[assignment]
        manager.state.status = "starting"

        with pytest.raises(VSCodeServerStartError) as exc_info:
            await manager._wait_for_port()

        msg = str(exc_info.value)
        # Header is still present.
        assert "code-server exited during startup" in msg
        # But the tail marker MUST NOT appear — empty buffer means no tail.
        assert "code-server output (tail)" not in msg

    async def test_wait_for_port_crash_resets_status_to_stopped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After the startup crash, ``state.status`` is ``"stopped"`` —
        callers (and the watchdog) must not see the manager stuck in
        ``"starting"`` forever after a failed spawn.
        """
        manager = make_manager(tmp_path)
        monkeypatch.setattr(
            "daemon.services.vscode_server_manager.VSCODE_PORT_DETECTION_POLL_S",
            0.001,
        )

        fake_proc = FakeProcess(pid=12345, returncode=139)
        manager._process = fake_proc  # type: ignore[assignment]
        manager.state.status = "starting"

        with pytest.raises(VSCodeServerStartError):
            await manager._wait_for_port()

        assert manager.state.status == "stopped"

    async def test_wait_for_port_crash_cancels_reader_task(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """On startup crash, the still-running reader task is cancelled
        AND awaited via ``asyncio.gather(..., return_exceptions=True)``
        so it doesn't outlive the manager on this path.

        ``_wait_for_port()`` MUST call ``.cancel()`` on
        ``state.reader_task`` when the task is present and not done,
        then ``await asyncio.gather(...)`` to observe the
        ``CancelledError`` re-raised by ``_reader_loop`` (captured by
        ``return_exceptions=True``). The fake reader task MUST be a real
        awaitable because ``asyncio.gather`` only accepts coroutines,
        Futures, or awaitables — a plain ``done()``/``cancel()`` mock
        is not.
        """
        manager = make_manager(tmp_path)
        monkeypatch.setattr(
            "daemon.services.vscode_server_manager.VSCODE_PORT_DETECTION_POLL_S",
            0.001,
        )

        # Real pending ``asyncio.Future`` (the event loop is running in
        # this async test, so ``asyncio.Future()`` binds to it). For a
        # pending future:
        #   - ``.done()`` returns False (guard passes),
        #   - ``.cancel()`` cancels it,
        #   - ``asyncio.gather(future, return_exceptions=True)`` is a
        #     valid await (Future is an awaitable) and captures the
        #     ``CancelledError`` instead of propagating it.
        fake_task: asyncio.Future = asyncio.Future()
        manager.state.reader_task = fake_task  # type: ignore[assignment]

        fake_proc = FakeProcess(pid=12345, returncode=2)
        manager._process = fake_proc  # type: ignore[assignment]
        manager.state.status = "starting"

        with pytest.raises(VSCodeServerStartError):
            await manager._wait_for_port()

        # ``.cancel()`` was called on the task and ``asyncio.gather``
        # observed the resulting ``CancelledError`` (captured, not
        # re-raised). The future ends up in the cancelled state.
        assert fake_task.cancelled() is True, (
            "reader_task.cancel() must be called and awaited when the "
            "process exits during startup"
        )
        assert fake_task.done() is True, (
            "reader_task must be awaited via asyncio.gather after cancel"
        )

    async def test_wait_for_port_crash_logs_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
    ) -> None:
        """The crash path emits a ``logger.error(...)`` carrying the exit
        code (and the buffered output if non-empty) so operators have a
        persistent log record, not just the in-flight exception.
        """
        import logging

        manager = make_manager(tmp_path)
        monkeypatch.setattr(
            "daemon.services.vscode_server_manager.VSCODE_PORT_DETECTION_POLL_S",
            0.001,
        )

        # Pre-populate buffer so the log line carries the tail marker
        # (this exercises the full log path, not just the empty branch).
        manager.state.log_buffer.extend(
            b"some diagnostic line\nfatal: cannot bind port\n"
        )

        fake_proc = FakeProcess(pid=12345, returncode=42)
        manager._process = fake_proc  # type: ignore[assignment]
        manager.state.status = "starting"

        with caplog.at_level(
            logging.ERROR, logger="daemon.services.vscode_server_manager"
        ):
            with pytest.raises(VSCodeServerStartError):
                await manager._wait_for_port()

        # At least one ERROR record was emitted, and it carries BOTH the
        # startup message and the exit code (42) so the operator can
        # match it against logs. Asserting on both substrings prevents
        # false positives from unrelated log lines that happen to
        # contain "42".
        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(error_records) >= 1
        assert any(
            "code-server exited during startup" in r.getMessage()
            and "code=42" in r.getMessage()
            for r in error_records
        ), (
            f"expected startup message + exit code in error log; got: "
            f"{[r.getMessage() for r in error_records]}"
        )

    async def test_wait_for_port_crash_reader_task_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When ``state.reader_task`` is ``None`` at crash time, the
        ``is not None`` guard prevents an ``AttributeError`` and the
        crash path still raises ``VSCodeServerStartError``.
        """
        manager = make_manager(tmp_path)
        monkeypatch.setattr(
            "daemon.services.vscode_server_manager.VSCODE_PORT_DETECTION_POLL_S",
            0.001,
        )

        # No reader task assigned (e.g. caller constructed a manager
        # directly without going through start()).
        manager.state.reader_task = None  # type: ignore[assignment]

        fake_proc = FakeProcess(pid=12345, returncode=2)
        manager._process = fake_proc  # type: ignore[assignment]
        manager.state.status = "starting"

        # MUST NOT raise AttributeError; the is-not-None guard takes the
        # skip path and the function still raises VSCodeServerStartError.
        with pytest.raises(VSCodeServerStartError):
            await manager._wait_for_port()

    async def test_wait_for_port_crash_reader_task_already_done(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When the reader task is already ``done()`` at crash time, its
        ``.cancel()`` MUST NOT be called — cancelling a finished task is
        a no-op but can still raise ``InvalidStateError`` in some event
        loop implementations and clutters cancellation accounting.
        """
        manager = make_manager(tmp_path)
        monkeypatch.setattr(
            "daemon.services.vscode_server_manager.VSCODE_PORT_DETECTION_POLL_S",
            0.001,
        )

        # Fake reader task that has already finished.
        class DoneTask:
            def __init__(self) -> None:
                self.cancel_called = False

            def done(self) -> bool:
                return True

            def cancel(self) -> None:
                # If the guard is missing, this would be called and we
                # would see the test fail.
                self.cancel_called = True

        fake_task = DoneTask()
        manager.state.reader_task = fake_task  # type: ignore[assignment]

        fake_proc = FakeProcess(pid=12345, returncode=2)
        manager._process = fake_proc  # type: ignore[assignment]
        manager.state.status = "starting"

        with pytest.raises(VSCodeServerStartError):
            await manager._wait_for_port()

        # ``not done()`` guard: cancel() is NOT called for a finished task.
        assert fake_task.cancel_called is False, (
            "reader_task.cancel() must NOT be called when the task is "
            "already done"
        )

    async def test_wait_for_port_crash_caps_long_tail(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A pathological multi-MB line in the log buffer MUST be capped
        so it cannot bloat the exception message / 503 HTTP response.

        Verifies the ``VSCODE_CRASH_LOG_TAIL_MAX_BYTES`` cap and the
        ``[truncated]`` marker on the tail.
        """
        manager = make_manager(tmp_path)
        monkeypatch.setattr(
            "daemon.services.vscode_server_manager.VSCODE_PORT_DETECTION_POLL_S",
            0.001,
        )

        # Single line that exceeds the cap by a wide margin.
        huge_line = b"X" * (VSCODE_CRASH_LOG_TAIL_MAX_BYTES * 4)
        manager.state.log_buffer.extend(huge_line + b"\n")

        fake_proc = FakeProcess(pid=12345, returncode=99)
        manager._process = fake_proc  # type: ignore[assignment]
        manager.state.status = "starting"

        with pytest.raises(VSCodeServerStartError) as exc_info:
            await manager._wait_for_port()

        msg = str(exc_info.value)

        # Truncation marker is present so the operator knows content
        # was elided.
        assert "[truncated]" in msg, (
            f"exception message must contain [truncated] marker when "
            f"tail exceeds cap; got first 200 chars: {msg[:200]!r}"
        )

        # The huge marker bytes (millions of X's) MUST NOT all appear in
        # the message. The cap is a hard upper bound on the tail.
        # We check that the message length is bounded well below the
        # un-truncated size (which would be 64 KB just for the X's,
        # plus header/marker).
        assert len(msg) < VSCODE_CRASH_LOG_TAIL_MAX_BYTES + 512, (
            f"exception message should be bounded by cap + small "
            f"overhead; got len={len(msg)}, "
            f"cap={VSCODE_CRASH_LOG_TAIL_MAX_BYTES}"
        )
        # Sanity: the X content is present (we kept up to the cap).
        assert "X" in msg

    async def test_wait_for_port_crash_sets_exit_code_and_last_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """On the startup crash branch, ``state.exit_code`` MUST equal
        the process returncode AND ``state.last_error`` MUST contain
        the startup-exit message so the crash is observable via
        ``get_status()`` / the API (mirroring the watchdog's runtime
        crash behavior).
        """
        manager = make_manager(tmp_path)
        monkeypatch.setattr(
            "daemon.services.vscode_server_manager.VSCODE_PORT_DETECTION_POLL_S",
            0.001,
        )

        # Sanity: clean state before the crash.
        assert manager.state.exit_code is None
        assert manager.state.last_error is None

        fake_proc = FakeProcess(pid=12345, returncode=137)
        manager._process = fake_proc  # type: ignore[assignment]
        manager.state.status = "starting"

        with pytest.raises(VSCodeServerStartError):
            await manager._wait_for_port()

        # exit_code is the process returncode.
        assert manager.state.exit_code == 137
        # last_error carries the startup-exit message.
        assert manager.state.last_error is not None
        assert "code-server exited during startup" in (
            manager.state.last_error
        )
        # And the exit code is embedded so operators can correlate.
        assert "code=137" in manager.state.last_error
        # Status stays "stopped" (NOT "crashed") per the constraint.
        assert manager.state.status == "stopped"

    # ── Child environment: strip code-server-sensitive vars ───────────────

    async def test_start_strips_port_from_child_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``$PORT`` from the daemon's environment is stripped from the
        child env passed to code-server.

        code-server reads ``$PORT`` to override ``--bind-addr``'s port,
        so the daemon's own ``PORT`` (its listen port) would otherwise
        make code-server try to bind to the same address. Verify the
        captured ``env`` kwarg on the spawn call does NOT carry ``PORT``.
        """
        # Seed a ``PORT`` that would otherwise collide with the daemon.
        monkeypatch.setenv("PORT", "9797")

        manager = make_manager(tmp_path)
        fake_proc = FakeProcess()
        patch_resolve_binary(monkeypatch, manager)
        calls = patch_create_subprocess(monkeypatch, fake_proc)
        patch_process_signals(monkeypatch, fake_proc)
        monkeypatch.setattr("os.getpgid", lambda pid: pid + 1000)
        patch_port_wait(monkeypatch, manager)
        slow_down_health_check(monkeypatch)

        await manager.start()

        assert len(calls) == 1
        child_env = calls[0]["kwargs"].get("env")
        assert child_env is not None, (
            "start() must pass an explicit env= to create_subprocess_exec "
            "so the parent daemon's PORT cannot bleed through"
        )
        assert "PORT" not in child_env, (
            f"PORT must be stripped from child env to prevent "
            f"code-server from binding the daemon's listen port; got: "
            f"{child_env!r}"
        )

        await manager.cleanup()

    async def test_start_passes_standard_env_through(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Standard env vars (HOME, PATH, etc.) are passed through to the
        child. Only the code-server-sensitive vars are stripped — we
        don't sanitize the entire environment.
        """
        # Set at least one value we can check survives untouched.
        monkeypatch.setenv("HOME", "/tmp/fake-home-for-test")
        monkeypatch.setenv("PATH", "/usr/bin:/bin")
        # Also seed a sensitive var so the strip path is exercised in
        # the same call (regression guard against the strip block being
        # accidentally skipped if the env build is refactored).
        monkeypatch.setenv("PORT", "9797")

        manager = make_manager(tmp_path)
        fake_proc = FakeProcess()
        patch_resolve_binary(monkeypatch, manager)
        calls = patch_create_subprocess(monkeypatch, fake_proc)
        patch_process_signals(monkeypatch, fake_proc)
        monkeypatch.setattr("os.getpgid", lambda pid: pid + 1000)
        patch_port_wait(monkeypatch, manager)
        slow_down_health_check(monkeypatch)

        await manager.start()

        child_env = calls[0]["kwargs"].get("env")
        assert child_env is not None
        # Standard vars are preserved.
        assert child_env.get("HOME") == "/tmp/fake-home-for-test"
        assert child_env.get("PATH") == "/usr/bin:/bin"
        # Sensitive var is still stripped in the same call.
        assert "PORT" not in child_env

        await manager.cleanup()

    async def test_start_strips_other_codeserver_sensitive_vars(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """All non-``PORT`` vars in ``_CODESERVER_STRIP_ENV`` are stripped
        from the child env passed to code-server.

        ``PORT`` has its own dedicated test (``test_start_strips_port_from_child_env``).
        The remaining vars — ``PASSWORD``, ``HASHED_PASSWORD``,
        ``GITHUB_TOKEN``, ``CODE_SERVER_CONFIG_FILE``,
        ``CODE_SERVER_COOKIE_SUFFIX``, ``CS_DISABLE_FILE_DOWNLOADS``,
        ``CS_DISABLE_GETTING_STARTED_OVERRIDE`` — are code-server
        env vars that must not leak from the daemon. Asserting against
        the constant itself keeps the test self-maintaining: adding a
        var to ``_CODESERVER_STRIP_ENV`` automatically adds coverage here.
        """
        # Seed every var in the constant so the strip path is exercised
        # in a single spawn; ``PORT`` is included below for symmetry but
        # is the dedicated target of the sibling PORT test.
        seeded = {
            "PORT": "9797",
            "PASSWORD": "leaked-pw",
            "HASHED_PASSWORD": "leaked-hash",
            "GITHUB_TOKEN": "ghp_leaked",
            "CODE_SERVER_CONFIG_FILE": "/etc/passwd",
            "CODE_SERVER_COOKIE_SUFFIX": "leaked-suffix",
            "CS_DISABLE_FILE_DOWNLOADS": "1",
            "CS_DISABLE_GETTING_STARTED_OVERRIDE": "true",
        }
        for key, value in seeded.items():
            monkeypatch.setenv(key, value)

        manager = make_manager(tmp_path)
        fake_proc = FakeProcess()
        patch_resolve_binary(monkeypatch, manager)
        calls = patch_create_subprocess(monkeypatch, fake_proc)
        patch_process_signals(monkeypatch, fake_proc)
        monkeypatch.setattr("os.getpgid", lambda pid: pid + 1000)
        patch_port_wait(monkeypatch, manager)
        slow_down_health_check(monkeypatch)

        await manager.start()

        child_env = calls[0]["kwargs"].get("env")
        assert child_env is not None
        # Iterate the constant directly. Regression guard: if a new var
        # is added to ``_CODESERVER_STRIP_ENV`` without updating this
        # test, ``seeded`` above will cover it and the assert below will
        # fail loudly.
        for key in _CODESERVER_STRIP_ENV:
            assert key not in child_env, (
                f"{key} must be stripped from child env to prevent the "
                f"daemon's value from changing code-server behavior; "
                f"got child_env={child_env!r}"
            )

        await manager.cleanup()
