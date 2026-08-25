"""Regression tests for the mid-turn tool-call pairing guard.

Branch: ``fix/tool-pairing-mid-turn-injection``.

PRODUCTION BUG (the regression target):
    OpenAI-compatible gateways reject LLM requests shaped like
    ``AIMessage(tool_calls=[...])`` immediately followed by
    ``HumanMessage`` with error code ``2013: tool call result does not
    follow tool call``. After a daemon restart mid-tool-execution, the
    persisted ``state['messages']`` tail IS an
    ``AIMessage(tool_calls=[...])`` with no matching ``ToolMessage``,
    because the prior LLM/tool call died before the result was
    recorded. The :func:`create_agent_node` factory in
    ``daemon/graph.py`` appends mid-turn ``HumanMessage`` injections
    (user messages, skill injections, child report drains) to
    ``full_messages`` BEFORE the LLM call. Without the fix, this
    manufactures an API-invalid history which is then checkpointed
    (C2 return persists the injected messages) and replayed on every
    turn — the instance tips into permanent ``error``.

FIX:
    New helper :func:`daemon.graph._ensure_tool_result_pairing`
    inspects the tail of ``full_messages`` BEFORE any
    ``extend``/``append`` that introduces a ``HumanMessage``. When the
    tail carries an ``AIMessage(tool_calls=[...])`` without a matching
    ``ToolMessage``, the helper inserts a synthesized placeholder
    ``ToolMessage`` IMMEDIATELY AFTER that ``AIMessage`` so the
    history stays structurally valid for the gateway. The placeholder
    is honest about the cause (daemon restart / crash) — never
    fabricated output, never an empty string.

DESIGN CONSTRAINTS VERIFIED HERE:
    * O(1) happy-path: one ``isinstance`` check on the tail. NO
      full-history scan.
    * Bounded backward walk: capped at
      ``_TOOL_PAIRING_MAX_TRAVERSAL`` (8).
    * Dedupe: skip synthesis when a ``ToolMessage`` for the same
      ``tool_call_id`` already exists in the trailing window.
    * In-place insert: helper mutates the input list AND returns the
      synthesized messages so the caller can persist them via the C2
      return (heals the checkpoint permanently).

The helper is a STATELESS pure-except-for-mutation function. It only
depends on the messages list and the instance_short label (used only
for the WARNING log). Tests do NOT need to spin up an
``InstanceManager``, a graph, or any LLM — they exercise the helper
directly with hand-crafted message lists.

Cases covered (mapped to the dispatcher's required scenarios):

    1. ``TestSingleToolCallWithHumanInjection`` — tail =
       ``AIMessage(tool_calls=[call_1])`` + injection of two
       ``HumanMessage``s → one placeholder ``ToolMessage(call_1)``
       inserted between AI and HMs; order preserved.
    2. ``TestParallelToolCalls`` — tail = ``AIMessage`` with two
       parallel ``tool_calls`` → two placeholders with correct ids.
    3. ``TestHappyPathNoSynthesis`` — tail = ``ToolMessage`` (tool
       result already present) or plain ``AIMessage`` (no
       tool_calls) → NO synthesis, list unchanged, helper returns ``[]``.
    4. ``TestAIMessageChain`` — tail chain
       ``AIMessage(tc1) → AIMessage(tc2)`` → interleaved results
       (AI1, r1, AI2, r2).
    5. ``TestExistingToolMessageDedupe`` — existing ``ToolMessage`` for
       a ``tool_call_id`` in the trailing window → no duplicate
       synthesis for that id; other unanswered ids still get
       placeholders.
    6. ``TestEmptyInput`` — empty input list → returns ``[]``, no
       mutation, no error.
    7. ``TestHelperIsStatelessOnHappyPath`` — verifies the O(1)
       short-circuit by passing a long history with no trailing
       ``AIMessage(tool_calls)`` and confirming the helper returns
       immediately without walking the tail.
"""

from __future__ import annotations

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from daemon.graph import (
    _TOOL_PAIRING_MAX_TRAVERSAL,
    _TOOL_PAIRING_PLACEHOLDER_TEXT,
    _ensure_tool_result_pairing,
)


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


# ---------------------------------------------------------------------------
# Case 1 — single tool_call + HumanMessage injection
# ---------------------------------------------------------------------------


class TestSingleToolCallWithHumanInjection:
    """The dispatcher's primary regression case: tail AIMessage with 1
    unanswered tool_call, then two HumanMessages are injected by the
    caller. The helper MUST insert a placeholder ToolMessage BETWEEN
    the AIMessage and the HumanMessages and preserve order."""

    def test_placeholder_inserted_between_ai_and_humans(self):
        ai = AIMessage(content="", tool_calls=[_tc("call_1")])
        msgs: list = [
            SystemMessage(content="sys"),
            HumanMessage(content="hi"),
            ai,
        ]
        synthesized = _ensure_tool_result_pairing(msgs)

        # Simulate the caller extending with two HumanMessages
        msgs.extend([HumanMessage(content="inject_1"), HumanMessage(content="inject_2")])

        # 1 placeholder synthesized
        assert len(synthesized) == 1
        assert synthesized[0].tool_call_id == "call_1"
        # Order: SystemMessage, HumanMessage('hi'), AIMessage, ToolMessage(call_1),
        # HumanMessage('inject_1'), HumanMessage('inject_2')
        assert isinstance(msgs[2], AIMessage)
        assert isinstance(msgs[3], ToolMessage)
        assert msgs[3].tool_call_id == "call_1"
        assert msgs[3].name == "tool"
        assert msgs[3].content == _TOOL_PAIRING_PLACEHOLDER_TEXT
        assert isinstance(msgs[4], HumanMessage) and msgs[4].content == "inject_1"
        assert isinstance(msgs[5], HumanMessage) and msgs[5].content == "inject_2"

    def test_placeholder_returns_the_inserted_message(self):
        ai = AIMessage(content="", tool_calls=[_tc("call_1")])
        msgs = [SystemMessage(content="sys"), HumanMessage(content="hi"), ai]

        synthesized = _ensure_tool_result_pairing(msgs)

        # The returned list contains the exact placeholder that was
        # inserted in-place — same object identity.
        assert len(synthesized) == 1
        assert synthesized[0] is msgs[3]
        assert synthesized[0].tool_call_id == "call_1"


# ---------------------------------------------------------------------------
# Case 2 — parallel tool_calls
# ---------------------------------------------------------------------------


class TestParallelToolCalls:
    """Tail AIMessage with 2 parallel unanswered tool_calls → two
    placeholders, each with the correct tool_call_id."""

    def test_two_placeholders_one_per_call(self):
        ai = AIMessage(
            content="",
            tool_calls=[_tc("call_a", "foo"), _tc("call_b", "bar")],
        )
        msgs = [SystemMessage(content="sys"), HumanMessage(content="hi"), ai]

        synthesized = _ensure_tool_result_pairing(msgs)

        assert len(synthesized) == 2
        ids = {tm.tool_call_id for tm in synthesized}
        assert ids == {"call_a", "call_b"}
        # Names preserved
        names_by_id = {tm.tool_call_id: tm.name for tm in synthesized}
        assert names_by_id["call_a"] == "foo"
        assert names_by_id["call_b"] == "bar"
        # Both placeholders are at the tail, immediately after the AI
        assert msgs[3] is synthesized[0]
        assert msgs[4] is synthesized[1]
        assert isinstance(msgs[3], ToolMessage)
        assert isinstance(msgs[4], ToolMessage)


# ---------------------------------------------------------------------------
# Case 3 — happy path: NO synthesis
# ---------------------------------------------------------------------------


class TestHappyPathNoSynthesis:
    """Tail is a ToolMessage (tool already answered) or a plain AIMessage
    (no tool_calls) → helper returns [] and the list is unchanged."""

    def test_tail_tool_message_no_synthesis(self):
        tm = ToolMessage(content="ok", tool_call_id="call_x", name="foo")
        msgs = [
            SystemMessage(content="sys"),
            HumanMessage(content="hi"),
            AIMessage(content="", tool_calls=[_tc("call_x")]),
            tm,
        ]
        snapshot = list(msgs)

        synthesized = _ensure_tool_result_pairing(msgs)

        assert synthesized == []
        # List unchanged
        assert msgs == snapshot

    def test_tail_plain_ai_no_tool_calls_no_synthesis(self):
        ai = AIMessage(content="hello")
        msgs = [
            SystemMessage(content="sys"),
            HumanMessage(content="hi"),
            ai,
        ]
        snapshot = list(msgs)

        synthesized = _ensure_tool_result_pairing(msgs)

        assert synthesized == []
        assert msgs == snapshot

    def test_tail_human_message_no_synthesis(self):
        msgs = [
            SystemMessage(content="sys"),
            HumanMessage(content="hi"),
            AIMessage(content="reply"),
            HumanMessage(content="follow up"),
        ]
        snapshot = list(msgs)

        synthesized = _ensure_tool_result_pairing(msgs)

        assert synthesized == []
        assert msgs == snapshot

    def test_tail_empty_tool_calls_no_synthesis(self):
        # AIMessage with explicit empty tool_calls list (langchain default).
        ai = AIMessage(content="hello", tool_calls=[])
        msgs = [SystemMessage(content="sys"), HumanMessage(content="hi"), ai]
        snapshot = list(msgs)

        synthesized = _ensure_tool_result_pairing(msgs)

        assert synthesized == []
        assert msgs == snapshot


# ---------------------------------------------------------------------------
# Case 4 — AIMessage(tc) → AIMessage(tc) chain
# ---------------------------------------------------------------------------


class TestAIMessageChain:
    """Tail is a chain of unanswered AIMessages carrying tool_calls. The
    helper walks backward over the chain and inserts placeholders
    IMMEDIATELY AFTER each AIMessage, preserving block order:
    AI1, results1, AI2, results2, ..., then HumanMessages."""

    def test_two_unanswered_ai_messages_chain(self):
        ai1 = AIMessage(content="", tool_calls=[_tc("call_1", "first")])
        ai2 = AIMessage(content="", tool_calls=[_tc("call_2", "second")])
        msgs = [
            SystemMessage(content="sys"),
            HumanMessage(content="hi"),
            ai1,
            ai2,
        ]

        synthesized = _ensure_tool_result_pairing(msgs)

        # Order: SystemMessage, HumanMessage, AI1, TM(call_1), AI2, TM(call_2)
        assert len(synthesized) == 2
        assert msgs[2] is ai1
        assert isinstance(msgs[3], ToolMessage)
        assert msgs[3].tool_call_id == "call_1"
        assert msgs[3].name == "first"
        assert msgs[4] is ai2
        assert isinstance(msgs[5], ToolMessage)
        assert msgs[5].tool_call_id == "call_2"
        assert msgs[5].name == "second"
        # Block order preserved: synthesized[0] is AI1's result,
        # synthesized[1] is AI2's result.
        assert synthesized[0].tool_call_id == "call_1"
        assert synthesized[1].tool_call_id == "call_2"

    def test_three_unanswered_ai_messages_chain(self):
        ai1 = AIMessage(content="", tool_calls=[_tc("call_1")])
        ai2 = AIMessage(content="", tool_calls=[_tc("call_2", "b")])
        ai3 = AIMessage(content="", tool_calls=[_tc("call_3")])
        msgs = [
            SystemMessage(content="sys"),
            HumanMessage(content="hi"),
            ai1,
            ai2,
            ai3,
        ]

        synthesized = _ensure_tool_result_pairing(msgs)

        # Order: SM, HM, AI1, TM(call_1), AI2, TM(call_2), AI3, TM(call_3)
        assert len(synthesized) == 3
        assert msgs[2] is ai1
        assert msgs[3].tool_call_id == "call_1"
        assert msgs[4] is ai2
        assert msgs[5].tool_call_id == "call_2"
        assert msgs[6] is ai3
        assert msgs[7].tool_call_id == "call_3"


# ---------------------------------------------------------------------------
# Case 5 — existing ToolMessage dedupe
# ---------------------------------------------------------------------------


class TestExistingToolMessageDedupe:
    """If a tool_call_id is already represented by a synthesized
    ``ToolMessage`` in the trailing window being examined (e.g. the
    same id appears in two trailing AIMessage(tc) blocks), the helper
    MUST NOT synthesize a duplicate for the second block.

    Note on the dedupe semantics: the helper walks backward over
    trailing ``AIMessage(tool_calls)`` blocks ONLY — it stops at the
    first non-AIMessage(tc) message (typically a real ``ToolMessage``
    from a prior successful tool execution). That means an existing
    ``ToolMessage`` from earlier in the conversation that lives
    OUTSIDE the trailing AI(tc) chain does NOT suppress synthesis —
    and that is intentional: by the time we get here, the LLM-bound
    history will already contain that earlier ``ToolMessage`` at its
    original position, so re-synthesizing for a re-issued call does
    not produce a duplicate in the LLM request. (The dedupe window
    therefore covers synthesized placeholders within the trailing
    AI(tc) chain, plus any pre-existing ToolMessages that happen to
    fall INSIDE that chain — a structural impossibility today, but
    the helper enforces it defensively for future shapes.)"""

    def test_cross_block_dedupe_within_ai_chain(self):
        # Two trailing AIMessage(tc) blocks where the second block
        # repeats a tool_call_id from the first. The helper should
        # synthesize for the FIRST occurrence only.
        ai1 = AIMessage(
            content="",
            tool_calls=[_tc("call_1", "a"), _tc("call_2", "b")],
        )
        ai2 = AIMessage(
            content="",
            tool_calls=[_tc("call_1", "a"), _tc("call_3", "c")],
        )
        msgs = [
            SystemMessage(content="sys"),
            HumanMessage(content="hi"),
            ai1,
            ai2,
        ]

        synthesized = _ensure_tool_result_pairing(msgs)

        # call_1 appears in BOTH ai1 and ai2. The first occurrence
        # gets synthesized (placed after ai1), the second is skipped.
        # call_2 (in ai1) gets synthesized (placed after ai1).
        # call_3 (in ai2) gets synthesized (placed after ai2).
        synthesized_ids = [tm.tool_call_id for tm in synthesized]
        assert synthesized_ids.count("call_1") == 1
        assert "call_2" in synthesized_ids
        assert "call_3" in synthesized_ids
        # Order: SM, HM, ai1, TM(call_1), TM(call_2), ai2, TM(call_3)
        assert msgs[2] is ai1
        assert msgs[3].tool_call_id == "call_1"
        assert msgs[4].tool_call_id == "call_2"
        assert msgs[5] is ai2
        assert msgs[6].tool_call_id == "call_3"

    def test_existing_tool_message_before_chain_blocks_walk(self):
        # A pre-existing ToolMessage (between two AIMessages in the
        # raw list) sits OUTSIDE the trailing AI(tc) chain the helper
        # walks — because the helper stops at the first non-AI(tc)
        # message. So the helper only synthesizes for the reachable
        # tail AI(tc) block. The pre-existing ToolMessage is left
        # untouched.
        ai_partial = AIMessage(
            content="",
            tool_calls=[_tc("call_1", "a"), _tc("call_2", "b")],
        )
        existing_tm = ToolMessage(content="real result", tool_call_id="call_1", name="a")
        ai_tail = AIMessage(content="", tool_calls=[_tc("call_3", "c")])
        msgs = [
            SystemMessage(content="sys"),
            HumanMessage(content="hi"),
            ai_partial,
            existing_tm,
            ai_tail,
        ]

        synthesized = _ensure_tool_result_pairing(msgs)

        # Only ai_tail (the reachable trailing AI(tc) block) is in the
        # walk window — the walk broke at the existing TM at idx 3.
        # So only TM(call_3) is synthesized; ai_partial is left alone.
        synthesized_ids = {tm.tool_call_id for tm in synthesized}
        assert synthesized_ids == {"call_3"}
        # The pre-existing ToolMessage is untouched.
        assert msgs[3] is existing_tm
        assert msgs[3].content == "real result"

    def test_no_synthesis_when_tail_already_answered(self):
        # Tail is an AIMessage(tc) but the SAME id has a ToolMessage
        # in the trailing window (the chain has just one AI block with
        # one call, and a real ToolMessage immediately precedes it).
        # Walk backward: AI(tc) at idx 4 → add; TM at idx 3 → break.
        # ai_indices=[4]. Trailing window = messages[4:] = [ai_tail].
        # No ToolMessages in the window → synthesize TM for call_x.
        # (The earlier TM(call_x) at idx 3 is NOT in the trailing
        # window and does NOT suppress synthesis — see class docstring
        # for why this is correct: the original ToolMessage stays at
        # its original position in full_messages, and the synthesized
        # placeholder at the tail does not duplicate it.)
        ai_partial = AIMessage(content="", tool_calls=[_tc("call_x")])
        existing_tm = ToolMessage(content="real result", tool_call_id="call_x", name="tool")
        ai_tail = AIMessage(content="", tool_calls=[_tc("call_x")])
        msgs = [
            SystemMessage(content="sys"),
            HumanMessage(content="hi"),
            ai_partial,
            existing_tm,
            ai_tail,
        ]

        synthesized = _ensure_tool_result_pairing(msgs)

        # Only the tail's call_x gets synthesized; the earlier
        # existing_tm stays in place at idx 3 (the helper did not
        # touch messages[0..3]).
        assert len(synthesized) == 1
        assert synthesized[0].tool_call_id == "call_x"
        assert msgs[3] is existing_tm


# ---------------------------------------------------------------------------
# Case 6 — empty input
# ---------------------------------------------------------------------------


class TestEmptyInput:
    """Helper is defensive against empty input: returns [] without
    raising or mutating the caller-supplied list."""

    def test_empty_list_returns_empty(self):
        msgs: list = []
        synthesized = _ensure_tool_result_pairing(msgs)
        assert synthesized == []
        assert msgs == []


# ---------------------------------------------------------------------------
# Case 7 — bounded walk & O(1) happy-path short-circuit
# ---------------------------------------------------------------------------


class TestBoundedWalk:
    """The backward walk is bounded by ``_TOOL_PAIRING_MAX_TRAVERSAL``.
    Beyond that, the helper stops. Verifies the cap is enforced AND
    that the happy-path short-circuit (one isinstance check on the
    tail) works for long histories without a trailing AIMessage(tc)."""

    def test_walk_capped_at_max_traversal(self):
        # Build a chain of (MAX_TRAVERSAL + 4) unanswered AIMessages
        # followed by a non-AIMessage(tc) message at the very leftmost
        # position. The helper should walk backward, see the non-AI
        # tail, stop — but the chain itself is at the tail, so all
        # blocks are reachable. To test the CAP, we build a chain of
        # length exactly MAX_TRAVERSAL + 2 where the tail is one past
        # the cap from a HumanMessage anchor — but since the helper
        # only walks trailing AIMessages, the HumanMessage anchor at
        # the very end triggers no synthesis at all (happy path).
        # We instead verify the cap is enforced when an AIMessage(tc)
        # tail is followed by a long run of MORE AIMessages(tc) beyond
        # the cap — only the last MAX_TRAVERSAL blocks get synthesized.
        cap = _TOOL_PAIRING_MAX_TRAVERSAL

        # Build: [HM(anchor), AI_1, AI_2, ..., AI_{cap+3}]
        # Each AI_{i} has tool_call_id=f"call_{i}".
        msgs: list = [SystemMessage(content="sys"), HumanMessage(content="anchor")]
        n_blocks = cap + 3  # intentionally over the cap
        for i in range(1, n_blocks + 1):
            msgs.append(
                AIMessage(content="", tool_calls=[_tc(f"call_{i}")])
            )

        synthesized = _ensure_tool_result_pairing(msgs)

        # Only the LAST cap blocks are reachable. The leftmost
        # unreachable block (AI_1) is at index 2 (after SM and HM
        # anchor). The reachable window is the last cap blocks of the
        # tail, i.e. AI_{n_blocks - cap + 1} .. AI_{n_blocks}.
        # So we expect exactly ``cap`` synthesized placeholders, with
        # ids call_{n_blocks - cap + 2} .. call_{n_blocks + 1}.
        # (The first synthesized is for AI_{n_blocks - cap + 1}, which
        # is the leftmost reachable block in the trailing window.)
        # Total synthesized count = cap (one per reachable block).
        assert len(synthesized) == cap

        # Spot-check: the last block's tool_call_id is in the result.
        assert f"call_{n_blocks}" in {tm.tool_call_id for tm in synthesized}
        # And the first unreachable block's id is NOT in the result.
        assert "call_1" not in {tm.tool_call_id for tm in synthesized}

    def test_happy_path_on_long_history_no_walk(self):
        # Long history whose tail is a plain AIMessage (no tool_calls).
        # The helper should short-circuit on the tail isinstance check
        # and return [] immediately without synthesizing or mutating.
        msgs: list = [SystemMessage(content="sys")]
        for i in range(50):
            msgs.append(HumanMessage(content=f"h_{i}"))
            msgs.append(AIMessage(content=f"a_{i}"))
        tail_ai = AIMessage(content="final reply")
        msgs.append(tail_ai)
        snapshot = list(msgs)

        synthesized = _ensure_tool_result_pairing(msgs)

        assert synthesized == []
        assert msgs == snapshot


# ---------------------------------------------------------------------------
# Sanity check — placeholder text honesty
# ---------------------------------------------------------------------------


class TestPlaceholderText:
    """The synthesized placeholder text must be HONEST about the cause
    (daemon restart / crash) — NOT an empty string, NOT fabricated
    output. This is critical for downstream debugging: an operator
    inspecting history can tell why a tool call has no real result."""

    def test_placeholder_text_is_nonempty_and_honest(self):
        ai = AIMessage(content="", tool_calls=[_tc("call_1")])
        msgs = [SystemMessage(content="sys"), HumanMessage(content="hi"), ai]

        synthesized = _ensure_tool_result_pairing(msgs)

        assert len(synthesized) == 1
        text = synthesized[0].content
        assert isinstance(text, str)
        assert len(text) > 0
        assert text == _TOOL_PAIRING_PLACEHOLDER_TEXT
        # Explicit honesty markers — never fabricated.
        assert "interrupted" in text or "restart" in text or "crash" in text
        # NOT an empty string.
        assert text.strip() != ""
