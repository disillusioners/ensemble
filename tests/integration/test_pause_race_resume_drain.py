"""Integration test: report_injection row delivers a skipped child report on resume."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.report_injection.repository import (
    ReportInjectionRepository,
)
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


def test_skipped_report_delivered_on_resume_via_claim_for_injection(engine):
    parent_id = seed_instance(engine)
    child_id = seed_instance(engine, parent_id=parent_id)

    manager = MagicMock()
    manager.engine = engine
    manager.write_guard = WritePauseGuard()
    manager._deferred_question_pause = {parent_id}
    service = ChildReportsService(manager=manager)

    # 1. Child completes during the race window: Task skipped, ReportInjection
    #    row kept as durable fallback.
    result = service._process_child_completion_db_sync(
        child_id, "completed-msg", "child body"
    )
    assert result.outcome == "regular_child_completed"

    # 2. Resume: clear the marker, the parent's next LLM call drains.
    manager._deferred_question_pause.discard(parent_id)
    repo = ReportInjectionRepository(engine)
    delivered = repo.claim_for_injection(parent_id)
    assert [d["content"] for d in delivered] == ["child body"]
