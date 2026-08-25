"""Unit tests: resume-cascade Task SELECT JobItem guard (Phase 2, task 2.5).

W1 (Rev 2.1) — cancel-during-pause drift sub-fix. The Task SELECT
inside ``_resume_cascade_db_sync`` is widened with::

    AND NOT EXISTS (
        SELECT 1 FROM job_queue_items
        WHERE job_queue_items.job_id = task.work_id
          AND job_queue_items.deleted_at IS NULL
          AND job_queue_items.job_type <> 'message'
    )

A TASK-OWNED JobItem in ANY non-deleted state means the JobItem lane
owns the resume decision — the blind ``PAUSED → PENDING`` flip must
NOT fire for a task whose JobItem was cancelled during the pause (the
``(JobItem CANCELLED, Task PENDING)`` residue class).

The ``job_type <> 'message'`` qualifier restores the W1 plan-task-text
intent ("a task-owning JobItem"): JAFP says ``job_type='message'``
JobItems are pure mirrors of the Task row (created by
``enqueue_message_job``; the Task lifecycle owns their visibility) and
never re-drive the resume decision. Holding a Task PAUSED because its
mirror exists blocks the P1 c171a289 PAUSED→PENDING semantic the
full-chain test pins (and the c171a289 QUARANTINE.md-documented test
family). The ``deleted_at IS NULL`` filter is load-bearing: a
soft-deleted JobItem does NOT own the resume decision.

Cases (phase2-plan.md task 2.5):
  (a) JobItem=active    (task-type) + Task=PAUSED → Task stays PAUSED
  (b) JobItem=cancelled (task-type) + Task=PAUSED → Task stays PAUSED
  (c) JobItem=queued    (task-type) + Task=PAUSED → Task stays PAUSED
  (d) no JobItem                       + Task=PAUSED → Task flips PENDING
  (e) JobItem soft-deleted (deleted_at set)     → Task flips PENDING

NOTE — message-type JobItem scope: JAFP says message-type mirrors
never re-drive the resume decision (the WorkerPool re-claim drives
both atomically). Cases (a)-(c) therefore seed ``job_type='task'``
lanes — the residue class the W1 fix protects against is a TASK-owned
lane only; message-type mirrors cannot enter that residue shape.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue.models import AdmissionState, JobItem
from daemon.repositories.task.models import Task, TaskStatus
from daemon.repositories.task.repository import TaskRepository
from daemon.services.instance_lifecycle import InstanceLifecycleService
from daemon.write_pause_guard import WritePauseGuard


# ─── Fixtures & helpers ───────────────────────────────────────────────────────


@pytest.fixture
def engine() -> Engine:
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def write_guard() -> WritePauseGuard:
    return WritePauseGuard()


@pytest.fixture
def lifecycle_service(engine, write_guard):
    service = InstanceLifecycleService.__new__(InstanceLifecycleService)
    manager = MagicMock()
    manager.engine = engine
    manager.write_guard = write_guard
    manager._task_repo = TaskRepository(engine=engine)
    service._manager = manager
    return service


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_paused_instance(engine: Engine) -> str:
    iid = f"inst-{uuid.uuid4().hex[:8]}"
    now = _now_iso()
    with Session(engine) as s:
        s.add(
            Instance(
                instance_id=iid,
                agent_id="developer",
                agent_dir="/tmp/agents/developer",
                agent_name="developer",
                project_id="test-project",
                status=InstanceStatus.PAUSED.value,
                created_at=now,
                updated_at=now,
                paused_at=now,
            )
        )
        s.commit()
    return iid


def _seed_paused_task(engine: Engine, instance_id: str) -> tuple[int, str]:
    """Seed a PAUSED Task; return (task_id, work_id)."""
    work_id = f"work-{uuid.uuid4().hex[:12]}"
    with Session(engine) as s:
        task = Task(
            task_type="process_message",
            instance_id=instance_id,
            status=TaskStatus.PAUSED.value,
            work_id=work_id,
            created_at=datetime.now(timezone.utc),
        )
        s.add(task)
        s.commit()
        s.refresh(task)
        return int(task.id), work_id


def _seed_jobitem(
    engine: Engine,
    *,
    instance_id: str,
    work_id: str,
    admission_state: str,
    deleted: bool = False,
    job_type: str = "message",
) -> str:
    """Seed a JobItem mirroring the task's work_id.

    Cases (a)-(c) seed ``job_type='task'`` to genuinely exercise the
    TASK-owned lane residue class (the W1 fix protects). The default
    (``'message'``) preserves backward compatibility for cases that
    exercise the JAFP pure-mirror shape (e.g. case (e)'s soft-delete
    branch, where ``deleted_at IS NULL`` exclusion makes the
    ``job_type`` value immaterial).
    """
    now = _now_iso()
    with Session(engine) as s:
        job = JobItem(
            job_id=work_id,  # JobItem.job_id mirrors the task work_id
            agent_id="developer",
            agent_dir="/tmp/agents/developer",
            message="hello",
            source="api",
            project_id="test-project",
            job_type=job_type,
            admission_state=admission_state,
            instance_id=instance_id,
            created_at=now,
            deleted_at=now if deleted else None,
        )
        s.add(job)
        s.commit()
    return work_id


def _read_task_status(engine: Engine, task_id: int) -> str:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT status FROM task WHERE id = :tid"),
            {"tid": task_id},
        ).scalar_one()


def _run_resume(lifecycle_service, engine, write_guard, iid):
    return lifecycle_service._resume_cascade_db_sync(
        engine,
        write_guard,
        tree_ids=[iid],
        ancestor_ids=set(),
        is_root_resume=True,
    )


# ─── (a) JobItem active + Task PAUSED → stays PAUSED ──────────────────────────


def test_a_jobitem_active_task_stays_paused(lifecycle_service, engine, write_guard):
    """JobItem=active + Task=PAUSED → Task stays PAUSED.

    The JobItem lane is an OWNING lane (task-type; not a JAFP mirror).
    The active JobItem signals the worker is mid-processing — the
    lane owns the resume decision; the blind PAUSED→PENDING flip
    must not fire (otherwise the lane's processing state and the
    Task's pending state diverge).
    """
    iid = _seed_paused_instance(engine)
    task_id, work_id = _seed_paused_task(engine, iid)
    _seed_jobitem(
        engine,
        instance_id=iid,
        work_id=work_id,
        admission_state=AdmissionState.ACTIVE.value,
        job_type="task",
    )

    result = _run_resume(lifecycle_service, engine, write_guard, iid)

    assert _read_task_status(engine, task_id) == TaskStatus.PAUSED.value
    assert result.resumed_task_ids == [], "guarded task must not be resumed"


# ─── (b) JobItem CANCELLED + Task PAUSED → stays PAUSED (the bug) ─────────────


def test_b_jobitem_cancelled_task_stays_paused(lifecycle_service, engine, write_guard):
    """Rev 2.1 W1: the Rev 2 admission-state IN-list was too narrow.

    A CANCELLED JobItem evaded ``IN ('queued','active')`` and the
    blind PAUSED→PENDING flip produced ``(JobItem CANCELLED, Task
    PENDING)`` residue that no lane recovered. ANY non-deleted
    task-owned JobItem now owns the resume decision.

    The test seeds ``job_type='task'`` (TASK-owned lane) because
    message-type JobItems are JAFP pure mirrors that never re-drive
    the resume decision (the W1 residue class only exists for
    task-owned lanes — see module docstring).
    """
    iid = _seed_paused_instance(engine)
    task_id, work_id = _seed_paused_task(engine, iid)
    _seed_jobitem(
        engine,
        instance_id=iid,
        work_id=work_id,
        admission_state="cancelled",  # terminal demand state
        job_type="task",
    )

    result = _run_resume(lifecycle_service, engine, write_guard, iid)

    assert _read_task_status(engine, task_id) == TaskStatus.PAUSED.value
    assert result.resumed_task_ids == []


# ─── (c) JobItem queued + Task PAUSED → stays PAUSED ──────────────────────────


def test_c_jobitem_queued_task_stays_paused(lifecycle_service, engine, write_guard):
    """JobItem=queued + Task=PAUSED → Task stays PAUSED.

    The JobItem lane is OWNING (task-type); a queued JobItem means
    the worker pool has not yet claimed this job but the lane is
    authoritative about when to drive the Task. The blind flip is
    unsafe (the lane might re-drive once the worker pool settles
    the claim cycle).
    """
    iid = _seed_paused_instance(engine)
    task_id, work_id = _seed_paused_task(engine, iid)
    _seed_jobitem(
        engine,
        instance_id=iid,
        work_id=work_id,
        admission_state=AdmissionState.QUEUED.value,
        job_type="task",
    )

    result = _run_resume(lifecycle_service, engine, write_guard, iid)

    assert _read_task_status(engine, task_id) == TaskStatus.PAUSED.value
    assert result.resumed_task_ids == []


# ─── (d) no JobItem → Task flips PENDING ─────────────────────────────────────


def test_d_no_jobitem_task_flips_pending(lifecycle_service, engine, write_guard):
    iid = _seed_paused_instance(engine)
    task_id, work_id = _seed_paused_task(engine, iid)
    # No JobItem row at all.

    result = _run_resume(lifecycle_service, engine, write_guard, iid)

    assert _read_task_status(engine, task_id) == TaskStatus.PENDING.value
    assert task_id in result.resumed_task_ids


# ─── (e) soft-deleted JobItem → Task flips PENDING ───────────────────────────


def test_e_soft_deleted_jobitem_task_flips_pending(
    lifecycle_service, engine, write_guard
):
    """``deleted_at IS NULL`` is load-bearing: a soft-deleted JobItem
    does NOT own the resume decision (soft-delete → recreate is the
    idempotency-key contract — the deleted row is history, not
    authority).

    The soft-deleted row is seeded in a TERMINAL admission state
    (``done``) — the realistic soft-delete shape (rows are soft-deleted
    after reaching terminal; an active-soft-deleted-no-lock row would
    trip the reconciler's turn-mirror invariant, a PRE-EXISTING gap
    in ``reconcile_turn_mirror``'s deleted_at-agnostic invariant query
    — recorded as a P3 discovery, out of P2 scope).
    """
    iid = _seed_paused_instance(engine)
    task_id, work_id = _seed_paused_task(engine, iid)
    _seed_jobitem(
        engine,
        instance_id=iid,
        work_id=work_id,
        admission_state=AdmissionState.DONE.value,
        deleted=True,
    )

    result = _run_resume(lifecycle_service, engine, write_guard, iid)

    assert _read_task_status(engine, task_id) == TaskStatus.PENDING.value
    assert task_id in result.resumed_task_ids
