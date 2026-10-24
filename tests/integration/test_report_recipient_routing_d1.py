"""D1 — the report-recipient routing (completion-subsystem Phase 1).

Defect D1: a child's completion report was routed to the child's
STRUCTURAL PARENT. When the work was commissioned by somebody ELSE (an
``instance_nudge`` from an out-of-tree agent), the report went to the
wrong instance: the commissioner never learned the work finished, and
the structural parent consumed a report that was never its own (the
demonstrated fd302262 shape).

WHAT THIS FILE PINS
-------------------
1. The NATURAL write path addresses the RECIPIENT — the report
   ``MessageQueue`` row, the ``PROCESS_REPORT`` Task and the
   ``report_injections`` row all name the commissioner.
2. The STRUCTURAL parent is NOT overloaded — the result's ``parent_id``
   keeps its structural meaning (the bus corrective emit and the
   ``child_completed`` SSE express the WATCHER obligation).
3. WITHOUT a nudge the routing is a byte-level no-op (the structural
   parent).
4. §7.4 DUPLICATION FIREWALL — a commissioner-addressed row mints ZERO
   structural obligations: the backstop's evidence predicates are
   CHILD-KEYED, so the commissioner-addressed report reads as delivered
   and no second obligation is minted for the structural parent.
5. A no-row child WITH a committed nudge mints exactly ONE obligation,
   and it is the COMMISSIONER's.

The DB is REAL (file-backed SQLite); no mock on the write path.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel, select as sm_select

import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.instance_ops.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.dependency_bus.repository import (
    DependencyWatcherRepository,
)
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance_ops.models import InstanceOpsAudit
from daemon.repositories.message_queue.models import (
    MessageQueue,
    MessageStatus,
    MessageType,
)
from daemon.repositories.report_injection.models import ReportInjection
from daemon.repositories.report_injection.repository import (
    ReportInjectionRepository,
)
from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.services.child_reports import ChildReportsService
from daemon.services.dependency_bus import DependencyBus, set_dependency_bus
from daemon.services.report_delivery_recovery import (
    ReportDeliveryRecoveryService,
)
from daemon.write_pause_guard import WritePauseGuard


# ─── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def engine(tmp_path) -> Engine:
    """File-backed SQLite (NullPool + WAL + busy_timeout)."""
    eng = create_engine(
        f"sqlite:///{tmp_path / 'd1-integration.sqlite'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _configure_sqlite(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture(autouse=True)
def bus(engine: Engine):
    """A started DependencyBus bound to the test engine."""
    import asyncio

    repo = DependencyWatcherRepository(engine)
    b = DependencyBus(repo)
    asyncio.run(b.start())
    set_dependency_bus(b)
    try:
        yield b
    finally:
        asyncio.run(b.stop())


def _seed_instance(
    engine: Engine,
    *,
    instance_id: str,
    parent_id: str | None,
    status: str = InstanceStatus.RUNNING.value,
    agent_name: str = "worker",
):
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id=f"agent-{agent_name}",
                agent_name=agent_name,
                agent_dir=f"/tmp/{agent_name}",
                parent_id=parent_id,
                status=status,
                version=1,
                instance_metadata={},
            )
        )
        session.commit()


def _seed_nudge(
    engine: Engine, *, target: str, actor: str, committed: bool = True
):
    with Session(engine) as session:
        session.add(
            InstanceOpsAudit(
                action="instance_nudge",
                target=target,
                reason="please finish the commissioned work",
                dry_run=not committed,
                outcome="committed" if committed else "previewed",
                created_by_instance_id=actor,
                created_by_agent_id="maintenancer",
                created_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        session.commit()


def _seed_completed_message(engine: Engine, *, instance_id: str, message_id: str):
    with Session(engine) as session:
        session.add(
            MessageQueue(
                message_id=message_id,
                instance_id=instance_id,
                content="child final answer",
                source="agent",
                type=MessageType.AGENT.value,
                status=MessageStatus.COMPLETED.value,
                priority=0,
                enqueued_at=datetime.now(timezone.utc),
            )
        )
        session.commit()


def _build_child_reports_service(engine: Engine) -> ChildReportsService:
    manager = MagicMock(name="InstanceManager")
    manager.engine = engine
    manager.write_guard = WritePauseGuard()
    manager._checkpointer = None
    manager._live_hub = None
    manager._queue_repository = MagicMock()
    manager._instance_repository = MagicMock()
    manager._deferred_question_pause = set()
    manager._report_injection_pending = set()

    service = ChildReportsService.__new__(ChildReportsService)
    service._manager = manager
    service._events_service = None
    return service


def _report_rows(engine: Engine) -> list[MessageQueue]:
    with Session(engine) as session:
        return list(
            session.exec(
                sm_select(MessageQueue).where(
                    MessageQueue.source.like("internal_report:%")
                )
            ).all()
        )


def _report_tasks(engine: Engine) -> list[Task]:
    with Session(engine) as session:
        return list(
            session.exec(
                sm_select(Task).where(
                    Task.task_type == TaskType.PROCESS_REPORT.value
                )
            ).all()
        )


def _injection_rows(engine: Engine) -> list[ReportInjection]:
    with Session(engine) as session:
        return list(session.exec(sm_select(ReportInjection)).all())


# ─── The natural write path ─────────────────────────────────────────────────


class TestNaturalWritePathRouting:
    def test_a_commissioned_child_reports_to_the_commissioner(self, engine):
        """THE D1 POINT — the report is addressed to the COMMISSIONER."""
        child = f"child-{uuid.uuid4().hex[:8]}"
        structural = f"parent-{uuid.uuid4().hex[:8]}"
        commissioner = f"comm-{uuid.uuid4().hex[:8]}"
        _seed_instance(
            engine, instance_id=child, parent_id=structural
        )
        _seed_instance(engine, instance_id=structural, parent_id=None)
        _seed_instance(engine, instance_id=commissioner, parent_id=None)
        _seed_nudge(engine, target=child, actor=commissioner)

        service = _build_child_reports_service(engine)
        result = service._process_child_completion_db_sync(
            instance_id=child,
            completed_message_id="msg-child-1",
            last_content="the commissioned report",
        )

        assert result.outcome == "regular_child_completed"
        # The carrier carries the resolved recipient.
        assert result.report_recipient_id == commissioner
        # The STRUCTURAL parent is NOT overloaded.
        assert result.parent_id == structural

        # The report message is addressed to the commissioner.
        rows = _report_rows(engine)
        assert len(rows) == 1
        assert rows[0].instance_id == commissioner
        # The PROCESS_REPORT task belongs to the commissioner.
        tasks = _report_tasks(engine)
        assert len(tasks) == 1
        assert tasks[0].instance_id == commissioner
        assert tasks[0].status == TaskStatus.PENDING.value
        # The durable fallback row names the commissioner.
        injections = _injection_rows(engine)
        assert len(injections) == 1
        assert injections[0].parent_instance_id == commissioner
        assert injections[0].child_instance_id == child

    def test_an_uncommissioned_child_still_reports_to_its_parent(self, engine):
        """THE NO-OP HALF — 99%+ of children were never nudged."""
        child = f"child-{uuid.uuid4().hex[:8]}"
        structural = f"parent-{uuid.uuid4().hex[:8]}"
        _seed_instance(engine, instance_id=child, parent_id=structural)
        _seed_instance(engine, instance_id=structural, parent_id=None)

        service = _build_child_reports_service(engine)
        result = service._process_child_completion_db_sync(
            instance_id=child,
            completed_message_id="msg-child-1",
            last_content="the ordinary report",
        )

        assert result.outcome == "regular_child_completed"
        assert result.report_recipient_id == structural
        rows = _report_rows(engine)
        assert len(rows) == 1
        assert rows[0].instance_id == structural
        tasks = _report_tasks(engine)
        assert len(tasks) == 1
        assert tasks[0].instance_id == structural
        injections = _injection_rows(engine)
        assert len(injections) == 1
        assert injections[0].parent_instance_id == structural


class TestDuplicationFirewall:
    """§7.4 — the backstop must NOT mint a second obligation for the
    structural parent when the report was delivered to the
    commissioner."""

    def test_a_commissioner_addressed_row_mints_zero_structural_obligations(
        self, engine
    ):
        child = f"child-{uuid.uuid4().hex[:8]}"
        structural = f"parent-{uuid.uuid4().hex[:8]}"
        commissioner = f"comm-{uuid.uuid4().hex[:8]}"
        _seed_instance(engine, instance_id=child, parent_id=structural)
        _seed_instance(engine, instance_id=structural, parent_id=None)
        _seed_instance(engine, instance_id=commissioner, parent_id=None)
        _seed_nudge(engine, target=child, actor=commissioner)

        service = _build_child_reports_service(engine)
        result = service._process_child_completion_db_sync(
            instance_id=child,
            completed_message_id="msg-child-1",
            last_content="the commissioned report",
        )
        assert result.outcome == "regular_child_completed"

        # The backstop's candidate query must NOT admit this child —
        # its report WAS delivered (to the commissioner).
        repo = ReportInjectionRepository(engine=engine)
        rows = repo.find_completed_children_without_delivery(
            parent_not_terminal=False, limit=100
        )
        assert not any(r.get("child_id") == child for r in rows), (
            "the commissioner-addressed report must read as DELIVERED — "
            "otherwise the backstop mints a second structural obligation"
        )

    def test_a_no_row_child_with_a_committed_nudge_mints_one_commissioner_obligation(
        self, engine
    ):
        """The mirror image — a child with NO delivery evidence and a
        committed nudge mints EXACTLY ONE obligation, addressed to the
        COMMISSIONER."""
        child = f"child-{uuid.uuid4().hex[:8]}"
        structural = f"parent-{uuid.uuid4().hex[:8]}"
        commissioner = f"comm-{uuid.uuid4().hex[:8]}"
        _seed_instance(
            engine,
            instance_id=child,
            parent_id=structural,
            status=InstanceStatus.COMPLETED.value,
        )
        _seed_instance(
            engine,
            instance_id=structural,
            parent_id=None,
            status=InstanceStatus.WAITING_CHILDREN.value,
        )
        _seed_instance(engine, instance_id=commissioner, parent_id=None)
        _seed_nudge(engine, target=child, actor=commissioner)
        _seed_completed_message(
            engine, instance_id=child, message_id="msg-child-1"
        )

        ri_repo = ReportInjectionRepository(engine=engine)
        task_repo = MagicMock()
        task_repo.has_instance_busy = MagicMock(return_value=False)
        queue_repo = MagicMock()
        queue_repo.find_wake_already_delivered_evidence = MagicMock(
            return_value=False
        )
        manager = MagicMock()
        manager.engine = engine
        manager._checkpointer = None
        manager._handle_recover_deferred_report = MagicMock()
        service = ReportDeliveryRecoveryService(
            task_repo=task_repo,
            report_injection_repo=ri_repo,
            queue_repo=queue_repo,
            instance_repo=MagicMock(),
            manager_ref=manager,
            interval_seconds=300,
            age_bound_minutes=10,
            batch_cap=100,
            recovery_retry_minutes=1,
            enabled=True,
            lane_orphan=False,
        )

        service._run_no_row_backstop_lane()

        injections = _injection_rows(engine)
        assert len(injections) == 1, (
            "exactly ONE obligation must be minted — the commissioner"
        )
        assert injections[0].parent_instance_id == commissioner
        assert injections[0].child_instance_id == child
        # And NEVER one for the structural parent.
        assert not any(
            i.parent_instance_id == structural for i in injections
        )
