"""Integration tests: injection echo_id continuity across the full path.

message-display-latency Phase 1 — pins the three-hop id + timestamp
continuity contract:

    POST-time ``user_message`` event (router, unit-covered)
        → drain-time re-emit (``agent_node``, SAME id + SAME POST ts)
        → LangGraph checkpoint commit
        → GET /messages returns the SAME stable ``message_id``.

The drain runs the REAL ``create_agent_node`` closure (stub LLM + stub
RAM injection slot) inside a compiled LangGraph backed by a real
``MemorySaver`` checkpointer, so the checkpoint round-trip (serialize →
commit → read back via ``daemon.persistence.get_instance_messages``) is
exercised end-to-end — not stubbed.

``tests/conftest.py`` poisons ``sys.modules`` with mock langgraph modules
for the unit gate. This module swaps in the REAL langgraph for its own
duration (setup_module) and restores the mocks afterwards
(teardown_module) — same pattern as ``test_message_queue_e2e.py``, but
scoped to this module so ordering anywhere in a run is safe.

Covered here:

    1. ``TestInjectThenDrainIdContinuity`` — POST-entry {content,
       timestamp, echo_id} drains into ``HumanMessage(id=echo_id)``;
       the drain re-emit carries the SAME id + POST timestamp; the
       checkpointed message comes back from ``get_instance_messages``
       with ``message_id == echo_id`` (the GET id-stability fix).
    2. ``TestNQueuedInjectionsFifo`` — N queued injections drain as N
       per-entry ``user_message`` re-emits in FIFO order, each reusing
       its own entry's id; then ONE ``injection_consumed``.
    3. ``TestMidToolCallPairingGuardUnaffected`` — a poisoned state
       tail (``AIMessage`` with unanswered ``tool_calls``) + an
       echo_id-tagged injection still synthesizes the placeholder
       ``ToolMessage`` between AI and Human (``graph.py`` pairing
       guard), and the injected ``HumanMessage`` keeps its id.
"""

from __future__ import annotations

import importlib
import sys
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


# ---------------------------------------------------------------------------
# Real-langgraph swap (module-scoped; restores conftest mocks after)
# ---------------------------------------------------------------------------

_MOCKED_LANGGRAPH_KEYS = [
    "langgraph",
    "langgraph.graph",
    "langgraph.graph.state",
    "langgraph.prebuilt",
    "langgraph.constants",
    "langgraph.checkpoint",
    "langgraph.checkpoint.memory",
    "langgraph.checkpoint.sqlite",
    "langgraph.checkpoint.sqlite.aio",
]

_saved_mocks: dict[str, Any] = {}
_lg_graph: Any = None
_lg_memory: Any = None


def setup_module(module: Any) -> None:
    """Swap the conftest's mock langgraph modules for the real ones."""
    global _lg_graph, _lg_memory

    for key in _MOCKED_LANGGRAPH_KEYS:
        if key in sys.modules:
            _saved_mocks[key] = sys.modules[key]
            del sys.modules[key]
    # Drop any real langgraph children cached from a previous swap so the
    # re-import is coherent (parents were just deleted).
    for key in [k for k in sys.modules if k.startswith("langgraph")]:
        del sys.modules[key]

    _lg_graph = importlib.import_module("langgraph.graph")
    _lg_memory = importlib.import_module("langgraph.checkpoint.memory")


def teardown_module(module: Any) -> None:
    """Restore the conftest mocks exactly as we found them."""
    for key in [k for k in sys.modules if k.startswith("langgraph")]:
        del sys.modules[key]
    sys.modules.update(_saved_mocks)
    _saved_mocks.clear()


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _echo_id() -> str:
    """Mint a UUID4 the way the router does at POST time."""
    return str(uuid.uuid4())


class _StubInjectionSlot:
    """In-memory stand-in for the RAM injection slot handle."""

    def __init__(self, initial: dict[str, list[dict[str, str]]] | None = None):
        self._store: dict[str, list[dict[str, str]]] = dict(initial or {})

    def get(self, instance_id: str) -> list[dict[str, str]] | None:
        queue = self._store.get(instance_id)
        if not queue:
            return None
        return list(queue)

    def clear(self, instance_id: str) -> list[dict[str, str]] | None:
        return self._store.pop(instance_id, None)


class _StubLLM:
    """Records the messages it was called with; returns a fixed AIMessage."""

    def __init__(self):
        self.calls: list[list[Any]] = []

    def invoke(self, messages):
        self.calls.append(list(messages))
        return AIMessage(content="stub reply")


def _build_graph(instance_id: str, entries: list[dict[str, str]]):
    """Compile a 1-node graph around the REAL ``create_agent_node`` closure.

    Mirrors the production wiring shape (agent node + MessagesState +
    checkpointer) with stub LLM / slot / hub — so the node's checkpoint
    commit is the real LangGraph ``add_messages`` path.
    """
    from daemon.graph import create_agent_node

    llm = _StubLLM()
    slot = _StubInjectionSlot(initial={instance_id: list(entries)})
    hub = MagicMock()
    hub.stream_message = AsyncMock()

    agent_node = create_agent_node(
        llm_with_tools=llm,
        system_prompt="you are a test assistant",
        compactor=None,
        graph_ref=[None],
        config=None,
        llm_config={"model": "stub", "model_vision": None},
        retry_config={"transient_attempts": 1, "timeout_attempts": 1},
        llm_standard=None,
        injection_slot=slot,
        live_hub=hub,
    )

    builder = _lg_graph.StateGraph(_lg_graph.MessagesState)
    builder.add_node("agent", agent_node)
    builder.add_edge(_lg_graph.START, "agent")
    builder.add_edge("agent", _lg_graph.END)

    checkpointer = _lg_memory.MemorySaver()
    graph = builder.compile(checkpointer=checkpointer)
    return graph, llm, slot, hub, checkpointer


# ---------------------------------------------------------------------------
# 1. Inject → drain → checkpoint id continuity
# ---------------------------------------------------------------------------


class TestInjectThenDrainIdContinuity:
    """The full echo_id continuity chain: entry → HumanMessage.id → drain
    re-emit → checkpoint → GET /messages."""

    @pytest.mark.asyncio
    async def test_id_and_timestamp_survive_all_three_hops(self):
        instance_id = "inst-echo-1"
        post_ts = "2026-08-30T01:02:03+00:00"
        eid = _echo_id()
        entries = [{
            "content": "hello mid-turn",
            "timestamp": post_ts,
            "echo_id": eid,
        }]

        graph, llm, _slot, hub, checkpointer = _build_graph(instance_id, entries)

        config = {"configurable": {"thread_id": instance_id}}
        await graph.ainvoke({"messages": []}, config=config)

        # ---- Hop 1: drain built HumanMessage(id=echo_id) for the LLM ----
        drained = llm.calls[0][-1]
        assert isinstance(drained, HumanMessage)
        assert drained.id == eid
        assert drained.content == "hello mid-turn"
        assert drained.additional_kwargs == {"injected_message": True}

        # ---- Hop 2: drain-time SSE re-emit reuses id + POST timestamp ----
        user_calls = [
            c for c in hub.stream_message.await_args_list
            if c.kwargs.get("event_type") == "user_message"
        ]
        assert len(user_calls) == 1
        payload = user_calls[0].kwargs["message"]
        assert payload["message_id"] == eid
        assert payload["created_at"] == post_ts
        assert payload["role"] == "user"
        assert payload["instance_id"] == instance_id

        # ---- Hop 3: checkpoint read-back returns the SAME id ----
        # This is the exact read path GET /instances/{id}/messages uses.
        from daemon.persistence import get_instance_messages

        history = await get_instance_messages(checkpointer, instance_id)
        user_rows = [m for m in history if m.get("role") == "user"]
        assert len(user_rows) == 1
        assert user_rows[0]["message_id"] == eid, (
            "GET /messages must return the STABLE echo_id for the injected "
            "message (id-stability fix) — got a freshly minted id instead"
        )
        assert user_rows[0]["content"] == "hello mid-turn"

        # A second read returns the SAME id (no per-read random re-mint).
        history_again = await get_instance_messages(checkpointer, instance_id)
        user_rows_again = [m for m in history_again if m.get("role") == "user"]
        assert user_rows_again[0]["message_id"] == eid

    @pytest.mark.asyncio
    async def test_tool_path_entry_gets_pipeline_minted_id(self):
        """Tool-path back-compat (entries WITHOUT echo_id): the drain
        builds ``HumanMessage(id=None)`` (unit-pinned in
        ``tests/test_injection_graph.py``); under a real compiled graph
        the ``add_messages`` reducer mints its own uuid for the commit,
        so the checkpointed message reads back with a pipeline-assigned
        uuid4 — NOT an id we control. This is the asymmetry the echo_id
        threading fixes for the user-API path: only echo_id entries
        give GET /messages an id correlated with the POST-time event.
        """
        instance_id = "inst-echo-toolpath"
        entries = [{"content": "from tool", "timestamp": "2026-08-30T00:00:00+00:00"}]

        graph, llm, _slot, hub, checkpointer = _build_graph(instance_id, entries)
        await graph.ainvoke(
            {"messages": []},
            config={"configurable": {"thread_id": instance_id}},
        )

        from daemon.persistence import get_instance_messages

        history = await get_instance_messages(checkpointer, instance_id)
        user_rows = [m for m in history if m.get("role") == "user"]
        assert len(user_rows) == 1
        # The read-back id is a valid uuid minted by the pipeline (reducer
        # / serializer) — no server-minted POST-time id exists on this path.
        assert uuid.UUID(user_rows[0]["message_id"]).version == 4
        assert user_rows[0]["content"] == "from tool"


# ---------------------------------------------------------------------------
# 2. N queued injections → N POST echoes (unit-covered) + N drain re-emits
# ---------------------------------------------------------------------------


class TestNQueuedInjectionsFifo:
    """N queued injections drain as N per-entry re-emits in FIFO order,
    each collapsing onto its own POST-time bubble id."""

    @pytest.mark.asyncio
    async def test_three_injections_reemit_in_fifo_with_own_ids(self):
        instance_id = "inst-echo-n"
        pairs = [
            ("first", "2026-08-30T00:00:00+00:00"),
            ("second", "2026-08-30T00:00:01+00:00"),
            ("third", "2026-08-30T00:00:02+00:00"),
        ]
        entries = [
            {"content": c, "timestamp": ts, "echo_id": _echo_id()}
            for c, ts in pairs
        ]

        graph, _llm, _slot, hub, _checkpointer = _build_graph(instance_id, entries)
        await graph.ainvoke(
            {"messages": []},
            config={"configurable": {"thread_id": instance_id}},
        )

        user_calls = [
            c for c in hub.stream_message.await_args_list
            if c.kwargs.get("event_type") == "user_message"
        ]
        assert len(user_calls) == 3

        # FIFO order, same id + same POST stamp per entry.
        for call, (content, ts), entry in zip(user_calls, pairs, entries):
            payload = call.kwargs["message"]
            assert payload["content"] == content
            assert payload["message_id"] == entry["echo_id"]
            assert payload["created_at"] == ts

        # Distinct ids — no cross-entry collapse.
        ids = {c.kwargs["message"]["message_id"] for c in user_calls}
        assert len(ids) == 3

        # Exactly ONE injection_consumed closes the lifecycle.
        consumed_calls = [
            c for c in hub.stream_message.await_args_list
            if c.kwargs.get("event_type") == "injection_consumed"
        ]
        assert len(consumed_calls) == 1
        assert consumed_calls[0].kwargs["message"]["pending_count"] == 3


# ---------------------------------------------------------------------------
# 3. Mid-tool-call pairing guard unaffected by the id change
# ---------------------------------------------------------------------------


class TestMidToolCallPairingGuardUnaffected:
    """An echo_id-tagged injection draining behind a poisoned state tail
    (``AIMessage`` with unanswered ``tool_calls``) still gets the
    placeholder ``ToolMessage`` synthesized between AI and Human — the
    pairing guard is orthogonal to the id threading."""

    @pytest.mark.asyncio
    async def test_placeholder_still_synthesized_and_id_preserved(self):
        instance_id = "inst-echo-pairing"
        eid = _echo_id()
        entries = [{
            "content": "injected behind poisoned tail",
            "timestamp": "2026-08-30T00:00:00+00:00",
            "echo_id": eid,
        }]

        graph, llm, _slot, hub, _checkpointer = _build_graph(instance_id, entries)

        poisoned_tail = AIMessage(
            content="",
            tool_calls=[{
                "id": "call_mid_1",
                "name": "bash",
                "args": {"x": 1},
                "type": "tool_call",
            }],
        )
        await graph.ainvoke(
            {"messages": [poisoned_tail]},
            config={"configurable": {"thread_id": instance_id}},
        )

        seen = llm.calls[0]
        # Expected shape: [System, AIMessage(tool_calls), ToolMessage
        # (placeholder), HumanMessage(injected)]
        assert isinstance(seen[1], AIMessage)
        assert seen[1].tool_calls, "poisoned tail must reach the LLM call"
        assert isinstance(seen[2], ToolMessage)
        assert seen[2].tool_call_id == "call_mid_1"
        assert isinstance(seen[3], HumanMessage)
        assert seen[3].id == eid
        assert seen[3].content == "injected behind poisoned tail"
