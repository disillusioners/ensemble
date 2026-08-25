"""Unit tests: parent-completion idempotency for TERMINATED children (task 2.6).

Verification acceptance (phase2-plan.md task 2.6):
  (a) the general idempotency tri-state returns ``already_delivered``
      on second delivery of a TERMINATED-child FollowUp — no duplicate
      report rows, no duplicate queue messages;
  (b) the PAUSED-guards do NOT bypass the ghost-child termination
      path — a TERMINATED child with a PAUSED parent still gets its
      report obligation persisted (report_injection row created; the
      PROCESS_REPORT Task is suppressed by the pause guard and the
      row delivers on resume via claim_for_injection).

Site map (post-drift, located by symbol):
  * general idempotency — the ``existing_report`` query in
    ``_process_child_completion_db_sync``'s regular-child pipeline
    (``internal_report:{child}:{msg}`` source check) plus
    ``claim_for_task_delivery``'s guarded tri-state.
  * PAUSED-guards — the child-PAUSED guard at the top of
    ``_process_child_completion_db_sync`` (``deferred_pause``) and
    the parent-side ``marker_paused`` / ``db_paused`` /
    ``db_dead_parent`` guard around the PROCESS_REPORT Task creation.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel

# Register all models for create_all.
import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.dependency_bus.repository import (
    DependencyWatcherRepository,
)
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.report_injection.repository import (
    ReportInjectionRepository,
)
from daemon.services.child_reports import ChildReportsService
from daemon.services.dependency_bus import (
    DependencyBus,
    FollowUp,
    Outcome,
    set_dependency_bus,
)
from daemon.write_pause_guard import WritePauseGuard


# ─── Fixtures & helpers ───────────────────────────────────────────────────────


@pytest.fixture
def engine(tmp_path) -> Engine:
    """File-based SQLite — per-connection isolation (see the
    StaticPool interleave note in test_child_outcome_payload_surfacing)."""
    eng = create_engine(
        f"sqlite:///{tmp_path}/test.db",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def bus(engine):
    return DependencyBus(DependencyWatcherRepository(engine))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_instance(engine: Engine, *, status: str, parent_id: str | None) -> str:
    iid = f"inst-{uuid.uuid4().hex[:8]}"
    now = _now_iso()
    with Session(engine) as s:
        s.add(
            Instance(
                instance_id=iid,
                agent_id="tester",
                agent_dir="/tmp/agents/tester",
                agent_name="tester",
                parent_id=parent_id,
                project_id="test-project",
                status=status,
                created_at=now,
                updated_at=now,
            )
        )
        s.commit()
    return iid


def _build_service(engine: Engine) -> ChildReportsService:
    manager = MagicMock(name="InstanceManager")
    manager.engine = engine
    manager.write_guard = WritePauseGuard()
    manager._checkpointer = None
    manager._live_hub = None
    manager._events_service = None
    manager._deferred_question_pause = set()
    manager._instance_repository = SQLModelInstanceRepository(engine)
    manager._queue_repository = MagicMock()
    manager._task_repo = None

    service = ChildReportsService.__new__(ChildReportsService)
    service._manager = manager
    service._events_service = None
    return service


async def _fire_terminated(bus: DependencyBus, parent: str, child: str):
    fu = FollowUp(
        target_instance_id=parent,
        message=f"[dependency_bus] child {child} completed for message msg-X",
        source=f"internal_agent:{parent}",
        metadata={
            "kind": "child_complete",
            "child_id": child,
            "parent_id": parent,
            "message_id": "msg-X",
        },
    )
    await bus.watch("task-term-1", fu)
    return await bus.fire_for_terminated_target(
        child, Outcome(status="terminated")
    )


def _count(engine: Engine, table: str, where: str, arg: str) -> int:
    with engine.connect() as conn:
        return int(
            conn.execute(
                text(f"SELECT COUNT(*) FROM {table} WHERE {where}"),
                {"p": arg},
            ).scalar()
        )


# ─── (a) second delivery → already_delivered ─────────────────────────────────


@pytest.mark.asyncio
async def test_a_second_delivery_already_delivered(engine, bus):
    set_dependency_bus(bus)
    try:
        await bus.start()
        parent = _seed_instance(
            engine, status=InstanceStatus.RUNNING.value, parent_id=None
        )
        child = _seed_instance(
            engine, status=InstanceStatus.TERMINATED.value, parent_id=parent
        )
        await _fire_terminated(bus, parent, child)

        service = _build_service(engine)
        # First delivery: the report obligation is created (message +
        # injection row) and the child is re-stamped by the atomic
        # UPDATE (terminated is not in the guarded skip-set).
        await service._process_child_completion_and_notify_parent(
            child, "msg-X"
        )
        assert _count(
            engine, "report_injections", "parent_instance_id = :p", parent
        ) == 1
        report_msg_id = _count(
            engine,
            "message_queue",
            "instance_id = :p AND source LIKE 'internal_report:%'",
            parent,
        )
        assert report_msg_id == 1

        # Second delivery (the TERMINATED-child FollowUp replay —
        # e.g. recovery-lane re-entry or a revived-child race): the
        # existing_report idempotency check suppresses the duplicate.
        await service._process_child_completion_and_notify_parent(
            child, "msg-X"
        )

        # Exactly-one rows survive: no duplicate report, no duplicate
        # queue message.
        assert _count(
            engine, "report_injections", "parent_instance_id = :p", parent
        ) == 1
        assert (
            _count(
                engine,
                "message_queue",
                "instance_id = :p AND source LIKE 'internal_report:%'",
                parent,
            )
            == 1
        )

        # The tri-state on the fallback-task claim path: once the
        # live drain has delivered the row (INJECTED — exactly what
        # the natural first delivery does), the fallback-task claim
        # reports already_delivered (no double-delivery lane).
        ri_repo = ReportInjectionRepository(engine)
        drained = ri_repo.claim_for_injection(parent)
        assert len(drained) == 1
        with engine.connect() as conn:
            rmid = conn.execute(
                text(
                    "SELECT report_message_id FROM report_injections "
                    "WHERE parent_instance_id = :p"
                ),
                {"p": parent},
            ).scalar_one()
        claim = ri_repo.claim_for_task_delivery(rmid)
        assert claim.status == "already_delivered"
    finally:
        set_dependency_bus(None)
        await bus.stop()


# ─── (b) PAUSED-guard does not bypass ghost-child termination path ───────────


@pytest.mark.asyncio
async def test_b_paused_guard_does_not_bypass_terminated_child(engine, bus):
    """Parent PAUSED + child TERMINATED: the obligation survives.

    The pause guard suppresses the PROCESS_REPORT Task (a paused
    parent cannot claim it), but the report_injection row is created
    regardless — it delivers on resume via claim_for_injection. The
    ghost-child termination path is NOT bypassed: the parent will
    learn the child was terminated once resumed.
    """
    set_dependency_bus(bus)
    try:
        await bus.start()
        parent = _seed_instance(
            engine, status=InstanceStatus.PAUSED.value, parent_id=None
        )
        child = _seed_instance(
            engine, status=InstanceStatus.TERMINATED.value, parent_id=parent
        )
        await _fire_terminated(bus, parent, child)

        service = _build_service(engine)
        await service._process_child_completion_and_notify_parent(
            child, "msg-X"
        )

        # The obligation row exists (PENDING) for the paused parent…
        rows = _count(
            engine, "report_injections", "parent_instance_id = :p", parent
        )
        assert rows == 1
        with engine.connect() as conn:
            state = conn.execute(
                text(
                    "SELECT state FROM report_injections "
                    "WHERE parent_instance_id = :p"
                ),
                {"p": parent},
            ).scalar_one()
        assert state == "PENDING"

        # …the pause guard suppressed the PROCESS_REPORT Task…
        assert (
            _count(
                engine,
                "task",
                "instance_id = :p AND task_type = 'process_report'",
                parent,
            )
            == 0
        )

        # …and the report carries the terminated marker — the parent
        # LLM sees child_outcome=terminated on resume (task 2.13).
        ri_repo = ReportInjectionRepository(engine)
        with engine.connect() as conn:
            content = conn.execute(
                text(
                    "SELECT content FROM report_injections "
                    "WHERE parent_instance_id = :p"
                ),
                {"p": parent},
            ).scalar_one()
        assert "[child_outcome: terminated]" in content

        # Resume-shaped delivery: the graph-node drain claims it.
        drained = ri_repo.claim_for_injection(parent)
        assert len(drained) == 1
        assert "[child_outcome: terminated]" in drained[0]["content"]
    finally:
        set_dependency_bus(None)
        await bus.stop()
