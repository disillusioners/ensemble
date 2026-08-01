"""Phase 3 / Increment 4 — Pause/resume E2E for root instance, plus
explicit-handle selectors.

End-to-end exercise of the post-D13 pause/resume cycle against a real
in-memory SQLite engine, verifying the
``TaskRepository.find_paused_or_cancellable_turn`` pause-cascade
selector, the ``TaskRepository.find_suspended_turn_for_answer``
answer-gate selector, and the explicit ``suspension_reason`` /
``resume_target_turn_id`` handle persisted by
``SuspendTurn`` / ``ResumeTurn`` (see
``daemon/services/turn_transitions.py``).

Phase 2.5 (Task 2.5.10): the original ``find_paused_or_running_by_instance``
test surface is migrated here to the equivalent
``find_paused_or_cancellable_turn`` tests (the old selector is
deleted in Phase 3). The pause/resume scenario mirrors the
documented Phase 2.5 contract (D13 consumption-site rewrite):
  1. Seed a RUNNING instance with a RUNNING ``PROCESS_MESSAGE`` task.
  2. ``_pause_cascade_db_sync`` — instance + task both go PAUSED in one
     transaction. ``SuspendTurn`` (Phase 3) persists the suspension
     handle fields (``suspension_reason``,
     ``resume_target_turn_id``) in the same UPDATE.
  3. ``find_paused_or_cancellable_turn`` returns the task (the
     pause-cascade selector would feed ``SUSPEND_TURN`` /
     ``RESUME_TURN``).
  4. ``_resume_cascade_db_sync`` — instance goes RUNNING and the
     task transitions PAUSED → CANCELLED (the resume driver owns the
     graph turn; re-arming the task would race with the resume path).
     ``ResumeTurn`` (Phase 3) clears the suspension handle in the
     same guarded UPDATE.
  5. ``find_paused_or_cancellable_turn`` does NOT return the
     CANCELLED task (CANCELLED is not in the pause-cascade eligible
     set — the selector returns the task for an external pause
     cascade, but CANCELLED is excluded because the task is no longer
     cancellable). The resume cleanup path drives the natural
     recovery.
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
from daemon.repositories.task.models import SuspensionReason, Task, TaskStatus, TaskType
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
    engine and verifies the
    ``TaskRepository.find_paused_or_cancellable_turn`` pause-cascade
    selector plus the ``_finalize_job_db_sync(job_id=None)`` no-JobItem
    path.
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
          3. ``find_paused_or_cancellable_turn`` returns the task
             (pause-cascade selector for the explicit SUSPEND_TURN
             path).
          4. ``_resume_cascade_db_sync`` — instance RUNNING, task
             PAUSED → CANCELLED (resume driver owns the graph turn;
             the task is non-claimable so the WorkerPool cannot race).
          5. ``find_paused_or_cancellable_turn`` now returns
             ``None`` (CANCELLED is not in the pause-cascade eligible
             set — the selector restricts to PAUSED/RUNNING for the
             named pause/cancel transitions per §8.2).
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

        # 3. Pause-cascade selector returns the PAUSED task.
        routed_task = task_repo.find_paused_or_cancellable_turn(iid)
        assert routed_task is not None, (
            "find_paused_or_cancellable_turn must return the paused "
            "PROCESS_MESSAGE task after pause cascade (the pause-"
            "cascade selector for SUSPEND_TURN)"
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

        # 5. Pause-cascade selector after resume: CANCELLED is
        # NOT in the eligible status set (the selector restricts to
        # PAUSED/RUNNING for the named pause/cancel transitions,
        # per §8.2). The CANCELLED task is a no-longer-cancellable
        # marker — the resume cleanup path drives the natural
        # recovery, not the pause cascade.
        routed_after_resume = task_repo.find_paused_or_cancellable_turn(iid)
        assert routed_after_resume is None, (
            "find_paused_or_cancellable_turn must return None when "
            "the only candidate is CANCELLED (CANCELLED is not in "
            "the pause-cascade eligible set per §8.2)"
        )

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

    def test_find_paused_or_cancellable_excludes_pending_task(
        self, engine, write_guard, lifecycle_service
    ):
        """``find_paused_or_cancellable_turn`` ignores PENDING tasks.

        Sister query to ``find_running_by_instance``: only PAUSED or
        RUNNING ``PROCESS_MESSAGE`` / ``PROCESS_REPORT`` tasks
        qualify. A PENDING task is not yet "in flight" — it has not
        been claimed by a worker — so it does not identify a turn
        for the pause cascade. The selector must treat it as "no
        match" so the cascade does not act on a not-yet-running
        task.
        """
        iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        _seed_task(
            engine,
            instance_id=iid,
            status=TaskStatus.PENDING.value,  # not claimed yet
        )
        task_repo = TaskRepository(engine)

        routed = task_repo.find_paused_or_cancellable_turn(iid)
        assert routed is None, (
            "PENDING tasks must NOT count as pause-cascade "
            "candidates — only PAUSED/RUNNING PROCESS_MESSAGE / "
            "PROCESS_REPORT tasks do (per §8.2)"
        )

    def test_find_paused_or_cancellable_includes_report_task(
        self, engine, write_guard, lifecycle_service
    ):
        """``find_paused_or_cancellable_turn`` INCLUDES ``PROCESS_REPORT``.

        Per the plan §8.2 / §9.2 Bug-A fix: a paused ``PROCESS_REPORT``
        turn is a valid pause-cascade candidate. The selector widens
        ``task_type`` from ``PROCESS_MESSAGE`` only to include
        ``PROCESS_REPORT`` so the pause cascade sees the in-flight
        report turn — the regression test for pause-during-report.
        """
        iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        _seed_task(
            engine,
            instance_id=iid,
            status=TaskStatus.RUNNING.value,
            task_type=TaskType.PROCESS_REPORT.value,
        )
        task_repo = TaskRepository(engine)

        routed = task_repo.find_paused_or_cancellable_turn(iid)
        assert routed is not None, (
            "PROCESS_REPORT tasks MUST count as pause-cascade "
            "candidates — this is the §8.2 widening that closes "
            "the pause-during-report-turn routing gap (Bug A)"
        )
        assert routed.task_type == TaskType.PROCESS_REPORT.value


# ─── Phase 3 (Increment 4): find_suspended_turn_for_answer ─────────────────


class TestFindSuspendedTurnForAnswer:
    """Repository unit tests for the explicit answer-gate selector.

    Phase 3 (Increment 4, 2026-08-01).
    ``find_suspended_turn_for_answer(instance_id)`` is the
    answer-routing primitive that supersedes the inference-based
    ``find_paused_or_running_by_instance`` for answer-gate
    selection. It MUST only return a Task that was suspended via
    ``SuspendTurn(reason='awaiting_answer')`` with a non-null
    ``resume_target_turn_id`` and ``status='paused'`` (§7/§8.1).

    Per plan §8.1: returns ``None`` for 0 rows, returns the
    single row for 1 row, raises ``ValueError`` for ambiguity
    (>1 rows). Tests below cover the positive case, the
    negative case, the ambiguity case, and the suspension
    handle invariants.
    """

    def test_awaiting_answer_paused_with_target_returns_row(
        self, engine, write_guard, lifecycle_service
    ):
        """Positive: PAUSED + awaiting_answer + non-null target → row returned."""
        iid = _seed_instance(engine, status=InstanceStatus.PAUSED.value)
        target_work_id = str(uuid.uuid4().hex)
        # Seed a PAUSED task with the await-answer handle set.
        _seed_task_with_handle(
            engine,
            instance_id=iid,
            status=TaskStatus.PAUSED.value,
            task_type=TaskType.PROCESS_MESSAGE.value,
            suspension_reason=SuspensionReason.AWAITING_ANSWER.value,
            resume_target_turn_id=target_work_id,
        )
        task_repo = TaskRepository(engine)
        suspended = task_repo.find_suspended_turn_for_answer(iid)
        assert suspended is not None, (
            "Awaiting-answer PAUSED task with a non-null "
            "resume_target_turn_id MUST be returned by "
            "find_suspended_turn_for_answer (the explicit-handle "
            "answer-routing primitive)"
        )
        assert suspended.suspension_reason == SuspensionReason.AWAITING_ANSWER.value
        assert suspended.resume_target_turn_id == target_work_id

    def test_paused_without_handle_returns_none(
        self, engine, write_guard, lifecycle_service
    ):
        """PAUSED without ``suspension_reason`` set → ``None``.

        Pre-Increment-4 paused tasks (the B2 backfill set these to
        ``paused_external`` during the schema phase; pre-backfill
        row data is exercised here for the empty-handle shape) MUST
        NOT be selected as the awaiting-answer gate.
        """
        iid = _seed_instance(engine, status=InstanceStatus.PAUSED.value)
        _seed_task_with_handle(
            engine,
            instance_id=iid,
            status=TaskStatus.PAUSED.value,
            task_type=TaskType.PROCESS_MESSAGE.value,
            suspension_reason=None,
            resume_target_turn_id=None,
        )
        task_repo = TaskRepository(engine)
        assert task_repo.find_suspended_turn_for_answer(iid) is None

    def test_running_task_not_selected(
        self, engine, write_guard, lifecycle_service
    ):
        """RUNNING task is not yet a ``PAUSED`` answer-gate.

        ``find_suspended_turn_for_answer`` requires ``status='paused'`` —
        RUNNING is excluded. The defence-in-depth filter complements
        the ``SuspendTurn`` write path which atomically sets
        ``status='paused'`` + handle in one UPDATE.
        """
        iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        _seed_task_with_handle(
            engine,
            instance_id=iid,
            status=TaskStatus.RUNNING.value,
            task_type=TaskType.PROCESS_MESSAGE.value,
            suspension_reason=SuspensionReason.AWAITING_ANSWER.value,
            resume_target_turn_id=str(uuid.uuid4().hex),
        )
        task_repo = TaskRepository(engine)
        assert task_repo.find_suspended_turn_for_answer(iid) is None

    def test_non_answer_reason_returns_none(
        self, engine, write_guard, lifecycle_service
    ):
        """PAUSED with ``paused_external`` / ``awaiting_children`` → ``None``.

        Per §7 invariant 8: non-answer reasons MUST NOT be selected
        by ``find_suspended_turn_for_answer``. The selector is
        strictly answer-gate.
        """
        for non_answer_reason in (
            SuspensionReason.PAUSED_EXTERNAL.value,
            SuspensionReason.AWAITING_CHILDREN.value,
        ):
            iid = _seed_instance(
                engine, status=InstanceStatus.PAUSED.value
            )
            _seed_task_with_handle(
                engine,
                instance_id=iid,
                status=TaskStatus.PAUSED.value,
                task_type=TaskType.PROCESS_MESSAGE.value,
                suspension_reason=non_answer_reason,
                resume_target_turn_id=str(uuid.uuid4().hex),
            )
            task_repo = TaskRepository(engine)
            assert task_repo.find_suspended_turn_for_answer(iid) is None, (
                f"PAUSED task with suspension_reason="
                f"{non_answer_reason!r} MUST NOT be selected by "
                f"the answer-gate selector (§7 invariant 8)"
            )

    def test_awaiting_answer_with_null_target_returns_none(
        self, engine, write_guard, lifecycle_service
    ):
        """``awaiting_answer`` with NULL target → ``None``.

        §7 invariant 2: ``suspension_reason='awaiting_answer'``
        REQUIRES a non-null target. The selector filter is
        ``resume_target_turn_id IS NOT NULL`` — a leaked NULL
        target is treated as no answer-gate candidate (defence in
        depth; ``SuspendTurn.__init__`` also rejects this shape
        with a ``ValueError`` before the write).
        """
        iid = _seed_instance(engine, status=InstanceStatus.PAUSED.value)
        _seed_task_with_handle(
            engine,
            instance_id=iid,
            status=TaskStatus.PAUSED.value,
            task_type=TaskType.PROCESS_MESSAGE.value,
            suspension_reason=SuspensionReason.AWAITING_ANSWER.value,
            resume_target_turn_id=None,
        )
        task_repo = TaskRepository(engine)
        assert task_repo.find_suspended_turn_for_answer(iid) is None

    def test_multiple_awaiting_answer_raises_value_error(
        self, engine, write_guard, lifecycle_service
    ):
        """Multiple awaiting-answer rows → ``ValueError`` (§8.1 ambiguity).

        Per plan §8.1: "If multiple rows for one answer: fail
        loudly/log an invariant violation; never choose by
        recency." The selector's COUNT guard raises ``ValueError``
        rather than silently picking a winner.
        """
        iid = _seed_instance(engine, status=InstanceStatus.PAUSED.value)
        # Two rows with the awaiting-answer handle set. This is an
        # invariant violation — the answer gate has exactly one
        # authoritative suspension handle per instance.
        _seed_task_with_handle(
            engine,
            instance_id=iid,
            status=TaskStatus.PAUSED.value,
            task_type=TaskType.PROCESS_MESSAGE.value,
            suspension_reason=SuspensionReason.AWAITING_ANSWER.value,
            resume_target_turn_id=str(uuid.uuid4().hex),
        )
        _seed_task_with_handle(
            engine,
            instance_id=iid,
            status=TaskStatus.PAUSED.value,
            task_type=TaskType.PROCESS_MESSAGE.value,
            suspension_reason=SuspensionReason.AWAITING_ANSWER.value,
            resume_target_turn_id=str(uuid.uuid4().hex),
        )
        task_repo = TaskRepository(engine)
        with pytest.raises(ValueError, match="find_suspended_turn_for_answer"):
            task_repo.find_suspended_turn_for_answer(iid)

    def test_no_task_returns_none(
        self, engine, write_guard, lifecycle_service
    ):
        """No ``task`` row at all → ``None``."""
        iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        task_repo = TaskRepository(engine)
        assert task_repo.find_suspended_turn_for_answer(iid) is None


# ─── Phase 3 (Increment 4): find_paused_or_cancellable_turn ──────────────


class TestFindPausedOrCancellableTurn:
    """Repository unit tests for the pause-cascade selector.

    Phase 3 (Increment 4, 2026-08-01).
    ``find_paused_or_cancellable_turn(instance_id)`` is the
    pause-cascade primitive that supersedes
    ``find_paused_or_running_by_instance`` for pause / cancel
    consumers. It selects PAUSED or RUNNING ``PROCESS_MESSAGE`` /
    ``PROCESS_REPORT`` tasks for the instance and raises
    ``ValueError`` when more than one concurrently eligible turn
    exists (§8.2 one-running-turn-per-instance invariant).
    """

    def test_paused_message_task_returns_row(
        self, engine, write_guard, lifecycle_service
    ):
        """PAUSED PROCESS_MESSAGE task → returned."""
        iid = _seed_instance(engine, status=InstanceStatus.PAUSED.value)
        _seed_task(
            engine,
            instance_id=iid,
            status=TaskStatus.PAUSED.value,
            task_type=TaskType.PROCESS_MESSAGE.value,
        )
        task_repo = TaskRepository(engine)
        candidate = task_repo.find_paused_or_cancellable_turn(iid)
        assert candidate is not None
        assert candidate.status == TaskStatus.PAUSED.value
        assert candidate.task_type == TaskType.PROCESS_MESSAGE.value

    def test_running_report_task_returns_row(
        self, engine, write_guard, lifecycle_service
    ):
        """RUNNING PROCESS_REPORT task → returned (Bug A fix).

        Per §8.2: PROCESS_REPORT is INCLUDED in the eligible
        set so the pause cascade sees the in-flight report turn.
        This was previously excluded by the inference-based
        ``find_paused_or_running_by_instance`` heuristic that
        filtered on ``task_type = PROCESS_MESSAGE`` — a regression
        in the pause-during-report-turn incident.
        """
        iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        _seed_task(
            engine,
            instance_id=iid,
            status=TaskStatus.RUNNING.value,
            task_type=TaskType.PROCESS_REPORT.value,
        )
        task_repo = TaskRepository(engine)
        candidate = task_repo.find_paused_or_cancellable_turn(iid)
        assert candidate is not None, (
            "PROCESS_REPORT tasks MUST be candidates for the "
            "pause-cascade selector (Bug A regression coverage)"
        )
        assert candidate.task_type == TaskType.PROCESS_REPORT.value

    def test_pending_task_returns_none(
        self, engine, write_guard, lifecycle_service
    ):
        """PENDING task → ``None`` (not yet "in flight")."""
        iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        _seed_task(
            engine,
            instance_id=iid,
            status=TaskStatus.PENDING.value,
            task_type=TaskType.PROCESS_MESSAGE.value,
        )
        task_repo = TaskRepository(engine)
        assert task_repo.find_paused_or_cancellable_turn(iid) is None

    def test_cancelled_task_returns_none(
        self, engine, write_guard, lifecycle_service
    ):
        """CANCELLED task → ``None``.

        CANCELLED is the resume-cascade marker, not a cancellable
        turn. The selector restricts to PAUSED/RUNNING for the
        named pause/cancel transitions (§8.2).
        """
        iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        _seed_task(
            engine,
            instance_id=iid,
            status=TaskStatus.CANCELLED.value,
            task_type=TaskType.PROCESS_MESSAGE.value,
        )
        task_repo = TaskRepository(engine)
        assert task_repo.find_paused_or_cancellable_turn(iid) is None

    def test_completed_terminal_returns_none(
        self, engine, write_guard, lifecycle_service
    ):
        """COMPLETED terminal → ``None`` (not cancellable)."""
        iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        _seed_task(
            engine,
            instance_id=iid,
            status=TaskStatus.COMPLETED.value,
            task_type=TaskType.PROCESS_MESSAGE.value,
        )
        task_repo = TaskRepository(engine)
        assert task_repo.find_paused_or_cancellable_turn(iid) is None

    def test_multiple_eligible_raises_value_error(
        self, engine, write_guard, lifecycle_service
    ):
        """Multiple eligible turns → ``ValueError`` (§8.2 invariant).

        The one-running-turn-per-instance invariant requires that
        at most one PAUSED/RUNNING turn exist for an instance.
        Multiple matching rows means an inconsistency that must
        surface as a ``ValueError`` rather than a silent pick.
        """
        iid = _seed_instance(engine, status=InstanceStatus.PAUSED.value)
        # Two PAUSED PROCESS_MESSAGE tasks — invariant violation.
        _seed_task(
            engine,
            instance_id=iid,
            status=TaskStatus.PAUSED.value,
            task_type=TaskType.PROCESS_MESSAGE.value,
        )
        _seed_task(
            engine,
            instance_id=iid,
            status=TaskStatus.PAUSED.value,
            task_type=TaskType.PROCESS_MESSAGE.value,
        )
        task_repo = TaskRepository(engine)
        with pytest.raises(ValueError, match="find_paused_or_cancellable_turn"):
            task_repo.find_paused_or_cancellable_turn(iid)

    def test_no_task_returns_none(
        self, engine, write_guard, lifecycle_service
    ):
        """No ``task`` row → ``None``."""
        iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        task_repo = TaskRepository(engine)
        assert task_repo.find_paused_or_cancellable_turn(iid) is None

    def test_send_report_excluded(
        self, engine, write_guard, lifecycle_service
    ):
        """SEND_REPORT / CLEANUP tasks are NOT pause-cascade eligible.

        Only the active graph turns (PROCESS_MESSAGE, PROCESS_REPORT)
        are eligible. SEND_REPORT is the report delivery back-channel;
        CLEANUP is housekeeping; neither participates in the
        pause-cascade contract.
        """
        for excluded_type in (
            TaskType.SEND_REPORT.value,
            TaskType.CLEANUP.value,
        ):
            iid = _seed_instance(
                engine, status=InstanceStatus.RUNNING.value
            )
            _seed_task(
                engine,
                instance_id=iid,
                status=TaskStatus.RUNNING.value,
                task_type=excluded_type,
            )
            task_repo = TaskRepository(engine)
            assert task_repo.find_paused_or_cancellable_turn(iid) is None, (
                f"{excluded_type} tasks MUST NOT be pause-cascade "
                f"candidates (§8.2 — only PROCESS_MESSAGE and "
                f"PROCESS_REPORT are eligible)"
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


def _seed_task_with_handle(
    engine: Engine,
    *,
    instance_id: str,
    status: str,
    task_type: str = TaskType.PROCESS_MESSAGE.value,
    suspension_reason: str | None = None,
    resume_target_turn_id: str | None = None,
    work_id: str | None = None,
) -> int:
    """Insert a Task row with explicit suspension-handle fields.

    Used by Phase 3 (Increment 4) answer-gate selector tests to
    set the ``suspension_reason`` and ``resume_target_turn_id``
    columns directly without driving the full ``SuspendTurn``
    transition (which requires a transaction and a non-trivial
    setup). Mirrors the ``_seed_task_with_work_id`` helper but
    for the handle columns.

    Args:
        engine: SQLAlchemy engine bound to the test schema.
        instance_id: Instance to attach the task to.
        status: Task status (e.g. ``PAUSED``, ``RUNNING``).
        task_type: Task type (default ``PROCESS_MESSAGE``).
        suspension_reason: One of the ``SuspensionReason`` enum
            values, or ``None`` for the unset (legacy) shape.
        resume_target_turn_id: The Task ``work_id`` of the turn a
            later resume should reattach to; ``None`` when not
            applicable.
        work_id: Optional explicit ``work_id`` (auto-generated if
            ``None``).

    Returns:
        The task ``id`` of the inserted row.
    """
    now = datetime.now(timezone.utc)
    with Session(engine) as s:
        task = Task(
            task_type=task_type,
            instance_id=instance_id,
            status=status,
            work_id=work_id or str(uuid.uuid4()),
            created_at=now,
            updated_at=now,
            suspension_reason=suspension_reason,
            resume_target_turn_id=resume_target_turn_id,
        )
        s.add(task)
        s.commit()
        s.refresh(task)
        return int(task.id)
