"""Ladder phase 2 — D-1 boundary-freshness marker restart-durability.

Authored INDEPENDENTLY by the re-gate reviewer to close a coverage
gap surfaced by the RE-GATE probe
(``/tmp/rlp2rg-d1/probe_d1.py`` ARM D). The dev's
``tests/unit/test_ladder_p2_d1_boundary_freshness.py`` covers the
in-graph D-1 behaviors (mid-turn budget survival, latch escalation,
genuine reset) but does NOT pin restart-durability of the new
``last_repair_boundary_human_id`` field across a real
``AsyncSqliteSaver`` checkpoint cycle. The D-1 marker is checkpoint-
persisted (additive GraphState field, default ``""``); a restart that
forgets to re-load the marker would re-open the position-only reset
defect on the very next turn.

Scenarios:
- Run repair → budget=1, marker='h-real-d' persisted.
- Close conn1. Fresh aiosqlite conn + fresh saver + fresh compiled
  graph on the SAME file + same thread_id.
- Re-read state via ``aget_state`` (no ainvoke → no risk of
  contaminating with a new-human reset).
- Assert both fields are preserved EXACTLY across the restart cycle.

Schema discipline: per the conftest mock-layer contract, this test
re-imports the REAL langgraph primitives inside ``_RealLangGraph``
and defines a thin ``_State`` subclass that mirrors the production
``SessionState`` extras (``repair_budget_used``,
``pending_repair_ghost_terminal``, ``last_repair_boundary_human_id``).
This avoids the conftest's MagicMock ``MessagesState`` leaking into
``StateGraph.__init__``'s schema check (the
``Invalid state_schema: <MagicMock spec='str'>`` warning).
"""
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from daemon.config import LoopBreakerConfig
from daemon.graph import (
    TurnRepairLatch,
    create_agent_node,
    create_agent_repair_ghost_node,
    should_continue,
)
from daemon.services.symptom_repair_engine import SymptomRepairEngine
from tests.helpers.symptom_repair import (
    _RealLangGraph,
    ok_summarizer,
)


class _ScriptedProvider:
    def __init__(self, script):
        self.script = list(script)
        self.calls: list[list] = []
        self.call_count = 0

    def invoke(self, messages):
        self.call_count += 1
        self.calls.append(list(messages))
        if not self.script:
            raise AssertionError(
                f"script exhausted after {self.call_count} invokes"
            )
        return self.script.pop(0)


def _ghost_ai(content: str, id_: str) -> AIMessage:
    return AIMessage(content=content, id=id_)


def _final_answer(id_: str = "final-1") -> AIMessage:
    return AIMessage(content="final answer.", id=id_)


@pytest.fixture(autouse=True)
def _ladder_on(monkeypatch):
    monkeypatch.setenv("ENSEMBLE_SYMPTOM_REPAIR_LADDER", "1")
    monkeypatch.setenv("ENSEMBLE_REPAIR_LOOP_DURABLE", "1")
    from daemon.config import _reset_symptom_repair_ladder_for_tests

    _reset_symptom_repair_ladder_for_tests()
    yield
    _reset_symptom_repair_ladder_for_tests()


@pytest.fixture(autouse=True)
def _stub_summarizer(monkeypatch):
    monkeypatch.setattr(
        SymptomRepairEngine, "_summarize", staticmethod(ok_summarizer)
    )


@pytest.mark.asyncio
async def test_d1_marker_persists_across_async_sqlite_saver_restart(
    monkeypatch, tmp_path
):
    """D-1 marker is checkpoint-persisted; a fresh saver/conn/compiled
    cycle reads the SAME marker value. Proves the marker survives
    daemon restart, revival, restore-from-checkpoint — without this
    guarantee the position-only reset defect re-opens on the next
    genuine reset attempt."""
    latch = TurnRepairLatch()
    provider = _ScriptedProvider(
        [
            _ghost_ai("Step 1:", "g1"),
            _ghost_ai("Step 2:", "g2"),
            _ghost_ai("Step 3:", "g3"),
            _final_answer("final-pre-restart"),
        ]
    )

    def _make_agent_node():
        return create_agent_node(
            llm_with_tools=provider,
            system_prompt="you are a test assistant",
            compactor=None,
            graph_ref=[None],
            config=None,
            llm_config={"model": "test-model", "model_vision": None},
            retry_config={
                "transient_attempts": 1,
                "timeout_attempts": 1,
            },
            llm_standard=None,
            injection_slot=None,
            live_hub=None,
            throttle_slot=None,
            loop_breaker_slot=None,
            loop_repairer=None,
            loop_breaker_config=LoopBreakerConfig(
                enabled=False, threshold=3
            ),
            turn_repair_latch=latch,
        )

    with _RealLangGraph():
        import aiosqlite
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        from langgraph.graph import (
            END as _REAL_END,
            START as _REAL_START,
            MessagesState as _RealMessagesState,
            StateGraph as _RealStateGraph,
        )

        # Mirror of the production SessionState fields under test:
        # repair_budget_used, pending_repair_ghost_terminal,
        # last_repair_boundary_human_id. Defined inside the swap so
        # the parent class resolves to the REAL MessagesState.
        class _State(_RealMessagesState):
            repair_budget_used: int
            pending_repair_ghost_terminal: AIMessage | None = None
            last_repair_boundary_human_id: str = ""

        db_path = tmp_path / "d1-marker-restart.db"

        # ── Phase 1: run a repair → checkpoint budget=1, marker=h-real-d
        agent_node_1 = _make_agent_node()
        ghost_node_1 = create_agent_repair_ghost_node(
            llm_config={"model": "test-model"},
            system_prompt="you are a test assistant",
            turn_repair_latch=latch,
        )
        g1 = _RealStateGraph(_State)
        g1.add_node("agent", agent_node_1)
        g1.add_node("agent_repair_ghost", ghost_node_1)
        g1.add_edge(_REAL_START, "agent")
        g1.add_conditional_edges(
            "agent",
            should_continue,
            {
                "agent": "agent",
                "agent_repair_ghost": "agent_repair_ghost",
                _REAL_END: _REAL_END,
            },
        )
        g1.add_edge("agent_repair_ghost", "agent")

        conn1 = await aiosqlite.connect(str(db_path))
        saver1 = AsyncSqliteSaver(conn1)
        await saver1.setup()
        compiled1 = g1.compile(checkpointer=saver1)
        cfg1 = {
            "configurable": {"thread_id": "d1-marker-restart-thread"},
            "recursion_limit": 60,
        }
        try:
            await compiled1.ainvoke(
                {
                    "messages": [
                        HumanMessage(content="do thing", id="h-real-d")
                    ]
                },
                cfg1,
            )
            st1 = await compiled1.aget_state(cfg1)
            pre = dict(st1.values)
        finally:
            await conn1.close()

        # ── Phase 2: fresh conn + fresh saver + fresh compiled graph,
        # SAME file, SAME thread_id — proves the marker is durable.
        agent_node_2 = _make_agent_node()
        ghost_node_2 = create_agent_repair_ghost_node(
            llm_config={"model": "test-model"},
            system_prompt="you are a test assistant",
            turn_repair_latch=latch,
        )
        g2 = _RealStateGraph(_State)
        g2.add_node("agent", agent_node_2)
        g2.add_node("agent_repair_ghost", ghost_node_2)
        g2.add_edge(_REAL_START, "agent")
        g2.add_conditional_edges(
            "agent",
            should_continue,
            {
                "agent": "agent",
                "agent_repair_ghost": "agent_repair_ghost",
                _REAL_END: _REAL_END,
            },
        )
        g2.add_edge("agent_repair_ghost", "agent")

        conn2 = await aiosqlite.connect(str(db_path))
        saver2 = AsyncSqliteSaver(conn2)
        await saver2.setup()
        compiled2 = g2.compile(checkpointer=saver2)
        cfg2 = {
            "configurable": {"thread_id": "d1-marker-restart-thread"},
            "recursion_limit": 60,
        }
        try:
            # PURE-READ — no ainvoke to avoid contaminating with a new
            # genuine-reset that would clear the marker (correct
            # behavior, but irrelevant to the durability claim).
            st2 = await compiled2.aget_state(cfg2)
            post = dict(st2.values)
        finally:
            await conn2.close()

    # ── Pre- sanity: the repair landed as expected
    assert pre.get("repair_budget_used") == 1, (
        f"Phase 1 must end with budget=1 after one repair; got "
        f"{pre.get('repair_budget_used')!r}"
    )
    assert pre.get("last_repair_boundary_human_id") == "h-real-d", (
        f"Phase 1 must stamp marker='h-real-d'; got "
        f"{pre.get('last_repair_boundary_human_id')!r}"
    )

    # ── The pin: both fields survive a full saver/conn/compiled restart
    assert post.get("repair_budget_used") == 1, (
        f"D-1 durability: budget must persist as 1 across restart; got "
        f"{post.get('repair_budget_used')!r}. Without durability, the "
        f"budget resets to 0 and the position-only reset defect class "
        f"re-opens on the next genuine-reset attempt."
    )
    assert post.get("last_repair_boundary_human_id") == "h-real-d", (
        f"D-1 durability: marker must persist as 'h-real-d' across "
        f"restart; got {post.get('last_repair_boundary_human_id')!r}. "
        f"Without durability, the marker is lost and the OQ5 reset "
        f"predicate can no longer distinguish 'retained history' from "
        f"'new episode' — the exact D-1 defect the fix closed."
    )

    # ── Sentinel: nothing in Phase 2 invoked the LLM (no provider
    # script consumed in Phase 2 — fresh provider instance).
    assert provider.call_count == 4, (
        f"Phase 1 consumed exactly 4 scripted responses "
        f"(3 ghosts + 1 final answer); Phase 2 made 0 calls "
        f"(pure-read). Total: 4. Got {provider.call_count}."
    )