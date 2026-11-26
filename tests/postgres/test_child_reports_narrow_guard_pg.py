"""NEW-8: real-PostgreSQL proof of the narrowed mint guard (row (iii)).

The narrowed evidence query (plan.md Sec. 2 / design.md Sec. 5): a prior
DELIVERED report under a DIFFERENT anchor must NOT block a new turn's
mint, so the new-anchor report IS minted and the child terminalizes
(truth-table row (iii)).

On SQLite the guard is already exercised by
``tests/unit/services/test_child_reports.py`` (NEW-6b) and
``tests/unit/services/test_stale_running_sweep_rider_pins.py``
(NEW-1). This file proves the SAME predicate survives on real
PostgreSQL — i.e. the two-arm disjunction behaves identically under
the PG dialect (no SQLite-specific quirk in the ``LIKE ... ESCAPE``
clause or the ``status IN (...)`` evaluation).

Target runtime:

  timeout 300 .venv/bin/pytest tests/postgres/test_child_reports_narrow_guard_pg.py \\
      --override-ini="addopts=" -m postgres -q --tb=short

A SKIP (PG unreachable) is a LEG FAILURE, not a green.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, select as sm_select

# Model imports — required so SQLModel.metadata sees the tables when
# ``create_all()`` runs on the PG engine (matches the conftest pattern).
import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.dependency_bus.repository import (
    DependencyWatcherRepository,
)
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.message_queue.models import (
    MessageQueue,
    MessageStatus,
    MessageType,
)
from daemon.services.child_reports import ChildReportsService
from daemon.services.dependency_bus import DependencyBus, set_dependency_bus
from daemon.write_pause_guard import WritePauseGuard

pytestmark = pytest.mark.postgres

_RW_PARENT = "rw-pg-leader"
_RW_CHILD = "rw-pg-child"


def _build_child_reports_service(engine: Engine) -> ChildReportsService:
    manager = MagicMock(name="InstanceManager")
    manager.engine = engine
    manager.write_guard = WritePauseGuard()
    manager._checkpointer = None
    manager._live_hub = None
    manager._queue_repository = MagicMock()
    manager._instance_repository = MagicMock()
    manager._task_repo = None
    manager._worker_pool = None

    service = ChildReportsService.__new__(ChildReportsService)
    service._manager = manager
    service._events_service = None
    return service


@pytest.fixture(autouse=True)
def bus(pg_repository_factory):
    """Real DependencyBus singleton bound to the PG engine."""
    repo = pg_repository_factory(DependencyWatcherRepository)
    b = DependencyBus(repo)
    set_dependency_bus(b)
    try:
        yield b
    finally:
        set_dependency_bus(None)


def _seed_instance(engine: Engine, *, instance_id: str, parent_id, status):
    with Session(engine) as session:
        session.add(Instance(
            instance_id=instance_id,
            agent_id="worker" if instance_id != _RW_PARENT else "leader",
            agent_name="worker" if instance_id != _RW_PARENT else "leader",
            agent_dir="/tmp/x",
            parent_id=parent_id,
            status=status,
            version=1,
            created_at=datetime.now(timezone.utc).isoformat(),
            updated_at=datetime.now(timezone.utc).isoformat(),
            instance_metadata={},
        ))
        session.commit()


def _seed_report(engine: Engine, *, anchor: str, status: str, message_id: str):
    with Session(engine) as session:
        session.add(MessageQueue(
            message_id=message_id,
            instance_id=_RW_PARENT,
            content=f"report under {anchor}",
            source=f"internal_report:{_RW_CHILD}:{anchor}",
            type=MessageType.COMPLETION_REPORT.value,
            status=status,
        ))
        session.commit()


def _report_sources(engine: Engine) -> list[str]:
    with Session(engine) as session:
        rows = session.exec(
            sm_select(MessageQueue).where(
                MessageQueue.source.startswith(
                    f"internal_report:{_RW_CHILD}:", autoescape=True,
                )
            )
        ).all()
        return sorted(r.source for r in rows)


def _status(engine: Engine, instance_id: str) -> str | None:
    with Session(engine) as session:
        row = session.get(Instance, instance_id)
        return row.status if row else None


def _set_report_status(engine: Engine, status: str) -> None:
    with Session(engine) as session:
        rows = session.exec(
            sm_select(MessageQueue).where(
                MessageQueue.source.startswith(
                    f"internal_report:{_RW_CHILD}:", autoescape=True,
                )
            )
        ).all()
        for r in rows:
            r.status = status
        session.commit()


def _set_status(engine: Engine, instance_id: str, status: str) -> None:
    with Session(engine) as session:
        session.get(Instance, instance_id).status = status
        session.commit()


def test_new_anchor_report_is_minted_and_child_terminalizes(pg_engine: Engine):
    """NEW-8 (row (iii)) on real PG: a prior DELIVERED report under a
    different anchor does NOT block a new turn's mint; the new-anchor
    report is minted and the revived child terminalizes to COMPLETED.

    RED at base (the lifetime-prefix guard blocks the mint), GREEN at
    merge.
    """
    # Fence: assert the scratch DB BEFORE any DDL by this test.
    with Session(pg_engine) as _s:
        _dbname = _s.exec(
            text("SELECT current_database()")
        ).scalar()
    assert _dbname == "ensemble_test", _dbname

    service = _build_child_reports_service(pg_engine)
    _seed_instance(pg_engine, instance_id=_RW_PARENT, parent_id=None,
                   status=InstanceStatus.IDLE.value)
    _seed_instance(pg_engine, instance_id=_RW_CHILD, parent_id=_RW_PARENT,
                   status=InstanceStatus.RUNNING.value)

    # Episode 1 mints a report under anchor-1 and stamps the child
    # COMPLETED.
    first = service._process_child_completion_db_sync(
        instance_id=_RW_CHILD,
        completed_message_id="anchor-1",
        last_content="episode-1 report",
    )
    assert first.outcome == "regular_child_completed"
    assert _report_sources(pg_engine) == [
        f"internal_report:{_RW_CHILD}:anchor-1",
    ]
    assert _status(pg_engine, _RW_CHILD) == InstanceStatus.COMPLETED.value

    # The prior report is DELIVERED.
    _set_report_status(pg_engine, MessageStatus.COMPLETED.value)

    # Explicit revive (new episode).
    _set_status(pg_engine, _RW_CHILD, InstanceStatus.RUNNING.value)

    # Episode 2 — a DIFFERENT anchor. The narrowed guard must let the
    # mint through (row (iii)).
    second = service._process_child_completion_db_sync(
        instance_id=_RW_CHILD,
        completed_message_id="anchor-2",
        last_content="episode-2 report",
    )

    assert second.outcome == "regular_child_completed", second.outcome
    assert _report_sources(pg_engine) == [
        f"internal_report:{_RW_CHILD}:anchor-1",
        f"internal_report:{_RW_CHILD}:anchor-2",
    ], "NEW-8: the new-anchor report must be minted on real PG"
    assert _status(pg_engine, _RW_CHILD) == InstanceStatus.COMPLETED.value


