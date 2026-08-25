"""Unit tests: revive non-replay (Phase 2, task 2.7 — Q5 verification).

Architect Q5 (CONFIRMED with verification): revival
(``instance_messaging`` reactivates a TERMINATED/COMPLETED child and
queues a fresh MessageQueue/Task) touches NEITHER
``dependency_watchers`` NOR ``report_injection`` — the FIRED
obligation stays terminal and no double-delivery occurs.

The load-bearing sub-assertion (binding): ``claim_for_injection``
returns ``[]`` on second delivery — the natural idempotency is the
guarded ``WHERE state='PENDING'`` UPDATE on ``report_injection`` rows.
On the second call every row is already terminal (INJECTED /
TASK_DELIVERED), so the claim yields zero rows.

Cases:
  (a) revival + claim → 0 rows (the obligation was consumed before /
      around the revival; revival does not re-create it);
  (b) two consecutive claims → 0 rows on the second call.
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


async def _fire_and_deliver(bus, service, parent: str, child: str):
    """Fire the terminated-child watcher + run the delivery path once."""
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
    fired = await bus.fire_for_terminated_target(
        child, Outcome(status="terminated")
    )
    # Mirror the terminate-path helper's post-enqueue stamp contract
    # (task 2.3): after the FollowUp is enqueued, the FIRED row is
    # stamped so a restart's _recover_fired_unsent does not re-deliver.
    if fired:
        await bus.mark_enqueued_by_source_target("task-term-1", parent)
    await service._process_child_completion_and_notify_parent(child, "msg-X")
    return fired


def _revive(engine: Engine, child: str) -> None:
    """Simulate the revival transition (instance_messaging shape).

    The architect-verified revival path reactivates ``instances.status``
    and queues a fresh MessageQueue/Task — it touches NEITHER
    ``dependency_watchers`` NOR ``report_injection``. The test applies
    exactly that write surface (status RUNNING + a fresh queue row)
    and nothing else.
    """
    from daemon.repositories.message_queue.models import (
        MessageQueue,
        MessageStatus,
        MessageType,
    )

    with Session(engine) as s:
        inst = s.get(Instance, child)
        inst.status = InstanceStatus.RUNNING.value
        inst.updated_at = _now_iso()
        s.add(
            MessageQueue(
                message_id=f"msg-{uuid.uuid4().hex[:8]}",
                instance_id=child,
                content="fresh post-revival user message",
                type=MessageType.HUMAN.value,
                status=MessageStatus.READY.value,
                priority=1,
                enqueued_at=datetime.now(timezone.utc),
            )
        )
        s.commit()


# ─── (a) revival + claim → 0 rows ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_revival_then_claim_returns_zero(engine, bus):
    set_dependency_bus(bus)
    try:
        await bus.start()
        parent = _seed_instance(
            engine, status=InstanceStatus.RUNNING.value, parent_id=None
        )
        child = _seed_instance(
            engine, status=InstanceStatus.TERMINATED.value, parent_id=parent
        )
        service = _build_service(engine)
        fired = await _fire_and_deliver(bus, service, parent, child)
        assert len(fired) == 1

        # The obligation was consumed by the live drain.
        ri_repo = ReportInjectionRepository(engine)
        first = ri_repo.claim_for_injection(parent)
        assert len(first) == 1

        # Revive the TERMINATED child (send_message shape) — the
        # revival write surface does NOT touch watchers/injections.
        watcher_state_before = None
        with engine.connect() as conn:
            watcher_state_before = conn.execute(
                text(
                    "SELECT state, enqueued_at FROM dependency_watchers "
                    "WHERE source_task_id = 'task-term-1'"
                )
            ).mappings().first()
        _revive(engine, child)

        with engine.connect() as conn:
            watcher_state_after = conn.execute(
                text(
                    "SELECT state, enqueued_at FROM dependency_watchers "
                    "WHERE source_task_id = 'task-term-1'"
                )
            ).mappings().first()
        assert dict(watcher_state_after) == dict(watcher_state_before), (
            "revival must not touch dependency_watchers (Q5)"
        )

        # Load-bearing sub-assertion: post-revival claim → 0 rows.
        after_revival = ri_repo.claim_for_injection(parent)
        assert after_revival == []
    finally:
        set_dependency_bus(None)
        await bus.stop()


# ─── (b) two consecutive claims → 0 rows on second ───────────────────────────


@pytest.mark.asyncio
async def test_b_two_consecutive_claims_zero_on_second(engine, bus):
    set_dependency_bus(bus)
    try:
        await bus.start()
        parent = _seed_instance(
            engine, status=InstanceStatus.RUNNING.value, parent_id=None
        )
        child = _seed_instance(
            engine, status=InstanceStatus.TERMINATED.value, parent_id=parent
        )
        service = _build_service(engine)
        await _fire_and_deliver(bus, service, parent, child)

        ri_repo = ReportInjectionRepository(engine)
        first = ri_repo.claim_for_injection(parent)
        second = ri_repo.claim_for_injection(parent)

        assert len(first) == 1
        assert second == [], (
            "guarded WHERE state='PENDING' UPDATE makes the second "
            "claim a no-op — the natural idempotency mechanism (Q5)"
        )

        # The watcher obligation is terminal and stamped — a restart's
        # _recover_fired_unsent will not re-deliver it either.
        recovered = await bus._recover_fired_unsent()
        assert recovered == []
    finally:
        set_dependency_bus(None)
        await bus.stop()
