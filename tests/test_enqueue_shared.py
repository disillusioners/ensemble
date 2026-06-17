"""Phase 5 — Tests for the shared `_prepare_enqueued_message` helper.

These tests verify that the ``_prepare_enqueued_message`` helper — the
extracted shared prelude for ``enqueue_message`` (WorkerPool) and
``enqueue_message_via_jq`` (JobQueue) — produces **identical** pre-state
side effects when called from either public method.

What's tested (SHARED behavior, identical for both paths):

  1. **MessageQueue row parity** — both create a row with the same
     ``instance_id``, ``content``, ``source``, and ``type``.
  2. **Event row parity** — both create a ``MESSAGE_RECEIVED`` event
     row, sharing the same ``message_id``.
  3. **Status transition parity** — both transition
     ``IDLE`` / ``WAITING_CHILDREN`` / ``COMPLETED`` → ``RUNNING`` and
     bump ``last_activity_at`` / ``version``.
  4. **Title generation parity** — both fire ``_maybe_trigger_title_generation``
     on ``IDLE`` → ``RUNNING``.
  5. **Helper isolation** — calling ``_prepare_enqueued_message`` directly
     produces correct DB state and the expected ``_PreparedEnqueueContext``.

What's **NOT** tested (intentionally different between paths):

  - ``Task`` row creation (only in WorkerPool path).
  - ``JobQueueService.enqueue`` call (only in JobQueue path).
  - WorkerPool ``notify_work`` (only in WorkerPool path).
  - The ``_job_queue_service.enqueue`` call (only in JobQueue path).

These are tested separately in ``tests/job_queue/`` and
``tests/test_worker_notification*.py``.

Run with::

    pytest tests/test_enqueue_shared.py -v
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select

# Register all tables with SQLModel.metadata via model imports.
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
from daemon.services.instance_messaging import (
    InstanceMessagingService,
    _PreparedEnqueueContext,
)
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
    """Real ``SQLModelInstanceRepository`` backed by the in-memory engine."""
    return SQLModelInstanceRepository(engine)


@pytest.fixture
def cancellation_service():
    """Real ``CancellationService`` with ``is_shutting_down=False``."""
    service = MagicMock(spec=CancellationService)
    service.is_shutting_down = False
    return service


@pytest.fixture
def write_guard():
    """Real ``WritePauseGuard`` (no active pause)."""
    return WritePauseGuard()


def _seed_instance(
    engine,
    *,
    instance_id: str = "inst-1",
    agent_id: str = "coder",
    agent_dir: str = "/agents/coder",
    status: str = InstanceStatus.IDLE.value,
    project_id: str | None = "test-project",
    version: int = 1,
) -> Instance:
    """Insert an ``Instance`` row in the test engine.

    Returns the SQLModel instance as written (not enriched — no
    ``_load_children`` call, since this fixture doesn't add hierarchy
    rows).
    """
    inst = Instance(
        instance_id=instance_id,
        agent_id=agent_id,
        agent_dir=agent_dir,
        project_id=project_id,
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


def _build_manager(
    engine,
    instance_repository: SQLModelInstanceRepository,
    write_guard: WritePauseGuard,
) -> MagicMock:
    """Build a mock ``InstanceManager`` exposing only the attributes
    ``_prepare_enqueued_message`` and the public enqueue methods
    actually touch.
    """
    manager = MagicMock()
    manager.engine = engine
    manager.write_guard = write_guard
    manager._instance_repository = instance_repository

    # ``_live_hub.stream_status_change`` is awaited after status transition.
    manager._live_hub = MagicMock()
    manager._live_hub.stream_status_change = AsyncMock()

    # ``enqueue_message`` calls ``_worker_pool.notify_work()``; None is fine
    # — the code guards with ``if self._manager._worker_pool is not None``.
    manager._worker_pool = MagicMock()
    manager._worker_pool.notify_work = MagicMock()

    # ``enqueue_message_via_jq`` looks up instance + dispatches via JQS.
    manager._job_queue_service = MagicMock()
    manager._job_queue_service.enqueue = AsyncMock(
        return_value=MagicMock(job_id="job-test-123")
    )

    # ``_maybe_trigger_title_generation`` calls
    # ``_generate_and_broadcast_title`` via ``MainLoopBridge.run_async_no_wait``.
    manager._generate_and_broadcast_title = AsyncMock()

    return manager


@pytest.fixture
def manager(engine, instance_repository, write_guard):
    return _build_manager(engine, instance_repository, write_guard)


@pytest.fixture
def messaging_service(manager, cancellation_service):
    """``InstanceMessagingService`` wired to real engine + mock manager."""
    return InstanceMessagingService(
        manager=manager,
        cancellation_service=cancellation_service,
    )


def _load_message_queues(engine, instance_id: str) -> list[MessageQueue]:
    """Fetch all ``MessageQueue`` rows for ``instance_id`` (commit-safe)."""
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
# 1. MessageQueue row parity
# ──────────────────────────────────────────────────────────────────────────────


class TestMessageQueueRowParity:
    """Both enqueue paths must create an identical ``MessageQueue`` row."""

    @pytest.mark.asyncio
    async def test_enqueue_message_creates_message_queue_row(
        self, engine, manager, messaging_service
    ):
        """``enqueue_message`` inserts a MessageQueue with the input fields."""
        _seed_instance(engine, instance_id="inst-1", status=InstanceStatus.IDLE.value)
        # Patch out title-generation bridge; we only care about row state here.
        with patch(
            "daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"
        ):
            await messaging_service.enqueue_message(
                instance_id="inst-1",
                message="hello world",
                source="api",
                priority=1,
            )

        rows = _load_message_queues(engine, "inst-1")
        assert len(rows) == 1, "enqueue_message should create exactly one MessageQueue"
        row = rows[0]
        assert row.instance_id == "inst-1"
        assert row.content == "hello world"
        assert row.source == "api"
        assert row.type == MessageType.HUMAN.value
        assert row.status == MessageStatus.READY.value
        assert row.priority == 1
        assert row.message_id  # auto-minted

    @pytest.mark.asyncio
    async def test_enqueue_message_via_jq_creates_message_queue_row(
        self, engine, manager, messaging_service
    ):
        """``enqueue_message_via_jq`` inserts a MessageQueue with the input fields."""
        _seed_instance(engine, instance_id="inst-1", status=InstanceStatus.IDLE.value)
        with patch(
            "daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"
        ):
            await messaging_service.enqueue_message_via_jq(
                instance_id="inst-1",
                message="hello world",
                source="api",
                priority=1,
            )

        rows = _load_message_queues(engine, "inst-1")
        assert len(rows) == 1, "enqueue_message_via_jq should create exactly one MessageQueue"
        row = rows[0]
        assert row.instance_id == "inst-1"
        assert row.content == "hello world"
        assert row.source == "api"
        assert row.type == MessageType.HUMAN.value
        assert row.status == MessageStatus.READY.value
        assert row.priority == 1
        assert row.message_id

    @pytest.mark.asyncio
    async def test_message_queue_rows_are_identical(
        self, engine, manager, messaging_service
    ):
        """Both paths produce a MessageQueue with **the same** fields.

        The only difference allowed is the ``message_id`` (UUIDs are
        minted per-call) and row ``id`` / ``enqueued_at`` (auto-generated).
        """
        _seed_instance(engine, instance_id="inst-a", status=InstanceStatus.IDLE.value)
        _seed_instance(engine, instance_id="inst-b", status=InstanceStatus.IDLE.value)
        with patch(
            "daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"
        ):
            wp_result = await messaging_service.enqueue_message(
                instance_id="inst-a",
                message="identical body",
                source="telegram:user:42",
                priority=0,
            )
            jq_result = await messaging_service.enqueue_message_via_jq(
                instance_id="inst-b",
                message="identical body",
                source="telegram:user:42",
                priority=0,
            )

        wp_rows = _load_message_queues(engine, "inst-a")
        jq_rows = _load_message_queues(engine, "inst-b")
        assert len(wp_rows) == 1
        assert len(jq_rows) == 1

        wp, jq = wp_rows[0], jq_rows[0]
        # Fields that must be equal (shared by the helper):
        for field in (
            "content",
            "source",
            "type",
            "status",
            "priority",
            "images",
        ):
            assert getattr(wp, field) == getattr(jq, field), (
                f"Field {field!r} differs: wp={getattr(wp, field)!r} "
                f"jq={getattr(jq, field)!r}"
            )

        # message_id must be returned by both, but the value itself is per-call.
        assert wp_result.message_id == wp.message_id
        assert jq_result.message_id == jq.message_id
        assert wp.message_id != jq.message_id  # independent UUIDs

    @pytest.mark.asyncio
    async def test_internal_sources_map_to_correct_message_types(
        self, engine, manager, messaging_service
    ):
        """``source`` prefix determines ``MessageType`` — verified for both paths."""
        _seed_instance(engine, instance_id="inst-r", status=InstanceStatus.RUNNING.value)
        with patch(
            "daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"
        ):
            await messaging_service.enqueue_message(
                instance_id="inst-r",
                message="agent to agent",
                source="internal_agent:other",
            )
            await messaging_service.enqueue_message_via_jq(
                instance_id="inst-r",
                message="completion report",
                source="internal_report:child",
            )

        rows = _load_message_queues(engine, "inst-r")
        by_source = {r.source: r.type for r in rows}
        assert by_source["internal_agent:other"] == MessageType.AGENT.value
        assert by_source["internal_report:child"] == MessageType.COMPLETION_REPORT.value


# ──────────────────────────────────────────────────────────────────────────────
# 2. Event row parity
# ──────────────────────────────────────────────────────────────────────────────


class TestEventRowParity:
    """Both enqueue paths must create a ``MESSAGE_RECEIVED`` event."""

    @pytest.mark.asyncio
    async def test_enqueue_message_creates_message_received_event(
        self, engine, manager, messaging_service
    ):
        _seed_instance(engine, instance_id="inst-1", status=InstanceStatus.IDLE.value)
        with patch(
            "daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"
        ):
            result = await messaging_service.enqueue_message(
                instance_id="inst-1",
                message="hi",
                source="api",
            )

        events = _load_events(engine, "inst-1")
        assert len(events) == 1
        ev = events[0]
        assert ev.kind == EventKind.MESSAGE_RECEIVED.value
        assert ev.instance_id == "inst-1"
        assert ev.message_id == result.message_id

        # ``data`` is a JSON blob with content + role + source.
        data = json.loads(ev.data)
        assert data["content"] == "hi"
        assert data["source"] == "api"
        assert data["message_id"] == result.message_id
        # HUMAN source -> "user" role; COMPLETION_REPORT / AGENT -> still "user"
        # per helper logic (only SYSTEM role switches to "system"). HUMAN is
        # the default branch, so role="user".
        assert data["role"] == "user"

    @pytest.mark.asyncio
    async def test_enqueue_message_via_jq_creates_message_received_event(
        self, engine, manager, messaging_service
    ):
        _seed_instance(engine, instance_id="inst-1", status=InstanceStatus.IDLE.value)
        with patch(
            "daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"
        ):
            result = await messaging_service.enqueue_message_via_jq(
                instance_id="inst-1",
                message="hi",
                source="api",
            )

        events = _load_events(engine, "inst-1")
        assert len(events) == 1
        ev = events[0]
        assert ev.kind == EventKind.MESSAGE_RECEIVED.value
        assert ev.instance_id == "inst-1"
        assert ev.message_id == result.message_id
        data = json.loads(ev.data)
        assert data["content"] == "hi"
        assert data["source"] == "api"

    @pytest.mark.asyncio
    async def test_event_message_id_matches_message_queue_message_id(
        self, engine, manager, messaging_service
    ):
        """The MESSAGE_RECEIVED event's ``message_id`` must match the
        ``MessageQueue.message_id`` — the helper writes both in the
        same transaction.
        """
        _seed_instance(engine, instance_id="inst-1", status=InstanceStatus.IDLE.value)
        with patch(
            "daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"
        ):
            wp_result = await messaging_service.enqueue_message(
                instance_id="inst-1", message="x", source="api"
            )
            jq_result = await messaging_service.enqueue_message_via_jq(
                instance_id="inst-1", message="y", source="api"
            )

        # Two of each (1 message + 1 event per enqueue call).
        mq_rows = _load_message_queues(engine, "inst-1")
        events = _load_events(engine, "inst-1")
        assert len(mq_rows) == 2
        assert len(events) == 2

        mq_ids = {r.message_id for r in mq_rows}
        ev_ids = {e.message_id for e in events}
        assert mq_ids == ev_ids
        assert wp_result.message_id in mq_ids
        assert jq_result.message_id in mq_ids


# ──────────────────────────────────────────────────────────────────────────────
# 3. Status transition parity
# ──────────────────────────────────────────────────────────────────────────────


class TestStatusTransitionParity:
    """Both enqueue paths must perform identical status transitions."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("initial", [
        InstanceStatus.IDLE.value,
        InstanceStatus.WAITING_CHILDREN.value,
        InstanceStatus.COMPLETED.value,
    ])
    async def test_status_transitions_to_running(
        self, engine, manager, messaging_service, initial
    ):
        """``IDLE`` / ``WAITING_CHILDREN`` / ``COMPLETED`` all become ``RUNNING``.

        Uses a fresh instance per path so each call performs the actual
        transition (the first call on a given instance moves the status;
        the second call on the same instance is a no-op).
        """
        # WorkerPool path on its own instance
        wp_id = f"inst-wp-{initial}"
        _seed_instance(engine, instance_id=wp_id, status=initial, version=3)
        with patch(
            "daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"
        ):
            await messaging_service.enqueue_message(
                instance_id=wp_id, message="m", source="api"
            )

        wp_inst = _load_instance(engine, wp_id)
        assert wp_inst.status == InstanceStatus.RUNNING.value
        assert wp_inst.version == 4  # 3 + 1 enqueue call
        assert wp_inst.last_activity_at is not None
        assert manager._live_hub.stream_status_change.await_count == 1
        call = manager._live_hub.stream_status_change.await_args
        assert call.args[0] == wp_id
        assert call.args[1] == InstanceStatus.RUNNING.value

        # JobQueue path on its own instance (fresh manager mock to isolate counts)
        jq_id = f"inst-jq-{initial}"
        _seed_instance(engine, instance_id=jq_id, status=initial, version=3)
        manager._live_hub.stream_status_change.reset_mock()
        with patch(
            "daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"
        ):
            await messaging_service.enqueue_message_via_jq(
                instance_id=jq_id, message="m", source="api"
            )

        jq_inst = _load_instance(engine, jq_id)
        assert jq_inst.status == InstanceStatus.RUNNING.value
        assert jq_inst.version == 4
        assert jq_inst.last_activity_at is not None
        assert manager._live_hub.stream_status_change.await_count == 1
        call = manager._live_hub.stream_status_change.await_args
        assert call.args[0] == jq_id
        assert call.args[1] == InstanceStatus.RUNNING.value

    @pytest.mark.asyncio
    async def test_paused_instance_is_not_resumed(
        self, engine, manager, messaging_service
    ):
        """``PAUSED`` is intentionally **not** auto-resumed.

        The helper leaves PAUSED alone; only IDLE / WAITING_CHILDREN /
        COMPLETED are auto-transitioned.
        """
        _seed_instance(engine, instance_id="inst-paused", status=InstanceStatus.PAUSED.value)
        with patch(
            "daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"
        ):
            await messaging_service.enqueue_message(
                instance_id="inst-paused", message="x", source="api"
            )
            await messaging_service.enqueue_message_via_jq(
                instance_id="inst-paused", message="x", source="api"
            )

        inst = _load_instance(engine, "inst-paused")
        assert inst.status == InstanceStatus.PAUSED.value, (
            "PAUSED instances must stay PAUSED — helper only resumes IDLE / "
            "WAITING_CHILDREN / COMPLETED"
        )
        # No status_change SSE emitted.
        manager._live_hub.stream_status_change.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_running_instance_keeps_running(
        self, engine, manager, messaging_service
    ):
        """An already-RUNNING instance is left alone (no spurious transition)."""
        _seed_instance(engine, instance_id="inst-run", status=InstanceStatus.RUNNING.value)
        with patch(
            "daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"
        ):
            await messaging_service.enqueue_message(
                instance_id="inst-run", message="m", source="api"
            )
            await messaging_service.enqueue_message_via_jq(
                instance_id="inst-run", message="m", source="api"
            )

        inst = _load_instance(engine, "inst-run")
        assert inst.status == InstanceStatus.RUNNING.value
        # No transition -> no SSE status_change.
        manager._live_hub.stream_status_change.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_instance_in_wp_path_warns_but_creates_message(
        self, engine, manager, messaging_service
    ):
        """WorkerPool path: when the instance row is missing, the helper
        still creates the MessageQueue + Event but does NOT raise.
        """
        with patch(
            "daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"
        ):
            await messaging_service.enqueue_message(
                instance_id="ghost-wp", message="hi", source="api"
            )

        # MessageQueue and Event rows should still exist.
        assert len(_load_message_queues(engine, "ghost-wp")) == 1
        assert len(_load_events(engine, "ghost-wp")) == 1
        # And no status SSE was emitted (no instance to transition).
        manager._live_hub.stream_status_change.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_instance_in_jq_path_raises_value_error(
        self, engine, manager, messaging_service
    ):
        """JobQueue path raises ``ValueError`` if the instance is missing.

        This is intentional asymmetry: ``enqueue_message`` (WorkerPool) tolerates
        a missing instance and only logs a warning, while ``enqueue_message_via_jq``
        needs the instance metadata (agent_id, project_id) to enqueue the
        MESSAGE job, so it raises.
        """
        with patch(
            "daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"
        ):
            with pytest.raises(ValueError, match="Instance ghost-jq not found"):
                await messaging_service.enqueue_message_via_jq(
                    instance_id="ghost-jq", message="hi", source="api"
                )


# ──────────────────────────────────────────────────────────────────────────────
# 4. Title generation parity
# ──────────────────────────────────────────────────────────────────────────────


class TestTitleGenerationParity:
    """Both paths fire ``_maybe_trigger_title_generation`` on
    ``IDLE`` → ``RUNNING``."""

    @pytest.mark.asyncio
    async def test_wp_path_triggers_title_on_idle_to_running(
        self, engine, manager, messaging_service
    ):
        _seed_instance(engine, instance_id="inst-wp", status=InstanceStatus.IDLE.value)
        with patch(
            "daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"
        ) as mock_bridge:
            await messaging_service.enqueue_message(
                instance_id="inst-wp", message="hello", source="api"
            )

        # Bridge called once for the IDLE -> RUNNING transition.
        assert mock_bridge.call_count == 1

    @pytest.mark.asyncio
    async def test_jq_path_triggers_title_on_idle_to_running(
        self, engine, manager, messaging_service
    ):
        _seed_instance(engine, instance_id="inst-jq", status=InstanceStatus.IDLE.value)
        with patch(
            "daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"
        ) as mock_bridge:
            await messaging_service.enqueue_message_via_jq(
                instance_id="inst-jq", message="hello", source="api"
            )

        # Bridge called once for the IDLE -> RUNNING transition.
        assert mock_bridge.call_count == 1

    @pytest.mark.asyncio
    async def test_neither_path_triggers_title_on_completed_to_running(
        self, engine, manager, messaging_service
    ):
        """``COMPLETED`` → ``RUNNING`` (reactivation) does NOT trigger title gen.

        Title gen is reserved for the *first* message (IDLE → RUNNING).
        Reactivation of a completed instance is treated as a conversation
        resume, not a new conversation.
        """
        _seed_instance(
            engine, instance_id="inst-c-wp", status=InstanceStatus.COMPLETED.value
        )
        _seed_instance(
            engine, instance_id="inst-c-jq", status=InstanceStatus.COMPLETED.value
        )
        with patch(
            "daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"
        ) as mock_bridge:
            await messaging_service.enqueue_message(
                instance_id="inst-c-wp", message="resume", source="api"
            )
            await messaging_service.enqueue_message_via_jq(
                instance_id="inst-c-jq", message="resume", source="api"
            )

        # Title generation should NOT fire for the reactivation path.
        mock_bridge.assert_not_called()

    @pytest.mark.asyncio
    async def test_neither_path_triggers_title_on_waiting_children_to_running(
        self, engine, manager, messaging_service
    ):
        """``WAITING_CHILDREN`` → ``RUNNING`` does NOT trigger title gen.

        The parent is not starting a new conversation — a child report
        is unblocking it.
        """
        _seed_instance(
            engine, instance_id="inst-w-wp", status=InstanceStatus.WAITING_CHILDREN.value
        )
        _seed_instance(
            engine, instance_id="inst-w-jq", status=InstanceStatus.WAITING_CHILDREN.value
        )
        with patch(
            "daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"
        ) as mock_bridge:
            await messaging_service.enqueue_message(
                instance_id="inst-w-wp", message="child report", source="api"
            )
            await messaging_service.enqueue_message_via_jq(
                instance_id="inst-w-jq", message="child report", source="api"
            )

        mock_bridge.assert_not_called()


# ──────────────────────────────────────────────────────────────────────────────
# 5. Helper isolation
# ──────────────────────────────────────────────────────────────────────────────


class TestPrepareEnqueuedMessageHelper:
    """Tests that call ``_prepare_enqueued_message`` directly, bypassing
    the path-specific dispatch (Task row / JobQueue enqueue).

    These verify the helper's contract in isolation:

      * Returns a ``_PreparedEnqueueContext`` with the right fields.
      * Honors ``create_task_row=True`` (Task row in same transaction).
      * Honors ``create_task_row=False`` (no Task row).
      * Honors ``path_label`` (logged when reactivating COMPLETED).
      * Raises on shutdown.
    """

    def test_helper_returns_prepared_context_for_idle(
        self, engine, manager, messaging_service
    ):
        """IDLE → RUNNING, ``is_idle_to_running=True``."""
        _seed_instance(engine, instance_id="inst-1", status=InstanceStatus.IDLE.value)

        ctx = messaging_service._prepare_enqueued_message(
            instance_id="inst-1",
            message="hi",
            source="api",
            priority=1,
            images=None,
            metadata=None,
            create_task_row=True,
            path_label="WorkerPool",
        )

        assert isinstance(ctx, _PreparedEnqueueContext)
        assert ctx.message_id  # minted
        assert ctx.msg_type == MessageType.HUMAN.value
        assert ctx.status_changed_to_running is True
        assert ctx.is_idle_to_running is True
        assert ctx.previous_status == InstanceStatus.IDLE.value
        assert ctx.instance_agent_id == "coder"

        # DB state: 1 message queue + 1 task + 1 event.
        assert len(_load_message_queues(engine, "inst-1")) == 1
        assert len(_load_tasks(engine, "inst-1")) == 1
        assert len(_load_events(engine, "inst-1")) == 1
        inst = _load_instance(engine, "inst-1")
        assert inst.status == InstanceStatus.RUNNING.value

    def test_helper_create_task_row_true_inserts_task(
        self, engine, manager, messaging_service
    ):
        """``create_task_row=True`` writes a Task row in the same transaction."""
        _seed_instance(engine, instance_id="inst-1", status=InstanceStatus.RUNNING.value)

        ctx = messaging_service._prepare_enqueued_message(
            instance_id="inst-1",
            message="x",
            source="api",
            priority=1,
            images=None,
            metadata=None,
            create_task_row=True,
            path_label="WorkerPool",
        )

        tasks = _load_tasks(engine, "inst-1")
        assert len(tasks) == 1
        task = tasks[0]
        assert task.task_type == TaskType.PROCESS_MESSAGE.value
        assert task.instance_id == "inst-1"
        assert task.message_id == ctx.message_id
        assert task.status == TaskStatus.PENDING.value

    def test_helper_create_task_row_false_skips_task(
        self, engine, manager, messaging_service
    ):
        """``create_task_row=False`` (JobQueue path) writes no Task row."""
        _seed_instance(engine, instance_id="inst-1", status=InstanceStatus.RUNNING.value)

        ctx = messaging_service._prepare_enqueued_message(
            instance_id="inst-1",
            message="x",
            source="api",
            priority=1,
            images=None,
            metadata=None,
            create_task_row=False,
            path_label="",
        )

        assert len(_load_tasks(engine, "inst-1")) == 0
        # MessageQueue and Event are still written — only the Task is path-specific.
        assert len(_load_message_queues(engine, "inst-1")) == 1
        assert len(_load_events(engine, "inst-1")) == 1
        assert ctx.message_id

    def test_helper_is_idle_to_running_false_for_completed(
        self, engine, manager, messaging_service
    ):
        """``is_idle_to_running`` is ``False`` when previous status is COMPLETED."""
        _seed_instance(
            engine, instance_id="inst-c", status=InstanceStatus.COMPLETED.value
        )

        ctx = messaging_service._prepare_enqueued_message(
            instance_id="inst-c",
            message="resume",
            source="api",
            priority=1,
            images=None,
            metadata=None,
            create_task_row=False,
            path_label="JobQueue",
        )

        assert ctx.previous_status == InstanceStatus.COMPLETED.value
        assert ctx.status_changed_to_running is True
        assert ctx.is_idle_to_running is False  # not IDLE, was COMPLETED
        inst = _load_instance(engine, "inst-c")
        assert inst.status == InstanceStatus.RUNNING.value

    def test_helper_running_instance_no_status_change(
        self, engine, manager, messaging_service
    ):
        """Already-RUNNING instance: no status change, no title trigger."""
        _seed_instance(engine, instance_id="inst-r", status=InstanceStatus.RUNNING.value)

        ctx = messaging_service._prepare_enqueued_message(
            instance_id="inst-r",
            message="x",
            source="api",
            priority=1,
            images=None,
            metadata=None,
            create_task_row=False,
            path_label="",
        )

        assert ctx.status_changed_to_running is False
        assert ctx.is_idle_to_running is False
        assert ctx.previous_status == InstanceStatus.RUNNING.value
        # last_activity_at should still be bumped.
        inst = _load_instance(engine, "inst-r")
        assert inst.last_activity_at is not None

    def test_helper_atomicity_task_and_message_committed_together(
        self, engine, manager, messaging_service
    ):
        """When ``create_task_row=True``, the Task + MessageQueue commit
        atomically — i.e., both are visible after the call returns."""
        _seed_instance(engine, instance_id="inst-1", status=InstanceStatus.RUNNING.value)

        ctx = messaging_service._prepare_enqueued_message(
            instance_id="inst-1",
            message="atomic",
            source="api",
            priority=1,
            images=None,
            metadata=None,
            create_task_row=True,
            path_label="WorkerPool",
        )

        # Both rows must reference the same message_id.
        mq = _load_message_queues(engine, "inst-1")[0]
        task = _load_tasks(engine, "inst-1")[0]
        assert mq.message_id == task.message_id == ctx.message_id

    def test_helper_raises_on_shutdown(
        self, engine, manager, messaging_service, cancellation_service
    ):
        """``is_shutting_down=True`` raises ``RuntimeError`` before any DB write."""
        cancellation_service.is_shutting_down = True
        _seed_instance(engine, instance_id="inst-1", status=InstanceStatus.IDLE.value)

        with pytest.raises(RuntimeError, match="shutting down"):
            messaging_service._prepare_enqueued_message(
                instance_id="inst-1",
                message="x",
                source="api",
                priority=1,
                images=None,
                metadata=None,
                create_task_row=True,
                path_label="",
            )

        # No side effects must have been written.
        assert len(_load_message_queues(engine, "inst-1")) == 0
        assert len(_load_events(engine, "inst-1")) == 0
        assert len(_load_tasks(engine, "inst-1")) == 0
        inst = _load_instance(engine, "inst-1")
        assert inst.status == InstanceStatus.IDLE.value  # untouched

    def test_helper_message_type_resolution(self, engine, manager, messaging_service):
        """``source`` prefix -> ``msg_type`` mapping."""
        # All three internal prefixes and the default HUMAN case.
        sources_and_types = [
            ("api", MessageType.HUMAN.value),
            ("internal_agent:other", MessageType.AGENT.value),
            ("internal_report:child", MessageType.COMPLETION_REPORT.value),
            ("internal_error_report:child", MessageType.ERROR_REPORT.value),
        ]
        # Use a different instance per call to avoid duplicate row conflicts.
        for idx, (src, expected) in enumerate(sources_and_types):
            iid = f"inst-msg-{idx}"
            _seed_instance(engine, instance_id=iid, status=InstanceStatus.RUNNING.value)
            ctx = messaging_service._prepare_enqueued_message(
                instance_id=iid,
                message="x",
                source=src,
                priority=1,
                images=None,
                metadata=None,
                create_task_row=False,
                path_label="",
            )
            assert ctx.msg_type == expected, (
                f"source={src!r} expected msg_type={expected!r}, got {ctx.msg_type!r}"
            )

    def test_helper_metadata_and_images_passthrough(
        self, engine, manager, messaging_service
    ):
        """``metadata`` and ``images`` kwargs are stored on the MessageQueue row."""
        _seed_instance(engine, instance_id="inst-1", status=InstanceStatus.RUNNING.value)
        images = ["data:image/png;base64,AAA"]
        meta = {"resume_mode": True, "user_id": "u-1"}

        messaging_service._prepare_enqueued_message(
            instance_id="inst-1",
            message="x",
            source="api",
            priority=0,
            images=images,
            metadata=meta,
            create_task_row=False,
            path_label="",
        )

        row = _load_message_queues(engine, "inst-1")[0]
        assert row.images == images
        assert row.message_metadata == meta
        assert row.priority == 0


# ──────────────────────────────────────────────────────────────────────────────
# 6. Dispatch-layer difference (sanity check)
# ──────────────────────────────────────────────────────────────────────────────


class TestDispatchLayerDifference:
    """Sanity: the only difference between the two paths is the dispatch
    layer (Task row + WorkerPool notify for WP; JobQueueService.enqueue for
    JQ). Everything else (MessageQueue, Event, status) is identical."""

    @pytest.mark.asyncio
    async def test_worker_pool_path_creates_task_and_notifies_pool(
        self, engine, manager, messaging_service
    ):
        _seed_instance(engine, instance_id="inst-1", status=InstanceStatus.RUNNING.value)
        with patch(
            "daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"
        ):
            await messaging_service.enqueue_message(
                instance_id="inst-1", message="x", source="api"
            )

        # Task row created; WorkerPool notified.
        assert len(_load_tasks(engine, "inst-1")) == 1
        manager._worker_pool.notify_work.assert_called_once()
        # JobQueueService.enqueue NOT called for WorkerPool path.
        manager._job_queue_service.enqueue.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_jq_path_skips_task_and_calls_jq_service(
        self, engine, manager, messaging_service
    ):
        _seed_instance(engine, instance_id="inst-1", status=InstanceStatus.RUNNING.value)
        with patch(
            "daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"
        ):
            result = await messaging_service.enqueue_message_via_jq(
                instance_id="inst-1", message="x", source="api"
            )

        # No Task row, no WorkerPool notify.
        assert len(_load_tasks(engine, "inst-1")) == 0
        manager._worker_pool.notify_work.assert_not_called()
        # JobQueueService.enqueue called once.
        manager._job_queue_service.enqueue.assert_awaited_once()
        # The JobItem.job_id surfaces in the AsyncMessageResult.
        assert result.job_id == "job-test-123"
