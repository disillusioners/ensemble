"""Pause/resume watcher durability (Debug Phase 4) — integration.

End-to-end reproduction of the b7ead8a4 / d90b18f9 production
incident (2026-09-07):

  1. A leader (b7ead8a4 shape) has a child-completion watcher on its
     giter child (d90b18f9 shape) — ``dependency_watchers`` row,
     ``source_task_id=30975``, target = leader, payload
     ``metadata.child_id = child``.
  2. A pause cascade (the production
     ``_pause_cascade_db_sync``) suspends the tree; the watcher ends
     CANCELLED — never FIRED (the incident state).
  3. Resume (the production ``resume_instance_cascade``) — the
     Debug Phase 4 re-arm hook flips the CANCELLED, never-delivered
     watcher back to PENDING (the pre-fix code created NO
     replacement and the parent stuck in ``waiting_children``
     forever).
  4. The child completes → the corrective (parent, child)-keyed
     emit (``DependencyBus.emit_terminal_for_child_instance`` — task-
     id-agnostic, real bus over the real DB) FIRES the re-armed
     watcher and returns the FollowUp: the wake is restored.
     Exactly-once: a second corrective emit fires nothing.

Also pinned:

* Kill-switch OFF (``ENSEMBLE_WATCHER_REARM_ON_RESUME=0``): resume
  is byte-identical legacy behavior — the watcher stays CANCELLED.
* Child-terminal guard: a watcher cancelled because the child is
  TERMINAL stays cancelled after resume (terminate semantics
  preserved).
* Legacy rows without payload ``child_id``: re-arm resolves the
  child via ``source_task_id`` → task → instance.

Engine: file-backed SQLite at ``tmp_path`` with NullPool + WAL +
busy_timeout=10000 (repo conventions; never StaticPool+WriteGuard —
QUARANTINE).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel, select as sm_select

import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.dependency_bus.models import (
    DependencyWatcher,
    DependencyWatcherState,
)
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.services.dependency_bus import (
    DependencyBus,
    FollowUp,
    Outcome,
)
from daemon.repositories.dependency_bus.repository import (
    DependencyWatcherRepository,
)
from daemon.services.instance_lifecycle import (
    InstanceLifecycleService,
    _reset_watcher_rearm_for_tests,
)
from daemon.write_pause_guard import WritePauseGuard


# ─── Fixtures + helpers ─────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _rearm_kill_switch(monkeypatch):
    """Default ON for every test; each test resets the cached bool."""
    monkeypatch.delenv("ENSEMBLE_WATCHER_REARM_ON_RESUME", raising=False)
    _reset_watcher_rearm_for_tests()
    yield
    _reset_watcher_rearm_for_tests()


@pytest.fixture
def engine(tmp_path) -> Engine:
    """File-backed SQLite engine (NullPool + WAL + busy_timeout)."""
    db_path = tmp_path / "watcher-rearm-test.sqlite"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _configure_sqlite(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


def _seed_instance(
    engine: Engine,
    *,
    instance_id: str,
    parent_id: str | None,
    status: str,
) -> str:
    now = datetime.now(timezone.utc).isoformat()
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id="dev",
                agent_name="dev",
                agent_dir="/tmp/dev",
                parent_id=parent_id,
                status=status,
                version=1,
                created_at=now,
                updated_at=now,
                instance_metadata={},
            )
        )
        session.commit()
    return instance_id


def _seed_child_task(
    engine: Engine,
    *,
    task_id: int,
    instance_id: str,
    status: str,
) -> None:
    with Session(engine) as session:
        session.add(
            Task(
                id=task_id,
                work_id=f"work-{task_id}",
                task_type=TaskType.PROCESS_MESSAGE.value,
                instance_id=instance_id,
                status=status,
            )
        )
        session.commit()


def _seed_watcher(
    engine: Engine,
    *,
    source_task_id: str,
    target_instance_id: str,
    child_id: str | None,
    state: str,
) -> str:
    """A watcher row; CANCELLED = the post-pause incident state."""
    watch_id = str(uuid.uuid4())
    follow_up = FollowUp(
        target_instance_id=target_instance_id,
        message="child finished its turn",
        source="dependency_bus",
        metadata={"child_id": child_id} if child_id else {},
    )
    with Session(engine) as session:
        session.add(
            DependencyWatcher(
                watch_id=watch_id,
                source_task_id=source_task_id,
                target_instance_id=target_instance_id,
                follow_up_payload=follow_up.to_payload(),
                state=state,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        session.commit()
    return watch_id


def _watcher_state(engine: Engine, watch_id: str) -> DependencyWatcher:
    with Session(engine) as session:
        row = session.exec(
            sm_select(DependencyWatcher).where(
                DependencyWatcher.watch_id == watch_id
            )
        ).one()
        # Detach-safe read of the state fields.
        session.expunge(row)
        return row


def sa_update_dependency_watcher_state(watch_id: str, state: str):
    """Core UPDATE helper (keeps the incident-state mutation explicit)."""
    from sqlalchemy import update as sa_update

    return (
        sa_update(DependencyWatcher)
        .where(DependencyWatcher.watch_id == watch_id)
        .values(state=state)
    )


def _build_lifecycle(engine: Engine) -> tuple[InstanceLifecycleService, MagicMock]:
    """Lifecycle service over a real DB with a mock manager.

    Binds ONLY DB-facing real paths (pause/resume cascade helpers,
    tree traversal via the real instance repository); SSE / worker
    pool / graph tasks are mocks (no graph is running in this test).
    """
    from daemon.repositories.instance.repository import (
        SQLModelInstanceRepository,
    )

    manager = MagicMock(name="InstanceManager")
    manager.engine = engine
    manager.write_guard = WritePauseGuard()
    manager._graph_tasks = {}
    manager._instance_repository = SQLModelInstanceRepository(engine)
    manager._live_hub = AsyncMock(name="live_hub")
    manager._worker_pool = MagicMock(name="worker_pool")
    manager._task_repo = MagicMock(name="task_repo")
    # No pending injections queued for any node (avoids the
    # injection_consumed SSE branch in the pause cascade).
    manager.clear_injection = MagicMock(return_value=None)

    lifecycle = InstanceLifecycleService.__new__(InstanceLifecycleService)
    lifecycle._manager = manager
    return lifecycle, manager


# ─── The incident scenario, end to end ──────────────────────────────────────


@pytest.mark.asyncio
async def test_pause_cascade_resume_rearms_watcher_and_wake_fires(engine):
    """b7ead8a4/d90b18f9: pause → CANCELLED watcher → resume → PENDING
    → corrective emit FIRES → FollowUp returned (wake restored)."""
    leader_id = _seed_instance(
        engine,
        instance_id=f"leader-{uuid.uuid4().hex[:8]}",
        parent_id=None,
        status=InstanceStatus.WAITING_CHILDREN.value,
    )
    child_id = _seed_instance(
        engine,
        instance_id=f"giter-{uuid.uuid4().hex[:8]}",
        parent_id=leader_id,
        status=InstanceStatus.RUNNING.value,
    )
    # The child's in-flight task — the watcher's source (30975 shape).
    _seed_child_task(
        engine, task_id=30975, instance_id=child_id,
        status=TaskStatus.RUNNING.value,
    )
    watch_id = _seed_watcher(
        engine,
        source_task_id="30975",
        target_instance_id=leader_id,
        child_id=child_id,
        state=DependencyWatcherState.PENDING.value,
    )

    lifecycle, _manager = _build_lifecycle(engine)
    write_guard = WritePauseGuard()

    # 1. Pause cascade (production helper) — the whole tree.
    await lifecycle.pause_instance_cascade(leader_id)
    # Instances are PAUSED (read back alongside the watcher row).
    with Session(engine) as session:
        leader_inst = session.get(Instance, leader_id)
        child_inst = session.get(Instance, child_id)
        assert leader_inst.status == InstanceStatus.PAUSED.value
        assert child_inst.status == InstanceStatus.PAUSED.value

    # 2. The incident state: the watcher was cancelled mid-pause by
    #    the force-cancel cleanup path (whatever the actor, the DB
    #    shows CANCELLED, never FIRED, never delivered).
    with Session(engine) as session:
        session.execute(
            sa_update_dependency_watcher_state(watch_id, "CANCELLED"),
        )
        session.commit()
    row = _watcher_state(engine, watch_id)
    assert row.state == DependencyWatcherState.CANCELLED.value
    assert row.enqueued_at is None

    # 3. Resume via the production cascade — the re-arm hook runs
    #    (the child is PAUSED → non-terminal → re-armable; its task
    #    replays and can fire the watcher).
    result = await lifecycle.resume_instance_cascade(leader_id)
    assert leader_id in result["resumed_ids"]

    row = _watcher_state(engine, watch_id)
    assert row.state == DependencyWatcherState.PENDING.value, (
        "resume must re-arm the CANCELLED, never-delivered watcher "
        "(pre-fix: stayed CANCELLED → parent stuck forever)"
    )
    assert row.fired_at is None
    assert row.enqueued_at is None

    # 4. The child completes → corrective (parent, child)-keyed emit
    #    (task-id-agnostic) fires the re-armed watcher — REAL bus over
    #    the REAL DB.
    bus = DependencyBus(DependencyWatcherRepository(engine))
    fired = await bus.emit_terminal_for_child_instance(
        parent_instance_id=leader_id,
        child_instance_id=child_id,
        outcome=Outcome(status="completed"),
    )
    assert len(fired) == 1, "the restored wake must fire exactly once"
    assert fired[0].target_instance_id == leader_id
    assert fired[0].metadata["child_id"] == child_id
    row = _watcher_state(engine, watch_id)
    assert row.state == DependencyWatcherState.FIRED.value

    # 5. Exactly-once: a second corrective emit fires nothing.
    fired_again = await bus.emit_terminal_for_child_instance(
        parent_instance_id=leader_id,
        child_instance_id=child_id,
        outcome=Outcome(status="completed"),
    )
    assert fired_again == []



# ─── Kill-switch OFF: byte-identical legacy resume ──────────────────────────


@pytest.mark.asyncio
async def test_kill_switch_off_keeps_watcher_cancelled(
    engine, monkeypatch
):
    monkeypatch.setenv("ENSEMBLE_WATCHER_REARM_ON_RESUME", "0")
    _reset_watcher_rearm_for_tests()

    leader_id = _seed_instance(
        engine,
        instance_id=f"leader-{uuid.uuid4().hex[:8]}",
        parent_id=None,
        status=InstanceStatus.WAITING_CHILDREN.value,
    )
    child_id = _seed_instance(
        engine,
        instance_id=f"giter-{uuid.uuid4().hex[:8]}",
        parent_id=leader_id,
        status=InstanceStatus.RUNNING.value,
    )
    watch_id = _seed_watcher(
        engine,
        source_task_id="30975",
        target_instance_id=leader_id,
        child_id=child_id,
        state=DependencyWatcherState.PENDING.value,
    )

    lifecycle, _manager = _build_lifecycle(engine)

    await lifecycle.pause_instance_cascade(leader_id)
    with Session(engine) as session:
        session.execute(
            sa_update_dependency_watcher_state(watch_id, "CANCELLED")
        )
        session.commit()

    result = await lifecycle.resume_instance_cascade(leader_id)
    assert leader_id in result["resumed_ids"]

    row = _watcher_state(engine, watch_id)
    assert row.state == DependencyWatcherState.CANCELLED.value, (
        "kill-switch OFF = legacy behavior: no re-arm writes on resume"
    )


# ─── Child-terminal guard: terminate-class cancellation is permanent ────────


@pytest.mark.asyncio
async def test_terminal_child_watcher_stays_cancelled(engine):
    leader_id = _seed_instance(
        engine,
        instance_id=f"leader-{uuid.uuid4().hex[:8]}",
        parent_id=None,
        status=InstanceStatus.WAITING_CHILDREN.value,
    )
    child_id = _seed_instance(
        engine,
        instance_id=f"gone-{uuid.uuid4().hex[:8]}",
        parent_id=leader_id,
        status=InstanceStatus.TERMINATED.value,
    )
    watch_id = _seed_watcher(
        engine,
        source_task_id="4096",
        target_instance_id=leader_id,
        child_id=child_id,
        state=DependencyWatcherState.PENDING.value,
    )

    lifecycle, _manager = _build_lifecycle(engine)
    await lifecycle.pause_instance_cascade(leader_id)
    with Session(engine) as session:
        session.execute(
            sa_update_dependency_watcher_state(watch_id, "CANCELLED")
        )
        session.commit()

    await lifecycle.resume_instance_cascade(leader_id)

    row = _watcher_state(engine, watch_id)
    assert row.state == DependencyWatcherState.CANCELLED.value, (
        "a TERMINAL child will never run again — the watcher must "
        "stay cancelled (terminate semantics preserved)"
    )


# ─── Legacy rows: child resolved via source_task_id → task ──────────────────


@pytest.mark.asyncio
async def test_legacy_row_without_payload_child_id_rearms_via_task(engine):
    leader_id = _seed_instance(
        engine,
        instance_id=f"leader-{uuid.uuid4().hex[:8]}",
        parent_id=None,
        status=InstanceStatus.WAITING_CHILDREN.value,
    )
    child_id = _seed_instance(
        engine,
        instance_id=f"giter-{uuid.uuid4().hex[:8]}",
        parent_id=leader_id,
        status=InstanceStatus.RUNNING.value,
    )
    _seed_child_task(
        engine, task_id=4242, instance_id=child_id,
        status=TaskStatus.RUNNING.value,
    )
    watch_id = _seed_watcher(
        engine,
        source_task_id="4242",
        target_instance_id=leader_id,
        child_id=None,  # legacy row — no payload child_id
        state=DependencyWatcherState.PENDING.value,
    )

    lifecycle, _manager = _build_lifecycle(engine)
    await lifecycle.pause_instance_cascade(leader_id)
    with Session(engine) as session:
        session.execute(
            sa_update_dependency_watcher_state(watch_id, "CANCELLED")
        )
        session.commit()

    await lifecycle.resume_instance_cascade(leader_id)

    row = _watcher_state(engine, watch_id)
    assert row.state == DependencyWatcherState.PENDING.value, (
        "legacy row (no payload child_id) resolves the child via the "
        "source_task_id → task → instance fallback"
    )
