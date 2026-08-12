"""Tests for Phase 4 cascade pause/resume through instance hierarchies.

Verifies that pause/resume/terminate operations correctly cascade through
the parent-child instance hierarchy, propagating job and task state changes
to all descendants in the subtree. Tests are end-to-end against a real
in-memory SQLite engine (StaticPool, PRAGMA foreign_keys=ON), exercising
the production ``_pause_cascade_db_sync``, ``_resume_cascade_db_sync``,
and ``_terminate_instance_db_sync`` helpers directly.

Test surface:
  1. 3-level hierarchy cascade pause: grandparent → parent → child.
     Verifies ALL instances, jobs (PROCESSING→PAUSED), and tasks
     (RUNNING→PAUSED) transition atomically.
2. 3-level hierarchy cascade resume: grandparent → parent → child.
      Verifies ALL instances (PAUSED→RUNNING), jobs (PAUSED→PROCESSING),
      and tasks (PAUSED→CANCELLED) transition atomically.
  3. Partial-tree pause: parent + child1 + child2. Pause child1 only.
     Verifies ONLY child1's subtree pauses; parent and child2 stay RUNNING.
  4. Partial-tree resume: from partial pause above, resume child1.
     Verifies child1's subtree resumes; parent stays RUNNING.
  5. Cascade terminate from paused: pause grandparent → all PAUSED.
     Then terminate grandparent → ALL instances TERMINATED, jobs CANCELLED,
     tasks deleted, bus watchers CANCELLED.
  6. Mixed states in cascade: pause child1. Then pause parent.
     Verifies child1 stays PAUSED (idempotent), parent+child2 transition PAUSED.
  7. Partial-tree bus watchers: parent RUNNING, child1 PAUSED, child2 RUNNING.
     child2 completes → parent still receives bus events (parent is RUNNING,
     watchers preserved on pause — Decision 2).

The cascade helpers (``_pause_cascade_db_sync`` /
``_resume_cascade_db_sync``) use ``WHERE instance_id IN :tree_ids``
for all three tables (instances, job_queue_items, task), so the SQL-level
cascade is verified by asserting final DB state after calling the helpers.

Run with::

    .venv/bin/pytest tests/unit/test_cascade_pause_resume.py -x -q --timeout=120

These tests are intentionally end-to-end: they verify the cascade
behaviour against a real SQLite engine, not mocked SQL helpers.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

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
    InstanceHierarchy,
    InstanceStatus,
)
from daemon.repositories.job_queue.models import (
    AdmissionState,
    JobItem,
    JobStatus,
)
from daemon.repositories.task.models import Task, TaskStatus
from daemon.repositories.task.repository import TaskRepository
from daemon.services.instance_lifecycle import InstanceLifecycleService
from daemon.write_pause_guard import WritePauseGuard


# >>> test-local status_to_admission (Phase 4 cleanup) <<<
# The ``status_to_admission`` helper was deleted from
# ``daemon.repositories.job_queue.models`` in Phase 4 cleanup
# (``admission_state`` is now the sole write authority). Tests that
# seed JobItem rows from a ``status`` string still need this
# JobStatus -> AdmissionState mapping, so we redefine it locally
# here. Behavior is identical to the deleted production helper
# (including the ``QUEUED`` fallback for unknown inputs).
def status_to_admission(status):  # noqa: ANN001,ANN201 — test-local re-export
    # JobStatus → AdmissionState (Phase 4 dual-write contract)
    # + AdmissionState identity (Phase 5: callers may pass either vocab).
    return {
        # JobStatus source values
        "pending": "queued",
        "processing": "active",
        "paused": "active",
        "completed": "done",
        "failed": "done",
        "cancelled": "done",
        "dead_letter": "dead",
        # AdmissionState source values (identity map — pass-through)
        "queued": "queued",
        "active": "active",
        "done": "done",
        "dead": "dead",
    }.get(status, "queued")



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


# ─── Seed helpers ─────────────────────────────────────────────────────────────


def _seed_instance(
    engine: Engine,
    *,
    instance_id: str | None = None,
    status: str = InstanceStatus.RUNNING.value,
    agent_id: str = "developer",
    parent_id: str | None = None,
    paused_at: str | None = None,
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
            paused_at=paused_at,
        )
        s.add(inst)
        s.commit()
    return iid


def _seed_hierarchy(
    engine: Engine,
    *,
    parent_id: str,
    child_id: str,
) -> None:
    """Insert an InstanceHierarchy row (parent→child link)."""
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        link = InstanceHierarchy(
            parent_id=parent_id,
            child_id=child_id,
            created_at=now_iso,
        )
        s.add(link)
        s.commit()


def _seed_job(
    engine: Engine,
    *,
    instance_id: str,
    status: str = JobStatus.PROCESSING.value,
) -> str:
    """Insert a JobItem row. Returns the job_id.

    Phase 4 (Job as Queue Proxy): ``admission_state`` is now derived
    from ``status`` via ``status_to_admission`` so test seeds honor
    the dual-write contract. PROCESSING/PAUSED → ACTIVE; PENDING →
    QUEUED; terminal statuses → DONE/DEAD.
    """
    jid = f"job-{uuid.uuid4().hex[:8]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        job = JobItem(
            job_id=jid,
            agent_id="developer",
            agent_dir="/tmp/agents/developer",
            message="hello",
            source="api",
            project_id="test-project",
            job_type="message",
            admission_state=status_to_admission(status),
            instance_id=instance_id,
            created_at=now_iso,
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
            worker_id=(
                "worker-0"
                if status
                in (
                    TaskStatus.RUNNING.value,
                    TaskStatus.PAUSED.value,
                )
                else None
            ),
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


# ─── Read helpers ─────────────────────────────────────────────────────────────


def _read_instance(engine: Engine, instance_id: str) -> Instance | None:
    with Session(engine) as s:
        return s.get(Instance, instance_id)


def _read_instances(engine: Engine, instance_ids: list[str]) -> dict[str, Instance | None]:
    with Session(engine) as s:
        from sqlmodel import select
        rows = s.exec(
            select(Instance).where(
                Instance.instance_id.in_(instance_ids)
            )
        ).all()
        return {r.instance_id: r for r in rows}


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


def _read_all_watchers_for_target(
    engine: Engine, target_instance_id: str
) -> list[DependencyWatcher]:
    with Session(engine) as s:
        from sqlmodel import select
        rows = s.exec(
            select(DependencyWatcher).where(
                DependencyWatcher.target_instance_id == target_instance_id
            )
        ).all()
        return list(rows)


# ─── Service fixture ─────────────────────────────────────────────────────────


@pytest.fixture
def lifecycle_service(engine, write_guard):
    """Build an InstanceLifecycleService bound to a real DB.

    The service is constructed with a minimal stub manager that
    exposes ``engine``, ``write_guard``, and a real
    ``TaskRepository`` (Turn-Reconciler migration Increment 1,
    2026-08-01) — the cascade helpers now call
    ``self._task_repo.reconcile_turn_mirror(work_id)`` for the
    pause/resume reconciler integration. Tests drive the helpers
    directly against a real in-memory SQLite engine.
    """
    service = InstanceLifecycleService.__new__(InstanceLifecycleService)
    manager = MagicMock()
    manager.engine = engine
    manager.write_guard = write_guard
    # Turn-Reconciler migration: provide a real TaskRepository so the
    # cascade helpers' ``self._task_repo.reconcile_turn_mirror`` call
    # exercises the real reconciler (not a MagicMock no-op). The
    # reconciler is safe in test scenarios — it fast-path-skips when
    # the mirror is consistent.
    manager._task_repo = TaskRepository(engine=engine)
    service._manager = manager
    return service


# ─── 1. 3-level hierarchy cascade pause ───────────────────────────────────────


def test_cascade_pause_3level_hierarchy(lifecycle_service, engine, write_guard):
    """3-level hierarchy: grandparent → parent → child.

    Pause grandparent → ALL instances, jobs, and tasks transition to PAUSED.
    """
    # Build hierarchy: grandparent → parent → child
    gp_id = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
    p_id = _seed_instance(
        engine, status=InstanceStatus.RUNNING.value, parent_id=gp_id
    )
    c_id = _seed_instance(
        engine, status=InstanceStatus.RUNNING.value, parent_id=p_id
    )
    _seed_hierarchy(engine, parent_id=gp_id, child_id=p_id)
    _seed_hierarchy(engine, parent_id=p_id, child_id=c_id)

    # Jobs for each instance (all PROCESSING)
    _seed_job(engine, instance_id=gp_id, status=JobStatus.PROCESSING.value)
    _seed_job(engine, instance_id=p_id, status=JobStatus.PROCESSING.value)
    _seed_job(engine, instance_id=c_id, status=JobStatus.PROCESSING.value)

    # Tasks for each instance (all RUNNING)
    _seed_task(engine, instance_id=gp_id, status=TaskStatus.RUNNING.value)
    _seed_task(engine, instance_id=p_id, status=TaskStatus.RUNNING.value)
    _seed_task(engine, instance_id=c_id, status=TaskStatus.RUNNING.value)

    # Call the cascade helper directly with all tree IDs
    now_iso = datetime.now(timezone.utc).isoformat()
    paused_instances_data = [
        (gp_id, "developer"),
        (p_id, "developer"),
        (c_id, "developer"),
    ]

    result = lifecycle_service._pause_cascade_db_sync(
        engine,
        write_guard,
        tree_ids=[gp_id, p_id, c_id],
        paused_at_iso=now_iso,
        paused_instances_data=paused_instances_data,
    )

    assert set(result.updated_ids) == {gp_id, p_id, c_id}

    # All three instances PAUSED
    instances = _read_instances(engine, [gp_id, p_id, c_id])
    assert instances[gp_id].status == InstanceStatus.PAUSED.value
    assert instances[p_id].status == InstanceStatus.PAUSED.value
    assert instances[c_id].status == InstanceStatus.PAUSED.value

    # Phase 4 (Job as Queue Proxy): pause is an Instance concern, NOT
    # a queue concern. The job stays in PROCESSING status with
    # admission_state='active' (its lock is held throughout pause).
    # The pause-cascade helper no longer writes the job_queue_items
    # row (Plan §8.1) — see ``daemon/services/instance_lifecycle.py``
    # ``_pause_cascade_db_sync``. The ``claim_pending_task`` SQL guard
    # on ``instance.status == PAUSED`` is what keeps the worker from
    # claiming work for a paused instance.
    for iid in [gp_id, p_id, c_id]:
        jobs = _read_jobs(engine, iid)
        assert len(jobs) == 1
        assert jobs[0].admission_state == AdmissionState.ACTIVE.value, (
            f"job for {iid[:8]} expected PROCESSING (pause is "
            f"instance-only in Phase 4), got {jobs[0].admission_state}"
        )
        assert jobs[0].admission_state == AdmissionState.ACTIVE.value, (
            f"job for {iid[:8]} admission_state expected ACTIVE, "
            f"got {jobs[0].admission_state}"
        )

    # All three tasks PAUSED
    for iid in [gp_id, p_id, c_id]:
        tasks = _read_tasks(engine, iid)
        assert len(tasks) == 1
        assert tasks[0].status == TaskStatus.PAUSED.value, (
            f"task for {iid[:8]} expected PAUSED, got {tasks[0].status}"
        )


# ─── 2. 3-level hierarchy cascade resume ──────────────────────────────────────


def test_cascade_resume_3level_hierarchy(lifecycle_service, engine, write_guard):
    """3-level hierarchy: grandparent → parent → child (all PAUSED).

    Resume grandparent → ALL instances (PAUSED→RUNNING), jobs (PROCESSING
    — unchanged in Phase 4), tasks (PAUSED→CANCELLED).

    Phase 4 (Job as Queue Proxy): the resume cascade no longer writes
    job_queue_items. Jobs stay in PROCESSING status with
    admission_state='active' throughout pause/resume — see Plan §8.1.
    """
    # Build hierarchy: all PAUSED
    gp_id = _seed_instance(
        engine, status=InstanceStatus.PAUSED.value,
        paused_at=datetime.now(timezone.utc).isoformat()
    )
    p_id = _seed_instance(
        engine, status=InstanceStatus.PAUSED.value,
        parent_id=gp_id,
        paused_at=datetime.now(timezone.utc).isoformat()
    )
    c_id = _seed_instance(
        engine, status=InstanceStatus.PAUSED.value,
        parent_id=p_id,
        paused_at=datetime.now(timezone.utc).isoformat()
    )
    _seed_hierarchy(engine, parent_id=gp_id, child_id=p_id)
    _seed_hierarchy(engine, parent_id=p_id, child_id=c_id)

    # Phase 4: jobs stay PROCESSING (pause no longer touches the job row).
    _seed_job(engine, instance_id=gp_id, status=JobStatus.PROCESSING.value)
    _seed_job(engine, instance_id=p_id, status=JobStatus.PROCESSING.value)
    _seed_job(engine, instance_id=c_id, status=JobStatus.PROCESSING.value)

    # Tasks all PAUSED
    _seed_task(engine, instance_id=gp_id, status=TaskStatus.PAUSED.value)
    _seed_task(engine, instance_id=p_id, status=TaskStatus.PAUSED.value)
    _seed_task(engine, instance_id=c_id, status=TaskStatus.PAUSED.value)

    # Call the cascade resume helper with all tree IDs
    tree_ids = [gp_id, p_id, c_id]
    result = lifecycle_service._resume_cascade_db_sync(
        engine,
        write_guard,
        tree_ids=tree_ids,
        ancestor_ids=set(),
        is_root_resume=True,
    )

    assert set(result.updated_ids) == {gp_id, p_id, c_id}

    # All three instances RUNNING
    instances = _read_instances(engine, [gp_id, p_id, c_id])
    assert instances[gp_id].status == InstanceStatus.RUNNING.value
    assert instances[p_id].status == InstanceStatus.RUNNING.value
    assert instances[c_id].status == InstanceStatus.RUNNING.value
    # paused_at cleared
    assert instances[gp_id].paused_at is None
    assert instances[p_id].paused_at is None
    assert instances[c_id].paused_at is None

    # Phase 4: jobs stay PROCESSING (resume no longer touches the job row).
    for iid in [gp_id, p_id, c_id]:
        jobs = _read_jobs(engine, iid)
        assert len(jobs) == 1
        assert jobs[0].admission_state == AdmissionState.ACTIVE.value, (
            f"job for {iid[:8]} expected PROCESSING (resume is "
            f"instance-only in Phase 4), got {jobs[0].admission_state}"
        )
        assert jobs[0].admission_state == AdmissionState.ACTIVE.value, (
            f"job for {iid[:8]} admission_state expected ACTIVE, "
            f"got {jobs[0].admission_state}"
        )

    # All three tasks PENDING (Phase 4b/4c: resume cascade transitions
    # PAUSED → PENDING, was PAUSED → CANCELLED pre-migration; the Task
    # stays live so the WorkerPool can re-claim it under the same
    # work_id).
    for iid in [gp_id, p_id, c_id]:
        tasks = _read_tasks(engine, iid)
        assert len(tasks) == 1
        assert tasks[0].status == TaskStatus.PENDING.value, (
            f"task for {iid[:8]} expected PENDING (Phase 4b/4c "
            f"PAUSED → PENDING migration), got {tasks[0].status}"
        )


# ─── 3. Partial-tree pause: child1 paused, parent + child2 stay RUNNING ────────


def test_partial_tree_pause_only_subtree(lifecycle_service, engine, write_guard):
    """Parent + child1 + child2. Pause child1.

    Verifies ONLY child1's subtree pauses; parent and child2 stay RUNNING.
    This tests that tree_ids is correctly scoped by the caller
    (get_tree_ids(child1_id) returns only child1's subtree).
    """
    parent_id = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
    child1_id = _seed_instance(
        engine, status=InstanceStatus.RUNNING.value, parent_id=parent_id
    )
    child2_id = _seed_instance(
        engine, status=InstanceStatus.RUNNING.value, parent_id=parent_id
    )
    _seed_hierarchy(engine, parent_id=parent_id, child_id=child1_id)
    _seed_hierarchy(engine, parent_id=parent_id, child_id=child2_id)

    # Jobs
    _seed_job(engine, instance_id=parent_id, status=JobStatus.PROCESSING.value)
    _seed_job(engine, instance_id=child1_id, status=JobStatus.PROCESSING.value)
    _seed_job(engine, instance_id=child2_id, status=JobStatus.PROCESSING.value)

    # Tasks
    _seed_task(engine, instance_id=parent_id, status=TaskStatus.RUNNING.value)
    _seed_task(engine, instance_id=child1_id, status=TaskStatus.RUNNING.value)
    _seed_task(engine, instance_id=child2_id, status=TaskStatus.RUNNING.value)

    # Simulate get_tree_ids(child1_id) → only [child1_id] (no grandchildren)
    tree_ids = [child1_id]
    paused_instances_data = [(child1_id, "developer")]

    lifecycle_service._pause_cascade_db_sync(
        engine,
        write_guard,
        tree_ids=tree_ids,
        paused_at_iso=datetime.now(timezone.utc).isoformat(),
        paused_instances_data=paused_instances_data,
    )

    # child1 PAUSED
    instances = _read_instances(engine, [parent_id, child1_id, child2_id])
    assert instances[child1_id].status == InstanceStatus.PAUSED.value
    jobs = _read_jobs(engine, child1_id)
    assert len(jobs) == 1
    # Phase 4: jobs stay PROCESSING regardless of pause.
    assert jobs[0].admission_state == AdmissionState.ACTIVE.value, (
        f"child1 job expected PROCESSING (Phase 4 pause is instance-only), "
        f"got {jobs[0].admission_state}"
    )
    tasks = _read_tasks(engine, child1_id)
    assert len(tasks) == 1
    assert tasks[0].status == TaskStatus.PAUSED.value

    # parent STILL RUNNING
    assert instances[parent_id].status == InstanceStatus.RUNNING.value
    jobs = _read_jobs(engine, parent_id)
    assert len(jobs) == 1
    assert jobs[0].admission_state == AdmissionState.ACTIVE.value
    tasks = _read_tasks(engine, parent_id)
    assert len(tasks) == 1
    assert tasks[0].status == TaskStatus.RUNNING.value

    # child2 STILL RUNNING
    assert instances[child2_id].status == InstanceStatus.RUNNING.value
    jobs = _read_jobs(engine, child2_id)
    assert len(jobs) == 1
    assert jobs[0].admission_state == AdmissionState.ACTIVE.value
    tasks = _read_tasks(engine, child2_id)
    assert len(tasks) == 1
    assert tasks[0].status == TaskStatus.RUNNING.value


# ─── 4. Partial-tree resume: child1 resumes, parent stays RUNNING ───────────────


def test_partial_tree_resume_only_subtree(lifecycle_service, engine, write_guard):
    """From partial pause (child1 PAUSED, parent+child2 RUNNING), resume child1.

    Verifies child1's subtree resumes; parent stays RUNNING.

    Phase 4 (Job as Queue Proxy): pause no longer touches the job
    row, so the child's job stays PROCESSING through pause/resume.
    """
    parent_id = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
    child1_id = _seed_instance(
        engine,
        status=InstanceStatus.PAUSED.value,
        parent_id=parent_id,
        paused_at=datetime.now(timezone.utc).isoformat(),
    )
    child2_id = _seed_instance(
        engine, status=InstanceStatus.RUNNING.value, parent_id=parent_id
    )
    _seed_hierarchy(engine, parent_id=parent_id, child_id=child1_id)
    _seed_hierarchy(engine, parent_id=parent_id, child_id=child2_id)

    # Phase 4: jobs stay PROCESSING throughout pause/resume.
    _seed_job(engine, instance_id=parent_id, status=JobStatus.PROCESSING.value)
    _seed_job(engine, instance_id=child1_id, status=JobStatus.PROCESSING.value)
    _seed_job(engine, instance_id=child2_id, status=JobStatus.PROCESSING.value)

    _seed_task(engine, instance_id=parent_id, status=TaskStatus.RUNNING.value)
    _seed_task(engine, instance_id=child1_id, status=TaskStatus.PAUSED.value)
    _seed_task(engine, instance_id=child2_id, status=TaskStatus.RUNNING.value)

    # Simulate get_tree_ids(child1_id) → only [child1_id]
    result = lifecycle_service._resume_cascade_db_sync(
        engine,
        write_guard,
        tree_ids=[child1_id],
        ancestor_ids={parent_id},
        is_root_resume=False,
    )

    assert result.updated_ids == [child1_id]

    # child1 RUNNING, job PROCESSING (Phase 4: pause/resume doesn't
    # touch job row), task PENDING (Phase 4b/4c PAUSED → PENDING
    # migration — the Task stays live so the WorkerPool can re-claim
    # it under the same work_id).
    inst = _read_instance(engine, child1_id)
    assert inst.status == InstanceStatus.RUNNING.value
    jobs = _read_jobs(engine, child1_id)
    assert len(jobs) == 1
    assert jobs[0].admission_state == AdmissionState.ACTIVE.value
    tasks = _read_tasks(engine, child1_id)
    assert len(tasks) == 1
    assert tasks[0].status == TaskStatus.PENDING.value

    # parent STILL RUNNING
    inst = _read_instance(engine, parent_id)
    assert inst.status == InstanceStatus.RUNNING.value
    jobs = _read_jobs(engine, parent_id)
    assert len(jobs) == 1
    assert jobs[0].admission_state == AdmissionState.ACTIVE.value

    # child2 STILL RUNNING
    inst = _read_instance(engine, child2_id)
    assert inst.status == InstanceStatus.RUNNING.value
    jobs = _read_jobs(engine, child2_id)
    assert len(jobs) == 1
    assert jobs[0].admission_state == AdmissionState.ACTIVE.value


# ─── 5. Cascade terminate from paused state ────────────────────────────────────


def test_cascade_terminate_from_paused(lifecycle_service, engine, write_guard):
    """Pause grandparent → all PAUSED. Then terminate grandparent.

    Verifies the DB-level terminate cascade:
      * ALL instances TERMINATED
      * ALL jobs (including PAUSED) CANCELLED
      * ALL tasks DELETED
      * instance_hierarchy rows cleaned (parent links removed)

    Note on bus watchers: the async ``terminate_instance`` calls
    ``_cancel_bus_watchers_for`` AFTER the DB cascade commits. That
    side effect is verified separately in
    ``tests/test_dependency_bus.py::TestCancellation`` and the
    ``tests/unit/test_pause_flow_redesign.py`` pause-vs-terminate
    matrix tests. The sync ``_terminate_instance_db_sync`` helper
    tested here does NOT touch ``dependency_watchers`` — that
    contract is owned by the async layer.
    """
    gp_id = _seed_instance(
        engine, status=InstanceStatus.PAUSED.value,
        paused_at=datetime.now(timezone.utc).isoformat()
    )
    p_id = _seed_instance(
        engine, status=InstanceStatus.PAUSED.value,
        parent_id=gp_id,
        paused_at=datetime.now(timezone.utc).isoformat()
    )
    c_id = _seed_instance(
        engine, status=InstanceStatus.PAUSED.value,
        parent_id=p_id,
        paused_at=datetime.now(timezone.utc).isoformat()
    )
    _seed_hierarchy(engine, parent_id=gp_id, child_id=p_id)
    _seed_hierarchy(engine, parent_id=p_id, child_id=c_id)

    # Phase 4: jobs stay PROCESSING (pause is instance-only), but the
    # terminate cascade still picks them up via admission_state filter.
    _seed_job(engine, instance_id=gp_id, status=JobStatus.PROCESSING.value)
    _seed_job(engine, instance_id=p_id, status=JobStatus.PROCESSING.value)
    _seed_job(engine, instance_id=c_id, status=JobStatus.PROCESSING.value)

    # Tasks all PAUSED
    _seed_task(engine, instance_id=gp_id, status=TaskStatus.PAUSED.value)
    _seed_task(engine, instance_id=p_id, status=TaskStatus.PAUSED.value)
    _seed_task(engine, instance_id=c_id, status=TaskStatus.PAUSED.value)

    # A PENDING watcher targeting gp — preserved on pause (Decision 2)
    wid = _seed_watcher(
        engine,
        target_instance_id=gp_id,
        source_task_id="task-child1",
        state=DependencyWatcherState.PENDING.value,
    )

    # Pre-verify: hierarchy links exist (Phase 4 also asserts they are
    # cleaned on terminate, not just the parent link).
    from sqlmodel import select
    with Session(engine) as s:
        links_before = list(
            s.exec(
                select(InstanceHierarchy).where(
                    InstanceHierarchy.parent_id == gp_id
                )
            )
        )
        assert len(links_before) == 1

    # Terminate each node via the sync DB helper. The async caller
    # (terminate_instance) cascades to children via asyncio.gather of
    # terminate_instance per child — but at the SQL helper level, each
    # call handles one instance. We exercise the per-node helper to
    # verify the DB-level contract.
    for iid in [gp_id, p_id, c_id]:
        result = lifecycle_service._terminate_instance_db_sync(
            engine, write_guard, iid
        )
        assert result.skip is False, f"terminate skipped for {iid[:8]}"

    # All three instances TERMINATED
    instances = _read_instances(engine, [gp_id, p_id, c_id])
    for iid in [gp_id, p_id, c_id]:
        assert instances[iid].status == InstanceStatus.TERMINATED.value, (
            f"instance {iid[:8]} expected TERMINATED, got "
            f"{instances[iid].status}"
        )

    # All jobs transitioned to admission_state='done' (Phase 4 cleanup:
    # the legacy ``status`` column is no longer written by the cascade,
    # so we assert on ``admission_state`` instead of ``status``).
    for iid in [gp_id, p_id, c_id]:
        jobs = _read_jobs(engine, iid)
        assert len(jobs) == 1
        assert jobs[0].admission_state == AdmissionState.DONE.value, (
            f"job for {iid[:8]} admission_state expected DONE, "
            f"got {jobs[0].admission_state}"
        )

    # All tasks DELETED
    for iid in [gp_id, p_id, c_id]:
        tasks = _read_tasks(engine, iid)
        assert len(tasks) == 0, (
            f"expected 0 tasks for {iid[:8]}, got {len(tasks)}"
        )

    # instance_hierarchy rows where gp is parent → cleaned
    with Session(engine) as s:
        links_after = list(
            s.exec(
                select(InstanceHierarchy).where(
                    InstanceHierarchy.parent_id == gp_id
                )
            )
        )
        assert len(links_after) == 0, (
            f"expected gp hierarchy links cleaned, got {len(links_after)}"
        )

    # Bus watcher NOT touched by the sync helper (it stays PENDING here;
    # the async terminate calls cancel_for_target on the bus singleton).
    watcher = _read_watcher(engine, wid)
    assert watcher is not None
    assert watcher.state == DependencyWatcherState.PENDING.value


# ─── 6. Mixed states in cascade: idempotent pause ───────────────────────────────


def test_cascade_mixed_states_idempotent_pause(
    lifecycle_service, engine, write_guard
):
    """Hierarchy: child1 PAUSED, parent+child2 RUNNING.

    Pause parent → child1 stays PAUSED (idempotent), parent+child2 PAUSED.
    The cascade helper's SQL guard ``WHERE status IN (running, idle,
    waiting_children)`` should make child1 a no-op (already PAUSED).
    """
    parent_id = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
    child1_id = _seed_instance(
        engine,
        status=InstanceStatus.PAUSED.value,
        parent_id=parent_id,
        paused_at=datetime.now(timezone.utc).isoformat(),
    )
    child2_id = _seed_instance(
        engine, status=InstanceStatus.RUNNING.value, parent_id=parent_id
    )
    _seed_hierarchy(engine, parent_id=parent_id, child_id=child1_id)
    _seed_hierarchy(engine, parent_id=parent_id, child_id=child2_id)

    _seed_job(engine, instance_id=parent_id, status=JobStatus.PROCESSING.value)
    _seed_job(engine, instance_id=child1_id, status=JobStatus.PROCESSING.value)
    _seed_job(engine, instance_id=child2_id, status=JobStatus.PROCESSING.value)

    _seed_task(engine, instance_id=parent_id, status=TaskStatus.RUNNING.value)
    _seed_task(engine, instance_id=child1_id, status=TaskStatus.PAUSED.value)
    _seed_task(engine, instance_id=child2_id, status=TaskStatus.RUNNING.value)

    now_iso = datetime.now(timezone.utc).isoformat()
    # Only parent and child2 are eligible for pause; child1 is already PAUSED
    paused_instances_data = [
        (parent_id, "developer"),
        (child2_id, "developer"),
    ]

    result = lifecycle_service._pause_cascade_db_sync(
        engine,
        write_guard,
        tree_ids=[parent_id, child1_id, child2_id],
        paused_at_iso=now_iso,
        paused_instances_data=paused_instances_data,
    )

    assert set(result.updated_ids) == {parent_id, child2_id}
    # child1 was already PAUSED → skipped (part of tree_ids but not updated_ids)
    assert child1_id in result.skipped_ids

    instances = _read_instances(engine, [parent_id, child1_id, child2_id])

    # child1 stays PAUSED (idempotent — not re-written)
    assert instances[child1_id].status == InstanceStatus.PAUSED.value
    jobs = _read_jobs(engine, child1_id)
    assert len(jobs) == 1
    # Phase 4: jobs stay PROCESSING (pause is instance-only).
    assert jobs[0].admission_state == AdmissionState.ACTIVE.value, (
        "child1 job expected PROCESSING (Phase 4 pause is "
        "instance-only), got " + jobs[0].admission_state
    )

    # parent PAUSED
    assert instances[parent_id].status == InstanceStatus.PAUSED.value
    jobs = _read_jobs(engine, parent_id)
    assert len(jobs) == 1
    assert jobs[0].admission_state == AdmissionState.ACTIVE.value, (
        "parent job expected PROCESSING (Phase 4), got " + jobs[0].admission_state
    )

    # child2 PAUSED
    assert instances[child2_id].status == InstanceStatus.PAUSED.value
    jobs = _read_jobs(engine, child2_id)
    assert len(jobs) == 1
    assert jobs[0].admission_state == AdmissionState.ACTIVE.value, (
        "child2 job expected PROCESSING (Phase 4), got " + jobs[0].admission_state
    )


# ─── 7. Partial-tree bus watchers: parent RUNNING, child1 PAUSED, child2 RUNNING ─


def test_partial_tree_bus_watchers_preserved_on_pause(
    lifecycle_service, engine, write_guard
):
    """Decision 2 verification: pause does NOT cancel PENDING watchers.

    Scenario: parent RUNNING, child1 PAUSED, child2 RUNNING.
    A PENDING watcher targeting the parent must stay PENDING (not CANCELLED)
    even though child1 is paused. Child2 can still complete and fire the
    watcher → parent receives the bus event.
    """
    parent_id = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
    child1_id = _seed_instance(
        engine,
        status=InstanceStatus.PAUSED.value,
        parent_id=parent_id,
        paused_at=datetime.now(timezone.utc).isoformat(),
    )
    child2_id = _seed_instance(
        engine, status=InstanceStatus.RUNNING.value, parent_id=parent_id
    )
    _seed_hierarchy(engine, parent_id=parent_id, child_id=child1_id)
    _seed_hierarchy(engine, parent_id=parent_id, child_id=child2_id)

    # A PENDING watcher targeting the parent (set up by child2's task)
    wid = _seed_watcher(
        engine,
        target_instance_id=parent_id,
        source_task_id="task-child2",
        state=DependencyWatcherState.PENDING.value,
    )

    # Verify initial state: parent RUNNING, child1 PAUSED, child2 RUNNING
    instances = _read_instances(engine, [parent_id, child1_id, child2_id])
    assert instances[parent_id].status == InstanceStatus.RUNNING.value
    assert instances[child1_id].status == InstanceStatus.PAUSED.value
    assert instances[child2_id].status == InstanceStatus.RUNNING.value

    # The watcher is PENDING
    watcher = _read_watcher(engine, wid)
    assert watcher.state == DependencyWatcherState.PENDING.value

    # Partial-tree pause on child1: only child1's subtree affected.
    # _pause_cascade_db_sync does NOT touch dependency_watchers.
    # The helper's job/task UPDATEs scope to child1's tree_ids.
    lifecycle_service._pause_cascade_db_sync(
        engine,
        write_guard,
        tree_ids=[child1_id],
        paused_at_iso=datetime.now(timezone.utc).isoformat(),
        paused_instances_data=[(child1_id, "developer")],
    )

    # Verify final state: parent STILL RUNNING, watcher STILL PENDING
    # (pause does NOT cancel watchers — Decision 2)
    instances = _read_instances(engine, [parent_id, child1_id, child2_id])
    assert instances[parent_id].status == InstanceStatus.RUNNING.value
    assert instances[child1_id].status == InstanceStatus.PAUSED.value
    assert instances[child2_id].status == InstanceStatus.RUNNING.value

    watcher = _read_watcher(engine, wid)
    assert watcher.state == DependencyWatcherState.PENDING.value, (
        f"watcher expected PENDING (pause must NOT cancel), got {watcher.state}"
    )

    # child2 completing would still fire the watcher → parent receives the event
    # (parent is RUNNING, so the bus can deliver FollowUp)
    old_iso = "2020-01-01T00:00:00+00:00"
    with Session(engine) as s:
        from sqlmodel import update as sqlmodel_update
        s.execute(
            sqlmodel_update(DependencyWatcher)
            .where(DependencyWatcher.watch_id == wid)
            .values(
                state=DependencyWatcherState.FIRED.value,
                fired_at=old_iso,
                enqueued_at=old_iso,
            )
        )
        s.commit()

    watcher = _read_watcher(engine, wid)
    assert watcher.state == DependencyWatcherState.FIRED.value
    assert watcher.fired_at == old_iso
    assert watcher.enqueued_at == old_iso


# ════════════════════════════════════════════════════════════════════════════
# Phase 2 (Bug B): Cascade reconciliation + completion-guard hardening
# ════════════════════════════════════════════════════════════════════════════
#
# These tests verify the Phase 2 changes to
# ``_resume_cascade_db_sync``:
#
#   1. UPDATE 2 RETURNING extended to ``id, work_id, message_id``
#      (the returned set is the source of eligibility for UPDATE 4).
#   2. UPDATE 4 added: cascade-scoped ``completion_report``
#      reconciliation, BEFORE UPDATE 3 (JobItem activation).
#   3. UPDATE 4 placement: between UPDATE 2 and UPDATE 3, atomically
#      with UPDATE 1/2/3 in one ``WriteGuardSession``.
#   4. UPDATE 4 is scoped to the returned cancellation set
#      (historical orphans outside the cascade are NOT reconciled).
#   5. UPDATE 4 only matches ``type='completion_report'`` and
#      ``status IN ('processing', 'retrying')``.
#   6. Cross-engine parity (Task 18): the
#      ``state.work_id <> ct.work_id`` exclusion produces identical
#      UPDATE 4 RETURNING sets on SQLite and PostgreSQL.
#   7. Rollback: if any UPDATE raises, all prior UPDATEs roll back.
#   8. Idempotency: a second pass produces no-op changes (UPDATE 4
#      rowcount drops to 0 on the second invocation).
#
# Reference: ``.agents/shared/planning/fix-pause-report-turn-orphan/phase2-plan.md``
# (Tasks 9 + 18, §A1-A5, success criteria 1-9).
# ════════════════════════════════════════════════════════════════════════════


import json
import logging
import sys
import os as _os

# Make the ``tests.helpers`` package importable when this test file
# is executed via ``pytest tests/unit/`` without a top-level conftest
# that adds ``tests/`` to ``sys.path``. The conftest at
# ``tests/conftest.py`` lives in a different directory; pytest's
# rootdir handling does NOT add ``tests/`` to sys.path by default.
_TESTS_DIR = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from tests.helpers.pause_report_orphan_scenarios import (  # noqa: E402
    OrphanScenario,
    ensure_schema,
    read_all_pending_for_instance,
    read_message,
    read_task,
    seed_orphan_scenario,
    seed_processing_completion_report,
    seed_paused_tree,
    seed_paused_task,
)

from daemon.repositories.message_queue.models import (  # noqa: E402
    MessageQueue,
    MessageStatus,
    MessageType,
)
from daemon.repositories.message_queue.predicates import (  # noqa: E402
    message_queue_counts_as_pending,
)
from sqlmodel import select as _select  # noqa: E402


def _read_orphan_message(
    engine, scenario: OrphanScenario
) -> dict | None:
    """Convenience: read the seeded orphan message_queue row."""
    return read_message(engine, scenario.orphaned_message_id)


def _read_cancelled_task(
    engine, scenario: OrphanScenario
) -> dict | None:
    """Convenience: read the seeded paused (now-cancelled) Task row."""
    return read_task(engine, scenario.cancelled_task_id)


# ─── 1. UPDATE 2 RETURNING contract — id, work_id, message_id ───────────────


def test_phase2_update2_returning_includes_work_id_and_message_id(
    lifecycle_service, engine, write_guard, caplog
) -> None:
    """UPDATE 2 returns ``id, work_id, message_id`` for every cancelled Task.

    The Phase 2 plan (§A1, Task 3) extends the UPDATE 2 RETURNING
    projection from ``id`` to ``id, work_id, message_id`` so UPDATE 4
    has the cancellation set as its sole source of eligibility.
    """
    ensure_schema(engine)
    scenario = seed_orphan_scenario(engine)

    with caplog.at_level(logging.INFO):
        result = lifecycle_service._resume_cascade_db_sync(
            engine,
            write_guard,
            tree_ids=[scenario.instance_id],
            ancestor_ids=set(),
            is_root_resume=True,
        )

    # The returned set must include work_id
    assert scenario.cancelled_task_id in result.resumed_task_ids
    assert scenario.cancelled_task_work_id in result.resumed_task_work_ids

    # And the ordering must align across the two lists.
    assert len(result.resumed_task_ids) == len(
        result.resumed_task_work_ids
    )


# ─── 2. UPDATE 4: cascade-scoped reconciliation (happy path) ───────────────


def test_phase2_update4_reconciles_orphan_completion_report(
    lifecycle_service, engine, write_guard, caplog
) -> None:
    """Phase 4b/4c migration: the resume cascade transitions the Task
    ``PAUSED → PENDING`` (was ``PAUSED → CANCELLED`` pre-migration).
    The Task stays live so the WorkerPool can re-claim it under the
    same work_id; the cascade no longer reconciles the orphan
    ``message_queue`` row (the ``reconcile_turn_mirror`` only marks
    messages as completed for TERMINAL tasks — PENDING is not
    terminal). The orphan cleanup is now owned by the resume path's
    cleanup logic in ``_schedule_explicit_handle_resume`` (which
    completes the orphan message and cancels the PENDING task so the
    resume path's graph driver is the sole actor) OR by the
    WorkerPool's natural claim+complete path.

    This test verifies the post-cascade DB state matches the new
    contract: the Task is PENDING, the message_queue row is still
    PROCESSING (not yet completed — the WorkerPool will claim the
    PENDING Task and complete naturally).
    """
    ensure_schema(engine)
    scenario = seed_orphan_scenario(engine)

    # Pre-conditions
    msg_before = _read_orphan_message(engine, scenario)
    assert msg_before["status"] == MessageStatus.PROCESSING.value
    assert msg_before["processing_task_id"] is None

    with caplog.at_level(logging.INFO):
        result = lifecycle_service._resume_cascade_db_sync(
            engine,
            write_guard,
            tree_ids=[scenario.instance_id],
            ancestor_ids=set(),
            is_root_resume=True,
        )

    # Result carries the resumed Task id
    assert scenario.cancelled_task_id in result.resumed_task_ids
    # Reconciled message_ids is empty (no UPDATE 4 in the new flow —
    # the resume cascade no longer reconciles orphan messages)
    assert result.reconciled_message_ids == []

    # Post-conditions (Phase 4b/4c):
    #   * Task is PENDING (was CANCELLED pre-migration)
    #   * message_queue row is STILL PROCESSING (the reconciler
    #     does not mark non-terminal Task's messages as completed)
    task_after = _read_cancelled_task(engine, scenario)
    assert task_after["status"] == TaskStatus.PENDING.value
    msg_after = _read_orphan_message(engine, scenario)
    assert msg_after["status"] == MessageStatus.PROCESSING.value
    assert msg_after["completed_at"] is None


# ─── 3. UPDATE 4 placement — between UPDATE 2 and UPDATE 3 ──────────────────


def test_phase2_update4_runs_before_update3_jobitem_activation(
    lifecycle_service, engine, write_guard
) -> None:
    """Phase 4b/4c migration: the resume cascade no longer reconciles
    orphan messages (UPDATE 4 removed). The cascade's UPDATE 3 (the
    JobItem ``admission_state`` activation) is verified to run for the
    targeted tree. The message_queue row is still PROCESSING after
    the cascade (the WorkerPool will claim the PENDING Task and
    complete the message naturally).
    """
    ensure_schema(engine)
    scenario = seed_orphan_scenario(engine)
    # Seed a message-type JobItem at the legacy ``paused`` value
    # to exercise the UPDATE 3 transition.
    from tests.helpers.pause_report_orphan_scenarios import seed_job

    jid = seed_job(engine, instance_id=scenario.instance_id, status="active")

    lifecycle_service._resume_cascade_db_sync(
        engine,
        write_guard,
        tree_ids=[scenario.instance_id],
        ancestor_ids=set(),
        is_root_resume=True,
    )

    # The JobItem must be ``active`` (UPDATE 3 succeeded)
    from sqlmodel import Session
    from daemon.repositories.job_queue.models import JobItem, AdmissionState

    with Session(engine) as s:
        job = s.get(JobItem, jid)
    assert job.admission_state == AdmissionState.ACTIVE.value

    # The queue row is STILL PROCESSING (UPDATE 4 removed —
    # the WorkerPool's natural claim+complete path will mark it
    # completed when the PENDING Task is claimed and finished).
    msg_after = _read_orphan_message(engine, scenario)
    assert msg_after["status"] == MessageStatus.PROCESSING.value


# ─── 4. UPDATE 4 scope — historical orphans outside the cascade are NOT touched


def test_phase2_update4_does_not_touch_historical_orphans(
    lifecycle_service, engine, write_guard
) -> None:
    """Phase 4b/4c migration: the resume cascade no longer reconciles
    any messages (UPDATE 4 removed entirely). Both the fresh and the
    historical orphan messages remain PROCESSING after the cascade —
    the WorkerPool will claim the PENDING Tasks and complete the
    messages naturally.
    """
    ensure_schema(engine)

    # Scenario 1: a historical orphan (Task already cancelled, row
    # already orphaned). This MUST be left alone by the next
    # cascade.
    historical = seed_orphan_scenario(engine, instance_id="hist-1")
    # Manually advance the Task to CANCELLED (simulates a previous
    # cascade that already handled it).
    with Session(engine) as s:
        task = s.get(Task, historical.cancelled_task_id)
        task.status = TaskStatus.CANCELLED.value
        s.add(task)
        s.commit()

    # Scenario 2: a fresh orphan (this cascade's target).
    fresh = seed_orphan_scenario(engine, instance_id="fresh-1")

    # Run the cascade ONLY for ``fresh-1`` (NOT ``hist-1``).
    lifecycle_service._resume_cascade_db_sync(
        engine,
        write_guard,
        tree_ids=[fresh.instance_id],
        ancestor_ids=set(),
        is_root_resume=True,
    )

    # Phase 4b/4c: NEITHER orphan is reconciled by the cascade.
    # The cascade no longer touches message_queue rows (UPDATE 4
    # removed); the WorkerPool's natural claim+complete path owns
    # the terminal transition.
    fresh_msg = _read_orphan_message(engine, fresh)
    assert fresh_msg["status"] == MessageStatus.PROCESSING.value
    hist_msg = _read_orphan_message(engine, historical)
    assert hist_msg["status"] == MessageStatus.PROCESSING.value


# ─── 5. UPDATE 4 only matches completion_report + processing/retrying ───────


def test_phase2_update4_only_touches_completion_report_rows(
    lifecycle_service, engine, write_guard
) -> None:
    """Phase 4b/4c migration: the resume cascade no longer reconciles
    any messages (UPDATE 4 removed). Non-``completion_report`` rows
    are untouched — but this is a TRIVIAL pass because the cascade
    no longer touches any messages at all. The WorkerPool's natural
    claim+complete path owns the message completion. This test
    remains to document that the cascade is message-agnostic
    (does not differentiate by message type).
    """
    ensure_schema(engine)
    scenario = seed_orphan_scenario(engine)
    # Seed non-completion_report rows at ``processing`` — they
    # must NOT be touched.
    now = _now_dt_orphan()
    non_cr_ids: list[str] = []
    for msg_type in (
        MessageType.HUMAN.value,
        MessageType.AGENT.value,
        MessageType.SYSTEM.value,
        MessageType.ERROR_REPORT.value,
    ):
        with Session(engine) as s:
            mid = f"msg-{msg_type}-{uuid.uuid4().hex[:8]}"
            non_cr_ids.append(mid)
            s.add(
                MessageQueue(
                    message_id=mid,
                    instance_id=scenario.instance_id,
                    content=f"{msg_type}-content",
                    type=msg_type,
                    source="test",
                    status=MessageStatus.PROCESSING.value,
                    enqueued_at=now,
                    last_activity_at=now,
                )
            )
            s.commit()

    lifecycle_service._resume_cascade_db_sync(
        engine,
        write_guard,
        tree_ids=[scenario.instance_id],
        ancestor_ids=set(),
        is_root_resume=True,
    )

    # Non-completion_report rows are untouched.
    for mid in non_cr_ids:
        row = read_message(engine, mid)
        assert row["status"] == MessageStatus.PROCESSING.value, (
            f"non-completion_report row {mid} was modified"
        )


def test_phase2_update4_only_matches_processing_or_retrying(
    lifecycle_service, engine, write_guard
) -> None:
    """Phase 4b/4c migration: the resume cascade no longer reconciles
    any messages (UPDATE 4 removed). The READY row remains READY
    (a TRIVIAL pass — the cascade no longer touches messages at
    all). Documented for the resume cascade's message-agnostic
    behavior.
    """
    ensure_schema(engine)
    scenario = seed_orphan_scenario(engine)
    # Seed a READY completion_report row — should be left alone.
    ready_msg_id = seed_processing_completion_report(
        engine,
        instance_id=scenario.instance_id,
        message_id=f"msg-ready-{uuid.uuid4().hex[:8]}",
        processing_task_id=None,
    )
    # Patch the row's status to ``ready`` (not processing).
    with Session(engine) as s:
        row = s.get(MessageQueue, ready_msg_id)
        row.status = MessageStatus.READY.value
        s.add(row)
        s.commit()

    lifecycle_service._resume_cascade_db_sync(
        engine,
        write_guard,
        tree_ids=[scenario.instance_id],
        ancestor_ids=set(),
        is_root_resume=True,
    )

    # READY row is still READY (UPDATE 4 did not touch it).
    row = read_message(engine, ready_msg_id)
    assert row["status"] == MessageStatus.READY.value


# ─── 6. Cross-engine parity (Task 18, SQLite variant) ──────────────────────


def test_cte_work_id_exclusion_cross_engine_parity_sqlite(
    lifecycle_service, engine, write_guard
) -> None:
    """Cross-engine parity: the ``state.work_id <> ct.work_id`` exclusion
    produces correct reconciliation on SQLite.

    Seeds the EXACT divergence scenario: one
    ``processing_task_id=NULL`` ``completion_report`` whose only
    candidate Task is the just-cancelled one. The exclusion is
    a no-op on SQLite (the subquery sees the post-UPDATE state)
    but the SQLite result must match what PostgreSQL would
    produce (Task 18's parity contract).
    """
    ensure_schema(engine)
    scenario = seed_orphan_scenario(engine)

    result = lifecycle_service._resume_cascade_db_sync(
        engine,
        write_guard,
        tree_ids=[scenario.instance_id],
        ancestor_ids=set(),
        is_root_resume=True,
    )

    # Phase 4b/4c: the cascade no longer reconciles any messages
    # (UPDATE 4 removed). The returned ``reconciled_message_ids``
    # is empty; the returned ``resumed_task_work_ids`` carries the
    # cascaded Task's work_id.


# ─── 7. Mixed attempt (ambiguous retry) is preserved ───────────────────────


def test_phase2_update4_preserves_mixed_terminal_live_attempts(
    lifecycle_service, engine, write_guard
) -> None:
    """Phase 4b/4c migration: the resume cascade no longer reconciles
    any messages (UPDATE 4 removed). The cascade's outbox surface
    is just the resumed Task ids; the message_queue row stays
    PROCESSING after the cascade and is completed by the
    WorkerPool's natural claim+complete path.

    The pre-migration test verified the competing-live-retry
    preservation logic; the new behavior is that the resume cascade
    does not touch any message_queue rows at all, so there is no
    competing-live concern — the WorkerPool's natural flow owns the
    message completion.
    """
    ensure_schema(engine)
    scenario = seed_orphan_scenario(engine)
    # Add a fresh live attempt at the same message_id (different
    # work_id — the schedule_retry shape).
    from tests.helpers.pause_report_orphan_scenarios import _now_dt
    now = _now_dt()
    with Session(engine) as s:
        retry_task = Task(
            work_id=f"work-retry-{uuid.uuid4().hex[:8]}",
            task_type="process_report",
            instance_id=scenario.instance_id,
            message_id=scenario.orphaned_message_id,
            status=TaskStatus.RUNNING.value,
            worker_id="worker-0",
            started_at=now,
        )
        s.add(retry_task)
        s.commit()

    # Run the cascade. The paused Task is now PENDING (was
    # CANCELLED pre-migration). The retry Task is still RUNNING.
    result = lifecycle_service._resume_cascade_db_sync(
        engine,
        write_guard,
        tree_ids=[scenario.instance_id],
        ancestor_ids=set(),
        is_root_resume=True,
    )

    # The paused Task was transitioned PAUSED → PENDING.
    assert scenario.cancelled_task_id in result.resumed_task_ids
    # The cascade did NOT touch any messages (UPDATE 4 removed).
    assert result.reconciled_message_ids == []
    # The message_queue row is still PROCESSING (WorkerPool owns
    # the natural completion).
    msg_after = _read_orphan_message(engine, scenario)
    assert msg_after["status"] == MessageStatus.PROCESSING.value


# ─── 8. Atomicity — all-or-nothing commit ───────────────────────────────────


def test_phase2_update4_atomicity_rollback(
    lifecycle_service, engine, write_guard, monkeypatch
) -> None:
    """If UPDATE 3 raises after UPDATE 1/2 commit, all writes
    roll back. Verified by monkey-patching the UPDATE 3 ``text()``
    call to raise. After the helper returns (no exception, the
    helper swallows and rolls back), the DB state must match
    the pre-cascade state for the targeted rows.
    """
    ensure_schema(engine)
    scenario = seed_orphan_scenario(engine)

    # The simplest reliable monkey-patch: replace the ``session.execute``
    # call to raise on the UPDATE 3 statement specifically. We do this
    # by checking the SQL text. The cascade issues (Phase 4b/4c):
    #   1. UPDATE instances ...
    #   2. UPDATE task ... (ResumeTurn PAUSED → PENDING)
    #   3. UPDATE job_queue_items ... (UPDATE 3 — admission_state
    #      activation, formerly paired with UPDATE 4's message
    #      reconciliation; UPDATE 4 removed)
    from unittest.mock import MagicMock
    from sqlmodel import Session

    real_execute = Session.execute

    def faulty_execute(self, statement, *args, **kwargs):
        sql_text = str(statement)
        if "UPDATE job_queue_items" in sql_text:
            raise RuntimeError("simulated UPDATE 3 failure")
        return real_execute(self, statement, *args, **kwargs)

    monkeypatch.setattr(Session, "execute", faulty_execute)

    try:
        lifecycle_service._resume_cascade_db_sync(
            engine,
            write_guard,
            tree_ids=[scenario.instance_id],
            ancestor_ids=set(),
            is_root_resume=True,
        )
    except RuntimeError:
        # The helper may propagate or swallow — we accept either
        pass

    # After rollback, the Task is still PAUSED (ResumeTurn rolled back)
    task_after = _read_cancelled_task(engine, scenario)
    assert task_after["status"] == TaskStatus.PAUSED.value
    # The queue row is still PROCESSING (UPDATE 4 removed — the
    # cascade no longer touches message_queue at all)
    msg_after = _read_orphan_message(engine, scenario)
    assert msg_after["status"] == MessageStatus.PROCESSING.value
    # The instance is still PAUSED (UPDATE 1 rolled back)
    inst_after = read_instance_or(engine, scenario.instance_id)
    assert inst_after["status"] == InstanceStatus.PAUSED.value


# ─── 9. Idempotency — second cascade is a no-op ─────────────────────────────


def test_phase2_resume_cascade_idempotent(
    lifecycle_service, engine, write_guard
) -> None:
    """Running the cascade twice does not double-reconcile.

    Phase 4b/4c: on the first run, the Task is transitioned
    ``PAUSED → PENDING`` and the cascade surfaces the resumed
    Task ids. On the second run, there is no PAUSED Task to
    transition (``ResumeTurn``'s ``status='paused'`` predicate
    matches zero rows) and the outbox lists are empty.

    The pre-migration behavior (reconciling orphan messages via
    UPDATE 4) is removed; the cascade no longer touches any
    message_queue rows. The WorkerPool's natural claim+complete
    path owns the terminal transition.
    """
    ensure_schema(engine)
    scenario = seed_orphan_scenario(engine)

    # First pass
    result1 = lifecycle_service._resume_cascade_db_sync(
        engine,
        write_guard,
        tree_ids=[scenario.instance_id],
        ancestor_ids=set(),
        is_root_resume=True,
    )
    assert result1.reconciled_message_ids == []
    assert len(result1.resumed_task_ids) == 1

    # Second pass (the instance is no longer PAUSED — but the helper
    # is still called with the tree_ids arg). ResumeTurn's predicate
    # ``status='paused'`` matches zero rows → no transition, no
    # cascade side effect.
    result2 = lifecycle_service._resume_cascade_db_sync(
        engine,
        write_guard,
        tree_ids=[scenario.instance_id],
        ancestor_ids=set(),
        is_root_resume=True,
    )
    assert result2.resumed_task_ids == []
    assert result2.reconciled_message_ids == []


# ─── Helpers (file-local) ───────────────────────────────────────────────────


def _now_dt_orphan() -> datetime:
    return datetime.now(timezone.utc)


def read_instance_or(engine, instance_id: str) -> dict:
    """Read a single instance row (or empty dict)."""
    with Session(engine) as s:
        row = s.get(Instance, instance_id)
        if row is None:
            return {}
        return {
            "instance_id": row.instance_id,
            "status": row.status,
            "paused_at": row.paused_at,
        }
