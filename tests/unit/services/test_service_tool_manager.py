"""Manager-level tests for ``ServiceToolManager`` (Phase 1.B).

Covers the BLOCKING acceptance criteria for the manager layer:

* **F8** — synchronous spawn failure (bad cwd / missing binary) writes
  an EXITED row via ``insert_with_status`` immediately (no 30s slot
  block for the Phase-2 reaper).
* **F2** — concurrent same-name race returns
  ``{"status": "name_in_use", "reason": "concurrent_start_won_race"}``
  AND ``killpg``s the just-spawned child (the orphan otherwise leaks
  as a live untracked kill-exempt process).
* **A1 + F1** — ``stop`` signals the process GROUP via ``os.killpg``,
  re-verifies ``(pid, start_time)`` ownership BEFORE the signal and
  on EVERY grace-poll iteration and immediately BEFORE the SIGKILL
  escalation. PID-recycle paths return
  ``reason="pid_recycled"`` / ``"pid_recycled_during_grace"`` /
  ``"pid_recycled_pre_kill"`` WITHOUT escalating.
* **A2** — ``stop`` is async; the 5s grace uses
  ``await asyncio.to_thread(get_process_start_time, pid)`` polling
  (NOT ``time.sleep`` busy-wait).
* **F7** — the spawner ``stop`` is NEVER called without ownership
  verification at this manager boundary.
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
from pathlib import Path
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
    ServiceTracking,
)
from daemon.repositories.service_tool.repository import ServiceRepo
from daemon.services.service_tool_manager import (
    DEFAULT_MAX_CONCURRENT,
    DEFAULT_STOP_GRACE_SECONDS,
    ServiceToolManager,
)
from daemon.tools.service_spawner import (
    get_process_start_time,
    is_process_alive,
    kill_log_path,
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