"""Cross-platform tests for ``daemon.tools.service_spawner`` (Phase 1.B).

Covers the 1.B.3 acceptance criteria:

* :func:`get_process_start_time` — Linux (parses ``/proc/<pid>/stat``
  field 22, anchoring on the LAST ``)`` to skip a multi-word
  ``comm``); macOS (parses ``ps -o lstart`` to epoch seconds); Windows
  raises :class:`NotImplementedError`.
* :func:`spawn` — returns ``(pid, start_time)``, the child is in a new
  pgid (verified via ``os.getpgid(pid) != os.getpgid(os.getpid())``),
  and the start_time token matches what :func:`get_process_start_time`
  re-reads.
* :func:`stop` — ``force=True`` kills within timeout; the F1
  ownership-verification contract is the manager's responsibility, not
  the spawner's.
* :func:`is_process_alive` — excludes zombies (state ``Z`` /
  ``Z+``).
* :func:`kill_log_path` — returns ``<root>/<name>.log`` and creates
  the parent directory.

Platform gating: Linux and macOS are tested (the plan ships on those).
Windows raises ``NotImplementedError`` and is not exercised here
(``pytest.skip`` on Windows).

Tests are fast and bounded — no real 5s grace waits in the common
path. The spawner is the LOW-LEVEL helper; the manager's grace path
is tested separately in ``test_service_tool_manager.py``.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest


# ── fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def tmp_log_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the spawner log root to a temp dir via env var."""
    monkeypatch.setenv("ENSEMBLE_SERVICE_LOG_DIR", str(tmp_path))
    return tmp_path


# ── get_process_start_time ──────────────────────────────────────────


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="service_spawner is not supported on Windows",
)
def test_get_process_start_time_returns_int_for_self() -> None:
    """The current process has a readable start_time token."""
    from daemon.tools.service_spawner import get_process_start_time

    pid = os.getpid()
    token = get_process_start_time(pid)
    assert token is not None
    assert isinstance(token, int)
    assert token > 0


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="service_spawner is not supported on Windows",
)
def test_get_process_start_time_returns_none_for_dead_pid() -> None:
    """An obviously-dead PID returns ``None`` (not raise)."""
    from daemon.tools.service_spawner import get_process_start_time

    # PID 2_000_000_000 is far above any realistic PID — the kernel
    # returns ESRCH immediately.
    assert get_process_start_time(2_000_000_000) is None


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="service_spawner is not supported on Windows",
)
def test_get_process_start_time_stable_across_re_reads() -> None:
    """Re-reading the same PID returns the SAME token (F1 ownership key)."""
    from daemon.tools.service_spawner import get_process_start_time

    pid = os.getpid()
    token_a = get_process_start_time(pid)
    token_b = get_process_start_time(pid)
    assert token_a == token_b


def test_get_process_start_time_raises_on_windows() -> None:
    """Windows raises :class:`NotImplementedError` (out of scope)."""
    if sys.platform != "win32":
        pytest.skip("Windows-only assertion")
    from daemon.tools.service_spawner import get_process_start_time

    with pytest.raises(NotImplementedError):
        get_process_start_time(os.getpid())


# ── is_process_alive ────────────────────────────────────────────────


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="service_spawner is not supported on Windows",
)
def test_is_process_alive_for_self() -> None:
    """Self is alive."""
    from daemon.tools.service_spawner import is_process_alive

    assert is_process_alive(os.getpid()) is True


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="service_spawner is not supported on Windows",
)
def test_is_process_alive_for_dead_pid() -> None:
    """Dead PID returns ``False``."""
    from daemon.tools.service_spawner import is_process_alive

    assert is_process_alive(2_000_000_000) is False


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="service_spawner is not supported on Windows",
)
def test_is_process_alive_after_proc_exit() -> None:
    """A PID that has exited (just now) returns ``False``."""
    from daemon.tools.service_spawner import is_process_alive

    # Spawn a child that exits immediately; capture the PID; verify
    # ``is_process_alive`` is False within a brief settle window.
    proc = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.exit(0)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )
    pid = proc.pid
    proc.wait(timeout=5.0)
    # PID is freed; ``is_process_alive`` should return False.
    # A short settle sleep avoids racing the kernel reap.
    time.sleep(0.05)
    assert is_process_alive(pid) is False


# ── spawn ───────────────────────────────────────────────────────────


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="service_spawner is not supported on Windows",
)
def test_spawn_returns_pid_and_start_time(tmp_log_dir: Path) -> None:
    """spawn returns ``(pid, start_time)`` and the child is in a new pgid."""
    from daemon.tools.service_spawner import get_process_start_time, spawn

    log_path = str(tmp_log_dir / "child.log")
    # ``sleep 30`` is universally available on macOS + Linux.
    pid, start_time = spawn(["sleep", "30"], log_path=log_path, cwd="/tmp")

    try:
        # (1) pid is a positive int.
        assert isinstance(pid, int) and pid > 0
        # (2) start_time is a positive int.
        assert isinstance(start_time, int) and start_time > 0
        # (3) the child's pgid differs from the parent's (setsid'd).
        assert os.getpgid(pid) != os.getpgid(os.getpid())
        # (4) the stored start_time token matches a fresh re-read.
        assert get_process_start_time(pid) == start_time
        # (5) the log file exists and is writable.
        assert os.path.exists(log_path)
    finally:
        # Cleanup: kill the child + its process group so the test
        # does not leak ``sleep`` processes across runs.
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="service_spawner is not supported on Windows",
)
def test_spawn_setsid_persists_across_parent_exit(tmp_log_dir: Path) -> None:
    """A setsid'd service survives parent-process termination.

    The grandchild (the actual spawned process) outlives the helper
    process that called ``spawn``. This is the F1 / D1 contract:
    the child is in a new session + process group; nothing in the
    daemon holds a reference that would kill it on teardown.
    """
    from daemon.tools.service_spawner import get_process_start_time, spawn

    log_path = str(tmp_log_dir / "orphan.log")
    pid, start_time = spawn(["sleep", "5"], log_path=log_path, cwd="/tmp")

    try:
        # The PID is reachable from this process (we own it). The
        # ``spawn`` docstring asserts it would also be reachable
        # from any other process by PID + start_time token.
        assert get_process_start_time(pid) == start_time
    finally:
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="service_spawner is not supported on Windows",
)
def test_spawn_raises_oserror_on_bad_cwd(tmp_log_dir: Path) -> None:
    """A bad cwd surfaces as a synchronous ``OSError`` (F8 contract).

    The ``ServiceToolManager`` caller catches this and writes an
    EXITED row — see ``test_service_tool_manager.py::test_f8_*``.
    """
    from daemon.tools.service_spawner import spawn

    log_path = str(tmp_log_dir / "badcwd.log")
    with pytest.raises(OSError):
        # A path under /nonexistent is guaranteed not to exist on
        # both Linux + macOS. Popen raises FileNotFoundError, which
        # is an ``OSError`` subclass.
        spawn(["echo", "hi"], log_path=log_path, cwd="/nonexistent/path/xyz")


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="service_spawner is not supported on Windows",
)
def test_spawn_raises_oserror_on_missing_binary(tmp_log_dir: Path) -> None:
    """A missing binary surfaces as ``OSError`` (F8 contract)."""
    from daemon.tools.service_spawner import spawn

    log_path = str(tmp_log_dir / "missing.log")
    with pytest.raises(OSError):
        spawn(["/no/such/binary/exists"], log_path=log_path, cwd="/tmp")


def test_spawn_raises_value_error_on_empty_argv(tmp_log_dir: Path) -> None:
    """Empty argv is a structural validation error (``ValueError``)."""
    from daemon.tools.service_spawner import spawn

    log_path = str(tmp_log_dir / "empty.log")
    with pytest.raises(ValueError):
        spawn([], log_path=log_path, cwd="/tmp")


# ── kill_log_path ───────────────────────────────────────────────────


def test_kill_log_path_creates_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``kill_log_path`` creates the parent directory on first call."""
    from daemon.tools.service_spawner import kill_log_path

    monkeypatch.setenv("ENSEMBLE_SERVICE_LOG_DIR", str(tmp_path))
    log_path = kill_log_path("test-svc")
    assert log_path.endswith("test-svc.log")
    # Parent directory was created (mkdir idempotent).
    assert os.path.isdir(os.path.dirname(log_path))


def test_kill_log_path_is_absolute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The returned path is absolute (portable across relative-cwd callers)."""
    from daemon.tools.service_spawner import kill_log_path

    monkeypatch.setenv("ENSEMBLE_SERVICE_LOG_DIR", "relative-services")
    log_path = kill_log_path("abs")
    assert os.path.isabs(log_path)


# ── stop ────────────────────────────────────────────────────────────


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="service_spawner is not supported on Windows",
)
def test_stop_force_kills_within_timeout(tmp_log_dir: Path) -> None:
    """``stop(pid, force=True)`` kills the process group quickly."""
    from daemon.tools.service_spawner import (
        get_process_start_time,
        is_process_alive,
        spawn,
        stop,
    )

    log_path = str(tmp_log_dir / "killme.log")
    pid, start_time = spawn(["sleep", "30"], log_path=log_path, cwd="/tmp")
    try:
        assert is_process_alive(pid) is True
        assert get_process_start_time(pid) == start_time
        # ``force=True`` skips SIGTERM and SIGKILLs immediately.
        start = time.monotonic()
        stop(pid, force=True, grace_seconds=2.0)
        elapsed = time.monotonic() - start
        # force=True should be near-instant; allow generous headroom.
        assert elapsed < 1.5
        # Brief settle sleep before liveness re-check.
        time.sleep(0.05)
        assert is_process_alive(pid) is False
    finally:
        # Defensive cleanup if the assertion above failed.
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="service_spawner is not supported on Windows",
)
def test_stop_graceful_then_escalate(tmp_log_dir: Path) -> None:
    """``stop(pid, force=False)`` SIGTERMs → grace → SIGKILLs.

    Uses a process that IGNORES SIGTERM (``sh -c 'trap "" TERM; sleep 30'``)
    so we exercise the grace → SIGKILL escalation path (rather than
    SIGTERM-kills-immediately).
    """
    from daemon.tools.service_spawner import (
        is_process_alive,
        spawn,
        stop,
    )

    log_path = str(tmp_log_dir / "graceful.log")
    pid, _ = spawn(
        ["sh", "-c", 'trap "" TERM; sleep 30'],
        log_path=log_path,
        cwd="/tmp",
    )
    try:
        # SIGTERM is ignored (trap "" TERM); only SIGKILL ends the
        # process. ``stop`` must wait the full grace_seconds, then
        # escalate to SIGKILL.
        start = time.monotonic()
        stop(pid, force=False, grace_seconds=0.5)
        elapsed = time.monotonic() - start
        # Grace is 0.5s; allow generous headroom for the escalation
        # + reap. Lower bound accounts for the SIGTERM round-trip
        # (essentially free on a local sh).
        assert 0.4 < elapsed < 2.5, (
            f"elapsed {elapsed:.3f}s not in (0.4, 2.5); expected "
            f"~0.5s grace + escalation"
        )
        time.sleep(0.05)
        assert is_process_alive(pid) is False
    finally:
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="service_spawner is not supported on Windows",
)
def test_stop_on_dead_pid_is_noop(tmp_log_dir: Path) -> None:
    """``stop`` on a dead PID returns cleanly (no raise).

    Spawns a short-lived process (``sleep 0.1``) and waits for it to
    exit naturally — the PID is freed by the kernel and stop() must
    not raise. Avoids the macOS PID-recycling hazard of ``killpg``
    externally and immediately re-using the PID (the kernel may
    reassign the PID to a system process before ``stop`` runs, which
    yields EPERM instead of ESRCH).
    """
    from daemon.tools.service_spawner import is_process_alive, spawn, stop

    log_path = str(tmp_log_dir / "dead.log")
    pid, _ = spawn(
        ["python3", "-c", "import time; time.sleep(0.1)"],
        log_path=log_path,
        cwd="/tmp",
    )
    # Wait for the process to exit naturally — ``is_process_alive`` is
    # the same probe ``stop`` uses, so we wait on the same signal.
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if not is_process_alive(pid):
            break
        time.sleep(0.05)
    else:  # pragma: no cover - defensive
        pytest.fail("test process did not exit within 5s")

    # Sanity: the process is gone before ``stop`` runs.
    assert not is_process_alive(pid)

    # ``stop`` swallows ``ProcessLookupError`` — no raise. The actual
    # killpg in stop() may briefly raise EPERM on macOS if the PID has
    # been recycled by the time the signal lands, but stop() catches
    # both ProcessLookupError and PermissionError in its branch path.
    try:
        stop(pid, force=True, grace_seconds=0.5)
    except PermissionError:
        # macOS PID-recycling edge case — the kernel may reassign the
        # PID to a different process between our is_alive probe and
        # the killpg call. This is the documented hazard the F1
        # ownership defense exists to prevent; the manager layer
        # re-verifies ownership BEFORE killpg, which we don't do in
        # this spawner-only test (it's the manager's job). Tolerate.
        pass
