"""Path-equivalence tests for ``enqueue_message`` (both dispatch paths).

These tests assert that calling the unified ``enqueue_message`` with
``dispatch_path="workerpool"`` and ``dispatch_path="jobqueue"`` with
the **same inputs** produces the same observable state — for everything
that the two paths are supposed to share (the ``_prepare_enqueued_message``
prelude side-effects).

The two paths are *deliberately* divergent at the dispatch layer:

  * ``enqueue_message(dispatch_path="workerpool")`` writes a ``Task``
    row and notifies ``_worker_pool.notify_work()``.
  * ``enqueue_message(dispatch_path="jobqueue")`` writes a ``JobItem``
    row and calls ``_job_queue_service.enqueue()``.

That divergence is tested explicitly as a sanity check (tests 4 and 5)
so future refactors can't accidentally collapse the two dispatchers.

Run with::

    pytest tests/test_dispatcher_path_equivalence.py -v
"""

from __future__ import annotations

import json
from typing import Any
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
        agent_id="coder",
        agent_dir="/agents/coder",
        project_id="test-project",
        status=status,
        version=version,
        instance_metadata={},
        children="[]",
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

    # WorkerPool notify is called only on the WP path; mock it so we can
    # assert call counts.
    manager._worker_pool = MagicMock()
    manager._worker_pool.notify_work = MagicMock()

    # JobQueueService.enqueue is called only on the JQ path; mock it so we
    # can assert call counts and inspect kwargs (images, priority, etc.).
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
# Shared inputs — used for side-by-side equivalence tests
# ──────────────────────────────────────────────────────────────────────────────

_SAMPLE_MESSAGE = "Hello, world!"
_SAMPLE_SOURCE = "telegram:user:42"
_SAMPLE_PRIORITY = 1
_SAMPLE_IMAGES = ["data:image/png;base64,AAAA"]


# ──────────────────────────────────────────────────────────────────────────────
# Tests 1–10: Path equivalence
# ──────────────────────────────────────────────────────────────────────────────


class TestDispatcherPathEquivalence:
    """Both paths must produce equivalent observable state for the same inputs."""

    # ────────────────────────────────────────────────────────────────────────
    # 1. MessageQueue row equivalence
    # ────────────────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_1_message_queue_row_equivalence(
        self, engine, manager, messaging_service
    ):
        """Both paths insert a ``MessageQueue`` row with identical fields.

        The only differences allowed are row ``id`` and ``enqueued_at``
        (auto-generated) and ``message_id`` (UUIDs minted per call).
        """
        _seed_instance(engine, instance_id="inst-wp")
        _seed_instance(engine, instance_id="inst-jq")

        with patch("daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"):
            await messaging_service.enqueue_message(
                instance_id="inst-wp",
                message=_SAMPLE_MESSAGE,
                source=_SAMPLE_SOURCE,
                priority=_SAMPLE_PRIORITY,
                images=_SAMPLE_IMAGES,
                metadata={"resume_mode": True},
            )
            await messaging_service.enqueue_message(dispatch_path="jobqueue", 
                instance_id="inst-jq",
                message=_SAMPLE_MESSAGE,
                source=_SAMPLE_SOURCE,
                priority=_SAMPLE_PRIORITY,
                images=_SAMPLE_IMAGES,
                metadata={"resume_mode": True},
            )

        wp = _load_message_queues(engine, "inst-wp")[0]
        jq = _load_message_queues(engine, "inst-jq")[0]

        # Both paths create exactly one MessageQueue row.
        assert len(_load_message_queues(engine, "inst-wp")) == 1
        assert len(_load_message_queues(engine, "inst-jq")) == 1

        # Compare field-by-field on the shared prelude contract.
        # NOTE: ``instance_id`` is the test-supplied input (the two paths
        # are seeded with different IDs) and ``message_id`` is a UUID
        # minted per-call — both are necessarily different. We compare
        # the remaining fields that the prelude is contractually
        # obligated to produce identically for the same inputs.
        for field in (
            "content",
            "source",
            "type",
            "status",
            "priority",
            "images",
        ):
            assert getattr(wp, field) == getattr(jq, field), (
                f"MessageQueue.{field} diverges between paths: "
                f"wp={getattr(wp, field)!r} jq={getattr(jq, field)!r}"
            )

        # Both paths must store their own (per-call) instance_id correctly.
        assert wp.instance_id == "inst-wp"
        assert jq.instance_id == "inst-jq"
        # message_id is a per-call UUID — must exist but values will differ.
        assert wp.message_id and jq.message_id
        assert wp.message_id != jq.message_id

        assert wp.content == _SAMPLE_MESSAGE
        assert wp.source == _SAMPLE_SOURCE
        assert wp.priority == _SAMPLE_PRIORITY
        assert wp.images == _SAMPLE_IMAGES
        assert wp.type == MessageType.HUMAN.value
        assert wp.status == MessageStatus.READY.value

    # ────────────────────────────────────────────────────────────────────────
    # 2. Status transition equivalence (IDLE → RUNNING)
    # ────────────────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_2_idle_to_running_transition_equivalence(
        self, engine, manager, messaging_service
    ):
        """Both paths transition IDLE → RUNNING with the same version bump."""
        _seed_instance(engine, instance_id="inst-wp", version=3)
        _seed_instance(engine, instance_id="inst-jq", version=3)

        with patch("daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"):
            await messaging_service.enqueue_message(
                instance_id="inst-wp", message="m", source="api"
            )
            await messaging_service.enqueue_message(dispatch_path="jobqueue", 
                instance_id="inst-jq", message="m", source="api"
            )

        wp_inst = _load_instance(engine, "inst-wp")
        jq_inst = _load_instance(engine, "inst-jq")

        assert wp_inst.status == jq_inst.status == InstanceStatus.RUNNING.value
        # version went from 3 → 4 (one enqueue call).
        assert wp_inst.version == jq_inst.version == 4
        # last_activity_at set on both.
        assert wp_inst.last_activity_at is not None
        assert jq_inst.last_activity_at is not None

    # ────────────────────────────────────────────────────────────────────────
    # 3. MESSAGE_RECEIVED event equivalence
    # ────────────────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_3_message_received_event_equivalence(
        self, engine, manager, messaging_service
    ):
        """Both paths emit a ``MESSAGE_RECEIVED`` event linked by message_id."""
        _seed_instance(engine, instance_id="inst-wp")
        _seed_instance(engine, instance_id="inst-jq")

        with patch("daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"):
            wp_result = await messaging_service.enqueue_message(
                instance_id="inst-wp", message=_SAMPLE_MESSAGE, source=_SAMPLE_SOURCE
            )
            jq_result = await messaging_service.enqueue_message(dispatch_path="jobqueue", 
                instance_id="inst-jq", message=_SAMPLE_MESSAGE, source=_SAMPLE_SOURCE
            )

        wp_evs = _load_events(engine, "inst-wp")
        jq_evs = _load_events(engine, "inst-jq")

        assert len(wp_evs) == 1
        assert len(jq_evs) == 1

        wp_ev, jq_ev = wp_evs[0], jq_evs[0]

        # Event kind is identical.
        assert wp_ev.kind == jq_ev.kind == EventKind.MESSAGE_RECEIVED.value
        # message_id links event to MessageQueue row in both paths.
        assert wp_ev.message_id == wp_result.message_id
        assert jq_ev.message_id == jq_result.message_id

        # Event payload parity on shared fields. ``message_id`` is a
        # per-call UUID (necessarily different between the two enqueues);
        # we compare the contractually shared fields only.
        wp_data = json.loads(wp_ev.data)
        jq_data = json.loads(jq_ev.data)
        for field in ("content", "source", "role"):
            assert wp_data[field] == jq_data[field], (
                f"event.data.{field} diverges between paths: "
                f"wp={wp_data[field]!r} jq={jq_data[field]!r}"
            )
        assert wp_data["content"] == _SAMPLE_MESSAGE
        assert wp_data["source"] == _SAMPLE_SOURCE
        # Each event's message_id must match its own call's returned UUID.
        assert wp_data["message_id"] == wp_result.message_id
        assert jq_data["message_id"] == jq_result.message_id

    # ────────────────────────────────────────────────────────────────────────
    # 4. Dispatch divergence: Task row (WorkerPool only)
    # ────────────────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_4_worker_pool_path_creates_task_row(
        self, engine, manager, messaging_service
    ):
        """WorkerPool writes a ``Task`` row + notifies the pool; JQ does neither."""
        _seed_instance(engine, instance_id="inst-wp")
        _seed_instance(engine, instance_id="inst-jq")

        with patch("daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"):
            await messaging_service.enqueue_message(
                instance_id="inst-wp", message="m", source="api"
            )
            await messaging_service.enqueue_message(dispatch_path="jobqueue", 
                instance_id="inst-jq", message="m", source="api"
            )

        wp_tasks = _load_tasks(engine, "inst-wp")
        jq_tasks = _load_tasks(engine, "inst-jq")

        # WP path created a Task; JQ path did not.
        assert len(wp_tasks) == 1
        assert len(jq_tasks) == 0

        wp_task = wp_tasks[0]
        assert wp_task.task_type == TaskType.PROCESS_MESSAGE.value
        assert wp_task.status == TaskStatus.PENDING.value

        # WorkerPool.notify_work called by WP only.
        manager._worker_pool.notify_work.assert_called_once()

    # ────────────────────────────────────────────────────────────────────────
    # 5. Dispatch divergence: JobItem enqueue (JobQueue only)
    # ────────────────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_5_jobqueue_path_calls_jq_service(
        self, engine, manager, messaging_service
    ):
        """JQ path calls ``_job_queue_service.enqueue``; WP path does not."""
        _seed_instance(engine, instance_id="inst-wp")
        _seed_instance(engine, instance_id="inst-jq")

        with patch("daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"):
            await messaging_service.enqueue_message(
                instance_id="inst-wp", message="m", source="api"
            )
            await messaging_service.enqueue_message(dispatch_path="jobqueue", 
                instance_id="inst-jq", message="m", source="api"
            )

        # JQ service called once; WP path did not call it.
        manager._job_queue_service.enqueue.assert_awaited_once()
        call_kwargs = manager._job_queue_service.enqueue.await_args.kwargs
        assert call_kwargs["job_type"] == "message"
        assert call_kwargs["instance_id"] == "inst-jq"
        assert call_kwargs["message"] == "m"
        assert call_kwargs["source"] == "api"
        # The dispatched message_id should land in the JQ job metadata so
        # downstream workers can correlate.
        assert call_kwargs["metadata"]["message_id"]

    # ────────────────────────────────────────────────────────────────────────
    # 6. version + last_activity_at equivalence
    # ────────────────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_6_version_and_activity_bump_equivalence(
        self, engine, manager, messaging_service
    ):
        """Both paths bump ``version`` and set ``last_activity_at``."""
        _seed_instance(engine, instance_id="inst-wp", version=7)
        _seed_instance(engine, instance_id="inst-jq", version=7)

        with patch("daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"):
            await messaging_service.enqueue_message(
                instance_id="inst-wp", message="x", source="api"
            )
            await messaging_service.enqueue_message(dispatch_path="jobqueue", 
                instance_id="inst-jq", message="x", source="api"
            )

        wp_inst = _load_instance(engine, "inst-wp")
        jq_inst = _load_instance(engine, "inst-jq")

        assert wp_inst.version == 8
        assert jq_inst.version == 8
        assert wp_inst.last_activity_at is not None
        assert jq_inst.last_activity_at is not None

    # ────────────────────────────────────────────────────────────────────────
    # 7. AsyncMessageResult contract equivalence
    # ────────────────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_7_async_message_result_equivalence(
        self, engine, manager, messaging_service
    ):
        """Both return ``AsyncMessageResult``; only JQ populates ``job_id``."""
        _seed_instance(engine, instance_id="inst-wp")
        _seed_instance(engine, instance_id="inst-jq")

        with patch("daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"):
            wp_result = await messaging_service.enqueue_message(
                instance_id="inst-wp", message="m", source="api"
            )
            jq_result = await messaging_service.enqueue_message(dispatch_path="jobqueue", 
                instance_id="inst-jq", message="m", source="api"
            )

        # Type contract is identical.
        assert isinstance(wp_result, AsyncMessageResult)
        assert isinstance(jq_result, AsyncMessageResult)

        # Both must return message_id, instance_id, status="queued".
        assert wp_result.message_id and jq_result.message_id
        assert wp_result.instance_id == "inst-wp"
        assert jq_result.instance_id == "inst-jq"
        assert wp_result.status == jq_result.status == "queued"

        # job_id asymmetry: JQ path surfaces the JobItem.job_id; WP path
        # has no job (it dispatches via the Task table) so job_id stays None.
        assert wp_result.job_id is None, (
            "WorkerPool path must NOT populate job_id — there is no JobItem"
        )
        assert jq_result.job_id == "job-jq-xyz", (
            "JobQueue path must surface the JobItem.job_id from _job_queue_service"
        )

    # ────────────────────────────────────────────────────────────────────────
    # 8. images metadata equivalence
    # ────────────────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_8_images_metadata_equivalence(
        self, engine, manager, messaging_service
    ):
        """``images`` is stored on the MessageQueue row by both paths."""
        _seed_instance(engine, instance_id="inst-wp")
        _seed_instance(engine, instance_id="inst-jq")

        with patch("daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"):
            await messaging_service.enqueue_message(
                instance_id="inst-wp",
                message="vision",
                source="api",
                images=_SAMPLE_IMAGES,
            )
            await messaging_service.enqueue_message(dispatch_path="jobqueue", 
                instance_id="inst-jq",
                message="vision",
                source="api",
                images=_SAMPLE_IMAGES,
            )

        wp = _load_message_queues(engine, "inst-wp")[0]
        jq = _load_message_queues(engine, "inst-jq")[0]
        assert wp.images == _SAMPLE_IMAGES
        assert jq.images == _SAMPLE_IMAGES

        # JQ path also passes images through to JobItem metadata so the
        # downstream MessageJobHandler can hydrate the multimodal payload.
        jq_call_kwargs = manager._job_queue_service.enqueue.await_args.kwargs
        assert jq_call_kwargs["metadata"]["images"] == _SAMPLE_IMAGES

    # ────────────────────────────────────────────────────────────────────────
    # 9. priority parameter equivalence
    # ────────────────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    @pytest.mark.parametrize("priority", [0, 1, 5])
    async def test_9_priority_parameter_equivalence(
        self, engine, manager, messaging_service, priority
    ):
        """``priority`` parameter is stored on MessageQueue by both paths."""
        wp_id = f"inst-wp-{priority}"
        jq_id = f"inst-jq-{priority}"
        _seed_instance(engine, instance_id=wp_id)
        _seed_instance(engine, instance_id=jq_id)

        with patch("daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"):
            await messaging_service.enqueue_message(
                instance_id=wp_id, message="m", source="api", priority=priority
            )
            await messaging_service.enqueue_message(dispatch_path="jobqueue", 
                instance_id=jq_id, message="m", source="api", priority=priority
            )

        wp = _load_message_queues(engine, wp_id)[0]
        jq = _load_message_queues(engine, jq_id)[0]
        assert wp.priority == priority
        assert jq.priority == priority

        # JQ path forwards priority to the JobItem (where ordering matters).
        jq_call_kwargs = manager._job_queue_service.enqueue.await_args.kwargs
        assert jq_call_kwargs["priority"] == priority

    # ────────────────────────────────────────────────────────────────────────
    # 10. SSE status_change equivalence
    # ────────────────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_10_sse_status_change_equivalence(
        self, engine, manager, messaging_service
    ):
        """Both paths emit ``_live_hub.stream_status_change`` on IDLE → RUNNING."""
        _seed_instance(engine, instance_id="inst-wp")
        _seed_instance(engine, instance_id="inst-jq")

        # Reset mock so we can count calls per path.
        manager._live_hub.stream_status_change.reset_mock()

        with patch("daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"):
            await messaging_service.enqueue_message(
                instance_id="inst-wp", message="m", source="api"
            )
            await messaging_service.enqueue_message(dispatch_path="jobqueue", 
                instance_id="inst-jq", message="m", source="api"
            )

        # Exactly one SSE per path (each transitioned IDLE → RUNNING).
        assert manager._live_hub.stream_status_change.await_count == 2

        # Both calls targeted the running status with the correct instance id.
        wp_call = manager._live_hub.stream_status_change.await_args_list[0]
        jq_call = manager._live_hub.stream_status_change.await_args_list[1]
        assert wp_call.args[0] == "inst-wp"
        assert jq_call.args[0] == "inst-jq"
        assert wp_call.args[1] == InstanceStatus.RUNNING.value
        assert jq_call.args[1] == InstanceStatus.RUNNING.value
