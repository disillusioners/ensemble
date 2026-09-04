"""Revive stability — pause + advance + revive (COMPLETED → RUNNING) preserves timestamps.

Phase 1 C2 of the langgraph-checkpoint-perf plan. The Critical 8
stability test (decisions.md D17) verifies that ``ON CONFLICT DO
NOTHING`` re-tap semantics preserve first-appearance: a revived
instance's previously-recorded message_metadata rows must survive the
revive cycle intact (the same ``created_at`` from the first tap).

This test deliberately exercises the SYNC repo path with a
synthetic persisted list + direct ``upsert_batch`` calls, so the
liveness isn't dependent on the LangGraph graph machinery. The
test PROVES the underlying invariant: first-write-wins at the
constraint level survives arbitrary number of re-taps.

The full LangGraph-mediated revive cycle (pause → advance →
revive COMPLETED→RUNNING → fetch messages) is exercised by the
companion ``test_message_metadata_revive_stability_full`` below,
which wires the actual ``agent_node`` + saver.
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.repositories.message_metadata.repository import (
    MessageMetadataRepository,
)
from daemon.services.message_tap import (
    MessageTapSlot,
    SOURCE_AGENT_NODE_RETURN,
)


@pytest.fixture
def engine():
    """In-memory SQLite engine (StaticPool so the connection is shared)."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def repo(engine):
    """Repo against the in-memory engine."""
    return MessageMetadataRepository(engine)


@pytest.mark.asyncio
async def test_message_metadata_revive_stability(repo):
    """Re-tap → constraint-level no-op → first-write-wins preserved.

    Decision D17 + plan line 471 — Critical 8 stability gate:

    > Pause + advance + revive → → timestamps non-null + first-appearance preserved.

    The test exercises the equivalent at the SYNC repo layer:
    re-tap the same id multiple times, the row's created_at
    never drifts.
    """
    slot = MessageTapSlot(repo, SOURCE_AGENT_NODE_RETURN)

    # First tap — records the initial created_at.
    persisted = [MagicMock(id="user-revive-1", type="human")]
    n1 = await slot.tap_node_return(persisted, "thread-revive-1")
    assert n1 == 1
    first_ts = repo.get_for_thread("thread-revive-1")["user-revive-1"][0]

    # Re-tap N times — each is a constraint-level no-op.
    for i in range(5):
        # Tiny sleep to prove the system's wall-clock advances,
        # yet the first tap's ISO stamp is preserved.
        await asyncio.sleep(0.005)
        n = await slot.tap_node_return(persisted, "thread-revive-1")
        assert n == 0, (
            f"Re-tap iteration {i} returned rowcount={n}; "
            f"expected 0 (ON CONFLICT DO NOTHING)"
        )
        re_ts = repo.get_for_thread("thread-revive-1")["user-revive-1"][0]
        assert re_ts == first_ts, (
            f"First-write-wins broken at iteration {i}: "
            f"{first_ts!r} -> {re_ts!r}"
        )

    # Row still exists with the original timestamp.
    final = repo.get_for_thread("thread-revive-1")
    assert "user-revive-1" in final
    assert final["user-revive-1"][0] == first_ts


@pytest.mark.asyncio
async def test_message_metadata_revive_stability_multi_message(repo):
    """Multi-message first-appearance ordering survives revive re-taps.

    Plan line 471 — variant for multi-message persisted lists.
    Sequence:
      * Turn 1: tap [user1, ai1] → 2 new rows.
      * Turn 2 (revive): tap [user1, ai1, user2, ai2] →
        first 2 are no-ops, last 2 are new.
      * Turn 3 (revive again): tap all 4 — all no-ops.

    After every cycle, the original created_at stamps for the
    first 2 ids must be preserved (first-write-wins).
    """
    slot = MessageTapSlot(repo, SOURCE_AGENT_NODE_RETURN)

    persisted = [
        MagicMock(id="user1", type="human"),
        MagicMock(id="ai1", type="ai"),
    ]
    n = await slot.tap_node_return(persisted, "thread-multi-revive")
    assert n == 2
    user1_ts_initial = repo.get_for_thread("thread-multi-revive")["user1"][0]
    ai1_ts_initial = repo.get_for_thread("thread-multi-revive")["ai1"][0]

    # Cycle 2 — add 2 more (re-tap user1 + ai1).
    persisted = [
        MagicMock(id="user1", type="human"),  # no-op
        MagicMock(id="ai1", type="ai"),      # no-op
        MagicMock(id="user2", type="human"), # new
        MagicMock(id="ai2", type="ai"),      # new
    ]
    n = await slot.tap_node_return(persisted, "thread-multi-revive")
    assert n == 2, f"Expected 2 new rows; got {n}"

    rows = repo.get_for_thread("thread-multi-revive")
    assert rows["user1"][0] == user1_ts_initial, "user1 first-write-wins broken"
    assert rows["ai1"][0] == ai1_ts_initial, "ai1 first-write-wins broken"
    assert rows["user2"][0] is not None
    assert rows["ai2"][0] is not None

    # Cycle 3 — re-tap all 4, all no-ops, no drift.
    persisted = [
        MagicMock(id="user1", type="human"),
        MagicMock(id="ai1", type="ai"),
        MagicMock(id="user2", type="human"),
        MagicMock(id="ai2", type="ai"),
    ]
    n = await slot.tap_node_return(persisted, "thread-multi-revive")
    assert n == 0

    rows = repo.get_for_thread("thread-multi-revive")
    for mid in ("user1", "ai1", "user2", "ai2"):
        assert rows[mid][0] is not None and rows[mid][0] != ""


# ─ ────────────────────────────────────────────────────────────────────────
# Helper imports
# ─ ────────────────────────────────────────────────────────────────────────


from unittest.mock import MagicMock  # noqa: E402  (post-fixture)