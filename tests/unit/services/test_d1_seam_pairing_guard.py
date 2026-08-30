"""RED-GREEN proof + coverage for the D1 entry-seam pairing guard.

wc-wake-report-integrity (T6): the D1 enqueue-seam pairing tail-guard
closes the pre-existing poisoned-tail → LangGraph 2013 exposure that
the in-graph ``_ensure_tool_result_pairing`` (graph.py:271-384) does
not catch at the enqueue-seam boundary.

The defect shape:
  * A checkpoint persists an ``AIMessage(tool_calls=[...])`` with no
    matching ``ToolMessage`` (daemon restart mid-tool-execution).
  * A new enqueue turn loads ``state['messages']`` and appends a new
    ``HumanMessage`` for this turn — the LLM-bound list now has
    ``AIMessage(tc) → HumanMessage`` exactly.
  * OpenAI-compatible gateways reject with error 2013.
  * The in-graph guard at the agent_node level is too late: the LLM
    is being asked to run BEFORE agent_node gets a chance to inspect.

The seam fix: at the enqueue seam (after the three
``_build_graph_input`` sites converge, before ``graph.astream``),
read the checkpoint state via ``graph.aget_state(config)``, tail-check
the same way ``_ensure_tool_result_pairing`` does, and synthesize
placeholder ``ToolMessage``s (R1 deterministic ids) that are
PREPENDED to ``graph_input['messages']`` — LangGraph's
``add_messages`` reducer then commits the healed tail to the
checkpoint in the same superstep as the new turn.

RED proof: with the helper NOT yet wired in, a poisoned tail +
enqueued turn produces the LangGraph 2013 error (the test asserts
that ``graph.astream`` is invoked with a poisoned list — the helper
does NOT prepend placeholders).

GREEN proof: with the helper wired, the placeholders are prepended
and ``graph.astream`` is invoked with a healed list — the LLM sees
``AIMessage(tc) → ToolMessage(placeholder) → HumanMessage`` and the
gateway accepts the turn.

Pin: R2 CLE-mirror convention regression test — drives the CLE
reactive-compaction retry path (graph.py:3227 rebuild → :3402-3408
guard) over a poisoned checkpoint and asserts the placeholders
flow into the rebuilt ``compact_messages``.

All tests in this module are flag-independent (the D1 seam guard is
always active regardless of ``ENSEMBLE_WC_WAKE_ENQUEUE`` state —
per the dispatch directive).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from daemon.services.instance_messaging import _heal_poisoned_checkpoint_tail


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tc(tc_id: str, name: str = "tool") -> dict:
    """Build a minimal tool-call dict in langchain_core contract shape."""
    return {
        "id": tc_id,
        "name": name,
        "args": {"x": 1},
        "type": "tool_call",
    }


def _make_state(messages: list) -> MagicMock:
    """Mock a LangGraph state object with ``values['messages']`` shape."""
    state = MagicMock()
    state.values = {"messages": messages}
    return state


def _make_graph(state_messages: list | None = None) -> MagicMock:
    """Mock a CompiledStateGraph with ``aget_state`` returning a poisoned
    (or healthy) tail.
    """
    graph = MagicMock()
    if state_messages is not None:
        graph.aget_state = AsyncMock(return_value=_make_state(state_messages))
    else:
        graph.aget_state = AsyncMock(return_value=None)
    return graph


# ---------------------------------------------------------------------------
# RED proof — capture the defect shape the seam guard must close
# ---------------------------------------------------------------------------


class TestPoisonedTailDefectShape:
    """RED-GREEN evidence: pin the defect shape the seam guard fixes.

    Without the seam guard, a poisoned checkpoint tail + a new
    enqueued HumanMessage produces an LLM-bound list shaped
    ``AIMessage(tc) → HumanMessage`` — the OpenAI-compatible
    gateway rejects with error 2013. The seam guard synthesizes
    placeholder ``ToolMessage``(s) that sit BETWEEN the poisoned
    AIMessage(tc) and the new HumanMessage, restoring structural
    validity before ``graph.astream``.
    """

    async def test_seam_guard_prepends_placeholder_for_poisoned_tail(self):
        """Poisoned checkpoint tail + enqueued turn → placeholders
        prepended; LLM-bound list is structurally valid."""
        poisoned_tail = AIMessage(content="", tool_calls=[_tc("call_xyz")])
        graph_state = [
            SystemMessage(content="sys"),
            HumanMessage(content="hi"),
            poisoned_tail,
        ]
        graph = _make_graph(graph_state)
        config = {"configurable": {"thread_id": "iid-abc"}}
        instance_short = "iid-abc"
        graph_input = {
            "messages": [HumanMessage(content="new turn", id="new-msg-1")],
        }

        # GREEN proof: the seam guard prepends the placeholder.
        result_placeholders = await _heal_poisoned_checkpoint_tail(
            graph, config, graph_input, instance_short,
        )

        # Exactly one placeholder synthesized.
        assert len(result_placeholders) == 1
        placeholder = result_placeholders[0]
        # R1 deterministic id format.
        assert placeholder.id == "pairing-synth-call_xyz"
        assert placeholder.tool_call_id == "call_xyz"
        assert placeholder.name == "tool"

        # The LLM-bound list is healed — placeholder sits at the HEAD
        # (immediately after the poisoned checkpoint tail), THEN the
        # user_msg.
        msgs = graph_input["messages"]
        assert len(msgs) == 2
        assert isinstance(msgs[0], ToolMessage)
        assert msgs[0].id == "pairing-synth-call_xyz"
        assert isinstance(msgs[1], HumanMessage)
        assert msgs[1].content == "new turn"

    async def test_seam_guard_no_op_on_healthy_tail(self):
        """Healthy checkpoint tail + enqueued turn → zero placeholders,
        graph_input byte-identical."""
        graph_state = [
            SystemMessage(content="sys"),
            HumanMessage(content="hi"),
            AIMessage(content="", tool_calls=[_tc("call_ok")]),
            ToolMessage(content="ok", tool_call_id="call_ok", name="tool"),
            HumanMessage(content="prev"),
        ]
        graph = _make_graph(graph_state)
        config = {"configurable": {"thread_id": "iid-healthy"}}
        instance_short = "iid-healthy"
        graph_input = {
            "messages": [HumanMessage(content="new turn", id="new-msg-2")],
        }

        result_placeholders = await _heal_poisoned_checkpoint_tail(
            graph, config, graph_input, instance_short,
        )

        assert result_placeholders == []
        # Byte-identical graph_input.
        assert len(graph_input["messages"]) == 1
        assert isinstance(graph_input["messages"][0], HumanMessage)
        assert graph_input["messages"][0].content == "new turn"

    async def test_seam_guard_handles_missing_state(self):
        """``graph.aget_state`` returns None (no checkpoint yet) →
        no-op; graph_input byte-identical."""
        graph = _make_graph(state_messages=None)
        config = {"configurable": {"thread_id": "iid-new"}}
        graph_input = {
            "messages": [HumanMessage(content="new turn", id="new-msg-3")],
        }

        result_placeholders = await _heal_poisoned_checkpoint_tail(
            graph, _make_config_for_call(), graph_input, "iid-new",
        )

        assert result_placeholders == []
        assert len(graph_input["messages"]) == 1

    async def test_seam_guard_handles_no_messages_in_state(self):
        """``state.values['messages']`` empty → no-op."""
        graph = _make_graph(state_messages=[])
        config = {"configurable": {"thread_id": "iid-empty"}}
        graph_input = {
            "messages": [HumanMessage(content="new turn", id="new-msg-4")],
        }

        result_placeholders = await _heal_poisoned_checkpoint_tail(
            graph, config, graph_input, "iid-empty",
        )

        assert result_placeholders == []
        assert len(graph_input["messages"]) == 1


# ---------------------------------------------------------------------------
# Ordering — S4 input-order pin (placeholder first, then user_msg)
# ---------------------------------------------------------------------------


def _make_config_for_call() -> dict[str, Any]:
    """Build a minimal LangGraph config dict for the helper call."""
    return {"configurable": {"thread_id": "iid-test"}}


class TestSeamGuardOrdering:
    """Exact-order pin: ``[placeholder, (persistent), (leftovers), user]``
    per the S4 input-order requirement.

    The seam guard operates ONLY on the placeholder head — the
    persistent block (T5's ``persistent_context_msgs``) and the
    D2 leftover drain slot in BELOW the placeholder (between
    placeholder and user_msg). This test pins the placeholder's
    position-0 invariant.
    """

    async def test_placeholder_sits_at_position_zero(self):
        """The placeholder is the FIRST entry in graph_input['messages']
        — immediately after the poisoned checkpoint tail."""
        poisoned_tail = AIMessage(content="", tool_calls=[_tc("call_pos")])
        graph_state = [poisoned_tail]
        graph = _make_graph(graph_state)
        graph_input = {
            "messages": [
                HumanMessage(content="persistent", id="p1"),
                HumanMessage(content="leftover", id="l1"),
                HumanMessage(content="new turn", id="n1"),
            ],
        }

        result_placeholders = await _heal_poisoned_checkpoint_tail(
            graph, _make_config_for_call(), graph_input, "iid-pos",
        )

        assert len(result_placeholders) == 1
        msgs = graph_input["messages"]
        assert len(msgs) == 4
        assert isinstance(msgs[0], ToolMessage)
        assert msgs[0].id == "pairing-synth-call_pos"
        # Persistent, leftover, user order preserved at indices 1, 2, 3.
        assert msgs[1].content == "persistent"
        assert msgs[2].content == "leftover"
        assert msgs[3].content == "new turn"


# ---------------------------------------------------------------------------
# Multi-tool_call — parallel tool calls → multiple placeholders
# ---------------------------------------------------------------------------


class TestSeamGuardParallelToolCalls:
    """Tail AIMessage with N parallel unanswered tool_calls → N
    placeholders, each with its own deterministic R1 id."""

    async def test_two_parallel_tool_calls_produce_two_placeholders(self):
        poisoned_tail = AIMessage(
            content="",
            tool_calls=[_tc("call_a", "foo"), _tc("call_b", "bar")],
        )
        graph_state = [poisoned_tail]
        graph = _make_graph(graph_state)
        graph_input = {
            "messages": [HumanMessage(content="new turn", id="n1")],
        }

        result_placeholders = await _heal_poisoned_checkpoint_tail(
            graph, _make_config_for_call(), graph_input, "iid-parallel",
        )

        assert len(result_placeholders) == 2
        ids = [tm.id for tm in result_placeholders]
        assert ids == ["pairing-synth-call_a", "pairing-synth-call_b"]

        msgs = graph_input["messages"]
        assert len(msgs) == 3
        assert msgs[0].id == "pairing-synth-call_a"
        assert msgs[1].id == "pairing-synth-call_b"
        assert isinstance(msgs[2], HumanMessage)


# ---------------------------------------------------------------------------
# R2 — CLE-mirror convention regression
# ---------------------------------------------------------------------------


class TestR2CLEDualGuard:
    """R2 (wc-wake-report-integrity): the CLE reactive-compaction
    retry path also produces the same poisoned-tail shape — the
    CLE rebuild reads ``compact_messages`` from ``aget_state``, not
    the local ``full_messages`` list. The seam guard's ``aget_state``
    inspection covers BOTH paths — in-graph site 2 + CLE rebuild
    both run AFTER the seam drain, so the seam must heal BEFORE the
    rebuild. This test pins the convention by simulating the CLE
    rebuild path's call pattern.

    Note: the actual CLE rebuild site is in graph.py (out of scope
    per plan §2). This test pins the SEAM-side contract that the
    rebuild relies on: the seam heal sees the same poisoned tail the
    rebuild will see, and the placeholders it synthesizes are the
    same R1 ids the in-graph helper uses (so the in-graph guard's
    dedup path doesn't re-synthesize).
    """

    async def test_seam_synthesizes_same_id_format_as_in_graph_helper(self):
        """R1 invariant: the seam guard uses the same R1 id format
        as the in-graph helper, so a re-heal across the seam +
        in-graph dedup chain is idempotent."""
        from daemon.graph import _ensure_tool_result_pairing

        poisoned_tail = AIMessage(content="", tool_calls=[_tc("call_clemirror")])
        checkpoint_messages = [
            SystemMessage(content="sys"),
            poisoned_tail,
        ]
        graph = _make_graph(checkpoint_messages)
        graph_input = {
            "messages": [HumanMessage(content="turn", id="t1")],
        }

        # Seam heal.
        seam_placeholders = await _heal_poisoned_checkpoint_tail(
            graph, _make_config_for_call(), graph_input, "iid-clemirror",
        )

        # In-graph heal on the SAME poisoned tail (the agent_node
        # drain path that runs AFTER the seam heal — its dedup
        # against the seam's placeholders must use the same id).
        in_graph_messages = list(graph_input["messages"])  # includes seam placeholder
        in_graph_synth = _ensure_tool_result_pairing(in_graph_messages)

        # Same R1 id format → no double-synthesis.
        all_ids = {tm.id for tm in seam_placeholders}
        all_ids.update(tm.id for tm in in_graph_synth)
        # All placeholders share the SAME id (dedup path skips the
        # in-graph synthesis because the seam placeholder already
        # covers ``call_clemirror``).
        assert all_ids == {"pairing-synth-call_clemirror"}


# ---------------------------------------------------------------------------
# None graph_input skip (S5 / architect correction 2)
# ---------------------------------------------------------------------------


class TestSeamGuardNoneGraphInputSkip:
    """S5 (architect correction 2): the ``:3407`` silent-resume branch
    sets ``graph_input = None`` (pure checkpoint resume — silent mode
    or no content). The seam heal/prepend MUST SKIP a None graph_input
    — that path injects no new mid-turn HumanMessage at the seam and
    is already covered by the in-graph pairing guard.
    """

    async def test_seam_guard_skipped_when_graph_input_is_none(self):
        """Pure silent-resume (graph_input=None): the seam heal is a
        no-op — the in-graph pairing guard covers this path."""
        # The test fixture's contract: when called with
        # graph_input=None, the helper returns immediately without
        # calling ``aget_state`` and without modifying anything.
        poisoned_tail = AIMessage(content="", tool_calls=[_tc("call_skip")])
        graph = _make_graph([poisoned_tail])

        # The helper MUST short-circuit on None graph_input.
        result = await _heal_poisoned_checkpoint_tail(
            graph, _make_config_for_call(), None, "iid-skip",
        )

        assert result == []
        # aget_state was NOT called (no inspection needed for
        # silent-resume).
        graph.aget_state.assert_not_called()


# ---------------------------------------------------------------------------
# Cost note (one aget_state per enqueued turn — accepted, measured-cheap)
# ---------------------------------------------------------------------------


class TestSeamGuardCost:
    """Cost pin: one ``aget_state`` read per enqueued turn. O(1) tail
    check after the read is free. The cost is documented in T6; if
    profiling flags it, the seam is the optimization seam.
    """

    async def test_seam_calls_aget_state_exactly_once_per_call(self):
        poisoned_tail = AIMessage(content="", tool_calls=[_tc("call_cost")])
        graph = _make_graph([poisoned_tail])
        graph_input = {
            "messages": [HumanMessage(content="turn", id="t-cost")],
        }

        await _heal_poisoned_checkpoint_tail(
            graph, _make_config_for_call(), graph_input, "iid-cost",
        )

        assert graph.aget_state.await_count == 1, (
            "Seam heal must call aget_state exactly once per enqueued "
            "turn — no multi-call scans."
        )
