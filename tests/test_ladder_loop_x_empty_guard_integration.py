"""T-1 joint integration: loop-breaker × empty-guard in ONE graph run.

The MISSING joint test (technical-analysis ground truth #9): a
loop-prone tool storm DURING an empty-degenerate provider episode, run
through a REAL langgraph superstep cycle wiring the PRODUCTION
machinery — the real ``agent_node`` closure (durable loop rung), the
real ``should_continue`` router (S5 degenerate cap), the real
``nudge_node``, and the real ``SessionState`` schema.

Acceptance (T-1):
* bounded repairs (≤ REPAIR_BUDGET=3) with durable budget carried;
* bounded S5/S1 behavior (≤ cap=3 degenerate re-invokes, then the cap
  fall-through → nudge row);
* correct class attribution in telemetry (``[SYMPTOM] class=loop`` for
  the loop rung only; the S5 cap warns under its own ``[LLM-EMPTY]``
  surface);
* NO cross-budget interference (P-9): the loop repair neither resets nor
  consumes the S5 derivation, and the S5 cap sequence never touches the
  repair budget;
* loud terminal NOT triggered while budget remains (exhaustion arms are
  pinned separately in tests/unit/test_symptom_repair_ladder.py, T-6).

Runbook: SQLite (file-backed) via the real langgraph runtime.
"""
from __future__ import annotations

import logging
import sys

import pytest
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    ToolMessage,
)

from daemon.config import (
    LoopBreakerConfig,
    _reset_symptom_repair_ladder_for_tests,
)
from daemon.graph import (
    create_agent_node,
    nudge_node,
    should_continue,
)
from daemon.services.symptom_repair_engine import (
    REPAIR_DOC_ID_PREFIX,
    SymptomRepairEngine,
)
from tests.helpers.symptom_repair import _RealLangGraph, ok_summarizer


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _ScriptedProvider:
    """LLM stub with a scripted response sequence (loop storm → empty
    storm → final answer). Counts invokes for the burn assertions."""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[list] = []

    def invoke(self, messages):
        self.calls.append(list(messages))
        if not self.script:
            raise AssertionError("provider script exhausted — unbounded run")
        return self.script.pop(0)


class _StubLoopBreakerSlot:
    def __init__(self):
        self._state: dict[str, dict] = {}
        self.record_calls: list[tuple[str, str]] = []

    def record_repair(self, instance_id, summary):
        self.record_calls.append((instance_id, summary))
        state = self._state.setdefault(instance_id, {"count": 0})
        state["count"] += 1
        return state["count"]

    def clear(self, instance_id):
        self._state.pop(instance_id, None)

    def get_repair_count(self, instance_id):
        return self._state.get(instance_id, {}).get("count", 0)


def _loop_response(seq: int) -> AIMessage:
    """A loop-storm response: identical tool call, empty content,
    reasoning-only body (degenerate-with-tool — S5's shape partition
    excludes tool_calls, P-1)."""
    return AIMessage(
        content="",
        additional_kwargs={"reasoning_content": "calling bash again"},
        tool_calls=[
            {"id": f"tc-{seq}", "name": "bash", "args": {"cmd": "ls"}}
        ],
        id=f"ai-{seq}",
    )


def _degenerate_response(seq: int) -> AIMessage:
    """An empty-storm response: reasoning-only, NO tool calls (S5's
    trailing-degenerate class)."""
    return AIMessage(
        content="",
        id=f"deg-{seq}",
        additional_kwargs={"reasoning_content": "still thinking"},
    )


def _make_agent_node(provider: _ScriptedProvider, slot: _StubLoopBreakerSlot):
    return create_agent_node(
        llm_with_tools=provider,
        system_prompt="you are a test assistant",
        compactor=None,
        graph_ref=[None],
        config=None,
        llm_config={"model": "test-model", "model_vision": None},
        retry_config={"transient_attempts": 1, "timeout_attempts": 1},
        llm_standard=None,
        injection_slot=None,
        live_hub=None,
        throttle_slot=None,
        loop_breaker_slot=slot,
        loop_repairer=None,  # durable path owns the rung under ON
        loop_breaker_config=LoopBreakerConfig(enabled=True, threshold=3),
    )


def _tools_node(state):
    """Apply every tool_call in the last AIMessage (stub tools node)."""
    last = state["messages"][-1]
    results = []
    for tc in getattr(last, "tool_calls", None) or []:
        results.append(
            ToolMessage(
                content=f"result for {tc['name']}",
                tool_call_id=tc["id"],
                name=tc["name"],
                id=f"tm-{tc['id']}",
            )
        )
    return {"messages": results}


# ---------------------------------------------------------------------------
# The joint test
# ---------------------------------------------------------------------------


@pytest.fixture
def ladder_on(monkeypatch):
    monkeypatch.setenv("ENSEMBLE_SYMPTOM_REPAIR_LADDER", "1")
    monkeypatch.setenv("ENSEMBLE_REPAIR_LOOP_DURABLE", "1")
    _reset_symptom_repair_ladder_for_tests()
    yield
    _reset_symptom_repair_ladder_for_tests()


class TestJointLoopXEmptyGuard:
    @pytest.mark.asyncio
    async def test_loop_storm_during_empty_episode_bounded_and_attributed(
        self, ladder_on, monkeypatch, caplog, tmp_path
    ):
        """ONE graph run: 3-identical-call loop storm → durable repair →
        degenerate-empty storm → S5 cap → nudge → final answer."""
        with _RealLangGraph():
            import aiosqlite
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
            from langgraph.graph import END, START, StateGraph

            # Engine summarizer stub — the surgery/budget/doc flow is real.
            monkeypatch.setattr(
                SymptomRepairEngine,
                "_summarize",
                staticmethod(ok_summarizer),
            )

            script = [
                _loop_response(0),
                _loop_response(1),
                _loop_response(2),
                # Repair fires when agent_node re-enters with 3 units;
                # the provider then degrades into the empty storm.
                _degenerate_response(0),
                _degenerate_response(1),
                _degenerate_response(2),
                AIMessage(content="final answer", id="final-1"),
            ]
            provider = _ScriptedProvider(script)
            slot = _StubLoopBreakerSlot()
            agent_node = _make_agent_node(provider, slot)

            # Schema INSIDE the swap window: daemon.graph's SessionState
            # was classed against the conftest's MOCKED langgraph at
            # import time, so the REAL StateGraph needs a schema built
            # from the REAL MessagesState (same additive-field pattern:
            # messages via add_messages + the durable budget channel).
            from langgraph.graph import MessagesState

            class _JointState(MessagesState):
                repair_budget_used: int

            g = StateGraph(_JointState)
            g.add_node("agent", agent_node)
            g.add_node("tools", _tools_node)
            g.add_node("nudge", nudge_node)
            g.add_edge(START, "agent")
            g.add_conditional_edges(
                "agent",
                should_continue,
                {
                    "tools": "tools",
                    "nudge": "nudge",
                    "agent": "agent",
                    END: END,
                },
            )
            g.add_edge("tools", "agent")
            g.add_edge("nudge", "agent")

            db_path = tmp_path / "joint_loop_empty.db"
            conn = await aiosqlite.connect(str(db_path))
            saver = AsyncSqliteSaver(conn)
            await saver.setup()
            try:
                compiled = g.compile(checkpointer=saver)
                cfg = {
                    "configurable": {"thread_id": "joint-iid"},
                    "recursion_limit": 60,
                }
                with caplog.at_level(logging.INFO):
                    await compiled.ainvoke(
                        {
                            "messages": [
                                HumanMessage(
                                    content="run the loop task", id="h1"
                                )
                            ],
                            "repair_budget_used": 0,
                        },
                        cfg,
                    )
                st = await compiled.aget_state(cfg)
                values = st.values
                final_ids = [
                    getattr(m, "id", None) for m in values["messages"]
                ]
                contents = [
                    str(getattr(m, "content", "")) for m in values["messages"]
                ]

                # ── bounded repairs (≤3): exactly ONE durable repair fired
                assert len(slot.record_calls) == 1
                assert values.get("repair_budget_used") == 1
                doc_ids = [
                    i
                    for i in final_ids
                    if str(i).startswith(
                        f"{REPAIR_DOC_ID_PREFIX}joint-iid-"
                    )
                ]
                assert len(doc_ids) == 1

                # The loop window is GONE from the checkpoint (durable
                # surgery): duplicates removed, evidence retained.
                # (ToolMessage ids in this harness: tm-<tool_call_id>.)
                assert "ai-1" not in final_ids and "tm-tc-1" not in final_ids
                assert "ai-2" not in final_ids and "tm-tc-2" not in final_ids
                assert "ai-0" in final_ids and "tm-tc-0" in final_ids

                # ── the run TERMINATED with the final answer (no wedge,
                # no loop terminal — budget remained).
                assert contents[-1] == "final answer"
                assert not any("LOOP TERMINATION" in c for c in contents)

                # ── bounded S5: exactly 3 degenerate re-invokes, then the
                # cap fall-through (the scripted degenerates 0..2 are the
                # ONLY trailing-degenerate re-invokes; the S5 cap WARN
                # fired at the cap).
                deg_ids = [i for i in final_ids if str(i).startswith("deg-")]
                assert len(deg_ids) <= 3
                warn_lines = [
                    r.getMessage()
                    for r in caplog.records
                    if "degenerate re-invoke cap hit" in r.getMessage()
                ]
                assert warn_lines, "S5 cap warning must fire at the cap"

                # ── correct class attribution + dual emit (F-3)
                symptom_lines = [
                    r.getMessage()
                    for r in caplog.records
                    if "[SYMPTOM]" in r.getMessage()
                ]
                joined = "\n".join(symptom_lines)
                assert "class=loop phase=detect action=fired" in joined
                assert "class=loop phase=repair action=fired" in joined
                assert "budget=1/3" in joined
                assert "phase=terminal" not in joined

                # ── provider burn bounded (T-9 loop arm): 7 scripted
                # calls, script not exhausted beyond the final answer.
                assert len(provider.calls) == 7

                # ── P-9 no cross-budget interference: the S5 cap sequence
                # ran AFTER the repair and the repair budget was NOT
                # consumed by the empty storm (still 1), while the repair
                # did not pre-clear the S5 derivation (the degenerate
                # storm still walked its full cap).
                assert values.get("repair_budget_used") == 1
            finally:
                await conn.close()

    @pytest.mark.asyncio
    async def test_guard_off_ladder_on_still_repairs_bounded(
        self, ladder_on, monkeypatch, caplog, tmp_path
    ):
        """Fourth 2×2 arm (review W1): ``ENSEMBLE_EMPTY_RESPONSE_GUARD=0``
        with the ladder ON. The S5 degenerate-cap machinery is disabled,
        but the DURABLE LOOP RUNG is independent of the empty-guard flag:
        (i) the S5 cap warning does NOT fire, (ii) the durable repair
        still runs, (iii) the durable budget still increments. Both
        mechanisms' independence is the partition contract (P-9).

        Runtime-flag note: the empty-guard kill-switch lives in the
        ``daemon.response_validation`` module cache (installed by
        ``load_config``) — the env var alone is inert at runtime, so this
        arm drives ``install_empty_guard_config`` directly and restores
        the documented defaults afterwards.
        """
        import daemon.response_validation as rv

        rv.install_empty_guard_config(enabled=False, compaction_skip=False)
        try:
            with _RealLangGraph():
                import aiosqlite
                from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
                from langgraph.graph import END, START, MessagesState, StateGraph

                monkeypatch.setattr(
                    SymptomRepairEngine,
                    "_summarize",
                    staticmethod(ok_summarizer),
                )

                script = [
                    _loop_response(0),
                    _loop_response(1),
                    _loop_response(2),
                    # Repair fires on the 4th agent entry; the provider
                    # then degrades into UNGUARDED degenerate re-invokes
                    # (guard OFF ⇒ no S5 cap, no cap fall-through nudge).
                    _degenerate_response(0),
                    _degenerate_response(1),
                    _degenerate_response(2),
                    AIMessage(content="final answer", id="final-1"),
                ]
                provider = _ScriptedProvider(script)
                slot = _StubLoopBreakerSlot()
                agent_node = _make_agent_node(provider, slot)

                from langgraph.graph import MessagesState

                class _JointState(MessagesState):
                    repair_budget_used: int

                g = StateGraph(_JointState)
                g.add_node("agent", agent_node)
                g.add_node("tools", _tools_node)
                g.add_node("nudge", nudge_node)
                g.add_edge(START, "agent")
                g.add_conditional_edges(
                    "agent",
                    should_continue,
                    {
                        "tools": "tools",
                        "nudge": "nudge",
                        "agent": "agent",
                        END: END,
                    },
                )
                g.add_edge("tools", "agent")
                g.add_edge("nudge", "agent")

                db_path = tmp_path / "joint_guard_off.db"
                conn = await aiosqlite.connect(str(db_path))
                saver = AsyncSqliteSaver(conn)
                await saver.setup()
                try:
                    compiled = g.compile(checkpointer=saver)
                    cfg = {
                        "configurable": {"thread_id": "joint-guard-off"},
                        "recursion_limit": 60,
                    }
                    with caplog.at_level(logging.INFO):
                        await compiled.ainvoke(
                            {
                                "messages": [
                                    HumanMessage(
                                        content="run the loop task", id="h1"
                                    )
                                ],
                                "repair_budget_used": 0,
                            },
                            cfg,
                        )
                    st = await compiled.aget_state(cfg)
                    values = st.values
                    final_ids = [
                        getattr(m, "id", None) for m in values["messages"]
                    ]

                    # (i) S5 cap warning does NOT fire with the guard OFF —
                    # the derived trailing-degenerate counter is pinned to
                    # 0, so the cap branch is unreachable.
                    cap_warns = [
                        r.getMessage()
                        for r in caplog.records
                        if "degenerate re-invoke cap hit" in r.getMessage()
                    ]
                    assert cap_warns == [], (
                        "S5 cap warning must NOT fire with the empty-guard "
                        f"OFF; got {cap_warns!r}"
                    )

                    # (ii) the durable repair STILL runs — the loop rung
                    # does not depend on the empty-guard flag.
                    assert len(slot.record_calls) == 1
                    doc_ids = [
                        i
                        for i in final_ids
                        if str(i).startswith(
                            f"{REPAIR_DOC_ID_PREFIX}joint-guard-off-"
                        )
                    ]
                    assert len(doc_ids) == 1
                    assert "ai-1" not in final_ids and "ai-2" not in final_ids
                    assert "ai-0" in final_ids  # evidence retained

                    # (iii) the durable budget STILL increments.
                    assert values.get("repair_budget_used") == 1

                    # The run completes; no loop terminal, no wedge.
                    assert values["messages"][-1].content == "final answer"
                    symptom_lines = [
                        r.getMessage()
                        for r in caplog.records
                        if "[SYMPTOM]" in r.getMessage()
                    ]
                    assert not any(
                        "phase=terminal" in l for l in symptom_lines
                    )
                finally:
                    await conn.close()
        finally:
            rv._reset_empty_guard_config_for_tests()

    @pytest.mark.asyncio
    async def test_flags_off_preserves_shipped_routing_in_joint_run(
        self, monkeypatch, tmp_path
    ):
        """T-8 joint arm: with BOTH kill-switches OFF the same storm runs
        the SHIPPED transient path — no repair doc, no durable budget,
        loop repair via the shipped in-memory filter (no checkpoint
        surgery)."""
        monkeypatch.setenv("ENSEMBLE_SYMPTOM_REPAIR_LADDER", "0")
        monkeypatch.setenv("ENSEMBLE_REPAIR_LOOP_DURABLE", "0")
        _reset_symptom_repair_ladder_for_tests()
        try:
            with _RealLangGraph():
                import aiosqlite
                from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
                from langgraph.graph import END, START, StateGraph

                # Shipped summarizer path would call the LLM — the shipped
                # LoopRepairer builds its own ThinkingChatOpenAI; stub it
                # at the class level to return a usable summary offline.
                from unittest.mock import patch

                class _FakeLLM:
                    def invoke(self, messages):
                        return AIMessage(content="offline summary")

                script = [
                    _loop_response(0),
                    _loop_response(1),
                    _loop_response(2),
                    AIMessage(content="recovered answer", id="rec-1"),
                ]
                provider = _ScriptedProvider(script)
                slot = _StubLoopBreakerSlot()
                agent_node = _make_agent_node(provider, slot)

                from langgraph.graph import MessagesState

                class _JointState(MessagesState):
                    repair_budget_used: int

                g = StateGraph(_JointState)
                g.add_node("agent", agent_node)
                g.add_node("tools", _tools_node)
                g.add_node("nudge", nudge_node)
                g.add_edge(START, "agent")
                g.add_conditional_edges(
                    "agent",
                    should_continue,
                    {
                        "tools": "tools",
                        "nudge": "nudge",
                        "agent": "agent",
                        END: END,
                    },
                )
                g.add_edge("tools", "agent")
                g.add_edge("nudge", "agent")

                db_path = tmp_path / "joint_off.db"
                conn = await aiosqlite.connect(str(db_path))
                saver = AsyncSqliteSaver(conn)
                await saver.setup()
                try:
                    compiled = g.compile(checkpointer=saver)
                    cfg = {
                        "configurable": {"thread_id": "joint-off"},
                        "recursion_limit": 60,
                    }
                    with patch(
                        "daemon.graph.ThinkingChatOpenAI", _FakeLLM
                    ):
                        await compiled.ainvoke(
                            {
                                "messages": [
                                    HumanMessage(
                                        content="run the loop task", id="h1"
                                    )
                                ],
                                "repair_budget_used": 0,
                            },
                            cfg,
                        )
                    st = await compiled.aget_state(cfg)
                    values = st.values
                    final_ids = [
                        getattr(m, "id", None) for m in values["messages"]
                    ]
                    # OFF: NO checkpoint surgery — the loop messages REMAIN
                    # in the channel (the shipped repair is transient,
                    # LLM-call-scoped only), no repair doc, budget 0.
                    assert "ai-1" in final_ids and "tm-tc-1" in final_ids
                    assert not any(
                        str(i).startswith(REPAIR_DOC_ID_PREFIX)
                        for i in final_ids
                    )
                    assert values.get("repair_budget_used", 0) == 0
                    # The turn still completed through the shipped path.
                    assert values["messages"][-1].content == "recovered answer"
                finally:
                    await conn.close()
        finally:
            _reset_symptom_repair_ladder_for_tests()
