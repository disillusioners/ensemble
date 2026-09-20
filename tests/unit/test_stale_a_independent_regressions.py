"""LCA stale-A fix (B1) — INDEPENDENT live regressions at the unit seam.

Test-code authoring for the dual-autopsy B1 stale-A fix (commits
94fc6da1..e0d15e93). The dev's tests at
``tests/unit/test_attestation_resolver_activation.py`` cover the
production code; this file is a SECOND, independent construction
designed for the merge gate (EVALUATOR-style — orthogonal scenarios,
independent fixtures, distinct assertions).

Construction rule: every fixture here is built from scratch using the
public contract surface — ``collect_source_a_signals``,
``activation_predicate``, ``assemble_fused_bundle``, and
``evaluate_resolver_activation`` — against the EXACT byte shape the
resolver scans (``internal_report:<child_iid>:<completed_message_id>``
stamp on a ``HumanMessage``). The dev's ``_internal_report_message``
helper and the dev's specific child-uuid are deliberately NOT reused.

Each scenario below maps to ONE spec scenario in the merge-gate
matrix and asserts the REAL module output (NOT a derived
re-implementation): the structured :class:`SourceASignals` flags
(``advisory_present`` / ``phrase_match`` / ``contradiction_flag`` /
``word_count_below_threshold``), the :class:`ActivationResult` band
+ terms, the bundle's per-section text, and the snapshot's
``would_be_outcome``. If a real behavior at HEAD disagrees with the
spec expectation, the test FAILS LOUDLY — the divergence becomes the
report, not a forced-green.

Production code is FROZEN — this file is test-only, new file, and
does NOT modify the dev's test bodies or fixtures.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Sequence

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from daemon.services import attestation_resolver_activation as ara
from daemon.services.attestation_gate import Decision
from daemon.services.attestation_resolver_activation import (
    BAND_A_SUSPICION,
    BAND_DENY,
    SourceASignals,
    SourceBSignals,
    SourceCSignals,
    WOULD_DENY_NUDGE,
    WOULD_HINT,
    WOULD_TERMINAL,
    activation_predicate,
    assemble_fused_bundle,
    collect_source_a_signals,
    evaluate_resolver_activation,
)


# ─────────────────────────────────────────────────────────────────────────────
# Independent fixture builders — no reuse of dev's helpers
# ─────────────────────────────────────────────────────────────────────────────


# Two distinct child ids so the fixtures can prove per-child independence
# (acbf5627's giter child vs a second child in cross-resolve tests).
_GITER_CHILD_ID = "aaaa1111-bbbb-2222-cccc-333344445555"
_REVIEWER_CHILD_ID = "eeee4444-ffff-5555-aaaa-666677778888"


def _internal_report(
    content: str,
    *,
    child_id: str = _GITER_CHILD_ID,
    completed_message_id: str | None = None,
    stamp_id: str | None = None,
) -> HumanMessage:
    """Build a live child completion-report ``HumanMessage``.

    Mirrors the byte-shape the resolver scans: stamp =
    ``additional_kwargs={"injected_message": True, "source":
    f"internal_report:{child_iid}:{completed_message_id}"}`` per
    ``daemon/graph.py`` line ~6950 (report-injection drain) and the
    fallback ``_stamped_additional_kwargs`` at
    ``daemon/services/instance_messaging.py:525``. Each fixture here
    gets its own ``stamp_id`` so the harness can assert on stable
    ordering and on a band by stable id if needed.
    """
    completed = completed_message_id or str(uuid.uuid4())
    source = f"internal_report:{child_id}:{completed}"
    msg = HumanMessage(
        content=content,
        id=stamp_id or str(uuid.uuid4()),
        additional_kwargs={
            "injected_message": True,
            "source": source,
        },
    )
    return msg


def _delegated_state(final: str = "Wrap-up complete. All shipped.") -> list:
    """A minimal leader conversation that makes the A-scan + predicate
    eligible. Delegation evidence = AIMessage with ``send_message``
    tool_call; the final AIMessage carries the wrap-up.
    """
    return [
        HumanMessage(content="run the merge"),
        AIMessage(
            content="",
            tool_calls=[
                {"name": "send_message", "args": {"target": "giter"}, "id": "d-1"}
            ],
        ),
        AIMessage(content=final),
    ]


@dataclass(frozen=True)
class _TreeRow:
    """Minimal dict-shape row the resolver's cross-resolution consumes.

    The resolver's ``_completed_child_ids`` reads ONLY
    ``instance_id`` / ``status`` (and tolerates extra keys) — see
    ``daemon/services/attestation_resolver_activation.py:608-629``.
    Dicts are accepted by the public surface as well; we use plain
    dicts to mirror the canonical wiring.
    """

    instance_id: str
    status: str
    agent_id: str = "giter"


def _rows(*status_pairs: tuple[str, str]) -> list[dict]:
    return [
        {"instance_id": iid, "status": status, "agent_id": "giter"}
        for iid, status in status_pairs
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 1 — acbf5627 shape (giter phase-stop → later delivery)
# ─────────────────────────────────────────────────────────────────────────────


class TestScenario1Acbf5627StaleAdvisoryCleared:
    """Incident acbf5627: giter emitted a phase-stop "will report back
    after phase 2" promise as its FIRST terminal report, then LATER
    emitted a clean delivery report. Tree status = completed. The
    stale advisory must NOT hold the leader in a final quiet-tree
    evaluation."""

    def test_phase_stop_then_clean_delivery_no_advisory(self):
        """First report promises phase-stop work; second (NEWER) report
        reports clean delivery. ``advisory_present`` MUST be False on
        a quiet-tree final eval — the superseded advisory cleared."""
        phase_stop = _internal_report(
            "Phase 1 merged. Will report back after phase 2.",
            stamp_id="s1-phase",
        )
        # The clean final MUST be marker-free (no "then I'll", no
        # "still pending", no "awaiting" — all catalog hits).
        clean_delivery = _internal_report(
            "Done. 5/5 tests pass. Merged abc123. All shipped, "
            "no follow-ups.",
            stamp_id="s1-delivery",
        )
        tree_rows = _rows((_GITER_CHILD_ID, "completed"))

        signals = collect_source_a_signals(
            _delegated_state() + [phase_stop, clean_delivery],
            tree_rows_provider=lambda: tree_rows,
        )

        assert signals.advisory_present is False, (
            "acbf5627: superseded phase-stop advisory must be cleared "
            "by the newer clean delivery report"
        )
        assert signals.phrase_match is False
        assert signals.contradiction_flag is False
        assert signals.word_count_below_threshold is False
        assert signals.evidence == ()

    def test_phase_stop_then_clean_delivery_quiet_tree_deny_band_only(self):
        """Quiet-tree variant: with the stale advisory cleared, only
        the C-quiet term fires (the deny band, by §4.2 precedence).
        ``a_suspicion`` must NOT appear in ``terms_fired`` — that is
        the heart of the bug."""
        phase_stop = _internal_report(
            "Phase 1 merged. Will report back after phase 2.",
            stamp_id="s1-phase2",
        )
        clean_delivery = _internal_report(
            "All shipped. Done.",
            stamp_id="s1-delivery2",
        )
        signals = collect_source_a_signals(
            _delegated_state() + [phase_stop, clean_delivery],
            tree_rows_provider=lambda: _rows((_GITER_CHILD_ID, "completed")),
        )

        # Quiet tree — the deny band engages on C alone. A-band must be
        # silent. No residual "still pending" hold.
        result = activation_predicate(
            attestation_enabled=True,
            scope_applicable=True,
            mode="enforce",
            attestation_required=True,
            attested=False,
            user_answer_pending=False,
            a_source=lambda: signals,
            b_source=lambda: _b_quiet(),
            c_source=lambda: _c_quiet(),
        )

        assert result.fired is True
        assert result.band == BAND_DENY
        assert ara.TERM_C_QUIET in result.terms_fired
        assert ara.TERM_A_SUSPICION not in result.terms_fired, (
            "acbf5627 regression: cleared advisory must not surface "
            "a_suspicion"
        )
        assert result.b_signals is not None
        assert result.b_signals.marker_hit is False


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 2 — fba90db8 shape (reviewer "still pending on my ledger"
# then approved)
# ─────────────────────────────────────────────────────────────────────────────


class TestScenario2Fba90db8ReviewerStaleAdvisoryCleared:
    """Incident fba90db8: the reviewer reported "still pending on my
    ledger" as its first report, then later reported approval+merge.
    Tree status = completed. The stale "still pending" advisory must
    NOT keep the leader in a denied state."""

    def test_reviewer_pending_then_approved_no_advisory(self):
        pending = _internal_report(
            "Still pending on my ledger — awaiting the merge decision.",
            stamp_id="s2-pending",
        )
        approved = _internal_report(
            "Approved and merged. Ledger clear, nothing pending.",
            stamp_id="s2-approved",
        )
        signals = collect_source_a_signals(
            _delegated_state() + [pending, approved]
        )

        assert signals.advisory_present is False
        assert signals.phrase_match is False
        # "still pending" is a contradiction marker — must NOT survive
        # the cleanup (the later clean report is the canonical state).
        assert signals.contradiction_flag is False
        assert signals.evidence == ()

    def test_reviewer_pending_then_approved_band_is_quiet_only(self):
        pending = _internal_report(
            "Still pending on my ledger — awaiting the merge decision.",
            stamp_id="s2-pending2",
        )
        approved = _internal_report(
            "Approved and merged. Ledger clear.",
            stamp_id="s2-approved2",
        )
        signals = collect_source_a_signals(
            _delegated_state() + [pending, approved]
        )

        result = activation_predicate(
            attestation_enabled=True,
            scope_applicable=True,
            mode="enforce",
            attestation_required=True,
            attested=False,
            user_answer_pending=False,
            a_source=lambda: signals,
            b_source=lambda: _b_quiet(),
            c_source=lambda: _c_quiet(),
        )

        assert result.fired is True
        assert result.band == BAND_DENY
        assert ara.TERM_A_SUSPICION not in result.terms_fired


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 3 — operator-scope demotion
# ─────────────────────────────────────────────────────────────────────────────


class TestScenario3OperatorScopeDemotion:
    """Operator-scope demotion: a child report whose hit sits inside an
    operator-action sentence ("pending: rebuild+restart activation")
    keeps the advisory VISIBLE for the judge (B1 item 3) but does NOT
    raise ``contradiction_flag`` (the operator's pending action, not
    undelivered child work)."""

    def test_operator_pending_advisory_visible_but_not_contradiction(self):
        report = _internal_report(
            "Phase 1 merged. Still pending: rebuild+restart "
            "activation (operator action).",
            stamp_id="s3-operator",
        )
        signals = collect_source_a_signals(
            _delegated_state() + [report]
        )

        # Demoted — advisory stays visible.
        assert signals.advisory_present is True, (
            "operator-scoped advisory must stay visible (demoted, "
            "not suppressed wholesale)"
        )
        assert signals.phrase_match is True
        # But contradiction_flag must NOT raise.
        assert signals.contradiction_flag is False, (
            "operator-scoped hit MUST be excluded from contradiction_flag "
            "(B1 item 3 demotion)"
        )
        # The evidence row carries the operator-scoped term tuple.
        assert len(signals.evidence) == 1
        ev = signals.evidence[0]
        assert ev.matched_terms, "matched_terms must be non-empty"
        assert "still pending" in ev.operator_scoped_terms, (
            "the operator-scoped hit must be classified as such "
            "(contained in operator-action sentence)"
        )

    def test_genuine_pending_outside_operator_sentence_contradicts(self):
        """COUNTERWEIGHT: a "still pending" sentence WITHOUT an operator
        token must still raise contradiction_flag — the demotion is
        SENTENCE-scoped, not report-scoped. The operator mention lives
        in a different sentence here."""
        report = _internal_report(
            "Integration still pending - will report back. "
            "The operator can rebuild+restart afterwards.",
            stamp_id="s3-genuine",
        )
        signals = collect_source_a_signals(_delegated_state() + [report])

        assert signals.advisory_present is True
        assert signals.contradiction_flag is True, (
            "sentence-scope: a 'still pending' in a non-operator "
            "sentence must still contradict"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 4 — child-lie counterweight (FRESH, independent of dev flagship)
# ─────────────────────────────────────────────────────────────────────────────


class TestScenario4ChildLieCounterweight:
    """CHILD-LIE COUNTERWEIGHT: a completed child whose NEWEST report
    genuinely promises undelivered work ("will report back after the
    follow-up merge") MUST still surface — delivery without delivery
    is the lie class. The later-contradiction exception engages."""

    def test_completed_child_genuine_promise_surfaces_advisory(self):
        genuine_lie = _internal_report(
            "Merged the branch. Will report back after the follow-up merge.",
            stamp_id="s4-lie",
        )
        tree_rows = _rows((_GITER_CHILD_ID, "completed"))

        signals = collect_source_a_signals(
            _delegated_state() + [genuine_lie],
            tree_rows_provider=lambda: tree_rows,
        )

        # The later-contradiction exception keeps the lie visible.
        assert signals.advisory_present is True, (
            "child-lie class: a completed child whose newest report "
            "promises undelivered work MUST surface — cross-resolution "
            "must NOT suppress genuine hits"
        )
        assert len(signals.evidence) == 1
        assert "will report back" in signals.evidence[0].matched_terms
        # "will report back" is a promise marker, NOT a contradiction
        # marker — contradiction_flag stays false, but advisory_present
        # must hold (the 4-field OR has ``advisory_present`` already).
        assert signals.contradiction_flag is False

    def test_child_lie_band_a_engages_on_busy_tree(self):
        """Band behavior: with a live descendant (busy tree), A-band
        engages alone (Δ2 — Source A is NOT busy-suppressed).
        Real predicate output, not a re-implementation."""
        genuine_lie = _internal_report(
            "Merged the branch. Will report back after the follow-up merge.",
            stamp_id="s4-lie-busy",
        )
        signals = collect_source_a_signals(
            _delegated_state() + [genuine_lie],
            tree_rows_provider=lambda: _rows((_GITER_CHILD_ID, "completed")),
        )

        result_busy = activation_predicate(
            attestation_enabled=True,
            scope_applicable=True,
            mode="enforce",
            attestation_required=True,
            attested=False,
            user_answer_pending=False,
            a_source=lambda: signals,
            b_source=lambda: _b_quiet(),
            c_source=lambda: _c_busy(),  # live descendants, not quiet
        )

        assert result_busy.fired is True
        assert result_busy.band == BAND_A_SUSPICION, (
            "busy tree + child-lie advisory ⇒ band=A_suspicion alone "
            "(Δ2: Source A is NOT busy-suppressed)"
        )
        assert ara.TERM_A_SUSPICION in result_busy.terms_fired

    def test_child_lie_on_quiet_tree_engages_deny_band(self):
        """Quiet-tree variant: deny band wins (precedence over
        A-band); terms_fired carries BOTH c_quiet and a_suspicion."""
        genuine_lie = _internal_report(
            "Will report back after the follow-up merge.",
            stamp_id="s4-lie-quiet",
        )
        signals = collect_source_a_signals(
            _delegated_state() + [genuine_lie],
            tree_rows_provider=lambda: _rows((_GITER_CHILD_ID, "completed")),
        )

        result = activation_predicate(
            attestation_enabled=True,
            scope_applicable=True,
            mode="enforce",
            attestation_required=True,
            attested=False,
            user_answer_pending=False,
            a_source=lambda: signals,
            b_source=lambda: _b_quiet(),
            c_source=lambda: _c_quiet(),
        )

        assert result.fired is True
        assert result.band == BAND_DENY
        assert ara.TERM_C_QUIET in result.terms_fired
        assert ara.TERM_A_SUSPICION in result.terms_fired, (
            "quiet-tree + child-lie: deny band wins but a_suspicion "
            "term MUST still appear (advisory is real evidence)"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 5 — newest-only semantics ×2
# ─────────────────────────────────────────────────────────────────────────────


class TestScenario5NewestOnlySemantics:
    """Newest-only semantics, two pin variants. The newest-report-only
    per child rule (B1 item 1) decides which side of the OR the
    predicate sees."""

    def test_early_contradiction_clean_newest_clears(self):
        """(i) EARLY genuine contradiction + CLEAN newest report → cleared."""
        early_contradiction = _internal_report(
            "Still pending integration, ending turn.",
            stamp_id="s5-early",
        )
        clean_newest = _internal_report(
            "Integration complete. All checks green. Shipped.",
            stamp_id="s5-newest-clean",
        )

        signals = collect_source_a_signals(
            _delegated_state() + [early_contradiction, clean_newest],
            tree_rows_provider=lambda: _rows((_GITER_CHILD_ID, "completed")),
        )

        assert signals.advisory_present is False
        assert signals.contradiction_flag is False
        assert signals.evidence == ()

    def test_clean_early_contradictory_newest_fires(self):
        """(ii) CLEAN early report + CONTRADICTORY newest → fires.

        The contradictory newest is "still pending" (a contradiction
        marker) in a non-operator sentence — must raise
        contradiction_flag AND keep the advisory_present."""
        clean_early = _internal_report(
            "Done with phase 1. All green.",
            stamp_id="s5-early-clean",
        )
        contradictory_newest = _internal_report(
            "Integration still pending - will report back after.",
            stamp_id="s5-newest-contradict",
        )

        signals = collect_source_a_signals(
            _delegated_state() + [clean_early, contradictory_newest],
            tree_rows_provider=lambda: _rows((_GITER_CHILD_ID, "completed")),
        )

        assert signals.advisory_present is True
        assert signals.contradiction_flag is True, (
            "newest-only: the LATEST report's contradiction marker "
            "must raise contradiction_flag, even when an earlier "
            "report was clean"
        )
        # Cross-resolution suppresses the child because the child is
        # completed AND the newest report carries a genuine hit
        # (later-contradiction exception REARMS the advisory).
        assert len(signals.evidence) == 1
        assert "still pending" in signals.evidence[0].matched_terms


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 6 — suppression strictness
# ─────────────────────────────────────────────────────────────────────────────


class TestScenario6SuppressionStrictness:
    """Three strictness pins on the cross-resolution predicate.

    (a) terminated/error/failed statuses do NOT suppress — work
        genuinely unfinished.
    (b) A child ABSENT from the tree rows does NOT suppress — the
        resolver cannot cross-resolve absent children (conservative).
    (c) The later-contradiction exception IS reachable: a completed
        child whose newest report carries a GENUINE (non-operator-
        scoped) hit surfaces."""

    @pytest.mark.parametrize(
        "non_delivered_status",
        ["terminated", "error", "failed"],
    )
    def test_terminated_error_failed_do_not_suppress(self, non_delivered_status):
        """(a) Work genuinely unfinished statuses keep their advisories."""
        report = _internal_report(
            "Still pending the final run.",
            stamp_id=f"s6-{non_delivered_status}",
        )
        tree_rows = _rows((_GITER_CHILD_ID, non_delivered_status))

        signals = collect_source_a_signals(
            _delegated_state() + [report],
            tree_rows_provider=lambda: tree_rows,
        )

        assert signals.advisory_present is True, (
            f"status={non_delivered_status}: completed-only suppression "
            "must NOT apply — work genuinely unfinished"
        )
        assert signals.contradiction_flag is True
        assert len(signals.evidence) == 1

    def test_child_absent_from_tree_rows_kept(self):
        """(b) A child missing from the rows cannot be cross-resolved."""
        report = _internal_report(
            "Will report back after the follow-up merge.",
            stamp_id="s6-absent",
        )
        # Tree contains a DIFFERENT child, not the report's child.
        tree_rows = _rows((_REVIEWER_CHILD_ID, "completed"))

        signals = collect_source_a_signals(
            _delegated_state() + [report],
            tree_rows_provider=lambda: tree_rows,
        )

        assert signals.advisory_present is True, (
            "child absent from rows ⇒ conservative keep (no suppression)"
        )

    def test_later_contradiction_exception_reachable_on_completed_child(self):
        """(c) The later-contradiction exception RE-ARMS the advisory
        when a completed child's newest report carries a genuine hit."""
        # "still pending" sits in a non-operator sentence → genuine.
        genuine_hit = _internal_report(
            "Integration still pending - will report back.",
            stamp_id="s6-exception",
        )
        tree_rows = _rows((_GITER_CHILD_ID, "completed"))

        signals = collect_source_a_signals(
            _delegated_state() + [genuine_hit],
            tree_rows_provider=lambda: tree_rows,
        )

        # The exception engages: completed child + genuine newest hit
        # → the advisory SURFACES (the child-lie class — closed but
        # reachable).
        assert signals.advisory_present is True
        assert len(signals.evidence) == 1
        # Genuine (non-operator-scoped) hit drives the surface.
        assert "still pending" not in signals.evidence[0].operator_scoped_terms


# ─────────────────────────────────────────────────────────────────────────────
# Bonus A — bundle assembly integration (assemble_fused_bundle + A-scan)
# ─────────────────────────────────────────────────────────────────────────────


class TestBundleAssemblyForClearedAndKeptAdvisories:
    """The Stage-2 fused bundle consumes ``a_signals`` from
    ``collect_source_a_signals`` — these tests pin the bundle's A
    section so the cleared/retained decisions flow into the judge."""

    def test_cleared_advisory_drops_from_bundle_a_section(self):
        cleared_signals = collect_source_a_signals(
            _delegated_state()
            + [
                _internal_report(
                    "Phase 1 merged. Will report back after phase 2.",
                    stamp_id="bd-cleared-r1",
                ),
                _internal_report(
                    "Phase 2 merged. All shipped, no follow-ups.",
                    stamp_id="bd-cleared-r2",
                ),
            ],
            tree_rows_provider=lambda: _rows((_GITER_CHILD_ID, "completed")),
        )
        assert cleared_signals.advisory_present is False

        bundle = assemble_fused_bundle(
            a_signals=cleared_signals,
            b_signals=_b_quiet(),
            c_signals=_c_quiet(),
            c_tree_rows=[{"instance_id": _GITER_CHILD_ID, "status": "completed"}],
            ai_tail_messages=[AIMessage(content="Wrap-up complete.")],
        )

        # The A section header MUST still render — but with the
        # "(no Child Report Check notes delivered)" empty-state line.
        assert "SOURCE A: child-terminal contradiction evidence" in bundle.text
        assert "(no Child Report Check notes delivered)" in bundle.text
        # No child identifier leaks through into the cleared bundle.
        assert _GITER_CHILD_ID not in bundle.text

    def test_kept_advisory_renders_in_bundle_a_section(self):
        kept_signals = collect_source_a_signals(
            _delegated_state()
            + [
                _internal_report(
                    "Merged the branch. Will report back after the "
                    "follow-up merge.",
                    stamp_id="bd-kept-r1",
                ),
            ],
            tree_rows_provider=lambda: _rows((_GITER_CHILD_ID, "completed")),
        )
        assert kept_signals.advisory_present is True

        bundle = assemble_fused_bundle(
            a_signals=kept_signals,
            b_signals=_b_quiet(),
            c_signals=_c_quiet(),
            c_tree_rows=[{"instance_id": _GITER_CHILD_ID, "status": "completed"}],
            ai_tail_messages=[AIMessage(content="Wrap-up complete.")],
        )

        assert "SOURCE A: child-terminal contradiction evidence" in bundle.text
        # The kept evidence row carries the child id (redacted in the
        # bundle) AND the matched terms verbatim.
        assert "<redacted-child-" in bundle.text, (
            "kept evidence row must carry the redacted child id "
            "on the A-section evidence line"
        )
        assert "will report back" in bundle.text


# ─────────────────────────────────────────────────────────────────────────────
# Bonus B — end-to-end evaluate_resolver_activation per scenario
# ─────────────────────────────────────────────────────────────────────────────


class TestEvaluateResolverActivationPerScenario:
    """End-to-end through ``evaluate_resolver_activation`` — the gate
    calls this with the live facade reads. Pin the snapshot's
    ``would_be_outcome`` per scenario to assert the spec mapping."""

    @staticmethod
    def _quiet_eval(messages, tree_rows):
        return evaluate_resolver_activation(
            instance_id="indie-stale-a",
            gate_location="end_candidate",
            leader_prompt_version="v1",
            messages=messages,
            mode="enforce",
            attestation_enabled=True,
            scope_applicable=True,
            attestation_required=True,
            attested=False,
            user_answer_pending=False,
            c_values=_c_quiet(),
            b_values=_b_quiet(),
            denied_count=0,
            deny_bound=3,
            old_decision=Decision.DENIED,
            c_tree_rows_provider=lambda: tree_rows,
        )

    def test_acbf5627_quiet_eval_maps_to_would_deny_nudge_no_residual(self):
        """Snapshot outcome pin: with the stale advisory cleared, only
        c_quiet drives the deny band — the no-judge mapping is
        ``would_deny_nudge`` (no terminal escalation, count=0)."""
        messages = _delegated_state() + [
            _internal_report(
                "Will report back after phase 2.",
                stamp_id="er-acbf-r1",
            ),
            _internal_report(
                "All shipped. Done.",
                stamp_id="er-acbf-r2",
            ),
        ]
        snapshot = self._quiet_eval(
            messages, _rows((_GITER_CHILD_ID, "completed"))
        )

        assert snapshot.result.fired is True
        assert snapshot.result.band == BAND_DENY
        # The advisory cleared: a_signals shows zero evidence rows.
        assert snapshot.result.a_signals is not None
        assert snapshot.result.a_signals.advisory_present is False
        assert snapshot.result.a_signals.evidence == ()
        assert ara.TERM_A_SUSPICION not in snapshot.result.terms_fired
        # The would-be outcome: deny below the bound (count=0).
        assert snapshot.would_be_outcome == WOULD_DENY_NUDGE

    def test_fba90db8_quiet_eval_clears_and_does_not_escalate(self):
        messages = _delegated_state() + [
            _internal_report(
                "Still pending on my ledger.",
                stamp_id="er-fba-r1",
            ),
            _internal_report(
                "Approved and merged. Ledger clear.",
                stamp_id="er-fba-r2",
            ),
        ]
        snapshot = self._quiet_eval(messages, _rows((_GITER_CHILD_ID, "completed")))

        assert snapshot.result.band == BAND_DENY
        assert snapshot.result.a_signals.advisory_present is False
        assert snapshot.would_be_outcome == WOULD_DENY_NUDGE

    def test_child_lie_quiet_eval_engages_deny_with_real_a_signal(self):
        """Counterweight end-to-end: completed child whose newest
        report genuinely promises undelivered work keeps the A-signal
        AND the deny band. The would-be outcome is deny-nudge."""
        messages = _delegated_state() + [
            _internal_report(
                "Merged the branch. Will report back after the follow-up merge.",
                stamp_id="er-lie",
            ),
        ]
        snapshot = self._quiet_eval(messages, _rows((_GITER_CHILD_ID, "completed")))

        assert snapshot.result.fired is True
        assert snapshot.result.band == BAND_DENY
        assert snapshot.result.a_signals.advisory_present is True
        assert len(snapshot.result.a_signals.evidence) == 1
        assert ara.TERM_A_SUSPICION in snapshot.result.terms_fired
        # The bundle A-section carries the redacted child + terms.
        assert snapshot.result.bundle is not None
        assert "will report back" in snapshot.result.bundle.text

    def test_deny_bound_terminal_mapping_at_count_three(self):
        """Bound escalation pin: deny + denied_count==deny_bound ⇒
        would_terminal (the §4.3 mapping). Independent of stale-A."""
        messages = _delegated_state()
        snapshot = evaluate_resolver_activation(
            instance_id="indie-stale-a",
            gate_location="end_candidate",
            leader_prompt_version="v1",
            messages=messages,
            mode="enforce",
            attestation_enabled=True,
            scope_applicable=True,
            attestation_required=True,
            attested=False,
            user_answer_pending=False,
            c_values=_c_quiet(),
            b_values=_b_quiet(),
            denied_count=3,
            deny_bound=3,
            old_decision=Decision.DENIED,
            c_tree_rows_provider=lambda: [],
        )
        assert snapshot.would_be_outcome == WOULD_TERMINAL


# ─────────────────────────────────────────────────────────────────────────────
# Internal fixtures — quiet tree / quiet band B sources
# ─────────────────────────────────────────────────────────────────────────────


def _b_quiet() -> SourceBSignals:
    return SourceBSignals(
        marker_hit=False,
        marker_terms=(),
        length_trigger=False,
        final_word_count=500,
        attested=False,
    )


def _c_quiet() -> SourceCSignals:
    return SourceCSignals(
        pending_children=0,
        queued_or_expected_wakeups=0,
        live_descendants=0,
        busy_descendants=0,
    )


def _c_busy() -> SourceCSignals:
    return SourceCSignals(
        pending_children=0,
        queued_or_expected_wakeups=0,
        live_descendants=1,
        busy_descendants=1,
    )
