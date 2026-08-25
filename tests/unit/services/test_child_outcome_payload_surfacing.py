"""Unit tests: ``child_outcome`` payload surfacing (Phase 2, task 2.13 — W4).

Acceptance (phase2-plan.md task 2.13):
  (i) terminate → fire → parent drain → claim → the injected message
      content the parent LLM receives carries a
      ``child_outcome=terminated`` marker.
  (ii) two consecutive claims (replay) → second claim returns ``[]``
      AND the payload is byte-identical on both deliveries
      (idempotency of the surfacing — no field stripping, no
      re-stamping).

The wiring under test: ``fire_for_terminated_target`` stamps
``FollowUp.metadata["child_outcome"] = "terminated"`` AND persists the
enriched payload onto the FIRED watcher row;
``_process_child_completion_and_notify_parent`` copies the marker into
the report content (the field that becomes the ``report_injection``
row's payload / the injected message content the parent LLM reads) —
additive only.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
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
    """File-based SQLite — each Session gets its OWN connection.

    The completion path interleaves bus-repository sessions INSIDE
    the open WriteGuardSession transaction (count_pending calls).
    With StaticPool every session would share one DBAPI connection
    and the inner sessions' close-time ROLLBACK would destroy the
    outer transaction's writes. A file-backed DB with the default
    pool gives correct per-connection isolation (mirrors production).
    """
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
    """Unstarted bus — each test starts/stops it inside its own loop."""
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
    manager._checkpointer = None  # no assistant history → fallback content
    manager._live_hub = None
    manager._events_service = None
    manager._deferred_question_pause = set()
    manager._instance_repository = SQLModelInstanceRepository(engine)
    manager._queue_repository = MagicMock()
    manager._task_repo = None  # getattr-guarded

    service = ChildReportsService.__new__(ChildReportsService)
    service._manager = manager
    service._events_service = None
    return service


async def _fire_terminated(bus: DependencyBus, parent: str, child: str):
    """Register + fire the parent's watcher on the terminated child."""
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


def _read_injection_rows(engine: Engine, parent: str):
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT injection_id, content, state FROM report_injections "
                "WHERE parent_instance_id = :p"
            ),
            {"p": parent},
        ).mappings().all()


# ─── (i) terminate → fire → drain → claim carries the marker ─────────────────


@pytest.mark.asyncio
async def test_i_terminate_fire_drain_claim_carries_marker(engine, bus):
    set_dependency_bus(bus)
    try:
        await bus.start()
        parent = _seed_instance(
            engine, status=InstanceStatus.RUNNING.value, parent_id=None
        )
        child = _seed_instance(
            engine, status=InstanceStatus.TERMINATED.value, parent_id=parent
        )

        # terminate-side fire (task 2.2/2.3): FIRED + enriched payload.
        fired = await _fire_terminated(bus, parent, child)
        assert len(fired) == 1
        assert fired[0].metadata["child_outcome"] == "terminated"

        # The enriched marker is persisted on the FIRED row (the
        # lookup seam task 2.13 reads).
        assert (
            bus._repo.fetch_child_outcome_for_fired(child) == "terminated"
        )

        # The child-completion path (the drain's row-creation seam)
        # copies the marker into the report content — additive only.
        service = _build_service(engine)
        await service._process_child_completion_and_notify_parent(
            child, "msg-X"
        )

        rows = _read_injection_rows(engine, parent)
        assert len(rows) == 1
        assert "[child_outcome: terminated]" in rows[0]["content"]

        # claim (graph-node drain): the injected content the parent
        # LLM receives carries the marker.
        ri_repo = ReportInjectionRepository(engine)
        drained = ri_repo.claim_for_injection(parent)
        assert len(drained) == 1
        assert "[child_outcome: terminated]" in drained[0]["content"]
    finally:
        set_dependency_bus(None)
        await bus.stop()


# ─── (ii) replay: second claim returns [] + payload byte-identical ───────────


@pytest.mark.asyncio
async def test_ii_two_consecutive_claims_replay_byte_identical(engine, bus):
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
        await service._process_child_completion_and_notify_parent(
            child, "msg-X"
        )

        ri_repo = ReportInjectionRepository(engine)
        first = ri_repo.claim_for_injection(parent)
        assert len(first) == 1
        first_payload = first[0]["content"]

        # Replay: second claim returns [] (guarded WHERE state='PENDING').
        second = ri_repo.claim_for_injection(parent)
        assert second == []

        # Byte-identical payload on both "deliveries": the stored row
        # (now INJECTED) still carries the exact content the first
        # claim returned — no field stripping, no re-stamping.
        rows = _read_injection_rows(engine, parent)
        assert len(rows) == 1
        assert rows[0]["state"] == "INJECTED"
        assert rows[0]["content"] == first_payload
        assert "[child_outcome: terminated]" in rows[0]["content"]
    finally:
        set_dependency_bus(None)
        await bus.stop()
