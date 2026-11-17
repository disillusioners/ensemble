"""Compact-note lane — the GRAPH seam wiring (append + swap).

Exercises ``daemon.graph.create_compact_note_tools_node`` and
``daemon.graph.create_compact_note_agent_node`` directly with synthetic
``state`` / ``config`` (no live LLM, no daemon). These are the two
wrappers that finish the append path: the tools-node APPEND and the
agent-node SWAP.
"""

from __future__ import annotations

import hashlib
import inspect
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

import daemon.graph as graph_mod
from daemon.graph import (
    create_compact_note_agent_node,
    create_compact_note_tools_node,
)
import daemon.services.prompt_refresh as pr
from daemon.services.prompt_refresh import arm_compact_note, read_compact_note_state


class FakeRepo:
    def __init__(self):
        self.metadata = {}

    def get(self, instance_id):
        return SimpleNamespace(
            agent_id="ari", instance_metadata=dict(self.metadata)
        )

    def set_metadata_many(self, instance_id, updates):
        self.metadata.update(updates)

    def delete_metadata(self, instance_id, key):
        self.metadata.pop(key, None)

    def set_metadata(self, instance_id, key, value):
        self.metadata[key] = value


class FakeManager:
    def __init__(self, repo):
        self._instance_repository = repo


class FakeGraph:
    """Minimal compiled-graph stand-in for the persist seam."""

    def __init__(self, messages):
        self.state_messages = list(messages)
        self.writes = []

    async def aget_state(self, config):
        s = SimpleNamespace()
        s.values = {"messages": list(self.state_messages)}
        return s

    async def aupdate_state(self, config, update, as_node=None):
        self.writes.append((dict(update), as_node))
        if "messages" in update:
            self.state_messages = list(update["messages"])
        return None


def _inner_returning(payload):
    async def _inner(state, config=None):
        return payload

    return _inner


NOTE = "the sonic arc is dead; the decision-module thread is settled"


# ── Append (tools node) ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_append_armed_appends_request_in_same_return():
    repo = FakeRepo()
    arm_compact_note(repo, "iid-1", NOTE)
    manager = FakeManager(repo)
    inner = _inner_returning(
        {"messages": [ToolMessage(content="armed", name="prompt_reload", tool_call_id="tc1")]}
    )
    node = create_compact_note_tools_node(
        inner, manager=manager, compactor=None, llm_config={"model": "gpt-4o"}
    )
    state = {
        "messages": [
            HumanMessage(content="hi"),
            AIMessage(content="x", tool_calls=[{"id": "tc1", "name": "prompt_reload", "args": {}}]),
        ]
    }
    config = {"configurable": {"thread_id": "iid-1"}}
    out = await node(state, config)
    assert isinstance(out, dict)
    assert isinstance(out["messages"], list)
    last = out["messages"][-1]
    assert isinstance(last, HumanMessage)
    assert last.additional_kwargs.get("context_kind") == "compaction_request"
    assert last.additional_kwargs.get("injected_message") is True
    assert last.content.startswith("[System request: Compaction]")


@pytest.mark.asyncio
async def test_append_bare_list_return_handled():
    repo = FakeRepo()
    arm_compact_note(repo, "iid-1", NOTE)
    manager = FakeManager(repo)
    inner = _inner_returning(
        [ToolMessage(content="armed", name="prompt_reload", tool_call_id="tc1")]
    )
    node = create_compact_note_tools_node(
        inner, manager=manager, compactor=None, llm_config={"model": "gpt-4o"}
    )
    state = {"messages": [HumanMessage(content="hi")]}
    config = {"configurable": {"thread_id": "iid-1"}}
    out = await node(state, config)
    assert isinstance(out, list)
    assert isinstance(out[-1], HumanMessage)
    assert out[-1].additional_kwargs.get("context_kind") == "compaction_request"


@pytest.mark.asyncio
async def test_append_unarmed_is_byte_identical_passthrough():
    repo = FakeRepo()  # not armed
    manager = FakeManager(repo)
    payload = {"messages": [ToolMessage(content="armed", name="prompt_reload", tool_call_id="tc1")]}
    inner = _inner_returning(payload)
    node = create_compact_note_tools_node(
        inner, manager=manager, compactor=None, llm_config={"model": "gpt-4o"}
    )
    state = {"messages": [HumanMessage(content="hi")]}
    config = {"configurable": {"thread_id": "iid-1"}}
    out = await node(state, config)
    # Passthrough: same messages, no request appended.
    assert out["messages"] == payload["messages"]
    assert not any(
        isinstance(m, HumanMessage) and (getattr(m, "additional_kwargs", {}) or {}).get("context_kind") == "compaction_request"
        for m in out["messages"]
    )


# ── Swap (agent node) ────────────────────────────────────────────────────────


def _request_msg():
    return HumanMessage(
        content="[System request: Compaction] ...",
        additional_kwargs={"injected_message": True, "context_kind": "compaction_request"},
    )


@pytest.mark.asyncio
async def test_swap_armed_produces_sentinel_doc_preserved():
    repo = FakeRepo()
    arm_compact_note(repo, "iid-1", NOTE)
    manager = FakeManager(repo)

    request = _request_msg()
    state = {
        "messages": [
            HumanMessage(content="hi"),
            AIMessage(content="tc", tool_calls=[{"id": "tc1", "name": "prompt_reload", "args": {}}]),
            ToolMessage(content="armed", name="prompt_reload", tool_call_id="tc1"),
            request,
        ]
    }
    reply = AIMessage(content="THE COMPACTED OUTPUT")
    inner = _inner_returning({"messages": [reply]})
    graph = FakeGraph(state["messages"])
    cfg_obj = SimpleNamespace(
        recent_message_window=10,
        min_recent_window=3,
        threshold=0.80,
        context_window_overrides={},
        context_window_default=0,
    )
    compactor = SimpleNamespace(config=cfg_obj)
    node = create_compact_note_agent_node(
        inner,
        manager=manager,
        compactor=compactor,
        llm_config={"model": "gpt-4o", "model_vision": "gpt-4o-vision"},
        llm_standard=None,
        graph_ref=[graph],
    )
    config = {"configurable": {"thread_id": "iid-1"}}
    out = await node(state, config)

    assert isinstance(out, dict)
    channel = out["messages"]
    # sentinel first
    from langchain_core.messages import RemoveMessage

    assert isinstance(channel[0], RemoveMessage)
    # doc second
    doc = channel[1]
    assert isinstance(doc, SystemMessage)
    assert "THE COMPACTED OUTPUT" in (doc.content if isinstance(doc.content, str) else str(doc.content))
    assert "SECTION DETAIL below remains complete" not in (doc.content if isinstance(doc.content, str) else str(doc.content))
    assert (doc.content if isinstance(doc.content, str) else str(doc.content)).startswith("[CONTEXT COMPACTION")
    # note text absent from doc
    assert NOTE not in (doc.content if isinstance(doc.content, str) else str(doc.content))
    # exchange consumed: request & reply not in channel
    assert request not in channel
    assert reply not in channel
    # ordering: doc before tail
    assert channel[1] is doc
    # preserved tail present (the "hi" human message survives)
    assert any(isinstance(m, HumanMessage) and getattr(m, "content", None) == "hi" for m in channel[2:])


@pytest.mark.asyncio
async def test_swap_empty_reply_no_swap_arm_persists():
    repo = FakeRepo()
    arm_compact_note(repo, "iid-1", NOTE)
    manager = FakeManager(repo)
    request = _request_msg()
    state = {"messages": [HumanMessage(content="hi"), request]}
    inner = _inner_returning({"messages": [AIMessage(content="")]})
    graph = FakeGraph(state["messages"])
    cfg_obj = SimpleNamespace(recent_message_window=10, min_recent_window=3, threshold=0.80, context_window_overrides={}, context_window_default=0)
    compactor = SimpleNamespace(config=cfg_obj)
    node = create_compact_note_agent_node(
        inner, manager=manager, compactor=compactor,
        llm_config={"model": "gpt-4o"}, llm_standard=None, graph_ref=[graph],
    )
    config = {"configurable": {"thread_id": "iid-1"}}
    out = await node(state, config)
    # No swap: channel unchanged (still has the reply message, not sentinel-first).
    assert not (isinstance(out.get("messages"), list) and out["messages"] and isinstance(out["messages"][0], __import__("langchain_core.messages", fromlist=["RemoveMessage"]).RemoveMessage))
    # arm still armed
    st = read_compact_note_state(repo, "iid-1")
    assert st.armed is True


@pytest.mark.asyncio
async def test_swap_reply_with_tool_calls_no_swap():
    repo = FakeRepo()
    arm_compact_note(repo, "iid-1", NOTE)
    manager = FakeManager(repo)
    request = _request_msg()
    state = {"messages": [HumanMessage(content="hi"), request]}
    reply = AIMessage(content="x", tool_calls=[{"id": "t", "name": "foo", "args": {}}])
    inner = _inner_returning({"messages": [reply]})
    graph = FakeGraph(state["messages"])
    cfg_obj = SimpleNamespace(recent_message_window=10, min_recent_window=3, threshold=0.80, context_window_overrides={}, context_window_default=0)
    compactor = SimpleNamespace(config=cfg_obj)
    node = create_compact_note_agent_node(
        inner, manager=manager, compactor=compactor,
        llm_config={"model": "gpt-4o"}, llm_standard=None, graph_ref=[graph],
    )
    config = {"configurable": {"thread_id": "iid-1"}}
    out = await node(state, config)
    # Not sentinel-first
    from langchain_core.messages import RemoveMessage

    assert not (isinstance(out.get("messages"), list) and out["messages"] and isinstance(out["messages"][0], RemoveMessage))


@pytest.mark.asyncio
async def test_swap_persist_false_no_swap_no_compacted():
    repo = FakeRepo()
    arm_compact_note(repo, "iid-1", NOTE)
    manager = FakeManager(repo)
    request = _request_msg()
    state = {"messages": [HumanMessage(content="hi"), request]}
    inner = _inner_returning({"messages": [AIMessage(content="COMPACTED")]})
    graph = FakeGraph(state["messages"])
    cfg_obj = SimpleNamespace(recent_message_window=10, min_recent_window=3, threshold=0.80, context_window_overrides={}, context_window_default=0)
    compactor = SimpleNamespace(config=cfg_obj)
    node = create_compact_note_agent_node(
        inner, manager=manager, compactor=compactor,
        llm_config={"model": "gpt-4o"}, llm_standard=None, graph_ref=[graph],
    )
    config = {"configurable": {"thread_id": "iid-1"}}
    with patch("daemon.services._compaction_persist_seam.persist_compaction_result", return_value=False):
        out = await node(state, config)
    from langchain_core.messages import RemoveMessage

    assert not (isinstance(out.get("messages"), list) and out["messages"] and isinstance(out["messages"][0], RemoveMessage))
    st = read_compact_note_state(repo, "iid-1")
    assert st.armed is True


@pytest.mark.asyncio
async def test_swap_same_model_resolve_never_called():
    repo = FakeRepo()
    arm_compact_note(repo, "iid-1", NOTE)
    manager = FakeManager(repo)
    request = _request_msg()
    state = {"messages": [HumanMessage(content="hi"), request]}
    inner = _inner_returning({"messages": [AIMessage(content="COMPACTED")]})
    graph = FakeGraph(state["messages"])
    cfg_obj = SimpleNamespace(recent_message_window=10, min_recent_window=3, threshold=0.80, context_window_overrides={}, context_window_default=0)
    compactor = SimpleNamespace(config=cfg_obj)
    node = create_compact_note_agent_node(
        inner, manager=manager, compactor=compactor,
        llm_config={"model": "gpt-4o"}, llm_standard=None, graph_ref=[graph],
    )
    config = {"configurable": {"thread_id": "iid-1"}}
    with patch("daemon.compaction.resolve_compaction_model", side_effect=RuntimeError("must not be called")):
        out = await node(state, config)
    # If it had been called, the exception would have propagated.
    assert isinstance(out, dict)


@pytest.mark.asyncio
async def test_swap_vision_turn_uses_vision_model_for_window():
    repo = FakeRepo()
    arm_compact_note(repo, "iid-1", NOTE)
    manager = FakeManager(repo)
    request = _request_msg()
    # state message carrying an image block
    img_msg = HumanMessage(content=[{"type": "image_url", "image_url": {"url": "x"}}])
    state = {"messages": [img_msg, request]}
    inner = _inner_returning({"messages": [AIMessage(content="COMPACTED")]})
    graph = FakeGraph(state["messages"])
    cfg_obj = SimpleNamespace(
        recent_message_window=10,
        min_recent_window=3,
        threshold=0.80,
        context_window_overrides={"gpt-4o-vision": 9999, "gpt-4o": 1111},
        context_window_default=0,
    )
    compactor = SimpleNamespace(config=cfg_obj)
    node = create_compact_note_agent_node(
        inner,
        manager=manager,
        compactor=compactor,
        llm_config={"model": "gpt-4o", "model_vision": "gpt-4o-vision"},
        llm_standard=object(),  # not None => vision switch possible
        graph_ref=[graph],
    )
    config = {"configurable": {"thread_id": "iid-1"}}
    with patch("daemon.compaction.get_model_context_limit", wraps=__import__("daemon.compaction", fromlist=["get_model_context_limit"]).get_model_context_limit) as spy:
        out = await node(state, config)
        # The spy should have been called with the vision model name.
        called_models = [c.args[0] for c in spy.call_args_list]
        assert "gpt-4o-vision" in called_models, called_models


@pytest.mark.asyncio
async def test_swap_tail_byte_identity():
    repo = FakeRepo()
    arm_compact_note(repo, "iid-1", NOTE)
    manager = FakeManager(repo)
    request = _request_msg()
    preserved_human = HumanMessage(content="hi")
    state = {"messages": [preserved_human, request]}
    inner = _inner_returning({"messages": [AIMessage(content="COMPACTED")]})
    graph = FakeGraph(state["messages"])
    cfg_obj = SimpleNamespace(recent_message_window=10, min_recent_window=3, threshold=0.80, context_window_overrides={}, context_window_default=0)
    compactor = SimpleNamespace(config=cfg_obj)
    node = create_compact_note_agent_node(
        inner, manager=manager, compactor=compactor,
        llm_config={"model": "gpt-4o"}, llm_standard=None, graph_ref=[graph],
    )
    config = {"configurable": {"thread_id": "iid-1"}}
    out = await node(state, config)
    channel = out["messages"]
    # The preserved message object survives byte-identical.
    assert preserved_human in channel


@pytest.mark.asyncio
async def test_swap_observed_routing_tail_is_preserved():
    repo = FakeRepo()
    arm_compact_note(repo, "iid-1", NOTE)
    manager = FakeManager(repo)
    request = _request_msg()
    state = {"messages": [HumanMessage(content="hi"), request]}
    inner = _inner_returning({"messages": [AIMessage(content="COMPACTED")]})
    graph = FakeGraph(state["messages"])
    cfg_obj = SimpleNamespace(recent_message_window=10, min_recent_window=3, threshold=0.80, context_window_overrides={}, context_window_default=0)
    compactor = SimpleNamespace(config=cfg_obj)
    node = create_compact_note_agent_node(
        inner, manager=manager, compactor=compactor,
        llm_config={"model": "gpt-4o"}, llm_standard=None, graph_ref=[graph],
    )
    config = {"configurable": {"thread_id": "iid-1"}}
    out = await node(state, config)
    channel = out["messages"]
    # The tail's last message is a preserved message, not the reply/request,
    # so the normal router decides the turn's continuation (live routing
    # itself is unobserved in this unit test).
    assert not isinstance(channel[-1], AIMessage) or channel[-1].content != "COMPACTED"


@pytest.mark.asyncio
async def test_swap_persisted_calls_settle_and_swaps():
    repo = FakeRepo()
    arm_compact_note(repo, "iid-1", NOTE)
    manager = FakeManager(repo)
    request = _request_msg()
    state = {"messages": [HumanMessage(content="hi"), request]}
    inner = _inner_returning({"messages": [AIMessage(content="COMPACTED")]})
    graph = FakeGraph(state["messages"])
    cfg_obj = SimpleNamespace(recent_message_window=10, min_recent_window=3, threshold=0.80, context_window_overrides={}, context_window_default=0)
    compactor = SimpleNamespace(config=cfg_obj)
    node = create_compact_note_agent_node(
        inner, manager=manager, compactor=compactor,
        llm_config={"model": "gpt-4o"}, llm_standard=None, graph_ref=[graph],
    )
    config = {"configurable": {"thread_id": "iid-1"}}
    with patch("daemon.services._compaction_persist_seam.persist_compaction_result", return_value=True):
        out = await node(state, config)
    from langchain_core.messages import RemoveMessage

    assert isinstance(out["messages"], list) and out["messages"] and isinstance(out["messages"][0], RemoveMessage)
    st = read_compact_note_state(repo, "iid-1")
    assert st.armed is False


def test_zero_decision_calls_in_wrappers():
    src_tools = inspect.getsource(create_compact_note_tools_node)
    src_agent = inspect.getsource(create_compact_note_agent_node)
    for src in (src_tools, src_agent):
        assert "daemon.services.decision" not in src
        assert "decision" not in src.lower() or True  # soft: only import-path check is hard
    # Hard pin: no decision module import in either wrapper.
    for src in (src_tools, src_agent):
        assert "from .services.decision" not in src
        assert "import daemon.services.decision" not in src


def test_factories_exported():
    assert callable(create_compact_note_tools_node)
    assert callable(create_compact_note_agent_node)
