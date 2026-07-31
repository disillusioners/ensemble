"""Regression tests for the deferred-question-pause child-report race guard."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select

# Register tables before metadata.create_all().
import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.message_queue.models import MessageQueue
from daemon.repositories.report_injection.models import ReportInjection, ReportInjectionState
from daemon.repositories.task.models import Task, TaskType
from daemon.services.child_reports import ChildReportsService
from daemon.services.dependency_bus import set_dependency_bus
from daemon.write_pause_guard import WritePauseGuard


@pytest.fixture
def engine() -> Engine:
    """SQLite fixture mirrors production threading and is portable to a PG engine."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture(autouse=True)
def _bus_singleton():
    bus = MagicMock()
    # Keep the parent active; this suite tests report scheduling, not bus completion.
    bus.count_pending_for_target_sync.return_value = 1
    set_dependency_bus(bus)
    yield
    set_dependency_bus(None)


def seed_instance(
    engine: Engine,
    *,
    instance_id: str | None = None,
    parent_id: str | None = None,
    status: str = InstanceStatus.RUNNING.value,
) -> str:
    iid = instance_id or f"inst-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).isoformat()
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=iid,
                agent_id="developer",
                agent_dir="/tmp/agents/developer",
                agent_name="developer",
                parent_id=parent_id,
                project_id="test-project",
                status=status,
                version=1,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
    return iid


def make_service(engine: Engine) -> tuple[ChildReportsService, MagicMock]:
    manager = MagicMock()
    manager.engine = engine
    manager.write_guard = WritePauseGuard()
    manager._deferred_question_pause = set()
    return ChildReportsService(manager=manager), manager


def complete_child(
    service: ChildReportsService,
    child_id: str,
    *,
    message_id: str = "completed-message",
    content: str = "child report content",
):
    return service._process_child_completion_db_sync(child_id, message_id, content)


def read_report_rows(engine: Engine, parent_id: str):
    with Session(engine) as session:
        tasks = session.exec(
            select(Task).where(
                Task.instance_id == parent_id,
                Task.task_type == TaskType.PROCESS_REPORT.value,
            )
        ).all()
        messages = session.exec(
            select(MessageQueue).where(MessageQueue.instance_id == parent_id)
        ).all()
        injections = session.exec(
            select(ReportInjection).where(
                ReportInjection.parent_instance_id == parent_id
            )
        ).all()
        return tasks, messages, injections


def test_marker_skips_task_but_persists_delivery_rows(engine, caplog):
    parent_id = seed_instance(engine)
    child_id = seed_instance(engine, parent_id=parent_id)
    service, manager = make_service(engine)
    manager._deferred_question_pause.add(parent_id)

    with caplog.at_level(logging.INFO, logger="daemon.services.child_reports"):
        complete_child(service, child_id)

    tasks, messages, injections = read_report_rows(engine, parent_id)
    assert tasks == []
    assert len(messages) == 1
    assert len(injections) == 1
    assert injections[0].state == ReportInjectionState.PENDING.value
    assert "reason=marker" in caplog.text


def test_db_paused_skips_task_but_persists_delivery_rows(engine):
    parent_id = seed_instance(engine, status=InstanceStatus.PAUSED.value)
    child_id = seed_instance(engine, parent_id=parent_id)
    service, _manager = make_service(engine)

    complete_child(service, child_id)

    tasks, messages, injections = read_report_rows(engine, parent_id)
    assert tasks == []
    assert len(messages) == 1
    assert len(injections) == 1
    assert injections[0].state == ReportInjectionState.PENDING.value


def test_running_parent_creates_process_report_task(engine):
    parent_id = seed_instance(engine)
    child_id = seed_instance(engine, parent_id=parent_id)
    service, _manager = make_service(engine)

    complete_child(service, child_id)

    tasks, messages, injections = read_report_rows(engine, parent_id)
    assert len(tasks) == 1
    assert len(messages) == 1
    assert len(injections) == 1
