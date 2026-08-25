"""Unit tests for ``DependencyBus.fire_for_terminated_target`` (Phase 2, task 2.2).

B3 fix acceptance: the terminate path fires (does not cancel) the
PENDING watchers waiting on a terminated instance, stamps ``fired_at``,
and returns FollowUps whose metadata carries the additive
``child_outcome`` key.

Cases (per phase2-plan.md task 2.2):
  1. empty    — no PENDING watchers → [] and no state change.
  2. single   — one waiting parent → one FollowUp, FIRED + fired_at.
  3. multi    — two waiting parents (different source tasks) → both
                fired, each FollowUp carries child_outcome.
  4. race-with-emit_terminal — a concurrent task-keyed
                ``emit_terminal`` that wins first leaves nothing for
                the fire (exactly-once via transition_state rowcount);
                and vice-versa the fire winning leaves [] for the
                emit.
  5. race-with-cancel_for_target — a watcher already CANCELLED by
                ``cancel_for_target`` is skipped (rowcount guard);
                exactly-once holds across the two sibling methods.

In-memory SQLite, no daemon, no PostgreSQL. Mirrors the fixture
strategy of ``tests/test_dependency_bus.py`` (StaticPool +
check_same_thread=False so ``asyncio.to_thread`` shares the
connection).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

# Register table models so the watcher table exists for create_all.
import daemon.repositories.dependency_bus.models  # noqa: F401

from daemon.repositories.dependency_bus import (
    DependencyWatcher,
    DependencyWatcherRepository,
    DependencyWatcherState,
)
from daemon.services.dependency_bus import (
    DependencyBus,
    FollowUp,
    Outcome,
)


# -------------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------------


@pytest.fixture
def repo():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    watcher_table = SQLModel.metadata.tables.get("dependency_watchers")
    if watcher_table is not None:
        watcher_table.create(eng, checkfirst=True)
    return DependencyWatcherRepository(eng)


@pytest.fixture
async def bus(repo):
    b = DependencyBus(repo)
    await b.start()
    try:
        yield b
    finally:
        await b.stop()


def _seed_watcher(
    repo: DependencyWatcherRepository,
    *,
    source_task_id: str,
    target_instance_id: str,
    child_id: str,
    state: str = DependencyWatcherState.PENDING.value,
    fired_at: str | None = None,
    enqueued_at: str | None = None,
) -> str:
    """Insert a watcher row; return its watch_id."""
    fu = FollowUp(
        target_instance_id=target_instance_id,
        message=f"[dependency_bus] child {child_id} completed",
        source=f"internal_agent:{target_instance_id}",
        metadata={
            "kind": "child_complete",
            "child_id": child_id,
            "parent_id": target_instance_id,
            "message_id": f"msg-{source_task_id}",
        },
    )
    row = DependencyWatcher(
        source_task_id=source_task_id,
        target_instance_id=target_instance_id,
        follow_up_payload=fu.to_payload(),
        state=state,
        fired_at=fired_at,
        enqueued_at=enqueued_at,
    )
    repo.insert(row)
    return row.watch_id


def _read_watcher(repo: DependencyWatcherRepository, watch_id: str):
    with repo.engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT state, fired_at, enqueued_at "
                "FROM dependency_watchers WHERE watch_id = :wid"
            ),
            {"wid": watch_id},
        ).mappings().first()


# -------------------------------------------------------------------------
# Case 1 — empty
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fire_for_terminated_empty(bus, repo):
    """No PENDING watchers waiting on the terminated instance → []."""
    # A watcher for a DIFFERENT child must be untouched.
    other_wid = _seed_watcher(
        repo,
        source_task_id="task-other",
        target_instance_id="parent-X",
        child_id="child-other",
    )

    fired = await bus.fire_for_terminated_target(
        "child-not-watched", Outcome(status="terminated")
    )

    assert fired == []
    row = _read_watcher(repo, other_wid)
    assert row["state"] == DependencyWatcherState.PENDING.value


# -------------------------------------------------------------------------
# Case 2 — single
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fire_for_terminated_single(bus, repo):
    """One waiting parent → FIRED with fired_at + FollowUp metadata."""
    wid = _seed_watcher(
        repo,
        source_task_id="task-1",
        target_instance_id="parent-A",
        child_id="child-T",
    )

    fired = await bus.fire_for_terminated_target(
        "child-T", Outcome(status="terminated")
    )

    assert len(fired) == 1
    fu = fired[0]
    assert fu.target_instance_id == "parent-A"
    # Q2 (Rev 2.1): additive metadata keys only.
    assert fu.metadata["child_outcome"] == "terminated"
    assert fu.metadata["source_task_id"] == "task-1"
    # Existing metadata keys survive untouched (no field stripping).
    assert fu.metadata["kind"] == "child_complete"
    assert fu.metadata["child_id"] == "child-T"

    row = _read_watcher(repo, wid)
    assert row["state"] == DependencyWatcherState.FIRED.value
    assert row["fired_at"] is not None
    # The fire does NOT stamp enqueued_at — the CALLER stamps after
    # enqueueing (crash-window contract: unstamped FIRED rows are
    # re-delivered by _recover_fired_unsent on restart).
    assert row["enqueued_at"] is None


# -------------------------------------------------------------------------
# Case 3 — multi
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fire_for_terminated_multi(bus, repo):
    """Two waiting parents (different source tasks) → both fired."""
    wid1 = _seed_watcher(
        repo,
        source_task_id="task-1",
        target_instance_id="parent-A",
        child_id="child-T",
    )
    wid2 = _seed_watcher(
        repo,
        source_task_id="task-2",
        target_instance_id="parent-B",
        child_id="child-T",
    )

    fired = await bus.fire_for_terminated_target(
        "child-T", Outcome(status="terminated")
    )

    assert len(fired) == 2
    targets = {fu.target_instance_id for fu in fired}
    assert targets == {"parent-A", "parent-B"}
    for fu in fired:
        assert fu.metadata["child_outcome"] == "terminated"
    for wid in (wid1, wid2):
        row = _read_watcher(repo, wid)
        assert row["state"] == DependencyWatcherState.FIRED.value
        assert row["fired_at"] is not None


# -------------------------------------------------------------------------
# Case 4 — race with emit_terminal
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fire_for_terminated_race_with_emit_terminal(bus, repo):
    """Exactly-once across fire_for_terminated_target vs emit_terminal.

    The guarded ``WHERE state='PENDING'`` UPDATE means whichever side
    transitions first wins; the loser sees rowcount==0 and returns no
    FollowUp for that row.
    """
    # 4a: emit_terminal wins first → the fire finds nothing PENDING.
    wid = _seed_watcher(
        repo,
        source_task_id="task-1",
        target_instance_id="parent-A",
        child_id="child-T",
    )
    emitted = await bus.emit_terminal(
        "task-1", Outcome(status="completed")
    )
    assert len(emitted) == 1

    fired = await bus.fire_for_terminated_target(
        "child-T", Outcome(status="terminated")
    )
    assert fired == []
    row = _read_watcher(repo, wid)
    assert row["state"] == DependencyWatcherState.FIRED.value
    assert row["fired_at"] is not None

    # 4b: the fire wins first → the emit finds nothing PENDING.
    wid2 = _seed_watcher(
        repo,
        source_task_id="task-2",
        target_instance_id="parent-B",
        child_id="child-U",
    )
    fired2 = await bus.fire_for_terminated_target(
        "child-U", Outcome(status="terminated")
    )
    assert len(fired2) == 1
    emitted2 = await bus.emit_terminal(
        "task-2", Outcome(status="completed")
    )
    assert emitted2 == []
    row2 = _read_watcher(repo, wid2)
    assert row2["state"] == DependencyWatcherState.FIRED.value


# -------------------------------------------------------------------------
# Case 5 — race with cancel_for_target
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fire_for_terminated_race_with_cancel_for_target(bus, repo):
    """A CANCELLED watcher is invisible to the fire (rowcount guard).

    ``cancel_for_target`` cancels target-side rows; when it has already
    transitioned a row the fire's guarded UPDATE no-ops — no FollowUp,
    no double-delivery. Exactly-once holds across the sibling methods.
    """
    # 5a: cancel_for_target(parent) wins on the parent's own watcher.
    wid_cancelled = _seed_watcher(
        repo,
        source_task_id="task-1",
        target_instance_id="parent-A",
        child_id="child-T",
    )
    cancelled = await bus.cancel_for_target("parent-A")
    assert cancelled == 1

    fired = await bus.fire_for_terminated_target(
        "child-T", Outcome(status="terminated")
    )
    assert fired == []
    row = _read_watcher(repo, wid_cancelled)
    assert row["state"] == DependencyWatcherState.CANCELLED.value

    # 5b: fresh watcher, fire wins, cancel after → cancel counts 0 for
    # the already-FIRED row (the terminate flow's fire runs first in
    # the post-commit outbox; a later cancel scan is a no-op).
    wid_fired = _seed_watcher(
        repo,
        source_task_id="task-2",
        target_instance_id="parent-B",
        child_id="child-U",
    )
    fired2 = await bus.fire_for_terminated_target(
        "child-U", Outcome(status="terminated")
    )
    assert len(fired2) == 1
    cancelled2 = await bus.cancel_for_target("parent-B")
    assert cancelled2 == 0
    row2 = _read_watcher(repo, wid_fired)
    assert row2["state"] == DependencyWatcherState.FIRED.value


# -------------------------------------------------------------------------
# Supplementary — parent gate clears (B3 acceptance core)
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fire_clears_parent_pending_gate(bus, repo):
    """The waiting parent's count_pending gate clears after the fire.

    This is the B3 acceptance core: pre-fix the terminate path left
    the watcher PENDING forever and the parent's
    ``count_pending_for_target_sync`` gate never cleared (ghost-child
    wait). Post-fix the FIRED transition clears it.
    """
    _seed_watcher(
        repo,
        source_task_id="task-1",
        target_instance_id="parent-A",
        child_id="child-T",
    )
    assert bus.count_pending_for_target_sync("parent-A") == 1

    await bus.fire_for_terminated_target(
        "child-T", Outcome(status="terminated")
    )

    assert bus.count_pending_for_target_sync("parent-A") == 0
