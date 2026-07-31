"""W7 fix: enqueue_message_job must skip JobItem creation when the
Phase 2 marker guard in _prepare_enqueued_message skipped the Task.

The W7 fix is the companion to the C1 marker-lifetime fix: when
``_prepare_enqueued_message`` skips the ``Task`` row (the marker
branch), ``ctx.task_id`` is set to ``None``. Without the W7 guard,
``enqueue_message_job`` would still call
``manager._job_queue_service.enqueue(...)`` to create a JobItem that
has no Task to claim — the JobProcessor would wake the dispatch bus
and the work would be silently lost.

This test exercises the W7 path directly:

  1. Set the deferred-pause marker on the manager (mimicking
     ``question_pause_node`` running inside the graph task).
  2. Call ``enqueue_message_job`` (the W7 path).
  3. Assert:
       * The Phase 2 marker guard in ``_prepare_enqueued_message`` SKIPPED
         the ``Task`` row (the ``MessageQueue`` row IS preserved).
       * ``enqueue_message_job`` returned an ``AsyncMessageResult``
         with ``status="queued"`` and ``queued=False``.
       * ``manager._job_queue_service.enqueue`` was NOT called (no
         JobItem was created).
       * The ``MessageQueue`` row is preserved as the audit record
         (status=READY).
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select

from daemon.manager import InstanceManager
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.message_queue.models import MessageQueue, MessageStatus
from daemon.repositories.task.models import Task
from daemon.services.cancellation import CancellationService
from daemon.services.instance_messaging import InstanceMessagingService
from daemon.write_pause_guard import WritePauseGuard


@pytest.fixture
def engine() -> Engine:
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


def _seed_running_instance(engine: Engine, instance_id: str) -> None:
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id="developer",
                agent_dir="/tmp/agents/developer",
                agent_name="developer",
                project_id="test-project",
                status=InstanceStatus.RUNNING.value,
                instance_metadata={},
            )
        )
        session.commit()


def _make_manager_with_real_marker_state(engine: Engine) -> MagicMock:
    """Mock manager with REAL peek/pop semantics against a real backing set.

    The W7 path runs through ``_prepare_enqueued_message`` which checks
    ``manager._deferred_question_pause`` for the marker. We bind the
    real production methods to the mock so the marker check is
    exercised end-to-end.
    """
    manager = MagicMock()
    manager.engine = engine
    manager.write_guard = WritePauseGuard()
    manager._deferred_question_pause = set()
    manager._graph_tasks = {}
    # JobQueueService is the service that would normally create the
    # JobItem; we assert ``enqueue`` is NOT called when the marker
    # guard skips the Task.
    manager._job_queue_service = MagicMock()
    # The service the W7 path does NOT call when the marker guard fires
    # (enqueue + stamp_message_id).
    manager._job_queue_service.enqueue = MagicMock(
        return_value=asyncio.Future()  # not awaited in this test
    )
    manager._job_queue_service._repository = MagicMock()

    manager.set_deferred_question_pause = (
        InstanceManager.set_deferred_question_pause.__get__(manager)
    )
    manager.has_deferred_question_pause = (
        InstanceManager.has_deferred_question_pause.__get__(manager)
    )
    manager.pop_deferred_question_pause = (
        InstanceManager.pop_deferred_question_pause.__get__(manager)
    )
    return manager


@pytest.mark.asyncio
async def test_w7_marker_guard_skips_jobitem_creation(
    engine: Engine,
) -> None:
    """Phase 2 marker guard + W7 = no orphan JobItem when Task is skipped.

    Sequence:

      1. ``question_pause_node`` sets the marker on the manager.
      2. ``enqueue_message_job`` runs (this is the W7 path).
      3. ``_prepare_enqueued_message`` sees the marker and SKIPS the
         ``Task`` row; the ``MessageQueue`` row is preserved.
      4. W7 guard: ``ctx.task_id is None`` → log warning and return
         without creating a JobItem.

    Asserts:

      * No ``Task`` row was created (Phase 2 marker guard fired).
      * A ``MessageQueue(READY)`` row was preserved.
      * ``manager._job_queue_service.enqueue`` was NOT called.
      * The returned ``AsyncMessageResult`` has ``status="queued"`` and
        ``queued=False``.
    """
    instance_id = "w7-marker-guard"
    _seed_running_instance(engine, instance_id)
    write_guard = WritePauseGuard()

    manager = _make_manager_with_real_marker_state(engine)

    cancellation = MagicMock(spec=CancellationService)
    cancellation.is_shutting_down = False
    messaging = InstanceMessagingService(manager, cancellation)

    # 1. ``question_pause_node`` sets the marker.
    manager.set_deferred_question_pause(instance_id)

    # 2. Run ``enqueue_message_job`` (the W7 path).
    result = await messaging.enqueue_message_job(
        instance_id=instance_id,
        message="arrived during the marker window",
        source="api",
        priority=1,
        images=None,
        metadata=None,
    )

    # 3+4. Assert: marker guard fired (no Task), W7 guard fired (no JobItem).
    with Session(engine) as session:
        tasks = list(
            session.exec(
                select(Task).where(Task.instance_id == instance_id)
            )
        )
        messages = list(
            session.exec(
                select(MessageQueue).where(
                    MessageQueue.instance_id == instance_id
                )
            )
        )
    assert tasks == [], (
        "Phase 2 marker guard must have SKIPPED the PROCESS_MESSAGE "
        "Task creation because the marker is set"
    )
    assert len(messages) == 1, (
        "MessageQueue row must be preserved as the durable audit record "
        "even when the Task is skipped"
    )
    assert messages[0].status == MessageStatus.READY.value

    # W7: JobItem creation was NOT called.
    manager._job_queue_service.enqueue.assert_not_called(), (
        "W7 guard: enqueue_message_job must NOT call "
        "manager._job_queue_service.enqueue when ctx.task_id is None "
        "(otherwise the JobProcessor would wake a non-existent Task)"
    )

    # Return shape: AsyncMessageResult with status="queued", queued=False.
    assert result is not None
    assert result.message_id == messages[0].message_id
    assert result.instance_id == instance_id
    assert result.status == "queued", (
        "W7 must still return status='queued' to the HTTP caller so "
        "the response shape is unchanged from the normal path"
    )
    assert result.queued is False, (
        "queued=False signals that no work was admitted to the queue"
    )

    # Marker is still set (the post-graph callback has not yet run; the
    # marker will be peeked + popped by the production finally block).
    assert manager.has_deferred_question_pause(instance_id)
