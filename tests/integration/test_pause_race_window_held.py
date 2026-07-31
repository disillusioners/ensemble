"""Integration test: report is held while the deferred-pause window stays open."""

from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select

import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.report_injection.models import ReportInjection, ReportInjectionState
from daemon.repositories.task.models import Task, TaskType
from daemon.services.child_reports import ChildReportsService
from daemon.services.dependency_bus import set_dependency_bus
from daemon.write_pause_guard import WritePauseGuard


@pytest.fixture
def engine() -> Engine:
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
    bus.count_pending_for_target_sync.return_value = 1
    set_dependency_bus(bus)
    yield
    set_dependency_bus(None)


def seed_instance(engine: Engine, *, parent_id: str | None = None,
                  status: str = InstanceStatus.RUNNING.value) -> str:
    iid = f"inst-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).isoformat()
    with Session(engine) as session:
        session.add(Instance(
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
        ))
        session.commit()
    return iid


def test_held_window_during_cascade_defers_process_report(engine):
    """While the cascade's DB commit is in flight, the marker is set but
    the parent's DB row still shows RUNNING. A child completion in that
    window must NOT create a PROCESS_REPORT task."""
    parent_id = seed_instance(engine)
    child_id = seed_instance(engine, parent_id=parent_id)

    manager = MagicMock()
    manager.engine = engine
    manager.write_guard = WritePauseGuard()
    manager._deferred_question_pause = set()
    service = ChildReportsService(manager=manager)

    cascade_started = threading.Event()
    cascade_release = threading.Event()

    def _slow_cascade_marker():
        # Mirrors question_pause_node: set the marker, then block until the
        # test releases us, so the window stays open long enough to fire a
        # child completion.
        manager._deferred_question_pause.add(parent_id)
        cascade_started.set()
        cascade_release.wait(timeout=2.0)
        with Session(engine) as session:
            inst = session.get(Instance, parent_id)
            inst.status = InstanceStatus.PAUSED.value
            session.add(inst)
            session.commit()
        manager._deferred_question_pause.discard(parent_id)

    cascade_thread = threading.Thread(target=_slow_cascade_marker)
    cascade_thread.start()
    assert cascade_started.wait(timeout=2.0)

    # Window is open: marker set, DB still RUNNING. Child completion fires.
    result = service._process_child_completion_db_sync(
        child_id, "completed-msg", "child body"
    )
    assert result.outcome == "regular_child_completed"

    with Session(engine) as session:
        tasks = session.exec(
            select(Task).where(
                Task.instance_id == parent_id,
                Task.task_type == TaskType.PROCESS_REPORT.value,
            )
        ).all()
        injections = session.exec(
            select(ReportInjection).where(
                ReportInjection.parent_instance_id == parent_id
            )
        ).all()
    assert tasks == []  # skip fires (marker check)
    assert len(injections) == 1
    assert injections[0].state == ReportInjectionState.PENDING.value

    cascade_release.set()
    cascade_thread.join(timeout=2.0)

    # After the cascade settles, the parent reads PAUSED and the durable
    # ReportInjection row is still queued for the resume drain.
    with Session(engine) as session:
        inst = session.get(Instance, parent_id)
        assert inst.status == InstanceStatus.PAUSED.value
        injections = session.exec(
            select(ReportInjection).where(
                ReportInjection.parent_instance_id == parent_id
            )
        ).all()
    assert injections[0].state == ReportInjectionState.PENDING.value
