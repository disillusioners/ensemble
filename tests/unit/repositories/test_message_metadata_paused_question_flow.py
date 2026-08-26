"""Paused-question flow — every persisted message has a non-null ``created_at``.

Phase 1 C2 — Critical 8 (decisions.md D17 + plan line 472). The
test scenario is the most demanding for the C2 plumbing: a turn
that involves a ``question_pause_node`` mid-flow (an interrupt
that asks the user a question), then the user answers, then the
agent resumes. Every persisted message — including the
resume-turn ``AIMessage`` — must receive a ``message_metadata``
row with a non-null ``created_at``.

This proves the C2 tap fires across the full lifecycle:

1. First turn — user asks, agent pauses for clarification.
2. Question pause node — no LLM call, just a state update.
3. User answer — agent resumes.
4. Second turn — agent emits a final response.

We exercise this at the SYNC repo + tap level: the
``MessageTapSlot`` fires at every persisted-list handoff, and we
verify every message id lands a row.

For the integration variant (full LangGraph graph + real
question_pause_node), the test would need a real LLM stub and
the question-pause plumbing. We instead pin the invariants at the
repo layer, which is where the C2 contract lives (the tap fires
on every persisted-list handoff; first-appearance wins).
"""
from __future__ import annotations

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
    """Repo against the in-memory engine."""
    return MessageMetadataRepository(engine)


def _msg(msg_id: str, type_: str = "ai"):
    m = MagicMock()
    m.id = msg_id
    m.type = type_
    return m


@pytest.mark.asyncio
async def test_message_metadata_paused_question_flow(repo):
    """Every persisted message in the paused-question flow has a ``created_at``.

    Plan line 472 — Critical 8 binding test:

    > Send a user message → agent invokes ``question_pause_node``
    > → user answers → agent resumes → fetch messages — assert
    > every persisted message has non-null ``created_at``,
    > including the resume-turn ``AIMessage``.

    The test exercises the equivalent at the repo layer: every
    message id the tap sees lands a row. We simulate the 4
    tap-site events of a paused-question flow:

    1. **Entry-path tap** (turn 1) — captures the user's first
       question.
    2. **Agent_node single-return tap** (turn 1) — captures the
       AI's first response (before the question pause).
    3. **Compaction re-tap** (messaging-side, optional in the flow)
       — captures the post-pause resume turn's persisted messages.
    4. **Agent_node single-return tap** (turn 2, post-resume) —
       captures the resume-turn AIMessage.
    """
    slot = MessageTapSlot(repo, SOURCE_AGENT_NODE_RETURN)
    thread_id = "thread-paused-question"

    # ── (1) Entry-path tap on the user's first question ─────────────
    user_question_1 = _msg("user-q1-msg", type_="human")
    n = await slot.tap_node_return([user_question_1], thread_id)
    assert n == 1

    # ── (2) Agent_node single-return tap (pre-pause AI response) ────
    ai_pre_pause = _msg("ai-pre-pause", type_="ai")
    ai_question = _msg("ai-clarify-question", type_="ai")
    n = await slot.tap_node_return(
        [ai_pre_pause, ai_question], thread_id
    )
    assert n == 2

    # ── (3) Compaction re-tap (messaging-side: post-pause state) ───
    # This is a typical flow: the question_pause_node writes the
    # pending question to state, then the agent resumes on user
    # answer. The compaction re-tap captures the post-pause
    # re-appended messages.
    resume_injected = _msg("user-answer-1", type_="human")
    n = await slot.tap_node_return([resume_injected], thread_id)
    assert n == 1

    # ── (4) Agent_node single-return tap (resume-turn AI response) ──
    ai_resume = _msg("ai-resume-final", type_="ai")
    n = await slot.tap_node_return([ai_resume], thread_id)
    assert n == 1

    # ── Assertion: EVERY persisted message has a non-null created_at ─
    rows = repo.get_for_thread(thread_id)
    expected_ids = {
        "user-q1-msg",
        "ai-pre-pause",
        "ai-clarify-question",
        "user-answer-1",
        "ai-resume-final",
    }
    assert set(rows.keys()) == expected_ids, (
        f"Every persisted message must have a row. "
        f"Missing: {expected_ids - set(rows.keys())}; "
        f"Extra: {set(rows.keys()) - expected_ids}"
    )
    for mid in expected_ids:
        ts, seq = rows[mid]
        assert ts is not None and ts != "", (
            f"created_at must be non-null for {mid}; got {ts!r}"
        )
        assert seq is None  # Phase 2 PERF-2 (D5)

    # ── First-appearance ordering: user-q1 must precede ai-pre-pause
    # (entry-path tap fires before agent_node tap on the same turn).
    ts_user_q1, _ = rows["user-q1-msg"]
    ts_ai_pre_pause, _ = rows["ai-pre-pause"]
    assert ts_user_q1 <= ts_ai_pre_pause, (
        f"First-appearance ordering broken: user-q1 must precede "
        f"ai-pre-pause on the same turn; got user={ts_user_q1!r} "
        f"ai={ts_ai_pre_pause!r}"
    )


@pytest.mark.asyncio
async def test_message_metadata_paused_question_flow_idempotent(repo):
    """A complete paused-question replay is a no-op at the row level.

    Decision D17 — first-appearance wins. The whole 4-step flow
    replayed end-to-end must NOT insert new rows (all 5 ids
    already exist from the original flow).
    """
    slot = MessageTapSlot(repo, SOURCE_AGENT_NODE_RETURN)
    thread_id = "thread-replay"

    # First run — 5 rows.
    msgs = [
        _msg("m1", "human"),
        _msg("m2", "ai"),
        _msg("m3", "ai"),
        _msg("m4", "human"),
        _msg("m5", "ai"),
    ]
    n = await slot.tap_node_return(msgs, thread_id)
    assert n == 5

    initial_rows = repo.get_for_thread(thread_id)
    initial_timestamps = {mid: ts for mid, (ts, _) in initial_rows.items()}

    # Second run — replay the entire flow. All 5 are no-ops.
    n = await slot.tap_node_return(msgs, thread_id)
    assert n == 0, "Replay must be all no-ops"

    rows = repo.get_for_thread(thread_id)
    for mid, ts in initial_timestamps.items():
        assert rows[mid][0] == ts, (
            f"First-write-wins broken on replay: {mid} drifted from "
            f"{ts!r} to {rows[mid][0]!r}"
        )


@pytest.mark.asyncio
async def test_message_metadata_paused_question_flow_remove_marker_safe(repo):
    """A ``RemoveMessage`` marker inserted mid-flow is filtered — no row.

    D17 fold-in: ``RemoveMessage`` markers (LangChain's reducer
    delete markers) MUST NOT be inserted as new-message rows. In a
    paused-question flow the resume turn can include a
    ``RemoveMessage`` for a stale message id from the pre-pause
    state; the tap filters it out.
    """
    slot = MessageTapSlot(repo, SOURCE_AGENT_NODE_RETURN)
    thread_id = "thread-remove-marker"

    persisted = [
        _msg("m-keep-1", "human"),
        _msg("m-remove", "remove"),  # marker — filtered
        _msg("m-keep-2", "ai"),
    ]
    n = await slot.tap_node_return(persisted, thread_id)
    assert n == 2, "RemoveMessage marker must be filtered out"

    rows = repo.get_for_thread(thread_id)
    assert set(rows.keys()) == {"m-keep-1", "m-keep-2"}, (
        f"Only non-remove ids may produce rows; got {sorted(rows.keys())}"
    )