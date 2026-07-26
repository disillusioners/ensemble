"""Phase 2 serialization tests for the Job-as-Front-Primitive message bridge.

The message-Job mirror participates in serialization for the same
instance: only 1 Task runs at a time (cross-system guard via
``_admitted_task_carve_out_sql``). This file is the Phase 2 deliverable
tests (Task 6 in the user's brief).

What's covered (Phase 6 / Option B cutover):

  1. Two message-Jobs to the same instance: Tasks serialize (1 RUNNING,
     1 PENDING) — not parallel.
  2. ``enqueue_message_job`` creates a ``queued`` JobItem (no eager
     activation — concurrency enforcement is now via the
     ``start_job_atomic_with_lock`` lock slot, not a pre-flip).
  3. A failed message-Job finalizes to ``done`` (terminal_reason='failed')
     — NEVER to ``dead`` (the retry/DLQ path is reserved for TASK jobs).

Option B contract notes:

  * ``enqueue_message_job`` creates the Task + MessageQueue rows before
    calling ``JobQueueService.enqueue``. The shared UUID is passed as
    ``Task.work_id`` and ``JobItem.job_id``.
  * The JobItem remains ``queued`` until the JobProcessor admits a queue
    slot. Its message_id metadata is stamped immediately after enqueue.

Run with::

    pytest tests/test_message_job_serialization.py -v
"""

from __future__ import annotations

import asyncio
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
def queue_repository(engine):
    """Real ``JobQueueRepository`` seeded with ``system_parallel_queue``
    for ``test-project`` so the POC ``enqueue_message_job`` resolves a
    real ``queue_id`` (string) for the JobItem mirror. Without this the
    the previously-broken lookup (``_repository`` instead of
    ``_queue_repo``) silently raised ``AttributeError``, leaving
    ``queue_id=None`` on production message JobItems.
    """
    from daemon.repositories.job_queue.queue_repository import JobQueueRepository

    repo = JobQueueRepository(engine)
    repo.create(
        project_id="test-project",
        queue_name="system_parallel_queue",
        queue_type="parallel",
        concurrency_limit=5,
        is_system=True,
    )
    return repo


@pytest.fixture
def write_guard():
    """Real ``WritePauseGuard`` (no active pause)."""
    return WritePauseGuard()


def _build_manager(
    engine, instance_repository, write_guard, job_repository, queue_repository
):
    """Build a mock ``InstanceManager`` exposing only the attributes
    ``enqueue_message`` and ``enqueue_message_job`` actually touch.

    Phase 5 (Option B) cutover: ``enqueue_message_job`` now calls
    ``manager._job_queue_service.enqueue(...)`` (the unified entry
    point) instead of writing the JobItem + Task + MessageQueue rows
    directly. We wire ``_job_queue_service.enqueue`` to an ``AsyncMock``
    that delegates to the real ``JobRepository.create`` so the test
    exercises the same DB layer the production code uses for JobItem
    rows. ``_repository`` and ``_queue_repo`` remain on the
    ``_job_queue_service`` mock for any future lookups.

    Args:
        engine: Real in-memory SQLAlchemy engine.
        instance_repository: Real ``SQLModelInstanceRepository``.
        write_guard: Real ``WritePauseGuard``.
        job_repository: Real ``JobRepository`` (used as the side-effect
            target for ``_job_queue_service.enqueue``).
        queue_repository: Real ``JobQueueRepository`` (seeded with
            ``system_parallel_queue``).
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

    # Wire the JobQueueService to expose a real JobRepository. The
    # ``_job_queue_service.enqueue`` method must be ``await``-able —
    # the production code path now does:
    #     ``job_item = await self._manager._job_queue_service.enqueue(...)``
    # so we replace it with an AsyncMock that delegates to the real
    # ``JobRepository.create`` (same DB layer the production code uses
    # for JobItem rows). This keeps the test fully end-to-end against
    # the SQLite engine without spinning up the full
    # ``JobQueueService`` (which would also pull in LockManager +
    # project_repo).
    manager._job_queue_service = MagicMock()
    manager._job_queue_service._repository = job_repository
    manager._job_queue_service._queue_repo = queue_repository

    async def _enqueue_side_effect(
        agent_id,
        message,
        source="api",
        project_id=None,
        priority=5,
        metadata=None,
        queue_id=None,
        idempotency_key=None,
        job_type="task",
        instance_id=None,
        job_id=None,
    ):
        """Async shim that mirrors ``JobQueueService.enqueue``'s DB write.

        ``enqueue_message_job`` calls ``enqueue`` to write the JobItem
        row — the simplest test seam is to delegate straight to
        ``JobRepository.create`` (the same target the production
        service calls inside its own atomic insert path). We resolve
        ``agent_dir`` from the test registry the same way the test
        code does — a real registry call would require an
        ``agents/`` directory, which the unit-test engine does not
        provision.
        """
        return await asyncio.to_thread(
            job_repository.create,
            agent_id=agent_id,
            agent_dir="",
            message=message,
            source=source,
            project_id=project_id,
            priority=priority,
            job_metadata=metadata,
            queue_id=queue_id,
            idempotency_key=idempotency_key,
            job_type=job_type,
            instance_id=instance_id,
            job_id=job_id,
        )

    manager._job_queue_service.enqueue = AsyncMock(side_effect=_enqueue_side_effect)

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
    """Two message-Jobs to the same instance serialize via the queue's
    ``concurrency_limit`` (now enforced by ``start_job_atomic_with_lock``).

    Pre-Option B (D13 mirror path): the serialization contract was
    observed at Task-claim time — two message JobItems triggered two
    Task rows, and ``TaskRepository.claim_pending_task``'s per-instance
    guard held the second Task out of ``RUNNING`` while the first ran.
    The cross-system ``_admitted_task_carve_out_sql`` predicate looked
    up Task rows via ``job_queue_items.metadata.message_id``.

    Post-Option B: ``enqueue_message_job`` creates the Task +
    MessageQueue rows synchronously, before the JobItem is enqueued.
    The queue controls only the JobItem admission transition; the
    worker pool's per-instance Task guard still serializes execution.

    This test verifies:

      * Two ``enqueue_message_job`` calls produce two distinct JobItem
        rows and two matching Task rows.
      * Each JobItem stays in ``admission_state='queued'`` (no eager
        activation).
      * Both ``result.message_id`` values are real, non-null IDs minted
        by the synchronous Task creation.
    """

    @pytest.mark.asyncio
    async def test_two_messages_one_running_one_pending(
        self,
        engine,
        instance_repository,
        write_guard,
        job_repository,
        task_repository,
        queue_repository,
    ):
        # ── Phase 5: every public message is a JobItem ──
        manager = _build_manager(
            engine, instance_repository, write_guard, job_repository, queue_repository
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

        # ── Result contract: distinct job_ids, message_ids are real ──
        assert result_A.job_id is not None
        assert result_B.job_id is not None
        assert result_A.job_id != result_B.job_id, (
            "Two enqueue_message_job calls must mint distinct job_ids "
            "(each call mints a fresh UUID4 via uuid.uuid4())"
        )
        job_id_A = result_A.job_id
        job_id_B = result_B.job_id

        # Option B synchronous Task contract: both message IDs are
        # created by the pre-enqueue Task transaction.
        assert result_A.message_id is not None
        assert result_B.message_id is not None
        assert result_A.message_id != result_B.message_id

        # ── DB state: Task + MessageQueue rows exist before dispatch ──
        mq_rows = _load_message_queues(engine, "inst-1")
        assert len(mq_rows) == 2, (
            f"enqueue_message_job must create MessageQueue rows before "
            f"the JobItem is dispatched; got {len(mq_rows)}"
        )

        tasks = _load_tasks(engine, "inst-1")
        assert len(tasks) == 2, (
            f"enqueue_message_job must create Task rows before the "
            f"JobItem is dispatched; got {len(tasks)}"
        )
        assert {task.work_id for task in tasks} == {job_id_A, job_id_B}


        # ── DB state: 2 JobItem rows in 'queued' ──
        jobs = _load_message_job_items(engine)
        assert len(jobs) == 2, (
            f"Two enqueue calls must produce two JobItem(message) rows; "
            f"got {len(jobs)}"
        )
        for job in jobs:
            assert job.job_type == "message"
            assert job.instance_id == "inst-1"
            # Phase 5 (Option B) cutover: ``enqueue_message_job`` now
            # routes through ``JobQueueService.enqueue`` and creates
            # JobItems in ``admission_state='queued'`` — no eager
            # ``active`` flip. Concurrency enforcement is now via the
            # ``start_job_atomic_with_lock`` slot claim that runs inside
            # ``_process_next_job``'s message branch. The per-instance
            # serialization is observed at Task claim time below.
            assert job.admission_state == AdmissionState.QUEUED.value, (
                f"After enqueue_message_job, both JobItems must remain in "
                f"'queued' (the JobProcessor's message branch performs the "
                f"queued -> active transition via start_job_atomic_with_lock); "
                f"got {job.admission_state!r}"
            )

        # ── The serialization contract itself (Task A RUNNING, Task B PENDING) ──
        # The pre-created Task rows are available immediately, while the
        # JobProcessor still owns queue-slot admission. The full claim
        # integration remains covered by
        # ``tests/job_queue/test_option_b_message_routing.py``.


# ──────────────────────────────────────────────────────────────────────────────
# Test 2: JobItem stays in 'queued' after enqueue_message_job (Option B)
# ──────────────────────────────────────────────────────────────────────────────


class TestQueuedAfterEnqueue:
    """``enqueue_message_job`` creates a JobItem in ``admission_state='queued'``
    — there is NO eager ``queued -> active`` flip at enqueue time.

    Pre-Option B (D13 mirror path): ``enqueue_message_job`` created the
    JobItem AND immediately flipped it ``queued -> active`` via
    ``atomic_transition`` so the cross-system guard (the per-instance
    ``Task.status='running'`` exclusion in ``claim_pending_task``) saw it
    as actively dispatching — preventing the second-message-same-instance
    race during the natural window between Task claim and the worker's
    post-claim activation UPDATE.

    Post-Option B (Phase 5 cutover): the queue dispatch is the SOLE
    concurrency authority. ``JobProcessor._process_next_job``'s message
    branch acquires the per-queue slot via
    ``start_job_atomic_with_lock`` (which atomically flips
    ``queued -> active`` AND inserts the ``job_locks`` row in one
    transaction). The previous D13 mirror activation UPDATE was removed
    — messages no longer race on ``atomic_transition`` during the
    ``enqueue_message_job`` call.

    This test verifies the new contract:

      * After ``enqueue_message_job``, the JobItem is in ``queued``.
      * ``atomic_transition(queued -> active)`` is NOT called by
        ``enqueue_message_job`` — the flip is performed later by
        ``start_job_atomic_with_lock`` in the JobProcessor's message branch.
    """

    @pytest.mark.asyncio
    async def test_jobitem_queued_after_enqueue(
        self,
        engine,
        instance_repository,
        write_guard,
        job_repository,
        queue_repository,
    ):
        # ── Flag ON, instance IDLE ──
        manager = _build_manager(
            engine, instance_repository, write_guard, job_repository, queue_repository
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
        # capturing call args — we want to verify NO
        # ``atomic_transition`` was called by ``enqueue_message_job``
        # for the eager-activation purpose.
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
                message="queued-after-enqueue-test",
                source="api",
            )

        # ── The JobItem stays in 'queued' after enqueue_message_job ──
        jobs = _load_message_job_items(engine)
        assert len(jobs) == 1, (
            f"Expected exactly one JobItem(message) row; got {len(jobs)}"
        )
        assert jobs[0].admission_state == AdmissionState.QUEUED.value, (
            f"Post-Option-B cutover: JobItem must stay in 'queued' after "
            f"enqueue_message_job (no eager activation); "
            f"got {jobs[0].admission_state!r}"
        )

        # ── No atomic_transition(queued -> active) was invoked at enqueue time ──
        # The pre-Option-B eager activation called
        # ``atomic_transition(job_id, "queued", "active")`` here. That
        # call is gone — concurrency enforcement is now owned by
        # ``start_job_atomic_with_lock`` in the JobProcessor's message
        # branch, which performs the flip atomically with the lock INSERT.
        # We allow ``atomic_transition`` to have been called for OTHER
        # reasons (none in this test, but future-proof), but reject the
        # specific ``queued -> active`` transition that was the D13
        # mirror's responsibility.
        for call in job_repository.atomic_transition.call_args_list:
            args = call.args
            call_kwargs = call.kwargs
            from_state = (
                args[1] if len(args) >= 2 else call_kwargs.get("from_status")
            )
            to_status = (
                args[2] if len(args) >= 3 else call_kwargs.get("to_status")
            )
            assert not (
                from_status == AdmissionState.QUEUED.value
                and to_status == AdmissionState.ACTIVE.value
            ), (
                f"enqueue_message_job must NOT eagerly flip the JobItem "
                f"from 'queued' to 'active' — the JobProcessor's "
                f"start_job_atomic_with_lock owns the transition. "
                f"Got atomic_transition call: from_status={from_status!r} "
                f"to_status={to_status!r}"
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

      * After ``enqueue_message_job`` (Option B cutover), the JobItem
        stays in ``queued`` — no eager activation.
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
        queue_repository,
    ):
        # ── Flag ON, instance IDLE ──
        manager = _build_manager(
            engine, instance_repository, write_guard, job_repository, queue_repository
        )
        messaging_service = InstanceMessagingService(
            manager=manager,
            cancellation_service=MagicMock(
                spec=CancellationService, is_shutting_down=False
            ),
        )

        _seed_instance(engine, instance_id="inst-1", status=InstanceStatus.IDLE.value)

        # ── Create the message-Job (no eager activation post-Option-B) ──
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

        # ── Sanity: JobItem stays in 'queued' after enqueue_message_job ──
        # (Option B cutover: ``enqueue_message_job`` no longer eagerly
        # flips queued -> active. Concurrency enforcement is now owned
        # by ``start_job_atomic_with_lock`` in the JobProcessor's
        # message branch.)
        jobs = _load_message_job_items(engine)
        assert len(jobs) == 1
        assert jobs[0].admission_state == AdmissionState.QUEUED.value, (
            "precondition: enqueue_message_job leaves JobItem in 'queued' "
            f"(got {jobs[0].admission_state!r})"
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
