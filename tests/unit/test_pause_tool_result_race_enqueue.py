"""Phase 2 branch tests for the PROCESS_MESSAGE deferred-pause guard."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.message_queue.models import MessageQueue, MessageStatus
from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.services.cancellation import CancellationService
from daemon.services.instance_messaging import InstanceMessagingService
from daemon.write_pause_guard import WritePauseGuard


@pytest.fixture
def engine() -> Engine:
    """Real SQLite database; the fixture shape also works with a PG engine."""
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


def _seed_instance(engine: Engine, instance_id: str, status: str) -> None:
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id="developer",
                agent_dir="/tmp/agents/developer",
                agent_name="developer",
                project_id="test-project",
                status=status,
                instance_metadata={},
            )
        )
        session.commit()


def _make_service(engine: Engine) -> tuple[InstanceMessagingService, MagicMock]:
    """Use a mock facade but retain production marker/set and database state."""
    manager = MagicMock()
    manager.engine = engine
    manager.write_guard = WritePauseGuard()
    manager._deferred_question_pause = set()
    cancellation = MagicMock(spec=CancellationService)
    cancellation.is_shutting_down = False
    return InstanceMessagingService(manager, cancellation), manager


def _prepare(service: InstanceMessagingService, instance_id: str) -> None:
    service._prepare_enqueued_message(
        instance_id=instance_id,
        message="message during pause transition",
        source="api",
        priority=1,
        images=None,
        metadata=None,
    )


def _rows(engine: Engine, model: type, instance_id: str) -> list:
    with Session(engine) as session:
        return list(session.exec(select(model).where(model.instance_id == instance_id)))


def test_marker_set_running_preserves_ready_message_but_skips_task(
    engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    instance_id = "marker-running-instance"
    _seed_instance(engine, instance_id, InstanceStatus.RUNNING.value)
    service, manager = _make_service(engine)
    manager._deferred_question_pause.add(instance_id)

    with caplog.at_level(logging.WARNING, logger="daemon.services.instance_messaging"):
        _prepare(service, instance_id)

    assert _rows(engine, Task, instance_id) == []
    messages = _rows(engine, MessageQueue, instance_id)
    assert len(messages) == 1
    assert messages[0].status == MessageStatus.READY.value
    assert "reason=marker (in-window race)" in caplog.text
    assert "KNOWN LIMITATION" in caplog.text


def test_marker_empty_paused_creates_pending_task_and_logs_sql_gate(
    engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    instance_id = "post-cascade-paused"
    _seed_instance(engine, instance_id, InstanceStatus.PAUSED.value)
    service, _manager = _make_service(engine)

    with caplog.at_level(logging.INFO, logger="daemon.services.instance_messaging"):
        _prepare(service, instance_id)

    tasks = _rows(engine, Task, instance_id)
    assert len(tasks) == 1
    assert tasks[0].task_type == TaskType.PROCESS_MESSAGE.value
    assert tasks[0].status == TaskStatus.PENDING.value
    assert "with DB=PAUSED" in caplog.text
    assert "relying on claim_pending_task SQL gate" in caplog.text


def test_marker_empty_running_creates_pending_task_normally(engine: Engine) -> None:
    instance_id = "normal-running-instance"
    _seed_instance(engine, instance_id, InstanceStatus.RUNNING.value)
    service, _manager = _make_service(engine)

    _prepare(service, instance_id)

    tasks = _rows(engine, Task, instance_id)
    assert len(tasks) == 1
    assert tasks[0].task_type == TaskType.PROCESS_MESSAGE.value
    assert tasks[0].status == TaskStatus.PENDING.value
    messages = _rows(engine, MessageQueue, instance_id)
    assert len(messages) == 1
    assert messages[0].status == MessageStatus.READY.value
