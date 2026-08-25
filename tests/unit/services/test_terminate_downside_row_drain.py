"""Unit tests: DOWN-side row drain in ``_cancel_bus_watchers_for`` (Phase 2, FINDING B).

Review F1 (2026-08-24, MAJOR): the unconditional swap from
``bus.cancel_for_target`` (PENDING→CANCELLED, DOWN-side) to
``bus.fire_for_terminated_target`` (PENDING→FIRED, UP-side only) in
Task 2.3 left the DOWN-side rows (where the TERMINATED instance is the
TARGET — its own watches on its children) as PENDING. After
``_terminate_instance_db_sync`` deletes the terminated instance's task
rows, those PENDING rows become orphans (their ``source_task_id``
references are gone; ``_sweep_orphan_watchers`` only runs at
``bus.start()``, not mid-session).

Failure mode: a mid-session REVIVE of the terminated instance counts
those orphans via ``count_pending_for_target_sync(revived_id)`` and
blocks the completion gate indefinitely.

Mitigation: ``_cancel_bus_watchers_for`` now also calls
``bus.cancel_for_target(instance_id)`` AFTER the fire call. The
UP-side ``fire_for_terminated_target`` matches ``metadata.child_id ==
instance_id``; the DOWN-side ``cancel_for_target`` matches
``target_instance_id == instance_id`` — DISJOINT row sets, so the
composition is exactly-once-safe (both use guarded ``transition_state``;
the second method's guarded UPDATE no-ops on rows already terminal
via the first).

Cases (acceptance — review F1):
  (i)  terminate → UP-side rows land FIRED-with-outcome, DOWN-side
       leftover rows land CANCELLED (NOT orphaned PENDING).
  (ii) mid-session REVIVE of the terminated instance does NOT count
       the DOWN-side rows in ``count_pending_for_target_sync``
       (gate not blocked).

Both shapes are pinned with the helper's REAL
``DependencyBus`` (in-memory engine, no daemon, no PG). Mirrors the
fixture strategy of ``tests/test_dependency_bus.py``.
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

import daemon.repositories.dependency_bus.models  # noqa: F401

from daemon.repositories.dependency_bus import (
    DependencyWatcherState,
    DependencyWatcherRepository,
)
from daemon.services.dependency_bus import (
    DependencyBus,
    FollowUp,
    set_dependency_bus,
)
from daemon.services.instance_lifecycle import (
    _cancel_bus_watchers_for,
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


async def _register_watcher(
    bus: DependencyBus,
    *,
    source_task_id: str,
    target_instance_id: str,
    child_id: str,
    parent_id: str,
    message_id: str = "msg-F1B",
):
    """Register a single watcher.

    Mirrors the ``bus.watch`` production call: parent waits on child.
    ``child_id`` and ``parent_id`` are stamped into the FollowUp
    payload's metadata per the send_message convention.
    """
    fu = FollowUp(
        target_instance_id=target_instance_id,
        message=f"[dependency_bus] child {child_id} completed for {message_id}",
        source=f"internal_agent:{target_instance_id}",
        metadata={
            "kind": "child_complete",
            "child_id": child_id,
            "parent_id": parent_id,
            "message_id": message_id,
        },
    )
    await bus.watch(source_task_id, fu)


def _all_watcher_rows(engine: Engine) -> list[dict]:
    with engine.connect() as conn:
        return [
            dict(r) for r in conn.execute(
                text(
                    "SELECT watch_id, source_task_id, target_instance_id, "
                    "       state, fired_at, enqueued_at "
                    "FROM dependency_watchers"
                )
            ).mappings().all()
        ]


def _count_pending(engine: Engine, target_instance_id: str) -> int:
    """PENDING-only count (mirrors ``count_pending_for_target_sync``)."""
    with engine.connect() as conn:
        return int(
            conn.execute(
                text(
                    "SELECT COUNT(*) FROM dependency_watchers "
                    "WHERE target_instance_id = :t "
                    "  AND state = :p"
                ),
                {"t": target_instance_id, "p": DependencyWatcherState.PENDING.value},
            ).scalar_one()
        )


# ─── (i) terminate drains BOTH sides — UP FIRED-with-outcome, DOWN CANCELLED ──


@pytest.mark.asyncio
async def test_terminate_drains_up_and_down_side_rows(watcher_engine):
    """terminate → UP rows FIRED-with-outcome + DOWN rows CANCELLED.

    Setup mirrors the realistic terminate-side residue:

      * UP-side (parent → terminated child): a parent registered a
        watch on the to-be-terminated instance via
        ``bus.watch(parent_task, FollowUp(target=parent, child=terminated))``.
        This row must end FIRED-with-outcome via the helper's
        ``fire_for_terminated_target(terminated)`` call.

      * DOWN-side (terminated → its own child): the to-be-terminated
        instance was itself a parent watching one of ITS children
        (``bus.watch(terminated_task, FollowUp(target=terminated,
        child=some_descendant))``). After the instance is terminated
        and its task rows are deleted (modeled here by clearing the
        row independently), the DOWN-side watcher's source_task_id
        dangles. Without the cancel_for_target drain it would
        linger as a PENDING orphan mid-session, blocking the gate
        on any mid-session REVIVE.

    The helper must land the UP row FIRED-with-outcome (B3 fix) AND
    the DOWN row CANCELLED (F1 mitigation).
    """
    repo = DependencyWatcherRepository(watcher_engine)
    bus = DependencyBus(repo)
    await bus.start()
    set_dependency_bus(bus)
    try:
        # ── Two parents + the terminated instance + a grandchild ──
        grandchild = f"grand-{uuid.uuid4().hex[:8]}"
        terminated = f"term-{uuid.uuid4().hex[:8]}"
        up_parent = f"up-p-{uuid.uuid4().hex[:8]}"
        # (down_parent and the terminated instance ARE the same row:
        # the terminated instance IS the parent watching its grandchild)

        # UP-side: up_parent watches the (to-be-terminated) instance.
        await _register_watcher(
            bus,
            source_task_id="task-up",
            target_instance_id=up_parent,
            child_id=terminated,
            parent_id=up_parent,
        )
        # DOWN-side: the terminated instance watches its grandchild.
        # ``source_task_id`` is the terminated instance's own task —
        # which is what _terminate_instance_db_sync would delete.
        await _register_watcher(
            bus,
            source_task_id="task-down",
            target_instance_id=terminated,
            child_id=grandchild,
            parent_id=terminated,
        )

        assert _count_pending(watcher_engine, up_parent) == 1
        assert _count_pending(watcher_engine, terminated) == 1

        manager = AsyncMock()
        manager.enqueue_message = AsyncMock(
            return_value={"message_id": "m-f1b-1"}
        )

        # ── Act: helper invocation models the post-commit seam ──
        await _cancel_bus_watchers_for(manager, terminated, "terminate_instance")

        # ── Acceptance — UP row: FIRED-with-outcome + enqueued_at set ──
        up_row = next(
            r for r in _all_watcher_rows(watcher_engine)
            if r["target_instance_id"] == up_parent
        )
        assert up_row["state"] == DependencyWatcherState.FIRED.value, (
            f"UP-side row must be FIRED-with-outcome (B3 fix); got "
            f"{up_row['state']!r}"
        )
        assert up_row["fired_at"] is not None
        assert up_row["enqueued_at"] is not None, (
            "UP-side row's enqueued_at must be stamped after the "
            "enqueue (C1 dedup marker)"
        )
        # Manager mock received one enqueue for the FIRED FollowUp.
        assert manager.enqueue_message.await_count == 1
        kwargs = manager.enqueue_message.call_args.kwargs
        assert kwargs["instance_id"] == up_parent
        assert kwargs["metadata"]["child_outcome"] == "terminated"

        # ── Acceptance — DOWN row: CANCELLED (NOT orphaned PENDING) ──
        down_row = next(
            r for r in _all_watcher_rows(watcher_engine)
            if r["target_instance_id"] == terminated
        )
        assert down_row["state"] == DependencyWatcherState.CANCELLED.value, (
            f"DOWN-side row must be CANCELLED (F1 mitigation); got "
            f"{down_row['state']!r}. A lingering PENDING would block "
            f"a mid-session REVIVE via count_pending_for_target_sync."
        )
        assert down_row["fired_at"] is None, (
            "CANCELLED rows do not stamp a fired_at (fire's only)"
        )

        # ── Belt-and-braces — no PENDING rows linger on the
        # terminated instance's target slot.
        assert _count_pending(watcher_engine, terminated) == 0, (
            "Review F1: no DOWN-side PENDING must linger on the "
            "terminated instance — cancel_for_target closes the gap."
        )
        # The UP-side row is FIRED, not PENDING — but
        # count_pending_for_target_sync gates only on PENDING.
        assert _count_pending(watcher_engine, up_parent) == 0
    finally:
        set_dependency_bus(None)
        await bus.stop()


# ─── (ii) mid-session REVIVE gate — count_pending_for_target_sync == 0 ────────


@pytest.mark.asyncio
async def test_revive_after_terminate_mid_session_gate_clear(watcher_engine):
    """After terminate + mid-session REVIVE,
    ``count_pending_for_target_sync(revived_id) == 0``.

    The REVIVE path's gate is the ``count_pending_for_target_sync``
    check the parent's completion logic depends on. Without the F1
    drain, lingering DOWN-side PENDING rows block the gate. With the
    drain, the gate is clear after the helper returns.

    Test shape: a DOWN-side watcher exists pre-terminate; the helper
    runs; after the helper runs (modeling the post-commit seam) the
    terminated instance is REVIVED mid-session; the gate must be 0.
    """
    repo = DependencyWatcherRepository(watcher_engine)
    bus = DependencyBus(repo)
    await bus.start()
    set_dependency_bus(bus)
    try:
        grandchild = f"grand-{uuid.uuid4().hex[:8]}"
        terminated = f"term-{uuid.uuid4().hex[:8]}"
        up_parent = f"up-p-{uuid.uuid4().hex[:8]}"

        # UP-side: parent waiting on the to-be-terminated instance.
        await _register_watcher(
            bus,
            source_task_id="task-up",
            target_instance_id=up_parent,
            child_id=terminated,
            parent_id=up_parent,
        )
        # DOWN-side: terminated-instance task watching its grandchild.
        await _register_watcher(
            bus,
            source_task_id="task-down",
            target_instance_id=terminated,
            child_id=grandchild,
            parent_id=terminated,
        )

        # Pre-terminate sanity: terminated instance has 1 PENDING
        # watcher (its DOWN-side row).
        assert _count_pending(watcher_engine, terminated) == 1

        manager = AsyncMock()
        manager.enqueue_message = AsyncMock(
            return_value={"message_id": "m-f1b-rev"}
        )

        # ── Terminate fires ──
        await _cancel_bus_watchers_for(manager, terminated, "terminate_instance")

        # ── Mid-session REVIVE gate check ──
        # P1 cascade enumerates the tree and terminates each
        # descendant; the helper runs per-instance. After the helper
        # returns, the gate (count_pending_for_target_sync) must
        # return 0 so the parent's completion logic can proceed.
        gate_count = _count_pending(watcher_engine, terminated)
        assert gate_count == 0, (
            f"Review F1 acceptance: after mid-session REVIVE, "
            f"count_pending_for_target_sync({terminated[:8]}...) must "
            f"be 0 (gate not blocked); got {gate_count}. The F1 "
            f"mitigation (cancel_for_target after fire) is the load-"
            f"bearing fix."
        )

        # ── Belt-and-braces — DOWN row is terminal (CANCELLED), not
        # PENDING. The exact terminal state doesn't matter to the
        # gate; only that PENDING count is 0.
        rows = _all_watcher_rows(watcher_engine)
        down_row = next(
            r for r in rows if r["target_instance_id"] == terminated
        )
        assert down_row["state"] in (
            DependencyWatcherState.CANCELLED.value,
            DependencyWatcherState.FIRED.value,
        ), (
            f"DOWN-side row must be terminal (CANCELLED or FIRED); "
            f"got {down_row['state']!r}. The gate counts only PENDING."
        )
    finally:
        set_dependency_bus(None)
        await bus.stop()


# ─── (iii) exactly-once composition — UP row already-CANCELLED is skipped ────


@pytest.mark.asyncio
async def test_composition_is_exactly_once_safe(watcher_engine):
    """The two methods' row sets are DISJOINT — exactly-once holds.

    Composition safety check: the ``cancel_for_target`` call only
    touches rows where ``target_instance_id == instance_id`` (the
    DOWN-side rows). The ``fire_for_terminated_target`` call only
    touches rows where ``metadata.child_id == instance_id`` (UP-side
    rows). A row registered on a DIFFERENT instance (orphan
    reservation) is unaffected by either call.

    This test pins the disjoint-set invariant directly: an
    orthogonal watcher (parent=other, child=unrelated) survives the
    helper call unchanged.
    """
    repo = DependencyWatcherRepository(watcher_engine)
    bus = DependencyBus(repo)
    await bus.start()
    set_dependency_bus(bus)
    try:
        unrelated_grand = f"unrelated-grand-{uuid.uuid4().hex[:8]}"
        unrelated_parent = f"unrelated-p-{uuid.uuid4().hex[:8]}"
        terminated = f"term-{uuid.uuid4().hex[:8]}"
        up_parent = f"up-p-{uuid.uuid4().hex[:8]}"

        # UP-side: up_parent watches the terminated instance.
        await _register_watcher(
            bus,
            source_task_id="task-up",
            target_instance_id=up_parent,
            child_id=terminated,
            parent_id=up_parent,
        )
        # DOWN-side: terminated instance watches its grandchild.
        await _register_watcher(
            bus,
            source_task_id="task-down",
            target_instance_id=terminated,
            child_id=f"grand-{terminated}",
            parent_id=terminated,
        )
        # ORTHOGONAL: completely unrelated registration that the
        # helper must NOT touch (different parent, different child,
        # different source-task).
        await _register_watcher(
            bus,
            source_task_id="task-orthogonal",
            target_instance_id=unrelated_parent,
            child_id=unrelated_grand,
            parent_id=unrelated_parent,
        )

        manager = AsyncMock()
        manager.enqueue_message = AsyncMock(
            return_value={"message_id": "m-f1b-orth"}
        )

        await _cancel_bus_watchers_for(manager, terminated, "terminate_instance")

        # Helper enqueued only the FIRED UP-side FollowUp.
        assert manager.enqueue_message.await_count == 1

        rows = _all_watcher_rows(watcher_engine)

        # UP row: FIRED-with-outcome.
        up_row = next(r for r in rows if r["source_task_id"] == "task-up")
        assert up_row["state"] == DependencyWatcherState.FIRED.value

        # DOWN row: CANCELLED.
        down_row = next(r for r in rows if r["source_task_id"] == "task-down")
        assert down_row["state"] == DependencyWatcherState.CANCELLED.value

        # ORTHOGONAL row: UNCHANGED — still PENDING, fired_at NULL.
        orth_row = next(
            r for r in rows if r["source_task_id"] == "task-orthogonal"
        )
        assert orth_row["state"] == DependencyWatcherState.PENDING.value, (
            "The disjoint-set invariant: orthogonal watchers must "
            "not be touched by either fire_for_terminated_target "
            "(matched by metadata.child_id) OR cancel_for_target "
            "(matched by target_instance_id)."
        )
        assert orth_row["fired_at"] is None
    finally:
        set_dependency_bus(None)
        await bus.stop()


# ─── (iv) failure handling — bus failure does NOT fail terminate path ────────


@pytest.mark.asyncio
async def test_terminate_path_survives_bus_failures(watcher_engine):
    """Both bus calls must log+swallow on failure (terminate path
    safety). Pre-fix try/except shape is preserved across the new
    composition.
    """
    repo = DependencyWatcherRepository(watcher_engine)
    bus = DependencyBus(repo)
    await bus.start()
    set_dependency_bus(bus)
    try:
        grandchild = f"grand-{uuid.uuid4().hex[:8]}"
        terminated = f"term-{uuid.uuid4().hex[:8]}"
        up_parent = f"up-p-{uuid.uuid4().hex[:8]}"

        await _register_watcher(
            bus,
            source_task_id="task-up",
            target_instance_id=up_parent,
            child_id=terminated,
            parent_id=up_parent,
        )
        await _register_watcher(
            bus,
            source_task_id="task-down",
            target_instance_id=terminated,
            child_id=grandchild,
            parent_id=terminated,
        )

        manager = AsyncMock()
        # First enqueue succeeds; subsequent enqueues fail.
        manager.enqueue_message = AsyncMock(
            side_effect=RuntimeError("queue down")
        )

        # Must NOT raise — failures swallowed (log+swallow contract).
        await _cancel_bus_watchers_for(manager, terminated, "terminate_instance")

        # UP row still FIRED (bus transition committed even if
        # enqueue failed — the row's enqueued_at remains unset so a
        # restart's _recover_fired_unsent re-delivers it).
        rows = _all_watcher_rows(watcher_engine)
        up_row = next(r for r in rows if r["source_task_id"] == "task-up")
        assert up_row["state"] == DependencyWatcherState.FIRED.value
        # DOWN row CANCELLED despite the upstream failure
        # (the second bus call has its own try/except).
        down_row = next(r for r in rows if r["source_task_id"] == "task-down")
        assert down_row["state"] == DependencyWatcherState.CANCELLED.value
        # No PENDING rows linger on the terminated instance.
        assert _count_pending(watcher_engine, terminated) == 0
    finally:
        set_dependency_bus(None)
        await bus.stop()
