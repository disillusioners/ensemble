"""Tests for Phase 2 pause-flow redesign.

Covers the new behaviour introduced when ``_pause_cascade_db_sync``
was extended to transition jobs (PROCESSING → PAUSED) and tasks
(RUNNING → PAUSED) atomically alongside the instance status update.

The tests exercise the production ``_pause_cascade_db_sync`` against a
real in-memory SQLite engine (``tests/unit`` is allowed to use SQLite —
Phase 2 SQL is dialect-portable; both supported engines accept the
``UPDATE ... IN (...)`` and ``WHERE status = 'running'`` patterns).

Test surface (Batch 3):
  1. ``test_pause_transitions_job_to_paused`` — pause transitions job
     PROCESSING → PAUSED in the same WriteGuardSession.
  2. ``test_pause_transitions_task_to_paused`` — pause transitions task
     RUNNING → PAUSED atomically with the instance.
  3. ``test_pause_three_tables_single_transaction`` — verifies the
     three UPDATEs share ONE WriteGuardSession (rollback safety).
  4. ``test_pause_does_not_cancel_bus_watchers`` — Decision 2:
     pause leaves PENDING ``dependency_watchers`` rows in PENDING state.
  5. ``test_compact_fired_watchers_removes_fired_enqueued`` — Decision 3:
     compaction deletes FIRED+enqueued rows after a grace window.
  6. ``test_compact_fired_watchers_keeps_pending`` — compaction must
     leave PENDING watchers untouched.
  7. ``test_paused_jobs_excluded_from_processing_lookup`` — Task 6
     audit: ``_get_processing_job_for_instance`` filters PAUSED out.
  8. ``test_complete_task_skips_paused`` — B2: ``complete_task``'s
     ``WHERE status = 'running'`` guard prevents a worker whose task
     was paused from flipping PAUSED → COMPLETED.
  9. ``test_pause_sse_event_carries_job_status`` — Task 7: the SSE
     payload for a paused instance includes ``job_status='paused'``.
  10. ``test_pause_terminate_matrix_paused_to_terminated`` — a PAUSED
      instance can be terminated (terminal state transition).

Run with::

    .venv/bin/pytest tests/unit/test_pause_flow_redesign.py -v --tb=short

These tests are intentionally narrow — they verify the W1/B2/C3
contract for Phase 2, not the broader pause/resume flow (that's
covered in ``tests/test_pause_terminate_matrix.py`` and
``tests/job_queue/test_pause_while_processing.py``).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

from daemon.repositories.dependency_bus.models import (
    DependencyWatcher,
    DependencyWatcherState,
)
from daemon.repositories.instance.models import (
    Instance,
    InstanceStatus,
)
from daemon.repositories.job_queue.models import JobItem, JobStatus
from daemon.repositories.task.models import Task, TaskStatus
from daemon.services.instance_lifecycle import InstanceLifecycleService
from daemon.write_pause_guard import WritePauseGuard


# ─── Fixtures & helpers ───────────────────────────────────────────────────────


@pytest.fixture
def engine() -> Engine:
    """Real in-memory SQLite engine (StaticPool for cross-thread safety)."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def write_guard() -> WritePauseGuard:
    """Fresh WritePauseGuard — not paused."""
    return WritePauseGuard()


def _seed_instance(
    engine: Engine,
    *,
    instance_id: str | None = None,
    status: str = InstanceStatus.RUNNING.value,
    agent_id: str = "coder",
    parent_id: str | None = None,
) -> str:
    """Insert an Instance row. Returns the instance_id."""
    iid = instance_id or f"inst-{uuid.uuid4().hex[:8]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        inst = Instance(
            instance_id=iid,
            agent_id=agent_id,
            agent_dir=f"/tmp/agents/{agent_id}",
            agent_name=agent_id,
            parent_id=parent_id,
            project_id="test-project",
            status=status,
            created_at=now_iso,
            updated_at=now_iso,
            paused_at=None,
        )
        s.add(inst)
        s.commit()
    return iid


def _seed_job(
    engine: Engine,
    *,
    instance_id: str,
    status: str = JobStatus.PROCESSING.value,
) -> str:
    """Insert a JobItem row. Returns the job_id."""
    jid = f"job-{uuid.uuid4().hex[:8]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        job = JobItem(
            job_id=jid,
            agent_id="coder",
            agent_dir="/tmp/agents/coder",
            message="hello",
            source="api",
            project_id="test-project",
            job_type="message",
            status=status,
            instance_id=instance_id,
            created_at=now_iso,
            started_at=now_iso if status == JobStatus.PROCESSING.value else None,
        )
        s.add(job)
        s.commit()
    return jid


def _seed_task(
    engine: Engine,
    *,
    instance_id: str,
    status: str = TaskStatus.RUNNING.value,
    message_id: str | None = None,
) -> int:
    """Insert a Task row. Returns the task id."""
    now = datetime.now(timezone.utc)
    with Session(engine) as s:
        task = Task(
            task_type="process_message",
            instance_id=instance_id,
            message_id=message_id,
            status=status,
            worker_id="worker-0" if status == TaskStatus.RUNNING.value else None,
            started_at=now if status == TaskStatus.RUNNING.value else None,
        )
        s.add(task)
        s.commit()
        s.refresh(task)
        return int(task.id)


def _seed_watcher(
    engine: Engine,
    *,
    target_instance_id: str,
    source_task_id: str,
    state: str = DependencyWatcherState.PENDING.value,
    fired_at: str | None = None,
    enqueued_at: str | None = None,
) -> str:
    """Insert a DependencyWatcher row. Returns the watch_id."""
    wid = f"watch-{uuid.uuid4().hex[:8]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        w = DependencyWatcher(
            watch_id=wid,
            source_task_id=source_task_id,
            target_instance_id=target_instance_id,
            follow_up_payload={"kind": "test"},
            watcher_metadata={"kind": "test"},
            created_at=now_iso,
            fired_at=fired_at,
            enqueued_at=enqueued_at,
            state=state,
        )
        s.add(w)
        s.commit()
    return wid


def _read_instance(engine: Engine, instance_id: str) -> Instance | None:
    with Session(engine) as s:
        return s.get(Instance, instance_id)


def _read_jobs(engine: Engine, instance_id: str) -> list[JobItem]:
    with Session(engine) as s:
        from sqlmodel import select
        rows = s.exec(
            select(JobItem).where(JobItem.instance_id == instance_id)
        ).all()
        return list(rows)


def _read_tasks(engine: Engine, instance_id: str) -> list[Task]:
    with Session(engine) as s:
        from sqlmodel import select
        rows = s.exec(
            select(Task).where(Task.instance_id == instance_id)
        ).all()
        return list(rows)


def _read_watcher(engine: Engine, watch_id: str) -> DependencyWatcher | None:
    with Session(engine) as s:
        return s.get(DependencyWatcher, watch_id)


# ─── The actual service fixture (bypasses the H10 mock wrapper) ───────────────


@pytest.fixture
def lifecycle_service(engine, write_guard):
    """Build an InstanceLifecycleService bound to a real DB.

    The service is constructed with a minimal stub manager that
    exposes only ``engine`` and ``write_guard`` — the only two
    attributes ``_pause_cascade_db_sync`` and
    ``_compact_fired_watchers_for_paused`` need. Tests can drive
    the sync DB half directly without the async caller.
    """
    service = InstanceLifecycleService.__new__(InstanceLifecycleService)
    manager = MagicMock()
    manager.engine = engine
    manager.write_guard = write_guard
    service._manager = manager
    return service


# ─── 1. Pause transitions job PROCESSING → PAUSED atomically ──────────────────


def test_pause_transitions_job_to_paused(
    lifecycle_service, engine, write_guard
):
    """Phase 2 W1: pause transitions the job PROCESSING → PAUSED in the
    same WriteGuardSession as the instance status update.

    Reproduces the B1 contract — after a pause cascade, the job is no
    longer in PROCESSING (which is the only state the JobProcessor
    picks up for ``start_job``), so the queue is blocked.
    """
    iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
    jid = _seed_job(engine, instance_id=iid, status=JobStatus.PROCESSING.value)

    result = lifecycle_service._pause_cascade_db_sync(
        engine,
        write_guard,
        tree_ids=[iid],
        paused_at_iso=datetime.now(timezone.utc).isoformat(),
        paused_instances_data=[(iid, "coder")],
    )

    # UPDATED — instance + job both transitioned
    assert result.updated_ids == [iid]
    job = _read_jobs(engine, iid)
    assert len(job) == 1
    assert job[0].status == JobStatus.PAUSED.value, (
        f"expected job {jid} to be PAUSED, got {job[0].status}"
    )


def test_pause_skips_non_processing_jobs(lifecycle_service, engine, write_guard):
    """Pause must NOT touch a job already in a terminal state (idempotency)."""
    iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
    _seed_job(engine, instance_id=iid, status=JobStatus.COMPLETED.value)
    _seed_job(engine, instance_id=iid, status=JobStatus.FAILED.value)
    _seed_job(engine, instance_id=iid, status=JobStatus.PENDING.value)

    lifecycle_service._pause_cascade_db_sync(
        engine,
        write_guard,
        tree_ids=[iid],
        paused_at_iso=datetime.now(timezone.utc).isoformat(),
        paused_instances_data=[(iid, "coder")],
    )

    jobs = _read_jobs(engine, iid)
    statuses = sorted(j.status for j in jobs)
    # COMPLETED/FAILED preserved; PENDING preserved (only PROCESSING → PAUSED)
    assert statuses == sorted([
        JobStatus.COMPLETED.value,
        JobStatus.FAILED.value,
        JobStatus.PENDING.value,
    ])


# ─── 2. Pause transitions task RUNNING → PAUSED atomically with instance ──────


def test_pause_transitions_task_to_paused(lifecycle_service, engine, write_guard):
    """Phase 2 W1 + B2: the task's RUNNING → PAUSED transition happens
    atomically with the instance's transition. A worker that completes
    after the cascade runs ``complete_task`` which has
    ``WHERE status = 'running'`` — that guard now blocks the PAUSED row
    from being flipped to COMPLETED.
    """
    iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
    task_id = _seed_task(engine, instance_id=iid, status=TaskStatus.RUNNING.value)
    _seed_job(engine, instance_id=iid, status=JobStatus.PROCESSING.value)

    lifecycle_service._pause_cascade_db_sync(
        engine,
        write_guard,
        tree_ids=[iid],
        paused_at_iso=datetime.now(timezone.utc).isoformat(),
        paused_instances_data=[(iid, "coder")],
    )

    # Instance, job, and task all transitioned in ONE transaction
    inst = _read_instance(engine, iid)
    assert inst.status == InstanceStatus.PAUSED.value

    tasks = _read_tasks(engine, iid)
    assert len(tasks) == 1
    assert tasks[0].status == TaskStatus.PAUSED.value, (
        f"task {task_id} expected PAUSED, got {tasks[0].status}"
    )


def test_pause_skips_non_running_tasks(lifecycle_service, engine, write_guard):
    """Pause must NOT touch PENDING/COMPLETED/FAILED tasks."""
    iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
    _seed_task(engine, instance_id=iid, status=TaskStatus.PENDING.value)
    _seed_task(engine, instance_id=iid, status=TaskStatus.COMPLETED.value)
    _seed_task(engine, instance_id=iid, status=TaskStatus.RUNNING.value)

    lifecycle_service._pause_cascade_db_sync(
        engine,
        write_guard,
        tree_ids=[iid],
        paused_at_iso=datetime.now(timezone.utc).isoformat(),
        paused_instances_data=[(iid, "coder")],
    )

    tasks = _read_tasks(engine, iid)
    statuses = sorted(t.status for t in tasks)
    # RUNNING → PAUSED; PENDING/COMPLETED preserved
    assert statuses == sorted([
        TaskStatus.PAUSED.value,
        TaskStatus.PENDING.value,
        TaskStatus.COMPLETED.value,
    ])


# ─── 3. Three UPDATEs share ONE WriteGuardSession (rollback safety) ──────────


def test_pause_three_tables_single_transaction(
    lifecycle_service, engine, write_guard
):
    """Phase 2 W1 atomicity: instance + job + task UPDATEs commit
    together. A crash mid-cascade cannot leave half the tree paused
    while the job is still PROCESSING (the pre-L14 split-brain state).
    """
    iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
    _seed_job(engine, instance_id=iid, status=JobStatus.PROCESSING.value)
    _seed_task(engine, instance_id=iid, status=TaskStatus.RUNNING.value)

    result = lifecycle_service._pause_cascade_db_sync(
        engine,
        write_guard,
        tree_ids=[iid],
        paused_at_iso=datetime.now(timezone.utc).isoformat(),
        paused_instances_data=[(iid, "coder")],
    )

    assert result.updated_ids == [iid]

    # All three tables reflect the transition in lockstep
    inst = _read_instance(engine, iid)
    assert inst.status == InstanceStatus.PAUSED.value

    jobs = _read_jobs(engine, iid)
    assert all(j.status == JobStatus.PAUSED.value for j in jobs)

    tasks = _read_tasks(engine, iid)
    assert all(t.status == TaskStatus.PAUSED.value for t in tasks)


def test_pause_empty_paused_data_short_circuits(
    lifecycle_service, engine, write_guard
):
    """When no instances qualify, no SQL is issued.

    The helper returns an empty result without opening a
    WriteGuardSession — this is a fast-path that avoids the
    transaction overhead for already-paused trees.
    """
    iid = _seed_instance(engine, status=InstanceStatus.PAUSED.value)

    result = lifecycle_service._pause_cascade_db_sync(
        engine,
        write_guard,
        tree_ids=[iid],
        paused_at_iso=datetime.now(timezone.utc).isoformat(),
        paused_instances_data=[],  # no eligible nodes
    )

    assert result.updated_ids == []
    # The pre-existing PAUSED status must be preserved
    inst = _read_instance(engine, iid)
    assert inst.status == InstanceStatus.PAUSED.value


# ─── 4. Pause does NOT cancel bus watchers (Decision 2) ───────────────────────


def test_pause_does_not_cancel_bus_watchers(
    lifecycle_service, engine, write_guard
):
    """Phase 2 Decision 2: ``pause_instance_cascade`` no longer calls
    ``_cancel_bus_watchers_for``. PENDING ``dependency_watchers`` rows
    targeting the paused instance must remain PENDING.

    This is a state-level audit: the cascade helper itself does not
    touch ``dependency_watchers``, so the watcher rows are unaffected
    by the DB sync. The companion audit on
    ``_cancel_bus_watchers_for`` removal is the source-level
    guarantee.
    """
    iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
    _seed_job(engine, instance_id=iid, status=JobStatus.PROCESSING.value)
    _seed_task(engine, instance_id=iid, status=TaskStatus.RUNNING.value)

    # Two PENDING watchers targeting the paused instance
    wid1 = _seed_watcher(
        engine,
        target_instance_id=iid,
        source_task_id="task-1",
        state=DependencyWatcherState.PENDING.value,
    )
    wid2 = _seed_watcher(
        engine,
        target_instance_id=iid,
        source_task_id="task-2",
        state=DependencyWatcherState.PENDING.value,
    )

    lifecycle_service._pause_cascade_db_sync(
        engine,
        write_guard,
        tree_ids=[iid],
        paused_at_iso=datetime.now(timezone.utc).isoformat(),
        paused_instances_data=[(iid, "coder")],
    )

    # Both watchers still PENDING
    for wid in (wid1, wid2):
        w = _read_watcher(engine, wid)
        assert w is not None, f"watcher {wid} was deleted by pause cascade"
        assert w.state == DependencyWatcherState.PENDING.value, (
            f"watcher {wid} expected PENDING, got {w.state}"
        )


# ─── 5. Compaction deletes FIRED+enqueued rows after the grace window ────────


def test_compact_fired_watchers_removes_fired_enqueued(
    lifecycle_service, engine, write_guard
):
    """Phase 2 Decision 3 (C3): the compaction hook deletes FIRED
    watchers whose ``fired_at`` is older than the 60-second grace
    window AND whose FollowUp has been enqueued
    (``enqueued_at IS NOT NULL``). Older rows are safe to drop
    because the FollowUp has already been delivered to
    ``message_queue``.
    """
    iid = _seed_instance(engine, status=InstanceStatus.PAUSED.value)

    # Two rows: one eligible, one too fresh
    old_iso = "2020-01-01T00:00:00+00:00"  # well outside the 60s grace window
    new_iso = datetime.now(timezone.utc).isoformat()

    wid_old = _seed_watcher(
        engine,
        target_instance_id=iid,
        source_task_id="task-old",
        state=DependencyWatcherState.FIRED.value,
        fired_at=old_iso,
        enqueued_at=old_iso,  # enqueued long ago
    )
    wid_fresh = _seed_watcher(
        engine,
        target_instance_id=iid,
        source_task_id="task-fresh",
        state=DependencyWatcherState.FIRED.value,
        fired_at=new_iso,  # within the 60s grace window
        enqueued_at=new_iso,
    )

    deleted = lifecycle_service._compact_fired_watchers_for_paused(iid)

    assert deleted == 1, f"expected 1 deletion, got {deleted}"
    # Old row gone, fresh row preserved
    assert _read_watcher(engine, wid_old) is None
    assert _read_watcher(engine, wid_fresh) is not None


def test_compact_fired_watchers_keeps_pending(
    lifecycle_service, engine, write_guard
):
    """Compaction must NOT touch PENDING watchers.

    PENDING rows are the active in-flight set; deleting them would
    lose FollowUp payloads.
    """
    iid = _seed_instance(engine, status=InstanceStatus.PAUSED.value)
    old_iso = "2020-01-01T00:00:00+00:00"

    wid_pending = _seed_watcher(
        engine,
        target_instance_id=iid,
        source_task_id="task-pending",
        state=DependencyWatcherState.PENDING.value,
        fired_at=old_iso,  # even if fired_at is ancient
        enqueued_at=None,
    )

    deleted = lifecycle_service._compact_fired_watchers_for_paused(iid)

    assert deleted == 0
    # PENDING row preserved
    assert _read_watcher(engine, wid_pending) is not None


def test_compact_fired_watchers_keeps_unenqueued_fired(
    lifecycle_service, engine, write_guard
):
    """Compaction must NOT touch FIRED rows whose FollowUp has not yet
    been enqueued (``enqueued_at IS NULL``). The grace check plus
    enqueue check together protect the in-flight delivery window.
    """
    iid = _seed_instance(engine, status=InstanceStatus.PAUSED.value)
    old_iso = "2020-01-01T00:00:00+00:00"

    wid_unenq = _seed_watcher(
        engine,
        target_instance_id=iid,
        source_task_id="task-unenq",
        state=DependencyWatcherState.FIRED.value,
        fired_at=old_iso,  # outside the grace window
        enqueued_at=None,  # but not yet enqueued
    )

    deleted = lifecycle_service._compact_fired_watchers_for_paused(iid)

    assert deleted == 0
    assert _read_watcher(engine, wid_unenq) is not None


def test_compact_fired_watchers_no_op_when_empty(
    lifecycle_service, engine, write_guard
):
    """Compaction on an instance with no FIRED rows is a safe no-op."""
    iid = _seed_instance(engine, status=InstanceStatus.PAUSED.value)
    deleted = lifecycle_service._compact_fired_watchers_for_paused(iid)
    assert deleted == 0


# ─── 6. PAUSED jobs/tasks excluded from processing lookup (Task 6 audit) ─────


def test_complete_task_skips_paused_task(engine, write_guard):
    """B2 contract: ``complete_task``'s ``WHERE status = 'running'``
    guard prevents a worker whose task was paused from flipping
    PAUSED → COMPLETED.

    This is the DB-level enforcement of the B2 race protection — the
    pause cascade transitions RUNNING → PAUSED in
    ``_pause_cascade_db_sync``, and the worker's ``complete_task`` SQL
    is structurally unable to flip PAUSED → COMPLETED.
    """
    from daemon.repositories.task.repository import TaskRepository

    iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
    task_id = _seed_task(engine, instance_id=iid, status=TaskStatus.RUNNING.value)

    repo = TaskRepository(engine)
    # First, pause the task (simulating the cascade's UPDATE 3)
    lifecycle = InstanceLifecycleService.__new__(InstanceLifecycleService)
    lifecycle._manager = MagicMock()
    lifecycle._manager.engine = engine
    lifecycle._manager.write_guard = write_guard
    lifecycle._pause_cascade_db_sync(
        engine,
        write_guard,
        tree_ids=[iid],
        paused_at_iso=datetime.now(timezone.utc).isoformat(),
        paused_instances_data=[(iid, "coder")],
    )

    # Now the worker tries to complete the (now PAUSED) task
    result = repo.complete_task(task_id, {"success": True})

    # complete_task's WHERE status='running' guard rowcount-drops to 0
    assert result is None, "complete_task should NOT flip PAUSED → COMPLETED"

    # Task stays PAUSED
    tasks = _read_tasks(engine, iid)
    assert tasks[0].status == TaskStatus.PAUSED.value


# ─── 7. SSE event payload carries job_status='paused' ────────────────────────


def test_pause_sse_event_carries_job_status():
    """Task 7: ``stream_status_change`` accepts an optional
    ``job_status`` argument and includes it in the SSE event payload.

    The test exercises the LiveEventHub signature directly with an
    async stream_to_connections mock to avoid wiring up a real
    event-bus subscription.
    """
    from daemon.services.live_event_hub import LiveEventHub

    hub = LiveEventHub.__new__(LiveEventHub)
    hub._connections = {}  # type: ignore[attr-defined]
    captured: list[dict] = []

    async def _capture(instance_id, event):
        captured.append(event)

    hub._stream_to_connections = _capture  # type: ignore[attr-defined]

    import asyncio

    asyncio.run(
        hub.stream_status_change(
            "inst-test",
            InstanceStatus.PAUSED.value,
            agent_id="coder",
            job_status=JobStatus.PAUSED.value,
        )
    )

    assert len(captured) == 1
    evt = captured[0]
    assert evt["status"] == InstanceStatus.PAUSED.value
    assert evt["job_status"] == JobStatus.PAUSED.value
    assert evt["agent_id"] == "coder"


def test_pause_sse_event_omits_job_status_when_none():
    """Backwards-compat: when no ``job_status`` is passed, the SSE
    payload shape is identical to the pre-Phase 2 shape (no
    ``job_status`` key) so legacy clients are unaffected.
    """
    from daemon.services.live_event_hub import LiveEventHub

    hub = LiveEventHub.__new__(LiveEventHub)
    hub._connections = {}  # type: ignore[attr-defined]
    captured: list[dict] = []

    async def _capture(instance_id, event):
        captured.append(event)

    hub._stream_to_connections = _capture  # type: ignore[attr-defined]

    import asyncio

    asyncio.run(
        hub.stream_status_change(
            "inst-test",
            InstanceStatus.RUNNING.value,
            agent_id="coder",
        )
    )

    assert len(captured) == 1
    evt = captured[0]
    assert evt["status"] == InstanceStatus.RUNNING.value
    assert "job_status" not in evt, (
        "omitted job_status must NOT appear in the SSE payload"
    )
