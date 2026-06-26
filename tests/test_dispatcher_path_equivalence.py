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
    async def test_4_both_paths_create_task_row(
        self, engine, manager, messaging_service
    ):
        """D13: BOTH paths write a ``Task`` row + notify the pool.

        Pre-D13: only ``dispatch_path="workerpool"`` created a Task row;
        the ``"jobqueue"`` path created a JobItem instead. Post-D13 both
        paths are identical: they write MessageQueue + Task + Event rows
        and notify the WorkerPool. No JobItem is ever created.
        """
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

        # D13: BOTH paths create exactly one Task row each.
        assert len(wp_tasks) == 1, "WorkerPool path must create a Task row"
        assert len(jq_tasks) == 1, "JobQueue path must ALSO create a Task row (D13)"

        # Task rows are structurally identical (same type, status, etc.).
        wp_task, jq_task = wp_tasks[0], jq_tasks[0]
        assert wp_task.task_type == jq_task.task_type == TaskType.PROCESS_MESSAGE.value
        assert wp_task.status == jq_task.status == TaskStatus.PENDING.value

        # WorkerPool.notify_work called once per enqueue (both paths).
        assert manager._worker_pool.notify_work.call_count == 2

    # ────────────────────────────────────────────────────────────────────────
    # 5. Dispatch divergence: JobItem enqueue (JobQueue only)
    # ────────────────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_5_no_path_calls_jq_service(
        self, engine, manager, messaging_service
    ):
        """D13: NEITHER path calls ``_job_queue_service.enqueue``.

        Pre-D13: ``dispatch_path="jobqueue"`` called
        ``_job_queue_service.enqueue(job_type="message")``. Post-D13
        messages no longer create JobItem rows — the unified WorkerPool
        path writes only Task + MessageQueue rows. ``_job_queue_service``
        is reserved for TASK-type dispatch-queue jobs.
        """
        _seed_instance(engine, instance_id="inst-wp")
        _seed_instance(engine, instance_id="inst-jq")

        with patch("daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"):
            await messaging_service.enqueue_message(
                instance_id="inst-wp", message="m", source="api"
            )
            await messaging_service.enqueue_message(dispatch_path="jobqueue",
                instance_id="inst-jq", message="m", source="api"
            )

        # D13 invariant: ``_job_queue_service.enqueue`` is NEVER called
        # for messages (the guard in ``enqueue_job`` rejects job_type=
        # "message" with ValueError).
        manager._job_queue_service.enqueue.assert_not_awaited()

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
        """D13: BOTH paths return ``AsyncMessageResult`` with ``job_id``
        populated as ``str(task_id)``.

        Pre-D13: only the ``"jobqueue"`` path populated ``job_id`` (from
        ``JobItem.job_id``); the ``"workerpool"`` path left it as
        ``None``. Post-D13 both paths populate ``job_id`` with
        ``str(task_id)`` as the adapter contract — the HTTP route
        discards it and the ``job_continue`` tool returns it as
        ``new_job_id``. The semantic shift from ``JobItem.job_id`` (UUID)
        to ``Task.id`` (int) is intentional and documented on
        ``AsyncMessageResult.job_id``.
        """
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

        # D13: BOTH paths populate job_id with str(task_id). The value is
        # a stringified int (Task PK), not the old "job-jq-xyz" sentinel
        # from the mock — that sentinel is no longer relevant since no
        # JobItem is created.
        assert wp_result.job_id is not None and wp_result.job_id.isdigit(), (
            f"WorkerPool path must populate job_id=str(task_id) post-D13, "
            f"got {wp_result.job_id!r}"
        )
        assert jq_result.job_id is not None and jq_result.job_id.isdigit(), (
            f"JobQueue path must populate job_id=str(task_id) post-D13, "
            f"got {jq_result.job_id!r}"
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

        # D13: ``_job_queue_service.enqueue`` is NEVER called for messages,
        # so the pre-D13 assertion that ``jq_call_kwargs["metadata"]
        # ["images"]`` matches the input is no longer applicable. Images
        # live on the MessageQueue row, which both paths populate.

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

        # D13: priority flows only through MessageQueue.priority (both
        # paths). No JobItem is created, so there is no per-path
        # priority divergence.

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

    # ────────────────────────────────────────────────────────────────────────
    # D13 (Phase 2) invariants
    # ────────────────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_d13_no_jobitem_row_created_for_either_path(
        self, engine, manager, messaging_service
    ):
        """D13 invariant: NO ``job_queue_items`` row is created for ANY
        ``enqueue_message`` call.

        Pre-D13: ``dispatch_path="jobqueue"`` created a JobItem with
        ``job_type="message"``. Post-D13 both paths write only
        ``MessageQueue`` + ``Task`` rows. The ``job_queue_items`` table
        is reserved for TASK-type dispatch-queue jobs only.
        """
        from sqlmodel import Session, select
        from daemon.repositories.job_queue.models import JobItem

        _seed_instance(engine, instance_id="inst-wp")
        _seed_instance(engine, instance_id="inst-jq")

        with patch("daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"):
            await messaging_service.enqueue_message(
                instance_id="inst-wp", message="m", source="api"
            )
            await messaging_service.enqueue_message(dispatch_path="jobqueue",
                instance_id="inst-jq", message="m", source="api"
            )

        with Session(engine) as session:
            jobs = list(session.exec(select(JobItem)))
        assert len(jobs) == 0, (
            f"D13 invariant violated: enqueue_message created {len(jobs)} "
            f"JobItem row(s); expected 0"
        )

    @pytest.mark.asyncio
    async def test_d13_job_id_adapter_is_str_of_task_id(
        self, engine, manager, messaging_service
    ):
        """D13: ``AsyncMessageResult.job_id`` = ``str(task_id)`` for BOTH paths.

        The HTTP ``send_message`` route discards ``job_id`` (unaffected).
        The ``job_continue`` tool returns ``job_id`` as ``new_job_id`` —
        it must be a stable identifier the calling agent can reference
        later for status. ``str(task_id)`` (int-as-string) is that stable
        identifier after D13.
        """
        _seed_instance(engine, instance_id="inst-wp")
        _seed_instance(engine, instance_id="inst-jq")

        with patch("daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"):
            wp_result = await messaging_service.enqueue_message(
                instance_id="inst-wp", message="m", source="api"
            )
            jq_result = await messaging_service.enqueue_message(dispatch_path="jobqueue",
                instance_id="inst-jq", message="m", source="api"
            )

        # job_id must be a stringified int (Task PK), not None, not the
        # legacy "job-jq-xyz" mock sentinel.
        for label, result in (("wp", wp_result), ("jq", jq_result)):
            assert result.job_id is not None, (
                f"D13: {label} path must populate job_id (str(task_id)), got None"
            )
            assert isinstance(result.job_id, str), (
                f"D13: {label} job_id must be str, got {type(result.job_id).__name__}"
            )
            assert result.job_id.isdigit(), (
                f"D13: {label} job_id must be a stringified int (Task PK), "
                f"got {result.job_id!r}"
            )

        # The two job_ids must be distinct (independent Task rows).
        assert wp_result.job_id != jq_result.job_id


# ──────────────────────────────────────────────────────────────────────────────
# D13 guard: enqueue_job(job_type="message") raises ValueError
# ──────────────────────────────────────────────────────────────────────────────


class TestEnqueueJobRejectsMessage:
    """D13 defense-in-depth guard: ``JobQueueService.enqueue`` rejects
    ``job_type="message"`` with ``ValueError``.

    Pre-D13 the JobQueue path created a MESSAGE JobItem for messages.
    Post-D13 messages write only Task + MessageQueue rows. Any leftover
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
