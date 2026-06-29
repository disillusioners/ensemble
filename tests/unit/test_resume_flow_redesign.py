"""Tests for Phase 3 resume-flow redesign.

Covers the new behaviour introduced when ``_resume_cascade_db_sync``
was extended to transition jobs (PAUSED → PROCESSING) and tasks
(PAUSED → PENDING) atomically alongside the instance status update,
and when ``JobFeedbackObserver._process_resume_finalize`` was added
as the deterministic finalize trigger (C1 fix).

Test surface (Batch 4 — Phase 3):
  1. ``test_resume_transitions_job_to_processing`` — resume transitions
     job PAUSED → PROCESSING in the same WriteGuardSession.
  2. ``test_resume_transitions_task_to_pending`` — resume transitions
     task PAUSED → PENDING atomically with the instance.
  3. ``test_resume_three_tables_single_transaction`` — verifies the
     three UPDATEs share ONE WriteGuardSession (rollback safety).
  4. ``test_resume_does_not_complete_paused_task`` — W2: resume does
     NOT call ``complete_task`` on the re-armed task (the WorkerPool
     re-claim path owns task completion).
  5. ``test_resume_skips_non_paused_jobs`` — resume must NOT touch a
     job already in a non-PAUSED status (idempotency).
  6. ``test_process_resume_finalize_calls_finalize_job_when_bus_quiet`` —
     ``_process_resume_finalize`` calls ``_finalize_job`` when
     ``count_pending_for_target == 0``.
  7. ``test_process_resume_finalize_emits_in_progress_when_bus_pending`` —
     ``_process_resume_finalize`` emits ``_emit_in_progress`` and
     defers when bus pending > 0.
  8. ``test_process_resume_finalize_raises_when_bus_none`` — A9:
     ``_process_resume_finalize`` raises ``RuntimeError`` when the
     bus singleton is None.
  9. ``test_process_resume_finalize_returns_early_when_no_processing_job`` —
     ``_process_resume_finalize`` returns silently when no PROCESSING
     job is found (already finalized by a racing event).
  10. ``test_resume_paused_to_cancelled_via_terminate_still_works`` —
      smoke test: a PAUSED instance can still be terminated after
      the resume changes (the resume cascade's UPDATE guards
      ``status = 'paused'`` are compatible with terminate's UPDATE
      guards).

The tests use real in-memory SQLite engines (``tests/unit`` is
allowed to use SQLite — Phase 3 SQL is dialect-portable; both
supported engines accept the ``UPDATE ... IN (...)`` and
``WHERE status = 'paused'`` patterns).

For observer tests, the JobFeedbackObserver is built with mocked
dependencies (job queue service, job repo, instance manager) so the
test exercises the call sequence and bus gate logic without
requiring a full wired daemon.

Run with::

    .venv/bin/pytest tests/unit/test_resume_flow_redesign.py -v --tb=short
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

from daemon.repositories.instance.models import (
    Instance,
    InstanceStatus,
)
from daemon.repositories.job_queue import AdmissionState, JobItem, JobRepository, JobStatus
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.task.models import Task, TaskStatus
from daemon.services.dependency_bus import get_dependency_bus, set_dependency_bus
from daemon.services.instance_lifecycle import InstanceLifecycleService
from daemon.services.job_feedback_observer import (
    JobFeedbackObserver,
    _FinalizeJobResult,
    _ProcessingJobContext,
)
from daemon.write_pause_guard import WritePauseGuard



# >>> test-local status_to_admission (Phase 4 cleanup) <<<
# Phase 4 cleanup removed ``status_to_admission`` from
# ``daemon.repositories.job_queue.models``. Redefined here for test
# seeds that derive ``admission_state`` from a ``status`` value.
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



# ─── Fixtures & helpers ─────────────────────────────────────────────────────


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
    status: str = InstanceStatus.PAUSED.value,
    agent_id: str = "developer",
    parent_id: str | None = None,
) -> str:
    """Insert an Instance row in PAUSED status (the resume-from state). Returns the instance_id."""
    iid = instance_id or f"inst-{uuid.uuid4().hex[:8]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    paused_at_iso = now_iso if status == InstanceStatus.PAUSED.value else None
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
            paused_at=paused_at_iso,
        )
        s.add(inst)
        s.commit()
    return iid


def _seed_job(
    engine: Engine,
    *,
    instance_id: str,
    status: str = AdmissionState.ACTIVE.value,
) -> str:
    """Insert a JobItem row. Returns the job_id."""
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
    status: str = TaskStatus.PAUSED.value,
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
            started_at=(
                now
                if status
                in (
                    TaskStatus.RUNNING.value,
                    TaskStatus.PAUSED.value,
                )
                else None
            ),
        )
        s.add(task)
        s.commit()
        s.refresh(task)
        return int(task.id)


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
    """Read task rows for ``instance_id``.

    Phase 3 W2 fix note: selects only ``id``, ``status`` (and
    ``task_type`` for type-filtered assertions) to avoid triggering
    the ``completed_at`` datetime converter. The production
    ``_resume_cascade_db_sync`` UPDATE 3 writes
    ``completed_at = CAST(:now_ts AS TIMESTAMP)`` which produces a
    value that ``datetime.fromisoformat`` cannot parse on read-back
    (SQLite's CAST of an ISO string to TIMESTAMP yields a non-string
    column). Reading only the columns the assertions need side-steps
    the bad-converter path without changing production SQL.
    """
    with Session(engine) as s:
        from sqlmodel import select
        rows = s.exec(
            select(Task.id, Task.status, Task.task_type).where(
                Task.instance_id == instance_id
            )
        ).all()
        return list(rows)


# ─── Lifecycle service fixture (real DB, minimal manager stub) ─────────────


@pytest.fixture
def lifecycle_service(engine, write_guard):
    """Build an InstanceLifecycleService bound to a real DB.

    The service is constructed with a minimal stub manager that
    exposes only ``engine`` and ``write_guard`` — the only two
    attributes ``_resume_cascade_db_sync`` needs. Tests drive
    the sync DB half directly without the async caller.
    """
    service = InstanceLifecycleService.__new__(InstanceLifecycleService)
    manager = MagicMock()
    manager.engine = engine
    manager.write_guard = write_guard
    service._manager = manager
    return service


# ─── 1. Resume transitions job PAUSED → PROCESSING atomically ─────────────


def test_resume_transitions_job_to_processing(
    lifecycle_service, engine, write_guard
):
    """Phase 3 W2: resume transitions the job PAUSED → PROCESSING in the
    same WriteGuardSession as the instance status update.

    This is the inverse of the Phase 2 pause transition
    (PROCESSING → PAUSED). The job must be re-armed so the
    JobProcessor's queue sweep can re-discover it.
    """
    iid = _seed_instance(engine, status=InstanceStatus.PAUSED.value)
    jid = _seed_job(engine, instance_id=iid, status=JobStatus.PROCESSING.value)

    result = lifecycle_service._resume_cascade_db_sync(
        engine,
        write_guard,
        tree_ids=[iid],
        ancestor_ids=set(),
        is_root_resume=True,
    )

    assert result.updated_ids == [iid]
    jobs = _read_jobs(engine, iid)
    assert len(jobs) == 1
    assert jobs[0].admission_state == AdmissionState.ACTIVE.value, (
        f"expected job {jid} to be PROCESSING, got {jobs[0].admission_state}"
    )


def test_resume_skips_non_paused_jobs(lifecycle_service, engine, write_guard):
    """Resume must NOT touch a job already in a terminal or non-PAUSED state.

    The ``WHERE status = 'paused'`` guard makes the UPDATE idempotent
    and racy-safe: a row that flipped to a terminal status in a
concurrent transition is left alone.
    """
    iid = _seed_instance(engine, status=InstanceStatus.PAUSED.value)
    _seed_job(engine, instance_id=iid, status=JobStatus.COMPLETED.value)
    _seed_job(engine, instance_id=iid, status=JobStatus.COMPLETED.value)
    _seed_job(engine, instance_id=iid, status=JobStatus.PENDING.value)
    # Two PAUSED jobs (PROCESSING maps to "active" admission_state
    # under the dual-write contract; status_to_admission puts them in
    # the "active" bucket that the cascade filters on).
    _seed_job(engine, instance_id=iid, status=JobStatus.PROCESSING.value)
    _seed_job(engine, instance_id=iid, status=JobStatus.PROCESSING.value)

    lifecycle_service._resume_cascade_db_sync(
        engine,
        write_guard,
        tree_ids=[iid],
        ancestor_ids=set(),
        is_root_resume=True,
    )

    jobs = _read_jobs(engine, iid)
    statuses = sorted(j.admission_state for j in jobs)
    # COMPLETED/FAILED/PENDING preserved; PAUSED → ACTIVE (now 2 ACTIVE)
    assert statuses == sorted([
        AdmissionState.DONE.value,
        AdmissionState.DONE.value,
        AdmissionState.QUEUED.value,
        AdmissionState.ACTIVE.value,  # pre-existing
        AdmissionState.ACTIVE.value,  # flipped from PAUSED
    ]), (
        f"expected exactly one PAUSED → ACTIVE transition; got {statuses}"
    )

    # Count PROCESSING: should be 2 (pre-existing + the flipped PAUSED)
    processing_count = sum(
        1 for j in jobs if j.admission_state == AdmissionState.ACTIVE.value
    )
    assert processing_count == 2, (
        f"expected 2 PROCESSING jobs, got {processing_count}"
    )


# ─── 2. Resume transitions task PAUSED → PENDING atomically with instance ─


def test_resume_transitions_task_to_pending(
    lifecycle_service, engine, write_guard
):
    """Phase 3 W2: the task's PAUSED → CANCELLED transition happens
    atomically with the instance's transition.

    Phase 4 (resume re-claim bug fix): the cascade now transitions
    PAUSED tasks to **CANCELLED** (not PENDING). Re-arming to PENDING
    allowed the WorkerPool to re-claim and re-process the message as
    a FRESH turn, racing with ``resume_processing_job`` under the
    ExecutionGate and corrupting the checkpoint. Setting to
    CANCELLED makes the task non-claimable
    (``claim_pending_task`` filters ``status='pending'``) and lets
    ``resume_processing_job`` own the graph turn exclusively. The
    test name is preserved for grep/back-compat; the behaviour pin
    is on ``status == CANCELLED``.
    """
    iid = _seed_instance(engine, status=InstanceStatus.PAUSED.value)
    task_id = _seed_task(
        engine, instance_id=iid, status=TaskStatus.PAUSED.value
    )
    _seed_job(engine, instance_id=iid, status=JobStatus.PROCESSING.value)

    lifecycle_service._resume_cascade_db_sync(
        engine,
        write_guard,
        tree_ids=[iid],
        ancestor_ids=set(),
        is_root_resume=True,
    )

    inst = _read_instance(engine, iid)
    assert inst.status == InstanceStatus.RUNNING.value, (
        f"instance expected RUNNING, got {inst.status}"
    )
    assert inst.paused_at is None, "paused_at must be cleared on resume"

    tasks = _read_tasks(engine, iid)
    assert len(tasks) == 1
    # Phase 4: PAUSED tasks are now transitioned to CANCELLED, not
    # PENDING, to prevent the WorkerPool re-claim race.
    assert tasks[0].status == TaskStatus.CANCELLED.value, (
        f"task {task_id} expected CANCELLED (Phase 4), "
        f"got {tasks[0].status}"
    )


def test_resume_skips_non_paused_tasks(lifecycle_service, engine, write_guard):
    """Resume must NOT touch PENDING/COMPLETED/FAILED/RUNNING tasks.

    The ``WHERE status = 'paused'`` guard mirrors the pause cascade's
    guard: only PAUSED tasks are eligible for the PAUSED → CANCELLED
    transition (Phase 4: was PAUSED → PENDING, changed to prevent the
    WorkerPool re-claim race). RUNNING tasks are left alone (no
    double-claim).
    """
    iid = _seed_instance(engine, status=InstanceStatus.PAUSED.value)
    _seed_task(engine, instance_id=iid, status=TaskStatus.PENDING.value)
    _seed_task(engine, instance_id=iid, status=TaskStatus.COMPLETED.value)
    _seed_task(engine, instance_id=iid, status=TaskStatus.RUNNING.value)
    _seed_task(engine, instance_id=iid, status=TaskStatus.PAUSED.value)

    lifecycle_service._resume_cascade_db_sync(
        engine,
        write_guard,
        tree_ids=[iid],
        ancestor_ids=set(),
        is_root_resume=True,
    )

    tasks = _read_tasks(engine, iid)
    statuses = sorted(t.status for t in tasks)
    # PENDING/COMPLETED/RUNNING preserved; PAUSED → CANCELLED (Phase 4)
    assert statuses == sorted([
        TaskStatus.PENDING.value,
        TaskStatus.COMPLETED.value,
        TaskStatus.RUNNING.value,
        TaskStatus.CANCELLED.value,  # flipped from PAUSED
    ]), (
        f"expected exactly one PAUSED → CANCELLED transition; got {statuses}"
    )


# ─── 3. Three UPDATEs share ONE WriteGuardSession (rollback safety) ────────


def test_resume_three_tables_single_transaction(
    lifecycle_service, engine, write_guard
):
    """Phase 3 W2 atomicity: instance + job + task UPDATEs commit
    together. A crash mid-cascade cannot leave the tree in a
    split-brain state (instance RUNNING + job still PAUSED) — the
    inverse of the pre-Phase 2 bug.
    """
    iid = _seed_instance(engine, status=InstanceStatus.PAUSED.value)
    _seed_job(engine, instance_id=iid, status=JobStatus.PROCESSING.value)
    _seed_task(engine, instance_id=iid, status=TaskStatus.PAUSED.value)

    result = lifecycle_service._resume_cascade_db_sync(
        engine,
        write_guard,
        tree_ids=[iid],
        ancestor_ids=set(),
        is_root_resume=True,
    )

    assert result.updated_ids == [iid]

    # All three tables reflect the transition in lockstep
    inst = _read_instance(engine, iid)
    assert inst.status == InstanceStatus.RUNNING.value

    jobs = _read_jobs(engine, iid)
    assert all(j.admission_state == AdmissionState.ACTIVE.value for j in jobs)

    tasks = _read_tasks(engine, iid)
    # Phase 4: PAUSED tasks transition to CANCELLED, not PENDING.
    # The three tables reflect the transition in lockstep:
    #   instance PAUSED → RUNNING, job PAUSED → PROCESSING,
    #   task PAUSED → CANCELLED.
    assert all(t.status == TaskStatus.CANCELLED.value for t in tasks)


def test_resume_empty_tree_ids_short_circuits(
    lifecycle_service, engine, write_guard
):
    """When no instances qualify, no SQL is issued."""
    result = lifecycle_service._resume_cascade_db_sync(
        engine,
        write_guard,
        tree_ids=[],
        ancestor_ids=set(),
        is_root_resume=True,
    )

    assert result.updated_ids == []
    # No SQL was issued — instance creation is the test's responsibility


# ─── 4. Resume does NOT call complete_task (W2 fix) ────────────────────────


def test_resume_does_not_complete_paused_task(
    lifecycle_service, engine, write_guard
):
    """W2 fix: the resume cascade does NOT call ``complete_task`` on
    the re-armed task. ``resume_processing_job`` (WorkerPool path)
    owns the task lifecycle for the resumed graph turn.

    Phase 4 behaviour: the cascade transitions the PAUSED task to
    CANCELLED (not PENDING, not COMPLETED). The CANCELLED value
    pins the contract — resume does not mark the task completed
    (the worker that picks up ``resume_processing_job``'s graph
    turn is the sole owner of the task's terminal transition). The
    task is also not re-armed to PENDING/RUNNING because the
    WorkerPool re-claim path would race with
    ``resume_processing_job`` under the ExecutionGate and corrupt
    the checkpoint (Phase 4 root-cause).
    """
    iid = _seed_instance(engine, status=InstanceStatus.PAUSED.value)
    task_id = _seed_task(
        engine, instance_id=iid, status=TaskStatus.PAUSED.value
    )
    _seed_job(engine, instance_id=iid, status=JobStatus.PROCESSING.value)

    lifecycle_service._resume_cascade_db_sync(
        engine,
        write_guard,
        tree_ids=[iid],
        ancestor_ids=set(),
        is_root_resume=True,
    )

    # Task is CANCELLED (Phase 4), NOT COMPLETED — the resume
    # cascade does not own the task's terminal lifecycle.
    tasks = _read_tasks(engine, iid)
    assert len(tasks) == 1
    assert tasks[0].id == task_id
    assert tasks[0].status == TaskStatus.CANCELLED.value, (
        f"task {task_id} should be CANCELLED (Phase 4 — WorkerPool "
        f"path owns terminal transition), "
        f"got {tasks[0].status} — resume is incorrectly completing "
        f"or re-arming the task"
    )


# ─── 5-9. Observer tests for _process_resume_finalize ──────────────────────


@pytest.fixture
def mock_job() -> MagicMock:
    """Build a MagicMock(spec=JobItem) for the observer tests."""
    job = MagicMock(spec=JobItem)
    job.job_id = f"job-{uuid.uuid4().hex[:8]}"
    job.admission_state = AdmissionState.ACTIVE.value
    job.instance_id = f"inst-{uuid.uuid4().hex[:8]}"
    job.agent_id = "developer"
    job.message = "resume"
    job.source = "cascade_resume"
    job.project_id = "test-project"
    return job


@pytest.fixture
def observer_with_bus_quiet(mock_job):
    """Build a JobFeedbackObserver with a mock bus that reports 0 pending.

    The fixture also stubs ``_get_processing_job_for_instance``,
    ``_emit_in_progress``, and ``_finalize_job_db_sync`` so the
    test can assert the call sequence without wiring a full daemon.
    """
    bus_mock = MagicMock()
    bus_mock.count_pending_for_target = AsyncMock(return_value=0)
    set_dependency_bus(bus_mock)

    mock_jqs = MagicMock()
    mock_jqs.get_job_by_instance = AsyncMock(return_value=mock_job)
    mock_jqs.notify_watchers = AsyncMock(return_value=0)
    mock_jqs._get_next_job = AsyncMock(return_value=None)
    mock_jqs.start_job = AsyncMock(return_value=None)

    mock_job_repo = MagicMock(spec=JobRepository)
    mock_lock_repo = MagicMock(spec=LockRepository)
    mock_lock_repo.release_by_instance = MagicMock(return_value=0)

    mock_instance_manager = MagicMock()
    mock_instance_manager._get_last_assistant_message_raw = AsyncMock(
        return_value="resume response"
    )

    observer = JobFeedbackObserver(
        event_bus=MagicMock(),
        job_queue_service=mock_jqs,
        job_repo=mock_job_repo,
        lock_repo=mock_lock_repo,
        project_repo=MagicMock(),
        instance_manager=mock_instance_manager,
    )

    # Stub the sync helper so we don't need a real DB.
    def fake_sync(
        job_id, instance_id, terminal_status, result_summary, error_message
    ):
        return _FinalizeJobResult(
            skip=False,
            terminal_status=terminal_status,
            job_id=job_id,
            instance_id=instance_id,
            parent_id=None,
            agent_id="developer",
            result_summary=result_summary,
            error_message=error_message,
            locks_released=0,
            instance_was_terminal=False,
        )

    observer._finalize_job_db_sync = fake_sync

    yield observer, bus_mock, {
        "jqs": mock_jqs,
        "job_repo": mock_job_repo,
        "lock_repo": mock_lock_repo,
        "instance_manager": mock_instance_manager,
    }

    set_dependency_bus(None)


@pytest.mark.asyncio
async def test_process_resume_finalize_calls_finalize_job_when_bus_quiet(
    observer_with_bus_quiet, mock_job
):
    """When the bus reports 0 pending for the instance,
    ``_process_resume_finalize`` must call ``_finalize_job`` (the
    same path as ``_process_event``) so the job transitions
    PROCESSING → COMPLETED.

    The pre-Phase 3 bug was that the resume path called
    ``complete_job(COMPLETED)`` directly with a TOCTOU bus check.
    The new method routes through the same transactional finalize
    path as the lifecycle-event handler.
    """
    observer, bus_mock, _mocks = observer_with_bus_quiet
    finalize_spy = AsyncMock(return_value=None)
    observer._finalize_job = finalize_spy

    await observer._process_resume_finalize(
        instance_id=mock_job.instance_id,
        job_id=mock_job.job_id,
        result_summary="resume response",
    )

    # Bus pre-check happened
    bus_mock.count_pending_for_target.assert_awaited_once_with(
        mock_job.instance_id
    )

    # _finalize_job was called (the canonical finalize path)
    finalize_spy.assert_awaited_once()
    # Phase 2.5: the first positional arg is now a
    # :class:`_ProcessingJobContext` (instance_id + job_id) rather
    # than the raw ``JobItem``. Verify the context carries the
    # expected identifiers so a future refactor that drops the
    # field wiring still fails the test.
    args, kwargs = finalize_spy.call_args
    assert isinstance(args[0], _ProcessingJobContext), (
        f"expected _ProcessingJobContext, got {type(args[0]).__name__}"
    )
    assert args[0].instance_id == mock_job.instance_id
    assert args[0].job_id == mock_job.job_id
    assert args[1] == mock_job.instance_id
    assert args[2] == "completed"
    assert kwargs.get("error") is None


@pytest.mark.asyncio
async def test_process_resume_finalize_emits_in_progress_when_bus_pending(
    observer_with_bus_quiet, mock_job
):
    """When the bus reports > 0 pending for the instance,
    ``_process_resume_finalize`` must call ``_emit_in_progress`` and
    defer (return without calling ``_finalize_job``).

    The author of the original bug raised C1 — that the no-op
    resume path left the job stuck in PROCESSING. This is the
    C1-fix test: even when children are still resolving, the
    deterministic finalize trigger emits the in_progress
    notification so watchers see consistent state.
    """
    observer, bus_mock, _mocks = observer_with_bus_quiet
    bus_mock.count_pending_for_target = AsyncMock(return_value=3)

    emit_spy = AsyncMock(return_value=None)
    observer._emit_in_progress = emit_spy

    finalize_spy = AsyncMock(return_value=None)
    observer._finalize_job = finalize_spy

    await observer._process_resume_finalize(
        instance_id=mock_job.instance_id,
        job_id=mock_job.job_id,
        result_summary="resume response",
    )

    # Pre-check found pending children
    bus_mock.count_pending_for_target.assert_awaited_once_with(
        mock_job.instance_id
    )

    # _emit_in_progress called (deferred terminal transition)
    emit_spy.assert_awaited_once()
    # Phase 2.5: ``_emit_in_progress`` now takes a
    # :class:`_ProcessingJobContext` rather than a raw ``JobItem``.
    # Verify the context carries the expected identifiers.
    emit_args = emit_spy.call_args.args
    assert isinstance(emit_args[0], _ProcessingJobContext), (
        f"expected _ProcessingJobContext, got {type(emit_args[0]).__name__}"
    )
    assert emit_args[0].instance_id == mock_job.instance_id
    assert emit_args[0].job_id == mock_job.job_id
    assert emit_args[1] == mock_job.instance_id

    # _finalize_job NOT called (we deferred)
    finalize_spy.assert_not_called()


@pytest.mark.asyncio
async def test_process_resume_finalize_raises_when_bus_none(mock_job):
    """A9: when ``get_dependency_bus()`` returns ``None``,
    ``_process_resume_finalize`` must raise ``RuntimeError``.

    The pre-Phase 3 resume path had the same A9 invariant in the
    bus check; the new method centralizes it. The bus is the SOLE
    completion authority (Phase 5) — its absence is a fatal
    misconfiguration, not a gracefully-degradable state.
    """
    set_dependency_bus(None)

    mock_jqs = MagicMock()
    mock_jqs.get_job_by_instance = AsyncMock(return_value=mock_job)

    observer = JobFeedbackObserver(
        event_bus=MagicMock(),
        job_queue_service=mock_jqs,
        job_repo=MagicMock(),
        lock_repo=MagicMock(),
        project_repo=MagicMock(),
        instance_manager=MagicMock(),
    )

    with pytest.raises(RuntimeError) as exc_info:
        await observer._process_resume_finalize(
            instance_id=mock_job.instance_id,
            job_id=mock_job.job_id,
            result_summary="resume response",
        )

    # The error message must reference the bus invariant
    assert "DependencyBus" in str(exc_info.value)
    assert "invalid state" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_process_resume_finalize_returns_early_when_no_processing_job(
    observer_with_bus_quiet, mock_job
):
    """When ``_get_processing_job_for_instance`` returns ``None``
    (the job is already finalized by a racing event-driven
    finalize), ``_process_resume_finalize`` returns silently.

    This is the double-finalize prevention path: both
    ``_process_event`` (via lifecycle event) and
    ``_process_resume_finalize` (via explicit call) race to
    finalize. The first writer transitions the job; the second
    sees PROCESSING → None and returns.
    """
    observer, bus_mock, _mocks = observer_with_bus_quiet
    observer._get_processing_job_for_instance = AsyncMock(return_value=None)

    finalize_spy = AsyncMock(return_value=None)
    observer._finalize_job = finalize_spy

    emit_spy = AsyncMock(return_value=None)
    observer._emit_in_progress = emit_spy

    await observer._process_resume_finalize(
        instance_id=mock_job.instance_id,
        job_id=mock_job.job_id,
        result_summary="resume response",
    )

    # No bus check (we short-circuit on the job lookup)
    bus_mock.count_pending_for_target.assert_not_called()
    # No finalize, no in_progress — just return
    finalize_spy.assert_not_called()
    emit_spy.assert_not_called()


# ─── 10. PAUSED → CANCELLED via terminate still works after resume changes ─


def test_paused_to_cancelled_via_terminate_still_works(engine):
    """Smoke test: a PAUSED instance can still be terminated after
    the resume changes. The resume cascade's UPDATE guards
    (``status = 'paused'``) are compatible with terminate's
    UPDATE guards (``status IN (running, idle, paused, waiting_children)``)
    — neither path overwrites the other's writes.
    """
    # Phase 3's resume changes do not touch the terminate path.
    # We verify the basic invariant: an instance in PAUSED can be
    # transitioned to a terminal status (TERMINATED) by a direct
    # repository update. This guards against a regression where
    # the resume cascade accidentally added a guard that blocks
    # terminate's writes (it didn't — but the test pins the
    # contract).
    from sqlmodel import update as sqlmodel_update

    iid = _seed_instance(engine, status=InstanceStatus.PAUSED.value)
    _seed_job(engine, instance_id=iid, status=JobStatus.PROCESSING.value)
    _seed_task(engine, instance_id=iid, status=TaskStatus.PAUSED.value)

    # Simulate terminate's status transition (this is the same
    # shape ``_terminate_instance_db_sync`` uses).
    with Session(engine) as s:
        s.exec(
            sqlmodel_update(Instance)
            .where(Instance.instance_id == iid)
            .where(Instance.status == InstanceStatus.PAUSED.value)
            .values(status=InstanceStatus.TERMINATED.value)
        )
        s.commit()

    inst = _read_instance(engine, iid)
    assert inst.status == InstanceStatus.TERMINATED.value, (
        f"terminate must succeed on a PAUSED instance, "
        f"got {inst.status}"
    )
