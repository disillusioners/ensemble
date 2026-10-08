"""Regression + pin tests for the full-history tool-call pairing
validator and healer (W1 / W2 / W3 / W4).

Branch: ``fix/tool-pairing-full-history-heal``.

PRODUCTION BUG (incident 03d7657f):
    Front-door Ari instance bricked by provider rejection
    ``openai: invalid params, tool call result does not follow tool
    call (2013)``. Checkpoint blob v0980 decode found the defect
    class: an ``AIMessage(tool_calls)`` committed mid-history whose
    ``ToolMessage`` never arrived (stream died after commit, before
    the result was written). Forensic id was ``call_8ed9e1771dca``
    (1c/0r — one call-site, zero results). Nothing ever healed it —
    the lenient primary provider accepted the defective payload for 6
    days; both 2013 rejections fired when proxy routing hit a strict
    validator.

    The pre-existing O(1) tail-only helpers
    (:func:`daemon.graph._ensure_tool_result_pairing` and
    :func:`daemon.services.instance_messaging._heal_poisoned_checkpoint_tail`)
    could NOT see this defect because the unanswered AIMessage was
    buried mid-history. The W1 fix introduces a full-history
    adjacency / immediacy check that catches the violation AND heals
    the history at the LLM dispatch boundary.

LOAD-BEARING LEARNING (live repair evidence):
    Strict OpenAI-compatible gateways enforce IMMEDIACY (every
    AIMessage(tc) answered by the IMMEDIATELY-adjacent following
    ``ToolMessage`` block — no intervening non-ToolMessage between
    call and answer), NOT count-pairing (every tc_id has SOME
    ToolMessage SOMEWHERE in the history). The repair evidence
    recorded a near-miss where the synthesized TM was placed at the
    right POSITION (count-pairing was valid) but the WRONG adjacent
    AIMessage (an intervening AI-other message between the issuing
    AIMessage and its synthesized TM) — a strict gateway STILL
    2013-rejected that placement. The W1 fix places synthesized
    ToolMessages IMMEDIATELY after the issuing AIMessage (or at the
    end of the existing adjacent ToolMessage block), so the result
    is order-valid for the strict gateway.

Forensic fact correction (commission correction 2026-10-08):
    The originally-cited "duplicate tool_call_id
    call_01a0fcefec43 (2 call-sites / 2 results)" was a 12-char-
    prefix grouping FALSE POSITIVE — call_01a0fcefacc228e8
    (job_get) and call_01a0fcefacc228e9 (get_mission) are DISTINCT
    valid calls. The incident's ONLY real defect was the unanswered
    call_8ed9e1771dca (interrupted-mid-tool shape). This test file
    focuses on the genuine defect class (1c/0r mid-history +
    adjacency-trap) and treats the duplicate-id case as a defensive
    fixture only (W1(c) clarification).

Fixture map:

    PRIMARY REGRESSION — ``TestUnansweredToolCallMidHistory``
        Replays the forensic shape: an ``AIMessage(tool_calls)``
        committed mid-history, no ``ToolMessage`` ever written.
        Validator heals by synthesizing a placeholder immediately
        after the AIMessage; gateway-validated payload passes.

    PRIMARY REGRESSION — ``TestAdjacencyTrap``
        ``[AI-issuer][AI-other][TM-issuer][TM-orphan]`` — count-
        pairing is valid (every tc_id has a TM somewhere) but the
        gateway STILL 2013-rejects because the AI-other message
        breaks the call→result adjacency. Validator heals by
        synthesizing a placeholder immediately after the AI-issuer
        AND removing BOTH stranded original TMs: the orphaned
        ``TM-orphan`` (no AIMessage anywhere issued its tc_id) AND
        the misplaced ``TM-issuer`` (its nearest preceding NON-TOOL
        message after Phase 1 is AI-other, which did not issue its
        tc_id — the block-ownership rule). The synth placeholder
        at index 1 (a partner-synth, naturally belonging to
        AI-issuer) satisfies AI-issuer's adjacency.

    DEFENSIVE — ``TestDuplicateToolCallIdEachHasOwnTM``
        Two AIMessages with the same ``tool_call_id``; each has
        its own adjacent ToolMessage. Strict gateways accept this
        (each AIMessage has its own adjacent block). Validator
        leaves both pairs intact — count-duplicates are NOT
        detected as violations per W1(c).

    FIX 3 — ``TestOrderInvertedToolMessageBeforeIssuer``
        Order-inverted shape: a ``ToolMessage`` whose issuing
        ``AIMessage`` appears LATER in the list. Pre-FIX 3 the
        probe flagged it but the healer's global issued-set kept
        the TM (detected-but-unhealable). FIX 3 scopes the
        issued-set to AIMessages at indices BEFORE each TM; the
        misplaced TM is now removed as an orphan. Probe and
        healer agree.

    ROUND-2 BLOCKER — ``TestBlockOwnershipMisplacement``
        ``[AI(X)][AI(Y)][TM(X)][TM(Y)]`` — TM(X) is in the wrong
        adjacent block (its issuer AI(X) is earlier but the TM
        sits in AI(Y)'s block). Round-1 prefix rule kept TM(X)
        (had an earlier issuer); round-2 block-ownership rule
        (TM's nearest preceding NON-TOOL message must have
        issued its tc_id) removes it. AI(X) is then answered
        by a synth placeholder. Also covers the
        ``[AI(X)][Human][TM(X)]`` interleave sub-shape (Human
        is the nearest preceding non-Tool, issued nothing → TM
        stranded → removed). ``TestBlockOwnershipProbePin`` pins
        the probe side of the same rule.

    TEST GAPS — ``TestMultiCallAIMessageBothTcMissing``,
        ``TestAdjacentAIMessageTcBackToBack``
        Multi-tc AIMessage with NO TMs in its block (all
        synthesized) and two adjacent AIMessage(tc)s back-to-back
        with no TMs (each gets its own synth). Pins Phase 1
        multi-call coverage.

    PROBE / W1 HOT-PATH GATE — ``TestHasPairingViolations``
        The cheap O(n) pre-flight probe returns False on healthy
        histories (O(n) auxiliary set) and True on poisoned
        histories.

    ADJACENCY HELPERS — ``TestBuildNextNonToolAfter``
        The pre-computed ``next_non_tool_after`` array correctly
        identifies the end of the contiguous ToolMessage block
        following each index.

    W3 EMERGENCY_TRUNCATE — ``TestEmergencyTruncateAdjacencySnap``
        The pop(0) loop drops oldest messages, then the validator
        runs to fix any order violations introduced by the drops.

    W4 PRODUCER GUARD — ``TestDuplicateToolCallIdEachHasOwnTM``
        The cheap defensive guard re-ids colliding tool_call_ids in
        the incoming AIMessage at the COMMIT boundary
        (real home: ``TestDuplicateToolCallIdEachHasOwnTM::
        test_defensive_dedupe_incoming_collisions_at_commit_boundary``).
"""

from __future__ import annotations

import pytest
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from daemon.tool_pairing_history import (
    PARTNER_SYNTH_TEXT,
    ToolPairingHealReport,
    _build_next_non_tool_after,
    _is_partner_synth,
    dedupe_incoming_tool_call_ids,
    has_pairing_violations,
    validate_and_heal_messages,
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
# PRIMARY REGRESSION — unanswered tool call mid-history (1c/0r)
# Mirrors the forensic shape of incident 03d7657f: an AIMessage(tc)
# committed mid-history whose ToolMessage never arrived.
# ---------------------------------------------------------------------------


class TestUnansweredToolCallMidHistory:
    """Forensic replay of incident 03d7657f's actual defect class.

    The defensive helpers (``_ensure_tool_result_pairing``,
    ``_heal_poisoned_checkpoint_tail``) could NOT catch this because
    the unanswered AIMessage was buried mid-history — both helpers
    short-circuit on the O(1) tail-only happy path. The W1 full-
    history adjacency check finds and heals the defect.
    """

    def test_unanswered_mid_history_synthesizes_partner_immediately_after(self):
        """1c/0r mid-history → synthesize partner immediately after
        the issuing AIMessage, preserving call→result adjacency."""
        ai_unanswered = AIMessage(content="", tool_calls=[_tc("call_8ed9e1771dca")])
        msgs: list = [
            SystemMessage(content="sys"),
            HumanMessage(content="hi"),
            ai_unanswered,
            # Subsequent messages — model kept talking without a
            # matching ToolMessage for call_8ed9e1771dca.
            HumanMessage(content="hello?"),
            AIMessage(content="reply", tool_calls=[_tc("call_c")]),
            ToolMessage(content="r_c", tool_call_id="call_c", name="tool"),
            HumanMessage(content="bye"),
        ]

        # Pre-state: probe flags the violation.
        assert has_pairing_violations(msgs) is True

        # Heal.
        report = validate_and_heal_messages(msgs, instance_short="iid-03d7657f")

        # Synthesized exactly one placeholder.
        assert len(report.synthesized) == 1
        synth = report.synthesized[0]
        assert synth.tool_call_id == "call_8ed9e1771dca"
        assert synth.id == f"partner-synth-call_8ed9e1771dca"
        assert synth.content == PARTNER_SYNTH_TEXT

        # Placeholder sits IMMEDIATELY after the issuing AIMessage
        # (no intervening messages — strict gateway adjacency).
        i_ai = msgs.index(ai_unanswered)
        assert i_ai >= 0
        assert isinstance(msgs[i_ai + 1], ToolMessage)
        assert msgs[i_ai + 1].tool_call_id == "call_8ed9e1771dca"
        assert msgs[i_ai + 1].id == f"partner-synth-call_8ed9e1771dca"
        # The HumanMessage that was at index i_ai+1 BEFORE the heal
        # is now at i_ai+2.
        assert isinstance(msgs[i_ai + 2], HumanMessage)
        assert msgs[i_ai + 2].content == "hello?"

        # Post-state: probe is clean.
        assert has_pairing_violations(msgs) is False

    def test_multiple_unanswered_mid_history_synthesizes_each(self):
        """2 unanswered AIMessages mid-history → 2 synthesized
        partners, each immediately after its issuing AIMessage."""
        ai_a = AIMessage(content="", tool_calls=[_tc("call_a")])
        ai_b = AIMessage(content="", tool_calls=[_tc("call_b")])
        msgs: list = [
            ai_a,
            HumanMessage(content="mid"),
            ai_b,
            HumanMessage(content="end"),
        ]

        assert has_pairing_violations(msgs) is True
        report = validate_and_heal_messages(msgs, instance_short="iid-multi")
        assert len(report.synthesized) == 2

        # After heal: ai_a → synth_a → HM → ai_b → synth_b → HM
        kinds = [type(m).__name__ for m in msgs]
        assert kinds == [
            "AIMessage", "ToolMessage", "HumanMessage",
            "AIMessage", "ToolMessage", "HumanMessage",
        ]
        # synth_a immediately after ai_a.
        assert msgs[1].tool_call_id == "call_a"
        # synth_b immediately after ai_b.
        assert msgs[4].tool_call_id == "call_b"
        # Probe clean.
        assert has_pairing_violations(msgs) is False

    def test_partial_unanswered_in_existing_adjacent_block(self):
        """AIMessage(tc=[A, B]) with TM(A) but no TM(B) in adjacent
        block → synthesize TM(B) at the end of the block, after TM(A).
        Preserves AI(tc) → TM(A) → TM(B) adjacency.
        """
        msgs: list = [
            AIMessage(content="", tool_calls=[_tc("call_a"), _tc("call_b")]),
            ToolMessage(content="r_a", tool_call_id="call_a", name="tool"),
            HumanMessage(content="mid"),
        ]
        assert has_pairing_violations(msgs) is True
        report = validate_and_heal_messages(msgs, instance_short="iid-partial")
        assert len(report.synthesized) == 1
        assert report.synthesized[0].tool_call_id == "call_b"

        # Order: AI(tc=[A,B]) → TM(A) → TM(B, synth) → HM.
        kinds = [type(m).__name__ for m in msgs]
        assert kinds == [
            "AIMessage", "ToolMessage", "ToolMessage", "HumanMessage",
        ]
        assert msgs[1].tool_call_id == "call_a"
        assert msgs[2].tool_call_id == "call_b"
        assert msgs[2].id == "partner-synth-call_b"
        assert msgs[3].content == "mid"
        assert has_pairing_violations(msgs) is False


# ---------------------------------------------------------------------------
# PRIMARY REGRESSION — adjacency trap (the load-bearing lesson)
# ---------------------------------------------------------------------------


class TestAdjacencyTrap:
    """Strict gateways enforce IMMEDIACY, not count-pairing.

    The trap: ``[AI-issuer][AI-other][TM-synth][TM-other]`` has
    count-pairing (every tc_id has a TM somewhere) but the gateway
    STILL 2013-rejects because the AI-other message breaks the
    call→result adjacency — the TM-synth is NOT immediately after
    the AI-issuer.

    Validator heals: synthesizes a placeholder IMMEDIATELY after
    AI-issuer (the issuing AIMessage), and removes the original
    TM-synth (now an orphan with no AIMessage(tc) calling for it
    in the surviving list).
    """

    def test_adjacency_trap_synthesizes_partner_immediately_after_issuer(self):
        msgs: list = [
            AIMessage(content="", tool_calls=[_tc("call_issuer")]),
            AIMessage(content="other AI msg — no tool_calls"),
            ToolMessage(content="r_issuer", tool_call_id="call_issuer", name="tool"),
            ToolMessage(content="r_other", tool_call_id="call_other", name="tool"),
            HumanMessage(content="hi"),
        ]

        # Pre-state: probe flags the adjacency violation.
        assert has_pairing_violations(msgs) is True

        report = validate_and_heal_messages(msgs, instance_short="iid-adj")

        # Synthesized exactly one — for call_issuer (AI-other has no
        # tool_calls, so no synthesis for it).
        assert len(report.synthesized) == 1
        synth = report.synthesized[0]
        assert synth.tool_call_id == "call_issuer"
        assert synth.id == "partner-synth-call_issuer"

        # The placeholder sits IMMEDIATELY after AI-issuer (index 0).
        assert msgs[1].tool_call_id == "call_issuer"
        assert msgs[1].id == "partner-synth-call_issuer"
        # AI-other is now at index 2.
        assert isinstance(msgs[2], AIMessage)
        assert msgs[2].content == "other AI msg — no tool_calls"
        # ROUND-2 BLOCKER: TM(call_issuer, original) IS removed by
        # the block-ownership rule. Its nearest preceding NON-TOOL
        # message at this point is AI-other (the AIMessage that
        # broke adjacency), which did NOT issue call_issuer — the
        # TM is stranded in the wrong block, NOT in AI-issuer's
        # adjacent block, so it must come out. The synth placeholder
        # at index 1 (a partner-synth, naturally belonging to
        # AI-issuer) satisfies AI-issuer's adjacency. The pre-FIX
        # test comment claimed the original TM was kept (the
        # round-1 prefix rule masked this misplacement-after-other-
        # AI shape), but the round-2 block-ownership rule removes
        # it — which is exactly the safer behavior since the
        # strict-gateway would have 2013-rejected the kept TM.
        # Verify: BOTH original TMs are removed (call_issuer as a
        # stranded misplaced TM, call_other as an orphan with no
        # issuing AIMessage anywhere in the list).
        original_tm_call_issuer = [
            m for m in msgs
            if isinstance(m, ToolMessage)
            and m.tool_call_id == "call_issuer"
            and not _is_partner_synth(m)
        ]
        assert original_tm_call_issuer == [], (
            f"stranded original TM(call_issuer) must be removed by "
            f"the block-ownership rule; still in msgs: "
            f"{[m.tool_call_id for m in original_tm_call_issuer]}"
        )
        other_tc_ids = [
            m.tool_call_id for m in msgs
            if isinstance(m, ToolMessage)
            and m.tool_call_id == "call_other"
        ]
        assert other_tc_ids == [], (
            f"orphan TM(call_other) was NOT removed by validator; "
            f"still in history: {other_tc_ids}"
        )
        # Orphan removal tracked at least 2 (both stranded TMs).
        assert len(report.removed_orphan_indices) >= 2, (
            f"both stranded TMs must be removed; "
            f"removed_orphan_indices={report.removed_orphan_indices}"
        )

        # Post-state: probe clean. The remaining list is
        # [AI-issuer, synth(call_issuer), AI-other, HumanMessage]
        # — the partner-synth at index 1 satisfies AI-issuer's
        # adjacency; AI-other has no tool_calls; the trailing
        # HumanMessage bounds the block-ownership region for any
        # future TM (none here).
        assert has_pairing_violations(msgs) is False


# ---------------------------------------------------------------------------
# FIX 3 — order-inverted (TM-before-issuer) shape
# ---------------------------------------------------------------------------


class TestOrderInvertedToolMessageBeforeIssuer:
    """FIX 3: probe/healer agreement on the order-inverted shape.

    The 2013-bricking bug class also includes a shape where a
    ``ToolMessage`` appears in the history BEFORE its issuing
    ``AIMessage``. Count-pairing is valid (the same ``tool_call_id``
    IS issued by an AIMessage SOMEWHERE in the list) but the gateway
    still rejects because the order is wrong: the TM has no
    IMMEDIATELY-adjacent issuing AIMessage to attach to, and a
    later AIMessage's call would be unbacked by an adjacent TM
    block.

    Pre-FIX 3 the probe flagged this shape (backward scan finds no
    earlier AIMessage(tc) for the TM) but the healer's Phase 2
    used a GLOBAL issued-set, so the TM was NOT removed
    (detected-but-unhealable). The strict gateway then 2013-rejected
    on the next dispatch, the W2 retry healed nothing (the same
    issue), and the instance bricked.

    FIX 3 scopes the issued-set to AIMessages at indices BEFORE
    each TM. The TM is now an orphan (no earlier issuer) and is
    removed by Phase 2. Probe and healer agree.
    """

    def test_tm_before_issuer_is_flagged_by_probe(self):
        msgs: list = [
            HumanMessage(content="hi"),
            # TM appears BEFORE its issuing AIMessage (order-inverted).
            ToolMessage(content="r_a", tool_call_id="call_a", name="tool"),
            AIMessage(content="", tool_calls=[_tc("call_a")]),
            HumanMessage(content="next"),
        ]
        # The probe MUST flag the TM-before-issuer shape.
        assert has_pairing_violations(msgs) is True

    def test_tm_before_issuer_is_removed_by_healer(self):
        msgs: list = [
            HumanMessage(content="hi"),
            ToolMessage(content="r_a", tool_call_id="call_a", name="tool"),
            AIMessage(content="", tool_calls=[_tc("call_a")]),
            HumanMessage(content="next"),
        ]

        # The original TM is non-synth (not a partner-synth).
        original_tm = msgs[1]
        assert not _is_partner_synth(original_tm)

        report = validate_and_heal_messages(msgs, instance_short="iid-oinv")

        # The orphan TM (no earlier AIMessage(tc) issued its tc_id) was
        # removed by Phase 2.
        assert len(report.removed_orphan_indices) >= 1, (
            f"TM-before-issuer must be removed as orphan; "
            f"removed_orphan_indices={report.removed_orphan_indices}"
        )
        # The remaining history contains NO ORIGINAL (non-synth)
        # ToolMessage(call_a) — the misplaced one was removed. A
        # partner-synth placeholder may exist (Phase 1 synthesizes
        # one for the now-unanswered AIMessage at the end), but the
        # ORIGINAL TM is gone.
        remaining_original_tm_call_a = [
            m for m in msgs
            if isinstance(m, ToolMessage)
            and m.tool_call_id == "call_a"
            and not _is_partner_synth(m)
        ]
        assert remaining_original_tm_call_a == [], (
            f"original TM(call_a) must be removed; still in msgs: "
            f"{[m.tool_call_id for m in remaining_original_tm_call_a]}"
        )

        # Post-state: probe clean. The remaining AIMessage(call_a) at
        # the end (or wherever it ended up) has a partner-synth
        # placeholder from Phase 1, so adjacency is satisfied; no
        # orphan TMs; order is valid.
        assert has_pairing_violations(msgs) is False, (
            f"post-heal history must be probe-clean; msgs="
            f"{[(type(m).__name__, getattr(m, 'tool_call_id', None), _is_partner_synth(m)) for m in msgs]}"
        )

    def test_tm_before_issuer_with_intervening_aimessage_other(self):
        """A more complex order-inverted shape: TM, intervening
        AIMessage (no tc_ids), then the issuer. Same outcome — the
        ORIGINAL TM is removed as orphan (a partner-synth
        placeholder for the AIMessage's unanswered tool call may
        remain, but no original TM(call_a) survives).
        """
        msgs: list = [
            HumanMessage(content="hi"),
            ToolMessage(content="r_a", tool_call_id="call_a", name="tool"),
            # Intervening AIMessage with no tool_calls — does not
            # issue call_a, so the TM remains an orphan.
            AIMessage(content="intervening — no tool_calls"),
            AIMessage(content="", tool_calls=[_tc("call_a")]),
            HumanMessage(content="next"),
        ]
        assert has_pairing_violations(msgs) is True

        report = validate_and_heal_messages(msgs, instance_short="iid-oinv2")
        # The TM was removed.
        assert len(report.removed_orphan_indices) >= 1
        # No ORIGINAL (non-synth) TM(call_a) remains.
        assert [
            m for m in msgs
            if isinstance(m, ToolMessage)
            and m.tool_call_id == "call_a"
            and not _is_partner_synth(m)
        ] == []
        # Post-heal: probe clean (the AIMessage at the end gets a
        # synthesized partner in Phase 1).
        assert has_pairing_violations(msgs) is False


# ---------------------------------------------------------------------------
# ROUND-2 BLOCKER — block-ownership rule (misplacement-after-other-AI + Human-interleave)
# ---------------------------------------------------------------------------


class TestBlockOwnershipMisplacement:
    """ROUND-2 BLOCKER: the misplacement-after-other-AI shape.

    The shape ``[AI(X)][AI(Y)][TM(X)][TM(Y)]`` is the exact
    forensic-class from the live 10-08 repair: a TM whose issuer
    exists earlier in the list but the TM is in the WRONG
    adjacent block. Count-pairing is valid (every tc_id has a TM
    somewhere) and the round-1 prefix-issued-set rule kept
    TM(X) (its issuer AI(X) is at an earlier index), but the
    strict-gateway 2013-rejects because TM(X) is in AI(Y)'s
    block, not AI(X)'s.

    The round-1 probe reported CLEAN for this shape, the W1 heal
    was a no-op, and the W2 retry healed nothing (same issue)
    — permanent brick WITH probe masking diagnosis.

    The round-2 block-ownership rule subsumes this: a TM
    survives only if the NEAREST PRECEDING NON-TOOL message is
    an AIMessage that issued its tc_id. TM(X)'s nearest preceding
    non-Tool (after Phase 1) is AI(Y) at index 2 (the new
    layout has AI(X), synth(X), AI(Y), TM(X), TM(Y)), which
    did not issue X — TM(X) is removed. AI(X) is then answered
    by the synth placeholder.
    """

    def test_misplaced_tm_after_other_ai_is_flagged_by_probe(self):
        """Pre-heal: probe flags the misplacement."""
        msgs: list = [
            AIMessage(content="", tool_calls=[_tc("call_x")]),
            AIMessage(content="other AI", tool_calls=[_tc("call_y")]),
            ToolMessage(content="r_x", tool_call_id="call_x", name="tool"),
            ToolMessage(content="r_y", tool_call_id="call_y", name="tool"),
        ]
        # Pre-heal probe: BLOCKER (TM(x) is in AI(y)'s block, not
        # AI(x)'s — adjacency violation at AI(x); the gate will
        # 2013-reject).
        assert has_pairing_violations(msgs) is True

    def test_misplaced_tm_after_other_ai_is_removed_by_healer(self):
        """Heal outcome: TM(X) removed, AI(X) answered by synth."""
        msgs: list = [
            AIMessage(content="", tool_calls=[_tc("call_x")]),
            AIMessage(content="other AI", tool_calls=[_tc("call_y")]),
            ToolMessage(content="r_x", tool_call_id="call_x", name="tool"),
            ToolMessage(content="r_y", tool_call_id="call_y", name="tool"),
        ]

        report = validate_and_heal_messages(msgs, instance_short="iid-block")

        # 1 synth: for call_x (AI(x) was unanswered after the
        # misplacement removal). AI(y) is answered by its own TM
        # in its adjacent block, so no synth for call_y.
        assert len(report.synthesized) == 1
        assert report.synthesized[0].tool_call_id == "call_x"
        assert report.synthesized[0].id == "partner-synth-call_x"

        # The original TM(x) (the misplaced one) is REMOVED. Its
        # nearest preceding non-Tool after Phase 1 is AI(y) at the
        # new index 2, which did not issue call_x.
        original_tm_call_x = [
            m for m in msgs
            if isinstance(m, ToolMessage)
            and m.tool_call_id == "call_x"
            and not _is_partner_synth(m)
        ]
        assert original_tm_call_x == [], (
            f"misplaced TM(call_x) must be removed; still in msgs: "
            f"{[m.tool_call_id for m in original_tm_call_x]}"
        )

        # The TM(y) survives (its nearest preceding non-Tool is
        # AI(y) at index 2, which DID issue call_y).
        tm_call_y = [
            m for m in msgs
            if isinstance(m, ToolMessage)
            and m.tool_call_id == "call_y"
        ]
        assert len(tm_call_y) == 1, (
            f"TM(call_y) must be kept (correctly placed in AI(y)'s "
            f"block); got {len(tm_call_y)}"
        )

        # The healed list shape (in order):
        # [AI(x)][synth(x)][AI(y)][TM(y)]
        # — the reviewer's expected heal form.
        assert msgs[0].tool_calls[0]["id"] == "call_x"
        assert msgs[1].tool_call_id == "call_x"
        assert msgs[1].id == "partner-synth-call_x"
        assert isinstance(msgs[2], AIMessage)
        assert msgs[2].tool_calls[0]["id"] == "call_y"
        assert msgs[3].tool_call_id == "call_y"

        # Orphan removal tracked exactly 1 (the misplaced TM(x)).
        assert len(report.removed_orphan_indices) == 1

        # Post-heal: probe clean. The healed list
        # [AI(x)][synth(x)][AI(y)][TM(y)] is order-valid:
        # - AI(x) answered by synth(x) at index 1
        # - AI(y) answered by TM(y) at index 3
        # - TM(y)'s nearest preceding non-Tool is AI(y), which
        #   issued call_y ✓
        assert has_pairing_violations(msgs) is False

    def test_human_interleave_subshape(self):
        """ROUND-2: the Human-interleave sub-shape the review called
        out as the reason for tightening past the literal
        "nearest preceding AIMessage" phrasing.

        ``[AI(X)][Human][TM(X)]`` — TM(X) appears AFTER a Human
        message. Under the literal "nearest preceding AIMessage"
        rule, the nearest preceding AIMessage IS the issuer, so
        the stranded TM would survive and the probe would stay
        clean — recreating the same masking class. The
        non-Tool formulation subsumes this: the Human is a
        non-Tool, so the tracking set is empty, and TM(X) is
        flagged as stranded.
        """
        msgs: list = [
            AIMessage(content="", tool_calls=[_tc("call_a")]),
            HumanMessage(content="interrupt"),
            ToolMessage(content="r_a", tool_call_id="call_a", name="tool"),
        ]

        # Pre-heal: probe MUST flag the stranded TM (the
        # round-2 block-ownership rule, not the round-1 prefix
        # rule which would have passed it).
        assert has_pairing_violations(msgs) is True

        report = validate_and_heal_messages(msgs, instance_short="iid-intl")

        # Heal: AI(a) was answered by a synth placeholder (its
        # adjacent block was empty — Human at index 1 is the
        # first non-Tool, breaking adjacency). The stranded
        # TM(call_a) is removed (its nearest preceding non-Tool
        # is the Human, which issued nothing).
        assert len(report.synthesized) == 1
        assert report.synthesized[0].tool_call_id == "call_a"

        # The original TM(a) is REMOVED.
        original_tm_call_a = [
            m for m in msgs
            if isinstance(m, ToolMessage)
            and m.tool_call_id == "call_a"
            and not _is_partner_synth(m)
        ]
        assert original_tm_call_a == [], (
            f"stranded TM(call_a) (Human-interleave) must be removed; "
            f"still in msgs: {[m.tool_call_id for m in original_tm_call_a]}"
        )

        # Healed shape: [AI(a)][synth(a)][Human]
        assert msgs[0].tool_calls[0]["id"] == "call_a"
        assert msgs[1].tool_call_id == "call_a"
        assert msgs[1].id == "partner-synth-call_a"
        assert isinstance(msgs[2], HumanMessage)

        # Post-heal: probe clean.
        assert has_pairing_violations(msgs) is False

    def test_valid_multicall_block_still_valid(self):
        """Sanity-pin: the block-ownership rule does NOT regress
        the valid multi-call adjacent block.

        ``[AI(X,Y)][TM(X)][TM(Y)]`` — both TMs are in the
        AIMessage's adjacent block. Each TM's nearest preceding
        non-Tool is AI(X,Y), which issued both X and Y. Both
        TMs survive. Probe-clean.
        """
        msgs: list = [
            AIMessage(
                content="",
                tool_calls=[_tc("call_x", "tool_x"), _tc("call_y", "tool_y")],
            ),
            ToolMessage(content="r_x", tool_call_id="call_x", name="tool_x"),
            ToolMessage(content="r_y", tool_call_id="call_y", name="tool_y"),
        ]

        # Pre-heal: probe clean (both TMs in their issuer's block).
        assert has_pairing_violations(msgs) is False

        # Healer is a no-op (no violations to fix).
        report = validate_and_heal_messages(msgs, instance_short="iid-valid-multi")
        assert report.synthesized == []
        assert report.removed_orphan_indices == []

        # Post-heal: still probe clean.
        assert has_pairing_violations(msgs) is False


class TestBlockOwnershipProbePin:
    """The probe mirrors Phase 2's block-ownership rule (the
    round-2 review requirement). Pre-existing probe tests
    already pin the adjacency side; this class pins the
    misplacement-flagging side."""

    def test_probe_flags_misplacement_after_other_ai(self):
        msgs = [
            AIMessage(content="", tool_calls=[_tc("call_x")]),
            AIMessage(content="other", tool_calls=[_tc("call_y")]),
            ToolMessage(content="r_x", tool_call_id="call_x", name="t"),
            ToolMessage(content="r_y", tool_call_id="call_y", name="t"),
        ]
        assert has_pairing_violations(msgs) is True

    def test_probe_flags_human_interleave(self):
        msgs = [
            AIMessage(content="", tool_calls=[_tc("call_a")]),
            HumanMessage(content="interrupt"),
            ToolMessage(content="r_a", tool_call_id="call_a", name="t"),
        ]
        assert has_pairing_violations(msgs) is True

    def test_probe_flags_stranded_after_other_ai_no_tc_match(self):
        """Stranded TM (tc_id not issued by any AIMessage) after
        another AI's block — the block-ownership rule catches it
        because the nearest preceding non-Tool (the other AI)
        didn't issue its tc_id."""
        msgs = [
            AIMessage(content="", tool_calls=[_tc("call_y")]),
            ToolMessage(content="orphan", tool_call_id="call_orphan", name="t"),
        ]
        assert has_pairing_violations(msgs) is True


# ---------------------------------------------------------------------------
# Test gaps (🟡 #6) — both-tc-missing multi-call AIMessage, adjacent AIMessage(tc) back-to-back
# ---------------------------------------------------------------------------


class TestMultiCallAIMessageBothTcMissing:
    """🟡 #6 test gap: an AIMessage with multiple tool_calls where
    NONE of the TMs are in the adjacent block. Phase 1 should
    synthesize placeholders for ALL missing tc_ids."""

    def test_both_tc_missing_synthesizes_both_placeholders(self):
        msgs: list = [
            AIMessage(
                content="",
                tool_calls=[_tc("call_x", "tool_x"), _tc("call_y", "tool_y")],
            ),
            HumanMessage(content="no tool results at all"),
        ]

        # Pre-heal: probe flags the adjacency violation (no TMs
        # in the AIMessage's block).
        assert has_pairing_violations(msgs) is True

        report = validate_and_heal_messages(msgs, instance_short="iid-both-missing")

        # BOTH missing tc_ids are synthesized.
        assert len(report.synthesized) == 2
        synth_tc_ids = {s.tool_call_id for s in report.synthesized}
        assert synth_tc_ids == {"call_x", "call_y"}
        # Both synths sit immediately after the AIMessage.
        assert msgs[1].tool_call_id in {"call_x", "call_y"}
        assert msgs[1].id in {
            "partner-synth-call_x", "partner-synth-call_y",
        }
        assert msgs[2].tool_call_id in {"call_x", "call_y"}
        assert msgs[2].id in {
            "partner-synth-call_x", "partner-synth-call_y",
        }
        # HumanMessage moved to index 3.
        assert isinstance(msgs[3], HumanMessage)

        # Post-heal: probe clean.
        assert has_pairing_violations(msgs) is False


class TestAdjacentAIMessageTcBackToBack:
    """🟡 #6 test gap: two AIMessages with tool_calls back-to-back,
    neither with a TM in the immediate adjacent block. Phase 1
    should synthesize placeholders for both."""

    def test_adjacent_ai_tc_synthesizes_for_each(self):
        msgs: list = [
            AIMessage(content="", tool_calls=[_tc("call_x")]),
            AIMessage(content="", tool_calls=[_tc("call_y")]),
            HumanMessage(content="no TMs at all"),
        ]

        # Pre-heal: probe flags BOTH adjacency violations.
        assert has_pairing_violations(msgs) is True

        report = validate_and_heal_messages(msgs, instance_short="iid-adj-ai")

        # 2 synths total — one for each AIMessage.
        assert len(report.synthesized) == 2
        synth_tc_ids = {s.tool_call_id for s in report.synthesized}
        assert synth_tc_ids == {"call_x", "call_y"}

        # The healed shape:
        # [AI(x)][synth(x)][AI(y)][synth(y)][Human]
        # — each AIMessage answered by its own synth at the end
        # of its (empty) adjacent block.
        assert msgs[0].tool_calls[0]["id"] == "call_x"
        assert msgs[1].tool_call_id == "call_x"
        assert msgs[1].id == "partner-synth-call_x"
        assert msgs[2].tool_calls[0]["id"] == "call_y"
        assert msgs[3].tool_call_id == "call_y"
        assert msgs[3].id == "partner-synth-call_y"
        assert isinstance(msgs[4], HumanMessage)

        # Post-heal: probe clean.
        assert has_pairing_violations(msgs) is False


# ---------------------------------------------------------------------------
# DEFENSIVE — duplicate tool_call_id (W1(c) clarification)
# ---------------------------------------------------------------------------


class TestDuplicateToolCallIdEachHasOwnTM:
    """Count-duplicates are NOT gateway violations.

    Strict gateways accept two AIMessages with the SAME
    ``tool_call_id`` as long as each AIMessage has its own adjacent
    ``ToolMessage``. The W1(c) clarification keeps the duplicate-id
    detection out of the main flow (over-aggressive) but preserves
    the producer-side guard (``dedupe_incoming_tool_call_ids``) as a
    cheap defensive measure.
    """

    def test_count_duplicate_with_each_own_tm_is_not_a_violation(self):
        msgs: list = [
            AIMessage(content="", tool_calls=[_tc("call_a")]),
            ToolMessage(content="r_a", tool_call_id="call_a", name="tool"),
            HumanMessage(content="mid"),
            AIMessage(content="", tool_calls=[_tc("call_a")]),
            ToolMessage(content="r_a2", tool_call_id="call_a", name="tool"),
        ]

        # Probe: NOT a violation (each AIMessage has its own TM).
        assert has_pairing_violations(msgs) is False

        report = validate_and_heal_messages(msgs, instance_short="iid-dup")
        # No synthesis, no orphan removal, no removal of any kind.
        assert report.synthesized == []
        assert report.removed_orphan_indices == []

        # History byte-identical.
        kinds = [type(m).__name__ for m in msgs]
        assert kinds == [
            "AIMessage", "ToolMessage", "HumanMessage",
            "AIMessage", "ToolMessage",
        ]

    def test_defensive_dedupe_incoming_collisions_at_commit_boundary(self):
        """W4 defensive guard: when an incoming AIMessage has a
        ``tool_call_id`` already committed in the history, re-id it
        deterministically at the COMMIT boundary.
        """
        committed = [
            HumanMessage(content="hi"),
            AIMessage(content="", tool_calls=[_tc("call_a")]),
            ToolMessage(content="r_a", tool_call_id="call_a", name="tool"),
        ]

        # New AIMessage with a colliding call_a + a fresh call_b.
        new_ai = AIMessage(
            content="regen",
            tool_calls=[_tc("call_a"), _tc("call_b")],
        )

        new_ai, reid_count = dedupe_incoming_tool_call_ids(
            new_ai, committed, instance_short="iid-w4",
        )

        assert reid_count == 1
        # call_a was re-id'd; call_b was not.
        reid_tc_ids = [tc["id"] for tc in new_ai.tool_calls]
        assert "call_a" not in reid_tc_ids
        assert reid_tc_ids[0] == "call_a__dup1"
        assert reid_tc_ids[1] == "call_b"

    def test_defensive_dedupe_no_collision_no_change(self):
        """W4 defensive guard: no collision → no re-id."""
        committed = [HumanMessage(content="hi")]
        new_ai = AIMessage(content="fresh", tool_calls=[_tc("call_x")])
        new_ai, reid_count = dedupe_incoming_tool_call_ids(
            new_ai, committed, instance_short="iid-w4-noop",
        )
        assert reid_count == 0
        assert new_ai.tool_calls[0]["id"] == "call_x"


# ---------------------------------------------------------------------------
# PROBE / W1 HOT-PATH GATE
# ---------------------------------------------------------------------------


class TestHasPairingViolations:
    """The cheap O(n) pre-flight probe.

    Hot-path gate: validate_and_heal_messages runs ONLY when this
    probe returns True. The probe is a single O(n) walk that
    precomputes an O(n) auxiliary set of issued tool_call_ids once,
    then performs an O(1) set lookup per ToolMessage for the orphan
    check — the older O(n²) ``msgs_list[:i]`` slice + walk was
    replaced when FIX 2 landed. For healthy histories the probe
    inspects every message but does NOT construct any heal
    artifacts.
    """

    def test_probe_false_on_healthy_history(self):
        msgs = [
            HumanMessage(content="hi"),
            AIMessage(content="reply"),
            HumanMessage(content="more"),
        ]
        assert has_pairing_violations(msgs) is False

    def test_probe_false_on_perfect_tool_pair(self):
        msgs = [
            HumanMessage(content="hi"),
            AIMessage(content="", tool_calls=[_tc("call_a")]),
            ToolMessage(content="r_a", tool_call_id="call_a", name="tool"),
            HumanMessage(content="done"),
        ]
        assert has_pairing_violations(msgs) is False

    def test_probe_true_on_unanswered_adjacent(self):
        msgs = [
            HumanMessage(content="hi"),
            AIMessage(content="", tool_calls=[_tc("call_a")]),
            HumanMessage(content="next"),
        ]
        assert has_pairing_violations(msgs) is True

    def test_probe_true_on_adjacency_trap(self):
        msgs = [
            AIMessage(content="", tool_calls=[_tc("call_a")]),
            AIMessage(content="other"),
            ToolMessage(content="r_a", tool_call_id="call_a", name="tool"),
        ]
        assert has_pairing_violations(msgs) is True

    def test_probe_true_on_orphan_tool_message(self):
        msgs = [
            ToolMessage(content="orphan", tool_call_id="orphan_id", name="tool"),
            HumanMessage(content="hi"),
        ]
        assert has_pairing_violations(msgs) is True

    def test_probe_true_on_tm_before_issuer(self):
        """FIX 3: order-inverted shape — TM appears BEFORE its
        issuing AIMessage. Count-pairing is valid (somewhere in the
        list the same tc_id IS issued) but the gateway rejects
        because the TM has no earlier issuer. The probe MUST flag
        this shape; the healer's Phase 2 (FIX 3 prefix scoping)
        must remove the TM.
        """
        msgs = [
            HumanMessage(content="hi"),
            ToolMessage(content="r_a", tool_call_id="call_a", name="tool"),
            AIMessage(content="", tool_calls=[_tc("call_a")]),
        ]
        assert has_pairing_violations(msgs) is True


# ---------------------------------------------------------------------------
# ADJACENCY HELPERS — _build_next_non_tool_after
# ---------------------------------------------------------------------------


class TestBuildNextNonToolAfter:
    """The pre-computed ``next_non_tool_after`` array correctly
    identifies the end of the contiguous ToolMessage block following
    each index.
    """

    def test_empty_list_returns_empty(self):
        assert _build_next_non_tool_after([]) == []

    def test_all_tool_messages_returns_n_for_each(self):
        msgs = [
            ToolMessage(content="a", tool_call_id="a", name="t"),
            ToolMessage(content="b", tool_call_id="b", name="t"),
        ]
        result = _build_next_non_tool_after(msgs)
        assert result == [2, 2]

    def test_simple_block_end(self):
        msgs = [
            AIMessage(content="ai", tool_calls=[_tc("a")]),
            ToolMessage(content="r", tool_call_id="a", name="t"),
            HumanMessage(content="h"),
        ]
        result = _build_next_non_tool_after(msgs)
        # next_non_tool_after[0] = first non-ToolMessage at index > 0
        # = HumanMessage at index 2.
        assert result[0] == 2
        # next_non_tool_after[1] = first non-ToolMessage at index > 1
        # = HumanMessage at index 2.
        assert result[1] == 2
        # next_non_tool_after[2] = first non-ToolMessage at index > 2
        # = no such index → n=3.
        assert result[2] == 3

    def test_block_at_tail_no_terminator(self):
        """AIMessage(tc) followed by 2 TMs and nothing else —
        next_non_tool_after returns the list length.
        """
        msgs = [
            AIMessage(content="ai", tool_calls=[_tc("a")]),
            ToolMessage(content="r1", tool_call_id="a", name="t"),
            ToolMessage(content="r2", tool_call_id="b", name="t"),
        ]
        result = _build_next_non_tool_after(msgs)
        # AI at index 0, TMs at 1 and 2. block_end = 3 (n).
        assert result[0] == 3
        # TM at 1, TM at 2. n=3.
        assert result[1] == 3
        assert result[2] == 3


# ---------------------------------------------------------------------------
# W3 EMERGENCY_TRUNCATE — adjacency snap delegates to validator
# ---------------------------------------------------------------------------


class TestEmergencyTruncateAdjacencySnap:
    """The pop(0) loop in ``emergency_truncate`` may sever a tool-call
    pair. The validator runs at the end of the loop to fix any order
    violations introduced by the drops.

    Pre-existing violations in the input are NOT repaired by
    ``emergency_truncate`` — the function returns early at Pass 0
    when the estimate is already under budget (it does not fix
    pre-existing violations, only those introduced by the pop loop).
    Caller-side responsibility for pre-existing violations lives at
    the LLM dispatch boundary (W1 / graph.py:9013).
    """

    def test_pop_with_unanswered_at_head_synthesizes_partner(self):
        """emergency_truncate drops oldest messages until under
        budget; if a pop leaves an AIMessage(tc) without an adjacent
        TM, the validator synthesizes a partner immediately after.
        """
        from daemon.compaction import emergency_truncate

        # Construct a history whose initial budget is OVER the limit
        # so the pop loop runs. The pop will drop the head messages,
        # potentially leaving an AIMessage(tc) at the head with no
        # adjacent TM.
        msgs = [
            HumanMessage(content="x" * 200),  # huge
            AIMessage(content="", tool_calls=[_tc("call_b")]),
            HumanMessage(content="x" * 200),  # huge
        ]
        def estimate(messages: list) -> int:
            return sum(len(str(m.content)) for m in messages)
        # budget small enough that at least one pop happens.
        result = emergency_truncate(msgs, max_tokens=100, estimate_fn=estimate)

        # Validator kept the result order-valid for the gateway.
        assert has_pairing_violations(result) is False

    def test_pop_with_tool_messages_only_does_not_zero_result(self):
        """When the history is all ToolMessages, the validator does
        NOT remove them (the at-least-one-message contract for
        emergency_truncate is preserved).
        """
        from daemon.compaction import emergency_truncate

        msgs = [
            ToolMessage(content="x" * 500, tool_call_id=f"c{i}", name="t", id=f"t{i}")
            for i in range(5)
        ]
        def estimate(_messages: list) -> int:
            return 5000
        result = emergency_truncate(msgs, max_tokens=1000, estimate_fn=estimate)
        # At least one message must remain (the pre-existing contract).
        assert 1 <= len(result) <= 5


# ---------------------------------------------------------------------------
# W2 REACTIVE 2013 RECOVERY — pairing-signature match
# ---------------------------------------------------------------------------


class TestToolPairingInvalidSignature:
    """The classifier detects pairing-invalid signatures and raises
    :class:`ToolPairingInvalidError`, which the agent_node W2 catch
    uses to drive a heal-once retry.
    """

    def test_canonical_2013_signature_matches(self):
        from daemon.llm_error_classifier import (
            ToolPairingInvalidError,
            _matches_pairing_invalid,
        )
        sig = _matches_pairing_invalid(
            "openai: invalid params, tool call result does not follow "
            "tool call (2013)"
        )
        assert sig == "tool call result does not follow tool call"

    def test_sibling_signature_anthropic_matches(self):
        from daemon.llm_error_classifier import _matches_pairing_invalid
        sig = _matches_pairing_invalid(
            "Invalid parameter: messages with role 'tool' must be a "
            "response to a preceeding message with 'tool_calls'"
        )
        assert sig is not None
        assert "messages with role 'tool'" in sig

    def test_non_pairing_invalid_params_does_not_match(self):
        from daemon.llm_error_classifier import _matches_pairing_invalid
        # Generic 400 — NOT a pairing shape.
        assert _matches_pairing_invalid(
            "openai: invalid params, missing required field 'messages'"
        ) is None

    def test_context_length_does_not_match(self):
        from daemon.llm_error_classifier import _matches_pairing_invalid
        assert _matches_pairing_invalid(
            "context_length_exceeded: maximum context length is 100000"
        ) is None

    def test_tool_pairing_invalid_error_class_wraps_original(self):
        from daemon.llm_error_classifier import ToolPairingInvalidError
        original = ValueError("inner")
        e = ToolPairingInvalidError(original, "test sig")
        assert e.original is original
        assert e.signature == "test sig"
        assert "Tool-pairing-invalid" in str(e)
        assert "test sig" in str(e)