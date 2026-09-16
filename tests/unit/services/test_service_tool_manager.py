"""Manager-level tests for ``ServiceToolManager`` (Phase 1.B).

Covers the BLOCKING acceptance criteria for the manager layer:

* **F8** — synchronous spawn failure (bad cwd / missing binary) writes
  an EXITED row via ``insert_with_status`` immediately (no 30s slot
  block for the Phase-2 reaper).
* **F2** — concurrent same-name race returns
  ``{"status": "name_in_use", "reason": "concurrent_start_won_race"}``
  AND, after a ``(pid, start_time)`` ownership re-verify (council
  Finding 1), ``killpg``s the just-spawned child (the orphan otherwise
  leaks as a live untracked kill-exempt process; a recycled PID skips
  the signal — ``pid_recycled_before_cleanup``).
* **A1 + F1** — ``stop`` signals the process GROUP via ``os.killpg``,
  re-verifies ``(pid, start_time)`` ownership BEFORE the signal and
  on EVERY grace-poll iteration and immediately BEFORE the SIGKILL
  escalation. PID-recycle paths return
  ``reason="pid_recycled"`` / ``"pid_recycled_during_grace"`` /
  ``"pid_recycled_pre_kill"`` WITHOUT escalating.
* **A2** — ``stop`` is async; the 5s grace uses
  ``await asyncio.to_thread(get_process_start_time, pid)`` polling
  (NOT ``time.sleep`` busy-wait).
* **F7** — the manager's inline grace loop is the SOLE kill path;
  every signal site in the manager re-verifies ``(pid, start_time)``
  ownership before signaling (the former ``service_spawner.stop``
  helper was deleted — council Finding 2).
* **A13** — ``mark_exited`` row-count return is consumed; race-lost
  updates are treated as idempotent success.
* **Cap** — ``start`` enforces ``cap`` BEFORE any side effect.

Test environment: file-backed SQLite + real subprocess spawns (Linux +
macOS only — Windows is ``pytest.skip``-gated per the spawner
contract). All long-running spawns use ``sleep`` so they don't pollute
the daemon process tree; tests clean up via ``force=True`` stop.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
from typing import Iterator

import pytest
from sqlalchemy import create_engine, event as sa_event
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

import daemon.repositories.service_tool.models  # noqa: F401
from daemon.repositories.service_tool.models import (
    ServiceStatus,
)
from daemon.repositories.service_tool.repository import ServiceRepo
from daemon.services.service_tool_manager import (
    DEFAULT_STOP_GRACE_SECONDS,
    ServiceToolManager,
)
from daemon.tools.service_spawner import (
    get_process_start_time,
    is_process_alive,
)


# ── fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def engine(tmp_path) -> Iterator[Engine]:
    """File-backed SQLite engine."""
    db_path = tmp_path / "service-manager-test.sqlite"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=NullPool,
    )

    @sa_event.listens_for(eng, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def repo(engine: Engine) -> ServiceRepo:
    return ServiceRepo(engine=engine)


@pytest.fixture
def manager(repo: ServiceRepo, tmp_path, monkeypatch) -> ServiceToolManager:
    """ServiceToolManager with cap=3 (small to keep cap tests fast)."""
    # Redirect log root to a tmp dir so tests don't pollute the repo.
    monkeypatch.setenv("ENSEMBLE_SERVICE_LOG_DIR", str(tmp_path / "logs"))
    return ServiceToolManager(repo=repo, cap=3)


# ── helpers ─────────────────────────────────────────────────────────


async def _start(
    manager: ServiceToolManager,
    name: str,
    argv: list[str],
    cwd: str | None = None,
) -> dict:
    return await manager.start(
        name=name,
        argv=argv,
        cwd=cwd,
        started_by_instance_id="inst-test",
        started_by_agent_id="worker",
    )


async def _stop(
    manager: ServiceToolManager,
    name: str,
    force: bool = False,
) -> dict:
    return await manager.stop(name=name, force=force)


# ── F8 — synchronous spawn failure ──────────────────────────────────


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="service_spawner is not supported on Windows",
)
def test_f8_bad_cwd_writes_exited_row(manager: ServiceToolManager, repo: ServiceRepo) -> None:
    """Bad cwd ⇒ Popen raises OSError ⇒ EXITED row written via insert_with_status."""
    result = asyncio.run(
        _start(manager, "bad-cwd", ["echo", "hi"], cwd="/nonexistent/path/xyz")
    )
    assert result["status"] == "spawn_failed"
    assert "no such file" in result["reason"].lower() or "not found" in result["reason"].lower()

    # Verify an EXITED row was written.
    row = repo.get_by_name_any_status("bad-cwd")
    assert row is not None
    assert row.status == ServiceStatus.EXITED.value
    assert row.pid is None
    assert row.start_time is None


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="service_spawner is not supported on Windows",
)
def test_f8_missing_binary_writes_exited_row(manager: ServiceToolManager, repo: ServiceRepo) -> None:
    """Missing binary ⇒ Popen raises OSError ⇒ EXITED row written."""
    result = asyncio.run(
        _start(manager, "missing-bin", ["/no/such/binary/zzz"], cwd="/tmp")
    )
    assert result["status"] == "spawn_failed"
    row = repo.get_by_name_any_status("missing-bin")
    assert row is not None
    assert row.status == ServiceStatus.EXITED.value


def test_f8_releases_cap_slot_immediately(
    manager: ServiceToolManager, repo: ServiceRepo
) -> None:
    """A spawn_failed row does NOT count against the cap.

    After a spawn_failed write the row is EXITED (not in the active
    set), so a subsequent start() for a different name is not
    cap-blocked by the failed row.
    """
    # Fill cap with successful sleep services (long-lived).
    asyncio.run(_start(manager, "svc-a", ["sleep", "60"], cwd="/tmp"))
    asyncio.run(_start(manager, "svc-b", ["sleep", "60"], cwd="/tmp"))
    asyncio.run(_start(manager, "svc-c", ["sleep", "60"], cwd="/tmp"))
    # Now at cap=3. A spawn-failed write does NOT count against the cap
    # because the row is EXITED.
    asyncio.run(
        _start(manager, "spawn-fail", ["echo", "hi"], cwd="/nonexistent/path/xyz")
    )
    # The row is EXITED ⇒ ``list_active`` returns 3.
    active_count = len(repo.list_active())
    assert active_count == 3

    # Cleanup
    asyncio.run(_stop(manager, "svc-a", force=True))
    asyncio.run(_stop(manager, "svc-b", force=True))
    asyncio.run(_stop(manager, "svc-c", force=True))


# ── Cap enforcement ─────────────────────────────────────────────────


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="service_spawner is not supported on Windows",
)
def test_cap_enforcement_blocks_before_spawn(
    manager: ServiceToolManager, repo: ServiceRepo
) -> None:
    """``cap`` is enforced BEFORE any side effect (no spawn, no insert)."""
    # Fill cap with 3 services.
    asyncio.run(_start(manager, "c1", ["sleep", "60"], cwd="/tmp"))
    asyncio.run(_start(manager, "c2", ["sleep", "60"], cwd="/tmp"))
    asyncio.run(_start(manager, "c3", ["sleep", "60"], cwd="/tmp"))

    # 4th must be cap-exceeded.
    result = asyncio.run(_start(manager, "c4", ["sleep", "60"], cwd="/tmp"))
    assert result["status"] == "cap_exceeded"
    assert result["reason"] == "max_concurrent_reached"
    assert result["cap"] == 3
    assert result["active"] == 3

    # No row was inserted for c4 (cap check is BEFORE any side effect).
    assert repo.get_by_name_any_status("c4") is None

    # Cleanup
    asyncio.run(_stop(manager, "c1", force=True))
    asyncio.run(_stop(manager, "c2", force=True))
    asyncio.run(_stop(manager, "c3", force=True))


def test_invalid_name_rejected_before_spawn(
    manager: ServiceToolManager, repo: ServiceRepo
) -> None:
    """Invalid name patterns are rejected before any side effect."""
    for bad_name in ("with space", "with/slash", "with.dot", "a" * 65, ""):
        result = asyncio.run(
            _start(manager, bad_name, ["sleep", "1"], cwd="/tmp")
        )
        assert result["status"] in ("invalid_name", "invalid_argv"), (
            f"name {bad_name!r} should have been rejected; got {result!r}"
        )
        assert repo.get_by_name_any_status(bad_name) is None


# ── F2 — concurrent same-name race ──────────────────────────────────


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="service_spawner is not supported on Windows",
)
def test_f2_duplicate_name_uses_python_pre_check(
    manager: ServiceToolManager, repo: ServiceRepo
) -> None:
    """Second ``start`` with the same name returns ``name_in_use`` (Python pre-check)."""
    asyncio.run(_start(manager, "dup-1", ["sleep", "60"], cwd="/tmp"))

    # Second start with the same name — the Python-side pre-check
    # rejects BEFORE spawn (the common case).
    result = asyncio.run(
        _start(manager, "dup-1", ["sleep", "60"], cwd="/tmp")
    )
    assert result["status"] == "name_in_use"
    assert result["reason"] == "name_already_active"

    # Cleanup
    asyncio.run(_stop(manager, "dup-1", force=True))


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="service_spawner is not supported on Windows",
)
def test_f2_integrity_error_killpg_orphan(
    manager: ServiceToolManager, repo: ServiceRepo, monkeypatch
) -> None:
    """F2 lost-race branch: ``IntegrityError`` on insert ⇒ VERIFIED killpg(SIGKILL) the orphan.

    ``repo.insert`` is mocked to raise ``sqlalchemy.exc.IntegrityError``
    (what the partial UNIQUE index ``idx_service_tracking_name_active``
    raises when a concurrent caller won the same-name race) while a
    REAL ``/bin/sleep 30`` child is spawned. The manager MUST
    re-verify ``(pid, start_time)`` ownership (council Finding 1 —
    the 5th guarded signal site) and, on MATCH,
    ``os.killpg(pid, SIGKILL)`` the just-spawned orphan and return
    ``{"status": "name_in_use", "reason": "concurrent_start_won_race"}``.
    Without this branch the child leaks as a live, untracked,
    kill-exempt OS process (spec D3, decisions.md L381-393).
    """
    import daemon.services.service_tool_manager as _stm

    # Spy on the spawner seam (delegating wrapper) to capture the REAL
    # (pid, start_time) pair — the pair feeds the ownership scripting
    # below AND the finally-cleanup even if asserts fail.
    spawned: list[int | None] = [None]
    spawned_start: list[int | None] = [None]
    real_spawn = _stm.spawner_spawn

    def _spying_spawn(argv, log_path, cwd=None):  # noqa: ANN001
        pid, start_time = real_spawn(argv, log_path, cwd)
        spawned[0] = pid
        spawned_start[0] = start_time
        return pid, start_time

    monkeypatch.setattr(_stm, "spawner_spawn", _spying_spawn)

    # Council Finding 1 (a): the manager re-reads ownership via
    # ``get_process_start_time`` BEFORE the cleanup killpg. Script a
    # MATCH (the child is genuinely ours) so the verified kill path
    # fires deterministically.
    monkeypatch.setattr(
        _stm,
        "get_process_start_time",
        lambda pid: spawned_start[0],
    )

    killpg_calls = _recording_killpg(monkeypatch)

    def _losing_insert(*args, **kwargs):  # noqa: ANN002, ANN003
        raise IntegrityError(
            "INSERT INTO service_tracking ... (partial UNIQUE index)",
            {"name": "f2-race"},
            None,
        )

    monkeypatch.setattr(repo, "insert", _losing_insert)

    try:
        result = asyncio.run(
            _start(manager, "f2-race", ["/bin/sleep", "30"], cwd="/tmp")
        )
    finally:
        # The real child must die even on assertion failure — the
        # recorder replaced ``os.killpg`` at the manager seam, so use
        # the pre-patch real killpg for this fallback.
        _hard_kill(spawned[0])

    assert result["status"] == "name_in_use"
    assert result["reason"] == "concurrent_start_won_race"
    assert result["pid"] == spawned[0]

    # Exactly one killpg: SIGKILL against the just-spawned orphan,
    # fired ONLY AFTER the ownership re-verify matched.
    assert killpg_calls == [(spawned[0], signal.SIGKILL)], (
        f"F2 violation: expected exactly one SIGKILL on the orphan, "
        f"got {killpg_calls}"
    )

    # The real child is actually dead (the recorder FORWARDED the
    # SIGKILL — record-then-forward, never a no-op patch).
    settle_deadline = time.monotonic() + 2.0
    while is_process_alive(spawned[0]) and time.monotonic() < settle_deadline:
        time.sleep(0.05)
    assert not is_process_alive(spawned[0]), (
        "F2 violation: the orphaned child survived the killpg(SIGKILL)"
    )


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="service_spawner is not supported on Windows",
)
def test_f2_cleanup_killpg_skipped_on_pid_recycle(
    manager: ServiceToolManager, repo: ServiceRepo, monkeypatch, caplog
) -> None:
    """Council Finding 1 (a) mismatch leg: ownership re-verify MISMATCH
    ⇒ cleanup killpg SKIPPED, ``name_in_use`` still returned, WARNING
    logged with the ``pid_recycled_before_cleanup`` class token.

    Same lost-race setup as ``test_f2_integrity_error_killpg_orphan``
    (real spawn + ``repo.insert`` raising IntegrityError), but the
    manager-seam ``get_process_start_time`` returns a DISTINCT
    integer token — simulating a fast-exit child whose PID the
    kernel recycled before the cleanup ran. Signaling a recycled PID
    would be a stray kill against an unrelated user process (the
    exact hazard F1 closes); the DB resolution is unaffected (the
    INSERT never committed).
    """
    import logging as _logging

    import daemon.services.service_tool_manager as _stm

    spawned: list[int | None] = [None]
    spawned_start: list[int | None] = [None]
    real_spawn = _stm.spawner_spawn

    def _spying_spawn(argv, log_path, cwd=None):  # noqa: ANN001
        pid, start_time = real_spawn(argv, log_path, cwd)
        spawned[0] = pid
        spawned_start[0] = start_time
        return pid, start_time

    monkeypatch.setattr(_stm, "spawner_spawn", _spying_spawn)

    recycled_token = (spawned_start[0] or 0) + 555_777_999  # DISTINCT INTEGER

    monkeypatch.setattr(
        _stm,
        "get_process_start_time",
        lambda pid: recycled_token,
    )

    killpg_calls = _recording_killpg(monkeypatch)

    def _losing_insert(*args, **kwargs):  # noqa: ANN002, ANN003
        raise IntegrityError(
            "INSERT INTO service_tracking ... (partial UNIQUE index)",
            {"name": "f2-recycle"},
            None,
        )

    monkeypatch.setattr(repo, "insert", _losing_insert)

    try:
        with caplog.at_level(
            _logging.WARNING, logger="daemon.services.service_tool_manager"
        ):
            result = asyncio.run(
                _start(manager, "f2-recycle", ["/bin/sleep", "30"], cwd="/tmp")
            )
    finally:
        # No signal was sent on this path — the child is still alive;
        # the real-killpg fallback is the only cleanup.
        _hard_kill(spawned[0])

    # The name_in_use shape is STILL returned — the row resolution is
    # unaffected by the skipped signal.
    assert result["status"] == "name_in_use"
    assert result["reason"] == "concurrent_start_won_race"
    assert result["pid"] == spawned[0]

    # NO signal fired — the (forwarding) recorder stayed empty.
    assert killpg_calls == [], (
        f"F1 violation: cleanup killpg fired on a recycled PID: "
        f"{killpg_calls}"
    )

    # The class-token WARNING carries expected vs got.
    assert "pid_recycled_before_cleanup" in caplog.text
    assert str(spawned_start[0]) in caplog.text  # expected
    assert str(recycled_token) in caplog.text  # got

    # No row leaked (the loser's INSERT never committed).
    assert repo.get_by_name_any_status("f2-recycle") is None


# ── A1 + F1 — PID-reuse defense ─────────────────────────────────────


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="service_spawner is not supported on Windows",
)
def test_stop_signals_process_group(manager: ServiceToolManager, repo: ServiceRepo) -> None:
    """``stop`` signals the PROCESS GROUP via killpg (A1).

    Spawns ``sh -c 'sleep 60 & sleep 60 & wait'`` — a shell with two
    background ``sleep`` children. The shell is its own session + group
    leader (pgid == shell-pid). A successful stop() must terminate
    both shell AND both children. We verify by waiting for the
    children to die.
    """
    start = asyncio.run(
        _start(
            manager,
            "grouped",
            ["sh", "-c", "sleep 60 & sleep 60 & wait"],
            cwd="/tmp",
        )
    )
    assert start["status"] == "running"
    pid = start["pid"]
    assert pid is not None

    # The shell and its children are all in the same pgid.
    child_pid_before = _any_child_pid(pid)
    assert child_pid_before is not None, (
        "shell did not fork a sleep child; test environment unexpected"
    )

    # Stop the service. force=True skips grace.
    asyncio.run(_stop(manager, "grouped", force=True))

    # The shell PID should be gone.
    assert not is_process_alive(pid)
    # The forked sleep child should also be gone (killpg reached it).
    time.sleep(0.05)
    assert not is_process_alive(child_pid_before), (
        "killpg did not reach the forked sleep child — "
        "service_stop signaled the wrong process"
    )


def _any_child_pid(parent_pid: int) -> int | None:
    """Find any child PID of ``parent_pid`` via ``pgrep -P`` (test helper)."""
    import subprocess

    try:
        out = subprocess.run(
            ["pgrep", "-P", str(parent_pid)],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    pids = out.stdout.strip().split()
    return int(pids[0])


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="service_spawner is not supported on Windows",
)
def test_stop_returns_exited_for_already_exited_row(
    manager: ServiceToolManager, repo: ServiceRepo
) -> None:
    """``stop`` on an already-EXITED row returns ``exited`` (idempotent)."""
    asyncio.run(_start(manager, "exit-1", ["sleep", "60"], cwd="/tmp"))
    # Force-stop the service to mark it EXITED.
    asyncio.run(_stop(manager, "exit-1", force=True))
    # Second stop returns the idempotent EXITED shape.
    result = asyncio.run(_stop(manager, "exit-1", force=False))
    assert result["status"] == "exited"


def test_stop_returns_not_found_for_unknown_name(
    manager: ServiceToolManager,
) -> None:
    """``stop`` on a never-registered name returns ``not_found``."""
    result = asyncio.run(_stop(manager, "never-existed", force=True))
    assert result["status"] == "not_found"


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="service_spawner is not supported on Windows",
)
def test_stop_pid_already_dead_returns_pid_dead(
    manager: ServiceToolManager, repo: ServiceRepo
) -> None:
    """A row whose PID died externally returns ``pid_dead`` (F1 ownership re-verify)."""
    asyncio.run(_start(manager, "dead-1", ["sleep", "60"], cwd="/tmp"))
    # Get the row and force-kill the PID externally.
    row = repo.get_by_name("dead-1")
    assert row is not None and row.pid is not None
    try:
        os.killpg(row.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    # Brief settle so the kernel reaps.
    time.sleep(0.1)

    result = asyncio.run(_stop(manager, "dead-1", force=False))
    assert result["status"] == "exited"
    assert result["reason"] == "pid_dead"


# ── F1 — PID-recycle branches (deterministic, anti-hang seams) ──────
#
# Seam rules for these tests (Phase 1.B review follow-ups — the prior
# attempt hung on violations of exactly these):
#
#   * ``os.killpg`` is patched ONLY as a record-then-FORWARD recorder
#     (``_recording_killpg``) — the real signal is always delivered,
#     so no real child spawned here can ever become unkillable.
#   * ``get_process_start_time`` is patched at the MANAGER-MODULE seam
#     and returns DISTINCT INTEGER tokens (``row.start_time`` vs
#     ``row.start_time + 987_654_321``) — never float comparisons.
#   * The pre-kill test's clock patch replaces the ``time`` NAME
#     inside the manager module ONLY (``_ManagerClockShim``) — the
#     ``asyncio`` event loop keeps the REAL ``time`` module, so
#     ``asyncio.sleep`` can never hang on a fake clock.
#   * Grace/poll constants are monkeypatched tiny so each test <2s.


_REAL_KILLPG = os.killpg  # captured at import time — NEVER a test patch


def _recording_killpg(monkeypatch) -> list[tuple[int, int]]:
    """Patch ``os.killpg`` at the manager seam as record-then-forward.

    The recorder appends ``(pid, sig)`` and then DELEGATES to the real
    ``os.killpg`` — signals are always really delivered, so real test
    children die on schedule even while we assert on the recording.
    """
    calls: list[tuple[int, int]] = []

    def _record_and_forward(pid: int, sig: int) -> None:
        calls.append((pid, sig))
        _REAL_KILLPG(pid, sig)

    monkeypatch.setattr(
        "daemon.services.service_tool_manager.os.killpg", _record_and_forward
    )
    return calls


def _hard_kill(pid: int | None) -> None:
    """Best-effort REAL SIGKILL cleanup for a spawned test child.

    Uses the import-time ``_REAL_KILLPG`` so it never routes through
    the recorder (keeping ``killpg_calls`` recordings assertion-clean)
    and works even while the recorder patch is active.
    """
    if pid is None:
        return
    try:
        _REAL_KILLPG(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


class _ManagerClockShim:
    """Deterministic ``time.monotonic`` for the MANAGER module only.

    Installed as ``daemon.services.service_tool_manager.time`` (the
    module NAME binding) so ``asyncio`` and every other module keep
    the REAL ``time`` module — the event-loop clock is never faked,
    which is what makes this hang-proof. ``monotonic()`` pops scripted
    INTEGER ticks; when the script runs dry it keeps INCREASING
    (monotonic contract) so any pending loop condition exits. All
    other attributes delegate to the real module.
    """

    def __init__(self, ticks: list[int]) -> None:
        self._ticks = list(ticks)
        self.now: int = ticks[0]

    def monotonic(self) -> int:
        if self._ticks:
            self.now = self._ticks.pop(0)
        else:
            self.now += 1_000
        return self.now

    def __getattr__(self, name: str):  # noqa: ANN204
        return getattr(time, name)


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="service_spawner is not supported on Windows",
)
def test_stop_pre_signal_recycle_returns_pid_recycled(
    manager: ServiceToolManager, repo: ServiceRepo, monkeypatch
) -> None:
    """F1 layer (a): pre-signal re-verify mismatch ⇒ ``pid_recycled``, NO signal.

    The FIRST ``get_process_start_time`` read (stop() ~L462) returns a
    MISMATCHED integer token ⇒ the manager returns
    ``{"status": "exited", "reason": "pid_recycled"}`` WITHOUT ever
    calling ``os.killpg`` — a signal against the recycled PID would
    kill an unrelated user process (the exact hazard F1 closes).
    """
    asyncio.run(_start(manager, "recycle-pre", ["sleep", "60"], cwd="/tmp"))
    row = repo.get_by_name("recycle-pre")
    assert row is not None and row.pid is not None and row.start_time is not None

    mismatched_token = row.start_time + 987_654_321  # DISTINCT INTEGER

    monkeypatch.setattr(
        "daemon.services.service_tool_manager.get_process_start_time",
        lambda pid: mismatched_token,
    )
    killpg_calls = _recording_killpg(monkeypatch)

    try:
        result = asyncio.run(_stop(manager, "recycle-pre", force=False))
    finally:
        # No signal was sent on this path — the child is still alive.
        _hard_kill(row.pid)

    assert result["status"] == "exited"
    assert result["reason"] == "pid_recycled"
    assert result["pid"] == row.pid

    # NO signal was sent — the (forwarding) recorder stayed empty.
    assert killpg_calls == [], (
        f"F1 violation: killpg fired despite pre-signal recycle: {killpg_calls}"
    )

    # Row marked EXITED (the name slot is released).
    reread = repo.get_by_name_any_status("recycle-pre")
    assert reread is not None
    assert reread.status == ServiceStatus.EXITED.value


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="service_spawner is not supported on Windows",
)
def test_stop_recycle_during_grace_no_escalation(
    manager: ServiceToolManager, repo: ServiceRepo, monkeypatch
) -> None:
    """F1 layer (b): recycle detected mid-grace ⇒ no SIGKILL escalation.

    Read #1 (pre-signal re-verify) MATCHES so the initial SIGTERM goes
    out; every later read (grace iterations) returns the mismatched
    token ⇒ the FIRST grace iteration detects the recycle and returns
    ``reason="pid_recycled_during_grace"``. The recorder must contain
    exactly the initial SIGTERM — never a SIGKILL escalation.
    """
    asyncio.run(_start(manager, "recycle-grace", ["sleep", "60"], cwd="/tmp"))
    row = repo.get_by_name("recycle-grace")
    assert row is not None and row.pid is not None and row.start_time is not None

    reads: list[int] = []

    def scripted_read(pid: int) -> int:
        reads.append(pid)
        # Read #1 = pre-signal re-verify → MATCH (SIGTERM goes out).
        # Every later read = grace iteration → MISMATCH (recycled).
        return row.start_time if len(reads) == 1 else row.start_time + 987_654_321

    monkeypatch.setattr(
        "daemon.services.service_tool_manager.get_process_start_time",
        scripted_read,
    )
    killpg_calls = _recording_killpg(monkeypatch)

    import daemon.services.service_tool_manager as _stm

    # Tiny grace window (<0.2s): the first grace iteration (~poll
    # interval in) already sees the mismatch, so runtime ≈ poll+ε.
    monkeypatch.setattr(_stm, "DEFAULT_STOP_GRACE_SECONDS", 0.15)
    monkeypatch.setattr(_stm, "DEFAULT_GRACE_POLL_INTERVAL_SECONDS", 0.05)

    try:
        result = asyncio.run(_stop(manager, "recycle-grace", force=False))
    finally:
        _hard_kill(row.pid)

    assert result["status"] == "exited"
    assert result["reason"] == "pid_recycled_during_grace"
    assert result["pid"] == row.pid

    # ≥2 reads: the pre-signal re-verify plus at least one grace
    # iteration (the mismatch was detected DURING grace, not before
    # the signal).
    assert len(reads) >= 2, (
        f"expected ≥1 grace re-verify after the pre-signal read; "
        f"got {len(reads)} reads"
    )

    # Exactly ONE killpg: the initial SIGTERM. No SIGKILL escalation —
    # the recycled PID is now an unrelated process.
    assert killpg_calls == [(row.pid, signal.SIGTERM)], (
        f"F1 violation: expected exactly one SIGTERM, got {killpg_calls}"
    )

    reread = repo.get_by_name_any_status("recycle-grace")
    assert reread is not None
    assert reread.status == ServiceStatus.EXITED.value


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="service_spawner is not supported on Windows",
)
def test_stop_recycle_pre_kill_returns_pid_recycled_pre_kill(
    manager: ServiceToolManager, repo: ServiceRepo, monkeypatch
) -> None:
    """F1 layer (c): pre-kill re-verify mismatch ⇒ ``pid_recycled_pre_kill``, NO SIGKILL.

    Scripted via the manager-scoped fake clock (``_ManagerClockShim``,
    manager module keeps the REAL grace constant 5.0 — the fake clock
    compresses the window; only the poll interval is patched tiny)::

        clock 1000          → deadline = 1000 + 5.0 = 1005
        clock 1001..1004    → 4 grace iterations, each read MATCHES
        clock 1006 ≥ 1005   → loop exits WITHOUT break (never "dies")
        pre-kill read       → MISMATCH (token flip keyed on clock ≥ deadline)

    ⇒ ``reason="pid_recycled_pre_kill"``, recorder holds ONLY the
    initial SIGTERM, SIGKILL escalation never reached.
    """
    asyncio.run(_start(manager, "recycle-prekill", ["sleep", "60"], cwd="/tmp"))
    row = repo.get_by_name("recycle-prekill")
    assert row is not None and row.pid is not None and row.start_time is not None

    import daemon.services.service_tool_manager as _stm

    clock = _ManagerClockShim([1000, 1001, 1002, 1003, 1004, 1006])
    monkeypatch.setattr(_stm, "time", clock)

    deadline_token = 1005  # first clock read (1000) + real grace 5.0

    reads: list[int] = []

    def flip_on_deadline(pid: int) -> int:
        reads.append(pid)
        # MATCH while the fake clock is inside the grace window;
        # MISMATCH from the first read at/after the deadline — which
        # is exactly the loop-else pre-kill re-verify.
        if clock.now < deadline_token:
            return row.start_time
        return row.start_time + 987_654_321

    monkeypatch.setattr(
        "daemon.services.service_tool_manager.get_process_start_time",
        flip_on_deadline,
    )
    killpg_calls = _recording_killpg(monkeypatch)

    # Only the poll interval is shrunk (real sleeps × 4 ≈ 0.2s wall);
    # DEFAULT_STOP_GRACE_SECONDS stays 5.0 — the fake clock governs.
    monkeypatch.setattr(_stm, "DEFAULT_GRACE_POLL_INTERVAL_SECONDS", 0.05)

    try:
        result = asyncio.run(_stop(manager, "recycle-prekill", force=False))
    finally:
        _hard_kill(row.pid)

    assert result["status"] == "exited"
    assert result["reason"] == "pid_recycled_pre_kill"
    assert result["pid"] == row.pid

    # The loop really ITERATED with matches before the pre-kill flip:
    # 6 reads = 1 pre-signal + 4 grace + 1 pre-kill (deterministic —
    # the fake clock scripts exactly 4 in-window checks).
    assert len(reads) == 6, (
        f"expected 6 ownership reads (1 pre-signal + 4 grace + 1 "
        f"pre-kill), got {len(reads)}"
    )

    # Exactly ONE killpg: the initial SIGTERM — the SIGKILL escalation
    # must NOT fire on a pre-kill recycle.
    assert killpg_calls == [(row.pid, signal.SIGTERM)], (
        f"F1 violation: SIGKILL escalation fired on pre-kill recycle; "
        f"got {killpg_calls}"
    )

    reread = repo.get_by_name_any_status("recycle-prekill")
    assert reread is not None
    assert reread.status == ServiceStatus.EXITED.value


# ── A2 — async grace path ───────────────────────────────────────────


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="service_spawner is not supported on Windows",
)
def test_stop_async_during_grace(manager: ServiceToolManager, repo: ServiceRepo) -> None:
    """``stop`` uses ``await asyncio.sleep`` + ``await asyncio.to_thread`` (A2).

    Spawns a process that IGNORES SIGTERM (``trap "" TERM; sleep 30``)
    so the grace path runs to completion. Verifies the event loop
    remains responsive by concurrently scheduling a tiny task during
    the stop — if the manager were busy-waiting on ``time.sleep``,
    the concurrent task would NOT execute during the 5s grace.
    """
    asyncio.run(
        _start(
            manager,
            "async-grace",
            ["sh", "-c", 'trap "" TERM; sleep 30'],
            cwd="/tmp",
        )
    )

    async def stop_and_yield() -> tuple[dict, int]:
        # Schedule a counter that ticks every 10ms during the stop.
        ticks = 0

        async def ticker() -> None:
            nonlocal ticks
            for _ in range(500):  # 5s budget
                await asyncio.sleep(0.01)
                ticks += 1

        ticker_task = asyncio.create_task(ticker())
        # Use a short grace to keep the test fast.
        result = await manager.stop(name="async-grace", force=False)
        await ticker_task
        return result, ticks

    # We can't easily inject grace_seconds into the manager — the
    # manager uses DEFAULT_STOP_GRACE_SECONDS=5. Override the constant
    # for this test to keep the runtime bounded.
    import daemon.services.service_tool_manager as mod

    original = mod.DEFAULT_STOP_GRACE_SECONDS
    mod.DEFAULT_STOP_GRACE_SECONDS = 0.5
    try:
        result, ticks = asyncio.run(stop_and_yield())
    finally:
        mod.DEFAULT_STOP_GRACE_SECONDS = original

    assert result["status"] == "exited"
    # The ticker ran for ~0.5s at 10ms cadence ⇒ ~50 ticks. If the
    # manager were blocking on time.sleep the ticker would NOT run.
    assert ticks > 20, (
        f"event loop was blocked during grace path: only {ticks} "
        f"ticks fired in ~0.5s; expected ~50. A2 (async grace) violated."
    )


# ── A13 — mark_exited row-count contract ─────────────────────────────


def test_mark_exited_treated_as_idempotent(
    manager: ServiceToolManager, repo: ServiceRepo
) -> None:
    """Second ``mark_exited`` on the same row returns 0 — idempotent success.

    Verifies the A13 contract that ``ServiceToolManager`` consumes:
    the rowcount return is treated as idempotent (not as failure).
    """
    row = repo.insert(
        name="idem-1",
        command=["sleep", "10"],
        pid=4242,
        start_time=1_700_000_000,
        cwd="/tmp",
        status=ServiceStatus.RUNNING.value,
        started_by_instance_id="inst-test",
        started_by_agent_id="worker",
        log_path="/tmp/idem-1.log",
    )
    rc1 = repo.mark_exited(row.id)
    assert rc1 == 1
    # Second call — 0 is the "race-lost / idempotent" signal.
    rc2 = repo.mark_exited(row.id)
    assert rc2 == 0
    # Exit code is immutable on the EXITED row.
    reread = repo.get_by_id(row.id)
    assert reread is not None
    assert reread.status == ServiceStatus.EXITED.value


# ── Status / list / logs ────────────────────────────────────────────


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="service_spawner is not supported on Windows",
)
def test_status_returns_running(manager: ServiceToolManager, repo: ServiceRepo) -> None:
    """``status`` returns ``running`` for a live service."""
    asyncio.run(_start(manager, "live-1", ["sleep", "60"], cwd="/tmp"))
    result = asyncio.run(manager.status(name="live-1"))
    assert result["status"] in ("running", "starting")
    assert result["name"] == "live-1"
    assert result["pid"] is not None

    asyncio.run(_stop(manager, "live-1", force=True))


def test_status_returns_not_found(manager: ServiceToolManager) -> None:
    """``status`` on a never-registered name returns ``not_found``."""
    result = asyncio.run(manager.status(name="never-registered"))
    assert result["status"] == "not_found"


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="service_spawner is not supported on Windows",
)
def test_list_all_returns_active_rows(
    manager: ServiceToolManager, repo: ServiceRepo
) -> None:
    """``list_all`` returns rows ordered by created_at DESC."""
    asyncio.run(_start(manager, "l1", ["sleep", "60"], cwd="/tmp"))
    asyncio.run(_start(manager, "l2", ["sleep", "60"], cwd="/tmp"))
    rows = asyncio.run(manager.list_all())
    names = [r["name"] for r in rows]
    assert "l1" in names
    assert "l2" in names

    asyncio.run(_stop(manager, "l1", force=True))
    asyncio.run(_stop(manager, "l2", force=True))


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="service_spawner is not supported on Windows",
)
def test_logs_reads_from_row_log_path(
    manager: ServiceToolManager, repo: ServiceRepo
) -> None:
    """``logs`` reads ``log_path`` from the row (F19 — never re-derives)."""
    asyncio.run(_start(manager, "logged", ["echo", "hello", "world"], cwd="/tmp"))
    # Give the shell a moment to write.
    time.sleep(0.2)
    result = asyncio.run(manager.logs(name="logged", tail_lines=20))
    # The log contains ``hello world`` (echo's stdout).
    assert "hello" in result.lower() or "world" in result.lower()

    asyncio.run(_stop(manager, "logged", force=True))


def test_logs_returns_empty_for_unknown_name(
    manager: ServiceToolManager,
) -> None:
    """``logs`` on an unknown name returns ``""`` (not raise)."""
    result = asyncio.run(manager.logs(name="never-existed", tail_lines=10))
    assert result == ""


# ── kill-switch flag ────────────────────────────────────────────────


def test_disabled_flag_returns_disabled_shape(
    repo: ServiceRepo, tmp_path, monkeypatch
) -> None:
    """When ``enabled=False``, ``start`` / ``stop`` / ``status`` return the disabled shape."""
    monkeypatch.setenv("ENSEMBLE_SERVICE_LOG_DIR", str(tmp_path / "logs"))
    mgr = ServiceToolManager(repo=repo, cap=3, enabled=False)
    start_result = asyncio.run(
        _start(mgr, "dis-1", ["sleep", "10"], cwd="/tmp")
    )
    assert start_result["status"] == "disabled"

    stop_result = asyncio.run(_stop(mgr, "dis-1", force=False))
    assert stop_result["status"] == "disabled"

    status_result = asyncio.run(mgr.status(name="dis-1"))
    assert status_result["status"] == "disabled"


def test_logs_disabled_returns_marker(
    repo: ServiceRepo, tmp_path, monkeypatch
) -> None:
    """Council F4(a): ``logs`` with ``enabled=False`` returns the
    disabled-marker string — NO DB read, no empty-string masquerade.

    The uniform OFF contract: start/stop/status/list_all all gate on
    the kill switch; ``logs`` was the last holdout. The str surface
    renders the canonical disabled dict as text
    (:data:`daemon.services.service_tool_manager.DISABLED_LOGS_MARKER`)
    so the marker is honest AND structured.
    """
    from daemon.services.service_tool_manager import DISABLED_LOGS_MARKER

    monkeypatch.setenv("ENSEMBLE_SERVICE_LOG_DIR", str(tmp_path / "logs"))
    mgr = ServiceToolManager(repo=repo, cap=3, enabled=False)

    result = asyncio.run(mgr.logs(name="whatever", tail_lines=10))
    assert result == DISABLED_LOGS_MARKER, (
        f"OFF manager.logs MUST return the disabled-marker text; "
        f"got {result!r}"
    )
    # The marker carries the same {status, reason} contract as the
    # dict surfaces.
    assert '"status": "disabled"' in result
    assert '"reason": "service_tool_enabled=False"' in result


# ── constructor validation ─────────────────────────────────────────


def test_constructor_rejects_non_repo() -> None:
    """TypeError when the repo arg is not a ``ServiceRepo``."""
    with pytest.raises(TypeError):
        ServiceToolManager(repo="not a repo", cap=10)  # type: ignore[arg-type]


def test_constructor_rejects_cap_lt_1(repo: ServiceRepo) -> None:
    """ValueError when ``cap < 1``."""
    with pytest.raises(ValueError):
        ServiceToolManager(repo=repo, cap=0)
    with pytest.raises(ValueError):
        ServiceToolManager(repo=repo, cap=-1)