"""T8 — graph smoke: the wired "tools" node IS the long-tool-nudge
wrapper over the module-level ``_LONG_TOOL_REGISTRY`` singleton.

Pins against any future refactor that allocates a per-graph registry
(graph builds many graphs per process — a per-graph allocation would
silently no-op the whole feature):

* ``build_instance_graph`` registers a ``"tools"`` node whose callable
  is the wrapper factory product.
* That wired callable stamps in-flight tool calls into the
  ``_LONG_TOOL_REGISTRY`` singleton of the SAME module instance the
  graph was built against (behavioral singleton-identity pin), and
  leaves the registry EMPTY after a happy-path single-tool-call batch.

Runs under the repo-standard ``evict_langgraph_mocks`` /
``restore_langgraph_mocks`` pattern — the real langgraph is required
for real graph construction and for ``ToolNode.ainvoke``'s runtime
config keys.
"""

from __future__ import annotations

import importlib
import sys
from typing import TypedDict

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from tests.helpers.checkpoint_prune_pg import (
    evict_langgraph_mocks,
    restore_langgraph_mocks,
)


class _S(TypedDict, total=False):
    messages: list


@pytest.fixture
def real_graph_chain():
    """daemon.graph + daemon.services.long_tool_nudge with REAL langgraph."""
    saved = evict_langgraph_mocks()
    saved_graph = sys.modules.pop("daemon.graph", None)
    saved_lt = sys.modules.pop("daemon.services.long_tool_nudge", None)
    try:
        lt = importlib.import_module("daemon.services.long_tool_nudge")
        graph_mod = importlib.import_module("daemon.graph")
        yield lt, graph_mod
    finally:
        sys.modules.pop("daemon.graph", None)
        if saved_graph is not None:
            sys.modules["daemon.graph"] = saved_graph
        sys.modules.pop("daemon.services.long_tool_nudge", None)
        if saved_lt is not None:
            sys.modules["daemon.services.long_tool_nudge"] = saved_lt
        restore_langgraph_mocks(saved)


def _capture_tools_node(graph_mod, monkeypatch):
    captured: dict = {}
    original_add_node = graph_mod.StateGraph.add_node

    def recording_add_node(self, name, fn, *args, **kwargs):
        captured[name] = fn
        return original_add_node(self, name, fn, *args, **kwargs)

    monkeypatch.setattr(
        graph_mod.StateGraph, "add_node", recording_add_node
    )
    return captured


def _build_graph(graph_mod, lt, tools):
    from langgraph.checkpoint.memory import MemorySaver

    return graph_mod.build_instance_graph(
        tools=tools,
        checkpointer=MemorySaver(),
        llm_config={"model": "gpt-4o", "api_key": "test-key"},
        system_prompt="smoke",
        manager=None,
        language_check_enabled=False,
    )


def test_tools_node_registered_with_callable(real_graph_chain, monkeypatch):
    lt, graph_mod = real_graph_chain
    captured = _capture_tools_node(graph_mod, monkeypatch)

    @tool
    def smoke_probe(x: str) -> str:
        """Probe tool."""
        return "probed"

    compiled = _build_graph(graph_mod, lt, [smoke_probe])
    assert "tools" in compiled.nodes
    assert "tools" in captured
    assert callable(captured["tools"])


@pytest.mark.asyncio
async def test_wired_tools_node_stamps_the_singleton_and_clears(
    real_graph_chain, monkeypatch
):
    lt, graph_mod = real_graph_chain
    captured = _capture_tools_node(graph_mod, monkeypatch)

    @tool
    def smoke_probe(x: str) -> str:
        """Probe tool that observes the singleton mid-flight."""
        return "probed"

    _build_graph(graph_mod, lt, [smoke_probe])
    tools_node = captured["tools"]

    from langgraph.graph import END, START, StateGraph

    g = StateGraph(_S)
    g.add_node("tools", tools_node)
    g.add_edge(START, "tools")
    g.add_edge("tools", END)
    compiled = g.compile()

    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "smoke_probe",
                        "args": {"x": "1"},
                        "id": "call-smoke-1",
                        "type": "tool_call",
                    }
                ],
            )
        ]
    }
    config = {"configurable": {"thread_id": "smoke-instance-1"}}

    registry = lt._LONG_TOOL_REGISTRY
    assert await registry.snapshot() == {}  # clean start
    result = await compiled.ainvoke(state, config=config)
    # Happy-path single tool_call completed → registry EMPTY post-turn.
    assert await registry.snapshot() == {}
    # The tool actually executed through the delegation (output shape
    # preserved — ToolNode semantics untouched).
    messages = result["messages"]
    tool_messages = [
        m for m in messages if getattr(m, "tool_call_id", None) == "call-smoke-1"
    ]
    assert len(tool_messages) == 1
    assert tool_messages[0].content == "probed"


@pytest.mark.asyncio
async def test_wired_tools_node_registry_identity_is_the_module_singleton(
    real_graph_chain, monkeypatch
):
    """Behavioral identity pin: mid-flight, the stamp observable via the
    module-level singleton is the SAME stamp the wrapper wrote."""
    lt, graph_mod = real_graph_chain
    captured = _capture_tools_node(graph_mod, monkeypatch)
    observed: dict = {}

    @tool
    def identity_probe(x: str) -> str:
        """Inspects the module singleton mid-batch."""
        observed["singleton_stamps"] = {
            iid: dict(tcs) for iid, tcs in lt._LONG_TOOL_REGISTRY._stamps.items()
        }
        return "identity-ok"

    _build_graph(graph_mod, lt, [identity_probe])
    tools_node = captured["tools"]

    from langgraph.graph import END, START, StateGraph

    g = StateGraph(_S)
    g.add_node("tools", tools_node)
    g.add_edge(START, "tools")
    g.add_edge("tools", END)
    compiled = g.compile()

    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "identity_probe",
                        "args": {"x": "1"},
                        "id": "call-id-1",
                        "type": "tool_call",
                    }
                ],
            )
        ]
    }
    await compiled.ainvoke(
        state, config={"configurable": {"thread_id": "smoke-instance-2"}}
    )
    mid = observed["singleton_stamps"]
    assert "call-id-1" in mid.get("smoke-instance-2", {})  # SAME singleton
    assert await lt._LONG_TOOL_REGISTRY.snapshot() == {}  # cleared after
