"""Ladder phase 2 — D-1 boundary-freshness + B-4 turn latch + B-6 tokens.

Defect D-1 (tester-found, 🔴): the ghost rung's fully-trailing removal
window leaves the ORIGINAL HumanMessage at ``messages[-1]`` after the
surgery; the OQ5 boundary predicate (position-only tail check) then read
that retained human as a "new turn" and reset ``repair_budget_used``
1→0 MID-TURN — budget exhaustion became unreachable in natural runs
(12-ghost storm: 13 LLM calls, 4 repairs, no terminal, uncaught
recursion_limit bound; the C1 carrier + ``[GHOST TERMINATION]`` were
dead code).

Fix under test — boundary-id-freshness: every successful durable repair
stamps ``last_repair_boundary_human_id`` (the id of the boundary human
its surgery RETAINED, via ``SymptomRepairOutcome.boundary_human_id``);
the OQ5 reset (``_repair_boundary_reset_state``) fires only when the
tail human's id DIFFERS from the marker. Same id ⇒ retained history
(suppress); different/absent id ⇒ genuinely new episode (reset + marker
clear + B-4 latch release).

B-4 (spec item): repair-once-per-turn RAM latch (``TurnRepairLatch``),
keyed per (instance, class) — at most ONE durable repair per class per
turn; cross-class same-turn sequences keep firing (P-9 preserved: the
latch caps each class, the durable budget caps the episode). A second
same-class cap-hit in one turn escalates through the SAME loud-terminal
carrier (ghost) / loud-ERROR fall-through (pre-terminal) / shipped
post-cap continue (loop).

B-6: every ``[SYMPTOM]`` line carries its REAL class token
(``class=ghost|truncated|empty_post_ladder|loop``) — pinned here on
live real-graph paths per class.

All scenarios run the REAL production nodes (``create_agent_node``,
``create_agent_repair_ghost_node``, ``should_continue``, durable loop
rung, pre-terminal intercept) over a REAL langgraph superstep cycle with
REAL ``AsyncSqliteSaver`` checkpointing; only the LLM-invoke seam and
the engine summarizer are stubbed (mirrors the routed-gap harnesses).
"""
from __future__ import annotations

import logging

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from daemon.config import (
    LoopBreakerConfig,
    _reset_symptom_repair_ladder_for_tests,
)
from daemon.graph import (
    TurnRepairLatch,
    create_agent_node,
    create_agent_repair_ghost_node,
    nudge_node,
    should_continue,
)
from daemon.response_validation import (
    EmptyLLMResponseError,
    LLMResponseValidationError,
)
from daemon.graph import (
    GhostDetectionResult,
    _collect_trailing_ghost_promise_ai_messages,
)
from daemon.services.symptom_repair_engine import (
    REPAIR_DOC_ID_PREFIX,
    SymptomRepairContext,
    SymptomRepairEngine,
)
from tests.helpers.symptom_repair import _RealLangGraph, ok_summarizer


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _ScriptedProvider:
    """LLM stub with a scripted response sequence."""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[list] = []

    def invoke(self, messages):
        self.calls.append(list(messages))
        if not self.script:
            raise AssertionError("provider script exhausted — unbounded run")
        return self.script.pop(0)


class _RaiseOnceProvider:
    """Raises ``exc_factory(1)`` on the FIRST invoke, then serves the
    script (the pre-terminal intercept's re-invocation)."""

    def __init__(self, exc_factory, script):
        self._exc_factory = exc_factory
        self.script = list(script)
        self.calls: list[list] = []
        self._raised = False

    def invoke(self, messages):
        self.calls.append(list(messages))
        if not self._raised:
            self._raised = True
            raise self._exc_factory(1)
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


def _ghost_ai(content: str, id_: str) -> AIMessage:
    """Colon-ending plain AIMessage — the ghost-promise signature."""
    return AIMessage(content=content, id=id_)


def _loop_response(seq: int) -> AIMessage:
    """Identical tool-call storm unit (the loop class's evidence)."""
    return AIMessage(
        content="",
        additional_kwargs={"reasoning_content": "calling bash again"},
        tool_calls=[
            {"id": f"tc-{seq}", "name": "bash", "args": {"cmd": "ls"}}
        ],
        id=f"ai-{seq}",
    )


def _final_answer(id_: str = "final-1") -> AIMessage:
    return AIMessage(content="final answer.", id=id_)


def _tools_node(state):
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


def _truncated_exception(seq: int) -> LLMResponseValidationError:
    return LLMResponseValidationError(
        "truncated response",
        response=AIMessage(
            content="partial answer that hit the token limit",
            response_metadata={"finish_reason": "length"},
            id=f"trunc-{seq}",
        ),
    )


def _empty_exception(seq: int) -> EmptyLLMResponseError:
    return EmptyLLMResponseError(
        "LLM returned an empty response with no tool calls and no "
        "reasoning content"
    )


def _reset_flags():
    _reset_symptom_repair_ladder_for_tests()


@pytest.fixture(autouse=True)
def _ladder_on(monkeypatch):
    """Master + loop-durable ON for every test in this module."""
    monkeypatch.setenv("ENSEMBLE_SYMPTOM_REPAIR_LADDER", "1")
    monkeypatch.setenv("ENSEMBLE_REPAIR_LOOP_DURABLE", "1")
    _reset_flags()
    yield
    _reset_flags()


@pytest.fixture(autouse=True)
def _stub_summarizer(monkeypatch):
    monkeypatch.setattr(
        SymptomRepairEngine, "_summarize", staticmethod(ok_summarizer)
    )


def _symptom_lines(caplog) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if "[SYMPTOM]" in r.getMessage()
    ]


def _doc_ids(values) -> list[str]:
    return [
        i
        for i in (getattr(m, "id", None) for m in values["messages"])
        if isinstance(i, str) and i.startswith(REPAIR_DOC_ID_PREFIX)
    ]


# ---------------------------------------------------------------------------
# Harnesses
# ---------------------------------------------------------------------------


def _make_ghost_pair(provider, latch: TurnRepairLatch | None):
    """``(agent_node, ghost_node)`` mirroring the production wiring."""
    agent_node = create_agent_node(
        llm_with_tools=provider,
        system_prompt="you are a test assistant",
        compactor=None,
        graph_ref=[None],
        config=None,
        llm_config={"model": "test-model"},
        retry_config={"transient_attempts": 1, "timeout_attempts": 1},
        turn_repair_latch=latch,
    )
    ghost_node = create_agent_repair_ghost_node(
        llm_config={"model": "test-model"},
        system_prompt="you are a test assistant",
        turn_repair_latch=latch,
    )
    return agent_node, ghost_node


async def _run_ghost_graph(
    compile_graph, tmp_path, thread_id, first_input, cfg_extra=None
):
    """Compile + run the agent/ghost graph; returns (compiled, cfg)."""
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from langgraph.graph import (
        END as _REAL_END,
        START as _REAL_START,
        MessagesState as _RealMessagesState,
        StateGraph as _RealStateGraph,
    )

    globals()["END"] = _REAL_END
    globals()["START"] = _REAL_START
    globals()["MessagesState"] = _RealMessagesState
    globals()["StateGraph"] = _RealStateGraph

    class _State(_RealMessagesState):
        repair_budget_used: int
        pending_repair_ghost_terminal: AIMessage | None = None
        last_repair_boundary_human_id: str = ""

    g = _RealStateGraph(_State)
    compile_graph(g)
    g.add_edge(_REAL_START, "agent")
    g.add_conditional_edges(
        "agent",
        should_continue,
        {
            "agent": "agent",
            "agent_repair_ghost": "agent_repair_ghost",
            _REAL_END: _REAL_END,
        },
    )
    g.add_edge("agent_repair_ghost", "agent")

    db_path = tmp_path / f"{thread_id}.db"
    conn = await aiosqlite.connect(str(db_path))
    saver = AsyncSqliteSaver(conn)
    await saver.setup()
    compiled = g.compile(checkpointer=saver)
    cfg = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": 60,
    }
    if cfg_extra:
        cfg.update(cfg_extra)
    return compiled, cfg, conn, first_input


# ---------------------------------------------------------------------------
# D-1 — boundary-id-freshness
# ---------------------------------------------------------------------------


class TestEngineStampsBoundaryHumanId:
    @pytest.mark.asyncio
    async def test_ghost_and_loop_outcomes_carry_boundary_human_id(self):
        """Engine-level pin: every successful repair outcome carries the
        id of the last real (non-injected) human in the PRE-surgery
        history — the marker source the OQ5 predicate suppresses on."""
        engine = SymptomRepairEngine()

        ghost_msgs = [
            HumanMessage(content="do thing", id="h1"),
            _ghost_ai("Step 1:", "g1"),
            _ghost_ai("Step 2:", "g2"),
            _ghost_ai("Step 3:", "g3"),
        ]
        outcome = await engine.repair(
            SymptomRepairContext(
                detection=GhostDetectionResult(
                    ghost_messages=_collect_trailing_ghost_promise_ai_messages(
                        ghost_msgs
                    ),
                    trailing_count=3,
                ),
                messages=list(ghost_msgs),
                llm_config={"model": "m"},
                system_prompt="sp",
                instance_id="iid-stamp",
                budget_used=0,
            ),
            symptom_class="ghost",
        )
        assert outcome.success
        assert outcome.boundary_human_id == "h1"

    def test_last_real_human_id_skips_injected_and_ai(self):
        from daemon.services.symptom_repair_engine import (
            _last_real_human_id,
        )

        injected = HumanMessage(
            content="injected",
            id="inj-1",
            additional_kwargs={"injected_message": True},
        )
        msgs = [
            HumanMessage(content="real", id="h-real"),
            injected,
            _ghost_ai("Step:", "g1"),
        ]
        assert _last_real_human_id(msgs) == "h-real"
        assert _last_real_human_id([_ghost_ai("x:", "a")]) == ""


class TestD1GhostBudgetSurvivesReentry:
    @pytest.mark.asyncio
    async def test_budget_survives_mid_turn_reentry_on_retained_human(
        self, monkeypatch, caplog, tmp_path
    ):
        """THE D-1 regression pin (natural run, NO budget pre-seed):
        ghost storm → repair (budget 1) → ``agent`` re-entry sees the
        RETAINED real human at the tail — the pre-fix code reset the
        budget 1→0 here; the marker now suppresses it. Budget must be 1
        at the end with NO mid-turn ``budget reset`` line."""
        latch = TurnRepairLatch()
        provider = _ScriptedProvider(
            [
                _ghost_ai("Step 1:", "g1"),
                _ghost_ai("Step 2:", "g2"),
                _ghost_ai("Step 3:", "g3"),
                _final_answer(),
            ]
        )

        def _compile(g):
            agent_node, ghost_node = _make_ghost_pair(provider, latch)
            g.add_node("agent", agent_node)
            g.add_node("agent_repair_ghost", ghost_node)

        with _RealLangGraph():
            compiled, cfg, conn, first_input = await _run_ghost_graph(
                _compile,
                tmp_path,
                "d1-survive",
                {
                    "messages": [HumanMessage(content="do thing", id="h1")],
                    "repair_budget_used": 0,
                    "pending_repair_ghost_terminal": None,
                    "last_repair_boundary_human_id": "",
                },
            )
            try:
                with caplog.at_level(logging.INFO):
                    await compiled.ainvoke(first_input, cfg)
                st = await compiled.aget_state(cfg)
                values = st.values

                # D-1 pin: the increment SURVIVED the re-entry.
                assert values.get("repair_budget_used") == 1, (
                    f"D-1: budget must survive the ghost-surgery re-entry "
                    f"(pre-fix the retained human tail reset it to 0); "
                    f"got {values.get('repair_budget_used')!r}"
                )
                # No mid-turn reset line.
                reset_lines = [
                    ln
                    for ln in _symptom_lines(caplog)
                    if "budget reset" in ln
                ]
                assert reset_lines == [], (
                    f"no budget reset may fire mid-turn; got {reset_lines!r}"
                )
                # Exactly one repair doc; turn ended with the answer.
                assert len(_doc_ids(values)) == 1
                assert values["messages"][-1].content == "final answer."
                assert len(provider.calls) == 4
                # B-6 pin: the ghost lines carry the REAL class token.
                joined = "\n".join(_symptom_lines(caplog))
                assert "class=ghost phase=detect action=fired" in joined
                assert "class=ghost phase=repair action=fired" in joined
                assert "budget=1/3" in joined
            finally:
                await conn.close()

    @pytest.mark.asyncio
    async def test_natural_ghost_storm_reaches_loud_terminal_within_bounds(
        self, monkeypatch, caplog, tmp_path
    ):
        """D-1(i) — the test class that would have caught the defect:
        a LONG ghost storm with NO budget pre-seeding must reach a LOUD
        terminal within bounded resources. Storm #1 → repair (budget 1);
        storm #2 → B-4 turn latch escalates through the C1 carrier →
        ``[GHOST TERMINATION]`` → END. Pre-fix this run NEVER terminated
        (budget pinned at 0 by the mid-turn reset, no latch)."""
        latch = TurnRepairLatch()
        provider = _ScriptedProvider(
            [
                _ghost_ai("Step 1:", "g1"),
                _ghost_ai("Step 2:", "g2"),
                _ghost_ai("Step 3:", "g3"),
                _ghost_ai("Step 4:", "g4"),
                _ghost_ai("Step 5:", "g5"),
                _ghost_ai("Step 6:", "g6"),
            ]
        )

        def _compile(g):
            agent_node, ghost_node = _make_ghost_pair(provider, latch)
            g.add_node("agent", agent_node)
            g.add_node("agent_repair_ghost", ghost_node)

        with _RealLangGraph():
            compiled, cfg, conn, first_input = await _run_ghost_graph(
                _compile,
                tmp_path,
                "d1-natural-terminal",
                {
                    "messages": [HumanMessage(content="do thing", id="h1")],
                    "repair_budget_used": 0,
                    "pending_repair_ghost_terminal": None,
                    "last_repair_boundary_human_id": "",
                },
            )
            try:
                with caplog.at_level(logging.INFO):
                    await compiled.ainvoke(first_input, cfg)
                st = await compiled.aget_state(cfg)
                values = st.values

                # Bounded: exactly the 6 scripted ghost calls — no burn
                # beyond the second cap-hit.
                assert len(provider.calls) == 6

                # Exactly ONE repair fired (B-4: ≤1 per class per turn);
                # the SECOND storm was latch-escalated, not repaired.
                ghost_docs = [
                    i for i in _doc_ids(values) if "d1-natural" in i
                ]
                assert len(ghost_docs) == 1
                assert values.get("repair_budget_used") == 1

                # Loud terminal: carrier consumed → terminal substituted
                # as the final visible message.
                assert values.get("pending_repair_ghost_terminal") is None
                last = values["messages"][-1]
                assert isinstance(last, AIMessage)
                assert last.id.startswith("repair-terminal-ghost-")
                assert "[GHOST TERMINATION]" in last.content

                term_warns = [
                    r.getMessage()
                    for r in caplog.records
                    if "[GHOST TERMINATION]" in r.getMessage()
                ]
                assert len(term_warns) == 1

                # Latch escalation telemetry — real class token (B-6).
                joined = "\n".join(_symptom_lines(caplog))
                assert (
                    "class=ghost phase=terminal action=escalate" in joined
                )
                assert "repair-once-per-turn-latch" in joined
                # And STILL no mid-turn budget reset (D-1).
                assert not [
                    ln
                    for ln in _symptom_lines(caplog)
                    if "budget reset" in ln
                ]
            finally:
                await conn.close()

    @pytest.mark.asyncio
    async def test_turn_latch_and_budget_reset_on_genuine_new_turn(
        self, monkeypatch, caplog, tmp_path
    ):
        """D-1 × B-4 composition: a genuinely NEW turn (new real human,
        different id) DOES reset the budget, clears the marker, releases
        the latch — and the ghost class can repair again next turn."""
        latch = TurnRepairLatch()
        provider = _ScriptedProvider(
            [
                # Turn 1: storm → repair → recovered.
                _ghost_ai("Step 1:", "g1"),
                _ghost_ai("Step 2:", "g2"),
                _ghost_ai("Step 3:", "g3"),
                _final_answer("final-t1"),
                # Turn 2: storm again → repair fires AGAIN (latch was
                # released at the turn boundary).
                _ghost_ai("Step 4:", "g4"),
                _ghost_ai("Step 5:", "g5"),
                _ghost_ai("Step 6:", "g6"),
                _final_answer("final-t2"),
            ]
        )

        def _compile(g):
            agent_node, ghost_node = _make_ghost_pair(provider, latch)
            g.add_node("agent", agent_node)
            g.add_node("agent_repair_ghost", ghost_node)

        with _RealLangGraph():
            compiled, cfg, conn, first_input = await _run_ghost_graph(
                _compile,
                tmp_path,
                "d1-new-turn",
                {
                    "messages": [HumanMessage(content="do thing", id="h1")],
                    "repair_budget_used": 0,
                    "pending_repair_ghost_terminal": None,
                    "last_repair_boundary_human_id": "",
                },
            )
            try:
                with caplog.at_level(logging.INFO):
                    await compiled.ainvoke(first_input, cfg)
                    # ── Turn 2: a genuinely new human episode.
                    await compiled.ainvoke(
                        {
                            "messages": [
                                HumanMessage(content="again", id="h2")
                            ]
                        },
                        cfg,
                    )
                st = await compiled.aget_state(cfg)
                values = st.values

                # Turn-boundary reset fired between the turns...
                reset_lines = [
                    ln
                    for ln in _symptom_lines(caplog)
                    if "budget reset" in ln
                ]
                assert len(reset_lines) == 1, reset_lines
                assert "(was 1)" in reset_lines[0]
                # ...the latch was released: turn 2's ghost repair FIRED
                # (a set latch would have escalated instead).
                assert len(_doc_ids(values)) == 2
                assert values.get("repair_budget_used") == 1
                assert values["messages"][-1].content == "final answer."
                assert len(provider.calls) == 8
            finally:
                await conn.close()

    async def test_genuine_reset_clears_marker_and_budget_stays_reset(
        self, monkeypatch, caplog, tmp_path
    ):
        """COVERAGE-GAP PIN (re-gate mutation-ii, 2026-09-14): a genuine
        new-turn boundary must CLEAR ``last_repair_boundary_human_id``
        (the clear rides the SAME node return as the budget reset) —
        the stale-marker mutation (marker never cleared on genuine
        reset) passed the ENTIRE ladder family unnoticed. Also pins the
        repair STAMP itself: after a durable ghost repair the marker
        carries the id of the boundary human the surgery retained."""
        latch = TurnRepairLatch()
        provider = _ScriptedProvider(
            [
                # Turn 1: storm → durable ghost repair (marker stamped
                # with the retained boundary human "h1") → final answer.
                _ghost_ai("Step 1:", "g1"),
                _ghost_ai("Step 2:", "g2"),
                _ghost_ai("Step 3:", "g3"),
                _final_answer("final-t1"),
                # Turn 2: a genuinely NEW human episode, CLEAN turn —
                # no symptom, no repair, so the marker must stay CLEAR
                # (reset-cleared at entry, never re-stamped).
                _final_answer("final-t2"),
            ]
        )

        def _compile(g):
            agent_node, ghost_node = _make_ghost_pair(provider, latch)
            g.add_node("agent", agent_node)
            g.add_node("agent_repair_ghost", ghost_node)

        with _RealLangGraph():
            compiled, cfg, conn, first_input = await _run_ghost_graph(
                _compile,
                tmp_path,
                "d1-marker-clear",
                {
                    "messages": [HumanMessage(content="do thing", id="h1")],
                    "repair_budget_used": 0,
                    "pending_repair_ghost_terminal": None,
                    "last_repair_boundary_human_id": "",
                },
            )
            try:
                with caplog.at_level(logging.INFO):
                    await compiled.ainvoke(first_input, cfg)
                st = await compiled.aget_state(cfg)
                values = st.values

                # Turn 1: the durable repair STAMPED the marker with the
                # boundary human its surgery retained.
                assert len(_doc_ids(values)) == 1
                assert (
                    values["last_repair_boundary_human_id"] == "h1"
                ), values.get("last_repair_boundary_human_id")
                assert values.get("repair_budget_used") == 1

                # ── Turn 2: genuinely new human (different id).
                with caplog.at_level(logging.INFO):
                    await compiled.ainvoke(
                        {
                            "messages": [
                                HumanMessage(content="again", id="h2")
                            ]
                        },
                        cfg,
                    )
                st = await compiled.aget_state(cfg)
                values = st.values

                # Genuine reset fired (budget 1→0) AND the marker clear
                # rode the SAME node return — the marker must be EMPTY
                # after a clean new turn (never re-stamped: no repair).
                reset_lines = [
                    ln
                    for ln in _symptom_lines(caplog)
                    if "budget reset" in ln
                ]
                assert len(reset_lines) == 1, reset_lines
                assert "(was 1)" in reset_lines[0]
                assert values.get("repair_budget_used") == 0
                assert (
                    values["last_repair_boundary_human_id"] == ""
                ), values.get("last_repair_boundary_human_id")
                assert len(_doc_ids(values)) == 1  # no turn-2 repair
            finally:
                await conn.close()


# ---------------------------------------------------------------------------
# B-4 — per-class-per-turn latch (P-9 composition preserved)
# ---------------------------------------------------------------------------


class TestTurnRepairLatch:
    def test_latch_is_per_instance_per_class(self):
        latch = TurnRepairLatch()
        latch.mark("i", "ghost")
        assert latch.is_set("i", "ghost")
        assert not latch.is_set("i", "loop")
        assert not latch.is_set("j", "ghost")
        latch.clear_instance("i")
        assert not latch.is_set("i", "ghost")

    @pytest.mark.asyncio
    async def test_cross_class_same_turn_both_repairs_fire_p9(
        self, monkeypatch, caplog, tmp_path
    ):
        """P-9 composition pin: a loop repair AND a ghost repair in the
        SAME turn BOTH fire (per-class latch keys, shared durable
        budget: loop 0→1, ghost 1→2) — the latch caps each class, not
        the turn total."""
        latch = TurnRepairLatch()
        slot = _StubLoopBreakerSlot()
        provider = _ScriptedProvider(
            [
                _loop_response(0),
                _loop_response(1),
                _loop_response(2),
                # Loop repair fires in-node on the 3-unit re-entry; the
                # provider then degrades into a ghost storm.
                _ghost_ai("Step 1:", "g1"),
                _ghost_ai("Step 2:", "g2"),
                _ghost_ai("Step 3:", "g3"),
                _final_answer(),
            ]
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
            globals()["END"] = _REAL_END
            globals()["START"] = _REAL_START
            globals()["MessagesState"] = _RealMessagesState
            globals()["StateGraph"] = _RealStateGraph

            agent_node = create_agent_node(
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
                loop_repairer=None,
                loop_breaker_config=LoopBreakerConfig(
                    enabled=True, threshold=3
                ),
                turn_repair_latch=latch,
            )
            ghost_node = create_agent_repair_ghost_node(
                llm_config={"model": "test-model"},
                system_prompt="you are a test assistant",
                turn_repair_latch=latch,
            )

            class _State(_RealMessagesState):
                repair_budget_used: int
                pending_repair_ghost_terminal: AIMessage | None = None
                last_repair_boundary_human_id: str = ""

            g = _RealStateGraph(_State)
            g.add_node("agent", agent_node)
            g.add_node("tools", _tools_node)
            g.add_node("nudge", nudge_node)
            g.add_node("agent_repair_ghost", ghost_node)
            g.add_edge(_REAL_START, "agent")
            g.add_conditional_edges(
                "agent",
                should_continue,
                {
                    "tools": "tools",
                    "nudge": "nudge",
                    "agent": "agent",
                    "agent_repair_ghost": "agent_repair_ghost",
                    _REAL_END: _REAL_END,
                },
            )
            g.add_edge("tools", "agent")
            g.add_edge("nudge", "agent")
            g.add_edge("agent_repair_ghost", "agent")

            db_path = tmp_path / "p9-cross-class.db"
            conn = await aiosqlite.connect(str(db_path))
            saver = AsyncSqliteSaver(conn)
            await saver.setup()
            try:
                compiled = g.compile(checkpointer=saver)
                cfg = {
                    "configurable": {"thread_id": "p9-iid"},
                    "recursion_limit": 60,
                }
                with caplog.at_level(logging.INFO):
                    await compiled.ainvoke(
                        {
                            "messages": [
                                HumanMessage(content="do thing", id="h1")
                            ],
                            "repair_budget_used": 0,
                            "pending_repair_ghost_terminal": None,
                            "last_repair_boundary_human_id": "",
                        },
                        cfg,
                    )
                st = await compiled.aget_state(cfg)
                values = st.values

                # BOTH repairs fired: loop (+1) then ghost (+1) — no
                # latch theft across classes; the shared budget is 2.
                assert values.get("repair_budget_used") == 2, (
                    f"P-9: loop + ghost repairs in one turn must both "
                    f"fire (per-class latch); budget "
                    f"{values.get('repair_budget_used')!r}"
                )
                assert len(_doc_ids(values)) == 2
                assert values["messages"][-1].content == "final answer."
                # Per-class attribution (B-6): loop lines vs ghost lines.
                joined = "\n".join(_symptom_lines(caplog))
                assert "class=loop phase=detect action=fired" in joined
                assert "class=loop phase=repair action=fired" in joined
                assert "class=ghost phase=detect action=fired" in joined
                assert "class=ghost phase=repair action=fired" in joined
                assert "budget=2/3" in joined
                assert len(provider.calls) == 7
            finally:
                await conn.close()


# ---------------------------------------------------------------------------
# D-1(ii) — loop / truncated / empty immunity pins
# ---------------------------------------------------------------------------


class TestNonGhostClassImmunity:
    @pytest.mark.asyncio
    async def test_loop_repair_budget_survives_turn(
        self, monkeypatch, caplog, tmp_path
    ):
        """Loop immunity: the durable loop rung repairs IN-NODE (surgery
        + fresh response ride the SAME agent_node return), so no
        boundary reset can fire mid-turn. Budget must be 1 at the end
        with NO reset line (class=loop telemetry pinned — B-6)."""
        latch = TurnRepairLatch()
        slot = _StubLoopBreakerSlot()
        provider = _ScriptedProvider(
            [
                _loop_response(0),
                _loop_response(1),
                _loop_response(2),
                _final_answer(),
            ]
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
            globals()["END"] = _REAL_END
            globals()["START"] = _REAL_START
            globals()["MessagesState"] = _RealMessagesState
            globals()["StateGraph"] = _RealStateGraph

            agent_node = create_agent_node(
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
                loop_breaker_slot=slot,
                loop_repairer=None,
                loop_breaker_config=LoopBreakerConfig(
                    enabled=True, threshold=3
                ),
                turn_repair_latch=latch,
            )

            class _State(_RealMessagesState):
                repair_budget_used: int
                pending_repair_ghost_terminal: AIMessage | None = None
                last_repair_boundary_human_id: str = ""

            g = _RealStateGraph(_State)
            g.add_node("agent", agent_node)
            g.add_node("tools", _tools_node)
            g.add_node("nudge", nudge_node)
            g.add_edge(_REAL_START, "agent")
            g.add_conditional_edges(
                "agent",
                should_continue,
                {
                    "tools": "tools",
                    "nudge": "nudge",
                    "agent": "agent",
                    _REAL_END: _REAL_END,
                },
            )
            g.add_edge("tools", "agent")
            g.add_edge("nudge", "agent")

            db_path = tmp_path / "loop-immune.db"
            conn = await aiosqlite.connect(str(db_path))
            saver = AsyncSqliteSaver(conn)
            await saver.setup()
            try:
                compiled = g.compile(checkpointer=saver)
                cfg = {
                    "configurable": {"thread_id": "loop-iid"},
                    "recursion_limit": 60,
                }
                with caplog.at_level(logging.INFO):
                    await compiled.ainvoke(
                        {
                            "messages": [
                                HumanMessage(content="do thing", id="h1")
                            ],
                            "repair_budget_used": 0,
                            "pending_repair_ghost_terminal": None,
                            "last_repair_boundary_human_id": "",
                        },
                        cfg,
                    )
                st = await compiled.aget_state(cfg)
                values = st.values
                assert values.get("repair_budget_used") == 1
                assert not [
                    ln
                    for ln in _symptom_lines(caplog)
                    if "budget reset" in ln
                ]
                assert values["messages"][-1].content == "final answer."
                joined = "\n".join(_symptom_lines(caplog))
                assert "class=loop phase=detect action=fired" in joined
                assert "class=loop phase=repair action=fired" in joined
                assert len(provider.calls) == 4
            finally:
                await conn.close()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "exc_factory,expected_class",
        [
            (_truncated_exception, "truncated"),
            (_empty_exception, "empty_post_ladder"),
        ],
    )
    async def test_preterminal_repair_budget_survives_turn(
        self, exc_factory, expected_class, monkeypatch, caplog, tmp_path
    ):
        """Truncated / empty_post_ladder immunity: the pre-terminal
        intercept repairs IN-NODE (surgery + re-invoked response ride
        the SAME return), so no boundary reset can fire. Also pins the
        B-6 per-class token on BOTH pre-terminal classes."""
        latch = TurnRepairLatch()
        provider = _RaiseOnceProvider(exc_factory, [_final_answer()])
        with _RealLangGraph():
            import aiosqlite
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
            from langgraph.graph import (
                END as _REAL_END,
                START as _REAL_START,
                MessagesState as _RealMessagesState,
                StateGraph as _RealStateGraph,
            )
            globals()["END"] = _REAL_END
            globals()["START"] = _REAL_START
            globals()["MessagesState"] = _RealMessagesState
            globals()["StateGraph"] = _RealStateGraph

            agent_node = create_agent_node(
                llm_with_tools=provider,
                system_prompt="you are a test assistant",
                compactor=None,
                graph_ref=[None],
                config=None,
                llm_config={"model": "test-model"},
                retry_config={"transient_attempts": 1, "timeout_attempts": 1},
                turn_repair_latch=latch,
            )

            class _State(_RealMessagesState):
                repair_budget_used: int
                pending_repair_ghost_terminal: AIMessage | None = None
                last_repair_boundary_human_id: str = ""

            g = _RealStateGraph(_State)
            g.add_node("agent", agent_node)
            g.add_edge(_REAL_START, "agent")
            g.add_conditional_edges(
                "agent",
                should_continue,
                {"agent": "agent", _REAL_END: _REAL_END},
            )

            db_path = tmp_path / f"preterm-{expected_class}.db"
            conn = await aiosqlite.connect(str(db_path))
            saver = AsyncSqliteSaver(conn)
            await saver.setup()
            try:
                compiled = g.compile(checkpointer=saver)
                cfg = {
                    "configurable": {"thread_id": f"preterm-{expected_class}"},
                    "recursion_limit": 60,
                }
                with caplog.at_level(logging.INFO):
                    await compiled.ainvoke(
                        {
                            "messages": [
                                HumanMessage(content="do thing", id="h1")
                            ],
                            "repair_budget_used": 0,
                            "pending_repair_ghost_terminal": None,
                            "last_repair_boundary_human_id": "",
                        },
                        cfg,
                    )
                st = await compiled.aget_state(cfg)
                values = st.values
                # Intercept repaired: budget 1, recovered, turn complete.
                assert values.get("repair_budget_used") == 1
                assert values["messages"][-1].content == "final answer."
                assert len(provider.calls) == 2
                # No boundary reset anywhere in the turn.
                assert not [
                    ln
                    for ln in _symptom_lines(caplog)
                    if "budget reset" in ln
                ]
                # B-6: the REAL class token on the live path.
                joined = "\n".join(_symptom_lines(caplog))
                assert (
                    f"class={expected_class} phase=detect action=fired"
                    in joined
                )
                assert (
                    f"class={expected_class} phase=repair action=fired"
                    in joined
                )
                # No wrong-class token anywhere on this non-loop path.
                assert "class=loop" not in joined
            finally:
                await conn.close()
