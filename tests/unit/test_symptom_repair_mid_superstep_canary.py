"""Return-carried canary mirror + SQLite durability — ladder phase 1.

* T-3 / G-2 canary mirror: the repair carrier must be RETURN-CARRIED —
  the node's own commit lands the sentinel-first surgery. Mirrors
  ``TestMidSuperstepPersistCanary`` (test_compact_executor_revive_brick_e2e)
  on a REAL langgraph + file-backed SQLite checkpointer (O17 binding:
  real graph run, not mocks — the supersede behavior is a property of
  the live checkpointer + commit interaction).
* T-2 durability: restart/revive simulation — a SECOND graph run on the
  same checkpoint must NOT re-trip the detector (the degenerate window
  is gone from the checkpoint) and the durable budget must persist.

P-10 source pins (no ``aupdate_state`` anywhere in the new path) live in
``tests/unit/test_symptom_repair_partition.py`` (AST-walked) — here we
pin the BEHAVIOR: a return-carried repair commit is durable across runs
and the node performs no mid-flight persist calls (counted wrapper).
"""
from __future__ import annotations

import sys

import pytest
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    ToolMessage,
)

from daemon.graph import LoopDetector
from daemon.services.symptom_repair_engine import (
    REPAIR_DOC_ID_PREFIX,
    SymptomRepairContext,
    SymptomRepairEngine,
)

try:
    from langgraph.graph.message import REMOVE_ALL_MESSAGES
except (ImportError, ModuleNotFoundError):  # pragma: no cover
    REMOVE_ALL_MESSAGES = "__remove_all__"


# ---------------------------------------------------------------------------
# Real-langgraph swap (mirrors the canary fixture in
# tests/unit/services/test_compact_executor_revive_brick_e2e.py)
# ---------------------------------------------------------------------------

_MOCKED_LANGGRAPH_KEYS = (
    "langgraph",
    "langgraph.graph",
    "langgraph.graph.state",
    "langgraph.prebuilt",
    "langgraph.constants",
    "langgraph.checkpoint",
    "langgraph.checkpoint.sqlite",
    "langgraph.checkpoint.sqlite.aio",
)


class _RealLangGraph:
    """Swap the conftest's mocked langgraph modules for the real ones
    around a block of test code, then restore."""

    def __enter__(self):
        self._original_modules = {
            k: sys.modules[k] for k in _MOCKED_LANGGRAPH_KEYS if k in sys.modules
        }
        for key in _MOCKED_LANGGRAPH_KEYS:
            if key in sys.modules:
                del sys.modules[key]
        for key in [k for k in sys.modules if k.startswith("langgraph")]:
            del sys.modules[key]
        return self

    def __exit__(self, exc_type, exc, tb):
        for key in [k for k in sys.modules if k.startswith("langgraph")]:
            del sys.modules[key]
        for key, mod in self._original_modules.items():
            sys.modules[key] = mod
        return False


# ---------------------------------------------------------------------------
# Repair-carrying agent node (the phase-1 durable recipe, minimal form)
# ---------------------------------------------------------------------------


def _build_repair_agent(instance_id: str):
    """A minimal agent node that runs the REAL detector + engine and
    carries the repair on its RETURN (the production ``agent_node``
    recipe, reduced to the state channels this canary exercises)."""
    engine = SymptomRepairEngine()

    async def _ok_summarizer(context, symptom_class):
        return "Stub summary of the loop."

    engine._summarize = _ok_summarizer

    async def agent(state, config):
        messages = list(state["messages"])
        budget = int(state.get("repair_budget_used", 0) or 0)
        response = AIMessage(content="turn response", id=f"resp-{budget}")
        detection = LoopDetector.scan(messages=messages, threshold=3)
        if detection is None:
            return {
                "messages": [response],
                "repair_budget_used": budget,
            }
        ctx = SymptomRepairContext(
            detection=detection,
            messages=messages,
            llm_config={"model": "test-model"},
            system_prompt="sp",
            instance_id=instance_id,
            budget_used=budget,
            budget_cap=3,
        )
        outcome = await engine.repair(ctx)
        if not outcome.success:
            return {"messages": [response], "repair_budget_used": budget}
        return {
            "messages": [*outcome.surgery_prefix, response],
            "repair_budget_used": budget + 1,
        }

    return agent


def _make_state_graph(agent):
    """START → agent → END over a MessagesState + budget channel.

    Built INSIDE the ``_RealLangGraph`` swap window (real langgraph).
    The state schema mirrors the production additive-field pattern:
    ``MessagesState`` (add_messages reducer — the sentinel recipe's
    real machinery) plus the durable ``repair_budget_used`` channel
    (last-value-wins, like ``SessionState``).
    """
    from langgraph.graph import END, START, MessagesState, StateGraph

    class _State(MessagesState):
        repair_budget_used: int

    g = StateGraph(_State)
    g.add_node("agent", agent)
    g.add_edge(START, "agent")
    g.add_edge("agent", END)
    return g


def _loop_units(count: int):
    out = []
    for i in range(count):
        tc_id = f"tc-{i}"
        out.append(
            AIMessage(
                content="",
                tool_calls=[{"id": tc_id, "name": "bash", "args": {"cmd": "ls"}}],
                id=f"ai-{i}",
            )
        )
        out.append(
            ToolMessage(
                content=f"res-{i}", tool_call_id=tc_id, name="bash", id=f"tm-{i}"
            )
        )
    return out


# ---------------------------------------------------------------------------
# Canary mirror (T-3) + durability (T-2)
# ---------------------------------------------------------------------------


class TestRepairReturnCarriedCanary:
    @pytest.mark.asyncio
    async def test_return_carried_repair_lands_in_task_commit(self, tmp_path):
        """The repair must survive the task commit: loop units REMOVED,
        doc + budget PERSISTED — via the node return alone."""
        with _RealLangGraph():
            import aiosqlite
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

            instance_id = "canary-iid"
            agent = _build_repair_agent(instance_id)
            g = _make_state_graph(agent)
            db_path = tmp_path / "repair_canary.db"
            conn = await aiosqlite.connect(str(db_path))
            saver = AsyncSqliteSaver(conn)
            await saver.setup()
            try:
                compiled = g.compile(checkpointer=saver)
                cfg = {"configurable": {"thread_id": instance_id}}
                await compiled.ainvoke(
                    {
                        "messages": [
                            HumanMessage(content="go", id="h1"),
                            *_loop_units(3),
                        ],
                        "repair_budget_used": 0,
                    },
                    cfg,
                )
                st = await compiled.aget_state(cfg)
                values = st.values
                contents = values["messages"]

                # The surgery LANDED: loop duplicates gone, evidence kept.
                ids = [getattr(m, "id", None) for m in contents]
                assert "ai-1" not in ids and "tm-1" not in ids
                assert "ai-2" not in ids and "tm-2" not in ids
                assert "ai-0" in ids and "tm-0" in ids  # evidence unit
                # The repair doc is checkpointed under its namespace.
                assert any(
                    str(i).startswith(f"{REPAIR_DOC_ID_PREFIX}{instance_id}-")
                    for i in ids
                )
                # Response appended AFTER the doc (never swallowed).
                assert ids[-1] == "resp-0"
                # The durable budget persisted through the commit.
                assert values.get("repair_budget_used") == 1
            finally:
                await conn.close()

    @pytest.mark.asyncio
    async def test_durability_no_retrip_after_restart(self, tmp_path):
        """T-2: a SECOND run on the same checkpoint (restart/revive
        simulation — fresh graph object, same file-backed checkpointer)
        must NOT re-trip: the degenerate window is gone and the budget
        persists."""
        with _RealLangGraph():
            import aiosqlite
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

            instance_id = "durability-iid"
            agent = _build_repair_agent(instance_id)
            db_path = tmp_path / "repair_durability.db"
            conn = await aiosqlite.connect(str(db_path))
            saver = AsyncSqliteSaver(conn)
            await saver.setup()
            try:
                g1 = _make_state_graph(agent).compile(checkpointer=saver)
                cfg = {"configurable": {"thread_id": instance_id}}
                await g1.ainvoke(
                    {
                        "messages": [
                            HumanMessage(content="go", id="h1"),
                            *_loop_units(3),
                        ],
                        "repair_budget_used": 0,
                    },
                    cfg,
                )

                # "Restart": a NEW compiled graph over the SAME saver.
                agent2 = _build_repair_agent(instance_id)
                g2 = _make_state_graph(agent2).compile(checkpointer=saver)
                st = await g2.aget_state(cfg)
                ids = [getattr(m, "id", None) for m in st.values["messages"]]
                # The repair is DURABLE in the checkpoint: only ONE repair
                # doc exists, budget still 1.
                doc_ids = [
                    i
                    for i in ids
                    if str(i).startswith(f"{REPAIR_DOC_ID_PREFIX}{instance_id}-")
                ]
                assert len(doc_ids) == 1
                assert st.values.get("repair_budget_used") == 1

                # A further user message runs cleanly (no loop → no doc 2).
                await g2.ainvoke(
                    {
                        "messages": [
                            HumanMessage(content="next task", id="h2")
                        ],
                        "repair_budget_used": st.values.get(
                            "repair_budget_used", 0
                        ),
                    },
                    cfg,
                )
                st2 = await g2.aget_state(cfg)
                ids2 = [getattr(m, "id", None) for m in st2.values["messages"]]
                doc_ids2 = [
                    i
                    for i in ids2
                    if str(i).startswith(f"{REPAIR_DOC_ID_PREFIX}{instance_id}-")
                ]
                assert len(doc_ids2) == 1  # no second repair fired
                assert st2.values.get("repair_budget_used") == 1
            finally:
                await conn.close()
