"""C1 invariant: marker lifetime covers the cascade-execution window
for the ``_prepare_enqueued_message`` (Phase 2) path.

The C1 race fix reorders the post-graph completion path so the deferred-pause
marker is **peeked BEFORE** ``pause_instance_cascade`` runs and **popped
AFTER** the cascade's ``finally`` block. The old ordering (pop-before-cascade)
left the marker empty during the cascade's DB-commit window — a user
message landing in that window would see ``marker=False, db=RUNNING`` and
create a spurious ``PROCESS_MESSAGE`` Task, re-creating the bug the
Phase 2 source-side guard was designed to stop.

This test exercises the **real** peek/await/pop ordering for the
``_prepare_enqueued_message`` path:

  1. ``question_pause_node`` calls ``set_deferred_question_pause(parent_id)``
     — marker set.
  2. The post-graph completion path peeks via ``has_deferred_question_pause``
     and awaits a slow cascade stub (200 ms hold).
  3. While the cascade is awaiting, a user message lands and runs
     ``_prepare_enqueued_message`` — the Phase 2 marker-only guard fires
     and SKIPS the ``PROCESS_MESSAGE`` Task creation (the MessageQueue
     row is preserved as the durable audit record).
  4. The cascade commits PAUSED to the DB and returns.
  5. The finally block pops the marker.

Compared to the previous ``test_pause_race_window_held_enqueue`` (which
set the marker manually and discarded it manually inside the cascade
stub — defeating the purpose of the C1 ordering check), this rewrite
uses the **real** peek/await/pop ordering with a finally block so a
regression of the C1 fix would make this test fail with a Task row
present.

Failure mode against the OLD ordering (pop-before-cascade):
  The marker would be popped BEFORE the cascade's 200 ms delay, so
  during the delay the marker is empty. The Phase 2 marker-only guard
  in ``_prepare_enqueued_message`` would NOT fire and a PROCESS_MESSAGE
  Task WOULD be created. The ``tasks == []`` assertion would fail.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timezone
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

    Same approach as the companion ``test_pause_race_window_held`` test —
    the production ``set_deferred_question_pause``,
    ``has_deferred_question_pause`` and ``pop_deferred_question_pause``
    methods are bound to the mock so the test exercises the real
    atomic-check + atomic-remove semantics.
    """
    manager = MagicMock()
    manager.engine = engine
    manager.write_guard = WritePauseGuard()
    manager._deferred_question_pause = set()
    manager._graph_tasks = {}
    manager._job_queue_service = MagicMock()

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


async def _run_post_graph_finally_block(
    manager: MagicMock, instance_id: str, cascade_callable
) -> bool:
    """Replay the post-graph completion path's finally block.

    Mirrors the exact ordering at
    ``daemon/services/instance_messaging.py:3272-...`` after the C1 fix:
    ``has`` (peek) → ``await shielded(cascade)`` → ``finally: pop``.
    """
    if not manager.has_deferred_question_pause(instance_id):
        return False
    try:
        await asyncio.shield(cascade_callable(instance_id))
    finally:
        manager.pop_deferred_question_pause(instance_id)
    return True


@pytest.mark.asyncio
async def test_c1_marker_lifetime_covers_cascade_window_skips_process_message_task(
    engine: Engine,
) -> None:
    """C1 invariant for the ``_prepare_enqueued_message`` path.

    Sequence:

      1. ``question_pause_node`` sets the marker on the manager.
      2. The post-graph finally block peeks the marker, then enters
         a slow cascade stub that:
           a. records the marker state (C1 ordering check),
           b. waits 200 ms (simulating the cascade's DB-commit window),
           c. commits PAUSED in the DB.
      3. While the cascade is awaiting, a user message lands and runs
         ``_prepare_enqueued_message``. The Phase 2 marker-only guard
         must SKIP the ``PROCESS_MESSAGE`` Task creation.
      4. The cascade finishes and the finally block pops the marker.

    Asserts:

      * The cascade observed the marker set during its execution
        (C1 ordering is in effect).
      * No ``Task`` row was created during the window (Phase 2
        marker guard fired).
      * A ``MessageQueue(READY)`` row was preserved as the durable
        audit record.
      * After the cascade settles, the parent reads PAUSED, the
        MessageQueue is still READY, and the marker is gone.
    """
    instance_id = "c1-race-window-enqueue"
    _seed_running_instance(engine, instance_id)
    write_guard = WritePauseGuard()

    manager = _make_manager_with_real_marker_state(engine)

    cancellation = MagicMock(spec=CancellationService)
    cancellation.is_shutting_down = False
    messaging = InstanceMessagingService(manager, cancellation)

    # 1. question_pause_node sets the marker.
    manager.set_deferred_question_pause(instance_id)

    cascade_entered = threading.Event()
    cascade_release = threading.Event()
    marker_observed_during_cascade: list[bool] = []

    async def slow_cascade(_target_id: str) -> dict:
        # C1 ordering check: marker MUST be set here because the
        # finally block peeks BEFORE awaiting the cascade.
        marker_observed_during_cascade.append(
            manager.has_deferred_question_pause(instance_id)
        )
        cascade_entered.set()
        # Hold the window open for 200 ms so the test can inject a
        # user message in the middle.
        await asyncio.sleep(0.2)
        # Commit PAUSED in the DB.
        await asyncio.to_thread(
            _commit_paused_sync, engine, instance_id
        )
        await asyncio.to_thread(cascade_release.wait, 2.0)
        return {"paused_ids": [instance_id], "skipped_ids": []}

    finally_task = asyncio.create_task(
        _run_post_graph_finally_block(manager, instance_id, slow_cascade)
    )

    # Wait for the cascade to enter the window.
    await asyncio.to_thread(cascade_entered.wait, 2.0)

    # Confirm pre-conditions: marker set, DB still RUNNING.
    assert manager.has_deferred_question_pause(instance_id), (
        "marker must be set during the cascade window (C1 invariant)"
    )
    with Session(engine) as session:
        assert session.get(Instance, instance_id).status == InstanceStatus.RUNNING.value, (
            "DB must still say RUNNING during the cascade window"
        )

    # 3. User message lands DURING the cascade window — Phase 2
    # marker-only guard must SKIP the PROCESS_MESSAGE Task creation.
    await asyncio.to_thread(
        messaging._prepare_enqueued_message,
        instance_id=instance_id,
        message="arrived inside the held race window",
        source="api",
        priority=1,
        images=None,
        metadata=None,
    )

    # Assert: no Task created, MessageQueue preserved as audit row.
    with Session(engine) as session:
        tasks = list(
            session.exec(
                select(Task).where(Task.instance_id == instance_id)
            )
        )
        message = session.exec(
            select(MessageQueue).where(MessageQueue.instance_id == instance_id)
        ).one()
    assert tasks == [], (
        "Phase 2 guard must have SKIPPED PROCESS_MESSAGE Task creation "
        "because the marker is still set during the cascade window "
        "(C1 invariant). If this fails with len(tasks) > 0, the "
        "pop-before-cascade ordering has been reintroduced."
    )
    assert message.status == MessageStatus.READY.value, (
        "MessageQueue row must be preserved as the durable audit record "
        "even when the Task is skipped"
    )

    # Release the cascade and let the finally block complete.
    cascade_release.set()
    cascade_called = await finally_task

    # Post-conditions: cascade ran, marker is gone, DB is PAUSED.
    assert cascade_called is True, "cascade must have been awaited"
    assert marker_observed_during_cascade == [True], (
        "C1 invariant violated: marker was empty during the cascade "
        "execution. The pop-before-cascade ordering has been "
        "reintroduced — pop must run in the finally block AFTER the "
        "cascade completes."
    )
    assert not manager.has_deferred_question_pause(instance_id), (
        "marker must be cleared by the pop in the finally block"
    )
    with Session(engine) as session:
        inst = session.get(Instance, instance_id)
        assert inst.status == InstanceStatus.PAUSED.value
        message = session.exec(
            select(MessageQueue).where(MessageQueue.instance_id == instance_id)
        ).one()
        tasks = list(
            session.exec(
                select(Task).where(Task.instance_id == instance_id)
            )
        )
    assert message.status == MessageStatus.READY.value
    assert tasks == []


def _commit_paused_sync(engine: Engine, instance_id: str) -> None:
    """Sync DB helper used by the slow cascade stub."""
    with Session(engine) as session:
        inst = session.get(Instance, instance_id)
        inst.status = InstanceStatus.PAUSED.value
        session.add(inst)
        session.commit()
