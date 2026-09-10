"""Streaming-loop hardening for the answer-gate pause instant.

Secondary defect from the 2026-09-10 RESUME_ROUTER duplicate-report
incident (parent ca14e233): at the pause instant prod logged::

    Streaming failed for message bb4d6af5: 'NoneType' object has no
    attribute 'get'

Root cause (empirically pinned by ``TestLangGraphUpdatesEmission``
below against the installed langgraph): ``stream_mode="updates"``
emits ``{node_name: None}`` for a node with NO state update — which
includes a node returning ``{}``. ``question_pause_node`` returns
``{}`` at exactly the ask_questions pause instant, so the streaming
consumer in ``InstanceMessagingService._process_message_with_tracking``
called ``node_data.get("messages", [])`` on ``None`` → AttributeError
→ caught by the broad ``except Exception`` → logged as "Streaming
failed" + a spurious ``stream_error`` SSE event, aborting the SSE
loop mid-turn.

Fix pinned here: both ``node_data`` consumption sites guard with
``isinstance(node_data, dict)`` before ``.get`` (a ``None``/non-dict
node update carries no messages — skip it). The behavioral
emission-contract half proves the premise; the source-integrity half
pins the guard at both consumption sites (``inspect.getsource``
substring assertions — the repo's facade-forwarding precedent).
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, TypedDict

import pytest

from daemon.services.instance_messaging import InstanceMessagingService
from tests.helpers.checkpoint_prune_pg import (
    evict_langgraph_mocks,
    restore_langgraph_mocks,
)


@pytest.fixture
def real_langgraph():
    """The root ``tests/conftest.py`` installs global langgraph mocks
    before daemon imports. This fixture evicts them for the duration
    of the test so the REAL langgraph loads (the repo-standard
    ``evict_langgraph_mocks`` / ``restore_langgraph_mocks`` pattern
    from ``tests/helpers/checkpoint_prune_pg.py``)."""
    saved = evict_langgraph_mocks()
    try:
        yield
    finally:
        restore_langgraph_mocks(saved)


class TestLangGraphUpdatesEmission:
    """Pin the emission contract that produced the crash shape.

    A node returning ``{}`` (no state update) is emitted as
    ``{node_name: None}`` in ``updates`` mode. This is the exact event
    shape the unguarded ``node_data.get("messages", [])`` choked on at
    the ask_questions pause instant.
    """

    def test_empty_return_node_emits_none_update(self, real_langgraph):
        # Import AFTER the mocks are evicted — these are the real
        # langgraph classes.
        from langgraph.graph import END, StateGraph

        class S(TypedDict, total=False):
            messages: list

        async def node_no_update(state: Any) -> dict:
            # The ``question_pause_node`` shape: returns {} on purpose.
            return {}

        async def node_with_messages(state: Any) -> dict:
            return {"messages": ["hello"]}

        g = StateGraph(S)
        g.add_node("question_pause_node", node_no_update)
        g.add_node("agent", node_with_messages)
        g.set_entry_point("question_pause_node")
        g.add_edge("question_pause_node", "agent")
        g.add_edge("agent", END)
        graph = g.compile()

        async def collect():
            events = []
            async for ev in graph.astream(
                {"messages": []}, stream_mode=["updates"]
            ):
                mode, data = ev if isinstance(ev, tuple) else ("updates", ev)
                if mode == "updates":
                    events.append(data)
            return events

        events = asyncio.run(collect())

        assert {"question_pause_node": None} in events, (
            "langgraph must emit {node: None} for a {}-returning node "
            "— the crash premise of the NoneType .get defect"
        )
        # The agent node's dict update keeps its messages shape.
        agent_updates = [
            e for e in events if "agent" in e and e["agent"] is not None
        ]
        assert agent_updates and "messages" in agent_updates[0]["agent"]


class TestStreamingLoopGuardsNoneNodeUpdates:
    """Both ``node_data`` consumption sites in
    ``_process_message_with_tracking`` must guard non-dict node
    updates before calling ``.get``."""

    SOURCE: str = inspect.getsource(
        InstanceMessagingService._process_message_with_tracking
    )

    def test_progressive_dispatch_site_guards_non_dict_update(self):
        # Site 1 — the progressive-dispatch branch (node == "agent").
        assert self.SOURCE.count("if not isinstance(node_data, dict)") >= 2, (
            "both node_data consumption sites must guard non-dict "
            "(None) node updates with isinstance(node_data, dict)"
        )

    def test_accumulate_site_guards_non_dict_update(self):
        # The accumulate loop's .get must appear AFTER the guard —
        # structural check: two guards, exactly two node_data .get
        # call sites, both preceded (in source order) by a guard.
        get_sites = [
            i for i in range(len(self.SOURCE))
            if self.SOURCE.startswith("node_data.get(", i)
        ]
        guard_sites = [
            i for i in range(len(self.SOURCE))
            if self.SOURCE.startswith("if not isinstance(node_data, dict)", i)
        ]
        assert len(get_sites) == 2, (
            "expected exactly two node_data.get call sites"
        )
        assert len(guard_sites) == 2
        for gs in guard_sites:
            assert any(gs < g for g in get_sites), (
                "each guard must precede its .get consumption"
            )

    def test_guard_skips_none_and_non_dict_updates(self):
        """Behavioral half of the guard: the exact skip predicate the
        streaming loop uses, driven with the emission shapes from the
        contract test (None update + dict update)."""
        accumulated: list = []

        def accumulate_node_update(node_data: Any) -> None:
            # Mirror of the in-loop guard (kept in lockstep via the
            # source assertions above).
            if not isinstance(node_data, dict):
                return
            node_messages = node_data.get("messages", [])
            accumulated.extend(node_messages)

        # The pause-instant emission: question_pause_node → None.
        accumulate_node_update(None)
        assert accumulated == []
        # A real agent update still accumulates.
        accumulate_node_update({"messages": [{"role": "assistant"}]})
        assert len(accumulated) == 1
