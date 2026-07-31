"""Controlled-delay integration test for the marker/DB torn-state window."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.message_queue.models import MessageQueue, MessageStatus
from daemon.repositories.task.models import Task
from daemon.services.cancellation import CancellationService
from daemon.services.instance_lifecycle import InstanceLifecycleService
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


@pytest.mark.asyncio
async def test_message_during_held_pause_window_skips_process_task(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance_id = "held-race-window"
    _seed_running_instance(engine, instance_id)
    write_guard = WritePauseGuard()

    manager = MagicMock()
    manager.engine = engine
    manager.write_guard = write_guard
    manager._deferred_question_pause = set()
    cancellation = MagicMock(spec=CancellationService)
    cancellation.is_shutting_down = False
    messaging = InstanceMessagingService(manager, cancellation)

    lifecycle = InstanceLifecycleService.__new__(InstanceLifecycleService)
    lifecycle._manager = manager
    cascade_entered = asyncio.Event()

    async def delayed_pause_instance_cascade(target_id: str) -> dict:
        assert target_id == instance_id
        cascade_entered.set()
        await asyncio.sleep(0.2)
        await asyncio.to_thread(
            lifecycle._pause_cascade_db_sync,
            engine,
            write_guard,
            tree_ids=[instance_id],
            paused_at_iso=datetime.now(timezone.utc).isoformat(),
            paused_instances_data=[(instance_id, "developer")],
        )
        manager._deferred_question_pause.discard(instance_id)
        return {"paused_ids": [instance_id], "skipped_ids": []}

    monkeypatch.setattr(
        manager, "pause_instance_cascade", delayed_pause_instance_cascade
    )

    # Simulate question_pause_node setting the marker before the deferred
    # callback begins its deliberately delayed database cascade.
    manager._deferred_question_pause.add(instance_id)
    pause_task = asyncio.create_task(manager.pause_instance_cascade(instance_id))
    await cascade_entered.wait()

    with Session(engine) as session:
        assert session.get(Instance, instance_id).status == InstanceStatus.RUNNING.value
    assert instance_id in manager._deferred_question_pause

    # User input lands while marker=SET and DB=RUNNING.
    await asyncio.to_thread(
        messaging._prepare_enqueued_message,
        instance_id=instance_id,
        message="arrived inside the held race window",
        source="api",
        priority=1,
        images=None,
        metadata=None,
    )

    with Session(engine) as session:
        assert list(session.exec(select(Task).where(Task.instance_id == instance_id))) == []
        message = session.exec(
            select(MessageQueue).where(MessageQueue.instance_id == instance_id)
        ).one()
        assert message.status == MessageStatus.READY.value

    await pause_task

    with Session(engine) as session:
        assert session.get(Instance, instance_id).status == InstanceStatus.PAUSED.value
        message = session.exec(
            select(MessageQueue).where(MessageQueue.instance_id == instance_id)
        ).one()
        assert message.status == MessageStatus.READY.value
        assert list(session.exec(select(Task).where(Task.instance_id == instance_id))) == []
