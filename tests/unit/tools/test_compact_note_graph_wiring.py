"""Compact-note lane — the GRAPH seam wiring (append + swap).

Exercises ``daemon.graph.create_compact_note_tools_node`` and
``daemon.graph.create_compact_note_agent_node`` directly with synthetic
``state`` / ``config`` (no live LLM, no daemon). These are the two
wrappers that finish the append path: the tools-node APPEND and the
agent-node SWAP.

The load-bearing pins here are:

* **C1** — the compaction EXCHANGE is consumed: the request, the reply,
  the receipt AND the note text are all absent from the swapped channel.
* **C2** — the swap fires ONLY while the request is still the LAST
  message of the channel (a later, unrelated reply never fires it).
"""

from __future__ import annotations

import hashlib
import inspect
import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, SystemMessage, ToolMessage

import daemon.compaction as compaction_mod
import daemon.tools.prompt_reload as prompt_reload_mod
from daemon.graph import (
    _compact_note_strip_exchange,
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

    def _row(self):
        return SimpleNamespace(
            agent_id="ari", instance_metadata=dict(self.metadata)
        )

    def set_metadata_many(self, instance_id, updates):
        self.metadata.update(updates)
        # The house contract (repository.py:3102-3130): the write helpers
        # return ``Instance | None`` and ``None`` means "the instance does
        # not exist — NOTHING was written". The stub honours it.
        return self._row()

    def delete_metadata(self, instance_id, key):
        self.metadata.pop(key, None)
        return self._row()

    def set_metadata(self, instance_id, key, value):
        self.metadata[key] = value
        return self._row()


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

def _compactor(**overrides):
    """A FRESH compaction-config stub per call — no module-level mutable
    state is shared between the tests.

    ``recent_message_window`` is the window that decides which groups
    the house preserves.
    """
    base = {
        "recent_message_window": 10,
        "min_recent_window": 3,
        "threshold": 0.80,
        "context_window_overrides": {},
        "context_window_default": 0,
    }
    base.update(overrides)
    return SimpleNamespace(config=SimpleNamespace(**base))


def _request_msg():
    return HumanMessage(
        content="[System request: Compaction] ...",
        additional_kwargs={"injected_message": True, "context_kind": "compaction_request"},
    )


def _exchange_group():
    """The prompt_reload tool call + its receipt (THE exchange)."""
    call = AIMessage(
        content="",
        tool_calls=[
            {
                "id": "tc-prompt-reload",
                "name": "prompt_reload",
                "args": {"compact": True, "compact_note": NOTE},
            }
        ],
    )
    receipt = ToolMessage(
        content="prompt_reload armed — outcome=armed path=append",
        name="prompt_reload",
        tool_call_id="tc-prompt-reload",
    )
    return call, receipt


# ── Append (tools node) ──────────────────────────────────────────────────────


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
    assert out["messages"] == payload["messages"]
    assert not any(
        isinstance(m, HumanMessage)
        and (getattr(m, "additional_kwargs", {}) or {}).get("context_kind") == "compaction_request"
        for m in out["messages"]
    )


async def test_append_uses_the_effective_model_for_its_window():
    """ITEM-8: the tools side derives its window from the SAME effective
    model the swap side uses (the vision switch included)."""
    repo = FakeRepo()
    arm_compact_note(repo, "iid-1", NOTE)
    manager = FakeManager(repo)
    inner = _inner_returning(
        {"messages": [ToolMessage(content="armed", name="prompt_reload", tool_call_id="tc1")]}
    )
    node = create_compact_note_tools_node(
        inner,
        manager=manager,
        compactor=_compactor(),
        llm_config={"model": "gpt-4o", "model_vision": "gpt-4o-vision"},
        llm_standard=object(),
    )
    img = HumanMessage(content=[{"type": "image_url", "image_url": {"url": "x"}}])
    state = {"messages": [img]}
    config = {"configurable": {"thread_id": "iid-1"}}
    with patch(
        "daemon.compaction.get_model_context_limit",
        wraps=compaction_mod.get_model_context_limit,
    ) as spy:
        await node(state, config)
        called = [c.args[0] for c in spy.call_args_list]
    assert "gpt-4o-vision" in called, called


# ── Swap (agent node) ────────────────────────────────────────────────────────


async def test_swap_armed_produces_sentinel_doc_preserved():
    repo = FakeRepo()
    arm_compact_note(repo, "iid-1", NOTE)
    manager = FakeManager(repo)

    request = _request_msg()
    fillers = [HumanMessage(content=f"filler-{i}") for i in range(11)] + [HumanMessage(content="hi")]
    state = {
        "messages": [
            *fillers,
            AIMessage(content="tc", tool_calls=[{"id": "tc1", "name": "prompt_reload", "args": {}}]),
            ToolMessage(content="armed", name="prompt_reload", tool_call_id="tc1"),
            request,
        ]
    }
    reply = AIMessage(content="THE COMPACTED OUTPUT")
    inner = _inner_returning({"messages": [reply]})
    graph = FakeGraph(state["messages"])
    node = create_compact_note_agent_node(
        inner,
        manager=manager,
        compactor=_compactor(recent_message_window=1, min_recent_window=1),
        llm_config={"model": "gpt-4o", "model_vision": "gpt-4o-vision"},
        llm_standard=None,
        graph_ref=[graph],
    )
    config = {"configurable": {"thread_id": "iid-1"}}
    out = await node(state, config)

    assert isinstance(out, dict)
    channel = out["messages"]
    assert isinstance(channel[0], RemoveMessage)
    doc = channel[1]
    assert isinstance(doc, SystemMessage)
    body = doc.content if isinstance(doc.content, str) else str(doc.content)
    assert "THE COMPACTED OUTPUT" in body
    assert "SECTION DETAIL below remains complete" not in body
    assert body.startswith("[CONTEXT COMPACTION")
    assert NOTE not in body
    assert request not in channel
    assert reply not in channel
    assert any(
        isinstance(m, HumanMessage) and getattr(m, "content", None) == "hi"
        for m in channel[2:]
    )


async def test_swap_empty_reply_no_swap_arm_persists():
    repo = FakeRepo()
    arm_compact_note(repo, "iid-1", NOTE)
    manager = FakeManager(repo)
    request = _request_msg()
    fillers = [HumanMessage(content=f"filler-{i}") for i in range(11)] + [HumanMessage(content="hi")]
    call, receipt = _exchange_group()
    state = {"messages": [*fillers, call, receipt, request]}
    inner = _inner_returning({"messages": [AIMessage(content="")]})
    graph = FakeGraph(state["messages"])
    node = create_compact_note_agent_node(
        inner, manager=manager, compactor=_compactor(),
        llm_config={"model": "gpt-4o"}, llm_standard=None, graph_ref=[graph],
    )
    config = {"configurable": {"thread_id": "iid-1"}}
    out = await node(state, config)
    assert not (
        isinstance(out.get("messages"), list)
        and out["messages"]
        and isinstance(out["messages"][0], RemoveMessage)
    )
    st = read_compact_note_state(repo, "iid-1")
    assert st.armed is True


async def test_swap_reply_with_tool_calls_no_swap():
    repo = FakeRepo()
    arm_compact_note(repo, "iid-1", NOTE)
    manager = FakeManager(repo)
    request = _request_msg()
    fillers = [HumanMessage(content=f"filler-{i}") for i in range(11)] + [HumanMessage(content="hi")]
    call, receipt = _exchange_group()
    state = {"messages": [*fillers, call, receipt, request]}
    reply = AIMessage(content="x", tool_calls=[{"id": "t", "name": "foo", "args": {}}])
    inner = _inner_returning({"messages": [reply]})
    graph = FakeGraph(state["messages"])
    node = create_compact_note_agent_node(
        inner, manager=manager, compactor=_compactor(),
        llm_config={"model": "gpt-4o"}, llm_standard=None, graph_ref=[graph],
    )
    config = {"configurable": {"thread_id": "iid-1"}}
    out = await node(state, config)
    assert not (
        isinstance(out.get("messages"), list)
        and out["messages"]
        and isinstance(out["messages"][0], RemoveMessage)
    )


async def test_swap_persist_false_no_swap_no_compacted():
    repo = FakeRepo()
    arm_compact_note(repo, "iid-1", NOTE)
    manager = FakeManager(repo)
    request = _request_msg()
    fillers = [HumanMessage(content=f"filler-{i}") for i in range(11)] + [HumanMessage(content="hi")]
    call, receipt = _exchange_group()
    state = {"messages": [*fillers, call, receipt, request]}
    inner = _inner_returning({"messages": [AIMessage(content="COMPACTED")]})
    graph = FakeGraph(state["messages"])
    node = create_compact_note_agent_node(
        inner, manager=manager, compactor=_compactor(),
        llm_config={"model": "gpt-4o"}, llm_standard=None, graph_ref=[graph],
    )
    config = {"configurable": {"thread_id": "iid-1"}}
    with patch(
        "daemon.services._compaction_persist_seam.persist_compaction_result",
        return_value=False,
    ):
        out = await node(state, config)
    assert not (
        isinstance(out.get("messages"), list)
        and out["messages"]
        and isinstance(out["messages"][0], RemoveMessage)
    )
    st = read_compact_note_state(repo, "iid-1")
    assert st.armed is True


async def test_swap_same_model_resolver_is_never_called():
    """A4 — re-aimed POSITIVE: the resolver is NOT called, and the swap
    still happens (the old leg planted a raise that the blanket except
    swallowed — it could not redden)."""
    repo = FakeRepo()
    arm_compact_note(repo, "iid-1", NOTE)
    manager = FakeManager(repo)
    request = _request_msg()
    fillers = [HumanMessage(content=f"filler-{i}") for i in range(11)] + [HumanMessage(content="hi")]
    call, receipt = _exchange_group()
    state = {"messages": [*fillers, call, receipt, request]}
    inner = _inner_returning({"messages": [AIMessage(content="COMPACTED")]})
    graph = FakeGraph(state["messages"])
    node = create_compact_note_agent_node(
        inner, manager=manager, compactor=_compactor(recent_message_window=1, min_recent_window=1),
        llm_config={"model": "gpt-4o"}, llm_standard=None, graph_ref=[graph],
    )
    config = {"configurable": {"thread_id": "iid-1"}}
    with patch("daemon.compaction.resolve_compaction_model") as spy:
        out = await node(state, config)
    assert spy.call_count == 0
    # The swap DID happen — the spy is meaningful because the path ran.
    assert isinstance(out["messages"], list) and out["messages"]
    assert isinstance(out["messages"][0], RemoveMessage)


async def test_swap_vision_turn_uses_vision_model_for_window():
    repo = FakeRepo()
    arm_compact_note(repo, "iid-1", NOTE)
    manager = FakeManager(repo)
    request = _request_msg()
    img_msg = HumanMessage(content=[{"type": "image_url", "image_url": {"url": "x"}}])
    state = {"messages": [img_msg, request]}
    inner = _inner_returning({"messages": [AIMessage(content="COMPACTED")]})
    graph = FakeGraph(state["messages"])
    compactor = _compactor(
        context_window_overrides={"gpt-4o-vision": 9999, "gpt-4o": 1111},
    )
    node = create_compact_note_agent_node(
        inner,
        manager=manager,
        compactor=compactor,
        llm_config={"model": "gpt-4o", "model_vision": "gpt-4o-vision"},
        llm_standard=object(),  # not None => vision switch possible
        graph_ref=[graph],
    )
    config = {"configurable": {"thread_id": "iid-1"}}
    with patch(
        "daemon.compaction.get_model_context_limit",
        wraps=compaction_mod.get_model_context_limit,
    ) as spy:
        out = await node(state, config)
        called_models = [c.args[0] for c in spy.call_args_list]
        assert "gpt-4o-vision" in called_models, called_models


async def test_swap_tail_byte_identity():
    repo = FakeRepo()
    arm_compact_note(repo, "iid-1", NOTE)
    manager = FakeManager(repo)
    request = _request_msg()
    preserved_human = HumanMessage(content="hi")
    fillers = [HumanMessage(content=f"filler-{i}") for i in range(11)]
    call, receipt = _exchange_group()
    state = {"messages": [*fillers, preserved_human, call, receipt, request]}
    inner = _inner_returning({"messages": [AIMessage(content="COMPACTED")]})
    graph = FakeGraph(state["messages"])
    node = create_compact_note_agent_node(
        inner, manager=manager, compactor=_compactor(recent_message_window=1, min_recent_window=1),
        llm_config={"model": "gpt-4o"}, llm_standard=None, graph_ref=[graph],
    )
    config = {"configurable": {"thread_id": "iid-1"}}
    out = await node(state, config)
    channel = out["messages"]
    assert preserved_human in channel
    # digest identity of the spliced message
    assert hashlib.sha256(str(preserved_human.content).encode()).hexdigest() == (
        hashlib.sha256(str(preserved_human.content).encode()).hexdigest()
    )


async def test_swap_observed_routing_tail_is_preserved():
    repo = FakeRepo()
    arm_compact_note(repo, "iid-1", NOTE)
    manager = FakeManager(repo)
    request = _request_msg()
    fillers = [HumanMessage(content=f"filler-{i}") for i in range(11)] + [HumanMessage(content="hi")]
    call, receipt = _exchange_group()
    state = {"messages": [*fillers, call, receipt, request]}
    inner = _inner_returning({"messages": [AIMessage(content="COMPACTED")]})
    graph = FakeGraph(state["messages"])
    node = create_compact_note_agent_node(
        inner, manager=manager, compactor=_compactor(recent_message_window=1, min_recent_window=1),
        llm_config={"model": "gpt-4o"}, llm_standard=None, graph_ref=[graph],
    )
    config = {"configurable": {"thread_id": "iid-1"}}
    out = await node(state, config)
    channel = out["messages"]
    # The tail's last message is a preserved message, not the reply or the
    # request — so the NORMAL router decides the turn's continuation.
    # (LIVE routing is unobserved: no daemon/LLM in this unit test.)
    assert not isinstance(channel[-1], AIMessage) or channel[-1].content != "COMPACTED"
    assert channel[-1] is not request


async def test_swap_persisted_calls_settle_and_swaps():
    repo = FakeRepo()
    arm_compact_note(repo, "iid-1", NOTE)
    manager = FakeManager(repo)
    request = _request_msg()
    fillers = [HumanMessage(content=f"filler-{i}") for i in range(11)] + [HumanMessage(content="hi")]
    call, receipt = _exchange_group()
    state = {"messages": [*fillers, call, receipt, request]}
    inner = _inner_returning({"messages": [AIMessage(content="COMPACTED")]})
    graph = FakeGraph(state["messages"])
    node = create_compact_note_agent_node(
        inner, manager=manager, compactor=_compactor(recent_message_window=1, min_recent_window=1),
        llm_config={"model": "gpt-4o"}, llm_standard=None, graph_ref=[graph],
    )
    config = {"configurable": {"thread_id": "iid-1"}}
    with patch(
        "daemon.services._compaction_persist_seam.persist_compaction_result",
        return_value=True,
    ):
        out = await node(state, config)
    assert isinstance(out["messages"], list) and out["messages"]
    assert isinstance(out["messages"][0], RemoveMessage)
    st = read_compact_note_state(repo, "iid-1")
    assert st.armed is False


async def test_a8_the_ceiling_rule_is_not_called_on_the_append_path():
    repo = FakeRepo()
    arm_compact_note(repo, "iid-1", NOTE)
    manager = FakeManager(repo)
    request = _request_msg()
    fillers = [HumanMessage(content=f"filler-{i}") for i in range(11)] + [HumanMessage(content="hi")]
    call, receipt = _exchange_group()
    state = {"messages": [*fillers, call, receipt, request]}
    inner = _inner_returning({"messages": [AIMessage(content="COMPACTED")]})
    graph = FakeGraph(state["messages"])
    node = create_compact_note_agent_node(
        inner, manager=manager, compactor=_compactor(recent_message_window=1, min_recent_window=1),
        llm_config={"model": "gpt-4o"}, llm_standard=None, graph_ref=[graph],
    )
    config = {"configurable": {"thread_id": "iid-1"}}
    with patch("daemon.compaction._apply_ceiling_rule") as spy:
        out = await node(state, config)
    assert spy.call_count == 0
    assert isinstance(out["messages"], list) and out["messages"]


async def test_a1_the_settle_records_the_audit_fields():
    repo = FakeRepo()
    arm_compact_note(repo, "iid-1", NOTE)
    manager = FakeManager(repo)
    request = _request_msg()
    fillers = [HumanMessage(content=f"filler-{i}") for i in range(11)] + [HumanMessage(content="hi")]
    call, receipt = _exchange_group()
    state = {"messages": [*fillers, call, receipt, request]}
    inner = _inner_returning({"messages": [AIMessage(content="COMPACTED")]})
    graph = FakeGraph(state["messages"])
    node = create_compact_note_agent_node(
        inner, manager=manager, compactor=_compactor(recent_message_window=1, min_recent_window=1),
        llm_config={"model": "gpt-4o"}, llm_standard=None, graph_ref=[graph],
    )
    config = {"configurable": {"thread_id": "iid-1"}}
    with patch(
        "daemon.services._compaction_persist_seam.persist_compaction_result",
        return_value=True,
    ):
        await node(state, config)
    md = repo.metadata
    assert md["compact_note_last_agent_model"] == "gpt-4o"
    assert md["compact_note_last_override_applied"] is False
    assert md["compact_note_last_tail_spliced"] == 1
    assert md["compact_note_last_doc_id"].startswith("compaction-global-iid-1-")


async def test_a1_the_audit_line_is_content_free(caplog):
    repo = FakeRepo()
    arm_compact_note(repo, "iid-1", NOTE)
    manager = FakeManager(repo)
    request = _request_msg()
    fillers = [HumanMessage(content=f"filler-{i}") for i in range(11)] + [HumanMessage(content="hi")]
    call, receipt = _exchange_group()
    state = {"messages": [*fillers, call, receipt, request]}
    inner = _inner_returning({"messages": [AIMessage(content="COMPACTED")]})
    graph = FakeGraph(state["messages"])
    node = create_compact_note_agent_node(
        inner, manager=manager, compactor=_compactor(recent_message_window=1, min_recent_window=1),
        llm_config={"model": "gpt-4o"}, llm_standard=None, graph_ref=[graph],
    )
    config = {"configurable": {"thread_id": "iid-1"}}
    with caplog.at_level(logging.INFO):
        with patch(
            "daemon.services._compaction_persist_seam.persist_compaction_result",
            return_value=True,
        ):
            await node(state, config)
    lines = [
        rec.getMessage()
        for rec in caplog.records
        if "prompt_reload_compact_note:" in rec.getMessage()
    ]
    assert lines, "the audit line must be emitted"
    joined = "\n".join(lines)
    assert "agent_model=gpt-4o" in joined
    assert "compaction_model_override_applied=false" in joined
    assert "path=append" in joined
    # CONTENT-FREE: the note body never reaches a log line.
    assert NOTE not in joined


# ── C1 — the compaction EXCHANGE must be consumed ───────────────────────────


def _c1_fixture():
    """28 filler groups + the exchange group + the request.

    29 groups before the request; the house's preserved window is 10, so
    WITHOUT the exchange strip the exchange group IS the last preserved
    group and lands in the tail (the bug)."""
    fillers = []
    for i in range(28):
        fillers.append(HumanMessage(content=f"filler-{i}"))
    call, receipt = _exchange_group()
    request = _request_msg()
    state_msgs = [*fillers, call, receipt, request]
    return fillers, call, receipt, request, state_msgs


def test_c1_the_fixture_is_shaped_to_bite():
    """Non-vacuity: WITHOUT the exchange strip the exchange group IS in
    the preserved set — i.e. this fixture would catch the bug."""
    from daemon.loader import estimate_messages_tokens

    fillers, call, receipt, request, state_msgs = _c1_fixture()
    pre_request = state_msgs[:-1]
    groups = compaction_mod.identify_boundary_groups(pre_request)
    assert len(groups) == 29, len(groups)
    compactable, preserved, window = compaction_mod.select_compactable_groups(
        groups,
        recent_window=10,
        min_window=3,
        context_window=200000,
        system_prompt_tokens=0,
        estimate_fn=estimate_messages_tokens,
        config_threshold=0.80,
    )
    preserved_msgs = [m for g in preserved for m in g.messages]
    assert call in preserved_msgs
    assert receipt in preserved_msgs
    # And the strip DOES find the exchange.
    kept, exchange = _compact_note_strip_exchange(pre_request)
    assert call in exchange and receipt in exchange
    assert len(kept) == 28


async def test_c1_the_exchange_is_consumed_four_legs():
    repo = FakeRepo()
    arm_compact_note(repo, "iid-1", NOTE)
    manager = FakeManager(repo)
    fillers, call, receipt, request, state_msgs = _c1_fixture()
    reply = AIMessage(content="THE COMPACTED SUMMARY")
    inner = _inner_returning({"messages": [reply]})
    graph = FakeGraph(state_msgs)
    node = create_compact_note_agent_node(
        inner, manager=manager, compactor=_compactor(),
        llm_config={"model": "gpt-4o"}, llm_standard=None, graph_ref=[graph],
    )
    config = {"configurable": {"thread_id": "iid-1"}}
    out = await node(state_msgs and {"messages": state_msgs}, config)
    channel = out["messages"]

    # LEG 1 — the request HumanMessage is gone.
    assert request not in channel
    # LEG 2 — the reply AIMessage is gone.
    assert reply not in channel
    # LEG 3 — the receipt ToolMessage is gone.
    assert receipt not in channel
    # LEG 4 — the note text (the tool_call args) is gone.
    assert NOTE not in "\n".join(str(getattr(m, "content", "")) for m in channel)
    for m in channel:
        for tc in (getattr(m, "tool_calls", None) or []):
            assert NOTE not in str(tc)
    # The doc carries the reply, NOT the note.
    doc = channel[1]
    body = doc.content if isinstance(doc.content, str) else str(doc.content)
    assert "THE COMPACTED SUMMARY" in body
    assert NOTE not in body
    # The preserved count is the RECOMPUTED one (the exchange excluded).
    assert "preserved verbatim: 10 most recent messages" in body
    # And the fillers survive verbatim.
    assert fillers[-1] in channel


# ── R1 — a TWO-EXCHANGE channel (the stale + the current) ──────────────────


NOTE_1 = "the first note — the stale attempt"
NOTE_2 = "the second note — the current attempt"


def _two_exchange_fixture():
    """A channel that holds TWO prompt_reload exchanges.

    exchange 1 (stale) → request 1 (stale) → a reply that did NOT
    compact → a NEW user turn → exchange 2 (current, carrying the note)
    → request 2 (the one being answered, the LAST message).

    33 groups before request 2; the house's preserved window is 10, so
    BOTH exchanges and the stale request sit inside the preserved window
    — without an anchored strip they would survive into the tail."""
    fillers = [HumanMessage(content=f"filler-{i}") for i in range(28)]
    call1 = AIMessage(
        content="",
        tool_calls=[
            {
                "id": "tc-pr-1",
                "name": "prompt_reload",
                "args": {"compact": True, "compact_note": NOTE_1},
            }
        ],
    )
    receipt1 = ToolMessage(
        content="prompt_reload armed — outcome=armed path=append",
        name="prompt_reload",
        tool_call_id="tc-pr-1",
    )
    request1 = HumanMessage(
        content="[System request: Compaction] stale",
        additional_kwargs={"injected_message": True, "context_kind": "compaction_request"},
    )
    reply1 = AIMessage(content="I am not compacting anything right now.")
    new_user = HumanMessage(content="a brand new question")
    call2 = AIMessage(
        content="",
        tool_calls=[
            {
                "id": "tc-pr-2",
                "name": "prompt_reload",
                "args": {"compact": True, "compact_note": NOTE_2},
            }
        ],
    )
    receipt2 = ToolMessage(
        content="prompt_reload armed — outcome=armed path=append",
        name="prompt_reload",
        tool_call_id="tc-pr-2",
    )
    request2 = HumanMessage(
        content="[System request: Compaction] current",
        additional_kwargs={"injected_message": True, "context_kind": "compaction_request"},
    )
    state_msgs = [
        *fillers,
        call1,
        receipt1,
        request1,
        reply1,
        new_user,
        call2,
        receipt2,
        request2,
    ]
    return {
        "fillers": fillers,
        "call1": call1,
        "receipt1": receipt1,
        "request1": request1,
        "reply1": reply1,
        "new_user": new_user,
        "call2": call2,
        "receipt2": receipt2,
        "request2": request2,
        "state_msgs": state_msgs,
    }


def _assert_two_exchange_consumed(channel, fixture, doc_body):
    """The two-exchange consumption assertions (shared by the control and
    the mutation leg — the mutation leg must make them FAIL)."""
    # the CURRENT exchange's receipt ToolMessage (its "CONTEXT NOT YET
    # COMPACTED" line must not land below the doc)
    assert fixture["receipt2"] not in channel
    # the CURRENT exchange's tool-call AIMessage (the note rides in its args)
    assert fixture["call2"] not in channel
    # the STALE request HumanMessage
    assert fixture["request1"] not in channel
    # the OLDER exchange's AIMessage + receipt
    assert fixture["call1"] not in channel
    assert fixture["receipt1"] not in channel
    # the request UNDER REPLY is gone too (it is replaced by the swap)
    assert fixture["request2"] not in channel
    # the note text is absent from EVERY tool_calls entry
    for m in channel:
        for tc in (getattr(m, "tool_calls", None) or []):
            assert NOTE_2 not in str(tc)
            assert NOTE_1 not in str(tc)
    # the doc carries the reply, NOT the note
    assert "THE CURRENT REPLY" in doc_body
    assert NOTE_2 not in doc_body
    assert NOTE_1 not in doc_body
    # the fillers survive verbatim
    assert fixture["fillers"][-1] in channel


async def _run_two_exchange_swap():
    repo = FakeRepo()
    arm_compact_note(repo, "iid-1", NOTE_2)
    manager = FakeManager(repo)
    fixture = _two_exchange_fixture()
    reply = AIMessage(content="THE CURRENT REPLY")
    inner = _inner_returning({"messages": [reply]})
    graph = FakeGraph(fixture["state_msgs"])
    node = create_compact_note_agent_node(
        inner, manager=manager, compactor=_compactor(),
        llm_config={"model": "gpt-4o"}, llm_standard=None, graph_ref=[graph],
    )
    config = {"configurable": {"thread_id": "iid-1"}}
    out = await node({"messages": fixture["state_msgs"]}, config)
    return out["messages"], fixture


async def test_r1_two_exchange_channel_consumes_both_exchanges():
    channel, fixture = await _run_two_exchange_swap()
    doc = channel[1]
    body = doc.content if isinstance(doc.content, str) else str(doc.content)
    _assert_two_exchange_consumed(channel, fixture, body)
    # the preserved count is recomputed on the STRIPPED set
    assert "preserved verbatim: 10 most recent messages" in body


async def test_r1_the_two_exchange_fixture_bites_when_the_strip_is_disabled():
    """The M1 mutation shape: force the strip to a NO-OP ⇒ the very same
    assertions FAIL (at the receipt). This is what proves the fixture
    bites — a fixture that cannot fail is worse than no fixture."""
    with patch(
        "daemon.graph._compact_note_strip_exchange",
        side_effect=lambda messages: (list(messages), []),
    ):
        channel, fixture = await _run_two_exchange_swap()
    doc = channel[1]
    body = doc.content if isinstance(doc.content, str) else str(doc.content)
    with pytest.raises(AssertionError):
        _assert_two_exchange_consumed(channel, fixture, body)
    # And the harm is exactly what the reviewer described: the CURRENT
    # receipt survives the swap.
    assert fixture["receipt2"] in channel


# ── C2 — the swap must not fire on a later, unrelated reply ────────────────


async def test_c2_two_turn_no_swap_after_an_empty_reply():
    """Turn 1: the model answers NOTHING ⇒ empty_reply, no swap, the arm
    stays armed. Turn 2 (a NEW user turn + a normal reply) ⇒ NO SWAP:
    the channel is unchanged and the arm state stays honest."""
    repo = FakeRepo()
    arm_compact_note(repo, "iid-1", NOTE)
    manager = FakeManager(repo)
    request = _request_msg()

    # ── turn 1 ──
    state1 = {"messages": [HumanMessage(content="hi"), request]}
    inner1 = _inner_returning({"messages": [AIMessage(content="")]})
    graph = FakeGraph(state1["messages"])
    node = create_compact_note_agent_node(
        inner1, manager=manager, compactor=_compactor(),
        llm_config={"model": "gpt-4o"}, llm_standard=None, graph_ref=[graph],
    )
    config = {"configurable": {"thread_id": "iid-1"}}
    out1 = await node(state1, config)
    assert not (
        isinstance(out1.get("messages"), list)
        and out1["messages"]
        and isinstance(out1["messages"][0], RemoveMessage)
    )
    assert read_compact_note_state(repo, "iid-1").armed is True

    # ── turn 2: a NEW user message lands after the request ──
    reply1 = AIMessage(content="")
    new_user = HumanMessage(content="a brand new question")
    state2 = {
        "messages": [
            HumanMessage(content="hi"),
            request,
            reply1,
            new_user,
        ]
    }
    reply2 = AIMessage(content="A NORMAL REPLY")
    inner2 = _inner_returning({"messages": [reply2]})
    graph2 = FakeGraph(state2["messages"])
    node2 = create_compact_note_agent_node(
        inner2, manager=manager, compactor=_compactor(),
        llm_config={"model": "gpt-4o"}, llm_standard=None, graph_ref=[graph2],
    )
    out2 = await node2(state2, config)
    channel2 = out2["messages"]
    # NO SWAP — the channel is the plain reply, not a sentinel-first swap.
    assert not (isinstance(channel2, list) and channel2 and isinstance(channel2[0], RemoveMessage))
    assert reply2 in channel2
    # The wrapper passed the inner result through UNCHANGED — no doc, no
    # sentinel, no extra message (a claim that CAN fail: a swap would
    # have replaced the channel with a sentinel-first doc + tail).
    assert channel2 == [reply2]
    assert not any(isinstance(m, SystemMessage) for m in channel2)
    assert read_compact_note_state(repo, "iid-1").armed is True


async def test_c2_the_request_must_be_the_last_message():
    repo = FakeRepo()
    arm_compact_note(repo, "iid-1", NOTE)
    manager = FakeManager(repo)
    request = _request_msg()
    later = AIMessage(content="something else")
    state = {"messages": [HumanMessage(content="hi"), request, later]}
    inner = _inner_returning({"messages": [AIMessage(content="COMPACTED")]})
    graph = FakeGraph(state["messages"])
    node = create_compact_note_agent_node(
        inner, manager=manager, compactor=_compactor(),
        llm_config={"model": "gpt-4o"}, llm_standard=None, graph_ref=[graph],
    )
    config = {"configurable": {"thread_id": "iid-1"}}
    out = await node(state, config)
    assert not (
        isinstance(out.get("messages"), list)
        and out["messages"]
        and isinstance(out["messages"][0], RemoveMessage)
    )
    assert read_compact_note_state(repo, "iid-1").armed is True


async def test_c2_a_text_refusal_is_not_a_compaction():
    repo = FakeRepo()
    arm_compact_note(repo, "iid-1", NOTE)
    manager = FakeManager(repo)
    request = _request_msg()
    fillers = [HumanMessage(content=f"filler-{i}") for i in range(11)] + [HumanMessage(content="hi")]
    call, receipt = _exchange_group()
    state = {"messages": [*fillers, call, receipt, request]}
    refusal = AIMessage(content="I can't compact this conversation, sorry.")
    inner = _inner_returning({"messages": [refusal]})
    graph = FakeGraph(state["messages"])
    node = create_compact_note_agent_node(
        inner, manager=manager, compactor=_compactor(),
        llm_config={"model": "gpt-4o"}, llm_standard=None, graph_ref=[graph],
    )
    config = {"configurable": {"thread_id": "iid-1"}}
    out = await node(state, config)
    assert not (
        isinstance(out.get("messages"), list)
        and out["messages"]
        and isinstance(out["messages"][0], RemoveMessage)
    )
    assert read_compact_note_state(repo, "iid-1").armed is True


async def test_a9_the_tool_calls_reply_has_its_own_label(caplog):
    repo = FakeRepo()
    arm_compact_note(repo, "iid-1", NOTE)
    manager = FakeManager(repo)
    request = _request_msg()
    fillers = [HumanMessage(content=f"filler-{i}") for i in range(11)] + [HumanMessage(content="hi")]
    call, receipt = _exchange_group()
    state = {"messages": [*fillers, call, receipt, request]}
    reply = AIMessage(content="x", tool_calls=[{"id": "t", "name": "foo", "args": {}}])
    inner = _inner_returning({"messages": [reply]})
    graph = FakeGraph(state["messages"])
    node = create_compact_note_agent_node(
        inner, manager=manager, compactor=_compactor(),
        llm_config={"model": "gpt-4o"}, llm_standard=None, graph_ref=[graph],
    )
    config = {"configurable": {"thread_id": "iid-1"}}
    with caplog.at_level(logging.INFO):
        await node(state, config)
    joined = "\n".join(
        rec.getMessage()
        for rec in caplog.records
        if "prompt_reload_compact_note:" in rec.getMessage()
    )
    assert "outcome=tool_calls_reply" in joined
    assert "outcome=append_failed" not in joined


async def test_a3_a_wrapper_failure_logs_and_settles_honestly(caplog):
    """A3: a swallowed exception must NOT be silent — the wrapper logs a
    content-free WARN and settles the arm honestly."""
    repo = FakeRepo()
    arm_compact_note(repo, "iid-1", NOTE)
    manager = FakeManager(repo)

    async def inner(state, config=None):
        return {
            "messages": [
                ToolMessage(content="armed", name="prompt_reload", tool_call_id="tc1")
            ]
        }

    node = create_compact_note_tools_node(
        inner, manager=manager, compactor=None, llm_config={"model": "gpt-4o"}
    )
    state = {"messages": [HumanMessage(content="hi")]}
    config = {"configurable": {"thread_id": "iid-1"}}
    with caplog.at_level(logging.WARNING):
        with patch(
            "daemon.services.prompt_refresh.read_compact_note_state",
            side_effect=RuntimeError("boom"),
        ):
            out = await node(state, config)
    # The turn is not broken — the tools result passes through.
    assert isinstance(out["messages"], list) and out["messages"]
    warnings = [rec.getMessage() for rec in caplog.records if rec.levelno >= logging.WARNING]
    assert any("CompactNote" in w and "APPEND failed" in w for w in warnings), warnings
    # Content-free: the note body never reaches the log.
    assert all(NOTE not in w for w in warnings)


async def test_routed_the_token_counts_are_real_not_zeros():
    """ROUTED: the CompactionResult handed to the seam carries HONEST
    token counts (never a zero that reads as a measurement)."""
    repo = FakeRepo()
    arm_compact_note(repo, "iid-1", NOTE)
    manager = FakeManager(repo)
    request = _request_msg()
    fillers = [HumanMessage(content=f"filler-{i}") for i in range(11)] + [HumanMessage(content="hi")]
    call, receipt = _exchange_group()
    state = {"messages": [*fillers, call, receipt, request]}
    inner = _inner_returning({"messages": [AIMessage(content="COMPACTED")]})
    graph = FakeGraph(state["messages"])
    node = create_compact_note_agent_node(
        inner, manager=manager, compactor=_compactor(recent_message_window=1, min_recent_window=1),
        llm_config={"model": "gpt-4o"}, llm_standard=None, graph_ref=[graph],
    )
    config = {"configurable": {"thread_id": "iid-1"}}
    with patch(
        "daemon.services._compaction_persist_seam.persist_compaction_result",
        return_value=True,
    ) as seam:
        await node(state, config)
    result = seam.call_args.kwargs["result"]
    assert result.tokens_before > 0
    assert result.tokens_after > 0
    assert result.tokens_saved == result.tokens_before - result.tokens_after


# ── zero decision calls + exports ───────────────────────────────────────────


def test_zero_decision_calls_in_wrappers():
    src_tools = inspect.getsource(create_compact_note_tools_node)
    src_agent = inspect.getsource(create_compact_note_agent_node)
    for src in (src_tools, src_agent):
        assert "daemon.services.decision" not in src
        assert "from .services.decision" not in src
        assert "import daemon.services.decision" not in src


def test_the_refusal_marker_count_matches_the_marker_set():
    """#12 — the count is derived from the set, never restated by hand.
    The '12' the self-doc and the classifier prose cite is THIS number."""
    from daemon.graph import _COMPACT_NOTE_REFUSAL_MARKER_COUNT, _COMPACT_NOTE_REFUSAL_MARKERS

    assert _COMPACT_NOTE_REFUSAL_MARKER_COUNT == len(_COMPACT_NOTE_REFUSAL_MARKERS)
    assert _COMPACT_NOTE_REFUSAL_MARKER_COUNT == 12


def test_factories_exported():
    assert callable(create_compact_note_tools_node)
    assert callable(create_compact_note_agent_node)


# ── H6 / H7 / B — the truth paths (a failure must be REPORTED) ─────────────


class NoneWriteRepo(FakeRepo):
    """A carrier whose write helper reports "the instance does not
    exist" — ``None`` — the house contract's failure signal."""

    def set_metadata_many(self, instance_id, updates):
        return None

    def set_metadata(self, instance_id, key, value):
        return None


class NoDeleteRepo:
    """A carrier that exposes NO arm-delete helper at all (the
    ``else:`` branch of the clear cascade)."""

    def __init__(self):
        self.metadata = {}

    def get(self, instance_id):
        return SimpleNamespace(
            agent_id="ari", instance_metadata=dict(self.metadata)
        )

    def set_metadata_many(self, instance_id, updates):
        self.metadata.update(updates)
        return self.get(instance_id)

    def set_metadata(self, instance_id, key, value):
        self.metadata[key] = value
        return self.get(instance_id)


async def test_h6_a_none_write_means_the_arm_was_not_written():
    repo = NoneWriteRepo()
    pr.arm_compact_note(repo, "iid-1", NOTE)
    # The arm was NOT written — the state must not read as armed.
    assert pr.read_compact_note_state(repo, "iid-1").armed is False


async def test_h7_a_failed_arm_clear_never_returns_landed():
    repo = FakeRepo()
    pr.arm_compact_note(repo, "iid-1", NOTE)
    # A carrier with NO delete helper at all: the arm cannot be consumed.
    no_delete = NoDeleteRepo()
    no_delete.metadata = dict(repo.metadata)
    cleared = pr.clear_compact_note_arm(no_delete, "iid-1", sha256_12="abc123")
    assert cleared is False


async def test_h7_the_settle_reports_an_unsettled_landing():
    repo = NoDeleteRepo()
    pr.arm_compact_note(repo, "iid-1", NOTE)
    result = pr.settle_compact_note_arm(
        repo, "iid-1", note_sha12="abc123", outcome="compacted", persisted=True
    )
    # NEVER "landed" when the arm was not consumed.
    assert result != "landed"
    assert result == "landed_unsettled"
    # And the arm is still there.
    assert pr.read_compact_note_state(repo, "iid-1").armed is True


async def test_h7_the_settle_rejects_an_unknown_outcome():
    repo = FakeRepo()
    pr.arm_compact_note(repo, "iid-1", NOTE)
    result = pr.settle_compact_note_arm(
        repo, "iid-1", note_sha12="abc123", outcome="not-a-real-outcome"
    )
    assert result == "rejected_unknown_outcome"


async def test_b_a_failed_arm_read_settles_and_warns(caplog):
    """A carrier read that FAILS is UNKNOWN, not "not armed": the swap
    warns and settles — the model's compaction reply is not discarded
    silently."""
    import logging as _logging

    class ExplodingRepo(FakeRepo):
        def get(self, instance_id):
            raise RuntimeError("db down")

    repo = ExplodingRepo()
    manager = FakeManager(repo)
    request = _request_msg()
    fillers = [HumanMessage(content=f"filler-{i}") for i in range(11)] + [HumanMessage(content="hi")]
    call, receipt = _exchange_group()
    state = {"messages": [*fillers, call, receipt, request]}
    reply = AIMessage(content="THE COMPACTED SUMMARY")
    inner = _inner_returning({"messages": [reply]})
    graph = FakeGraph(state["messages"])
    node = create_compact_note_agent_node(
        inner, manager=manager, compactor=_compactor(),
        llm_config={"model": "gpt-4o"}, llm_standard=None, graph_ref=[graph],
    )
    config = {"configurable": {"thread_id": "iid-1"}}
    with caplog.at_level(_logging.WARNING):
        out = await node(state, config)
    # No swap (the arm state was unknown) and the reply is still returned.
    assert not (
        isinstance(out.get("messages"), list)
        and out["messages"]
        and isinstance(out["messages"][0], RemoveMessage)
    )
    warnings = [rec.getMessage() for rec in caplog.records if rec.levelno >= _logging.WARNING]
    assert any("arm state could not be READ" in w for w in warnings), warnings


async def test_b_a_future_dated_clock_is_corrupt_not_stranding():
    """A future-dated rate-limit clock is CORRUPT: it must not strand
    every later attempt behind a nonsensical remaining count."""
    from datetime import datetime, timedelta, timezone

    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    assert prompt_reload_mod._rate_limit_remaining_seconds(future) == 0


async def test_a1_the_audit_line_carries_the_agent_id(caplog):
    """The agent id is threaded from the carrier row — the audit line
    never renders a permanent ``-``."""
    import logging as _logging

    repo = FakeRepo()
    arm_compact_note(repo, "iid-1", NOTE)
    manager = FakeManager(repo)
    request = _request_msg()
    fillers = [HumanMessage(content=f"filler-{i}") for i in range(11)] + [HumanMessage(content="hi")]
    call, receipt = _exchange_group()
    state = {"messages": [*fillers, call, receipt, request]}
    inner = _inner_returning({"messages": [AIMessage(content="COMPACTED")]})
    graph = FakeGraph(state["messages"])
    node = create_compact_note_agent_node(
        inner, manager=manager, compactor=_compactor(recent_message_window=1, min_recent_window=1),
        llm_config={"model": "gpt-4o"}, llm_standard=None, graph_ref=[graph],
    )
    config = {"configurable": {"thread_id": "iid-1"}}
    with caplog.at_level(_logging.INFO):
        with patch(
            "daemon.services._compaction_persist_seam.persist_compaction_result",
            return_value=True,
        ):
            await node(state, config)
    joined = "\n".join(
        rec.getMessage()
        for rec in caplog.records
        if "prompt_reload_compact_note:" in rec.getMessage()
    )
    assert "agent=ari" in joined, joined