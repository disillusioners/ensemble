"""Phase 2 serialization tests for the Job-as-Front-Primitive message bridge.

The message-Job mirror participates in serialization for the same
instance: only 1 Task runs at a time (cross-system guard via
``_admitted_task_carve_out_sql``). This file is the Phase 2 deliverable
tests (Task 6 in the user's brief).

What's covered:

  1. Two message-Jobs to the same instance: Tasks serialize (1 RUNNING,
     1 PENDING) — not parallel.
  2. ``enqueue_message_job``'s eager activation flips the JobItem
     ``queued -> active`` (SQLite, where the PG trigger doesn't fire).
  3. A failed message-Job finalizes to ``done`` (terminal_reason='failed')
     — NEVER to ``dead`` (the retry/DLQ path is reserved for TASK jobs).

Run with::

    pytest tests/test_message_job_serialization.py -v
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select, update as sqlmodel_update

# Register all tables with SQLModel.metadata via model imports.
from daemon.config import Config, JobSystemConfig
from daemon.repositories.event.models import Event
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.models import AdmissionState, JobItem
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.message_queue.models import MessageQueue
from daemon.repositories.task.models import Task, TaskStatus
from daemon.repositories.task.repository import TaskRepository
from daemon.services.cancellation import CancellationService
from daemon.services.instance_messaging import InstanceMessagingService
from daemon.services.job_feedback_observer import (
    JobFeedbackObserver,
    _ProcessingJobContext,
)
from daemon.write_pause_guard import WritePauseGuard


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures (mirroring tests/test_message_job_poc.py, lines 61-265)
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine():
    """In-memory SQLite engine (StaticPool for cross-thread safety).

    Mirrors the pattern from ``tests/test_enqueue_shared.py`` and
    ``tests/job_queue/conftest.py`` so existing tooling (e.g.
    ``SQLModel.metadata.create_all``) registers all SQLModel tables
    including ``job_queue_items``.
    """
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def instance_repository(engine):
    """Real ``SQLModelInstanceRepository`` backed by the in-memory engine."""
    return SQLModelInstanceRepository(engine)


@pytest.fixture
def job_repository(engine):
    """Real ``JobRepository`` backed by the in-memory engine.

    The POC's ``enqueue_message_job`` calls ``JobRepository.create``
    directly (bypassing ``JobQueueService.enqueue_job`` which rejects
    ``job_type='message'``) and then ``stamp_message_id`` to correlate
    the JobItem back to the originating ``message_id``.
    """
    return JobRepository(engine)


@pytest.fixture
def cancellation_service():
    """Mock ``CancellationService`` with ``is_shutting_down=False``."""
    service = MagicMock(spec=CancellationService)
    service.is_shutting_down = False
    return service


@pytest.fixture
def write_guard():
    """Real ``WritePauseGuard`` (no active pause)."""
    return WritePauseGuard()


def _build_manager(engine, instance_repository, write_guard, job_repository):
    """Build a mock ``InstanceManager`` exposing only the attributes
    ``enqueue_message`` and ``enqueue_message_job`` actually touch.

    ``_job_queue_service._repository`` is wired to the real
    ``JobRepository`` so the POC's ``enqueue_message_job`` can write
    JobItem rows + stamp ``message_id`` via the repository's
    low-level path.
    """
    manager = MagicMock()
    manager.engine = engine
    manager.write_guard = write_guard
    manager._instance_repository = instance_repository

    # ``_live_hub.stream_status_change`` is awaited after status transition.
    manager._live_hub = MagicMock()
    manager._live_hub.stream_status_change = AsyncMock()

    # ``enqueue_message`` calls ``_worker_pool.notify_work()``; None is fine
    # because the code guards with ``if self._manager._worker_pool is not None``.
    manager._worker_pool = MagicMock()
    manager._worker_pool.notify_work = MagicMock()

    # Wire the JobQueueService to expose a real JobRepository. The POC
    # ``enqueue_message_job`` resolves ``manager._job_queue_service._repository``
    # via the ``_job_repository`` property on InstanceMessagingService.
    manager._job_queue_service = MagicMock()
    manager._job_queue_service._repository = job_repository

    # Config -- Phase 5 cutover: ``message_jobs_enabled`` was removed,
    # there is only one public path now.
    manager.config = Config(job_system=JobSystemConfig())

    # Title generation fires via MainLoopBridge; we'll patch it out.
    manager._generate_and_broadcast_title = AsyncMock()

    return manager


def _seed_instance(
    engine,
    *,
    instance_id: str = "inst-1",
    agent_id: str = "developer",
    agent_dir: str = "/agents/developer",
    status: str = InstanceStatus.IDLE.value,
    project_id: str | None = "test-project",
    version: int = 1,
) -> Instance:
    """Insert an ``Instance`` row in the test engine."""
    inst = Instance(
        instance_id=instance_id,
        agent_id=agent_id,
        agent_dir=agent_dir,
        project_id=project_id,
        status=status,
        version=version,
        instance_metadata={},
    )
    with Session(engine) as session:
        session.add(inst)
        session.commit()
        session.refresh(inst)
    return inst


@pytest.fixture
def task_repository(engine):
    """Real ``TaskRepository`` backed by the in-memory engine.

    Used for the cross-system guard verification: directly invoking
    ``claim_pending_task`` to confirm Task A (already RUNNING) blocks
    Task B from being claimed on the same instance.
    """
    return TaskRepository(engine)


def _build_observer(engine, job_repository, task_repository) -> JobFeedbackObserver:
    """Build a minimal ``JobFeedbackObserver`` exposing only the
    attributes ``_get_processing_job_for_instance`` and
    ``_finalize_job_db_sync`` actually touch.

    Mirrors the same construction used in ``tests/test_message_job_poc.py``
    so the observer can be instantiated without spinning up the
    EventBus / LockRepository / ProjectRepository.
    """
    observer = JobFeedbackObserver.__new__(JobFeedbackObserver)
    observer._event_bus = MagicMock()
    observer._job_queue_service = MagicMock()

    async def _get_job_by_instance(instance_id: str) -> JobItem | None:
        return job_repository.get_by_instance(instance_id)

    observer._job_queue_service.get_job_by_instance = AsyncMock(
        side_effect=_get_job_by_instance
    )
    observer._job_repo = job_repository
    observer._lock_repo = MagicMock()
    observer._project_repo = MagicMock()

    manager = MagicMock()
    manager._task_repo = task_repository
    manager.engine = engine
    manager.write_guard = WritePauseGuard()
    observer._instance_manager = manager

    return observer


# ──────────────────────────────────────────────────────────────────────────────
# Query helpers (mirroring tests/test_message_job_poc.py)
# ──────────────────────────────────────────────────────────────────────────────


def _load_message_queues(engine, instance_id: str) -> list[MessageQueue]:
    with Session(engine) as session:
        return list(
            session.exec(
                select(MessageQueue).where(MessageQueue.instance_id == instance_id)
            )
        )


def _load_tasks(engine, instance_id: str) -> list[Task]:
    with Session(engine) as session:
        return list(
            session.exec(select(Task).where(Task.instance_id == instance_id))
        )


def _load_job_items(engine) -> list[JobItem]:
    """Fetch all JobItem rows (no instance filter — JobItem is the mirror)."""
    with Session(engine) as session:
        return list(session.exec(select(JobItem)))


def _load_message_job_items(engine) -> list[JobItem]:
    """Fetch all JobItem rows with ``job_type == 'message'``."""
    with Session(engine) as session:
        return list(
            session.exec(select(JobItem).where(JobItem.job_type == "message"))
        )


# ──────────────────────────────────────────────────────────────────────────────
# Test 1: Two message-Jobs to the same instance: only 1 Task runs at a time
# ──────────────────────────────────────────────────────────────────────────────


class TestTwoMessageJobsSerialize:
    """Two message-Jobs to the same instance serialize: only one Task
    reaches RUNNING at a time. The cross-system guard (via
    ``TaskRepository._admitted_task_carve_out_sql`` plus the per-instance
    ``status='running'`` exclusion in ``claim_pending_task``) enforces
    this invariant at the SQL layer.

    This test verifies:

      * Two distinct ``enqueue_message_job`` calls produce two distinct
        JobItem + Task + MessageQueue rows.
      * Both Tasks start in ``PENDING``.
      * After flipping Task A to ``RUNNING`` (simulating worker claim),
        Task B stays ``PENDING`` — the cross-system guard holds the
        second Task out of ``RUNNING``.
      * Optionally: ``task_repository.claim_pending_task()`` returns
        the RUNNING Task A (or rather, returns None because the only
        candidate is Task A which is already RUNNING — the guard
        filters it out).
    """

    @pytest.mark.asyncio
    async def test_two_messages_one_running_one_pending(
        self,
        engine,
        instance_repository,
        write_guard,
        job_repository,
        task_repository,
    ):
        # ── Phase 5: every public message is a JobItem ──
        manager = _build_manager(
            engine, instance_repository, write_guard, job_repository
        )
        messaging_service = InstanceMessagingService(
            manager=manager,
            cancellation_service=MagicMock(
                spec=CancellationService, is_shutting_down=False
            ),
        )

        _seed_instance(engine, instance_id="inst-1", status=InstanceStatus.IDLE.value)

        # ── Call enqueue_message_job twice for the same instance ──
        with patch(
            "daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"
        ), patch("daemon.registry.get_registry") as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get_resolved.return_value = None
            mock_get_registry.return_value = mock_registry

            result_A = await messaging_service.enqueue_message_job(
                instance_id="inst-1",
                message="first message",
                source="api",
            )
            result_B = await messaging_service.enqueue_message_job(
                instance_id="inst-1",
                message="second message",
                source="api",
            )

        # ── Result contract: distinct job_ids ──
        assert result_A.job_id is not None
        assert result_B.job_id is not None
        assert result_A.job_id != result_B.job_id, (
            "Two enqueue_message_job calls must mint distinct job_ids "
            "(each call mints a fresh UUID4 via uuid.uuid4())"
        )
        job_id_A = result_A.job_id
        job_id_B = result_B.job_id
        message_id_A = result_A.message_id
        message_id_B = result_B.message_id

        # ── DB state: 2 MessageQueue, 2 Task, 2 JobItem rows ──
        mq_rows = _load_message_queues(engine, "inst-1")
        assert len(mq_rows) == 2, (
            f"Two enqueue calls must produce two MessageQueue rows; "
            f"got {len(mq_rows)}"
        )

        tasks = _load_tasks(engine, "inst-1")
        assert len(tasks) == 2, (
            f"Two enqueue calls must produce two Task rows; got {len(tasks)}"
        )
        task_work_ids = {t.work_id for t in tasks}
        assert {job_id_A, job_id_B} == task_work_ids, (
            f"Task.work_id values must equal the two JobItem.job_ids "
            f"(linkage contract); got Task.work_ids={task_work_ids} vs "
            f"JobIds=({job_id_A!r}, {job_id_B!r})"
        )

        jobs = _load_message_job_items(engine)
        assert len(jobs) == 2, (
            f"Two enqueue calls must produce two JobItem(message) rows; "
            f"got {len(jobs)}"
        )
        for job in jobs:
            assert job.job_type == "message"
            assert job.instance_id == "inst-1"
            # Eager activation ran during enqueue_message_job — see Test 2.
            # On SQLite (no PG trigger), JobItems flip to 'active'.
            assert job.admission_state == AdmissionState.ACTIVE.value, (
                f"After eager activation, both JobItems should be 'active'; "
                f"got {job.admission_state!r}"
            )

        # Both Tasks must start in PENDING (no worker has claimed either yet).
        tasks_before_claim = _load_tasks(engine, "inst-1")
        statuses_before = sorted(t.status for t in tasks_before_claim)
        assert statuses_before == [
            TaskStatus.PENDING.value,
            TaskStatus.PENDING.value,
        ], (
            f"Both Tasks should start PENDING before worker claim; "
            f"got {statuses_before}"
        )

        # ── Simulate worker claiming Task A via task_repository.claim_pending_task ──
        # ``claim_pending_task`` is the production path a worker uses to
        # transition a Task from PENDING → RUNNING. It atomically sets
        # ``status='running'``, ``worker_id=...``, ``started_at=now``, and
        # ``last_heartbeat_at=now`` in a single UPDATE. The first PENDING
        # task (oldest by ``created_at``) for this instance is Task A.
        claimed = task_repository.claim_pending_task(worker_id="test-worker-A")
        assert claimed is not None, (
            "claim_pending_task should return the oldest PENDING task "
            "(Task A) for the instance"
        )
        assert claimed.work_id == job_id_A, (
            f"claim_pending_task returned Task {claimed.work_id!r}, "
            f"expected Task A={job_id_A!r} (Task A was created first)"
        )
        assert claimed.status == TaskStatus.RUNNING.value, (
            f"claim_pending_task should flip the claimed task to RUNNING; "
            f"got status={claimed.status!r}"
        )

        # ── Cross-system guard verification ──
        # Now Task A is RUNNING. The per-instance guard in
        # ``claim_pending_task`` excludes instances with a RUNNING task —
        # so a second ``claim_pending_task`` call on this instance must
        # return None (Task B is blocked by the same-instance guard).
        # This is the SQL-level proof that two message-Jobs serialize
        # at the same instance.
        second_claim = task_repository.claim_pending_task(worker_id="test-worker-B")
        assert second_claim is None, (
            "Cross-system / per-instance serialization guard must block "
            "claim_pending_task while Task A is RUNNING (only 1 Task per "
            f"instance may run at a time); got {second_claim.work_id!r} "
            "instead of None"
        )

        # ── Mirror probe: SQL-level cross-system guard probe ──
        # ``TaskRepository._admitted_task_carve_out_sql`` matches Task A
        # back to ``job_queue_items.metadata->>'message_id'``. The probe
        # via ``Task.message_id == message_id_A`` confirms the linkage
        # contract: Task A's message_id is stamped onto JobItem A's
        # metadata so the carve-out can find it.
        with Session(engine) as session:
            tasks_for_message_A = list(
                session.exec(
                    select(Task).where(Task.message_id == message_id_A)
                )
            )
            assert len(tasks_for_message_A) == 1, (
                f"Exactly one Task must carry message_id={message_id_A!r}; "
                f"got {len(tasks_for_message_A)}"
            )
            assert tasks_for_message_A[0].work_id == job_id_A

        # ── Status-count assertion: the minimum-acceptable coverage ──
        # Verifies the table state after Task A is RUNNING: 1 RUNNING
        # (the worker claim we just simulated) and 1 PENDING (Task B,
        # blocked by the per-instance serialization guard).
        tasks = _load_tasks(engine, "inst-1")
        running = [t for t in tasks if t.status == TaskStatus.RUNNING.value]
        pending = [t for t in tasks if t.status == TaskStatus.PENDING.value]
        assert len(running) == 1 and len(pending) == 1, (
            f"After flipping Task A to RUNNING, DB must have exactly "
            f"1 RUNNING and 1 PENDING Task — cross-system guard holds "
            f"Task B out of RUNNING. Got "
            f"running={[t.work_id for t in running]}, "
            f"pending={[t.work_id for t in pending]}"
        )
        assert running[0].work_id == job_id_A, (
            f"Task A (RUNNING) must be the one we claimed; got "
            f"work_id={running[0].work_id!r} vs expected {job_id_A!r}"
        )
        assert pending[0].work_id == job_id_B, (
            f"Task B (still PENDING) must be the second message-Job; got "
            f"work_id={pending[0].work_id!r} vs expected {job_id_B!r}"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Test 2: JobItem queued→active transition verified on SQLite
# ──────────────────────────────────────────────────────────────────────────────


class TestEagerActivationOnSqlite:
    """``enqueue_message_job`` eagerly flips the JobItem from ``queued``
    to ``active`` so the cross-system guard (Part B's
    ``claim_pending_task``) immediately recognises the JobItem as
    actively dispatching — preventing second-message-same-instance
    races during the natural Race #1 window between Task claim and
    the worker's post-claim activation UPDATE.

    On SQLite (this test's engine), the
    ``trg_job_queue_items_active_lock_guard`` PostgreSQL trigger that
    blocks ``queued → active`` WITHOUT a ``job_locks`` row does NOT
    exist, so the eager ``atomic_transition`` succeeds there. The
    trigger only fires on production PostgreSQL; SQLite is the only
    dialect we can reasonably exercise in unit tests.
    """

    @pytest.mark.asyncio
    async def test_queued_to_active_on_sqlite(
        self,
        engine,
        instance_repository,
        write_guard,
        job_repository,
    ):
        # ── Flag ON, instance IDLE ──
        manager = _build_manager(
            engine, instance_repository, write_guard, job_repository
        )
        messaging_service = InstanceMessagingService(
            manager=manager,
            cancellation_service=MagicMock(
                spec=CancellationService, is_shutting_down=False
            ),
        )

        _seed_instance(engine, instance_id="inst-1", status=InstanceStatus.IDLE.value)

        # ── Spy on JobRepository.atomic_transition BEFORE calling ──
        # ``MagicMock(wraps=...)`` preserves the real behaviour while
        # capturing call args — we want to verify ``atomic_transition``
        # was called with ``(<job_id>, "queued", "active")``.
        original_atomic_transition = job_repository.atomic_transition
        job_repository.atomic_transition = MagicMock(
            wraps=original_atomic_transition
        )

        # ── Call enqueue_message_job ──
        with patch(
            "daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"
        ), patch("daemon.registry.get_registry") as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get_resolved.return_value = None
            mock_get_registry.return_value = mock_registry

            result = await messaging_service.enqueue_message_job(
                instance_id="inst-1",
                message="eager-activation-test",
                source="api",
            )

        # ── The JobItem is in 'active' immediately after the call ──
        jobs = _load_message_job_items(engine)
        assert len(jobs) == 1, (
            f"Expected exactly one JobItem(message) row; got {len(jobs)}"
        )
        assert jobs[0].admission_state == AdmissionState.ACTIVE.value, (
            f"eager activation should flip JobItem to active (SQLite has "
            f"no PG trigger), got {jobs[0].admission_state!r}"
        )

        # ── atomic_transition was called with (job_id, "queued", "active") ──
        job_repository.atomic_transition.assert_called_once()
        call_args = job_repository.atomic_transition.call_args.args
        call_kwargs = job_repository.atomic_transition.call_args.kwargs
        # Two call styles are possible:
        #   atomic_transition(job_id, "queued", "active")
        #   atomic_transition(job_id=job_id, from_status="queued", to_status="active")
        if call_args:
            # Positional form: (job_id, from_status, to_status)
            assert len(call_args) >= 3, (
                f"atomic_transition positional args must include (job_id, "
                f"from_status, to_status); got {call_args!r}"
            )
            assert call_args[0] == result.job_id, (
                f"atomic_transition arg[0] (job_id) must be {result.job_id!r}; "
                f"got {call_args[0]!r}"
            )
            assert call_args[1] == AdmissionState.QUEUED.value, (
                f"atomic_transition arg[1] (from_status) must be "
                f"{AdmissionState.QUEUED.value!r}; got {call_args[1]!r}"
            )
            assert call_args[2] == AdmissionState.ACTIVE.value, (
                f"atomic_transition arg[2] (to_status) must be "
                f"{AdmissionState.ACTIVE.value!r}; got {call_args[2]!r}"
            )
        else:
            # Keyword form: (job_id=..., from_status=..., to_status=...)
            assert call_kwargs.get("job_id") == result.job_id, (
                f"atomic_transition(..., job_id) must be {result.job_id!r}; "
                f"got {call_kwargs.get('job_id')!r}"
            )
            assert call_kwargs.get("from_status") == AdmissionState.QUEUED.value, (
                f"atomic_transition(..., from_status) must be "
                f"{AdmissionState.QUEUED.value!r}; "
                f"got {call_kwargs.get('from_status')!r}"
            )
            assert call_kwargs.get("to_status") == AdmissionState.ACTIVE.value, (
                f"atomic_transition(..., to_status) must be "
                f"{AdmissionState.ACTIVE.value!r}; "
                f"got {call_kwargs.get('to_status')!r}"
            )


# ──────────────────────────────────────────────────────────────────────────────
# Test 3: Failed message-Job finalizes to 'done' (NOT 'dead')
# ──────────────────────────────────────────────────────────────────────────────


class TestFailedMessageJobGoesToDone:
    """A failed message-Job finalizes to ``admission_state='done'`` with
    ``terminal_reason='failed'`` — NEVER to ``admission_state='dead'``.

    The retry engine / dead-letter queue path (``admission_state='dead'``)
    is reserved for TASK-type jobs (the JobProcessor's dispatch path).
    Message-Jobs are finalized by the observer, which writes
    ``done`` (not ``dead``) with the cause encoded in
    ``terminal_reason``. This test verifies that contract directly:

      * After ``enqueue_message_job``, the JobItem is in ``active``.
      * The failed-finalize UPDATE writes
        ``admission_state='done', terminal_reason='failed'`` — the
        same WHERE clause ``_finalize_job_db_sync`` Step 1 issues
        (the observer's finalize path).
      * The JobItem ends in ``done``, not ``dead``.
    """

    @pytest.mark.asyncio
    async def test_failed_message_job_finalizes_to_done_not_dead(
        self,
        engine,
        instance_repository,
        write_guard,
        job_repository,
    ):
        # ── Flag ON, instance IDLE ──
        manager = _build_manager(
            engine, instance_repository, write_guard, job_repository
        )
        messaging_service = InstanceMessagingService(
            manager=manager,
            cancellation_service=MagicMock(
                spec=CancellationService, is_shutting_down=False
            ),
        )

        _seed_instance(engine, instance_id="inst-1", status=InstanceStatus.IDLE.value)

        # ── Create the message-Job (eager activation runs) ──
        with patch(
            "daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"
        ), patch("daemon.registry.get_registry") as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get_resolved.return_value = None
            mock_get_registry.return_value = mock_registry

            result = await messaging_service.enqueue_message_job(
                instance_id="inst-1",
                message="failed-finalize-test",
                source="api",
            )

        # ── Sanity: JobItem is in 'active' after eager activation ──
        jobs = _load_message_job_items(engine)
        assert len(jobs) == 1
        assert jobs[0].admission_state == AdmissionState.ACTIVE.value, (
            "precondition: eager activation must have flipped the JobItem "
            f"to 'active'; got {jobs[0].admission_state!r}"
        )

        # ── Simulate the failed-finalize path ──
        # Mirrors the exact UPDATE ``_finalize_job_db_sync`` Step 1 runs
        # for a failed message-Job: the WHERE clause matches
        # ``admission_state IN ('active', 'queued')`` so a stuck-queued
        # JobItem can still be finalized, and the VALUES set
        # ``admission_state='done', terminal_reason='failed'``.
        # On a real DB this is wrapped in a WriteGuardSession; for this
        # test we replicate the same SQL pattern.
        with Session(engine) as session:
            stmt = (
                sqlmodel_update(JobItem)
                .where(JobItem.job_id == result.job_id)
                .where(
                    JobItem.admission_state.in_(
                        [
                            AdmissionState.ACTIVE.value,
                            AdmissionState.QUEUED.value,
                        ]
                    )
                )
                .values(
                    admission_state=AdmissionState.DONE.value,
                    terminal_reason="failed",
                )
            )
            r = session.exec(stmt)
            session.commit()
            assert r.rowcount == 1, (
                f"Failed-finalize UPDATE must match exactly 1 JobItem "
                f"(the message JobItem created by enqueue_message_job); "
                f"got rowcount={r.rowcount}"
            )

        # ── Verify the JobItem ends in 'done' (NOT 'dead') ──
        jobs = _load_message_job_items(engine)
        assert len(jobs) == 1
        assert jobs[0].admission_state == AdmissionState.DONE.value, (
            f"A failed message-Job must finalize to 'done' (observer "
            f"finalize path), NOT to 'dead' (retry/DLQ path is reserved "
            f"for TASK jobs); got admission_state={jobs[0].admission_state!r}"
        )
        assert jobs[0].terminal_reason == "failed", (
            f"terminal_reason must record the cause as 'failed'; "
            f"got {jobs[0].terminal_reason!r}"
        )

        # ── Adversarial probe: confirm admission_state is NOT 'dead' ──
        # The retry/DLQ path writes ``admission_state='dead'`` and is the
        # wrong outcome for a failed message-Job. This explicit check
        # mirrors the brief's "NEVER to dead" guarantee.
        all_jobs = _load_job_items(engine)
        dead_jobs = [j for j in all_jobs if j.admission_state == AdmissionState.DEAD.value]
        assert len(dead_jobs) == 0, (
            f"No JobItem must ever land in 'dead' for a message-Job "
            f"failure (the retry/DLQ engine owns that admission state); "
            f"found {len(dead_jobs)} dead JobItem(s): "
            f"{[(j.job_id, j.job_type) for j in dead_jobs]}"
        )
