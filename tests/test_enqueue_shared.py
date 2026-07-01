"""Tests for the shared ``_prepare_enqueued_message`` helper and the
unified ``enqueue_message`` dispatcher.

These tests verify the unified dispatcher end-to-end — every test
exercises a single ``enqueue_message`` call (or a single
``_prepare_enqueued_message`` call for the helper-level tests) and
asserts the observable state.

What's verified:

  * MessageQueue row is created with the input fields.
  * Event row is created and links to the MessageQueue row by message_id.
  * Status transitions (any non-RUNNING / non-PAUSED state → RUNNING,
    incl. terminal COMPLETED / TERMINATED / ERROR / FAILED revival)
    and ``version`` + ``last_activity_at`` bumps.
  * PAUSED instances are NOT auto-resumed.
  * Title generation fires on IDLE → RUNNING and stays silent on
    reactivation transitions.
  * Internal source prefixes map to the correct ``MessageType``.
  * ``_prepare_enqueued_message`` contract: returns the right context
    fields, raises on shutdown, atomically writes MessageQueue + Task
    + Event rows.

Run with::

    pytest tests/test_enqueue_shared.py -v
"""

from __future__ import annotations

import json
import logging
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
    agent_id: str = "developer",
    agent_dir: str = "/agents/developer",
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

    # JobQueueService.enqueue is NEVER called for messages (D13 invariant).
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


class TestMessageQueueRow:
    """``enqueue_message`` creates a ``MessageQueue`` row with the input fields."""

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
    async def test_internal_sources_map_to_correct_message_types(
        self, engine, manager, messaging_service
    ):
        """``source`` prefix determines ``MessageType`` for the unified dispatcher."""
        # Use a different instance per call to avoid duplicate row conflicts.
        sources_and_types = [
            ("api", MessageType.HUMAN.value),
            ("internal_agent:other", MessageType.AGENT.value),
            ("internal_report:child", MessageType.COMPLETION_REPORT.value),
            ("internal_error_report:child", MessageType.ERROR_REPORT.value),
        ]
        with patch(
            "daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"
        ):
            for idx, (src, expected) in enumerate(sources_and_types):
                iid = f"inst-msg-{idx}"
                _seed_instance(engine, instance_id=iid, status=InstanceStatus.RUNNING.value)
                await messaging_service.enqueue_message(
                    instance_id=iid,
                    message="x",
                    source=src,
                    priority=1,
                )

        for idx, (src, expected) in enumerate(sources_and_types):
            iid = f"inst-msg-{idx}"
            rows = _load_message_queues(engine, iid)
            assert len(rows) == 1
            assert rows[0].type == expected, (
                f"source={src!r} expected type={expected!r}, got {rows[0].type!r}"
            )


# ──────────────────────────────────────────────────────────────────────────────
# 2. Event row parity
# ──────────────────────────────────────────────────────────────────────────────


class TestEventRow:
    """``enqueue_message`` creates a ``MESSAGE_RECEIVED`` event."""

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
            jq_result = await messaging_service.enqueue_message(
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


class TestStatusTransition:
    """``enqueue_message`` performs the documented status transitions."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("initial", [
        InstanceStatus.IDLE.value,
        InstanceStatus.WAITING_CHILDREN.value,
        InstanceStatus.COMPLETED.value,
        InstanceStatus.TERMINATED.value,
        InstanceStatus.ERROR.value,
        InstanceStatus.FAILED.value,
    ])
    async def test_status_transitions_to_running(
        self, engine, manager, messaging_service, initial
    ):
        """All non-RUNNING, non-PAUSED states become ``RUNNING``.

        Terminal states (COMPLETED / TERMINATED / ERROR / FAILED) are
        reactivated on a new message — "terminal" only records WHY the
        last run stopped; the checkpoint + history persist and reload on
        the next ``graph.astream``. Reviving a terminated instance is the
        same machinery as reviving a completed one (revive-fix,
        2026-07-01). Without this, a message to a terminated instance
        creates a Task that ``claim_pending_task`` can never claim
        (it excludes terminated instances) → stuck pending forever.
        """
        instance_id = f"inst-{initial}"
        _seed_instance(engine, instance_id=instance_id, status=initial, version=3)
        with patch(
            "daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"
        ):
            await messaging_service.enqueue_message(
                instance_id=instance_id, message="m", source="api"
            )

        inst = _load_instance(engine, instance_id)
        assert inst.status == InstanceStatus.RUNNING.value
        assert inst.version == 4  # 3 + 1 enqueue call
        assert inst.last_activity_at is not None
        assert manager._live_hub.stream_status_change.await_count == 1
        call = manager._live_hub.stream_status_change.await_args
        assert call.args[0] == instance_id
        assert call.args[1] == InstanceStatus.RUNNING.value

    @pytest.mark.asyncio
    async def test_paused_instance_is_not_resumed(
        self, engine, manager, messaging_service
    ):
        """``PAUSED`` is intentionally **not** auto-resumed.

        The helper leaves PAUSED alone; only non-RUNNING, non-PAUSED
        states are auto-transitioned. PAUSED is routed through the
        explicit resume path (see the messages endpoint) so the
        cooperative pause gate and resume cascade stay in control.
        """
        _seed_instance(engine, instance_id="inst-paused", status=InstanceStatus.PAUSED.value)
        with patch(
            "daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"
        ):
            await messaging_service.enqueue_message(
                instance_id="inst-paused", message="x", source="api"
            )

        inst = _load_instance(engine, "inst-paused")
        assert inst.status == InstanceStatus.PAUSED.value, (
            "PAUSED instances must stay PAUSED — helper only resumes "
            "non-RUNNING, non-PAUSED states via the explicit resume path."
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

        inst = _load_instance(engine, "inst-run")
        assert inst.status == InstanceStatus.RUNNING.value
        # No transition -> no SSE status_change.
        manager._live_hub.stream_status_change.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_instance_warns_but_creates_message(
        self, engine, manager, messaging_service, caplog
    ):
        """When the instance row is missing, the helper still creates the
        MessageQueue + Event, logs a warning, and does NOT raise.

        D13 unified the two dispatch paths. Both tolerate a briefly-
        missing instance row (race window during instance creation) by
        logging a warning and continuing — no ValueError is raised.
        """
        with patch(
            "daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"
        ):
            with caplog.at_level(
                logging.WARNING, logger="daemon.services.instance_messaging"
            ):
                await messaging_service.enqueue_message(
                    instance_id="ghost-inst", message="hi", source="api"
                )

        # MessageQueue and Event rows should still exist.
        assert len(_load_message_queues(engine, "ghost-inst")) == 1
        assert len(_load_events(engine, "ghost-inst")) == 1
        # And no status SSE was emitted (no instance to transition).
        manager._live_hub.stream_status_change.assert_not_awaited()
        # A warning about the missing instance must be logged.
        assert any(
            "ghost-inst" in record.getMessage() and "not found" in record.getMessage()
            for record in caplog.records
        ), "expected a warning log about missing instance 'ghost-inst'"


# ──────────────────────────────────────────────────────────────────────────────
# 4. Title generation
# ──────────────────────────────────────────────────────────────────────────────


class TestTitleGeneration:
    """``enqueue_message`` fires ``_maybe_trigger_title_generation`` on
    ``IDLE`` → ``RUNNING``.
    """

    @pytest.mark.asyncio
    async def test_triggers_title_on_idle_to_running(
        self, engine, manager, messaging_service
    ):
        _seed_instance(engine, instance_id="inst-1", status=InstanceStatus.IDLE.value)
        with patch(
            "daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"
        ) as mock_bridge:
            await messaging_service.enqueue_message(
                instance_id="inst-1", message="hello", source="api"
            )

        # Bridge called once for the IDLE -> RUNNING transition.
        assert mock_bridge.call_count == 1

    @pytest.mark.asyncio
    async def test_does_not_trigger_title_on_completed_to_running(
        self, engine, manager, messaging_service
    ):
        """``COMPLETED`` → ``RUNNING`` (reactivation) does NOT trigger title gen.

        Title gen is reserved for the *first* message (IDLE → RUNNING).
        Reactivation of a completed instance is treated as a conversation
        resume, not a new conversation.
        """
        _seed_instance(
            engine, instance_id="inst-c", status=InstanceStatus.COMPLETED.value
        )
        with patch(
            "daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"
        ) as mock_bridge:
            await messaging_service.enqueue_message(
                instance_id="inst-c", message="resume", source="api"
            )

        # Title generation should NOT fire for the reactivation path.
        mock_bridge.assert_not_called()

    @pytest.mark.asyncio
    async def test_does_not_trigger_title_on_waiting_children_to_running(
        self, engine, manager, messaging_service
    ):
        """``WAITING_CHILDREN`` → ``RUNNING`` does NOT trigger title gen.

        The parent is not starting a new conversation — a child report
        is unblocking it.
        """
        _seed_instance(
            engine, instance_id="inst-w", status=InstanceStatus.WAITING_CHILDREN.value
        )
        with patch(
            "daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"
        ) as mock_bridge:
            await messaging_service.enqueue_message(
                instance_id="inst-w", message="child report", source="api"
            )

        mock_bridge.assert_not_called()


# ──────────────────────────────────────────────────────────────────────────────
# 5. Helper isolation
# ──────────────────────────────────────────────────────────────────────────────


class TestPrepareEnqueuedMessageHelper:
    """Tests that call ``_prepare_enqueued_message`` directly, bypassing
    the dispatch layer.

    These verify the helper's contract in isolation:

      * Returns a ``_PreparedEnqueueContext`` with the right fields.
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
            path_label="WorkerPool",
        )

        assert isinstance(ctx, _PreparedEnqueueContext)
        assert ctx.message_id  # minted
        assert ctx.msg_type == MessageType.HUMAN.value
        assert ctx.status_changed_to_running is True
        assert ctx.is_idle_to_running is True
        assert ctx.previous_status == InstanceStatus.IDLE.value
        assert ctx.instance_agent_id == "developer"

        # DB state: 1 message queue + 1 task + 1 event (atomic).
        assert len(_load_message_queues(engine, "inst-1")) == 1
        assert len(_load_tasks(engine, "inst-1")) == 1
        assert len(_load_events(engine, "inst-1")) == 1
        inst = _load_instance(engine, "inst-1")
        assert inst.status == InstanceStatus.RUNNING.value

    def test_helper_always_inserts_task_row(
        self, engine, manager, messaging_service
    ):
        """Phase 4: ``_prepare_enqueued_message`` ALWAYS inserts a Task row
        in the same transaction as the MessageQueue row. Task row creation
        is unconditional.
        """
        _seed_instance(engine, instance_id="inst-1", status=InstanceStatus.RUNNING.value)

        ctx = messaging_service._prepare_enqueued_message(
            instance_id="inst-1",
            message="x",
            source="api",
            priority=1,
            images=None,
            metadata=None,
            path_label="WorkerPool",
        )

        tasks = _load_tasks(engine, "inst-1")
        assert len(tasks) == 1
        task = tasks[0]
        assert task.task_type == TaskType.PROCESS_MESSAGE.value
        assert task.instance_id == "inst-1"
        assert task.message_id == ctx.message_id
        assert task.status == TaskStatus.PENDING.value

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
        """Task + MessageQueue commit atomically — both visible after the call."""
        _seed_instance(engine, instance_id="inst-1", status=InstanceStatus.RUNNING.value)

        ctx = messaging_service._prepare_enqueued_message(
            instance_id="inst-1",
            message="atomic",
            source="api",
            priority=1,
            images=None,
            metadata=None,
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
            path_label="",
        )

        row = _load_message_queues(engine, "inst-1")[0]
        assert row.images == images
        assert row.message_metadata == meta
        assert row.priority == 0


# ──────────────────────────────────────────────────────────────────────────────
# 6. Dispatch-layer invariants
# ──────────────────────────────────────────────────────────────────────────────


class TestDispatchLayerInvariants:
    """Single dispatcher: Task row + WorkerPool notify; no JobQueue.enqueue."""

    @pytest.mark.asyncio
    async def test_creates_task_and_notifies_pool(
        self, engine, manager, messaging_service
    ):
        """``enqueue_message`` writes a Task row and notifies the WorkerPool."""
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
        # JobQueueService.enqueue NOT called.
        manager._job_queue_service.enqueue.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_returns_job_id_as_task_work_id(
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
        calling agent.
        """
        _seed_instance(engine, instance_id="inst-1", status=InstanceStatus.RUNNING.value)
        with patch(
            "daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"
        ):
            result = await messaging_service.enqueue_message(
                instance_id="inst-1", message="x", source="api"
            )

        # ``job_id`` must be a UUID4 (task.work_id), not None, not the
        # legacy ``"job-test-123"`` JobItem.job_id sentinel from the mock,
        # and not a stringified int (the previous adapter contract).
        assert result.job_id is not None
        import re
        uuid4_pattern = re.compile(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
        )
        assert uuid4_pattern.match(result.job_id), (
            f"job_id must be a UUID4 (task.work_id), got {result.job_id!r}"
        )