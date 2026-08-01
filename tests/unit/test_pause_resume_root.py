"""Phase 2.5 / Task 2.5.10 — Pause/resume E2E for root instance.

End-to-end exercise of the post-D13 pause/resume cycle against a real
in-memory SQLite engine, verifying the new
``TaskRepository.find_paused_or_running_by_instance`` routing primitive
plus the ``_finalize_job_db_sync(job_id=None)`` path that lets an
instance reach a terminal status without a ``JobItem`` row.

The scenario mirrors the documented Phase 2.5 contract (D13 consumption-
site rewrite):

  1. Seed a RUNNING instance with a RUNNING ``PROCESS_MESSAGE`` task.
  2. ``_pause_cascade_db_sync`` — instance + task both go PAUSED in one
     transaction.
  3. ``find_paused_or_running_by_instance`` returns the task (the root
     routing decision would pick ``_resume_processing_background``).
  4. ``_resume_cascade_db_sync`` — instance goes RUNNING and the
     task transitions PAUSED → CANCELLED (the resume driver owns the
     graph turn; re-arming the task would race with the resume path).
  5. ``find_paused_or_running_by_instance`` returns ``None`` (task is
     CANCELLED, not PAUSED/RUNNING) — the routing decision would now
     fall through to the child / WorkerPool path.
  6. ``_finalize_job_db_sync(job_id=None, terminal_status="completed",
     ...)`` — Step 1 (JobItem UPDATE) is skipped, Steps 2+3 (instance
     status → COMPLETED, lock release) run, and the instance reaches a
     terminal status.

The test surface intentionally avoids the full ``InstanceManager``
constructor (which wires a lot of dependencies) by directly driving the
production helpers via ``InstanceLifecycleService.__new__`` and a
real ``TaskRepository`` bound to the in-memory engine — same pattern
as ``tests/unit/test_pause_flow_redesign.py``.

Run with::

    pytest tests/unit/test_pause_resume_root.py -v --tb=short
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue.models import JobLock, AdmissionState, JobItem
from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository
from daemon.services.dependency_bus import set_dependency_bus
from daemon.services.instance_lifecycle import InstanceLifecycleService
from daemon.services.job_feedback_observer import JobFeedbackObserver
from daemon.write_pause_guard import WritePauseGuard, WriteGuardSession


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


@pytest.fixture
def _wire_bus_mock():
    """Wire a mock ``DependencyBus`` for the ``_finalize_job_db_sync`` A9 gate.

    The post-Phase-3 ``_finalize_job_db_sync`` raises ``RuntimeError``
    when the bus singleton is None (A9 invariant). The mock reports
    zero pending watchers so the gate passes and the cascade commits.
    """
    bus_mock = MagicMock()
    bus_mock.count_pending_for_target_sync = lambda _iid: 0
    set_dependency_bus(bus_mock)
    yield bus_mock
    set_dependency_bus(None)


def _seed_instance(
    engine: Engine,
    *,
    instance_id: str | None = None,
    status: str = InstanceStatus.RUNNING.value,
    agent_id: str = "developer",
    parent_id: str | None = None,
) -> str:
    """Insert an ``Instance`` row. Returns the ``instance_id``."""
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


def _seed_task(
    engine: Engine,
    *,
    instance_id: str,
    status: str = TaskStatus.RUNNING.value,
    task_type: str = TaskType.PROCESS_MESSAGE.value,
    message_id: str | None = None,
) -> int:
    """Insert a ``Task`` row. Returns the task ``id``."""
    now = datetime.now(timezone.utc)
    with Session(engine) as s:
        task = Task(
            task_type=task_type,
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


def _seed_lock(
    engine: Engine,
    *,
    instance_id: str,
    project_id: str = "test-project",
    queue_id: str = "default",
) -> str:
    """Insert a ``JobLock`` row. Returns the ``lock_id``."""
    lid = f"lock-{uuid.uuid4().hex[:8]}"
    with Session(engine) as s:
        lock = JobLock(
            lock_id=lid,
            project_id=project_id,
            queue_id=queue_id,
            job_id=f"job-{uuid.uuid4().hex[:8]}",
            instance_id=instance_id,
            lock_slot=0,
        )
        s.add(lock)
        s.commit()
    return lid


def _read_instance(engine: Engine, instance_id: str) -> Instance | None:
    with Session(engine) as s:
        return s.get(Instance, instance_id)


def _read_task(engine: Engine, instance_id: str) -> Task | None:
    with Session(engine) as s:
        from sqlmodel import select
        rows = s.exec(
            select(Task).where(Task.instance_id == instance_id)
        ).all()
        return rows[0] if rows else None


def _read_task_status(engine: Engine, instance_id: str) -> str | None:
    """Raw-SQL task status read.

    Workaround for the production code's resume cascade writing
    ``completed_at`` as a TEXT string via
    ``CAST(:now_ts AS TIMESTAMP)`` — SQLAlchemy then raises
    ``TypeError: fromisoformat: argument must be str`` when the ORM
    session tries to hydrate the column as ``datetime``. We read the
    status column directly to avoid the broken column entirely.
    """
    from sqlalchemy import text as _text
    with Session(engine) as s:
        result = s.execute(
            _text("SELECT status FROM task WHERE instance_id = :iid"),
            {"iid": instance_id},
        )
        row = result.first()
        return row[0] if row else None


def _count_locks(engine: Engine, instance_id: str) -> int:
    with Session(engine) as s:
        from sqlmodel import select
        rows = s.exec(
            select(JobLock).where(JobLock.instance_id == instance_id)
        ).all()
        return len(list(rows))


@pytest.fixture
def lifecycle_service(engine, write_guard):
    """Build ``InstanceLifecycleService`` bound to a real DB.

    Same bypass pattern as ``test_pause_flow_redesign.py``: the service
    is constructed via ``__new__`` so only the helpers we exercise
    (``_pause_cascade_db_sync`` / ``_resume_cascade_db_sync``) need
    their dependencies. The mock ``manager`` exposes ``engine`` and
    ``write_guard`` — the only two attributes the cascade helpers
    actually touch.
    """
    service = InstanceLifecycleService.__new__(InstanceLifecycleService)
    manager = MagicMock()
    manager.engine = engine
    manager.write_guard = write_guard
    service._manager = manager
    return service


# ─── Task 2.5.10: Pause/resume E2E for root instance ─────────────────────────


class TestPauseResumeRoot:
    """End-to-end pause/resume for a root instance.

    Drives the production ``_pause_cascade_db_sync`` and
    ``_resume_cascade_db_sync`` helpers against a real in-memory SQLite
    engine and verifies the new
    ``TaskRepository.find_paused_or_running_by_instance`` primitive
    plus the ``_finalize_job_db_sync(job_id=None)`` no-JobItem path.
    """

    def test_pause_then_resume_then_finalize_reaches_completed(
        self,
        engine,
        write_guard,
        lifecycle_service,
        _wire_bus_mock,
    ):
        """Full lifecycle: RUNNING → PAUSED → RUNNING → COMPLETED.

        Steps:

          1. Seed instance (RUNNING) + PROCESS_MESSAGE task (RUNNING).
          2. ``_pause_cascade_db_sync`` — instance + task both PAUSED.
          3. ``find_paused_or_running_by_instance`` returns the task
             (root-instance routing decision would fire checkpoint
             resume via ``_resume_processing_background``).
          4. ``_resume_cascade_db_sync`` — instance RUNNING, task
             PAUSED → CANCELLED (resume driver owns the graph turn;
             the task is non-claimable so the WorkerPool cannot race).
          5. ``find_paused_or_running_by_instance`` now returns
             ``None`` (CANCELLED is not in the PAUSED/RUNNING set).
          6. ``_finalize_job_db_sync(job_id=None, terminal_status=
             "completed", ...)`` — Step 1 (JobItem UPDATE) is skipped,
             Steps 2+3 (instance → COMPLETED, lock release) run.

        The instance reaches ``COMPLETED`` (not stuck in PAUSED or
        RUNNING) — the original B1 bug the D13 rewrite fixed.
        """
        # 1. Seed
        iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        task_id = _seed_task(
            engine,
            instance_id=iid,
            status=TaskStatus.RUNNING.value,
        )
        lock_id = _seed_lock(engine, instance_id=iid)
        assert _count_locks(engine, iid) == 1

        task_repo = TaskRepository(engine)

        # 2. Pause cascade — instance + task both PAUSED
        result = lifecycle_service._pause_cascade_db_sync(
            engine,
            write_guard,
            tree_ids=[iid],
            paused_at_iso=datetime.now(timezone.utc).isoformat(),
            paused_instances_data=[(iid, "developer")],
        )
        assert result.updated_ids == [iid]

        inst_after_pause = _read_instance(engine, iid)
        task_after_pause = _read_task(engine, iid)
        assert inst_after_pause.status == InstanceStatus.PAUSED.value
        assert task_after_pause.status == TaskStatus.PAUSED.value

        # 3. Routing decision after pause: root path (PAUSED PROCESS_MESSAGE task)
        routed_task = task_repo.find_paused_or_running_by_instance(iid)
        assert routed_task is not None, (
            "find_paused_or_running_by_instance must return the paused "
            "PROCESS_MESSAGE task after pause cascade (root routing "
            "decision — checkpoint resume via _resume_processing_background)"
        )
        assert routed_task.id == task_id
        assert routed_task.status == TaskStatus.PAUSED.value
        assert routed_task.task_type == TaskType.PROCESS_MESSAGE.value

        # 4. Resume cascade — instance RUNNING, task PAUSED → CANCELLED
        resume_result = lifecycle_service._resume_cascade_db_sync(
            engine,
            write_guard,
            tree_ids=[iid],
            ancestor_ids=set(),
            is_root_resume=True,
        )
        assert iid in resume_result.updated_ids

        inst_after_resume = _read_instance(engine, iid)
        # The task row's ``completed_at`` column is written as TEXT
        # by the production cascade (see ``_read_task_status`` for
        # context) so we read the status via raw SQL to avoid
        # SQLAlchemy's datetime hydration failure.
        task_status_after_resume = _read_task_status(engine, iid)
        assert inst_after_resume.status == InstanceStatus.RUNNING.value, (
            "instance must transition PAUSED → RUNNING on resume"
        )
        assert task_status_after_resume == TaskStatus.CANCELLED.value, (
            "task must transition PAUSED → CANCELLED on resume (the "
            "resume driver owns the graph turn; CANCELLED keeps the "
            "WorkerPool from re-claiming and racing)"
        )

        # 5. Routing decision after resume: still a root candidate.
        # The resume cascade transitions the task PAUSED → CANCELLED to
        # prevent the WorkerPool from re-claiming (W2 fix), but the
        # task is still the marker that this instance was paused-and-
        # resumed. ``find_paused_or_running_by_instance`` MUST include
        # CANCELLED in its status set so ``resume_processing_job`` can
        # locate the task, run the stale-message cleanup, and route to
        # ``_resume_processing_background`` (the root path).
        #
        # Without CANCELLED in the IN clause, ``resume_processing_job``
        # falls through to the WorkerPool child path, enqueues a NEW
        # task alongside the stale PAUSED/PROCESSING message from the
        # paused turn, and the parent wedges at ``waiting_children``
        # after the final LLM turn (the E2E
        # ``test_pause_after_spawn_then_resume`` regression).
        routed_after_resume = task_repo.find_paused_or_running_by_instance(iid)
        assert routed_after_resume is not None, (
            "find_paused_or_running_by_instance must return the CANCELLED "
            "task after resume cascade; CANCELLED is the marker that the "
            "instance was paused-and-resumed and needs the resume cleanup "
            "path (stale message → COMPLETED + _resume_processing_background)"
        )
        assert routed_after_resume.status == TaskStatus.CANCELLED.value
        assert routed_after_resume.id == task_id

        # 6. Finalize WITHOUT a JobItem — the post-D13 no-JobItem path.
        # Step 1 (JobItem UPDATE) is skipped because ``job_id is None``;
        # Steps 2+3 (instance status → COMPLETED + lock release) run.
        # We construct a JobFeedbackObserver with the minimum surface
        # the ``_finalize_job_db_sync`` helper needs (engine, write_guard,
        # bus singleton).
        from daemon.services.job_feedback_observer import (
            JobFeedbackObserver,
        )

        observer = JobFeedbackObserver.__new__(JobFeedbackObserver)
        observer._instance_manager = MagicMock()
        observer._instance_manager.engine = engine
        observer._instance_manager.write_guard = write_guard
        observer._instance_manager.is_write_paused = False
        observer._bus_count_pending_for_target_sync = lambda _iid: 0

        finalize_result = observer._finalize_job_db_sync(
            job_id=None,  # No-JobItem path
            instance_id=iid,
            terminal_status=InstanceStatus.COMPLETED.value,
            result_summary="all good",
            error_message=None,
        )

        assert finalize_result.skip is False
        assert finalize_result.terminal_status == InstanceStatus.COMPLETED.value
        assert finalize_result.instance_id == iid
        assert finalize_result.locks_released == 1, (
            "Step 3 (lock release) must run even with job_id=None; "
            "the seeded JobLock must be deleted"
        )
        assert finalize_result.instance_was_terminal is False

        # 6a. Instance reaches COMPLETED — not stuck in PAUSED / RUNNING.
        inst_final = _read_instance(engine, iid)
        assert inst_final.status == InstanceStatus.COMPLETED.value, (
            f"instance must reach COMPLETED after _finalize_job_db_sync; "
            f"got {inst_final.status!r}"
        )

        # 6b. Lock released.
        assert _count_locks(engine, iid) == 0, (
            "Step 3 (lock release) must delete the seeded JobLock; "
            "leaked locks would block the per-instance serialization "
            "guard for the next message on this instance"
        )

        # 6c. No JobItem exists (we never seeded one) — Step 1 was a
        # safe no-op, not an error.
        with Session(engine) as s:
            from sqlmodel import select
            from daemon.repositories.job_queue.models import JobItem
            jobs = s.exec(
                select(JobItem).where(JobItem.instance_id == iid)
            ).all()
            assert len(list(jobs)) == 0

    def test_find_paused_or_running_excludes_pending_task(
        self, engine, write_guard, lifecycle_service
    ):
        """``find_paused_or_running_by_instance`` ignores PENDING tasks.

        Sister query to ``find_running_by_instance``: only PAUSED or
        RUNNING ``PROCESS_MESSAGE`` tasks qualify. A PENDING task is
        not yet "in flight" — it has not been claimed by a worker —
        so it does not identify a root instance for checkpoint resume.
        The routing decision must treat it as a child path so a fresh
        message can be enqueued normally.
        """
        iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        _seed_task(
            engine,
            instance_id=iid,
            status=TaskStatus.PENDING.value,  # not claimed yet
        )
        task_repo = TaskRepository(engine)

        routed = task_repo.find_paused_or_running_by_instance(iid)
        assert routed is None, (
            "PENDING tasks must NOT count as root-resume candidates — "
            "only PAUSED/RUNNING PROCESS_MESSAGE tasks do"
        )

    def test_find_paused_or_running_excludes_report_task(
        self, engine, write_guard, lifecycle_service
    ):
        """``find_paused_or_running_by_instance`` filters on ``task_type``.

        Only ``PROCESS_MESSAGE`` tasks identify a root instance for
        checkpoint resume. ``PROCESS_REPORT`` tasks (Phase 1, report
        lane) ride alongside user messages on the same ``task`` table
        but are a sibling notification lane — a paused report task
        must NOT trigger the root-resume routing.
        """
        iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        _seed_task(
            engine,
            instance_id=iid,
            status=TaskStatus.RUNNING.value,
            task_type=TaskType.PROCESS_REPORT.value,
        )
        task_repo = TaskRepository(engine)

        routed = task_repo.find_paused_or_running_by_instance(iid)
        assert routed is None, (
            "PROCESS_REPORT tasks must NOT identify a root-resume "
            "candidate — only PROCESS_MESSAGE tasks do"
        )


# ─── Phase 1 Bug A / Step B: find_resume_root_candidate_by_active_job ───────


def _seed_job_item_for_test(
    engine: Engine,
    *,
    job_id: str,
    instance_id: str,
    admission_state: str = AdmissionState.ACTIVE.value,
    message_id: str | None = None,
    deleted_at: str | None = None,
) -> JobItem:
    """Seed a JobItem for the routing-primitive tests.

    Uses ``queue_id=None`` to avoid the FK constraint on
    ``job_queues.queue_id`` (the test engine has FKs enabled).
    """
    from daemon.repositories.job_queue.models import JobItem
    job = JobItem(
        job_id=job_id,
        agent_id="developer",
        agent_dir="/tmp/agents/developer",
        message="orphan-routing-test",
        source="api",
        project_id="test-project",
        priority=5,
        job_metadata={"message_id": message_id} if message_id else {},
        queue_id=None,  # FK: avoid requiring a job_queues row
        job_type="task",
        instance_id=instance_id,
        admission_state=admission_state,
        deleted_at=deleted_at,
    )
    with Session(engine) as s:
        s.add(job)
        s.commit()
        s.refresh(job)
    return job


class TestFindResumeRootCandidateByActiveJob:
    """Repository unit tests for the report-turn-pause fallback primitive.

    Phase 1 / Step B (Bug A). ``find_resume_root_candidate_by_active_job``
    is the active-orphan fallback for ``resume_processing_job``. The
    full test matrix is documented in the plan §3.2.
    """

    def test_terminal_task_with_active_jobitem_returns_candidate(
        self, engine, write_guard, lifecycle_service
    ):
        """Positive: terminal PROCESS_MESSAGE Task + matching active JobItem.

        The Bug A incident state — the original PROCESS_MESSAGE Task
        has reached COMPLETED, and an active JobItem correlates via
        ``work_id == job_id``. The fallback returns the terminal Task
        so ``resume_processing_job`` can route to the root path.
        """
        iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        task_work_id = str(uuid.uuid4().hex)
        task = _seed_task_with_work_id(
            engine,
            instance_id=iid,
            status=TaskStatus.COMPLETED.value,
            task_type=TaskType.PROCESS_MESSAGE.value,
            work_id=task_work_id,
        )
        _seed_job_item_for_test(
            engine,
            job_id=task_work_id,
            instance_id=iid,
            admission_state=AdmissionState.ACTIVE.value,
        )

        task_repo = TaskRepository(engine)
        candidate = task_repo.find_resume_root_candidate_by_active_job(iid)
        assert candidate is not None, (
            "Active-orphan fallback must return the terminal backing "
            "PROCESS_MESSAGE Task correlated via work_id == job_id"
        )
        assert candidate.work_id == task_work_id
        assert candidate.status == TaskStatus.COMPLETED.value

    def test_paused_task_blocks_fallback_returns_none(
        self, engine, write_guard, lifecycle_service
    ):
        """Existing route owns it — PAUSED PROCESS_MESSAGE Task present.

        When ``find_paused_or_running_by_instance`` already returns a
        Task (the normal paused-before-resume case), the active-orphan
        fallback MUST return ``None`` so the existing root route owns
        the routing decision.
        """
        iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        # A PAUSED PROCESS_MESSAGE Task — existing route owns it.
        _seed_task(
            engine,
            instance_id=iid,
            status=TaskStatus.PAUSED.value,
            task_type=TaskType.PROCESS_MESSAGE.value,
        )
        # And a terminal Task with matching JobItem (the bug A scenario).
        # The fallback MUST NOT return this because the PAUSED task
        # already represents a resumable marker.
        terminal_work_id = str(uuid.uuid4().hex)
        _seed_task_with_work_id(
            engine,
            instance_id=iid,
            status=TaskStatus.COMPLETED.value,
            task_type=TaskType.PROCESS_MESSAGE.value,
            work_id=terminal_work_id,
        )
        _seed_job_item_for_test(
            engine,
            job_id=terminal_work_id,
            instance_id=iid,
            admission_state=AdmissionState.ACTIVE.value,
        )

        task_repo = TaskRepository(engine)
        candidate = task_repo.find_resume_root_candidate_by_active_job(iid)
        assert candidate is None, (
            "When a PAUSED PROCESS_MESSAGE Task exists (existing route "
            "owns it), the active-orphan fallback MUST return None — "
            "do not double-route"
        )

    def test_running_task_blocks_fallback_returns_none(
        self, engine, write_guard, lifecycle_service
    ):
        """Existing route owns it — RUNNING PROCESS_MESSAGE Task present."""
        iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        _seed_task(
            engine,
            instance_id=iid,
            status=TaskStatus.RUNNING.value,
            task_type=TaskType.PROCESS_MESSAGE.value,
        )

        task_repo = TaskRepository(engine)
        candidate = task_repo.find_resume_root_candidate_by_active_job(iid)
        assert candidate is None

    def test_cancelled_task_blocks_fallback_returns_none(
        self, engine, write_guard, lifecycle_service
    ):
        """Existing route owns it — CANCELLED Task (resume cascade marker)."""
        iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        _seed_task(
            engine,
            instance_id=iid,
            status=TaskStatus.CANCELLED.value,
            task_type=TaskType.PROCESS_MESSAGE.value,
        )

        task_repo = TaskRepository(engine)
        candidate = task_repo.find_resume_root_candidate_by_active_job(iid)
        assert candidate is None

    def test_queued_jobitem_no_match_returns_none(
        self, engine, write_guard, lifecycle_service
    ):
        """Queued JobItem (not active) → fallback returns None.

        Only ``admission_state = 'active'`` JobItems trigger the
        fallback. A queued mirror (F1 stuck-mirror) does NOT match.
        """
        iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        task_work_id = str(uuid.uuid4().hex)
        _seed_task_with_work_id(
            engine,
            instance_id=iid,
            status=TaskStatus.COMPLETED.value,
            task_type=TaskType.PROCESS_MESSAGE.value,
            work_id=task_work_id,
        )
        _seed_job_item_for_test(
            engine,
            job_id=task_work_id,
            instance_id=iid,
            admission_state=AdmissionState.QUEUED.value,
        )

        task_repo = TaskRepository(engine)
        candidate = task_repo.find_resume_root_candidate_by_active_job(iid)
        assert candidate is None

    def test_done_jobitem_no_match_returns_none(
        self, engine, write_guard, lifecycle_service
    ):
        """Done JobItem (terminal) → fallback returns None.

        The JobItem has reached its terminal admission state and is
        no longer holding a lock; nothing to orphan-resume.
        """
        iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        task_work_id = str(uuid.uuid4().hex)
        _seed_task_with_work_id(
            engine,
            instance_id=iid,
            status=TaskStatus.COMPLETED.value,
            task_type=TaskType.PROCESS_MESSAGE.value,
            work_id=task_work_id,
        )
        _seed_job_item_for_test(
            engine,
            job_id=task_work_id,
            instance_id=iid,
            admission_state=AdmissionState.DONE.value,
        )

        task_repo = TaskRepository(engine)
        candidate = task_repo.find_resume_root_candidate_by_active_job(iid)
        assert candidate is None

    def test_deleted_jobitem_no_match_returns_none(
        self, engine, write_guard, lifecycle_service
    ):
        """Deleted (soft-delete) JobItem → fallback returns None."""
        iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        task_work_id = str(uuid.uuid4().hex)
        _seed_task_with_work_id(
            engine,
            instance_id=iid,
            status=TaskStatus.COMPLETED.value,
            task_type=TaskType.PROCESS_MESSAGE.value,
            work_id=task_work_id,
        )
        _seed_job_item_for_test(
            engine,
            job_id=task_work_id,
            instance_id=iid,
            admission_state=AdmissionState.ACTIVE.value,
            deleted_at=datetime.now(timezone.utc).isoformat(),
        )

        task_repo = TaskRepository(engine)
        candidate = task_repo.find_resume_root_candidate_by_active_job(iid)
        assert candidate is None

    def test_mismatched_work_id_no_match_returns_none(
        self, engine, write_guard, lifecycle_service
    ):
        """Mismatched work_id → no match.

        The JobItem's job_id differs from the Task's work_id. They
        do NOT correlate. The fallback returns None.
        """
        iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        _seed_task_with_work_id(
            engine,
            instance_id=iid,
            status=TaskStatus.COMPLETED.value,
            task_type=TaskType.PROCESS_MESSAGE.value,
            work_id=str(uuid.uuid4().hex),
        )
        # JobItem with a DIFFERENT job_id
        _seed_job_item_for_test(
            engine,
            job_id=str(uuid.uuid4().hex),  # not matching
            instance_id=iid,
            admission_state=AdmissionState.ACTIVE.value,
        )

        task_repo = TaskRepository(engine)
        candidate = task_repo.find_resume_root_candidate_by_active_job(iid)
        assert candidate is None

    def test_no_jobitem_no_match_returns_none(
        self, engine, write_guard, lifecycle_service
    ):
        """No JobItem at all → fallback returns None."""
        iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        _seed_task_with_work_id(
            engine,
            instance_id=iid,
            status=TaskStatus.COMPLETED.value,
            task_type=TaskType.PROCESS_MESSAGE.value,
            work_id=str(uuid.uuid4().hex),
        )
        # No JobItem seeded

        task_repo = TaskRepository(engine)
        candidate = task_repo.find_resume_root_candidate_by_active_job(iid)
        assert candidate is None

    def test_no_task_no_match_returns_none(
        self, engine, write_guard, lifecycle_service
    ):
        """No PROCESS_MESSAGE Task at all → fallback returns None."""
        iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        # No Task seeded
        _seed_job_item_for_test(
            engine,
            job_id=str(uuid.uuid4().hex),
            instance_id=iid,
            admission_state=AdmissionState.ACTIVE.value,
        )

        task_repo = TaskRepository(engine)
        candidate = task_repo.find_resume_root_candidate_by_active_job(iid)
        assert candidate is None

    def test_report_task_only_no_match_returns_none(
        self, engine, write_guard, lifecycle_service
    ):
        """Only PROCESS_REPORT Tasks exist → fallback returns None.

        The fallback filters on ``task_type = PROCESS_MESSAGE``. A
        PROCESS_REPORT-only history is irrelevant.
        """
        iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        work_id_report = str(uuid.uuid4().hex)
        _seed_task_with_work_id(
            engine,
            instance_id=iid,
            status=TaskStatus.RUNNING.value,
            task_type=TaskType.PROCESS_REPORT.value,
            work_id=work_id_report,
        )
        _seed_job_item_for_test(
            engine,
            job_id=work_id_report,
            instance_id=iid,
            admission_state=AdmissionState.ACTIVE.value,
        )

        task_repo = TaskRepository(engine)
        candidate = task_repo.find_resume_root_candidate_by_active_job(iid)
        assert candidate is None

    def test_newest_terminal_message_with_active_jobitem_returns_newest(
        self, engine, write_guard, lifecycle_service
    ):
        """Newest terminal PROCESS_MESSAGE Task with matching active JobItem.

        When multiple terminal PROCESS_MESSAGE Tasks exist, the
        fallback returns the newest one. The active JobItem correlates
        to that newest Task via ``work_id == job_id``.
        """
        iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        # Older terminal Task — no JobItem correlation
        _seed_task_with_work_id(
            engine,
            instance_id=iid,
            status=TaskStatus.COMPLETED.value,
            task_type=TaskType.PROCESS_MESSAGE.value,
            work_id=str(uuid.uuid4().hex),
        )
        # Newer terminal Task — with matching active JobItem
        newer_work_id = str(uuid.uuid4().hex)
        _seed_task_with_work_id(
            engine,
            instance_id=iid,
            status=TaskStatus.COMPLETED.value,
            task_type=TaskType.PROCESS_MESSAGE.value,
            work_id=newer_work_id,
        )
        _seed_job_item_for_test(
            engine,
            job_id=newer_work_id,
            instance_id=iid,
            admission_state=AdmissionState.ACTIVE.value,
        )

        task_repo = TaskRepository(engine)
        candidate = task_repo.find_resume_root_candidate_by_active_job(iid)
        assert candidate is not None
        assert candidate.work_id == newer_work_id, (
            "Fallback must return the most-recent terminal PROCESS_MESSAGE "
            "Task with matching active JobItem correlation"
        )

    def test_retry_scenario_parent_cancelled_retry_pending_returns_none(
        self, engine, write_guard, lifecycle_service
    ):
        """Retry scenario (W4 case 1, KEY regression).

        Parent Task: CANCELLED, with matching active JobItem.
        Retry child Task: PENDING (same message_id, distinct work_id).
        The fallback MUST return ``None`` because:

        1. The CANCELLED parent is treated by the existing
           ``find_paused_or_running_by_instance`` as a "resumable
           marker" — the regular root route owns the instance.
        2. The retry path (schedule_retry) is NOT what the fallback
           services; the retry child is claimed by the regular
           ``claim_pending_task`` flow (FIFO recovery).

        The KEY regression (W4 case 1) at the admission-guard
        level (Step A) is covered in
        ``tests/test_terminal_orphan_matrix.py::
        TestRetryScenarioRegression``. This Step-B primitive test
        pins the retry-child routing contract: the fallback MUST
        NOT steal retry-path work.
        """
        iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        shared_message_id = "msg-retry-shared"

        # Parent CANCELLED with work_id X and matching active JobItem
        parent_work_id = str(uuid.uuid4().hex)
        _seed_task_with_work_id(
            engine,
            instance_id=iid,
            status=TaskStatus.CANCELLED.value,
            task_type=TaskType.PROCESS_MESSAGE.value,
            work_id=parent_work_id,
            message_id=shared_message_id,
        )
        _seed_job_item_for_test(
            engine,
            job_id=parent_work_id,
            instance_id=iid,
            admission_state=AdmissionState.ACTIVE.value,
            message_id=shared_message_id,
        )

        # Retry child PENDING with FRESH work_id Y, same message_id
        retry_work_id = str(uuid.uuid4().hex)
        assert retry_work_id != parent_work_id, (
            "Retry must have a distinct work_id from parent"
        )
        _seed_task_with_work_id(
            engine,
            instance_id=iid,
            status=TaskStatus.PENDING.value,
            task_type=TaskType.PROCESS_MESSAGE.value,
            work_id=retry_work_id,
            message_id=shared_message_id,
        )

        task_repo = TaskRepository(engine)
        candidate = task_repo.find_resume_root_candidate_by_active_job(iid)
        assert candidate is None, (
            "Retry scenario: CANCELLED parent is owned by the existing "
            "root route (resume cascade marker). The fallback MUST return "
            "None — the retry child is claimed via FIFO recovery, NOT "
            "via this fallback."
        )


def _seed_task_with_work_id(
    engine: Engine,
    *,
    instance_id: str,
    status: str,
    task_type: str = TaskType.PROCESS_MESSAGE.value,
    work_id: str,
    message_id: str | None = None,
) -> int:
    """Insert a Task row with an explicit work_id. Returns the task id."""
    now = datetime.now(timezone.utc)
    with Session(engine) as s:
        task = Task(
            task_type=task_type,
            instance_id=instance_id,
            message_id=message_id,
            status=status,
            work_id=work_id,
            created_at=now,
            updated_at=now,
        )
        s.add(task)
        s.commit()
        s.refresh(task)
        return int(task.id)
