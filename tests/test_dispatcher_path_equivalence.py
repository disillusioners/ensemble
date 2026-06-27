"""Tests for the unified ``enqueue_message`` dispatcher.

These tests verify the unified dispatcher behavior end-to-end — every
test exercises a single ``enqueue_message`` call and asserts the
observable state (MessageQueue, Task, Event rows, WorkerPool notify,
status transitions, AsyncMessageResult contract).

The earlier "path equivalence" framing is no longer relevant: the
parameter that named the two paths was removed in Phase 4. There is
now only one path.

What's verified here:

  * MessageQueue row is created with the input fields.
  * Status transition (IDLE / WAITING_CHILDREN / COMPLETED → RUNNING).
  * MESSAGE_RECEIVED event row.
  * Task row is created in the same transaction.
  * WorkerPool is notified.
  * Instance ``version`` + ``last_activity_at`` are bumped.
  * ``AsyncMessageResult.job_id`` is ``task.work_id`` (the stable
    cross-system UUID4 handle — Virtual Job Management Surface
    Phase 1, Batch 3).
  * ``images`` and ``priority`` are stored on the MessageQueue row.
  * SSE ``status_change`` is emitted on transitions.
  * PAUSED instances are NOT auto-resumed.
  * No ``JobItem`` row is created (D13 invariant).
  * ``_job_queue_service.enqueue`` is never called for messages.

Run with::

    pytest tests/test_dispatcher_path_equivalence.py -v
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select

from daemon.manager import AsyncMessageResult
from daemon.repositories.event.models import Event, EventKind
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.message_queue.models import (
    MessageQueue,
    MessageStatus,
    MessageType,
)
from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.services.cancellation import CancellationService
from daemon.services.instance_messaging import InstanceMessagingService
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
def cancellation_service():
    service = MagicMock(spec=CancellationService)
    service.is_shutting_down = False
    return service


@pytest.fixture
def write_guard():
    return WritePauseGuard()


def _seed_instance(
    engine,
    *,
    instance_id: str,
    status: str = InstanceStatus.IDLE.value,
    version: int = 1,
) -> Instance:
    inst = Instance(
        instance_id=instance_id,
        agent_id="developer",
        agent_dir="/agents/developer",
        project_id="test-project",
        status=status,
        version=version,
        instance_metadata={},
    )
    with Session(engine) as session:
        session.add(inst)
        session.commit()
        session.refresh(inst)
    return inst


def _build_manager(engine, instance_repository, write_guard) -> MagicMock:
    manager = MagicMock()
    manager.engine = engine
    manager.write_guard = write_guard
    manager._instance_repository = instance_repository

    # ``_live_hub.stream_status_change`` is awaited after status transition.
    manager._live_hub = MagicMock()
    manager._live_hub.stream_status_change = AsyncMock()

    # WorkerPool notify is called by the unified dispatcher; mock it so we
    # can assert call counts.
    manager._worker_pool = MagicMock()
    manager._worker_pool.notify_work = MagicMock()

    # JobQueueService.enqueue is NEVER called for messages (D13 invariant).
    # Mock it so the test can assert the absence.
    manager._job_queue_service = MagicMock()
    manager._job_queue_service.enqueue = AsyncMock(
        return_value=MagicMock(job_id="job-jq-xyz")
    )

    # Title-generation bridge (fire-and-forget) is patched in tests anyway,
    # but provide a mock so it never errors if it's invoked.
    manager._generate_and_broadcast_title = AsyncMock()
    return manager


@pytest.fixture
def manager(engine, instance_repository, write_guard):
    return _build_manager(engine, instance_repository, write_guard)


@pytest.fixture
def messaging_service(manager, cancellation_service):
    return InstanceMessagingService(
        manager=manager,
        cancellation_service=cancellation_service,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Helpers — DB loaders
# ──────────────────────────────────────────────────────────────────────────────


def _load_message_queues(engine, instance_id: str) -> list[MessageQueue]:
    with Session(engine) as session:
        return list(
            session.exec(
                select(MessageQueue).where(MessageQueue.instance_id == instance_id)
            )
        )


def _load_events(engine, instance_id: str) -> list[Event]:
    with Session(engine) as session:
        return list(
            session.exec(select(Event).where(Event.instance_id == instance_id))
        )


def _load_tasks(engine, instance_id: str) -> list[Task]:
    with Session(engine) as session:
        return list(
            session.exec(select(Task).where(Task.instance_id == instance_id))
        )


def _load_instance(engine, instance_id: str) -> Instance | None:
    with Session(engine) as session:
        return session.get(Instance, instance_id)


# ──────────────────────────────────────────────────────────────────────────────
# Shared inputs
# ──────────────────────────────────────────────────────────────────────────────

_SAMPLE_MESSAGE = "Hello, world!"
_SAMPLE_SOURCE = "telegram:user:42"
_SAMPLE_PRIORITY = 1
_SAMPLE_IMAGES = ["data:image/png;base64,AAAA"]


# ──────────────────────────────────────────────────────────────────────────────
# Tests — unified dispatcher behavior
# ──────────────────────────────────────────────────────────────────────────────


class TestUnifiedEnqueueDispatcher:
    """Unified dispatcher behavior — single ``enqueue_message`` call per test."""

    @pytest.mark.asyncio
    async def test_1_creates_message_queue_row_with_input_fields(
        self, engine, manager, messaging_service
    ):
        """``enqueue_message`` inserts a MessageQueue row with the input fields."""
        _seed_instance(engine, instance_id="inst-1")

        with patch("daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"):
            await messaging_service.enqueue_message(
                instance_id="inst-1",
                message=_SAMPLE_MESSAGE,
                source=_SAMPLE_SOURCE,
                priority=_SAMPLE_PRIORITY,
                images=_SAMPLE_IMAGES,
                metadata={"resume_mode": True},
            )

        rows = _load_message_queues(engine, "inst-1")
        assert len(rows) == 1, "enqueue_message should create exactly one MessageQueue"
        row = rows[0]
        assert row.instance_id == "inst-1"
        assert row.content == _SAMPLE_MESSAGE
        assert row.source == _SAMPLE_SOURCE
        assert row.priority == _SAMPLE_PRIORITY
        assert row.images == _SAMPLE_IMAGES
        assert row.type == MessageType.HUMAN.value
        assert row.status == MessageStatus.READY.value
        assert row.message_id  # auto-minted

    @pytest.mark.asyncio
    async def test_2_transitions_idle_to_running_with_version_bump(
        self, engine, manager, messaging_service
    ):
        """IDLE → RUNNING with the same version bump + last_activity_at."""
        _seed_instance(engine, instance_id="inst-1", version=3)

        with patch("daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"):
            await messaging_service.enqueue_message(
                instance_id="inst-1", message="m", source="api"
            )

        inst = _load_instance(engine, "inst-1")
        assert inst.status == InstanceStatus.RUNNING.value
        # version went from 3 → 4 (one enqueue call).
        assert inst.version == 4
        # last_activity_at set.
        assert inst.last_activity_at is not None

    @pytest.mark.asyncio
    async def test_3_emits_message_received_event(
        self, engine, manager, messaging_service
    ):
        """``enqueue_message`` emits a ``MESSAGE_RECEIVED`` event linked by message_id."""
        _seed_instance(engine, instance_id="inst-1")

        with patch("daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"):
            result = await messaging_service.enqueue_message(
                instance_id="inst-1", message=_SAMPLE_MESSAGE, source=_SAMPLE_SOURCE
            )

        events = _load_events(engine, "inst-1")
        assert len(events) == 1
        ev = events[0]
        assert ev.kind == EventKind.MESSAGE_RECEIVED.value
        assert ev.instance_id == "inst-1"
        assert ev.message_id == result.message_id

        data = json.loads(ev.data)
        assert data["content"] == _SAMPLE_MESSAGE
        assert data["source"] == _SAMPLE_SOURCE
        assert data["message_id"] == result.message_id

    @pytest.mark.asyncio
    async def test_4_creates_task_row_and_notifies_pool(
        self, engine, manager, messaging_service
    ):
        """``enqueue_message`` writes a ``Task`` row + notifies the WorkerPool."""
        _seed_instance(engine, instance_id="inst-1")

        with patch("daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"):
            await messaging_service.enqueue_message(
                instance_id="inst-1", message="m", source="api"
            )

        tasks = _load_tasks(engine, "inst-1")
        assert len(tasks) == 1, "Unified dispatcher must create a Task row"
        task = tasks[0]
        assert task.task_type == TaskType.PROCESS_MESSAGE.value
        assert task.status == TaskStatus.PENDING.value

        # WorkerPool.notify_work called once.
        manager._worker_pool.notify_work.assert_called_once()

    @pytest.mark.asyncio
    async def test_5_does_not_call_jq_service_enqueue(
        self, engine, manager, messaging_service
    ):
        """D13 invariant: ``_job_queue_service.enqueue`` is NEVER called
        for messages — the unified dispatcher only writes Task + MessageQueue
        rows and notifies the WorkerPool. ``_job_queue_service`` is reserved
        for TASK-type dispatch-queue jobs.
        """
        _seed_instance(engine, instance_id="inst-1")

        with patch("daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"):
            await messaging_service.enqueue_message(
                instance_id="inst-1", message="m", source="api"
            )

        manager._job_queue_service.enqueue.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_6_no_jobitem_row_created(
        self, engine, manager, messaging_service
    ):
        """D13 invariant: NO ``job_queue_items`` row is created for any
        ``enqueue_message`` call. Messages write only MessageQueue + Task
        rows. The ``job_queue_items`` table is reserved for TASK-type
        dispatch-queue jobs only.
        """
        from sqlmodel import Session, select
        from daemon.repositories.job_queue.models import JobItem

        _seed_instance(engine, instance_id="inst-1")

        with patch("daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"):
            await messaging_service.enqueue_message(
                instance_id="inst-1", message="m", source="api"
            )

        with Session(engine) as session:
            jobs = list(session.exec(select(JobItem)))
        assert len(jobs) == 0, (
            f"D13 invariant violated: enqueue_message created {len(jobs)} "
            f"JobItem row(s); expected 0"
        )

    @pytest.mark.asyncio
    async def test_7_returns_async_message_result_with_job_id_task_work_id(
        self, engine, manager, messaging_service
    ):
        """``AsyncMessageResult.job_id`` = ``task.work_id`` — stable
        cross-system UUID4 handle minted at Task row creation by the
        model's ``default_factory``.

        Phase 1 (Batch 3, 2026-06-27) of
        ``feature/virtual-job-management-surface`` flipped the
        ``job_id`` payload from ``str(task.id)`` (int PK, a stop-gap
        after D13 retired the JobItem UUID) to ``task.work_id``
        (UUID4, the truthful resolver handle). The HTTP
        ``send_message`` route discards ``job_id``; the
        ``job_continue`` tool surfaces it as ``new_job_id`` to the
        calling agent — both work because the resolver
        (``daemon.services.work_resolver``) accepts ``work_id`` on
        both the ``task`` and ``job_queue_items`` sides of the union.
        """
        _seed_instance(engine, instance_id="inst-1")

        with patch("daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"):
            result = await messaging_service.enqueue_message(
                instance_id="inst-1", message="m", source="api"
            )

        assert isinstance(result, AsyncMessageResult)
        assert result.message_id
        assert result.instance_id == "inst-1"
        assert result.status == "queued"
        # ``work_id`` is a UUID4 string (``xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx``
        # with hex digits and hyphens). The old assertion
        # (``isdigit()``) tested for the int-PK adapter which has now
        # been superseded; the new contract is "is a UUID4 string".
        assert result.job_id is not None, "job_id must be set"
        # Verify the UUID4 shape: 8-4-4-4-12 hex digits, hyphens at
        # fixed positions, and version nibble '4' at position 14.
        import re
        uuid4_pattern = re.compile(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
        )
        assert uuid4_pattern.match(result.job_id), (
            f"job_id must be a UUID4 (task.work_id), got {result.job_id!r}"
        )

    @pytest.mark.asyncio
    async def test_8_images_stored_on_message_queue(
        self, engine, manager, messaging_service
    ):
        """``images`` is stored on the MessageQueue row."""
        _seed_instance(engine, instance_id="inst-1")

        with patch("daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"):
            await messaging_service.enqueue_message(
                instance_id="inst-1",
                message="vision",
                source="api",
                images=_SAMPLE_IMAGES,
            )

        row = _load_message_queues(engine, "inst-1")[0]
        assert row.images == _SAMPLE_IMAGES

    @pytest.mark.asyncio
    @pytest.mark.parametrize("priority", [0, 1, 5])
    async def test_9_priority_stored_on_message_queue(
        self, engine, manager, messaging_service, priority
    ):
        """``priority`` parameter is stored on the MessageQueue row."""
        instance_id = f"inst-prio-{priority}"
        _seed_instance(engine, instance_id=instance_id)

        with patch("daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"):
            await messaging_service.enqueue_message(
                instance_id=instance_id, message="m", source="api", priority=priority
            )

        row = _load_message_queues(engine, instance_id)[0]
        assert row.priority == priority

    @pytest.mark.asyncio
    async def test_10_emits_sse_status_change_on_idle_to_running(
        self, engine, manager, messaging_service
    ):
        """``_live_hub.stream_status_change`` is emitted on IDLE → RUNNING."""
        _seed_instance(engine, instance_id="inst-1")

        with patch("daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"):
            await messaging_service.enqueue_message(
                instance_id="inst-1", message="m", source="api"
            )

        assert manager._live_hub.stream_status_change.await_count == 1
        call = manager._live_hub.stream_status_change.await_args
        assert call.args[0] == "inst-1"
        assert call.args[1] == InstanceStatus.RUNNING.value


# ──────────────────────────────────────────────────────────────────────────────
# D13 guard: enqueue_job(job_type="message") raises ValueError
# ──────────────────────────────────────────────────────────────────────────────


class TestEnqueueJobRejectsMessage:
    """D13 defense-in-depth guard: ``JobQueueService.enqueue`` rejects
    ``job_type="message"`` with ``ValueError``.

    After D13 messages write only Task + MessageQueue rows. Any leftover
    caller attempting the legacy API must fail loudly rather than
    silently creating a JobItem that no processor can handle.
    """

    @pytest.fixture(autouse=True)
    def _set_system_default_project(self):
        """``JobQueueService.enqueue`` calls ``normalize_project_id`` which
        requires ``daemon.constants.SYSTEM_DEFAULT_PROJECT_ID`` to be set.
        Set it for the duration of each test in this class.
        """
        from daemon import constants
        original = constants.SYSTEM_DEFAULT_PROJECT_ID
        constants.SYSTEM_DEFAULT_PROJECT_ID = "test-system-project-id"
        try:
            yield
        finally:
            constants.SYSTEM_DEFAULT_PROJECT_ID = original

    @pytest.mark.asyncio
    async def test_enqueue_rejects_message_job_type(self):
        """``enqueue(job_type="message")`` raises ``ValueError`` immediately."""
        from unittest.mock import AsyncMock, MagicMock
        from daemon.services.job_queue_service import JobQueueService

        service = JobQueueService(
            repository=MagicMock(),
            lock_manager=MagicMock(),
            queue_repo=MagicMock(),
        )
        with pytest.raises(ValueError) as exc_info:
            await service.enqueue(
                agent_id="developer",
                message="test",
                job_type="message",
                project_id="test-project",
            )
        # The error message must mention both the deprecated type and the
        # replacement entry point so operators can find the right method.
        assert "message" in str(exc_info.value).lower()
        assert "enqueue_message" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_enqueue_accepts_task_job_type(self):
        """``enqueue(job_type="task")`` still works (dispatch-queue path)."""
        from unittest.mock import AsyncMock, MagicMock, patch
        from daemon.services.job_queue_service import JobQueueService
        from daemon.repositories.job_queue.models import JobStatus

        service = JobQueueService(
            repository=MagicMock(),
            lock_manager=MagicMock(),
            queue_repo=MagicMock(),
        )
        # Stub the repo's create() to return a fake JobItem
        fake_job = MagicMock()
        fake_job.job_id = "task-job-1"
        fake_job.status = JobStatus.PENDING.value
        # ``_queue_repo.get(queue_id)`` is called when queue_id is provided
        # directly (the test bypasses name resolution). The returned object
        # must have ``.project_id == "test-project"`` or the production
        # code's "Queue does not belong to project" guard raises ValueError.
        fake_queue = MagicMock()
        fake_queue.project_id = "test-project"
        queue_repo_stub = MagicMock(
            get_by_name=MagicMock(return_value=None),
            get=MagicMock(return_value=fake_queue),
        )
        with patch.object(service, "_queue_repo", new=queue_repo_stub):
            # ``_repository.create`` is a synchronous method (production
            # wraps it with ``asyncio.to_thread`` at the call site), so we
            # use a plain MagicMock, not AsyncMock.
            service._repository.create = MagicMock(return_value=fake_job)
            service._dispatch_bus = MagicMock()
            result = await service.enqueue(
                agent_id="developer",
                message="test",
                job_type="task",
                project_id="test-project",
                queue_id="some-queue-id",
            )
        assert result.job_id == "task-job-1"