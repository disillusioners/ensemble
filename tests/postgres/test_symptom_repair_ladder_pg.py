"""PG-mode durability for the durable loop repair carrier (E-2 / T-2 / T-13).

Mirrors ``tests/unit/test_symptom_repair_mid_superstep_canary.py`` on a
REAL PostgreSQL checkpoint store (``AsyncPostgresSaver``): the
return-carried repair surgery + the durable ``repair_budget_used``
budget must survive a restart/revive simulation against PG, and no
re-trip may occur.

Runbook (disposable-PG14 recipe — repo blueprint (f)):

    unset POSTGRES_HOST POSTGRES_PORT POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_URL
    /opt/homebrew/opt/postgresql@14/bin/initdb -A trust /tmp/ladder-pg14-pgdata
    /opt/homebrew/opt/postgresql@14/bin/pg_ctl -D /tmp/ladder-pg14-pgdata \\
        -o "-p 15432" -l /tmp/ladder-pg14-pgdata.log start
    /opt/homebrew/opt/postgresql@14/bin/createdb -h localhost -p 15432 ensemble_ladder_test
    LADDER_PG_CONNINFO="postgresql://localhost:15432/ensemble_ladder_test" \\
        uv run python -m pytest tests/postgres/test_symptom_repair_ladder_pg.py \\
        --override-ini="addopts=" -m postgres -q
    # teardown:
    /opt/homebrew/opt/postgresql@14/bin/pg_ctl -D /tmp/ladder-pg14-pgdata stop
    rm -rf /tmp/ladder-pg14-pgdata

Skips (loud, never fakes a pass) when no PG is reachable at
``LADDER_PG_CONNINFO``.
"""
from __future__ import annotations

import os
import sys

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

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

#: Connection string for the disposable PG (recipe in the module docstring).
PG_CONNINFO_ENV = "LADDER_PG_CONNINFO"

_MOCKED_LANGGRAPH_KEYS = (
    "langgraph",
    "langgraph.graph",
    "langgraph.graph.state",
    "langgraph.prebuilt",
    "langgraph.constants",
    "langgraph.checkpoint",
    "langgraph.checkpoint.postgres",
)


class _RealLangGraph:
    """Swap the conftest's mocked langgraph modules for the real ones."""

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


def _pg_conninfo() -> str | None:
    return os.environ.get(PG_CONNINFO_ENV)


def _build_repair_agent(instance_id: str):
    """Same minimal repair-carrying agent as the SQLite canary."""
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
            return {"messages": [response], "repair_budget_used": budget}
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


@pytest.mark.postgres
class TestRepairDurabilityPostgres:
    @pytest.mark.asyncio
    async def test_repair_and_budget_survive_restart_on_pg(self):
        conninfo = _pg_conninfo()
        if not conninfo:
            pytest.skip(
                f"no disposable PG configured — set {PG_CONNINFO_ENV} "
                f"(recipe in this module's docstring); NOT a pass, an "
                f"explicit skip"
            )
        with _RealLangGraph():
            import uuid as _uuid

            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

            # Unique thread per run: a persistent PG checkpoint store
            # keeps prior runs' state, and this test must be idempotent
            # across re-runs against the same database.
            instance_id = f"pg-durability-{_uuid.uuid4().hex[:8]}"
            agent = _build_repair_agent(instance_id)
            g = _make_state_graph(agent)
            async with AsyncPostgresSaver.from_conn_string(conninfo) as saver:
                await saver.setup()
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

                # "Restart": a NEW compiled graph over the SAME PG saver.
                agent2 = _build_repair_agent(instance_id)
                g2 = _make_state_graph(agent2).compile(checkpointer=saver)
                st = await g2.aget_state(cfg)
                ids = [getattr(m, "id", None) for m in st.values["messages"]]

                # Durable surgery landed on PG: loop duplicates gone,
                # evidence retained, exactly ONE repair doc, budget == 1.
                assert "ai-1" not in ids and "tm-1" not in ids
                assert "ai-2" not in ids and "tm-2" not in ids
                assert "ai-0" in ids and "tm-0" in ids
                doc_ids = [
                    i
                    for i in ids
                    if str(i).startswith(f"{REPAIR_DOC_ID_PREFIX}{instance_id}-")
                ]
                assert len(doc_ids) == 1
                assert st.values.get("repair_budget_used") == 1

                # A further clean run does NOT re-trip (no doc 2).
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
                assert len(doc_ids2) == 1
                assert st2.values.get("repair_budget_used") == 1
