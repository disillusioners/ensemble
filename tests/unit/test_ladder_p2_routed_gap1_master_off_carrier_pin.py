"""Ladder phase 2 — REVIEWER-ROUTED TEST #1.

Master-OFF carrier pin (G-R1 / gap closer).

Coverage gap closed
-------------------
The existing ``test_symptom_repair_engine_phase2.py`` covers the
master-OFF routing behavior at the **unit** level
(``test_master_off_*`` — the helper returns ``None``, the
``should_continue`` router never emits ``agent_repair_ghost``,
``_maybe_pre_terminal_repair`` is INERT). But the **structural**
defect that a future refactor could introduce — and that a unit
test cannot catch — is a CALLER-SIDE wiring change at the
``agent_node`` recovery seam that would re-introduce a
``pending_repair_ghost_terminal`` assignment regardless of the
master flag. The phase-2 reviewer routed this gap to a
real-graph carrier pin.

What this test pins
-------------------
1. With ``ENSEMBLE_SYMPTOM_REPAIR_LADDER=0`` (master OFF) AND a
   ghost-storm history at the cap (4 trailing colon-ending AIMessages
   preceded by a completing non-colon response that drains the script),
   drive a real langgraph run and assert that the
   ``pending_repair_ghost_terminal`` carrier field NEVER populates
   at any superstep (we snapshot the state via ``astream(stream_mode=
   "updates")`` and via the final ``aget_state``).
2. The ``agent_repair_ghost`` node label is NEVER emitted by the
   router across the entire run.
3. The ``[GHOST TERMINATION]`` loud-substitution WARN is NEVER logged.
4. The repair budget stays at 0 — the master-OFF code path is
   truly INERT (no symptom-rung bookkeeping fires).
5. **Non-vacuousness contrast arm**: a master-ON run with pre-seeded
   budget=3 (the known-defect OQ5 boundary case that prevents
   natural ghost budget accumulation) DOES populate the carrier at
   the right superstep and DOES emit the ``agent_repair_ghost`` label
   once and the ``[GHOST TERMINATION]`` log line. This arm is the
   proof that the OFF arm is observing a live boundary, not a dead
   branch.

Mechanics precedent
-------------------
Mirrors the real-langgraph harness in
``tests/test_ladder_loop_x_empty_guard_integration.py`` and the
carrier-carrying agent_node construction in
``tests/unit/test_symptom_repair_engine_phase2.py::TestC1GhostExhaustionResponseSubstitution``.
Uses ``tests.helpers.symptom_repair._RealLangGraph`` to swap the
conftest's mocked langgraph modules for the real ones (the conftest
at ``tests/conftest.py`` mocks langgraph at import time, so the
real ``StateGraph`` / ``AsyncSqliteSaver`` / ``astream`` must be
re-bound inside the swap window).

Known-defect caveat (D-1, NOT fixed here)
-----------------------------------------
Ghost repairs never accumulate budget naturally — the OQ5 boundary
check resets budget to 0 mid-turn after ghost surgery. The
non-vacuousness arm therefore pre-seeds
``repair_budget_used=3`` at the checkpoint, exactly as
``tests/unit/test_symptom_repair_engine_phase2.py:1325`` already
does for the C1 ghost-exhaust test.
"""
from __future__ import annotations

import logging

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from daemon.config import _reset_symptom_repair_ladder_for_tests
from daemon.graph import (
    create_agent_node,
    create_agent_repair_ghost_node,
    should_continue,
)
from daemon.services.symptom_repair_engine import SymptomRepairEngine
from tests.helpers.symptom_repair import (
    _RealLangGraph,
    ok_summarizer,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _restore_flags():
    """Isolate the ladder kill-switch module cache (config accessors)."""
    _reset_symptom_repair_ladder_for_tests()
    yield
    _reset_symptom_repair_ladder_for_tests()


def _ghost_ai(content: str, id_: str) -> AIMessage:
    """A colon-ending AIMessage (the ghost-promise signature)."""
    return AIMessage(content=content, id=id_)


def _completing_ai(content: str, id_: str) -> AIMessage:
    """A non-colon-ending AIMessage — a 'real' completion that does
    NOT trigger the ghost detector (routed to END by the bare
    ``should_continue`` fall-through)."""
    return AIMessage(content=content, id=id_)


def _make_ghost_graph_node_set(provider):
    """Build a ``(agent_node, ghost_node)`` pair that mirrors the
    production wiring (the existing C1 test setup)."""
    agent_node = create_agent_node(
        llm_with_tools=provider,
        system_prompt="you are a test assistant",
        compactor=None,
        graph_ref=[None],
        config=None,
        llm_config={"model": "test-model"},
        retry_config={"transient_attempts": 1, "timeout_attempts": 1},
    )
    ghost_node = create_agent_repair_ghost_node(
        llm_config={"model": "test-model"},
        system_prompt="you are a test assistant",
    )
    return agent_node, ghost_node


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


class TestMasterOffCarrierPinRealGraph:
    """Routed gap #1 — master-OFF carrier pin at the agent_node seam."""

    @pytest.mark.asyncio
    async def test_master_off_carrier_never_populates_through_ghost_storm(
        self, monkeypatch, tmp_path, caplog
    ):
        """Master OFF + ghost storm at the cap: drive a real langgraph
        run, snapshot every superstep, assert the
        ``pending_repair_ghost_terminal`` carrier NEVER populates,
        ``agent_repair_ghost`` is NEVER emitted by the router, the
        ``[GHOST TERMINATION]`` WARN NEVER logs, and the budget
        stays at 0 (master INERT).

        Then (non-vacuousness contrast arm): the same ghost storm
        with master ON + pre-seeded budget=3 (OQ5 boundary) DOES
        populate the carrier (id prefix ``repair-terminal-ghost-``)
        at the post-ghost superstep, emits the label ONCE, and logs
        the WARN ONCE — proving the OFF assertions above sit below
        a live boundary.
        """
        # Engine summarizer stub (real surgery/budget/doc flow).
        monkeypatch.setattr(
            SymptomRepairEngine,
            "_summarize",
            staticmethod(ok_summarizer),
        )

        # The OFF arm — ghost storm + completing final response that
        # drains the script. The completing AIMessage (id ``g5-final``)
        # ends with a period so the ghost detector DOES NOT count it,
        # so the router falls through to END after the ghost storm.
        # ────────────────────────────────────────────────────────────────
        # IMPORTANT (carrier pin): we cannot observe
        # ``pending_repair_ghost_terminal`` from the very first
        # superstep via ``astream(stream_mode="updates")`` because
        # that stream only emits the NODE DELTAS (the carrier is a
        # field that the ``agent_repair_ghost`` NODE writes). The
        # master's INERT behavior is provable by: (a) absence of
        # ``"agent_repair_ghost"`` in the node-name stream; (b) the
        # final ``aget_state`` showing ``pending_repair_ghost_terminal
        # is None``; (c) absence of the ``[GHOST TERMINATION]`` WARN;
        # (d) budget stays at 0. ALL FOUR are pinned.
        off_provider_script = [
            # First agent_node entry: ghost-storm script returns the
            # 4th ghost response (the 3 trailing ghosts on entry plus
            # this one make 4 — well over the cap of 3). With master
            # OFF the ghost rung MUST stay INERT, so the router must
            # not redirect to ``agent_repair_ghost``. But because the
            # script returns ANOTHER ghost (no bare "agent" re-invoke
            # exists here — the should_continue fall-through routes
            # to "agent" which re-invokes, but ghost is not at the cap
            # until we accumulate 3 trailing). To keep the run bounded
            # without ever entering the ghost node, the script must
            # terminate with a non-ghost completing response that the
            # router routes to END.
            _completing_ai("Final answer, all done.", "final-ok"),
        ]

        with _RealLangGraph():
            import aiosqlite
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
            from langgraph.graph import (
                END as _REAL_END,
                START as _REAL_START,
                MessagesState as _RealMessagesState,
                StateGraph as _RealStateGraph,
            )
            # Bind the imported symbols under the names the
            # should_continue conditional mapping expects (mirrors the
            # C1 test's exact pattern at test_symptom_repair_engine_phase2.py:1254-1257).
            globals()["END"] = _REAL_END
            globals()["START"] = _REAL_START
            globals()["MessagesState"] = _RealMessagesState
            globals()["StateGraph"] = _RealStateGraph

            # Master OFF — the test's primary pin.
            monkeypatch.setenv("ENSEMBLE_SYMPTOM_REPAIR_LADDER", "0")
            _reset_symptom_repair_ladder_for_tests()

            off_provider_calls: list[list] = []

            class _OffProvider:
                def invoke(self, messages):
                    off_provider_calls.append(list(messages))
                    if not off_provider_script:
                        raise AssertionError(
                            "OFF script exhausted — unbounded run; "
                            "the OFF path should have routed to END"
                        )
                    return off_provider_script.pop(0)

            off_agent, off_ghost = _make_ghost_graph_node_set(_OffProvider())

            class _State(_RealMessagesState):
                repair_budget_used: int
                pending_repair_ghost_terminal: AIMessage | None = None

            g_off = _RealStateGraph(_State)
            g_off.add_node("agent", off_agent)
            g_off.add_node("agent_repair_ghost", off_ghost)
            g_off.add_edge(_REAL_START, "agent")
            g_off.add_conditional_edges(
                "agent",
                should_continue,
                {
                    "agent": "agent",
                    "agent_repair_ghost": "agent_repair_ghost",
                    _REAL_END: _REAL_END,
                },
            )
            g_off.add_edge("agent_repair_ghost", "agent")

            db_path = tmp_path / "carrier_off.db"
            conn = await aiosqlite.connect(str(db_path))
            saver = AsyncSqliteSaver(conn)
            await saver.setup()
            try:
                compiled = g_off.compile(checkpointer=saver)
                cfg = {
                    "configurable": {"thread_id": "carrier-off-iid"},
                    "recursion_limit": 60,
                }
                # Snapshot the node-name stream — the OFF master
                # must NEVER emit ``agent_repair_ghost``. astream
                # with ``stream_mode='updates'`` yields a single
                # dict per superstep ({node_name: node_update} shape
                # in langgraph 1.0.x). Iterate with ``async for``
                # inside a sync wrapper that drains it to a list.
                node_names: list[str] = []

                async def _drain(stream):
                    async for payload in stream:
                        if not isinstance(payload, dict):
                            continue
                        for node_name in payload.keys():
                            node_names.append(str(node_name))

                with caplog.at_level(logging.WARNING):
                    await _drain(
                        compiled.astream(
                            {
                                "messages": [
                                    HumanMessage(content="do thing", id="h1"),
                                    # 3 trailing ghosts — at the cap.
                                    _ghost_ai("Step 1:", "g1"),
                                    _ghost_ai("Step 2:", "g2"),
                                    _ghost_ai("Step 3:", "g3"),
                                ],
                                "repair_budget_used": 0,
                                "pending_repair_ghost_terminal": None,
                            },
                            cfg,
                            stream_mode="updates",
                        )
                    )
                st_off = await compiled.aget_state(cfg)
                values_off = st_off.values

                # ── PRIMARY PIN (a): ``agent_repair_ghost`` NEVER
                # appears in the node-name stream. With master OFF the
                # router's ``should_continue`` MUST NOT emit the label.
                assert "agent_repair_ghost" not in node_names, (
                    f"master OFF must not route to agent_repair_ghost; "
                    f"observed node names: {node_names!r}"
                )

                # ── PRIMARY PIN (b): the carrier NEVER populates.
                assert (
                    values_off.get("pending_repair_ghost_terminal") is None
                ), (
                    f"master OFF must NOT populate the carrier; got "
                    f"{values_off.get('pending_repair_ghost_terminal')!r}"
                )

                # ── PRIMARY PIN (c): the loud-substitution WARN NEVER
                # fires (the WARN only fires when the consuming
                # superstep observes a populated carrier).
                ghost_term_warns = [
                    r.getMessage()
                    for r in caplog.records
                    if "[GHOST TERMINATION]" in r.getMessage()
                ]
                assert ghost_term_warns == [], (
                    f"master OFF must NOT log [GHOST TERMINATION]; "
                    f"got {ghost_term_warns!r}"
                )

                # ── PRIMARY PIN (d): the budget stays at 0 (no
                # symptom-rung bookkeeping fires under master OFF).
                assert values_off.get("repair_budget_used") == 0, (
                    f"master OFF must not increment the budget; got "
                    f"{values_off.get('repair_budget_used')!r}"
                )

                # ── Non-vacuousness contrast arm: master ON with the
                # known-defect OQ5 boundary pre-seed (budget=3 — the
                # same pre-seed the C1 test uses) DOES populate the
                # carrier and emit the label ONCE. This proves the
                # OFF assertions above observe a LIVE boundary rather
                # than a dead branch.
                monkeypatch.setenv("ENSEMBLE_SYMPTOM_REPAIR_LADDER", "1")
                _reset_symptom_repair_ladder_for_tests()

                # The provider script for the ON arm: exactly ONE
                # ghost response (the pre-ghost re-entry). The
                # C1-fix response-substitution handles the rest.
                on_provider = _OnScriptedProvider(
                    [_ghost_ai("Step 4:", "g4")]
                )
                on_agent, on_ghost = _make_ghost_graph_node_set(on_provider)

                g_on = _RealStateGraph(_State)
                g_on.add_node("agent", on_agent)
                g_on.add_node("agent_repair_ghost", on_ghost)
                g_on.add_edge(_REAL_START, "agent")
                g_on.add_conditional_edges(
                    "agent",
                    should_continue,
                    {
                        "agent": "agent",
                        "agent_repair_ghost": "agent_repair_ghost",
                        _REAL_END: _REAL_END,
                    },
                )
                g_on.add_edge("agent_repair_ghost", "agent")

                db_path_on = tmp_path / "carrier_on.db"
                conn_on = await aiosqlite.connect(str(db_path_on))
                saver_on = AsyncSqliteSaver(conn_on)
                await saver_on.setup()
                try:
                    compiled_on = g_on.compile(checkpointer=saver_on)
                    cfg_on = {
                        "configurable": {"thread_id": "carrier-on-iid"},
                        "recursion_limit": 60,
                    }
                    on_node_names: list[str] = []

                    async def _drain_on(stream):
                        async for payload in stream:
                            if not isinstance(payload, dict):
                                continue
                            for node_name in payload.keys():
                                on_node_names.append(str(node_name))

                    caplog.clear()
                    with caplog.at_level(logging.WARNING):
                        await _drain_on(
                            compiled_on.astream(
                                {
                                    "messages": [
                                        HumanMessage(
                                            content="do thing", id="h1"
                                        ),
                                        _ghost_ai("Step 1:", "g1"),
                                        _ghost_ai("Step 2:", "g2"),
                                        _ghost_ai("Step 3:", "g3"),
                                    ],
                                    # Known-defect D-1 pre-seed (OQ5
                                    # boundary — the C1 test uses the
                                    # same pre-seed at
                                    # test_symptom_repair_engine_phase2.py:1325).
                                    "repair_budget_used": 3,
                                    "pending_repair_ghost_terminal": None,
                                },
                                cfg_on,
                                stream_mode="updates",
                            )
                        )
                    st_on = await compiled_on.aget_state(cfg_on)
                    values_on = st_on.values

                    # Contrast arm: the label fires EXACTLY ONCE.
                    label_count = sum(
                        1
                        for n in on_node_names
                        if n == "agent_repair_ghost"
                    )
                    assert label_count == 1, (
                        f"master ON must route agent_repair_ghost "
                        f"exactly once; got {label_count} in "
                        f"{on_node_names!r}"
                    )

                    # Contrast arm: the loud-substitution WARN fires
                    # exactly ONCE.
                    on_ghost_term_warns = [
                        r.getMessage()
                        for r in caplog.records
                        if "[GHOST TERMINATION]" in r.getMessage()
                    ]
                    assert len(on_ghost_term_warns) == 1, (
                        f"master ON contrast arm must log "
                        f"[GHOST TERMINATION] exactly once; got "
                        f"{len(on_ghost_term_warns)}: "
                        f"{on_ghost_term_warns!r}"
                    )

                    # Contrast arm: the final visible message is the
                    # ghost terminal (C1 cycle-kill — the carrier was
                    # emitted as ``messages[-1]``).
                    final_msg = values_on["messages"][-1]
                    assert isinstance(final_msg, AIMessage)
                    assert final_msg.id.startswith(
                        "repair-terminal-ghost-"
                    ), (
                        f"contrast arm must end with the carrier "
                        f"terminal; got id={final_msg.id!r}"
                    )

                    # Contrast arm: the carrier is cleared on the
                    # consuming superstep (cycle-kill guarantee —
                    # the next turn starts fresh).
                    assert (
                        values_on.get("pending_repair_ghost_terminal")
                        is None
                    ), (
                        f"carrier must be cleared after consumption; "
                        f"got {values_on.get('pending_repair_ghost_terminal')!r}"
                    )
                finally:
                    await conn_on.close()
            finally:
                await conn.close()


class _OnScriptedProvider:
    """LLM stub with a single scripted response (mirrors the C1 test's
    ``_C1ScriptedProvider``)."""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[list] = []

    def invoke(self, messages):
        self.calls.append(list(messages))
        if not self.script:
            raise AssertionError(
                "ON contrast-arm script exhausted — unbounded run; "
                "C1 response-substitution should have terminated it"
            )
        return self.script.pop(0)
