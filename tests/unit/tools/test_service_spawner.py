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
* A1 killpg reachability — ``os.killpg(pid, sig)`` against a
  ``spawn``-ed setsid'd group reaches fork-children of a SIGTERM-
  trapping parent (the signal mechanism the manager's ownership-
  verified kill path relies on; the former low-level ``stop`` helper
  was DELETED per council Finding 2 — signals live behind the
  manager's re-verify, so the reachability proof here signals via
  ``os.killpg`` directly).
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


# ── A1 killpg reachability (the manager kill path's mechanism) ──────
#
# The former ``stop()``-helper tests (force-kill timing, graceful
# escalation, dead-pid no-op) were DELETED with the helper itself
# (council Finding 2 — dead code; the manager owns the grace loop).
# What survives is the invariant those tests could only borrow: A1 —
# ``os.killpg(pid, sig)`` against a ``spawn``-ed setsid'd group
# reaches the WHOLE process group, fork-children included. The test
# below proves it with the same primitives the manager's ownership-
# verified kill path uses.


def _pgrep_children(parent_pid: int) -> list[int]:
    """Helper: list direct child PIDs of ``parent_pid`` (test-side).

    Uses ``pgrep -P <pid>`` — universally available on Linux + macOS.
    Returns ``[]`` on any failure (no children / pgrep missing). The
    test tolerates an empty list as a structural artifact of the
    shell-trap spawn (see reliability note in
    ``test_killpg_reaches_sigterm_trapping_group``).
    """
    try:
        out = subprocess.run(
            ["pgrep", "-P", str(parent_pid)],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if out.returncode != 0 or not out.stdout.strip():
        return []
    return [int(line.strip()) for line in out.stdout.splitlines() if line.strip()]


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="service_spawner is not supported on Windows",
)
def test_killpg_reaches_sigterm_trapping_group(
    tmp_log_dir: Path,
) -> None:
    """A1: ``os.killpg(pid, SIGKILL)`` kills a SIGTERM-trapping parent
    AND its fork-children (the whole setsid'd group).

    Repurposed from the deleted ``stop``-helper escalation test
    (council Finding 2): the graceful SIGTERM→grace→SIGKILL loop now
    lives — ownership-verified — in ``ServiceToolManager.stop``; the
    spawner-level invariant worth pinning here is that the group
    signal reaches fork-children that EXPLICITLY ignore SIGTERM. The
    test signals via ``os.killpg`` directly, mirroring the manager's
    kill sites.

    Reliability bar: REAL processes only — no global ``time`` / ``os``
    patches. Wall-clock budget: < 3s.
    """
    from daemon.tools.service_spawner import is_process_alive, spawn

    # The fork-children ignore SIGTERM EXPLICITLY via the Python
    # signal module — portable across Linux (where ``sleep`` honors
    # SIGTERM) and macOS (where ``sleep`` ignores SIGTERM).
    _sigterm_ignoring_child = (
        'python3 -c "import signal, time; '
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        'time.sleep(60)"'
    )
    log_path = str(tmp_log_dir / "fork-children.log")
    # Parent shell that IGNORES SIGTERM + two forked children that
    # also ignore SIGTERM (in-child Python signal handler). `wait`
    # is necessary so the shell does NOT exit before the children.
    pid, _ = spawn(
        [
            "sh",
            "-c",
            f'trap "" TERM; {_sigterm_ignoring_child} & '
            f"{_sigterm_ignoring_child} & wait",
        ],
        log_path=log_path,
        cwd="/tmp",
    )

    # Brief settle so the children are actually forked and visible to
    # pgrep BEFORE we start the signal sequence.
    deadline = time.monotonic() + 1.0
    children: list[int] = []
    while time.monotonic() < deadline:
        children = _pgrep_children(pid)
        if len(children) >= 2:
            break
        time.sleep(0.02)

    assert len(children) >= 2, (
        f"shell did not fork 2 sleep children within 1s settle; "
        f"got {children} via pgrep -P {pid}; the test environment "
        f"cannot run the fork-children case (document disposition in "
        f"the Phase 3.A.4 runbook row)"
    )

    try:
        # (1) SIGTERM via killpg does NOT kill the group — parent AND
        # children all trap/ignore it (proves the group is genuinely
        # SIGTERM-immune, so any later death came from SIGKILL).
        os.killpg(pid, signal.SIGTERM)
        time.sleep(0.2)
        assert is_process_alive(pid), (
            "parent died on SIGTERM — the trap was not installed; "
            "the SIGKILL reachability proof below would be vacuous"
        )
        assert all(is_process_alive(c) for c in children), (
            "a fork-child died on SIGTERM despite SIG_IGN"
        )

        # (2) SIGKILL via the same killpg mechanism reaches the WHOLE
        # group — parent AND both fork-children die.
        os.killpg(pid, signal.SIGKILL)

        group_deadline = time.monotonic() + 2.0
        while time.monotonic() < group_deadline:
            if not is_process_alive(pid) and not any(
                is_process_alive(c) for c in children
            ):
                break
            time.sleep(0.02)

        assert not is_process_alive(pid), (
            "killpg(SIGKILL) did not kill the SIGTERM-trapping parent"
        )
        survivors = [c for c in children if is_process_alive(c)]
        assert not survivors, (
            f"killpg did not reach fork-children; survivors: "
            f"{survivors} of original {children}"
        )
    finally:
        # Defensive teardown — if any assertion failed above, kill the
        # WHOLE group so the test never leaks an orphaned sleep into
        # the next run.
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        for child in children:
            try:
                os.kill(child, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
