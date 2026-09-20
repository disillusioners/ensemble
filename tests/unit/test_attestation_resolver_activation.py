"""LCA unified resolver — Stage 1 parallel-dry activation predicate tests.

Spec: ``.agents/shared/planning/leader-completion-attestation/
resolver-unification.md`` (§4.1/§4.2/§4.3, §10 invariant tests) — Stage 1
additive shadow: pure activation predicate over sources A/B/C, fused-bundle
assembly (no LLM), and ONE structured ``leader_completion_resolver_eval``
log row per gate evaluation.

Locked user decisions mirrored here:
  * Δ2 — Source A is NOT busy-suppressed (a_suspicion fires ALONE even
    when ``busy_descendants > 0``). Source B IS busy-muted:
    ``b_fires := (marker_hit ∨ length_trigger) ∧ busy_descendants=0``.
  * R4/D10 mirror — ``¬attestation_required ⇒`` suspicion sources A/B
    are NEVER evaluated (short-circuit BEFORE the A/B provider calls).
  * §10.2 structural pin — ``busy>0 ⇒ ¬c_quiet`` (busy ⊆ live status
    sets in ``InstanceManager._count_descendants_busy_and_live``).
  * Budget parity — ZERO LLM calls in Stage 1 (sentinel test).

TDD ORDER NOTE: the R4 short-circuit test class
(:class:`TestR4ShortCircuitInvariant`) was written FIRST and failed on
import (module did not exist) before the implementation landed.
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import replace
from unittest.mock import MagicMock, call

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from daemon.services.attestation_gate import (
    Decision,
    GateSettings,
    evaluate as gate_evaluate,
)
from daemon.services.attestation_marker_scanner import (
    CHILD_TERMINAL_PROMISE_MARKERS,
)
from daemon.services import attestation_resolver_activation as ara
from daemon.services.attestation_resolver_activation import (
    BAND_A_SUSPICION,
    BAND_DENY,
    BAND_MARKER,
    BUNDLE_A_SECTION_MAX,
    BUNDLE_B_SECTION_MAX,
    BUNDLE_C_SECTION_MAX,
    BUNDLE_C_TREE_ROWS_MAX,
    BUNDLE_TOTAL_MAX,
    SourceASignals,
    SourceBSignals,
    SourceCSignals,
    WOULD_ALLOW,
    WOULD_DENY_NUDGE,
    WOULD_HINT,
    WOULD_TERMINAL,
    activation_predicate,
    assemble_fused_bundle,
    collect_source_a_signals,
    compute_would_be_outcome,
    map_old_decision_to_outcome,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures / builders
# ─────────────────────────────────────────────────────────────────────────────


def _c(
    pending: int = 0,
    wakeups: int = 0,
    live: int = 0,
    busy: int = 0,
    uap: bool = False,
) -> SourceCSignals:
    return SourceCSignals(
        pending_children=pending,
        queued_or_expected_wakeups=wakeups,
        live_descendants=live,
        busy_descendants=busy,
        user_answer_pending=uap,
    )


def _b(
    marker_hit: bool = False,
    length_trigger: bool = False,
    attested: bool = False,
    terms: tuple[str, ...] = (),
    words: int = 500,
) -> SourceBSignals:
    return SourceBSignals(
        marker_hit=marker_hit,
        marker_terms=terms if terms else (("ending turn",) if marker_hit else ()),
        length_trigger=length_trigger,
        final_word_count=(20 if length_trigger else words),
        attested=attested,
    )


def _a(advisory: bool = False) -> SourceASignals:
    return SourceASignals(
        advisory_present=advisory,
        phrase_match=advisory,
        contradiction_flag=False,
        word_count_below_threshold=False,
        promise_terms_total=1 if advisory else 0,
        evidence=(
            (
                ara.ChildReportCheckEvidence(
                    child_instance_id=str(uuid.uuid4()),
                    matched_terms=("ending turn", "will write"),
                    note_excerpt=(
                        "Child … completed while its final report promises "
                        "future work … likely premature completion."
                    ),
                    stable_id="child_report_check:…",
                    kwargs_surface_seen=True,
                ),
            )
            if advisory
            else ()
        ),
    )


def _pred(
    *,
    enabled: bool = True,
    scope: bool = True,
    mode: str = "enforce",
    required: bool = True,
    attested: bool = False,
    uap: bool = False,
    a=None,
    b=None,
    c=None,
    a_calls=None,
    b_calls=None,
):
    """Run the predicate with SPY providers (records call order)."""
    a_signals = _a() if a is None else a
    b_signals = _b() if b is None else b
    c_signals = _c() if c is None else c

    def a_source():
        if a_calls is not None:
            a_calls.append(1)
        return a_signals

    def b_source():
        if b_calls is not None:
            b_calls.append(1)
        return b_signals

    def c_source():
        return c_signals

    return activation_predicate(
        attestation_enabled=enabled,
        scope_applicable=scope,
        mode=mode,
        attestation_required=required,
        attested=attested,
        user_answer_pending=uap,
        a_source=a_source,
        b_source=b_source,
        c_source=c_source,
    )


def _child_report_check_note(
    child_id: str = "11111111-2222-3333-4444-555555555555",
    terms: tuple[str, ...] = ("ending turn", "will write"),
) -> HumanMessage:
    """Build a delivered Child Report Check note (canonical Stage-0 shape).

    Mirrors ``daemon/services/child_reports.py`` — body via the
    ``_make_context_message`` factory shape (``[SYSTEM CONTEXT: Child
    Report Check]`` prefix) + the ``context_kind`` /
    ``child_report_check`` / ``child_report_check_terms`` kwargs.
    """
    body = (
        f'Child {child_id} completed while its final report promises '
        f'future work ("{", ".join(terms)}") \u2014 likely premature '
        f"completion. Its promised next report will never arrive. "
        f"Verify the actual work state; if unfinished, revive it via "
        f'send_message (e.g. "continue your work") or verify its subtree '
        f"before relying on this report. (Advisory / heuristic \u2014 "
        f"marker scan is a substring match, not an LLM verdict.)"
    )
    msg = HumanMessage(content=f"[SYSTEM CONTEXT: Child Report Check]\n\n{body}")
    msg.additional_kwargs["context_kind"] = "child_report_check"
    msg.additional_kwargs["injected_message"] = True
    msg.additional_kwargs["child_report_check"] = True
    msg.additional_kwargs["child_report_check_terms"] = list(terms)
    msg.additional_kwargs["child_instance_id"] = child_id
    return msg


#: A representative valid UUID for the live-path fixtures (the regex
#: pattern in :data:`_INTERNAL_REPORT_ID_FROM_SOURCE_RE` enforces the
#: canonical 8-4-4-4-12 shape).
_INTERNAL_REPORT_FIXTURE_CHILD_ID = (
    "11111111-2222-3333-4444-555555555555"
)


def _internal_report_message(
    content: str = "Awaiting the final four. Ending turn.",
    child_id: str = _INTERNAL_REPORT_FIXTURE_CHILD_ID,
    completed_message_id: str | None = None,
    extra_kwargs: dict | None = None,
) -> HumanMessage:
    """Build a LIVE child completion-report ``HumanMessage``.

    Mirrors the stamp emitted by the report-injection drain at
    :file:`daemon/graph.py` line ~6950
    (``additional_kwargs={"injected_message": True, "source":
    f"internal_report:{report_child_iid}"}``) and the fallback
    ``PROCESS_REPORT`` task's enqueue-lane stamping via
    :func:`_stamped_additional_kwargs` at
    :file:`daemon/services/instance_messaging.py` line ~525.
    ``completed_message_id`` defaults to a deterministic UUID so the
    fixture is reproducible across test runs.
    """
    completed = completed_message_id or str(uuid.uuid4())
    source = f"internal_report:{child_id}:{completed}"
    msg = HumanMessage(
        content=content,
        id=str(uuid.uuid4()),
        additional_kwargs={
            "injected_message": True,
            "source": source,
        },
    )
    if extra_kwargs:
        msg.additional_kwargs.update(extra_kwargs)
    return msg


def _delegated_mission_state(final_text: str = "Done. All shipped.") -> list:
    delegation_ai = AIMessage(
        content="",
        tool_calls=[
            {"name": "send_message", "args": {"target": "child-id"}, "id": "c1"}
        ],
    )
    return [
        HumanMessage(content="please do it"),
        delegation_ai,
        AIMessage(content=final_text),
    ]


class _Row:
    """Fake instance row for the tree-rows provider tests."""

    def __init__(self, instance_id: str, status: str = "running", agent_id: str = "worker"):
        self.instance_id = instance_id
        self.status = status
        self.agent_id = agent_id


# ─────────────────────────────────────────────────────────────────────────────
# R4 INVARIANT — written FIRST (TDD). ¬attestation_required ⇒ A/B never run.
# ─────────────────────────────────────────────────────────────────────────────


class TestR4ShortCircuitInvariant:
    """§10.1 / R4 mirror: no delegation ⇒ suspicion sources NEVER evaluated."""

    def test_no_delegation_never_evaluates_a_or_b(self):
        a_calls: list = []
        b_calls: list = []
        result = _pred(required=False, a_calls=a_calls, b_calls=b_calls)
        assert result.fired is False
        assert result.band == ""
        assert result.meta_bypass is True
        assert a_calls == [], "Source A provider MUST NOT run when ¬attestation_required"
        assert b_calls == [], "Source B provider MUST NOT run when ¬attestation_required"
        # Even when A/B signals WOULD fire (suspicion present, quiet tree)
        # the short-circuit wins.
        a_calls.clear()
        b_calls.clear()
        result = _pred(
            required=False,
            a=_a(advisory=True),
            b=_b(marker_hit=True),
            c=_c(),  # quiet
            a_calls=a_calls,
            b_calls=b_calls,
        )
        assert result.fired is False
        assert a_calls == [] and b_calls == []

    def test_attested_never_evaluates_a_or_b(self):
        a_calls: list = []
        b_calls: list = []
        result = _pred(attested=True, a_calls=a_calls, b_calls=b_calls)
        assert result.fired is False
        assert result.meta_bypass is True
        assert a_calls == [] and b_calls == []

    def test_user_answer_pending_never_evaluates_a_or_b(self):
        a_calls: list = []
        b_calls: list = []
        result = _pred(uap=True, a_calls=a_calls, b_calls=b_calls)
        assert result.fired is False
        assert result.meta_bypass is True
        assert a_calls == [] and b_calls == []

    def test_delegated_mission_does_evaluate_a_and_b(self):
        a_calls: list = []
        b_calls: list = []
        result = _pred(required=True, a_calls=a_calls, b_calls=b_calls)
        assert a_calls == [1], "Source A MUST be evaluated on a delegated mission"
        assert b_calls == [1], "Source B MUST be evaluated on a delegated mission"
        assert result.meta_bypass is False


# ─────────────────────────────────────────────────────────────────────────────
# Term 0 — scope / mode outermost terms (R2 target shape)
# ─────────────────────────────────────────────────────────────────────────────


class TestTerm0ScopeMode:
    def test_attestation_enabled_false_short_circuits_everything(self):
        a_calls: list = []
        b_calls: list = []
        result = _pred(enabled=False, a_calls=a_calls, b_calls=b_calls)
        assert result.fired is False
        assert result.meta_bypass is True
        assert a_calls == [] and b_calls == []

    def test_scope_not_applicable_short_circuits_everything(self):
        a_calls: list = []
        b_calls: list = []
        result = _pred(scope=False, a_calls=a_calls, b_calls=b_calls)
        assert result.fired is False
        assert a_calls == [] and b_calls == []

    def test_mode_off_short_circuits_everything(self):
        a_calls: list = []
        b_calls: list = []
        result = _pred(mode="off", a_calls=a_calls, b_calls=b_calls)
        assert result.fired is False
        assert a_calls == [] and b_calls == []

    def test_mode_off_beats_c_quiet(self):
        # off is outermost — even the deny band (quiet tree) cannot fire.
        result = _pred(mode="off", c=_c(), b=_b(marker_hit=True), a=_a(advisory=True))
        assert result.fired is False
        assert result.band == ""

    def test_dry_mode_computes_normally(self):
        # Dry = activation computed + logged, node skipped (R3 target);
        # the predicate itself is mode-blind except "off".
        result = _pred(mode="dry", c=_c())
        assert result.fired is True
        assert result.band == BAND_DENY
        assert result.mode == "dry"


# ─────────────────────────────────────────────────────────────────────────────
# Core predicate matrix — §4.2 semantics
# ─────────────────────────────────────────────────────────────────────────────


class TestPredicateMatrix:
    def test_c_quiet_unattested_fires_deny_band(self):
        result = _pred(c=_c(), b=_b(), a=_a())
        assert result.fired is True
        assert result.band == BAND_DENY
        assert result.terms_fired == ("c_quiet",)

    def test_c_not_quiet_no_triggers_no_suspicion_does_not_fire(self):
        result = _pred(c=_c(pending=1), b=_b(), a=_a())
        assert result.fired is False
        assert result.band == ""
        assert result.terms_fired == ()

    def test_marker_band_when_not_quiet_and_busy_zero(self):
        result = _pred(
            c=_c(pending=2),  # not quiet, busy=0
            b=_b(marker_hit=True),
            a=_a(),
        )
        assert result.fired is True
        assert result.band == BAND_MARKER
        assert result.terms_fired == ("b_fires",)

    def test_length_trigger_alone_fires_marker_band(self):
        result = _pred(
            c=_c(wakeups=1),
            b=_b(length_trigger=True),
            a=_a(),
        )
        assert result.fired is True
        assert result.band == BAND_MARKER

    def test_busy_mutes_source_b(self):
        # R5: b_fires := (marker_hit ∨ length_trigger) ∧ busy_descendants=0
        result = _pred(
            c=_c(live=3, busy=3),  # busy>0 ⇒ live>0 ⇒ ¬c_quiet
            b=_b(marker_hit=True, length_trigger=True),
            a=_a(),
        )
        assert result.fired is False, "busy must mute the marker band entirely"
        assert result.band == ""
        assert "b_fires" not in result.terms_fired

    def test_delta2_a_band_fires_alone_while_busy(self):
        # THE Δ2 ROW — Source A is NOT busy-suppressed. Markers busy-muted,
        # tree not quiet (busy ⊆ live ⇒ live>0), A fires ALONE.
        result = _pred(
            c=_c(live=2, busy=2),
            b=_b(marker_hit=True),  # would fire, but busy mutes it
            a=_a(advisory=True),
        )
        assert result.fired is True
        assert result.band == BAND_A_SUSPICION
        assert result.terms_fired == ("a_suspicion",)

    def test_a_band_requires_not_quiet_and_no_b(self):
        result = _pred(
            c=_c(live=1),  # not quiet, busy=0
            b=_b(),
            a=_a(advisory=True),
        )
        assert result.fired is True
        assert result.band == BAND_A_SUSPICION

    def test_deny_band_wins_over_marker_and_a(self):
        # quiet tree + marker + suspicion → deny band (precedence).
        result = _pred(c=_c(), b=_b(marker_hit=True), a=_a(advisory=True))
        assert result.fired is True
        assert result.band == BAND_DENY
        assert set(result.terms_fired) == {"c_quiet", "b_fires", "a_suspicion"}

    def test_marker_band_wins_over_a(self):
        result = _pred(
            c=_c(pending=1),
            b=_b(marker_hit=True),
            a=_a(advisory=True),
        )
        assert result.fired is True
        assert result.band == BAND_MARKER
        assert set(result.terms_fired) == {"b_fires", "a_suspicion"}

    def test_structural_pin_busy_positive_implies_not_quiet(self):
        # §10.2 — busy>0 ⇒ ¬c_quiet. The pure predicate CANNOT see the
        # busy⊆live subset property (ints only); the pin lives at the
        # facade status-set level (TestSourceSetPins below) AND here as
        # the reachable-matrix consequence: every busy>0 row must have
        # live>0 so the deny band stays unreachable.
        result = _pred(
            c=_c(live=1, busy=1),  # the REACHABLE busy>0 shape
            b=_b(),
            a=_a(),
        )
        assert result.fired is False  # nothing fires: ¬quiet, b muted, no A

    def test_attested_snapshotted_on_result(self):
        result = _pred(attested=True)
        assert result.attested is True
        assert result.fired is False


class TestSourceSetPins:
    """§10.2 structural pin — busy status set ⊆ live status set (source pin).

    The subset property lives as inline set literals inside
    ``InstanceManager._count_descendants_busy_and_live``; this test
    extracts them from the source and asserts the subset — a future
    edit that adds a busy status not in the live set (or removes a live
    status that busy relies on) breaks this pin.
    """

    def test_busy_subset_of_live_in_manager_helper(self):
        import inspect

        from daemon import manager as manager_mod

        src = inspect.getsource(
            manager_mod.InstanceManager._count_descendants_busy_and_live
        )
        busy_match = re.search(
            r"busy_statuses = \{(.*?)\}", src, re.DOTALL
        )
        live_match = re.search(
            r"unconditional_live_statuses = busy_statuses \| \{(.*?)\}", src, re.DOTALL
        )
        assert busy_match, "busy_statuses literal not found in helper source"
        assert live_match, "unconditional_live_statuses literal not found"

        def parse_values(body: str) -> set[str]:
            # Statuses are ``InstanceStatus.RUNNING.value`` literals.
            return set(re.findall(r"InstanceStatus\.([A-Z_]+)\.value", body))

        busy = parse_values(busy_match.group(1))
        unconditional_live = parse_values(live_match.group(1)) | busy
        assert busy, "busy status set must be non-empty"
        assert busy <= unconditional_live, (
            f"busy statuses {busy - unconditional_live} missing from the "
            f"unconditional-live set — breaks busy>0 ⇒ live>0 ⇒ ¬c_quiet"
        )
        assert "PAUSED" in unconditional_live, (
            "PAUSED must stay live-for-deny-protection (b08f40fe amendment)"
        )
        assert "PAUSED" not in busy, "PAUSED is NOT busy (suspect, not healthy)"


# ─────────────────────────────────────────────────────────────────────────────
# C-read failure — whole-eval fail-open plain-allow
# ─────────────────────────────────────────────────────────────────────────────


class TestCReadFailureFailOpen:
    def test_c_provider_raise_fails_open(self):
        def exploding_c():
            raise RuntimeError("db down")

        result = activation_predicate(
            attestation_enabled=True,
            scope_applicable=True,
            mode="enforce",
            attestation_required=True,
            attested=False,
            user_answer_pending=False,
            a_source=lambda: (_ for _ in ()).throw(AssertionError("A must not run")),
            b_source=lambda: (_ for _ in ()).throw(AssertionError("B must not run")),
            c_source=exploding_c,
        )
        assert result.fail_open is True
        assert result.fired is False
        assert result.band == ""
        assert result.fail_open_error_class == "RuntimeError"

    def test_fail_open_maps_to_would_allow(self):
        def exploding_c():
            raise RuntimeError("db down")

        result = activation_predicate(
            attestation_enabled=True,
            scope_applicable=True,
            mode="enforce",
            attestation_required=True,
            attested=False,
            user_answer_pending=False,
            a_source=_a,
            b_source=_b,
            c_source=exploding_c,
        )
        assert compute_would_be_outcome(result, denied_count=0, deny_bound=3) == WOULD_ALLOW


# ─────────────────────────────────────────────────────────────────────────────
# Would-be-outcome mapping — §4.3 no-judge mapping (Stage 1, zero LLM)
# ─────────────────────────────────────────────────────────────────────────────


class TestWouldBeOutcomeMapping:
    def test_not_fired_maps_to_would_allow(self):
        result = _pred(c=_c(pending=1))
        assert compute_would_be_outcome(result, denied_count=0, deny_bound=3) == WOULD_ALLOW

    def test_deny_band_maps_to_would_deny_nudge(self):
        result = _pred(c=_c())
        assert compute_would_be_outcome(result, denied_count=0, deny_bound=3) == WOULD_DENY_NUDGE

    def test_deny_band_at_bound_maps_to_would_terminal(self):
        result = _pred(c=_c())
        assert compute_would_be_outcome(result, denied_count=3, deny_bound=3) == WOULD_TERMINAL

    def test_deny_band_below_bound_stays_nudge(self):
        result = _pred(c=_c())
        assert compute_would_be_outcome(result, denied_count=2, deny_bound=3) == WOULD_DENY_NUDGE

    def test_marker_band_with_pending_maps_to_would_hint(self):
        # marker band ⇒ ¬c_quiet ⇒ route-(b) pending predicate true.
        result = _pred(c=_c(pending=2), b=_b(marker_hit=True))
        assert result.band == BAND_MARKER
        assert compute_would_be_outcome(result, denied_count=0, deny_bound=3) == WOULD_HINT

    def test_a_band_with_pending_maps_to_would_hint(self):
        result = _pred(c=_c(live=1), a=_a(advisory=True))
        assert result.band == BAND_A_SUSPICION
        assert compute_would_be_outcome(result, denied_count=0, deny_bound=3) == WOULD_HINT

    def test_marker_band_no_pending_arm_exists_but_is_pure(self):
        # The pure function keeps the explicit pending arm — pin it via a
        # synthetic marker-band result with a quiet C (structurally
        # unreachable via the predicate — marker band REQUIRES ¬c_quiet —
        # but the Stage-2 node may compose bands differently).
        result = _pred(c=_c(pending=1), b=_b(marker_hit=True))
        quiet = replace(result, c_signals=_c())
        assert compute_would_be_outcome(quiet, denied_count=0, deny_bound=3) == WOULD_ALLOW

    def test_old_decision_mapping(self):
        assert map_old_decision_to_outcome(Decision.ALLOWED) == WOULD_ALLOW
        assert (
            map_old_decision_to_outcome(Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP)
            == WOULD_ALLOW
        )
        assert map_old_decision_to_outcome(Decision.DRY_LOG) == WOULD_ALLOW
        assert map_old_decision_to_outcome(Decision.DENIED) == WOULD_DENY_NUDGE
        assert map_old_decision_to_outcome(Decision.TERMINAL_AFTER_BOUND) == WOULD_TERMINAL

    def test_agreement_true_when_classes_match(self):
        from daemon.services.attestation_resolver_activation import compute_agreement

        assert compute_agreement(WOULD_ALLOW, WOULD_ALLOW) is True
        assert compute_agreement(WOULD_DENY_NUDGE, WOULD_DENY_NUDGE) is True
        assert compute_agreement(WOULD_TERMINAL, WOULD_TERMINAL) is True

    def test_agreement_false_on_divergence(self):
        from daemon.services.attestation_resolver_activation import compute_agreement

        assert compute_agreement(WOULD_DENY_NUDGE, WOULD_ALLOW) is False
        assert compute_agreement(WOULD_ALLOW, WOULD_DENY_NUDGE) is False
        # would_hint can never agree at the evaluate() seam — the old
        # path has no hint outcome there (hint rides graph.py route (b)).
        assert compute_agreement(WOULD_HINT, WOULD_ALLOW) is False


# ─────────────────────────────────────────────────────────────────────────────
# Source A collection — the LANDED Stage-0 producer contract
# ─────────────────────────────────────────────────────────────────────────────


class TestSourceACollection:
    def test_note_detected_via_kwargs_and_prefix(self):
        messages = _delegated_mission_state() + [_child_report_check_note()]
        signals = collect_source_a_signals(messages)
        assert signals.advisory_present is True
        assert signals.phrase_match is True
        assert len(signals.evidence) == 1
        ev = signals.evidence[0]
        assert ev.child_instance_id == "11111111-2222-3333-4444-555555555555"
        assert ev.matched_terms == ("ending turn", "will write")
        assert ev.kwargs_surface_seen is True

    def test_note_detected_by_content_prefix_without_kwargs(self):
        # Delivery-path robustness: the drain may not preserve kwargs —
        # the canonical ``[SYSTEM CONTEXT: Child Report Check]`` prefix
        # is the always-present surface.
        note = _child_report_check_note()
        note.additional_kwargs = {}  # strip kwargs entirely
        signals = collect_source_a_signals(_delegated_mission_state() + [note])
        assert signals.advisory_present is True
        assert signals.evidence[0].kwargs_surface_seen is False
        # child id re-derived from the note body ("Child {uuid} completed")
        assert (
            signals.evidence[0].child_instance_id
            == "11111111-2222-3333-4444-555555555555"
        )
        # terms re-derived by re-scanning the note body (it quotes them)
        assert "ending turn" in signals.evidence[0].matched_terms

    def test_no_note_no_suspicion(self):
        signals = collect_source_a_signals(_delegated_mission_state())
        assert signals.advisory_present is False
        assert signals.evidence == ()

    def test_other_context_kinds_ignored(self):
        other = HumanMessage(content="[SYSTEM CONTEXT: Skills]\n\nskill text")
        other.additional_kwargs["context_kind"] = "skills"
        signals = collect_source_a_signals(_delegated_mission_state() + [other])
        assert signals.advisory_present is False

    def test_evidence_bounded(self):
        notes = [
            _child_report_check_note(child_id=str(uuid.uuid4()))
            for _ in range(ara.A_EVIDENCE_NOTES_CAP + 3)
        ]
        signals = collect_source_a_signals(_delegated_mission_state() + notes)
        assert len(signals.evidence) == ara.A_EVIDENCE_NOTES_CAP

    # ─────────────────────────────────────────────────────────────────────
    # Live child-report scan path (2026-09-18 restoration, post-D-CTD-7).
    #
    # The LCA "Child Report Check" advisory note mint was REMOVED in
    # commit 6a695b8f; the A-band signal now lives in the live
    # child-report ``HumanMessage`` rows the report-injection drain
    # emits (graph.py:6950-6967 stamp) and the fallback PROCESS_REPORT
    # task's enqueue-lane stamping (instance_messaging.py:525). These
    # tests verify the new scan path re-derives the 4-field Source-A OR
    # signal set from the transcript scan.
    # ─────────────────────────────────────────────────────────────────────

    def test_internal_report_with_catalog_phrase_fires(self):
        """The live child-report path: a ``source=internal_report:…``
        ``HumanMessage`` whose content matches the 17-pattern catalog
        must produce an ``advisory_present=True`` A-signal through the
        real code path.

        FUNCTIONAL PIN — re-anchored from the deleted mint-time
        signature to the evaluation-time scan.
        """
        messages = _delegated_mission_state() + [
            _internal_report_message(
                content="Awaiting the final report. Ending turn."
            )
        ]
        signals = collect_source_a_signals(messages)
        assert signals.advisory_present is True
        assert signals.phrase_match is True
        assert len(signals.evidence) == 1
        ev = signals.evidence[0]
        assert ev.child_instance_id == _INTERNAL_REPORT_FIXTURE_CHILD_ID
        assert "ending turn" in ev.matched_terms
        assert ev.kwargs_surface_seen is True

    def test_internal_report_without_catalog_phrase_does_not_fire(self):
        """Clean completion-report content (no catalog phrases)
        must NOT raise the A-band. The 17-pattern catalog is preserved
        byte-identical; the scan maps verbatim from
        ``scan_child_terminal_report_for_promises``.
        """
        messages = _delegated_mission_state() + [
            _internal_report_message(
                content=(
                    "Done. 5/5 tests pass. Merged abc123. "
                    "All shipped, no follow-ups."
                )
            )
        ]
        signals = collect_source_a_signals(messages)
        assert signals.advisory_present is False
        assert signals.phrase_match is False
        assert signals.evidence == ()

    def test_user_injected_note_does_not_match_internal_report_path(self):
        """A user-injected note (``injected_message=True`` with NO
        ``source=internal_report:`` stamp) must NOT be detected by the
        live scan — the ``source``-prefix is the disambiguator. User
        notes are NOT A-band evidence.
        """
        user_note = HumanMessage(
            content="Awaiting the final four. Ending turn.",
            additional_kwargs={"injected_message": True},
        )
        signals = collect_source_a_signals(
            _delegated_mission_state() + [user_note]
        )
        assert signals.advisory_present is False
        assert signals.evidence == ()

    def test_a_system_message_with_internal_report_source_does_not_match(self):
        """Defense — the live scan requires an ``HumanMessage`` (the
        live delivery shape). An ``AIMessage`` even with the source
        stamp cannot be confused for a child-report delivery.
        """
        ai_with_stamp = AIMessage(
            content="Awaiting the final report. Ending turn.",
        )
        ai_with_stamp.additional_kwargs["source"] = (
            f"internal_report:{_INTERNAL_REPORT_FIXTURE_CHILD_ID}:msg"
        )
        signals = collect_source_a_signals(
            _delegated_mission_state() + [ai_with_stamp]
        )
        assert signals.advisory_present is False

    def test_contradiction_flag_raised_on_explicit_marker(self):
        """A matched_terms item in :data:`_CONTRADICTION_MARKERS`
        raises the ``contradiction_flag`` sub-signal. Verifies the
        4-field OR's most-pointed branch has live semantics.
        """
        messages = _delegated_mission_state() + [
            _internal_report_message(
                content="Work is still pending — awaiting more input."
            )
        ]
        signals = collect_source_a_signals(messages)
        assert signals.advisory_present is True
        assert signals.contradiction_flag is True

    def test_word_count_below_threshold_raised_on_short_report(self):
        """A matched child-report whose raw word count is below
        :data:`ara._SHORT_REPORT_WORD_THRESHOLD` (150) raises
        ``word_count_below_threshold`` — Source B's ``length_trigger``
        mirror.
        """
        # Build a short report (~20 words) with a single promise marker.
        short_content = (
            "I will continue the writeup later. Ending turn."
        )
        assert len(short_content.split()) < 150
        messages = _delegated_mission_state() + [
            _internal_report_message(content=short_content)
        ]
        signals = collect_source_a_signals(messages)
        assert signals.advisory_present is True
        assert signals.word_count_below_threshold is True

    def test_legacy_note_and_live_report_paths_coexist(self):
        """Both the legacy note path (defense-in-depth for historical
        checkpoints) AND the live child-report path (the new live
        source) produce evidence rows when both are present in the
        conversation. Each path runs independently and is bounded by
        :data:`ara.A_EVIDENCE_NOTES_CAP`.
        """
        messages = (
            _delegated_mission_state()
            + [_child_report_check_note(child_id=str(uuid.uuid4()))]
            + [_internal_report_message(child_id=str(uuid.uuid4()))]
        )
        signals = collect_source_a_signals(messages)
        assert signals.advisory_present is True
        assert len(signals.evidence) == 2


# ─────────────────────────────────────────────────────────────────────────────
# Fused bundle assembly — spec §4.1 caps, Δ1 + Δ3
# ─────────────────────────────────────────────────────────────────────────────


def _tree_rows(n: int) -> list[dict]:
    return [
        {
            "instance_id": f"{uuid.uuid4()}",
            "status": "running" if i % 2 else "waiting",
            "agent_id": "worker",
        }
        for i in range(n)
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Dual-autopsy B1 stale-A fix (2026-09-20) — newest-only per child,
# operator-action scoping, cross-resolution vs C, B-clip 2500.
#
# Grounded in incidents acbf5627 (4/4 final advisories stale — giter
# phase-stop protocol language ×3 + operator-scope "still pending
# (rebuild+restart)") and fba90db8 (reviewer "still pending on my
# ledger" for work later APPROVED). Band structure unchanged —
# a_suspicion stays a band; only its evidence quality changes.
# ─────────────────────────────────────────────────────────────────────────────


class TestNewestReportOnlyPerChild:
    """B1 item 1: each child's LATEST internal_report is the ONLY one
    scanned; superseded reports from the same child drop wholesale."""

    def test_superseded_phase_stop_reports_drop_when_final_clean(self):
        """INCIDENT acbf5627 shape: giter-style phase-stop protocol
        language ×2 superseded by a clean final report → NO stale
        advisories survive in A."""
        r1 = _internal_report_message(
            "Phase 1 done, will report back after phase 2.",
            completed_message_id=str(uuid.uuid4()),
        )
        r2 = _internal_report_message(
            "Phase 2 done, then I aggregate the ledger.",
            completed_message_id=str(uuid.uuid4()),
        )
        final = _internal_report_message(
            "Done. 5/5 tests pass. All shipped, no follow-ups."
        )
        signals = collect_source_a_signals(
            _delegated_mission_state() + [r1, r2, final]
        )
        assert signals.advisory_present is False
        assert signals.phrase_match is False
        assert signals.contradiction_flag is False
        assert signals.evidence == ()

    def test_only_latest_report_scanned_terms_are_latest_not_union(self):
        """The operative evidence carries ONLY the newest report's
        matched terms — the superseded report's terms do NOT union in."""
        r1 = _internal_report_message(
            "Integration still pending on my side.",
            completed_message_id=str(uuid.uuid4()),
        )
        r2 = _internal_report_message(
            "Merged. I will report back after the follow-up merge."
        )
        signals = collect_source_a_signals(
            _delegated_mission_state() + [r1, r2]
        )
        assert len(signals.evidence) == 1
        ev = signals.evidence[0]
        assert ev.matched_terms == ("will report back",)
        assert "still pending" not in ev.matched_terms
        # The newest report has no contradiction marker → flag down
        # (the superseded "still pending" must not resurrect it).
        assert signals.contradiction_flag is False

    def test_earlier_only_contradiction_does_not_resurface_when_latest_clean(
        self,
    ):
        """PINNED EDGE SEMANTICS (B1 item 1 + item 2 boundary): an
        EARLIER report's contradiction that the LATEST report does not
        repeat does NOT resurface when the child delivered a clean
        final report — delivery supersedes promise. The lie must live
        in the child's NEWEST report to surface (see
        TestCrossResolveAgainstTreeRows.test_child_lie_completed_
        without_delivery_still_fires for the surfacing half)."""
        r1 = _internal_report_message(
            "Still pending integration, ending turn.",
            completed_message_id=str(uuid.uuid4()),
        )
        final = _internal_report_message(
            "Integration complete. All checks green. Shipped."
        )
        rows = [
            {
                "instance_id": _INTERNAL_REPORT_FIXTURE_CHILD_ID,
                "status": "completed",
                "agent_id": "giter",
            }
        ]
        signals = collect_source_a_signals(
            _delegated_mission_state() + [r1, final],
            tree_rows_provider=lambda: rows,
        )
        assert signals.advisory_present is False
        assert signals.evidence == ()

    def test_per_child_independence(self):
        """Newest-only is PER CHILD: child A's clean final must not
        erase child B's live advisory."""
        child_b = "22222222-3333-4444-5555-666666666666"
        a_clean = _internal_report_message("Done. All shipped.")
        a_stale = _internal_report_message(
            "still pending review",
            completed_message_id=str(uuid.uuid4()),
        )
        b_hit = _internal_report_message(
            "Will report back after the merge.", child_id=child_b
        )
        signals = collect_source_a_signals(
            _delegated_mission_state() + [a_stale, a_clean, b_hit]
        )
        assert signals.advisory_present is True
        assert len(signals.evidence) == 1
        assert signals.evidence[0].child_instance_id == child_b

    def test_idless_reports_group_under_none_newest_wins(self):
        """Reports whose child id cannot be parsed share the ``None``
        bucket — newest wins (id-less reports are indistinguishable)."""
        import uuid as _uuid

        def _idless(content):
            msg = HumanMessage(
                content=content,
                id=str(_uuid.uuid4()),
                additional_kwargs={
                    "injected_message": True,
                    "source": "internal_report:not-a-uuid",
                },
            )
            return msg

        first = _idless("still pending integration")
        newest = _idless("Done. All shipped.")
        signals = collect_source_a_signals(
            _delegated_mission_state() + [first, newest]
        )
        assert signals.advisory_present is False
        assert signals.evidence == ()

    def test_reviewer_pending_then_approved_suppressed(self):
        """INCIDENT fba90db8 shape: reviewer's "still pending on my
        ledger" superseded by a later APPROVED report → suppressed."""
        pending = _internal_report_message(
            "Still pending on my ledger — awaiting the merge decision.",
            completed_message_id=str(uuid.uuid4()),
        )
        approved = _internal_report_message(
            "Approved and merged (abc123). Ledger clear, nothing pending."
        )
        signals = collect_source_a_signals(
            _delegated_mission_state() + [pending, approved]
        )
        assert signals.advisory_present is False
        assert signals.evidence == ()


class TestOperatorScopedHits:
    """B1 item 3: catalog hits whose sentence names an operator action
    (rebuild/restart/redeploy) are the OPERATOR's pending action —
    excluded from ``contradiction_flag``, never a child-work lie."""

    def test_operator_pending_sentence_excluded_from_contradiction_flag(self):
        """The canonical incident sentence "pending: rebuild+restart
        activation" is DEMOTED (advisory stays visible for the judge)
        but does NOT raise ``contradiction_flag``."""
        signals = collect_source_a_signals(
            _delegated_mission_state()
            + [
                _internal_report_message(
                    "Phase 1 merged. Still pending: rebuild+restart "
                    "activation (operator action)."
                )
            ]
        )
        assert signals.advisory_present is True  # demoted, not excluded
        assert signals.contradiction_flag is False
        assert signals.evidence[0].operator_scoped_terms == ("still pending",)

    def test_genuine_pending_outside_operator_sentence_still_contradicts(self):
        """A "still pending" sentence WITHOUT an operator token keeps
        the old contradiction semantic even when the report mentions
        rebuild elsewhere (sentence-scope, not report-scope)."""
        signals = collect_source_a_signals(
            _delegated_mission_state()
            + [
                _internal_report_message(
                    "Integration still pending - will report back. "
                    "The operator can rebuild+restart afterwards."
                )
            ]
        )
        assert signals.contradiction_flag is True
        assert signals.evidence[0].operator_scoped_terms == ()

    def test_mixed_operator_and_genuine_terms(self):
        """Operator-scoped + genuine hits in ONE report: the genuine
        hit drives ``contradiction_flag``; the operator hit is recorded
        on the evidence for the judge."""
        signals = collect_source_a_signals(
            _delegated_mission_state()
            + [
                _internal_report_message(
                    "Still pending: rebuild+restart activation. "
                    "Interim state until the operator acts."
                )
            ]
        )
        assert signals.advisory_present is True
        assert signals.contradiction_flag is True  # "interim" is genuine
        assert signals.evidence[0].operator_scoped_terms == ("still pending",)
        assert "interim" in signals.evidence[0].matched_terms

    def test_operator_token_in_different_sentence_does_not_scope(self):
        """Sentence-boundary discipline: [.!?\\n;] separates — an
        operator token in the NEXT sentence cannot scope the hit
        (colon deliberately does NOT split: "pending: rebuild+restart"
        stays one span)."""
        signals = collect_source_a_signals(
            _delegated_mission_state()
            + [
                _internal_report_message(
                    "Still pending. Rebuild+restart required afterwards."
                )
            ]
        )
        assert signals.contradiction_flag is True
        assert signals.evidence[0].operator_scoped_terms == ()


class TestCrossResolveAgainstTreeRows:
    """B1 item 2: advisories from a child whose tree status is
    ``completed`` are suppressed UNLESS the child's newest report still
    carries a GENUINE hit (the later-contradiction exception — a
    completed child that lied at the end must still surface)."""

    def test_child_lie_completed_without_delivery_still_fires(self):
        """FLAGSHIP REGRESSION (the child-lie class pin): a child whose
        FINAL report promises future work ("will report back after the
        follow-up merge") and NEVER delivers it, completed-without-
        delivery → the advisory STILL surfaces, a_suspicion still fires,
        and the A/C-subordination still applies (a_suspicion remains a
        band; the judge prompt retains genuine-advisory subordination).
        This pin closed the child-lie class — it must NOT regress."""
        lie = _internal_report_message(
            "Merged the branch. I will report back after the "
            "follow-up merge."
        )
        rows = [
            {
                "instance_id": _INTERNAL_REPORT_FIXTURE_CHILD_ID,
                "status": "completed",
                "agent_id": "coder",
            }
        ]
        signals = collect_source_a_signals(
            _delegated_mission_state() + [lie],
            tree_rows_provider=lambda: rows,
        )
        # The later-contradiction exception keeps the lie.
        assert signals.advisory_present is True
        assert len(signals.evidence) == 1
        assert "will report back" in signals.evidence[0].matched_terms
        assert signals.contradiction_flag is False  # promise, not marker
        # Band structure unchanged: with a live descendant (¬quiet, b
        # quiet) a_suspicion fires ALONE as its own band (Δ2 stays).
        result = _pred(a=signals, b=_b(), c=_c(live=1))
        assert result.fired is True
        assert result.band == BAND_A_SUSPICION
        assert ara.TERM_A_SUSPICION in result.terms_fired
        # Quiet-tree variant: the deny band still engages (the judge,
        # not the band, now adjudicates — with the softened prompt that
        # still subordinates GENUINE unresolved advisories).
        result_quiet = _pred(a=signals, b=_b(), c=_c())
        assert result_quiet.fired is True
        assert result_quiet.band == BAND_DENY

    def test_completed_child_operator_pending_suppressed(self):
        """INCIDENT acbf5627 final shape: completed child whose newest
        report's only hit is operator-scoped → suppressed wholesale."""
        report = _internal_report_message(
            "Phase work merged. Still pending: rebuild+restart "
            "activation (operator action)."
        )
        rows = [
            {
                "instance_id": _INTERNAL_REPORT_FIXTURE_CHILD_ID,
                "status": "completed",
                "agent_id": "giter",
            }
        ]
        signals = collect_source_a_signals(
            _delegated_mission_state() + [report],
            tree_rows_provider=lambda: rows,
        )
        assert signals.advisory_present is False
        assert signals.evidence == ()

    def test_running_child_operator_pending_kept(self):
        """Non-completed child: NO suppression (only the delivered
        status cross-resolves) — the operator-scoped advisory stays
        visible (demoted from contradiction_flag by item 3 only)."""
        report = _internal_report_message(
            "Still pending: rebuild+restart activation."
        )
        rows = [
            {
                "instance_id": _INTERNAL_REPORT_FIXTURE_CHILD_ID,
                "status": "running",
                "agent_id": "giter",
            }
        ]
        signals = collect_source_a_signals(
            _delegated_mission_state() + [report],
            tree_rows_provider=lambda: rows,
        )
        assert signals.advisory_present is True
        assert signals.contradiction_flag is False

    def test_child_absent_from_rows_kept(self):
        """A child missing from the tree rows cannot be cross-resolved
        → conservative keep."""
        report = _internal_report_message("Will report back after merge.")
        rows = [
            {
                "instance_id": "99999999-8888-7777-6666-555555555555",
                "status": "completed",
                "agent_id": "other",
            }
        ]
        signals = collect_source_a_signals(
            _delegated_mission_state() + [report],
            tree_rows_provider=lambda: rows,
        )
        assert signals.advisory_present is True

    def test_terminated_error_failed_statuses_do_not_suppress(self):
        """Only ``completed`` suppresses — terminated/error/failed
        children with advisories keep them (work genuinely unfinished)."""
        report = _internal_report_message("Still pending the final run.")
        for status in ("terminated", "error", "failed"):
            rows = [
                {
                    "instance_id": _INTERNAL_REPORT_FIXTURE_CHILD_ID,
                    "status": status,
                    "agent_id": "giter",
                }
            ]
            signals = collect_source_a_signals(
                _delegated_mission_state() + [report],
                tree_rows_provider=lambda rows=rows: rows,
            )
            assert signals.advisory_present is True, status

    def test_completed_status_case_insensitive(self):
        report = _internal_report_message("Still pending: rebuild+restart.")
        rows = [
            {
                "instance_id": _INTERNAL_REPORT_FIXTURE_CHILD_ID,
                "status": "Completed",
                "agent_id": "giter",
            }
        ]
        signals = collect_source_a_signals(
            _delegated_mission_state() + [report],
            tree_rows_provider=lambda: rows,
        )
        assert signals.advisory_present is False

    def test_provider_failure_keeps_advisories(self):
        """Best-effort: a raising provider ⇒ no rows ⇒ suppression
        no-ops (mirrors the C-provider fail-open discipline)."""
        def _boom():
            raise RuntimeError("db down")

        report = _internal_report_message(
            "Still pending: rebuild+restart activation."
        )
        rows = [
            {
                "instance_id": _INTERNAL_REPORT_FIXTURE_CHILD_ID,
                "status": "completed",
                "agent_id": "giter",
            }
        ]
        signals_with_rows = collect_source_a_signals(
            _delegated_mission_state() + [report],
            tree_rows_provider=lambda: rows,
        )
        assert signals_with_rows.advisory_present is False
        signals_boom = collect_source_a_signals(
            _delegated_mission_state() + [report],
            tree_rows_provider=_boom,
        )
        assert signals_boom.advisory_present is True

    def test_note_path_suppressed_for_completed_child_with_clean_final(self):
        """Legacy note entries participate in the cross-resolution: a
        completed child's note is suppressed when the child's newest
        live report is clean (delivered)."""
        child = "33333333-4444-5555-6666-777777777777"
        note = _child_report_check_note(child_id=child)
        final = _internal_report_message(
            "Done. All shipped.", child_id=child
        )
        rows = [
            {"instance_id": child, "status": "completed", "agent_id": "giter"}
        ]
        signals = collect_source_a_signals(
            _delegated_mission_state() + [note, final],
            tree_rows_provider=lambda: rows,
        )
        assert signals.advisory_present is False
        assert signals.evidence == ()

    def test_note_path_kept_when_no_live_report_exists(self):
        """A completed child with a note but NO live report in the
        transcript: conservative keep (nothing proves delivery)."""
        child = "33333333-4444-5555-6666-777777777777"
        note = _child_report_check_note(child_id=child)
        rows = [
            {"instance_id": child, "status": "completed", "agent_id": "giter"}
        ]
        signals = collect_source_a_signals(
            _delegated_mission_state() + [note],
            tree_rows_provider=lambda: rows,
        )
        assert signals.advisory_present is True
        assert len(signals.evidence) == 1

    def test_no_provider_no_suppression_backcompat(self):
        """Default (no provider) → NO cross-resolution — direct
        callers keep the pre-B1 semantics."""
        report = _internal_report_message(
            "Still pending: rebuild+restart activation."
        )
        signals = collect_source_a_signals(
            _delegated_mission_state() + [report]
        )
        assert signals.advisory_present is True
        assert len(signals.evidence) == 1

    def test_evaluate_resolver_activation_provider_fetched_once(self):
        """Wiring: the fetch-once cache shares the provider between the
        A-scan cross-resolution and the bundle's C-section — at most
        ONE provider call per evaluation; lazy (never called without
        advisory candidates)."""
        calls: list[int] = []

        def _counting_provider():
            calls.append(1)
            return [
                {
                    "instance_id": _INTERNAL_REPORT_FIXTURE_CHILD_ID,
                    "status": "completed",
                    "agent_id": "giter",
                }
            ]

        report = _internal_report_message(
            "Still pending: rebuild+restart activation."
        )
        snapshot = ara.evaluate_resolver_activation(
            instance_id="b1-cache-1",
            gate_location="end_candidate",
            leader_prompt_version="v1",
            messages=_delegated_mission_state() + [report],
            mode="enforce",
            attestation_enabled=True,
            scope_applicable=True,
            attestation_required=True,
            attested=False,
            user_answer_pending=False,
            c_values=_c(),
            b_values=_b(),
            denied_count=0,
            deny_bound=3,
            old_decision=Decision.DENIED,
            c_tree_rows_provider=_counting_provider,
        )
        # Delivered child + operator-scoped-only → suppressed → the
        # A-band is GONE (clean quiet-tree deny stands on C alone).
        assert snapshot.result.a_signals is not None
        assert snapshot.result.a_signals.advisory_present is False
        assert snapshot.result.a_signals.evidence == ()
        assert snapshot.result.fired is True
        assert snapshot.result.band == BAND_DENY
        # Provider ran EXACTLY ONCE (shared cache: A-scan + bundle).
        assert len(calls) == 1
        # And the bundle's C-section still got the rows.
        assert "SOURCE C: tree status" in snapshot.result.bundle.text

    def test_evaluate_resolver_activation_provider_lazy_without_advisories(
        self,
    ):
        """Lazy contract: a transcript with NO advisory candidates never
        invokes the provider during the A-scan; it runs only at bundle
        assembly (the predicate fired on the quiet tree)."""
        calls: list[int] = []

        def _counting_provider():
            calls.append(1)
            return []

        snapshot = ara.evaluate_resolver_activation(
            instance_id="b1-cache-2",
            gate_location="end_candidate",
            leader_prompt_version="v1",
            messages=_delegated_mission_state("All done, shipped."),
            mode="enforce",
            attestation_enabled=True,
            scope_applicable=True,
            attestation_required=True,
            attested=False,
            user_answer_pending=False,
            c_values=_c(),
            b_values=_b(),
            denied_count=0,
            deny_bound=3,
            old_decision=Decision.DENIED,
            c_tree_rows_provider=_counting_provider,
        )
        assert snapshot.result.fired is True
        assert snapshot.result.band == BAND_DENY
        assert len(calls) == 1  # bundle assembly only — never the A-scan


class TestBSectionClip2500:
    """B1 item 5: the B-section per-message clip raised 1500 → 2500
    (incident acbf5627: the final report's evidence tail + activation
    note were lost at 1500/2372 chars)."""

    def test_clip_constant_and_wiring(self):
        assert ara._B_MESSAGE_CLIP == 2500
        import inspect

        src = inspect.getsource(ara._build_b_section)
        assert "_B_MESSAGE_CLIP" in src, (
            "B1 item 5: _build_b_section must clip at the named "
            "constant (no bare literal regressions)"
        )

    @staticmethod
    def _bundle_with_tail(*contents: str) -> ara.FusedBundle:
        return assemble_fused_bundle(
            a_signals=SourceASignals(advisory_present=False, phrase_match=False),
            b_signals=_b(),
            c_signals=_c(),
            c_tree_rows=[],
            ai_tail_messages=[AIMessage(content=c) for c in contents],
        )

    def test_2500_char_message_rendered_in_full(self):
        bundle = self._bundle_with_tail("X" * 2500)
        lines = bundle.text.splitlines()
        rendered = [ln for ln in lines if ln.startswith("[1] ")]
        assert len(rendered) == 1
        body = rendered[0][len("[1] "):]
        assert body == "X" * 2500  # full — no ellipsis at the old cap

    def test_2501_char_message_clipped_at_2500(self):
        long = "X" * 2501 + "TAIL_SENTINEL_BEYOND_CLIP"
        bundle = self._bundle_with_tail(long)
        lines = bundle.text.splitlines()
        rendered = [ln for ln in lines if ln.startswith("[1] ")][0]
        body = rendered[len("[1] "):]
        assert len(body) == 2500  # clip bound inclusive of the ellipsis
        assert body.endswith("…")
        assert "TAIL_SENTINEL_BEYOND_CLIP" not in bundle.text

    def test_1501_char_message_no_longer_clipped(self):
        """The OLD boundary (1500) no longer truncates — the B1 fix's
        whole point (evidence tail between 1500 and 2500 survives)."""
        long = "Y" * 1501 + "ACTIVATION_NOTE_TAIL"
        bundle = self._bundle_with_tail(long)
        assert "ACTIVATION_NOTE_TAIL" in bundle.text
        lines = bundle.text.splitlines()
        rendered = [ln for ln in lines if ln.startswith("[1] ")][0]
        assert len(rendered) == len("[1] ") + 1501 + len("ACTIVATION_NOTE_TAIL")

    def test_three_maxed_messages_still_within_section_cap(self):
        """3 × (2500 + label) > BUNDLE_B_SECTION_MAX (6000): the
        external per-section cap remains the binding budget — the B1
        raise moves ONLY the per-message truncation point."""
        bundle = self._bundle_with_tail(*["Q" * 2600 for _ in range(3)])
        assert bundle.b_chars <= BUNDLE_B_SECTION_MAX
        assert bundle.total_chars <= BUNDLE_TOTAL_MAX


class TestFusedBundle:
    def _assemble(self, rows=None, notes=None, ai_tail=None):
        a = SourceASignals(
            advisory_present=True,
            phrase_match=True,
            contradiction_flag=False,
            word_count_below_threshold=False,
            promise_terms_total=1,
            evidence=notes if notes is not None else (_a(advisory=True).evidence),
        )
        return assemble_fused_bundle(
            a_signals=a,
            b_signals=_b(marker_hit=True, terms=("awaiting",)),
            c_signals=_c(pending=2, wakeups=1, live=5, busy=3),
            c_tree_rows=rows if rows is not None else _tree_rows(3),
            ai_tail_messages=ai_tail
            if ai_tail is not None
            else [AIMessage(content="Awaiting the final report. Ending turn.")],
        )

    def test_bundle_has_three_sections_and_header(self):
        bundle = self._assemble()
        text = bundle.text
        assert text.startswith("[LCA FUSED EVIDENCE BUNDLE v1]")
        assert "SOURCE A:" in text
        assert "SOURCE B:" in text
        assert "SOURCE C:" in text

    def test_delta1_a_evidence_present(self):
        bundle = self._assemble()
        assert "ending turn" in bundle.text  # matched terms surface
        assert bundle.a_chars > 0
        assert bundle.a_chars <= BUNDLE_A_SECTION_MAX

    def test_delta3_c_first_10_rows_plus_suffix_and_counts(self):
        bundle = self._assemble(rows=_tree_rows(15))
        text = bundle.text
        assert "pending_children=2" in text
        assert "queued_or_expected_wakeups=1" in text
        assert "live_descendants=5" in text
        assert "busy_descendants=3" in text
        # first-10 rows rendered
        assert text.count("status=") == BUNDLE_C_TREE_ROWS_MAX
        assert "(+5 more" in text  # 15 - 10 suffix

    def test_c_rows_id_redacted(self):
        rows = _tree_rows(3)
        bundle = self._assemble(rows=rows)
        for row in rows:
            assert row["instance_id"] not in bundle.text, (
                "descendant instance ids MUST be redacted in the bundle"
            )
        assert "redacted" in bundle.text

    def test_a_child_ids_redacted(self):
        """A-section note excerpt MUST redact any embedded child instance id.

        The Stage-0 producer (``daemon/services/child_reports.py``,
        ``_process_child_completion_db_sync``) opens the note body with
        ``Child {child_instance_id} completed …`` — the raw 36-char uuid
        is ALWAYS in the body that becomes ``note_excerpt``. The fused
        bundle is bound by the 98b59dd7 boundary ("the fused bundle
        carries NO raw instance ids"), so the A-section renderer MUST
        redact the excerpt the same way it already redacts ``child=`` and
        ``stable_id=``. The PRE-redaction positive-control assertion
        guarantees the test exercises the actual redaction (a vacuous
        fixture using ``"Child … completed"`` would silently pass on a
        regression that drops ``redact_ids`` from the excerpt line).
        """
        # Pick a deterministic uuid so the assertion is exact-match.
        fixed_child_uuid = "11111111-2222-3333-4444-555555555555"
        # Mirror the LANDED producer body template (child_reports.py:3140-3150)
        # so the body opens ``Child {real-uuid} completed …`` with a real
        # 36-char uuid embedded.
        body_in_producer_shape = (
            f'Child {fixed_child_uuid} completed while its final report '
            f'promises future work ("ending turn", "will write") — likely '
            f"premature completion. Its promised next report will never "
            f"arrive. Verify the actual work state; if unfinished, revive "
            f'it via send_message (e.g. "continue your work") or verify '
            f"its subtree before relying on this report. (Advisory / "
            f"heuristic — marker scan is a substring match, not an LLM "
            f"verdict.)"
        )
        # Positive control: the raw uuid IS in the input fixture (without
        # this assertion the test could silently go vacuous — the prior
        # fixture used an ellipsis in the excerpt body and the literal
        # uuid never appeared in any input).
        assert fixed_child_uuid in body_in_producer_shape
        # Build the bundle via the existing _assemble() helper with a
        # single note whose excerpt is the producer-shape body.
        evidence_note = ara.ChildReportCheckEvidence(
            child_instance_id=fixed_child_uuid,
            matched_terms=("ending turn", "will write"),
            note_excerpt=body_in_producer_shape,
            stable_id="child_report_check:…",
            kwargs_surface_seen=True,
        )
        # Pre-redaction sanity: the fixture's evidence carries the raw uuid
        # (this is the data ``_build_a_section`` sees BEFORE its redact
        # call), so the assertions below are real coverage not tautology.
        assert fixed_child_uuid in evidence_note.note_excerpt
        bundle = self._assemble(notes=(evidence_note,))
        # (a) the raw uuid MUST be absent from the bundle.
        assert fixed_child_uuid not in bundle.text, (
            "A-section note excerpt leaked the raw child uuid — "
            "excerpt line in _build_a_section must wrap _clip(...) "
            "with redact_ids(...) (98b59dd7 boundary)."
        )
        # (b) the redaction placeholder MUST be present.
        assert "redacted" in bundle.text, (
            "expected the redact_ids placeholder to appear in the A section"
        )

    def test_per_section_caps_respected(self):
        big_notes = tuple(
            ara.ChildReportCheckEvidence(
                child_instance_id=str(uuid.uuid4()),
                matched_terms=("ending turn",),
                note_excerpt="x" * 2000,
                stable_id="child_report_check:…",
                kwargs_surface_seen=True,
            )
            for _ in range(ara.A_EVIDENCE_NOTES_CAP)
        )
        big_tail = [AIMessage(content="y" * 20000)]
        bundle = self._assemble(rows=_tree_rows(50), notes=big_notes, ai_tail=big_tail)
        assert bundle.a_chars <= BUNDLE_A_SECTION_MAX
        assert bundle.b_chars <= BUNDLE_B_SECTION_MAX
        assert bundle.c_chars <= BUNDLE_C_SECTION_MAX

    def test_total_cap_respected(self):
        big_notes = tuple(
            ara.ChildReportCheckEvidence(
                child_instance_id=str(uuid.uuid4()),
                matched_terms=("ending turn",),
                note_excerpt="x" * 4000,
                stable_id="…",
                kwargs_surface_seen=True,
            )
            for _ in range(ara.A_EVIDENCE_NOTES_CAP)
        )
        bundle = self._assemble(notes=big_notes, ai_tail=[AIMessage(content="y" * 20000)])
        assert bundle.total_chars <= BUNDLE_TOTAL_MAX

    def test_total_cap_hard_clip_branch_respects_bound(self, monkeypatch):
        """Force the ``len(text) > BUNDLE_TOTAL_MAX`` hard-clip branch.

        Per-section internal caps limit the natural bundle to ~8300 chars
        (A≈2550 + B≈4762 + C≈980 + header/sections≈33), so the hard-clip
        branch is naturally unreachable. Monkey-patch ``BUNDLE_TOTAL_MAX``
        down to a value the natural max exceeds (so the branch fires
        without restructuring the section builders) and assert the
        precomputed-clip fix keeps ``len(text) <= BUNDLE_TOTAL_MAX``
        exactly — the prior shape overshot by ``len(truncation_suffix)``
        because the suffix length was kept out of the clip budget.
        """
        # Force the hard-clip branch to fire by lowering the total cap
        # to a value the natural per-section rendering exceeds.
        monkeypatch.setattr(ara, "BUNDLE_TOTAL_MAX", 5000)
        big_notes = tuple(
            ara.ChildReportCheckEvidence(
                child_instance_id=str(uuid.uuid4()),
                matched_terms=("ending turn",),
                note_excerpt="x" * 2000,
                stable_id="…",
                kwargs_surface_seen=True,
            )
            for _ in range(ara.A_EVIDENCE_NOTES_CAP)
        )
        big_tail = [AIMessage(content="y" * 20000)]
        bundle = self._assemble(
            notes=big_notes,
            ai_tail=big_tail,
            rows=_tree_rows(20),
        )
        # Hard-clip branch DID fire — total MUST equal BUNDLE_TOTAL_MAX
        # exactly (precompute + suffix leaves no slack).
        assert len(bundle.text) == ara.BUNDLE_TOTAL_MAX, (
            f"hard-clip overshot its own bound: len={len(bundle.text)} "
            f"BUNDLE_TOTAL_MAX={ara.BUNDLE_TOTAL_MAX}"
        )
        assert "bundle truncated at total cap" in bundle.text
        # Per-section caps still respected (the hard-clip branch is a
        # final safety net — it must not silently widen any section).
        assert bundle.a_chars <= BUNDLE_A_SECTION_MAX
        assert bundle.b_chars <= BUNDLE_B_SECTION_MAX
        assert bundle.c_chars <= BUNDLE_C_SECTION_MAX
        assert bundle.total_chars <= ara.BUNDLE_TOTAL_MAX

    def test_bundle_sha256_stable_and_size_matches(self):
        bundle = self._assemble()
        assert re.fullmatch(r"[0-9a-f]{64}", bundle.sha256)
        assert bundle.total_chars == len(bundle.text)


# ─────────────────────────────────────────────────────────────────────────────
# Stage-2 seam — provably inert (zero LLM)
# ─────────────────────────────────────────────────────────────────────────────


class TestFusedSeamContract:
    """Stage-2 flip re-contract (2026-09-16): the Stage-1 module-global
    seam (``STAGE2_JUDGE_SEAM`` / ``_maybe_invoke_stage2_judge``) is
    RETIRED — the fused judge invocation lives in the graph node's
    fused block (the ONE call site). The gate thread computes the
    snapshot; it NEVER invokes an LLM."""

    def test_module_global_seam_retired(self):
        # The Stage-1 scaffolding is gone; the single invocation site is
        # graph.py's fused block calling judge_fused_bundle_async.
        assert not hasattr(ara, "STAGE2_JUDGE_SEAM")
        assert not hasattr(ara, "_maybe_invoke_stage2_judge")

    def test_zero_llm_sentinel_full_gate_would_fire(self, monkeypatch, caplog):
        """Full gate evaluate() with would_fire=True ⇒ NO LLM/judge call.

        Delegated mission + un-attested + quiet tree ⇒ deny band ⇒ the
        gate-thread compute assembles + hashes the bundle and attaches
        the snapshot to the decision; the LLM invocation happens ONLY
        in the graph node (not exercised here). ANY judge/LLM surface
        exploding proves the gate-side zero-LLM contract.
        """
        # Sentinels: ANY judge/LLM surface explodes.
        from daemon.services import attestation_report_judge as judge_mod

        def _explode(*args, **kwargs):
            raise AssertionError("LLM/judge invoked in gate evaluate()!")

        # Stage 3 (R7): the legacy window-judge entry points are
        # deleted; the fused judge is the only invocation surface.
        assert not hasattr(judge_mod, "judge_completion_report_async")
        monkeypatch.setattr(judge_mod, "judge_fused_bundle_async", _explode)
        monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _explode)

        manager = MagicMock()
        manager.count_pending_children.return_value = 0
        manager.get_queued_or_expected_wakeups.return_value = 0
        manager.count_live_descendants.return_value = 0
        manager.count_busy_descendants.return_value = 0
        manager.get_tree_ids_permanent.return_value = []
        reader = MagicMock(return_value=False)
        manager.has_open_user_answer = reader

        with caplog.at_level(logging.INFO):
            decision = gate_evaluate(
                "leader-1",
                denied_count=0,
                messages=_delegated_mission_state("All done, shipped."),
                mode_resolver=GateSettings("enforce", 3, 3),
                manager=manager,
            )
        assert decision.decision is Decision.DENIED  # decide() unchanged
        # Stage-2 flip: the snapshot rides the decision (band + bundle).
        snapshot = decision.resolver
        assert snapshot is not None
        assert snapshot.result.fired is True
        assert snapshot.result.band == BAND_DENY
        assert snapshot.result.bundle is not None
        assert snapshot.result.bundle.sha256
        # The gate thread emits NO eval row anymore (the node does).
        eval_rows = [
            r for r in caplog.records
            if "event=leader_completion_resolver_eval" in r.getMessage()
        ]
        assert eval_rows == []


# ─────────────────────────────────────────────────────────────────────────────
# Parallel-log row shape — one structured event per gate evaluation
# ─────────────────────────────────────────────────────────────────────────────


REQUIRED_SHADOW_FIELDS = (
    "event=leader_completion_resolver_eval",
    "instance_id=",
    "gate_location=",
    "mode=",
    "fired=",
    "band=",
    "terms_fired=",
    "attestation_required=",
    "would_be_outcome=",
    "old_decision_value=",
    "agreement=",
    "fail_open=",
    "bundle_sha256=",
    "bundle_size_chars=",
    "judge_invoked=",
    "judge_verdict=",
    "resolver_outcome=",
)


class TestShadowEventRowShape:
    """Stage-2 flip re-contract (2026-09-16): the gate thread COMPUTES
    the snapshot (attached to the decision, no row); the graph node's
    fused block emits the row via ``emit_resolver_eval_row`` after the
    judge decision. These tests pin the snapshot + the ROW SHAPE by
    driving the emitter directly (node-level emission is pinned in
    ``tests/unit/test_attestation_resolver_stage2.py``)."""

    def _run_gate(self, caplog, *, messages, mode="enforce", pending=0, live=0, busy=0):
        manager = MagicMock()
        manager.count_pending_children.return_value = pending
        manager.get_queued_or_expected_wakeups.return_value = 0
        manager.count_live_descendants.return_value = live
        manager.count_busy_descendants.return_value = busy
        manager.get_tree_ids_permanent.return_value = []
        manager.has_open_user_answer = MagicMock(return_value=False)
        with caplog.at_level(logging.INFO):
            decision = gate_evaluate(
                "leader-shape",
                denied_count=0,
                messages=messages,
                mode_resolver=GateSettings(mode, 3, 3),
                manager=manager,
            )
        shadow_rows = []
        if decision.resolver is not None:
            with caplog.at_level(logging.INFO):
                ara.emit_resolver_eval_row(
                    decision.resolver, judge_invoked=False
                )
            shadow_rows = [
                r
                for r in caplog.records
                if "event=leader_completion_resolver_eval" in r.getMessage()
            ]
        return decision, shadow_rows

    def test_row_carries_all_required_fields(self, caplog):
        _, rows = self._run_gate(caplog, messages=_delegated_mission_state())
        assert rows, "expected exactly the eval row on the canonical path"
        row = rows[0].getMessage()
        for field in REQUIRED_SHADOW_FIELDS:
            assert field in row, f"eval row missing {field!r}"

    def test_agreement_true_case(self, caplog):
        # deny band vs old DENIED → agreement=true
        decision, rows = self._run_gate(caplog, messages=_delegated_mission_state())
        assert decision.decision is Decision.DENIED
        row = rows[0].getMessage()
        assert "would_be_outcome=would_deny_nudge" in row
        assert "old_decision_value=denied" in row
        assert "agreement=True" in row

    def test_agreement_false_case_delta2(self, caplog):
        # Δ2: A-band (busy>0, A note present) vs old
        # ALLOWED_LEGITIMATE_PENDING_WAKEUP → would_hint vs allow → False
        messages = _delegated_mission_state() + [_child_report_check_note()]
        decision, rows = self._run_gate(caplog, messages=messages, live=2, busy=2)
        assert decision.decision is Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP
        row = rows[0].getMessage()
        assert "band=a_suspicion" in row
        assert "would_be_outcome=would_hint" in row
        assert "agreement=False" in row

    def test_one_row_per_evaluation_no_row_on_meta_bypass(self, caplog):
        # meta-bypass (mode=off) — the resolver never runs (no snapshot,
        # no row where the gate doesn't run).
        decision, rows = self._run_gate(caplog, messages=_delegated_mission_state(), mode="off")
        assert rows == []
        assert decision.resolver is None

    def test_dry_mode_row_shape(self, caplog):
        _, rows = self._run_gate(caplog, messages=_delegated_mission_state(), mode="dry")
        assert rows
        row = rows[0].getMessage()
        assert "mode=dry" in row
        # dry: old path logs DRY_LOG (mapped allow); shadow deny band →
        # divergence false — the dry-soak signal.
        assert "old_decision_value=dry_log" in row
        assert "agreement=False" in row

    def test_db_error_emits_fail_open_shadow_row(self, caplog):
        manager = MagicMock()
        manager.count_pending_children.side_effect = RuntimeError("db down")
        with caplog.at_level(logging.INFO):
            decision = gate_evaluate(
                "leader-dberr",
                denied_count=0,
                messages=_delegated_mission_state(),
                mode_resolver=GateSettings("enforce", 3, 3),
                manager=manager,
            )
        assert decision.decision is Decision.ALLOWED  # fail-open unchanged
        assert decision.gate_exception_seen is True
        shadow_rows = [
            r
            for r in caplog.records
            if "event=leader_completion_resolver_eval" in r.getMessage()
        ]
        assert shadow_rows, "C-read failure must emit the fail-open shadow row"
        row = shadow_rows[0].getMessage()
        assert "fail_open=True" in row
        assert "would_be_outcome=would_allow" in row

    def test_shadow_error_never_breaks_the_gate(self, caplog, monkeypatch):
        """Exception isolation — a resolver-side crash logs an error row
        and NEVER propagates into gate control flow."""
        monkeypatch.setattr(
            ara,
            "activation_predicate",
            lambda **kwargs: (_ for _ in ()).throw(RuntimeError("resolver bug")),
        )
        manager = MagicMock()
        manager.count_pending_children.return_value = 0
        manager.get_queued_or_expected_wakeups.return_value = 0
        manager.count_live_descendants.return_value = 0
        manager.count_busy_descendants.return_value = 0
        manager.get_tree_ids_permanent.return_value = []
        manager.has_open_user_answer = MagicMock(return_value=False)
        with caplog.at_level(logging.INFO):
            decision = gate_evaluate(
                "leader-crash",
                denied_count=0,
                messages=_delegated_mission_state(),
                mode_resolver=GateSettings("enforce", 3, 3),
                manager=manager,
            )
        assert decision.decision is Decision.DENIED  # old path unaffected
        error_rows = [
            r
            for r in caplog.records
            if "leader_completion_resolver_eval_error" in r.getMessage()
        ]
        assert error_rows, "resolver crash must log the error row"

    def test_r4_wiring_no_a_scan_needed_on_non_delegated(self, caplog):
        # wiring parity: a non-delegated mission's shadow row records
        # attestation_required=False and fired=False
        messages = [
            HumanMessage(content="what's the answer?"),
            AIMessage(content="The answer is 42."),
        ]
        decision, rows = self._run_gate(caplog, messages=messages)
        assert decision.decision is Decision.ALLOWED
        assert rows
        row = rows[0].getMessage()
        assert "attestation_required=False" in row
        assert "fired=False" in row


# ─────────────────────────────────────────────────────────────────────────────
# Tree-rows provider (wiring-level C evidence collection)
# ─────────────────────────────────────────────────────────────────────────────


class TestTreeRowsProvider:
    def test_provider_enumerates_descendants(self):
        root = "root-id"
        child_ids = [str(uuid.uuid4()) for _ in range(3)]
        manager = MagicMock()
        manager.get_tree_ids_permanent.return_value = [root, *child_ids]
        repo = MagicMock()
        repo.get.side_effect = lambda iid: _Row(iid, status="running", agent_id="worker")
        manager._instance_repository = repo

        provider = ara.make_tree_rows_provider(manager, root)
        rows = provider()
        assert [r["instance_id"] for r in rows] == child_ids  # root excluded
        assert all(r["status"] == "running" for r in rows)

    def test_provider_no_repo_returns_empty(self):
        manager = MagicMock(spec=["get_tree_ids_permanent"])
        provider = ara.make_tree_rows_provider(manager, "x")
        assert provider() == []

    def test_provider_failure_returns_empty_never_raises(self):
        manager = MagicMock()
        manager.get_tree_ids_permanent.side_effect = RuntimeError("boom")
        provider = ara.make_tree_rows_provider(manager, "x")
        assert provider() == []

    def test_provider_bounded(self):
        ids = [str(uuid.uuid4()) for _ in range(ara.C_TREE_ROW_FETCH_CAP + 20)]
        manager = MagicMock()
        manager.get_tree_ids_permanent.return_value = ["root", *ids]
        repo = MagicMock()
        repo.get.side_effect = lambda iid: _Row(iid)
        manager._instance_repository = repo
        rows = ara.make_tree_rows_provider(manager, "root")()
        assert len(rows) == ara.C_TREE_ROW_FETCH_CAP


# ─────────────────────────────────────────────────────────────────────────────
# Source pins — module surface (Stage-0 contract consumed verbatim)
# ─────────────────────────────────────────────────────────────────────────────


class TestSourcePins:
    def test_catalog_imported_from_marker_scanner(self):
        # The Stage-0 producer's catalog is the one source of truth.
        from daemon.services.attestation_marker_scanner import (
            scan_child_terminal_report_for_promises,
        )

        scan = scan_child_terminal_report_for_promises("Will write the report. Ending turn.")
        assert scan.promise_hit is True
        assert ara.CHILD_TERMINAL_PROMISE_MARKERS is CHILD_TERMINAL_PROMISE_MARKERS

    def test_no_new_env_flag_reads(self):
        import ast
        import pathlib

        path = pathlib.Path(ara.__file__)
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    assert not (
                        node.func.attr in {"environ", "getenv"}
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "os"
                    ), "no os.environ/os.getenv reads in the Stage-1 module"


# ─────────────────────────────────────────────────────────────────────────────
# A-band restoration (2026-09-18) — evaluation-time transcript scan pins
# ─────────────────────────────────────────────────────────────────────────────
#
# User standing decision (Q2, 2026-09-18): A-band stays ACTIVE with the
# catalog UNCHANGED. The restoration shape (leader-decided, binding) is
# an evaluation-time transcript scan over the leader's in-context
# child-report ``HumanMessage`` rows (the ``internal_report:`` stamp
# from the report-injection drain at ``daemon/graph.py:6950-6967`` and
# the fallback ``_stamped_additional_kwargs`` path at
# ``daemon/services/instance_messaging.py:525``). The 17-pattern catalog
# stays byte-identical (existing identity pin). The mint-time machinery
# (D-CTD-7, commit 6a695b8f) stays gone — no note, no delivery Task, no
# SAVEPOINT, no notification.
#
# These pins anchor the restoration shape against future drift:
#   * Wiring — resolver still wires through ``collect_source_a_signals``
#     and the activation predicate still feeds ``a_suspicion``.
#   * Functional — an ``internal_report:``-stamped ``HumanMessage``
#     containing a catalog phrase MUST produce an A-signal through the
#     real code path (the live-source re-anchor).
#   * Catalog — 17-pattern catalog byte-identical (the user's deliberate
#     decision; judge filters FPs, catalog stays).
#   * Bundle cap — :data:`BUNDLE_A_SECTION_MAX` still 3000 (the A-section
#     renderer still feeds the fused judge).


class TestDCTD7ASignalPathPins:
    """Positive pins — the A-signal path is preserved verbatim
    (D-CTD-7, 2026-09-18 user decision; A-band restoration pins,
    2026-09-18 follow-on)."""

    def test_resolver_consumes_collect_source_a_signals(self):
        """The activation predicate still wires through
        ``collect_source_a_signals`` — the A-signal source the
        resolver reads. Untouched by D-CTD-7.
        """
        import daemon.services.attestation_resolver_activation as ara2
        from daemon.services.attestation_resolver_activation import (
            collect_source_a_signals,
        )
        assert hasattr(ara2, "collect_source_a_signals")
        assert callable(collect_source_a_signals)
        # Identity pin — same symbol the resolver wires into the
        # predicate (attestation_resolver_activation.py:1058).
        from daemon.services.attestation_resolver_activation import (
            evaluate_resolver_activation,
        )
        import inspect
        src = inspect.getsource(evaluate_resolver_activation)
        assert "collect_source_a_signals" in src, (
            "D-CTD-7: resolver activation path no longer wires "
            "collect_source_a_signals — A-signal source drifted"
        )
        # Wiring-shape pin — guards the A-band against an
        # accidental sever where the symbol survives (in a
        # docstring/comment) but the call shape `a_source=lambda:
        # collect_source_a_signals(messages)` is broken. This is
        # the exact drift class the restoration exists to block.
        assert "a_source=lambda" in src, (
            "D-CTD-7: a_source= lambda wiring missing from "
            "evaluate_resolver_activation — A-band severed"
        )
        assert "collect_source_a_signals(" in src, (
            "D-CTD-7: a_source lambda body no longer calls "
            "collect_source_a_signals — A-band severed"
        )
        assert "tree_rows_provider=_cached_tree_rows" in src, (
            "dual-autopsy B1 item 2 (2026-09-20): the a_source lambda "
            "must pass the fetch-once tree-rows provider into the "
            "A-scan — without it the delivered-child cross-resolution "
            "(stale-A suppression) is severed"
        )

    def test_activation_predicate_a_suspicion_term_intact(self):
        """The activation predicate's ``a_suspicion`` term must
        still consult Source A signals (advisory_present OR
        contradiction_flag OR phrase_match OR word_count_below_
        threshold). D2 (NOT busy-suppressed) preserved verbatim.
        """
        from daemon.services.attestation_resolver_activation import (
            activation_predicate,
        )
        import inspect
        src = inspect.getsource(activation_predicate)
        # The four OR'd fields of the A-suspicion term — if any
        # gets renamed/removed, the A-band trigger semantics drift.
        assert "a_signals.advisory_present" in src
        assert "a_signals.contradiction_flag" in src
        assert "a_signals.phrase_match" in src
        assert "a_signals.word_count_below_threshold" in src
        assert "a_suspicion = bool(" in src, (
            "D-CTD-7: activation_predicate a_suspicion term shape "
            "drifted from spec §4.2"
        )

    def test_fused_bundle_a_section_cap_unchanged(self):
        """The A-section cap (3000 chars) and the A-section
        renderer must remain byte-identical. Negative-resurrection
        of any per-A-section cap change is the test.
        """
        from daemon.services.attestation_resolver_activation import (
            BUNDLE_A_SECTION_MAX,
            _build_a_section,
        )
        assert BUNDLE_A_SECTION_MAX == 3000, (
            "D-CTD-7: BUNDLE_A_SECTION_MAX changed from 3000 — "
            "fused bundle A-section cap drift"
        )
        assert callable(_build_a_section)

    def test_catalog_byte_identical_after_removal(self):
        """The 17-pattern catalog survives D-CTD-7 byte-identical.
        The user explicitly declined tightening; the catalog stays.
        Identity pin (same module reference).
        """
        from daemon.services.attestation_marker_scanner import (
            CHILD_TERMINAL_PROMISE_MARKERS as BEFORE,
        )
        from daemon.services.attestation_marker_scanner import (
            CHILD_TERMINAL_PROMISE_MARKERS as AFTER,
        )
        assert BEFORE is AFTER
        # 17 entries per D-CTD-1 (decision catalog size pinned).
        assert len(CHILD_TERMINAL_PROMISE_MARKERS) == 17, (
            f"D-CTD-7: catalog size drifted from 17 to "
            f"{len(CHILD_TERMINAL_PROMISE_MARKERS)}"
        )

    def test_functional_pin_internal_report_message_produces_a_signal(
        self,
    ):
        """FUNCTIONAL PIN (A-band restoration, 2026-09-18).

        An ``internal_report:``-stamped ``HumanMessage`` whose content
        contains a catalog phrase MUST produce an
        ``advisory_present=True`` A-signal through the real
        ``collect_source_a_signals`` code path (NOT a re-anchored
        mint-time signal — the mint site is deleted per D-CTD-7).

        This is the authoritative re-anchor for the deletion-derailment
        class: any future regression that breaks the live transcript
        scan (e.g. drifting to a different stamp shape, or accidentally
        tightening the catalog) fails this test loud. ``test_evidence``
        is structurally equivalent to the deleted mint-time
        ``notify_worker_pool`` flag on a fresh D-CTD-7 producer — the
        shape change from "stamp at child-terminal time" to "scan at
        gate-evaluation time" is what this pin captures.
        """
        # Build the LIVE child-report stamp exactly as the
        # report-injection drain + fallback PROCESS_REPORT task
        # emit it:
        #   additional_kwargs = {"injected_message": True,
        #                        "source": f"internal_report:{child}:{msg}"}
        child_id = "11111111-2222-3333-4444-555555555555"
        content = (
            "Awaiting the final four: C12a/b/c + blame-worker. "
            "Then I will write the aggregation. Ending turn."
        )
        report_msg = HumanMessage(
            content=content,
            id="report-msg-uuid",
            additional_kwargs={
                "injected_message": True,
                "source": f"internal_report:{child_id}:cmpl-uuid",
            },
        )
        delegation = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "send_message",
                    "args": {"target": "child-id"},
                    "id": "c1",
                }
            ],
        )
        messages = [
            HumanMessage(content="please do it"),
            delegation,
            report_msg,
            AIMessage(content="Awaiting the final four. Ending turn."),
        ]
        signals = collect_source_a_signals(messages)
        # Literal-must-pass gate — the A-band restoration is anchored
        # on this assertion. If this fails, A-band is dormant again.
        assert signals.advisory_present is True, (
            "A-band restoration pin: an internal_report-stamped message "
            "with a catalog phrase must produce advisory_present=True"
        )
        assert signals.phrase_match is True
        assert any(
            "ending turn" in ev.matched_terms for ev in signals.evidence
        ), "scan must surface the catalog phrase in matched_terms"
        assert any(
            ev.child_instance_id == child_id for ev in signals.evidence
        ), "scan must surface the child instance id from the source stamp"
