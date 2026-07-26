"""Option B (Phase 6) tests — message routing through the JobProcessor.

This file pins the Phase 5 (Option B) contract end-to-end:

  1. ``enqueue_message_job`` routes the message through
     ``JobQueueService.enqueue(job_type='message', instance_id=...)``
     rather than writing Task / MessageQueue rows directly.
  2. ``JobProcessor._process_next_job`` has a new message branch that
     invokes ``enqueue_message(work_id=job.job_id)`` and skips the
     ``spawn_instance_with_mcp`` call (message jobs target an existing
     instance).
  3. ``JobQueueService.start_job`` preserves ``instance_id`` for
     message jobs (does NOT mint a fresh UUID) and returns None when
     the target instance is terminal.
  4. ``JobRecoveryService.recover_on_startup`` resets an active
     message JobItem with NO Task row back to ``queued`` (crash
     between ``start_job`` and ``enqueue_message``) and releases the
     slot lock so re-dispatch can re-acquire it.
  5. ``batch_cancel_queued`` and ``find_active_jobs`` still exclude
     message JobItems — the cleanup cascade must not desync message
     mirrors from their authoritative Task rows.

Run with::

    pytest tests/job_queue/test_option_b_message_routing.py -v
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select

# Register all tables with SQLModel.metadata via model imports.
from daemon.repositories.event.models import Event  # noqa: F401
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.job_queue.models import AdmissionState, JobItem, JobLock
from daemon.repositories.job_queue.queue_repository import JobQueueRepository
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.message_queue.models import MessageQueue
from daemon.repositories.task.models import Task, TaskStatus
from daemon.repositories.task.repository import TaskRepository
from daemon.services.cancellation import CancellationService
from daemon.services.job_lock_manager import JobLockManager
from daemon.services.job_processor import JobProcessor
from daemon.services.job_queue_service import DemandState, JobQueueService
from daemon.services.job_recovery_service import JobRecoveryService
from daemon.services.project_normalizer import normalize_project_id
from daemon.write_pause_guard import WritePauseGuard


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine():
    """In-memory SQLite engine (StaticPool for cross-thread safety)."""
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
    return SQLModelInstanceRepository(engine)


@pytest.fixture
def job_repository(engine):
    return JobRepository(engine)


@pytest.fixture
def lock_repository(engine):
    return LockRepository(engine)


@pytest.fixture
def lock_manager(lock_repository):
    return JobLockManager(lock_repo=lock_repository)


@pytest.fixture
def task_repository(engine):
    return TaskRepository(engine)


@pytest.fixture
def queue_repository(engine):
    """Real ``JobQueueRepository`` seeded with the two system queues
    used by ``enqueue_message_job``."""
    repo = JobQueueRepository(engine)
    repo.create(
        project_id="test-project",
        queue_name="system_parallel_queue",
        queue_type="parallel",
        concurrency_limit=3,
        is_system=True,
    )
    repo.create(
        project_id="test-project",
        queue_name="system_fifo_queue",
        queue_type="fifo",
        concurrency_limit=1,
        is_system=True,
    )
    return repo


@pytest.fixture
def job_queue_service(job_repository, lock_manager, queue_repository):
    """Real ``JobQueueService`` wired to the SQLite-backed repos."""
    service = JobQueueService(job_repository, lock_manager, queue_repository)
    # Disable the project-pause check path — there's no ProjectRepository
    # in this test, so we'd otherwise fall through to a None-pause guard.
    service._project_repo = None
    return service


@pytest.fixture(autouse=True)
def _set_system_default_project():
    """``normalize_project_id`` consults ``daemon.constants.SYSTEM_DEFAULT_PROJECT_ID``."""
    from daemon import constants
    original = constants.SYSTEM_DEFAULT_PROJECT_ID
    constants.SYSTEM_DEFAULT_PROJECT_ID = "test-system-project"
    try:
        yield
    finally:
        constants.SYSTEM_DEFAULT_PROJECT_ID = original


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


def _seed_message_job(
    engine,
    *,
    job_id: str | None = None,
    project_id: str = "test-project",
    queue_id: str | None = None,
    instance_id: str = "inst-1",
    admission_state: str = AdmissionState.QUEUED.value,
    message: str = "hello",
    job_metadata: dict | None = None,
) -> JobItem:
    """Insert a ``JobItem(job_type='message', ...)`` directly."""
    job_id = job_id or str(uuid.uuid4())
    with Session(engine) as session:
        job = JobItem(
            job_id=job_id,
            agent_id="developer",
            agent_dir="/agents/developer",
            message=message,
            source="api",
            project_id=project_id,
            queue_id=queue_id,
            priority=1,
            admission_state=admission_state,
            job_type="message",
            instance_id=instance_id,
            job_metadata=job_metadata or {},
            max_retries=0,
        )
        session.add(job)
        session.commit()
        session.refresh(job)
    return job


def _load_job(engine, job_id: str) -> JobItem | None:
    with Session(engine) as session:
        return session.get(JobItem, job_id)


def _load_job_locks(engine, *, job_id: str | None = None) -> list[JobLock]:
    with Session(engine) as session:
        stmt = select(JobLock)
        if job_id is not None:
            stmt = stmt.where(JobLock.job_id == job_id)
        return list(session.exec(stmt))


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


# ──────────────────────────────────────────────────────────────────────────────
# Test 1 & 2: concurrency_limit enforcement via the lock slot
# ──────────────────────────────────────────────────────────────────────────────


class TestConcurrencyEnforced:
    """``concurrency_limit`` on the queue is now the sole authority for
    message serialization.

    Phase 5 (Option B) cutover: messages no longer rely on the per-
    instance Task guard (``_admitted_task_carve_out_sql``) for
    serialization. Instead, the slot-based
    ``start_job_atomic_with_lock`` is the gate: with
    ``concurrency_limit=1`` (FIFO queue), only one message can hold a
    slot at a time.
    """

    @pytest.mark.asyncio
    async def test_concurrency_enforced_fifo(
        self,
        engine,
        job_repository,
        lock_repository,
        queue_repository,
        job_queue_service,
    ):
        """Queue 2 messages to a FIFO queue with ``concurrency_limit=1``.
        Verify only 1 acquires a slot; the other stays ``queued``."""
        _seed_instance(engine, instance_id="inst-1", status=InstanceStatus.IDLE.value)
        _seed_instance(engine, instance_id="inst-2", status=InstanceStatus.IDLE.value)

        fifo_queue = queue_repository.get_by_name("test-project", "system_fifo_queue")
        assert fifo_queue is not None, "fixture must seed system_fifo_queue"

        job_A = _seed_message_job(
            engine,
            instance_id="inst-1",
            queue_id=fifo_queue.queue_id,
            message="A",
        )
        job_B = _seed_message_job(
            engine,
            instance_id="inst-2",
            queue_id=fifo_queue.queue_id,
            message="B",
        )

        # Start the first message — should succeed.
        started_A = await job_queue_service.start_job(job_A.job_id)
        assert started_A is not None, "first message must acquire the slot"
        assert started_A.admission_state == AdmissionState.ACTIVE.value

        # Try to start the second message — must NOT acquire a slot
        # (concurrency_limit=1 and the FIFO slot is taken by A).
        started_B = await job_queue_service.start_job(job_B.job_id)
        assert started_B is None, (
            f"second message must NOT start while the FIFO slot is held "
            f"by job_A (concurrency_limit=1); got {started_B!r}"
        )

        # Job B stays in 'queued'.
        reloaded_B = _load_job(engine, job_B.job_id)
        assert reloaded_B is not None
        assert reloaded_B.admission_state == AdmissionState.QUEUED.value, (
            f"job_B must remain 'queued' while waiting for the FIFO slot; "
            f"got {reloaded_B.admission_state!r}"
        )

        # The lock table has exactly 1 row for the FIFO queue — the
        # slot is held by job_A.
        locks = lock_repository.get_active_locks(
            "test-project", fifo_queue.queue_id
        )
        assert len(locks) == 1, (
            f"FIFO queue with concurrency_limit=1 must hold exactly 1 "
            f"active lock; got {len(locks)}"
        )
        assert locks[0].job_id == job_A.job_id

    @pytest.mark.asyncio
    async def test_concurrency_enforced_parallel(
        self,
        engine,
        job_repository,
        lock_repository,
        queue_repository,
        job_queue_service,
    ):
        """Two messages on the ``system_parallel_queue``
        (``concurrency_limit=3``) targeting different instances can
        both acquire a slot and run in parallel."""
        _seed_instance(engine, instance_id="inst-1", status=InstanceStatus.IDLE.value)
        _seed_instance(engine, instance_id="inst-2", status=InstanceStatus.IDLE.value)

        parallel_queue = queue_repository.get_by_name("test-project", "system_parallel_queue")
        assert parallel_queue is not None

        job_A = _seed_message_job(
            engine,
            instance_id="inst-1",
            queue_id=parallel_queue.queue_id,
            message="A",
        )
        job_B = _seed_message_job(
            engine,
            instance_id="inst-2",
            queue_id=parallel_queue.queue_id,
            message="B",
        )

        # Both messages should acquire their own slots.
        started_A = await job_queue_service.start_job(job_A.job_id)
        started_B = await job_queue_service.start_job(job_B.job_id)
        assert started_A is not None and started_B is not None, (
            "both messages on the parallel queue must acquire slots"
        )
        assert started_A.instance_id == "inst-1"
        assert started_B.instance_id == "inst-2"

        # Both JobItems are 'active' — parallel slots acquired.
        reloaded_A = _load_job(engine, job_A.job_id)
        reloaded_B = _load_job(engine, job_B.job_id)
        assert reloaded_A.admission_state == AdmissionState.ACTIVE.value
        assert reloaded_B.admission_state == AdmissionState.ACTIVE.value

        # The lock table has exactly 2 rows — two parallel slots held.
        locks = lock_repository.get_active_locks(
            "test-project", parallel_queue.queue_id
        )
        lock_job_ids = {lock.job_id for lock in locks}
        assert lock_job_ids == {job_A.job_id, job_B.job_id}, (
            f"parallel queue must hold 2 distinct slots; got {lock_job_ids}"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Test 3: end-to-end dispatch writes Task + MessageQueue rows
# ──────────────────────────────────────────────────────────────────────────────


class TestMessageContentDelivered:
    """After the JobProcessor's message branch dispatches the job, the
    Task + MessageQueue rows exist with the correct linkage."""

    @pytest.mark.asyncio
    async def test_message_content_delivered(
        self,
        engine,
        instance_repository,
        job_repository,
        task_repository,
        queue_repository,
        lock_repository,
        lock_manager,
    ):
        """End-to-end: a queued message JobItem is dispatched via
        ``enqueue_message`` and ends up writing Task +
        MessageQueue rows with the correct linkage.
        """
        _seed_instance(engine, instance_id="inst-1", status=InstanceStatus.IDLE.value)
        parallel_queue = queue_repository.get_by_name("test-project", "system_parallel_queue")
        job = _seed_message_job(
            engine,
            instance_id="inst-1",
            queue_id=parallel_queue.queue_id,
            message="hello option-b",
        )

        # Build a real JobQueueService so start_job exercises the
        # atomic-with-lock path (slot INSERT + admission UPDATE in one
        # transaction). This is what the JobProcessor actually calls.
        jq_service = JobQueueService(job_repository, lock_manager, queue_repository)
        jq_service._project_repo = None
        jq_service._instance_manager = MagicMock()
        jq_service._instance_manager._instance_repository = instance_repository

        # Build the messaging service that will write Task + MessageQueue
        # rows when the message branch calls ``enqueue_message``.
        manager = MagicMock()
        manager.engine = engine
        manager.write_guard = WritePauseGuard()
        manager._instance_repository = instance_repository
        manager._queue_repository = MagicMock()
        manager._project_repository = MagicMock()
        manager._live_hub = MagicMock()
        manager._live_hub.stream_status_change = AsyncMock()
        manager._worker_pool = MagicMock()
        manager._worker_pool.notify_work = MagicMock()
        manager._job_queue_service = jq_service
        manager._generate_and_broadcast_title = AsyncMock()
        manager._last_context_usage = {}

        from daemon.services.instance_messaging import InstanceMessagingService
        messaging_service = InstanceMessagingService(
            manager=manager,
            cancellation_service=MagicMock(
                spec=CancellationService, is_shutting_down=False
            ),
        )
        manager.enqueue_message = messaging_service.enqueue_message

        # Start the job (acquire slot, flip queued -> active).
        started_job = await jq_service.start_job(job.job_id)
        assert started_job is not None, "message job must start"
        assert started_job.admission_state == AdmissionState.ACTIVE.value

        # Dispatch the message via the same call the message branch makes.
        result = await manager.enqueue_message(
            instance_id=started_job.instance_id,
            message=started_job.message,
            source=started_job.source,
            metadata=started_job.job_metadata,
            work_id=started_job.job_id,
        )
        assert result is not None
        assert result.message_id is not None

        # Task row exists with work_id == job_id.
        task = task_repository.get_by_work_id(started_job.job_id)
        assert task is not None, (
            f"Task with work_id={started_job.job_id} must exist after dispatch"
        )
        assert task.instance_id == "inst-1"

        # MessageQueue row exists with the original content.
        mq_rows = _load_message_queues(engine, "inst-1")
        assert len(mq_rows) == 1
        assert mq_rows[0].content == "hello option-b"

        # JobItem is now 'active' (the observer finalizes it on
        # instance completion — this test does not simulate that
        # final step).
        reloaded = _load_job(engine, job.job_id)
        assert reloaded is not None
        assert reloaded.admission_state == AdmissionState.ACTIVE.value


# ──────────────────────────────────────────────────────────────────────────────
# Test 4: JobProcessor._process_next_job routes message jobs to enqueue_message
# ──────────────────────────────────────────────────────────────────────────────


class TestProcessorRoutesMessage:
    """The JobProcessor's message branch routes the job to a wake-only
    step (``worker_pool.notify_work()``) instead of calling
    ``enqueue_message`` (the Task + MessageQueue are already written
    by ``enqueue_message_job``) and skips the
    ``spawn_instance_with_mcp`` call (message jobs target an EXISTING
    instance)."""

    @pytest.mark.asyncio
    async def test_processor_routes_message_to_wake_only(
        self,
        engine,
        job_repository,
        lock_repository,
        queue_repository,
        job_queue_service,
    ):
        """Create a message-type JobItem and call
        ``_process_next_job``. Verify ``worker_pool.notify_work()``
        was called (Task is pre-existing), ``enqueue_message`` was
        NOT called, and ``spawn_instance_with_mcp`` was NOT called."""
        _seed_instance(engine, instance_id="inst-1", status=InstanceStatus.IDLE.value)
        parallel_queue = queue_repository.get_by_name("test-project", "system_parallel_queue")
        job = _seed_message_job(
            engine,
            instance_id="inst-1",
            queue_id=parallel_queue.queue_id,
            message="processor message branch test",
        )

        # Build a real JobProcessor with a mocked InstanceManager.
        project_repo = MagicMock()
        project_repo.list_projects.return_value = [MagicMock(
            project_id="test-project",
            job_queue_paused=False,
        )]
        instance_manager = MagicMock()
        # The instance exists and is not PAUSED.
        instance_manager._instance_repository = MagicMock()
        instance_manager._instance_repository.get.return_value = MagicMock(
            instance_id="inst-1",
            status=InstanceStatus.IDLE.value,
        )
        # Worker pool mock — message branch calls
        # ``worker_pool.notify_work()`` to surface the pre-existing
        # Task to a worker thread.
        instance_manager._worker_pool = MagicMock()
        instance_manager._worker_pool.notify_work = MagicMock()
        # enqueue_message must NOT be called (the Task is pre-existing
        # in the new contract).
        instance_manager.enqueue_message = AsyncMock()
        instance_manager.spawn_instance_with_mcp = AsyncMock(return_value="inst-1")
        instance_manager.get_instance = AsyncMock(return_value=MagicMock())

        # Make the JobQueueService return the message job as pending
        # for this queue.
        queue_repo_with_queues = MagicMock()
        mock_queue = MagicMock(
            queue_id=parallel_queue.queue_id,
            project_id="test-project",
            queue_name="system_parallel_queue",
            is_paused=False,
            queue_type="parallel",
            concurrency_limit=3,
        )
        queue_repo_with_queues.list_by_project.return_value = [mock_queue]

        processor = JobProcessor(
            queue_service=job_queue_service,
            instance_manager=instance_manager,
            project_repo=project_repo,
            queue_repo=queue_repo_with_queues,
            poll_interval=0.1,
        )

        # Stub list_pending_by_queue to return only our message job.
        job_queue_service._repository.list_pending_by_queue = MagicMock(
            return_value=[job]
        )
        # Stub list_by_queue so the orphan re-dispatch path is skipped.
        job_queue_service._repository.list_by_queue = MagicMock(
            return_value=([], 0)
        )

        await processor._process_next_job()

        # ── The message branch woke the worker pool (Task is pre-existing) ──
        instance_manager._worker_pool.notify_work.assert_called_once_with(), (
            "the message branch must wake the worker pool so a worker "
            "thread can claim the pre-existing Task "
            "(Option B synchronous Task contract)"
        )

        # ── enqueue_message was NOT called (Task is pre-existing) ──
        instance_manager.enqueue_message.assert_not_awaited(), (
            "the message branch must NOT call enqueue_message — the "
            "Task + MessageQueue rows are already written by "
            "enqueue_message_job (Option B synchronous Task contract)"
        )

        # ── spawn_instance_with_mcp was NOT called (message branch skips it) ──
        instance_manager.spawn_instance_with_mcp.assert_not_awaited(), (
            "the message branch must NOT call spawn_instance_with_mcp "
            "(message jobs target an existing instance, not a fresh one)"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Test 5: start_job preserves instance_id for message jobs
# ──────────────────────────────────────────────────────────────────────────────


class TestStartJobPreservesInstanceId:
    """``JobQueueService.start_job`` preserves the existing instance_id
    for message jobs (does NOT mint a fresh UUID)."""

    @pytest.mark.asyncio
    async def test_start_job_preserves_instance_id_for_message(
        self,
        engine,
        instance_repository,
        job_repository,
        lock_repository,
        queue_repository,
        job_queue_service,
    ):
        """For ``job_type='message'`` with an existing ``instance_id``,
        ``start_job`` must preserve the instance_id (not overwrite it
        with a fresh UUID)."""
        _seed_instance(engine, instance_id="inst-target", status=InstanceStatus.IDLE.value)
        parallel_queue = queue_repository.get_by_name("test-project", "system_parallel_queue")
        job = _seed_message_job(
            engine,
            instance_id="inst-target",
            queue_id=parallel_queue.queue_id,
            message="preserve instance_id test",
        )

        # Wire the instance manager so start_job's TERMINAL / PAUSED
        # checks can find the instance.
        job_queue_service._instance_manager = MagicMock()
        job_queue_service._instance_manager._instance_repository = instance_repository

        started_job = await job_queue_service.start_job(job.job_id)
        assert started_job is not None, (
            "message job must start (slot available, instance IDLE)"
        )

        # ── The SAME instance_id must be preserved on the returned job ──
        # Pre-Option-B: ``start_job`` always minted a fresh UUID via
        # ``str(uuid.uuid4())``. Post-Option-B: it preserves the
        # existing ``instance_id`` so the message branch routes back
        # to the same instance.
        assert started_job.instance_id == "inst-target", (
            f"start_job must preserve the existing instance_id for "
            f"message jobs (got {started_job.instance_id!r}, expected "
            f"'inst-target'); a fresh UUID would detach the JobItem "
            f"from its target instance and lose the message."
        )

        # Verify the lock row also carries the preserved instance_id.
        locks = lock_repository.get_active_locks("test-project", parallel_queue.queue_id)
        assert len(locks) == 1
        assert locks[0].instance_id == "inst-target", (
            f"the slot lock must carry the preserved instance_id "
            f"(got {locks[0].instance_id!r})"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Test 6: metadata passthrough
# ──────────────────────────────────────────────────────────────────────────────


class TestMetadataPassthrough:
    """``images``, ``is_deferred``, and ``is_background`` flow from
    ``enqueue_message_job`` → JobItem.metadata → ``enqueue_message``."""

    @pytest.mark.asyncio
    async def test_metadata_passthrough(
        self,
        engine,
        instance_repository,
        job_repository,
        task_repository,
        queue_repository,
        lock_repository,
        lock_manager,
    ):
        """Enqueue a message job with all three metadata fields. After
        dispatch (via the same call the JobProcessor message branch
        makes), verify the Task row has ``is_deferred=True`` and the
        MessageQueue row carries the images."""
        _seed_instance(engine, instance_id="inst-1", status=InstanceStatus.IDLE.value)
        parallel_queue = queue_repository.get_by_name("test-project", "system_parallel_queue")
        images = ["data:image/png;base64,AAAA"]
        job = _seed_message_job(
            engine,
            instance_id="inst-1",
            queue_id=parallel_queue.queue_id,
            message="metadata-passthrough",
            job_metadata={
                "images": images,
                "is_deferred": True,
                "is_background": True,
            },
        )

        jq_service = JobQueueService(job_repository, lock_manager, queue_repository)
        jq_service._project_repo = None
        jq_service._instance_manager = MagicMock()
        jq_service._instance_manager._instance_repository = instance_repository

        # Build the messaging service.
        manager = MagicMock()
        manager.engine = engine
        manager.write_guard = WritePauseGuard()
        manager._instance_repository = instance_repository
        manager._queue_repository = MagicMock()
        manager._project_repository = MagicMock()
        manager._live_hub = MagicMock()
        manager._live_hub.stream_status_change = AsyncMock()
        manager._worker_pool = MagicMock()
        manager._worker_pool.notify_work = MagicMock()
        manager._job_queue_service = jq_service
        manager._generate_and_broadcast_title = AsyncMock()
        manager._last_context_usage = {}

        from daemon.services.instance_messaging import InstanceMessagingService
        messaging_service = InstanceMessagingService(
            manager=manager,
            cancellation_service=MagicMock(
                spec=CancellationService, is_shutting_down=False
            ),
        )
        manager.enqueue_message = messaging_service.enqueue_message

        # Start the job (acquire slot, flip queued -> active).
        started_job = await jq_service.start_job(job.job_id)
        assert started_job is not None

        # Dispatch via the same call the message branch makes. The
        # branch reads the dispatch-time metadata off
        # ``job.job_metadata`` and passes it as keyword args.
        job_meta = started_job.job_metadata or {}
        result = await manager.enqueue_message(
            instance_id=started_job.instance_id,
            message=started_job.message,
            source=started_job.source,
            images=job_meta.get("images"),
            metadata=job_meta,
            is_deferred=bool(job_meta.get("is_deferred", False)),
            is_background=bool(job_meta.get("is_background", False)),
            work_id=started_job.job_id,
        )
        assert result is not None

        # Task row has is_deferred=True (stamped at Task row creation).
        task = task_repository.get_by_work_id(started_job.job_id)
        assert task is not None
        assert task.is_deferred is True, (
            f"Task.is_deferred must be True (got {task.is_deferred!r})"
        )
        assert task.is_background is True, (
            f"Task.is_background must be True (got {task.is_background!r})"
        )

        # MessageQueue row carries the images.
        mq_rows = _load_message_queues(engine, "inst-1")
        assert len(mq_rows) == 1
        assert mq_rows[0].images == images, (
            f"MessageQueue.images must match the dispatch-time metadata; "
            f"got {mq_rows[0].images!r}"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Test 7 & 8: crash recovery for active message jobs
# ──────────────────────────────────────────────────────────────────────────────


class TestCrashRecovery:
    """``JobRecoveryService.recover_on_startup`` resets an active
    message JobItem with NO Task row back to ``queued`` (crash between
    ``start_job`` and ``enqueue_message``) and leaves an active message
    WITH a Task row alone."""

    @pytest.mark.asyncio
    async def test_crash_recovery_resets_active_message_no_task(
        self,
        engine,
        instance_repository,
        job_repository,
        lock_repository,
        queue_repository,
        task_repository,
    ):
        """Active message JobItem with NO Task → reset to ``queued``
        and the slot lock is released."""
        _seed_instance(engine, instance_id="inst-1", status=InstanceStatus.IDLE.value)
        parallel_queue = queue_repository.get_by_name("test-project", "system_parallel_queue")
        job = _seed_message_job(
            engine,
            instance_id="inst-1",
            queue_id=parallel_queue.queue_id,
            admission_state=AdmissionState.ACTIVE.value,
            message="crashed before enqueue_message",
        )

        # Seed a slot lock so we can verify the recovery path releases it.
        with Session(engine) as session:
            session.add(JobLock(
                lock_id=str(uuid.uuid4()),
                project_id="test-project",
                queue_id=parallel_queue.queue_id,
                job_id=job.job_id,
                instance_id="inst-1",
                lock_slot=0,
                acquired_at=datetime.now(timezone.utc).isoformat(),
            ))
            session.commit()

        # NO Task row — simulates crash between start_job and
        # enqueue_message.
        assert task_repository.get_by_work_id(job.job_id) is None

        recovery = JobRecoveryService(
            job_repository=job_repository,
            lock_repository=lock_repository,
            instance_repository=instance_repository,
            task_repository=task_repository,
        )

        stats = await recovery.recover_on_startup()

        # The active message JobItem was reset to 'queued'.
        assert stats["recovered"] >= 1, (
            f"recovery must reset the active message JobItem with no Task "
            f"back to 'queued'; got stats={stats!r}"
        )

        reloaded = _load_job(engine, job.job_id)
        assert reloaded is not None
        assert reloaded.admission_state == AdmissionState.QUEUED.value, (
            f"active message JobItem with no Task must be reset to "
            f"'queued' for re-dispatch; got {reloaded.admission_state!r}"
        )

        # The slot lock was released (re-dispatch can re-acquire).
        locks = lock_repository.get_active_locks("test-project", parallel_queue.queue_id)
        assert len(locks) == 0, (
            f"recovery must release the slot lock so re-dispatch can "
            f"re-acquire it; got {len(locks)} lock(s)"
        )

    @pytest.mark.asyncio
    async def test_crash_recovery_leaves_active_message_with_task(
        self,
        engine,
        instance_repository,
        job_repository,
        lock_repository,
        queue_repository,
        task_repository,
    ):
        """Active message JobItem WITH a Task row → left as 'active'
        (the observer handles completion)."""
        _seed_instance(engine, instance_id="inst-1", status=InstanceStatus.RUNNING.value)
        parallel_queue = queue_repository.get_by_name("test-project", "system_parallel_queue")
        job = _seed_message_job(
            engine,
            instance_id="inst-1",
            queue_id=parallel_queue.queue_id,
            admission_state=AdmissionState.ACTIVE.value,
            message="dispatched but not yet completed",
        )

        # Seed a slot lock.
        with Session(engine) as session:
            session.add(JobLock(
                lock_id=str(uuid.uuid4()),
                project_id="test-project",
                queue_id=parallel_queue.queue_id,
                job_id=job.job_id,
                instance_id="inst-1",
                lock_slot=0,
                acquired_at=datetime.now(timezone.utc).isoformat(),
            ))
            session.commit()

        # Seed a Task row with work_id == job_id (the dispatch-time
        # link).
        with Session(engine) as session:
            session.add(Task(
                instance_id="inst-1",
                work_id=job.job_id,
                message_id="msg-stub",
                status=TaskStatus.RUNNING.value,
                task_type="message",
                is_deferred=False,
                is_background=False,
            ))
            session.commit()

        assert task_repository.get_by_work_id(job.job_id) is not None

        recovery = JobRecoveryService(
            job_repository=job_repository,
            lock_repository=lock_repository,
            instance_repository=instance_repository,
            task_repository=task_repository,
        )

        await recovery.recover_on_startup()

        reloaded = _load_job(engine, job.job_id)
        assert reloaded is not None
        assert reloaded.admission_state == AdmissionState.ACTIVE.value, (
            f"active message JobItem WITH a Task row must be LEFT in "
            f"'active' (the observer handles completion); got "
            f"{reloaded.admission_state!r}"
        )

        # The slot lock is preserved — the observer's _finalize_job_db_sync
        # releases it on instance completion, not recovery.
        locks = lock_repository.get_active_locks("test-project", parallel_queue.queue_id)
        assert len(locks) == 1, (
            f"slot lock for an active message JobItem WITH a Task must "
            f"remain held (observer releases it on completion); got "
            f"{len(locks)} lock(s)"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Test 9: slot release on completion
# ──────────────────────────────────────────────────────────────────────────────


class TestSlotReleaseOnCompletion:
    """After an instance finishes, the slot lock row is deleted so the
    next message can acquire the slot."""

    @pytest.mark.asyncio
    async def test_slot_release_on_completion(
        self,
        engine,
        instance_repository,
        job_repository,
        lock_repository,
        queue_repository,
        lock_manager,
        task_repository,
    ):
        """Start a message job, simulate completion via
        ``LockRepository.release_by_instance`` (the same call
        ``_finalize_job_db_sync`` makes), and verify the lock row is
        gone."""
        _seed_instance(engine, instance_id="inst-1", status=InstanceStatus.IDLE.value)
        parallel_queue = queue_repository.get_by_name("test-project", "system_parallel_queue")
        job = _seed_message_job(
            engine,
            instance_id="inst-1",
            queue_id=parallel_queue.queue_id,
            message="slot release on completion",
        )

        jq_service = JobQueueService(job_repository, lock_manager, queue_repository)
        jq_service._project_repo = None
        jq_service._instance_manager = MagicMock()
        jq_service._instance_manager._instance_repository = instance_repository

        started_job = await jq_service.start_job(job.job_id)
        assert started_job is not None

        # Slot is held.
        locks_before = lock_repository.get_active_locks("test-project", parallel_queue.queue_id)
        assert len(locks_before) == 1

        # Simulate the observer's slot release on instance completion.
        # ``_finalize_job_db_sync`` deletes the JobLock row by
        # ``instance_id`` (inlined SQL — see
        # job_feedback_observer.py:~L3364). The simplest equivalent
        # at the repo layer is ``LockRepository.release_by_instance``.
        released_count = lock_repository.release_by_instance("inst-1")
        assert released_count == 1, (
            f"slot release must delete exactly 1 lock row; got {released_count}"
        )

        # The slot is free.
        locks_after = lock_repository.get_active_locks("test-project", parallel_queue.queue_id)
        assert len(locks_after) == 0, (
            f"slot lock row must be deleted on completion; got "
            f"{len(locks_after)} lock(s)"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Test 10: terminal instance guard
# ──────────────────────────────────────────────────────────────────────────────


class TestTerminalInstanceGuard:
    """``JobQueueService.start_job`` returns None when a message job's
    target instance is terminal — it must NOT clear the instance_id
    (so no re-spawn on the next tick)."""

    @pytest.mark.asyncio
    async def test_terminal_instance_guard(
        self,
        engine,
        instance_repository,
        job_repository,
        lock_repository,
        queue_repository,
        job_queue_service,
    ):
        """A message job targeting a TERMINAL instance → ``start_job``
        returns None and does NOT clear the instance_id."""
        _seed_instance(
            engine,
            instance_id="inst-dead",
            status=InstanceStatus.TERMINATED.value,
        )
        parallel_queue = queue_repository.get_by_name("test-project", "system_parallel_queue")
        job = _seed_message_job(
            engine,
            instance_id="inst-dead",
            queue_id=parallel_queue.queue_id,
            message="target terminal instance",
        )

        job_queue_service._instance_manager = MagicMock()
        job_queue_service._instance_manager._instance_repository = instance_repository

        result = await job_queue_service.start_job(job.job_id)

        # ── start_job returns None (terminal instance guard) ──
        assert result is None, (
            "start_job must return None for a message job targeting a "
            "terminal instance (the message cannot be delivered)"
        )

        # ── The job is dead-lettered to break the dispatch loop ──
        # B3 fix (v2): ``atomic_transition`` flips the row from
        # ``queued`` to ``done`` with ``terminal_reason='aborted'`` so
        # the dispatch query (which filters on
        # ``admission_state='queued'``) stops picking it up. Pre-v2
        # used ``complete_job(FAILED)`` which delegated to
        # ``_finalize_terminal`` — that path only handles 'active'
        # rows and silently no-op'd for queued jobs, creating an
        # infinite CPU-burn loop. The post-state assertion below is
        # the regression guard.
        reloaded = _load_job(engine, job.job_id)
        assert reloaded is not None
        assert reloaded.admission_state == AdmissionState.DONE.value, (
            f"terminal-instance guard must dead-letter the queued job "
            f"(expected admission_state='done', got "
            f"{reloaded.admission_state!r}); a queued job with the same "
            f"instance_id would loop forever in the dispatch loop"
        )
        assert reloaded.terminal_reason == "aborted", (
            f"terminal_reason must be 'aborted' (the v2 discriminator "
            f"for the instance-terminated cascade); got "
            f"{reloaded.terminal_reason!r}"
        )

        # ── The instance_id is preserved (NOT cleared) ──
        # Pre-Option-B: ``start_job`` cleared stale instance_ids for
        # TASK jobs (causing re-spawn on the next tick). For message
        # jobs, clearing would lose the message permanently — the
        # terminal-instance guard returns None WITHOUT clearing.
        assert reloaded.instance_id == "inst-dead", (
            f"terminal-instance guard must NOT clear the instance_id "
            f"for message jobs (would cause a re-spawn on the next tick, "
            f"losing the message); got instance_id={reloaded.instance_id!r}"
        )

        # ── A second dispatch invocation is a no-op (job already done) ──
        # Regression guard for the v1 bug: ``start_job`` returning None
        # alone does NOT prove the loop is broken — the job must
        # actually leave the ``queued`` bucket so the dispatch query
        # stops picking it up.
        result_2 = await job_queue_service.start_job(job.job_id)
        assert result_2 is None, (
            "second start_job on a dead-lettered job must also return None"
        )
        reloaded_2 = _load_job(engine, job.job_id)
        assert reloaded_2.admission_state == AdmissionState.DONE.value, (
            f"dead-letter state must persist across re-dispatch attempts "
            f"(admission_state={reloaded_2.admission_state!r})"
        )

        # No slot lock acquired.
        locks = lock_repository.get_active_locks("test-project", parallel_queue.queue_id)
        assert len(locks) == 0


# ──────────────────────────────────────────────────────────────────────────────
# Test 11 & 12: cleanup cascade excludes message mirrors
# ──────────────────────────────────────────────────────────────────────────────


class TestCleanupExcludesMessages:
    """``batch_cancel_queued`` and ``find_active_jobs`` must continue
    to exclude message JobItems — the cleanup cascade must not desync
    message mirrors from their authoritative Task rows."""

    def test_batch_cancel_queued_excludes_messages(
        self,
        engine,
        job_repository,
        queue_repository,
    ):
        """``batch_cancel_queued`` cancels queued TASK jobs but leaves
        queued MESSAGE jobs alone."""
        parallel_queue = queue_repository.get_by_name("test-project", "system_parallel_queue")

        # Seed a TASK-type job directly (not via the message-job helper,
        # which always sets ``job_type='message'``).
        task_job_id = str(uuid.uuid4())
        with Session(engine) as session:
            session.add(JobItem(
                job_id=task_job_id,
                agent_id="developer",
                agent_dir="/agents/developer",
                message="queued task",
                source="api",
                project_id="test-project",
                queue_id=parallel_queue.queue_id,
                priority=1,
                admission_state=AdmissionState.QUEUED.value,
                job_type="task",
                instance_id="inst-task",
            ))
            session.commit()

        message_job_id = _seed_message_job(
            engine,
            project_id="test-project",
            queue_id=parallel_queue.queue_id,
            instance_id="inst-msg",
            message="queued message",
        ).job_id

        cancelled = job_repository.batch_cancel_queued()
        assert cancelled == 1, (
            f"batch_cancel_queued must cancel exactly 1 queued TASK job "
            f"(message mirrors are excluded); got rowcount={cancelled}"
        )

        # The TASK job is 'done'.
        reloaded_task = _load_job(engine, task_job_id)
        assert reloaded_task is not None
        assert reloaded_task.admission_state == AdmissionState.DONE.value

        # The MESSAGE job is still 'queued'.
        reloaded_msg = _load_job(engine, message_job_id)
        assert reloaded_msg is not None
        assert reloaded_msg.admission_state == AdmissionState.QUEUED.value, (
            f"batch_cancel_queued must NOT cancel message JobItems "
            f"(cancelling mirrors would desync them from their Task rows); "
            f"got admission_state={reloaded_msg.admission_state!r}"
        )

    def test_find_active_jobs_excludes_messages(
        self,
        engine,
        job_repository,
        queue_repository,
    ):
        """``find_active_jobs`` returns only active TASK jobs —
        active MESSAGE jobs are excluded."""
        parallel_queue = queue_repository.get_by_name("test-project", "system_parallel_queue")

        # Seed one active TASK job (not via the message-job helper).
        task_job_id = str(uuid.uuid4())
        with Session(engine) as session:
            session.add(JobItem(
                job_id=task_job_id,
                agent_id="developer",
                agent_dir="/agents/developer",
                message="active task",
                source="api",
                project_id="test-project",
                queue_id=parallel_queue.queue_id,
                priority=1,
                admission_state=AdmissionState.ACTIVE.value,
                job_type="task",
                instance_id="inst-task",
            ))
            session.commit()

        _seed_message_job(
            engine,
            project_id="test-project",
            queue_id=parallel_queue.queue_id,
            instance_id="inst-msg",
            admission_state=AdmissionState.ACTIVE.value,
            message="active message",
        )

        active_jobs = job_repository.find_active_jobs()
        job_ids = {j.job_id for j in active_jobs}

        assert len(active_jobs) == 1, (
            f"find_active_jobs must return exactly 1 active TASK job "
            f"(message mirrors are excluded); got {len(active_jobs)}"
        )
        assert task_job_id in job_ids, (
            f"find_active_jobs must include the active TASK job; got {job_ids}"
        )
        # The MESSAGE JobItem is excluded.
        for j in active_jobs:
            assert j.job_type != "message", (
                f"find_active_jobs must NOT return message JobItems "
                f"(the cancel cascade would desync mirrors from their "
                f"Task rows); got {j.job_type!r}"
            )
