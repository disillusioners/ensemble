"""Unit tests: terminate-path fire-with-outcome coverage (Phase 2, task 2.3).

§D (Rev 2.1) path-coverage acceptance: EVERY reachable terminate path
lands the watcher **FIRED-with-outcome** (never CANCELLED) — i.e.
``state='FIRED' AND fired_at IS NOT NULL`` after the call returns —
and the resulting FollowUp carries
``metadata["child_outcome"] == "terminated"``.

The two currently-reachable terminate paths:

  1. the post-commit seam — ``terminate_instance`` step 7.8 →
     ``_cancel_bus_watchers_for(manager, instance_id,
     "terminate_instance")`` (the helper whose body task 2.3 patched
     unconditionally to fire-with-outcome).
  2. the converged direct path — the pre-existing direct
     ``bus.cancel_for_target`` duplicate (plan's :1781 site) was
     already folded through the same helper by the P1 W5 collapse;
     path 2 is therefore the helper invoked directly (the call shape
     the folded site uses).

Both drive a REAL ``DependencyBus`` over an in-memory engine with a
PENDING watcher registered by ``bus.watch`` (the production
registration path), so the guarded ``transition_state`` race
semantics are exercised for real.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.event.models  # noqa: F401

from daemon.repositories.dependency_bus import (
    DependencyWatcherState,
    DependencyWatcherRepository,
)
from daemon.services.dependency_bus import (
    DependencyBus,
    FollowUp,
    get_dependency_bus,
    set_dependency_bus,
)
from daemon.services.instance_lifecycle import (
    InstanceLifecycleService,
    _cancel_bus_watchers_for,
    _TerminateResult,
)


# ─── Helpers ──────────────────────────────────────────────────────────────────


@pytest.fixture
def watcher_engine() -> Engine:
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    watcher_table = SQLModel.metadata.tables.get("dependency_watchers")
    if watcher_table is not None:
        watcher_table.create(eng, checkfirst=True)
    return eng


async def _register_parent_watching_child(bus: DependencyBus, parent: str, child: str):
    """Register the production-shape watcher: parent waits on child."""
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


def _watcher_row(engine: Engine, source_task_id: str):
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT watch_id, state, fired_at, enqueued_at "
                "FROM dependency_watchers "
                "WHERE source_task_id = :st"
            ),
            {"st": source_task_id},
        ).mappings().first()


def _assert_fired_with_outcome(engine: Engine, manager_mock, parent: str):
    """Shared acceptance: FIRED-with-outcome, never CANCELLED."""
    row = _watcher_row(engine, "task-term-1")
    assert row is not None, "watcher row must survive the terminate"
    assert row["state"] == DependencyWatcherState.FIRED.value, (
        f"terminate path must FIRE the watcher (got {row['state']}); "
        f"CANCELLED is the B3 defect"
    )
    assert row["fired_at"] is not None
    assert row["enqueued_at"] is not None, (
        "the helper stamps enqueued_at after the enqueue (C1 dedup marker)"
    )
    # The FollowUp was enqueued to the waiting parent with the
    # additive child_outcome marker.
    assert manager_mock.enqueue_message.await_count == 1
    kwargs = manager_mock.enqueue_message.call_args.kwargs
    assert kwargs["instance_id"] == parent
    assert kwargs["metadata"]["child_outcome"] == "terminated"


# ─── Path 1 — post-commit seam via terminate_instance step 7.8 ───────────────


@pytest.mark.asyncio
async def test_terminate_flow_lands_fired_with_outcome(watcher_engine):
    """Path 1: full ``terminate_instance`` → helper → FIRED-with-outcome."""
    repo = DependencyWatcherRepository(watcher_engine)
    bus = DependencyBus(repo)
    await bus.start()
    set_dependency_bus(bus)
    try:
        parent, terminated = "parent-P1", "path1-root"
        await _register_parent_watching_child(bus, parent, terminated)

        manager = MagicMock()
        manager._instance_repository = MagicMock()
        manager._instance_repository.get.side_effect = lambda iid: {
            "path1-root": _make_instance("path1-root", "running"),
        }.get(iid)
        manager._instance_repository.get_cascade_tree_ids = MagicMock(
            return_value=["path1-root"]
        )
        manager.instances = {"path1-root": MagicMock()}
        manager.enqueue_message = AsyncMock(
            return_value={"message_id": "m-fire-1"}
        )
        manager._graph_tasks = {}
        manager._request_registry = MagicMock()
        manager._live_hub = MagicMock()
        manager._live_hub.cleanup_instance = AsyncMock()
        manager._live_hub.stream_status_change = AsyncMock()
        manager._live_hub.stream_message = AsyncMock()
        manager._watcher_repo = MagicMock()
        manager._mcp_service = None
        manager._queue_repository = MagicMock()
        manager._queue_repository.delete_by_instance = MagicMock(return_value=0)
        manager._job_queue_mgmt_service = MagicMock()
        manager._job_queue_mgmt_service._dispatch_bus = MagicMock()
        manager.engine = MagicMock()
        manager.write_guard = MagicMock()
        manager._todo_manager = MagicMock()
        manager._gii_throttle = {}
        manager._loop_breaker_state = {}
        manager._events_service = None

        svc = InstanceLifecycleService.__new__(InstanceLifecycleService)
        svc._manager = manager
        svc._cancellation_service = MagicMock()
        svc._events_service = None
        svc._job_queue_service = None
        # Non-skip result so the post-commit outbox (incl. step 7.8's
        # helper call) fires.
        svc._terminate_instance_db_sync = MagicMock(
            return_value=_TerminateResult(
                skip=False,
                parent_id=parent,
                agent_id="test-agent",
                message_jobs_cancelled=0,
                all_jobs_cancelled=0,
                message_queue_removed=0,
                tasks_removed=0,
            )
        )

        real_terminate = svc.terminate_instance
        svc.terminate_instance = AsyncMock(return_value=True)
        await real_terminate("path1-root")

        _assert_fired_with_outcome(watcher_engine, manager, parent)
    finally:
        set_dependency_bus(None)
        await bus.stop()


# ─── Path 2 — converged direct call (the folded :1781 shape) ─────────────────


@pytest.mark.asyncio
async def test_helper_direct_call_lands_fired_with_outcome(watcher_engine):
    """Path 2: the helper invoked directly (the folded duplicate's shape).

    The plan's pre-existing ``:1781`` direct ``cancel_for_target``
    duplicate was folded through ``_cancel_bus_watchers_for`` by the
    P1 W5 collapse; this test pins that the converged call shape
    lands FIRED-with-outcome exactly like the post-commit seam.
    """
    repo = DependencyWatcherRepository(watcher_engine)
    bus = DependencyBus(repo)
    await bus.start()
    set_dependency_bus(bus)
    try:
        parent, terminated = "parent-P2", "child-T2"
        await _register_parent_watching_child(bus, parent, terminated)

        manager = MagicMock()
        manager.enqueue_message = AsyncMock(
            return_value={"message_id": "m-fire-2"}
        )

        await _cancel_bus_watchers_for(manager, terminated, "terminate_instance")

        _assert_fired_with_outcome(watcher_engine, manager, parent)
    finally:
        set_dependency_bus(None)
        await bus.stop()


def _make_instance(instance_id: str, status: str):
    meta = MagicMock()
    meta.instance_id = instance_id
    meta.status = status
    meta.terminal_reason = None
    meta.agent_id = "test-agent"
    meta.parent_id = None
    return meta


# ─── Failure-handling pin (supplementary) ─────────────────────────────────────


@pytest.mark.asyncio
async def test_helper_swallows_bus_failure(watcher_engine):
    """A bus/enqueue failure must NOT fail the terminate path (log +
    swallow — the pre-fix try/except pattern is preserved)."""
    repo = DependencyWatcherRepository(watcher_engine)
    bus = DependencyBus(repo)
    await bus.start()
    set_dependency_bus(bus)
    try:
        parent, terminated = "parent-P3", "child-T3"
        await _register_parent_watching_child(bus, parent, terminated)

        manager = MagicMock()
        manager.enqueue_message = AsyncMock(
            side_effect=RuntimeError("queue down")
        )

        # Must not raise.
        await _cancel_bus_watchers_for(manager, terminated, "terminate_instance")

        # The watcher row was FIRED by the bus (transition committed);
        # the enqueue failed and was swallowed — the row remains
        # un-stamped, so a restart's _recover_fired_unsent re-delivers
        # it (the crash-window contract).
        row = _watcher_row(watcher_engine, "task-term-1")
        assert row["state"] == DependencyWatcherState.FIRED.value
        assert row["enqueued_at"] is None
    finally:
        set_dependency_bus(None)
        await bus.stop()
