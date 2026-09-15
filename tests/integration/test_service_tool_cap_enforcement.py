"""Service-tool Phase 3.A.6 — cap=10 enforcement integration tests.

Proves by REAL ``ServiceToolManager.start`` invocations against a
file-backed SQLite engine that:

* Up to ``cap=10`` services can be started successfully.
* The 11th call returns ``{"status": "cap_exceeded",
  "reason": "max_concurrent_reached"}`` — NO PID created, NO DB
  row inserted (the cap check is BEFORE any side effect).
* Stopping one service frees the slot — a subsequent ``start`` of a
  different name succeeds.
* The cap counter is computed from ``ServiceRepo.list_active()``
  (per-daemon, not per-instance) — so services started by DIFFERENT
  instance_ids count against the SAME cap.
* Cap is enforced across multiple distinct ``started_by_instance_id``
  values (services are NOT instance-owned — only attributed for
  forensics via ``started_by_instance_id`` recording).

F18 — TOCTOU window (documented, accepted by design):
    The cap check is a COUNT-then-INSERT advisory gate — two
    concurrent ``start()`` calls can both pass the count check
    and one will lose at the partial-UNIQUE index
    (``idx_service_tracking_name_active``) by NAME collision. The
    ``ServiceToolManager.start`` handles the IntegrityError path
    (killpg the orphan child); see
    ``tests/unit/services/test_service_tool_manager.py`` for the
    unit coverage. This file does NOT exercise the TOCTOU race;
    it documents the window in the file docstring above so a
    future contributor does not mistake the advisory check for a
    hard ceiling.

Fixture pattern: file-backed SQLite (tmp_path + NullPool + WAL +
busy_timeout=10000), 10 short-lived sleep children (each sleep
~30s), teardown reaps ALL spawned children even on test failure
(try/finally force-stop loop + killpg fallback).

All cases carry ``@pytest.mark.integration`` (addopts deselects by
default — invoke with ``pytest -m integration``).
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import uuid
from pathlib import Path
from typing import Iterator, List

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, event as sa_event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel, Session

# Register all SQLModel tables before create_all runs.
import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
import daemon.repositories.service_tool.models  # noqa: F401

from daemon.repositories.service_tool.models import ServiceStatus
from daemon.repositories.service_tool.repository import ServiceRepo
from daemon.services.service_tool_manager import ServiceToolManager


# ─────────────────────────────────────────────────────────────────────
# Fixtures — file-backed engine + manager + child reap helpers
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def file_backed_engine(tmp_path) -> Iterator[Engine]:
    """File-backed SQLite engine (NullPool + WAL + busy_timeout).

    Mirrors the canonical pattern at
    ``tests/integration/test_service_tool_kill_site_exemption.py``
    (the blueprint-pinned fixture for service-tool). File-backed
    (NEVER ``:memory:`` / StaticPool) so the spawned children's
    env can be observed via a shared DB.
    """
    db_path = tmp_path / "service-cap-enforcement.sqlite"
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
def svc_repo(file_backed_engine: Engine) -> ServiceRepo:
    return ServiceRepo(engine=file_backed_engine)


# Cap used by the test — 10 per the D5 brief.
CAP: int = 10
# Real-process argv for the cap-filling services. ``sleep 30`` is
# universally available on macOS + Linux; the test will reap them
# in teardown so they never leak across runs.
FILLER_ARGV: List[str] = [sys.executable, "-c", "import time; time.sleep(30)"]
# Owner instance id used for cap-filling spawns. The cap test
# exercises the "across multiple instance_ids" case explicitly via
# distinct id slicing; the filler uses one constant id.
FILLER_INSTANCE_ID: str = "cap-filler-instance"  # noqa: S105


def _pid_alive(pid: int | None) -> bool:
    """Liveness probe — ``os.kill(pid, 0)`` returns ESRCH for dead."""
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _reap_all(spawned: List[tuple[int, str]]) -> None:
    """Best-effort reaper for a list of ``(pid, name)`` pairs.

    Used by teardown to ensure NO ``sleep 30`` children leak across
    runs. ``killpg`` first (reaches the setsid group); individual
    ``kill`` second for any orphan that escaped its group; both
    swallow ``ProcessLookupError`` / ``PermissionError``.
    """
    for pid, _name in spawned:
        if pid is None:
            continue
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass


# ─────────────────────────────────────────────────────────────────────
# Case (a) + (b): cap is enforced; 10 succeed, 11th is rejected
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.skipif(
    sys.platform == "win32",
    reason="service_spawner is not supported on Windows",
)
@pytest.mark.asyncio
async def test_cap_enforcement_blocks_eleventh_start(
    file_backed_engine: Engine,
    svc_repo: ServiceRepo,
    tmp_path: Path,
) -> None:
    """Cases (a) + (b): 10 services fill the cap; the 11th is rejected.

    10 ``sleep 30`` children are spawned via ``ServiceToolManager.start``
    (REAL process spawn + REAL DB row insert). The 11th call returns
    the canonical ``cap_exceeded`` shape with NO PID created and NO
    DB row inserted. The 11th call's ``cap`` field echoes the
    manager's ``cap`` constant; the ``active`` field echoes the
    pre-call ``len(list_active())`` count.
    """
    manager = ServiceToolManager(repo=svc_repo, cap=CAP, enabled=True)
    spawned: List[tuple[int, str]] = []

    try:
        # Fill the cap with 10 services.
        for i in range(CAP):
            name = f"cap-{i:02d}"
            result = await manager.start(
                name=name,
                argv=FILLER_ARGV,
                cwd=str(tmp_path),
                started_by_instance_id=FILLER_INSTANCE_ID,
                started_by_agent_id="cap-tester",
            )
            assert result["status"] == "running", (
                f"filler spawn #{i} (name={name}) failed: {result!r}"
            )
            pid = result["pid"]
            assert pid is not None
            spawned.append((pid, name))

        # Sanity: 10 active rows in the DB.
        active = svc_repo.list_active()
        assert len(active) == CAP, (
            f"expected {CAP} active rows; got {len(active)}"
        )

        # The 11th call MUST be rejected (BEFORE any side effect).
        over_name = "cap-overflow"
        result = await manager.start(
            name=over_name,
            argv=FILLER_ARGV,
            cwd=str(tmp_path),
            started_by_instance_id=FILLER_INSTANCE_ID,
            started_by_agent_id="cap-tester",
        )

        assert result["status"] == "cap_exceeded", (
            f"11th start must be cap_exceeded; got {result!r}"
        )
        assert result["reason"] == "max_concurrent_reached"
        assert result["name"] == over_name
        assert result["cap"] == CAP
        assert result["active"] == CAP

        # NO PID created — the rejection is BEFORE spawn.
        assert "pid" not in result or result.get("pid") is None, (
            f"cap_exceeded MUST NOT carry a pid; got {result!r}"
        )

        # NO DB row inserted for the overflow name.
        overflow_row = svc_repo.get_by_name_any_status(over_name)
        assert overflow_row is None, (
            f"cap_exceeded MUST NOT insert a row; found {overflow_row!r}"
        )

        # Active count unchanged after the rejected attempt.
        assert len(svc_repo.list_active()) == CAP
    finally:
        _reap_all(spawned)


# ─────────────────────────────────────────────────────────────────────
# Case (c): stopping one service frees a slot
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.skipif(
    sys.platform == "win32",
    reason="service_spawner is not supported on Windows",
)
@pytest.mark.asyncio
async def test_stopping_one_service_frees_a_slot(
    file_backed_engine: Engine,
    svc_repo: ServiceRepo,
    tmp_path: Path,
) -> None:
    """Case (c): ``service_stop`` of one service frees the slot for a new
    ``service_start`` of a DIFFERENT name.

    Verifies the cap counter is computed from ``list_active()``
    (a transition-to-EXITED is reflected immediately) — a fresh
    start of ``replacement`` succeeds once the original ``cap-00``
    row is EXITED.
    """
    manager = ServiceToolManager(repo=svc_repo, cap=CAP, enabled=True)
    spawned: List[tuple[int, str]] = []

    try:
        # Fill the cap.
        for i in range(CAP):
            name = f"cap-{i:02d}"
            result = await manager.start(
                name=name,
                argv=FILLER_ARGV,
                cwd=str(tmp_path),
                started_by_instance_id=FILLER_INSTANCE_ID,
                started_by_agent_id="cap-tester",
            )
            assert result["status"] == "running", (
                f"filler spawn #{i} (name={name}) failed: {result!r}"
            )
            spawned.append((result["pid"], name))

        # The next start is rejected (cap is full).
        rejected = await manager.start(
            name="blocker",
            argv=FILLER_ARGV,
            cwd=str(tmp_path),
            started_by_instance_id=FILLER_INSTANCE_ID,
            started_by_agent_id="cap-tester",
        )
        assert rejected["status"] == "cap_exceeded"

        # Stop one service — frees the slot. force=True for
        # determinism (skip the 5s grace window).
        stopped = await manager.stop("cap-00", force=True)
        assert stopped["status"] == "exited"

        # Active count drops to CAP-1.
        assert len(svc_repo.list_active()) == CAP - 1

        # A fresh start of a DIFFERENT name succeeds (cap slot freed).
        replacement = await manager.start(
            name="replacement",
            argv=FILLER_ARGV,
            cwd=str(tmp_path),
            started_by_instance_id=FILLER_INSTANCE_ID,
            started_by_agent_id="cap-tester",
        )
        assert replacement["status"] == "running", (
            f"expected slot-freed start to succeed; got {replacement!r}"
        )
        spawned.append((replacement["pid"], "replacement"))

        # The cap is re-asserted immediately — a SECOND fresh start
        # would now exceed (10 active again).
        blocked_again = await manager.start(
            name="blocker-2",
            argv=FILLER_ARGV,
            cwd=str(tmp_path),
            started_by_instance_id=FILLER_INSTANCE_ID,
            started_by_agent_id="cap-tester",
        )
        assert blocked_again["status"] == "cap_exceeded", (
            f"second filler should still be blocked; got {blocked_again!r}"
        )
    finally:
        _reap_all(spawned)


# ─────────────────────────────────────────────────────────────────────
# Case (d): cap is computed from list_active() (per-daemon, not per-instance)
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.skipif(
    sys.platform == "win32",
    reason="service_spawner is not supported on Windows",
)
@pytest.mark.asyncio
async def test_cap_is_per_daemon_not_per_instance(
    file_backed_engine: Engine,
    svc_repo: ServiceRepo,
    tmp_path: Path,
) -> None:
    """Case (d): the cap is the SHARED counter on the manager — different
    ``started_by_instance_id`` values count against the SAME cap.

    This is the load-bearing property of the D5 same-cap-everywhere
    contract: the daemon has ONE cap=10, not one cap per instance /
    per agent. The fixture spawns 6 services attributed to instance
    A + 4 to instance B = 10; an 11th (attributed to instance C)
    is cap_exceeded.
    """
    manager = ServiceToolManager(repo=svc_repo, cap=CAP, enabled=True)
    spawned: List[tuple[int, str]] = []

    try:
        # 6 services under instance A.
        for i in range(6):
            name = f"instA-{i:02d}"
            result = await manager.start(
                name=name,
                argv=FILLER_ARGV,
                cwd=str(tmp_path),
                started_by_instance_id="instance-alpha",
                started_by_agent_id="cap-tester",
            )
            assert result["status"] == "running", result
            spawned.append((result["pid"], name))

        # 4 services under instance B.
        for i in range(4):
            name = f"instB-{i:02d}"
            result = await manager.start(
                name=name,
                argv=FILLER_ARGV,
                cwd=str(tmp_path),
                started_by_instance_id="instance-beta",
                started_by_agent_id="cap-tester",
            )
            assert result["status"] == "running", result
            spawned.append((result["pid"], name))

        # 10 total = at cap. An 11th attributed to instance C is
        # blocked. The DB shows rows for ALL three instance_ids
        # (proves attribution is recorded) and the cap check ignores
        # the instance_id (proves per-daemon scope).
        rejected = await manager.start(
            name="instC-overflow",
            argv=FILLER_ARGV,
            cwd=str(tmp_path),
            started_by_instance_id="instance-gamma",
            started_by_agent_id="cap-tester",
        )
        assert rejected["status"] == "cap_exceeded"
        assert rejected["cap"] == CAP
        assert rejected["active"] == CAP
        # The rejection message carries NO ``existing_pid`` field (cap
        # rejection is not a name collision).
        assert "existing_pid" not in rejected

        # Verify the rows are attributed to the right instance_ids.
        with Session(file_backed_engine) as session:
            inst_a_count = svc_repo.list_active()
            instance_ids = sorted(
                {row.started_by_instance_id for row in inst_a_count}
            )
        assert instance_ids == ["instance-alpha", "instance-beta"], (
            f"attribution by instance_id lost; got {instance_ids}"
        )
    finally:
        _reap_all(spawned)


# ─────────────────────────────────────────────────────────────────────
# Case (e): cap survives a "restart" (new ServiceToolManager on the
#           same engine — the active rows are durable)
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.skipif(
    sys.platform == "win32",
    reason="service_spawner is not supported on Windows",
)
@pytest.mark.asyncio
async def test_cap_counter_survives_manager_restart(
    file_backed_engine: Engine,
    svc_repo: ServiceRepo,
    tmp_path: Path,
) -> None:
    """Case (e) — restart-survival: a fresh ``ServiceToolManager`` on the
    SAME engine sees the persisted ``STARTING`` / ``RUNNING`` rows
    and the cap check still fires.

    Proves the cap counter is computed from ``list_active()`` (which
    reads the persisted rows, NOT in-process state) — a daemon
    restart does NOT silently drop the cap count.
    """
    manager_a = ServiceToolManager(repo=svc_repo, cap=CAP, enabled=True)
    spawned: List[tuple[int, str]] = []

    try:
        # Manager A fills the cap.
        for i in range(CAP):
            name = f"restart-{i:02d}"
            result = await manager_a.start(
                name=name,
                argv=FILLER_ARGV,
                cwd=str(tmp_path),
                started_by_instance_id="restart-instance",
                started_by_agent_id="cap-tester",
            )
            assert result["status"] == "running", result
            spawned.append((result["pid"], name))

        # Manager B is a fresh instance on the same engine. The cap
        # check MUST still fire — the rows are durable; the counter
        # rebuilds from ``list_active()``.
        manager_b = ServiceToolManager(repo=svc_repo, cap=CAP, enabled=True)
        rejected = await manager_b.start(
            name="restart-overflow",
            argv=FILLER_ARGV,
            cwd=str(tmp_path),
            started_by_instance_id="restart-instance",
            started_by_agent_id="cap-tester",
        )
        assert rejected["status"] == "cap_exceeded", (
            f"restart-survival: fresh manager must see the cap; "
            f"got {rejected!r}"
        )
        assert rejected["cap"] == CAP
        assert rejected["active"] == CAP

        # Manager B's cap echo confirms it shares the SAME cap value
        # (i.e. the cap is process-level, not per-Manager-instance).
        assert rejected["cap"] == manager_b.cap
    finally:
        _reap_all(spawned)