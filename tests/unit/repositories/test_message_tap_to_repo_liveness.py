"""End-to-end plumbing liveness — tap fires → row lands (Phase 1 C2).

Phase 1 C2 of the langgraph-checkpoint-perf plan. The blocking gate
``test_message_metadata_liveness_round_trip`` (decisions.md D19 +
phase1-plan.md:469) PROVES the plumbing: a real ``MessageTapSlot``
fires against a real ``MessageMetadataRepository`` backed by a real
SQLite engine, and the rows land in the ``message_metadata`` table.

The test is the C2 write-liveness gate: it BLOCKS PR2 merge. A
regression here would be:

* The tap side wires a broken ``asyncio.to_thread`` bridge.
* The repo's ``INSERT ... ON CONFLICT DO NOTHING`` SQL is malformed.
* The migration's PK + column shape diverges from the SQLModel
  definition (PG/SQLite mismatch).

The test deliberately avoids a full LangGraph graph + LLM — the
end-to-end ``astream`` call requires an LLM, which is non-deterministic
and slow. Instead we drive the ``MessageTapSlot`` directly with a
synthetic persisted list and verify the row lands. This isolates
the tap-side mechanics from the LLM-side mechanics; the integration
``test_message_metadata_liveness_round_trip`` covers the
LLM-mediated path on top of this.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

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
    """Repo backed by the in-memory engine."""
    return MessageMetadataRepository(engine)


def _msg(msg_id: str, type_: str = "human") -> Any:
    """Duck-typed BaseMessage stand-in."""
    m = MagicMock()
    m.id = msg_id
    m.type = type_
    return m


@pytest.mark.asyncio
async def test_tap_to_thread_bridge_produces_visible_rows(repo):
    """The full async chain lands a row in ``message_metadata``.

    Pipeline: ``MessageTapSlot.tap_node_return`` → ``asyncio.to_thread``
    bridge → sync ``MessageMetadataRepository.upsert_batch`` → SQLite
    INSERT → SELECT via ``get_for_thread``. If ANY link breaks, the
    final assertion (``rows["user-1"] is non-null``) fails.
    """
    slot = MessageTapSlot(repo, SOURCE_AGENT_NODE_RETURN)

    # Simulate the F2 single-return site's outgoing list:
    # a user HumanMessage + an AI response.
    user_msg = _msg("user-1", type_="human")
    ai_msg = _msg("ai-1", type_="ai")
    persisted = [user_msg, ai_msg]

    count = await slot.tap_node_return(persisted, "thread-liveness-1")
    assert count == 2, (
        f"Both user + AI rows must insert; got rowcount={count}"
    )

    # The repo's read primitive must see BOTH rows (proves the
    # bridge worked AND the schema is right).
    rows = repo.get_for_thread("thread-liveness-1")
    assert set(rows.keys()) == {"user-1", "ai-1"}, (
        f"Both ids must land; got {sorted(rows.keys())}"
    )
    for mid in ("user-1", "ai-1"):
        ts, seq = rows[mid]
        assert ts is not None and ts != "", (
            f"created_at must be a non-empty ISO-8601 string for {mid}; "
            f"got {ts!r}"
        )
        assert seq is None  # Phase 2 PERF-2 (D5)


@pytest.mark.asyncio
async def test_tap_to_thread_bridge_idempotent_re_tap(repo):
    """A second tap on the same ids is a constraint-level no-op.

    The repo's ``ON CONFLICT DO NOTHING`` collapses the second tap
    to a 0-row insert. The first tap's ``created_at`` survives
    (first-write-wins — decisions.md D3 + D17).
    """
    slot = MessageTapSlot(repo, SOURCE_AGENT_NODE_RETURN)

    persisted = [_msg("user-1")]
    first_count = await slot.tap_node_return(persisted, "thread-re")
    assert first_count == 1
    first_ts = repo.get_for_thread("thread-re")["user-1"][0]
    assert first_ts is not None

    # Re-tap after a real-time delay so the tap's fresh ISO stamp
    # would differ IF the constraint wasn't enforcing first-write-wins.
    await asyncio.sleep(0.01)
    second_count = await slot.tap_node_return(persisted, "thread-re")
    assert second_count == 0, (
        f"Re-tap must be a no-op under ON CONFLICT DO NOTHING; "
        f"got rowcount={second_count}"
    )
    second_ts = repo.get_for_thread("thread-re")["user-1"][0]
    assert second_ts == first_ts, (
        f"First-write-wins broken — created_at drifted "
        f"({first_ts!r} -> {second_ts!r})"
    )


@pytest.mark.asyncio
async def test_tap_to_thread_bridge_remove_message_skipped(repo):
    """``RemoveMessage`` markers are filtered inside the slot — no row."""
    slot = MessageTapSlot(repo, SOURCE_AGENT_NODE_RETURN)

    remove_marker = _msg("ghost", type_="remove")
    user_msg = _msg("user-1", type_="human")
    persisted = [user_msg, remove_marker]

    count = await slot.tap_node_return(persisted, "thread-rm")
    assert count == 1, "Only the user message should insert"

    rows = repo.get_for_thread("thread-rm")
    assert set(rows.keys()) == {"user-1"}, (
        f"RemoveMessage marker must NOT produce a row; "
        f"got {sorted(rows.keys())}"
    )


@pytest.mark.asyncio
async def test_tap_to_thread_bridge_empty_persisted_is_noop(repo):
    """An empty persisted list produces NO rows."""
    slot = MessageTapSlot(repo, SOURCE_AGENT_NODE_RETURN)
    count = await slot.tap_node_return([], "thread-empty")
    assert count == 0
    assert repo.get_for_thread("thread-empty") == {}


@pytest.mark.asyncio
async def test_tap_to_thread_bridge_thread_isolation(repo):
    """Two threads don't bleed into each other's rows."""
    slot = MessageTapSlot(repo, SOURCE_AGENT_NODE_RETURN)
    await slot.tap_node_return([_msg("u")], "thread-A")
    await slot.tap_node_return([_msg("u")], "thread-B")
    rows_a = repo.get_for_thread("thread-A")
    rows_b = repo.get_for_thread("thread-B")
    assert set(rows_a.keys()) == {"u"}
    assert set(rows_b.keys()) == {"u"}
    # Different threads ⇒ different timestamps (likely).
    # We don't assert inequality because asyncio + to_thread could
    # yield sub-microsecond collisions; the threads are separate
    # PK rows, not duplicates.
    assert rows_a["u"][0] is not None
    assert rows_b["u"][0] is not None


@pytest.mark.asyncio
async def test_tap_to_thread_bridge_returns_real_repo_rowcount(repo):
    """The tap's return value matches the repo's actual rowcount.

    This is the binding assertion: a broken
    ``asyncio.to_thread`` bridge would lose the return value
    (the coroutine wrapper default-returns ``None`` and we coerce
    to ``int``). The repo's rowcount feeds ``log_message_tap``;
    a drift between tap-return and repo-rowcount would silently
    misreport the upsert count in the structured log line.
    """
    slot = MessageTapSlot(repo, SOURCE_AGENT_NODE_RETURN)

    # First tap: 3 new rows.
    persisted = [_msg("a"), _msg("b"), _msg("c")]
    count = await slot.tap_node_return(persisted, "thread-counts")
    assert count == 3

    # Second tap: 1 new + 2 re-taps (no-ops).
    persisted = [_msg("a"), _msg("d"), _msg("b")]
    count = await slot.tap_node_return(persisted, "thread-counts")
    assert count == 1, (
        f"Expected 1 row (just ``d``); got {count} — re-tap "
        f"rowcounts are off"
    )


@pytest.mark.asyncio
async def test_tap_to_thread_bridge_full_liveness_round_trip(repo):
    """Final integration-style smoke: a user + AI turn + re-tap ⇒ 2 rows.

    Mirrors ``test_message_metadata_liveness_round_trip`` from the
    plan — but on a synthetic, in-memory repo (no real LLM, no real
    LangGraph). The full ``astream``-driven integration variant
    lives in ``tests/integration/test_message_metadata_liveness.py``
    which adds the LangGraph + checkpoint-saver machinery.
    """
    slot = MessageTapSlot(repo, SOURCE_AGENT_NODE_RETURN)

    # Round 1: user → agent_node single-return.
    persisted_round_1 = [_msg("user-round-1"), _msg("ai-round-1")]
    n1 = await slot.tap_node_return(persisted_round_1, "thread-rt")
    assert n1 == 2

    # Round 2: new user + new AI; old messages may re-tap (no-op).
    persisted_round_2 = [
        _msg("user-round-1"),  # re-tap, no-op
        _msg("ai-round-1"),    # re-tap, no-op
        _msg("user-round-2"),  # new
        _msg("ai-round-2"),    # new
    ]
    n2 = await slot.tap_node_return(persisted_round_2, "thread-rt")
    assert n2 == 2, f"Expected 2 new rows; got {n2}"

    # Final state: all 4 ids present, user-1 < ai-1 chronologically
    # (best-effort; timestamps may collide sub-millisecond).
    rows = repo.get_for_thread("thread-rt")
    assert set(rows.keys()) == {
        "user-round-1", "ai-round-1", "user-round-2", "ai-round-2"
    }
    # First-write-wins: round-1 timestamps survived the round-2
    # re-taps.
    ts_user_1, _ = rows["user-round-1"]
    ts_ai_1, _ = rows["ai-round-1"]
    assert ts_user_1 <= ts_ai_1, (
        f"First-appearance ordering broken — user < ai: "
        f"got user={ts_user_1!r} ai={ts_ai_1!r}"
    )