"""LCA unified resolver — Stage 1 parallel-dry activation predicate (shadow).

Implements the additive Stage-1 shadow of the unified 3-source completion
resolver (spec: ``.agents/shared/planning/leader-completion-attestation/
resolver-unification.md`` §4.1/§4.2/§4.3). NOTHING in this module routes,
denies, nudges, hints, or calls an LLM — the old gate paths remain
AUTHORITATIVE and byte-identical; every evaluation of this shadow emits ONE
structured ``event=leader_completion_resolver_eval`` log row (the soak
signal) and nothing else.

Stage-1 scope (user-locked 2026-09-16):
  * Δ1–Δ4 approved (evidence fusion shapes); Δ2 mirrored exactly — Source A
    is NOT busy-suppressed; Source B IS busy-muted.
  * DP-5 REJECTED — judge error/timeout stays path-(d) deny in the TARGET
    design; Stage 1 has no live effect (the would-be mapping is judge-free).
  * R1–R8 retirement is Stage 3 ONLY — this cycle removes/alters nothing.
  * NO new env flags (repo convention n). ZERO new LLM calls (budget
    parity ABSOLUTE).

Components
----------
``activation_predicate`` (pure)
    The §4.2 boolean over sources A/B/C with Term 0 (scope/mode) and Term 1
    (delegation gate, R4/D10 mirror) as outermost short-circuits. Sources
    are passed in as LAZY provider callables so the R4 short-circuit is
    spy-testable: when ``attestation_required`` is False (no delegation
    since the last real user message), the A/B providers are NEVER invoked.
    A raising C provider is the whole-eval fail-open (plain allow) —
    mirroring the gate's own DB-seam fail-open parity
    (``attestation_gate.evaluate``, gate DB-error handler).

``compute_would_be_outcome`` (pure)
    The §4.3 NO-JUDGE mapping — Stage 1 has no fused LLM node, so the
    would-be outcome is derived from the band + the shared deny bound +
    route-(b)'s pending predicate: deny band → ``would_deny_nudge``
    (``would_terminal`` when ``deny_bound_exceeded``); marker/A bands →
    ``would_hint`` when route-(b) pending (pending ∨ wakeups ∨ live, the
    graph.py ``nothing_pending`` composition) else ``would_allow``.

``assemble_fused_bundle`` (pure)
    Spec §4.1 bundle: A child-report evidence ≤3000 chars + B leader
    signals / last-3-AIMessages ≤6000 + C first-10 per-descendant tree rows
    (id-redacted) + scalar counts ≤3000, total ≤12000. Stage 1 only
    assembles + hashes it; the Stage-2 invocation seam
    (:data:`STAGE2_JUDGE_SEAM`) exists structurally but is inert (None).

``evaluate_shadow_activation``
    The wiring orchestrator: predicate → would-be outcome → agreement vs
    the old-path decision → conditional bundle assembly → ONE log row.
    Exception-isolated at the CALLER (the gate wraps it) AND internally
    best-effort: a resolver-side error logs
    ``event=leader_completion_resolver_eval_error`` and never propagates.

Naming divergence (recorded in decisions.md): the spec §4.1 suggested
``event=leader_activation`` and module ``attestation_activation.py``; the
caller pinned ``leader_completion_resolver_eval`` /
``attestation_resolver_activation.py`` — the LCA ``leader_completion_*``
log namespace and the existing ``attestation_resolver`` module family.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from .attestation_marker_scanner import (
    CHILD_TERMINAL_PROMISE_MARKERS,
    scan_child_terminal_report_for_promises,
)
from .context_messages import CONTEXT_KIND_CHILD_REPORT_CHECK

logger = logging.getLogger(__name__)

__all__ = [
    "A_EVIDENCE_NOTES_CAP",
    "ActivationResult",
    "BAND_A_SUSPICION",
    "BAND_DENY",
    "BAND_MARKER",
    "BAND_NONE",
    "BUNDLE_A_SECTION_MAX",
    "BUNDLE_B_SECTION_MAX",
    "BUNDLE_C_SECTION_MAX",
    "BUNDLE_C_TREE_ROWS_MAX",
    "BUNDLE_TOTAL_MAX",
    "C_TREE_ROW_FETCH_CAP",
    "ChildReportCheckEvidence",
    "SourceASignals",
    "SourceBSignals",
    "SourceCSignals",
    "FusedBundle",
    "STAGE2_JUDGE_SEAM",
    "TERM_A_SUSPICION",
    "TERM_B_FIRES",
    "TERM_C_QUIET",
    "WOULD_ALLOW",
    "WOULD_DENY_NUDGE",
    "WOULD_HINT",
    "WOULD_TERMINAL",
    "activation_predicate",
    "assemble_fused_bundle",
    "collect_source_a_signals",
    "compute_agreement",
    "compute_would_be_outcome",
    "evaluate_shadow_activation",
    "log_shadow_fail_open",
    "make_tree_rows_provider",
    "map_old_decision_to_outcome",
]


# ─────────────────────────────────────────────────────────────────────────────
# Constants — bands, terms, would-be outcomes, bundle caps
# ─────────────────────────────────────────────────────────────────────────────

#: Band constants (§4.2 band precedence: deny > marker > a_suspicion).
BAND_NONE: str = ""
BAND_DENY: str = "deny"
BAND_MARKER: str = "marker"
BAND_A_SUSPICION: str = "a_suspicion"

#: Term names surfaced on ``ActivationResult.terms_fired``.
TERM_C_QUIET: str = "c_quiet"
TERM_B_FIRES: str = "b_fires"
TERM_A_SUSPICION: str = "a_suspicion"

#: Would-be-outcome values (§4.3 no-judge mapping — Stage 1, zero LLM).
WOULD_ALLOW: str = "would_allow"
WOULD_HINT: str = "would_hint"
WOULD_DENY_NUDGE: str = "would_deny_nudge"
WOULD_TERMINAL: str = "would_terminal"

#: Bypass-reason values (why the predicate did not fire).
BYPASS_NONE: str = "none"
BYPASS_TERM0_SCOPE_OR_MODE: str = "term0_scope_or_mode"
BYPASS_META_BYPASS: str = "meta_bypass"
BYPASS_FAIL_OPEN: str = "fail_open"

#: Bundle caps (spec §4.1 — today's ``JUDGE_MAX_INPUT_CHARS`` discipline).
BUNDLE_A_SECTION_MAX: int = 3000
BUNDLE_B_SECTION_MAX: int = 6000
BUNDLE_C_SECTION_MAX: int = 3000
BUNDLE_TOTAL_MAX: int = 12000
#: C evidence renders the FIRST N per-descendant rows + ``+N more`` suffix.
BUNDLE_C_TREE_ROWS_MAX: int = 10
#: A evidence collects at most N Child Report Check notes (chars dominate;
#: the count bound keeps per-note formatting work O(N) on note storms).
A_EVIDENCE_NOTES_CAP: int = 5
#: C tree-row provider fetches at most N per-descendant instance rows
#: (bounds the shadow's point reads; the subtree BFS itself is capped at
#: ``LIVE_DESCENDANTS_BFS_CAP`` upstream — this cap is the shadow's own).
C_TREE_ROW_FETCH_CAP: int = 50

#: Canonical content prefix of the Stage-0 advisory note (the always-present
#: delivery surface — kwargs survival depends on the drain branch, the
#: ``_make_context_message`` prefix never changes).
_CHILD_REPORT_CHECK_PREFIX: str = "[SYSTEM CONTEXT: Child Report Check]"

#: Regex extracting the child instance id from the note body (the body
#: opens with ``Child {uuid} completed while its final report …``).
_CHILD_ID_FROM_BODY_RE: re.Pattern[str] = re.compile(
    r"^Child ([0-9a-fA-F-]{36}) completed\b"
)

#: UUID-shape token pattern for id-redaction inside bundle evidence.
_UUID_TOKEN_RE: re.Pattern[str] = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)


# ─────────────────────────────────────────────────────────────────────────────
# Typed signal structs
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ChildReportCheckEvidence:
    """One Child Report Check note found in the leader's message history.

    The Stage-0 producer (``daemon/services/child_reports.py``, landed
    ab3b5dc1) attaches the note at child-terminal time; this struct is the
    GATE-side view of the delivered note (spec §4.1 "one producer, two
    consumers" — the note is the UX consumer's surface, these fields are
    the resolver's).
    """

    #: The child whose terminal report carried promise-while-stopping
    #: markers. ``None`` when the id could not be recovered.
    child_instance_id: str | None
    #: Promise markers the producer matched (catalog-ordered subset of
    #: :data:`CHILD_TERMINAL_PROMISE_MARKERS`). From the note kwargs when
    #: the drain preserved them; re-derived from the note body otherwise.
    matched_terms: tuple[str, ...]
    #: Bounded excerpt of the note body (server-authored constant text —
    #: never the child's raw report).
    note_excerpt: str
    #: The note's stable LangGraph message id
    #: (``child_report_check:{parent}:{child}``), when visible.
    stable_id: str | None
    #: True when the structured kwargs surface (``child_report_check``,
    #: ``child_report_check_terms``, ``child_instance_id``) was present on
    #: the delivered message; False when detection fell back to the
    #: content prefix.
    kwargs_surface_seen: bool


@dataclass(frozen=True)
class SourceASignals:
    """Source A — child-terminal contradiction suspicion (§4.1).

    Spec §4.1 anticipated ``{advisory_present, contradiction_flag,
    phrase_promise_while_stopping, word_count_below_threshold,
    child_instance_id, report_excerpt}``. The LANDED Stage-0 producer
    contract is NARROWER: a promise-phrase substring scan whose only
    output is the advisory note (advisory_present ≡ phrase match; no
    contradiction flag, no word-count signal, no raw report excerpt).
    The two unlanded booleans stay on the struct (default False) so the
    predicate is forward-compatible with a richer producer; the delta is
    recorded in decisions.md.
    """

    advisory_present: bool
    phrase_match: bool
    contradiction_flag: bool = False
    word_count_below_threshold: bool = False
    promise_terms_total: int = 0
    evidence: tuple[ChildReportCheckEvidence, ...] = ()


@dataclass(frozen=True)
class SourceBSignals:
    """Source B — leader-message suspicion (existing gate trigger halves)."""

    marker_hit: bool
    marker_terms: tuple[str, ...]
    length_trigger: bool
    final_word_count: int
    #: In-window ``attest_completion`` scan verdict (anti-suspicion input,
    #: inventory #2) — carried on B (same leader-message scan family).
    attested: bool = False


@dataclass(frozen=True)
class SourceCSignals:
    """Source C — tree-status facades (existing homes, unchanged)."""

    pending_children: int
    queued_or_expected_wakeups: int
    live_descendants: int
    busy_descendants: int
    user_answer_pending: bool = False


@dataclass(frozen=True)
class FusedBundle:
    """The assembled Stage-2 fused-input bundle (hash + size witnesses)."""

    #: Full bundle text (per-section caps applied; total ≤12000).
    text: str
    #: sha256 hex digest of ``text`` (the soak's evidence fingerprint).
    sha256: str
    #: ``len(text)`` — convenience witness so the log row never re-encodes.
    total_chars: int
    a_chars: int
    b_chars: int
    c_chars: int


@dataclass(frozen=True)
class ActivationResult:
    """The activation predicate's structured output (§4.1 minimum set)."""

    #: Did the resolver activate (would the fused node fire in Stage 2)?
    fired: bool
    #: ``deny`` | ``marker`` | ``a_suspicion`` | ``""``.
    band: str
    #: Which §4.2 terms fired (``c_quiet`` / ``b_fires`` / ``a_suspicion``).
    terms_fired: tuple[str, ...]
    #: True when a C facade read failed → whole-eval fail-open plain-allow.
    fail_open: bool
    #: True when a Term-0 / Term-1 bypass returned before the core terms.
    meta_bypass: bool
    #: Why the predicate did not fire (``none`` when it fired).
    bypass_reason: str
    # ——— input snapshot ———
    attestation_enabled: bool = True
    scope_applicable: bool = True
    mode: str = "enforce"
    attestation_required: bool = False
    attested: bool = False
    user_answer_pending: bool = False
    # ——— signal snapshots (None when the source was never evaluated) ———
    a_signals: SourceASignals | None = None
    b_signals: SourceBSignals | None = None
    c_signals: SourceCSignals | None = None
    #: Error class of the C-provider failure on the fail-open branch.
    fail_open_error_class: str | None = None
    #: The fused evidence bundle, assembled when ``fired`` (else None).
    bundle: FusedBundle | None = field(default=None, repr=False)


# ─────────────────────────────────────────────────────────────────────────────
# Source A collection — the LANDED Stage-0 producer contract (pure)
# ─────────────────────────────────────────────────────────────────────────────


def _is_child_report_check_note(message: BaseMessage) -> bool:
    """Dual-surface detection of a delivered Child Report Check note."""
    if not isinstance(message, HumanMessage):
        return False
    kwargs = getattr(message, "additional_kwargs", None) or {}
    if kwargs.get("context_kind") == CONTEXT_KIND_CHILD_REPORT_CHECK:
        return True
    content = message.content if isinstance(message.content, str) else ""
    return content.startswith(_CHILD_REPORT_CHECK_PREFIX)


def _excerpt(text: str, limit: int = 400) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def collect_source_a_signals(
    messages: Sequence[BaseMessage],
) -> SourceASignals:
    """Scan the leader's message history for Child Report Check notes.

    Pure function; no I/O, no LLM, no DB. Detection uses BOTH surfaces
    of the landed Stage-0 contract: the structured kwargs
    (``context_kind=child_report_check``) when the delivery drain
    preserved them, and the canonical ``[SYSTEM CONTEXT: Child Report
    Check]`` content prefix otherwise (always present — the note body is
    built by the ``_make_context_message`` factory). Terms and the child
    id are recovered from kwargs first; on the prefix-fallback path the
    terms are re-derived by re-scanning the note body (it quotes the
    matched terms verbatim) and the child id is parsed from the body's
    opening sentence.
    """
    evidence: list[ChildReportCheckEvidence] = []
    for message in messages:
        if len(evidence) >= A_EVIDENCE_NOTES_CAP:
            break
        if not _is_child_report_check_note(message):
            continue
        kwargs = getattr(message, "additional_kwargs", None) or {}
        content = message.content if isinstance(message.content, str) else ""
        body = content[len(_CHILD_REPORT_CHECK_PREFIX):].lstrip() if content.startswith(
            _CHILD_REPORT_CHECK_PREFIX
        ) else content
        kwargs_seen = kwargs.get("child_report_check") is True or (
            kwargs.get("context_kind") == CONTEXT_KIND_CHILD_REPORT_CHECK
        )
        raw_terms = kwargs.get("child_report_check_terms")
        if isinstance(raw_terms, (list, tuple)) and raw_terms:
            terms = tuple(str(t) for t in raw_terms)
        else:
            # Prefix fallback — the body quotes the matched terms; the
            # scanner over the body recovers them (substring semantics).
            terms = scan_child_terminal_report_for_promises(body).matched_terms
        child_id = kwargs.get("child_instance_id")
        if not isinstance(child_id, str) or not child_id:
            match = _CHILD_ID_FROM_BODY_RE.match(body.lstrip())
            child_id = match.group(1) if match else None
        stable_id = getattr(message, "id", None)
        evidence.append(
            ChildReportCheckEvidence(
                child_instance_id=child_id,
                matched_terms=terms,
                note_excerpt=_excerpt(body),
                stable_id=stable_id if isinstance(stable_id, str) else None,
                kwargs_surface_seen=bool(kwargs_seen),
            )
        )
    if not evidence:
        return SourceASignals(
            advisory_present=False,
            phrase_match=False,
        )
    return SourceASignals(
        advisory_present=True,
        phrase_match=True,  # landed producer: the note fires iff a promise phrase matched
        promise_terms_total=sum(len(ev.matched_terms) for ev in evidence),
        evidence=tuple(evidence),
    )


# ─────────────────────────────────────────────────────────────────────────────
# The pure activation predicate (§4.2)
# ─────────────────────────────────────────────────────────────────────────────


def activation_predicate(
    *,
    attestation_enabled: bool,
    scope_applicable: bool,
    mode: str,
    attestation_required: bool,
    attested: bool,
    user_answer_pending: bool,
    a_source: Callable[[], SourceASignals],
    b_source: Callable[[], SourceBSignals],
    c_source: Callable[[], SourceCSignals],
) -> ActivationResult:
    """Evaluate the unified activation predicate (pure; no I/O).

    Semantics = spec §4.2 verbatim:

    * Term 0 (R2 target shape — outermost): ``¬attestation_enabled ∨
      ¬scope_applicable ∨ mode="off"`` → not fired. NOTHING is evaluated.
    * Meta-bypass / Term 1 (§4.2 first line + R4 target shape):
      ``¬attestation_required ∨ attested ∨ user_answer_pending`` → not
      fired. The A/B providers are NEVER invoked (D10 mirror — today's
      delegation gate skips suspicion work when no delegation happened;
      the unified predicate preserves the exclusion as an invariant).
    * C-read failure (any provider raise) → whole-eval FAIL-OPEN
      plain-allow (gate DB-seam parity).
    * Core terms:
      ``c_quiet := pending=0 ∧ wakeups=0 ∧ live=0``;
      ``b_fires := (marker_hit ∨ length_trigger) ∧ busy=0`` (R5 — Source
      B IS busy-muted); ``a_suspicion := advisory_present ∨
      contradiction_flag ∨ phrase_match ∨ word_count_below_threshold``
      (Δ2 — Source A is NOT busy-suppressed);
      ``activation := c_quiet ∨ b_fires ∨ a_suspicion``;
      band precedence ``deny > marker > a_suspicion``.

    Args:
        attestation_enabled: C2 master flag (Term 0).
        scope_applicable: D3 leader-scope flag (Term 0).
        mode: ``"off" | "dry" | "enforce"`` — ``off`` is Term 0; ``dry``
            and ``enforce`` compute NORMALLY (dry = activation computed +
            logged, node skipped — R3 target shape).
        attestation_required: delegation-gate flag (Term 1 / R4).
        attested: in-window ``attest_completion`` scan verdict.
        user_answer_pending: FIX-2 fifth legitimate-pending input.
        a_source: LAZY Source-A provider (spy-testable short-circuit).
        b_source: LAZY Source-B provider.
        c_source: LAZY Source-C provider; raising = whole-eval fail-open.

    Returns:
        :class:`ActivationResult` — structured, log-ready, bundle-less
        (bundle assembly is the orchestrator's conditional step).
    """
    base = dict(
        attestation_enabled=attestation_enabled,
        scope_applicable=scope_applicable,
        mode=mode,
        attestation_required=attestation_required,
        attested=attested,
        user_answer_pending=user_answer_pending,
    )
    # Term 0 — scope/mode outermost (R2 target shape). Nothing evaluated.
    if (not attestation_enabled) or (not scope_applicable) or mode == "off":
        return ActivationResult(
            fired=False,
            band=BAND_NONE,
            terms_fired=(),
            fail_open=False,
            meta_bypass=True,
            bypass_reason=BYPASS_TERM0_SCOPE_OR_MODE,
            **base,
        )
    # Meta-bypass / Term 1 (§4.2): ¬required ∨ attested ∨ answer-pending.
    # A/B providers NEVER run on this branch (R4/D10 mirror).
    if (not attestation_required) or attested or user_answer_pending:
        return ActivationResult(
            fired=False,
            band=BAND_NONE,
            terms_fired=(),
            fail_open=False,
            meta_bypass=True,
            bypass_reason=BYPASS_META_BYPASS,
            **base,
        )
    # C read — a provider raise is the whole-eval fail-open (§4.2 line 2;
    # gate.py DB-seam parity: plain allow, counts unknown).
    try:
        c_signals = c_source()
    except Exception as exc:  # noqa: BLE001 — fail-open at the C seam
        return ActivationResult(
            fired=False,
            band=BAND_NONE,
            terms_fired=(),
            fail_open=True,
            meta_bypass=False,
            bypass_reason=BYPASS_FAIL_OPEN,
            fail_open_error_class=type(exc).__name__,
            **base,
        )
    a_signals = a_source()
    b_signals = b_source()

    c_quiet = (
        c_signals.pending_children == 0
        and c_signals.queued_or_expected_wakeups == 0
        and c_signals.live_descendants == 0
    )
    b_fires = bool(
        (b_signals.marker_hit or b_signals.length_trigger)
        and c_signals.busy_descendants == 0
    )
    a_suspicion = bool(
        a_signals.advisory_present
        or a_signals.contradiction_flag
        or a_signals.phrase_match
        or a_signals.word_count_below_threshold
    )
    terms_fired = tuple(
        name
        for name, fired in (
            (TERM_C_QUIET, c_quiet),
            (TERM_B_FIRES, b_fires),
            (TERM_A_SUSPICION, a_suspicion),
        )
        if fired
    )
    fired = bool(terms_fired)
    if c_quiet:
        band = BAND_DENY
    elif b_fires:
        band = BAND_MARKER
    elif a_suspicion:
        band = BAND_A_SUSPICION
    else:
        band = BAND_NONE
    return ActivationResult(
        fired=fired,
        band=band if fired else BAND_NONE,
        terms_fired=terms_fired,
        fail_open=False,
        meta_bypass=False,
        bypass_reason=BYPASS_NONE,
        a_signals=a_signals,
        b_signals=b_signals,
        c_signals=c_signals,
        **base,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Would-be outcome — §4.3 no-judge mapping (Stage 1: zero LLM)
# ─────────────────────────────────────────────────────────────────────────────


def compute_would_be_outcome(
    result: ActivationResult,
    *,
    denied_count: int,
    deny_bound: int,
) -> str:
    """Map an :class:`ActivationResult` to the Stage-1 would-be outcome.

    Stage 1 has NO fused LLM node, so the would-be outcome is the §4.3
    NO-JUDGE mapping (pinned by tests; documented in docs/setup.md):

    * fail-open or not fired → ``would_allow`` (plain allow, 0 LLM).
    * deny band → ``would_deny_nudge``; ``would_terminal`` when the
      SHARED bound predicate ``deny_bound_exceeded`` fires (the same
      helper ``decide()`` step (6) and the graph.py marker-path
      conversions consult — FIX-1 6a0d60c9; imported, never re-implemented).
    * marker / a_suspicion bands → ``would_hint`` when route-(b)'s
      pending predicate holds (``pending ∨ wakeups ∨ live`` — the
      graph.py ``nothing_pending`` composition), else ``would_allow``
      (marker-only signal is too weak to deny — kill-switch parity).
    """
    if result.fail_open or not result.fired:
        return WOULD_ALLOW
    if result.band == BAND_DENY:
        from .attestation_gate import deny_bound_exceeded

        if deny_bound_exceeded(denied_count, deny_bound):
            return WOULD_TERMINAL
        return WOULD_DENY_NUDGE
    c = result.c_signals
    pending_work = bool(
        c
        and (
            c.pending_children > 0
            or c.queued_or_expected_wakeups > 0
            or c.live_descendants > 0
        )
    )
    return WOULD_HINT if pending_work else WOULD_ALLOW


def map_old_decision_to_outcome(decision: Any) -> str:
    """Map the old-path ``Decision`` into the would-be-outcome space.

    The old evaluate() seam has no hint outcome (the route-(b) hint rides
    the graph node's post-judge conversion), so allow-family decisions
    all map to ``would_allow``; ``would_hint`` therefore can never agree
    at this seam — hint-band rows are divergence rows BY CONSTRUCTION
    (the Δ2/Δ4 soak counter; documented in the runbook).
    """
    from .attestation_gate import Decision

    if decision is Decision.DENIED:
        return WOULD_DENY_NUDGE
    if decision is Decision.TERMINAL_AFTER_BOUND:
        return WOULD_TERMINAL
    # ALLOWED / ALLOWED_LEGITIMATE_PENDING_WAKEUP / DRY_LOG / unknown → allow.
    return WOULD_ALLOW


def compute_agreement(would_be_outcome: str, old_outcome: str) -> bool:
    """Agreement flag: exact class match in the four-value outcome space."""
    return would_be_outcome == old_outcome


# ─────────────────────────────────────────────────────────────────────────────
# Id-redaction + fused bundle assembly (§4.1 caps; Δ1/Δ3)
# ─────────────────────────────────────────────────────────────────────────────


def redact_ids(text: str, slot_hint: str = "id") -> str:
    """Replace UUID-shape tokens with stable slot placeholders.

    Per the 98b59dd7 evidence boundary: the fused bundle carries NO raw
    instance ids. Non-UUID ids (short tokens, agent names) pass through —
    the bundle's id-bearing fields are UUIDs by construction.
    """
    counter = {"n": 0}

    def _sub(match: re.Match[str]) -> str:
        counter["n"] += 1
        return f"<redacted-{slot_hint}-{counter['n']}>"

    return _UUID_TOKEN_RE.sub(_sub, text)


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _build_a_section(a_signals: SourceASignals | None) -> str:
    lines: list[str] = []
    if a_signals is None:
        return "=== SOURCE A: child-terminal contradiction evidence (not evaluated) ===\n"
    notes = a_signals.evidence
    lines.append(
        f"=== SOURCE A: child-terminal contradiction evidence ({len(notes)} note(s)) ==="
    )
    if not notes:
        lines.append("(no Child Report Check notes delivered)")
    for idx, ev in enumerate(notes, start=1):
        terms = ",".join(ev.matched_terms) if ev.matched_terms else "<none>"
        child = ev.child_instance_id or "<unknown>"
        lines.append(
            f"- note {idx}: child={redact_ids(child, 'child')} terms={terms}"
            + (f" stable_id={redact_ids(ev.stable_id, 'note')}" if ev.stable_id else "")
        )
        lines.append(
            f"  excerpt: {redact_ids(_clip(ev.note_excerpt, 400))}"
        )
    lines.append("")
    return "\n".join(lines)


def _build_b_section(
    b_signals: SourceBSignals | None,
    ai_tail_messages: Sequence[BaseMessage],
) -> str:
    lines: list[str] = []
    lines.append("=== SOURCE B: leader signals + tail AIMessages ===")
    if b_signals is None:
        lines.append("(source B not evaluated)")
    else:
        terms = ",".join(b_signals.marker_terms) if b_signals.marker_terms else "<none>"
        lines.append(
            f"marker_hit={b_signals.marker_hit} marker_terms={terms} "
            f"length_trigger={b_signals.length_trigger} "
            f"final_word_count={b_signals.final_word_count} "
            f"attested={b_signals.attested}"
        )
    lines.append("--- last AIMessages (newest last, truncated) ---")
    shown = 0
    for message in reversed(list(ai_tail_messages)):
        if shown >= 3:
            break
        if not isinstance(message, AIMessage):
            continue
        content = message.content if isinstance(message.content, str) else str(
            message.content or ""
        )
        lines.append(f"[{shown + 1}] {_clip(content, 1500)}")
        shown += 1
    if shown == 0:
        lines.append("(no AIMessages)")
    lines.append("")
    return "\n".join(lines)


def _build_c_section(
    c_signals: SourceCSignals | None,
    c_tree_rows: Sequence[dict[str, Any]],
) -> str:
    lines: list[str] = []
    lines.append(f"=== SOURCE C: tree status (first {BUNDLE_C_TREE_ROWS_MAX} rows) ===")
    if c_signals is None:
        lines.append("(source C not evaluated)")
    else:
        lines.append(
            f"pending_children={c_signals.pending_children} "
            f"queued_or_expected_wakeups={c_signals.queued_or_expected_wakeups} "
            f"live_descendants={c_signals.live_descendants} "
            f"busy_descendants={c_signals.busy_descendants} "
            f"user_answer_pending={c_signals.user_answer_pending}"
        )
    rendered = 0
    for row in c_tree_rows:
        if rendered >= BUNDLE_C_TREE_ROWS_MAX:
            break
        rendered += 1
        iid = redact_ids(str(row.get("instance_id", "<unknown>")), "descendant")
        lines.append(
            f"- row {rendered}: child={iid} status={row.get('status', '?')} "
            f"agent={row.get('agent_id', '?')}"
        )
    hidden = max(0, len(c_tree_rows) - BUNDLE_C_TREE_ROWS_MAX)
    if hidden > 0:
        lines.append(f"(+{hidden} more descendants not shown)")
    lines.append("")
    return "\n".join(lines)


def assemble_fused_bundle(
    *,
    a_signals: SourceASignals | None,
    b_signals: SourceBSignals | None,
    c_signals: SourceCSignals | None,
    c_tree_rows: Sequence[dict[str, Any]],
    ai_tail_messages: Sequence[BaseMessage],
) -> FusedBundle:
    """Assemble the §4.1 fused evidence bundle (pure; caps; redacted).

    Per-section caps: A ≤3000, B ≤6000, C ≤3000 (sum = the ≤12000 total —
    enforced defensively by a final hard clip with a truncation marker).
    Ids are redacted (:func:`redact_ids`) per the 98b59dd7 boundary.
    Stage 1 NEVER sends this bundle anywhere — the orchestrator hashes it
    and logs ``sha256`` + size; the Stage-2 seam
    (:data:`STAGE2_JUDGE_SEAM`) consumes it only when wired (it is None
    in Stage 1 — provably inert).
    """
    a_section = _clip(_build_a_section(a_signals), BUNDLE_A_SECTION_MAX)
    b_section = _clip(_build_b_section(b_signals, ai_tail_messages), BUNDLE_B_SECTION_MAX)
    c_section = _clip(_build_c_section(c_signals, c_tree_rows), BUNDLE_C_SECTION_MAX)
    text = (
        "[LCA FUSED EVIDENCE BUNDLE v1]\n"
        + a_section
        + "\n"
        + b_section
        + "\n"
        + c_section
    )
    if len(text) > BUNDLE_TOTAL_MAX:
        # Precompute the suffix so the clipped+reconstructed text ALWAYS
        # satisfies ``len(text) <= BUNDLE_TOTAL_MAX`` (the prior shape
        # ``text[:BUNDLE_TOTAL_MAX-1] + suffix`` overshot by len(suffix)
        # because it kept the suffix length out of the clip budget).
        _truncation_suffix = "…\n[bundle truncated at total cap]\n"
        text = text[: BUNDLE_TOTAL_MAX - len(_truncation_suffix)] + _truncation_suffix
    return FusedBundle(
        text=text,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        total_chars=len(text),
        a_chars=len(a_section),
        b_chars=len(b_section),
        c_chars=len(c_section),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Stage-2 invocation seam — STRUCTURALLY present, PROVABLY inert
# ─────────────────────────────────────────────────────────────────────────────

#: Stage-2 fused-node invocation hook. ``None`` in Stage 1 (and by default
#: forever until an explicit Stage-2 wiring sets it): the shadow assembles
#: + hashes the bundle but NEVER invokes a judge/LLM. Budget parity
#: ABSOLUTE in Stage 1 — zero new LLM calls.
STAGE2_JUDGE_SEAM: Callable[[FusedBundle], None] | None = None


def _maybe_invoke_stage2_judge(bundle: FusedBundle | None) -> None:
    """Inert in Stage 1: no seam wired ⇒ no-op. Never raises."""
    if STAGE2_JUDGE_SEAM is None or bundle is None:
        return
    # Stage 2 will wire the fused-node call here. Unreachable in Stage 1.
    STAGE2_JUDGE_SEAM(bundle)  # pragma: no cover — Stage-2 seam


# ─────────────────────────────────────────────────────────────────────────────
# Tree-rows provider (wiring-level C evidence; best-effort, never raises)
# ─────────────────────────────────────────────────────────────────────────────


def make_tree_rows_provider(
    manager: Any,
    instance_id: str | None,
) -> Callable[[], list[dict[str, Any]]]:
    """Build the lazy C tree-rows provider for the shadow wiring.

    Enumerates the leader's subtree via the PUBLIC
    ``manager.get_tree_ids_permanent`` facade (root excluded) and fetches
    at most :data:`C_TREE_ROW_FETCH_CAP` per-descendant status rows via
    the instance repository. Best-effort: ANY failure returns ``[]``
    (counts-only C evidence) — the provider MUST NEVER raise into the
    shadow. Only invoked when the predicate FIRES (lazy), so the DB cost
    exists solely on would-fire evaluations.
    """

    def _provide() -> list[dict[str, Any]]:
        try:
            if manager is None or not instance_id:
                return []
            tree_ids = manager.get_tree_ids_permanent(instance_id)
            descendant_ids = [iid for iid in (tree_ids or []) if iid != instance_id]
            if not descendant_ids:
                return []
            repo = getattr(manager, "_instance_repository", None)
            if repo is None:
                return []
            rows: list[dict[str, Any]] = []
            for iid in descendant_ids[:C_TREE_ROW_FETCH_CAP]:
                row = repo.get(iid)
                if row is None:
                    continue
                rows.append(
                    {
                        "instance_id": str(getattr(row, "instance_id", iid)),
                        "status": str(getattr(row, "status", "?")),
                        "agent_id": str(getattr(row, "agent_id", "?")),
                    }
                )
            return rows
        except Exception:  # noqa: BLE001 — provider is best-effort only
            return []

    return _provide


# ─────────────────────────────────────────────────────────────────────────────
# The shadow orchestrator — ONE structured event per gate evaluation
# ─────────────────────────────────────────────────────────────────────────────


def evaluate_shadow_activation(
    *,
    instance_id: str | None,
    gate_location: str,
    leader_prompt_version: str,
    messages: Sequence[BaseMessage],
    mode: str,
    attestation_enabled: bool,
    scope_applicable: bool,
    attestation_required: bool,
    attested: bool,
    user_answer_pending: bool,
    c_values: SourceCSignals,
    b_values: SourceBSignals,
    denied_count: int,
    deny_bound: int,
    old_decision: Any,
    c_tree_rows_provider: Callable[[], list[dict[str, Any]]] | None = None,
) -> ActivationResult:
    """Run the parallel-dry shadow evaluation and log ONE event row.

    Called ONLY from the canonical path of
    ``attestation_gate.evaluate`` (post canonical log row, pre return —
    exactly where the existing gate evaluation runs; the meta-bypass /
    off-mode early returns never reach it). The C values are the gate's
    ALREADY-MATERIALIZED facade reads (zero new DB reads for the
    predicate); the A scan is a pure walk of the in-node ``messages``;
    the B values are the gate's already-computed marker/length results.
    The tree-rows provider runs LAZILY and ONLY when the predicate
    fires (bundle assembly time).

    The caller wraps this in its own try/except (exception isolation);
    this function additionally never allows the Stage-2 seam to raise
    into the log contract.
    """
    result = activation_predicate(
        attestation_enabled=attestation_enabled,
        scope_applicable=scope_applicable,
        mode=mode,
        attestation_required=attestation_required,
        attested=attested,
        user_answer_pending=user_answer_pending,
        a_source=lambda: collect_source_a_signals(messages),
        b_source=lambda: b_values,
        c_source=lambda: c_values,
    )
    would_be = compute_would_be_outcome(result, denied_count=denied_count, deny_bound=deny_bound)
    old_outcome = map_old_decision_to_outcome(old_decision)
    agreement = compute_agreement(would_be, old_outcome)

    bundle: FusedBundle | None = None
    if result.fired:
        tree_rows: list[dict[str, Any]] = []
        if c_tree_rows_provider is not None:
            tree_rows = c_tree_rows_provider()
        bundle = assemble_fused_bundle(
            a_signals=result.a_signals,
            b_signals=result.b_signals,
            c_signals=result.c_signals,
            c_tree_rows=tree_rows,
            ai_tail_messages=messages,
        )
        result = replace(result, bundle=bundle)
        # Stage-2 seam — inert in Stage 1 (STAGE2_JUDGE_SEAM is None).
        _maybe_invoke_stage2_judge(bundle)

    c = result.c_signals
    a = result.a_signals
    logger.info(
        "event=leader_completion_resolver_eval instance_id=%s "
        "gate_location=%s leader_prompt_version=%s mode=%s "
        "fired=%s band=%s terms_fired=%s bypass_reason=%s "
        "attestation_required=%s attested=%s user_answer_pending=%s "
        "pending_children=%s queued_or_expected_wakeups=%s "
        "live_descendants=%s busy_descendants=%s "
        "marker_hit=%s length_trigger=%s final_word_count=%s "
        "a_advisory_present=%s a_notes=%s a_kwargs_seen=%s "
        "would_be_outcome=%s old_decision_value=%s agreement=%s "
        "fail_open=%s bundle_sha256=%s bundle_size_chars=%s "
        "bundle_a_chars=%s bundle_b_chars=%s bundle_c_chars=%s "
        "judge_invoked=%s",
        instance_id,
        gate_location,
        leader_prompt_version,
        mode,
        result.fired,
        result.band or "<none>",
        ",".join(result.terms_fired) if result.terms_fired else "<none>",
        result.bypass_reason,
        result.attestation_required,
        result.attested,
        result.user_answer_pending,
        c.pending_children if c else -1,
        c.queued_or_expected_wakeups if c else -1,
        c.live_descendants if c else -1,
        c.busy_descendants if c else -1,
        result.b_signals.marker_hit if result.b_signals else False,
        result.b_signals.length_trigger if result.b_signals else False,
        result.b_signals.final_word_count if result.b_signals else -1,
        a.advisory_present if a else False,
        len(a.evidence) if a else 0,
        (
            any(ev.kwargs_surface_seen for ev in a.evidence)
            if a and a.evidence
            else False
        ),
        would_be,
        getattr(old_decision, "value", str(old_decision)),
        agreement,
        result.fail_open,
        bundle.sha256 if bundle else "<none>",
        bundle.total_chars if bundle else 0,
        bundle.a_chars if bundle else 0,
        bundle.b_chars if bundle else 0,
        bundle.c_chars if bundle else 0,
        False,  # judge_invoked — Stage 1 is provably zero-LLM
    )
    return result


def log_shadow_fail_open(
    *,
    instance_id: str | None,
    gate_location: str,
    leader_prompt_version: str,
    mode: str,
    error_class: str,
    old_decision: Any,
) -> None:
    """Emit the fail-open shadow row (the gate's C-read DB-error seam).

    When the gate's OWN C facade reads fail, the whole evaluation is
    fail-open plain-allow (old behavior, untouched); the predicate's
    §4.2 C-read-failure branch is the same semantics — this row records
    it with ``fail_open=True`` / ``would_be_outcome=would_allow`` so the
    soak data is complete. Never raises.
    """
    old_outcome = map_old_decision_to_outcome(old_decision)
    logger.info(
        "event=leader_completion_resolver_eval instance_id=%s "
        "gate_location=%s leader_prompt_version=%s mode=%s "
        "fired=False band=<none> terms_fired=<none> "
        "bypass_reason=fail_open "
        "attestation_required=<unknown> attested=<unknown> "
        "user_answer_pending=<unknown> "
        "pending_children=-1 queued_or_expected_wakeups=-1 "
        "live_descendants=-1 busy_descendants=-1 "
        "marker_hit=<unknown> length_trigger=<unknown> final_word_count=-1 "
        "a_advisory_present=<unknown> a_notes=0 a_kwargs_seen=False "
        "would_be_outcome=%s old_decision_value=%s agreement=%s "
        "fail_open=True bundle_sha256=<none> bundle_size_chars=0 "
        "bundle_a_chars=0 bundle_b_chars=0 bundle_c_chars=0 "
        "judge_invoked=False error_class=%s",
        instance_id,
        gate_location,
        leader_prompt_version,
        mode,
        WOULD_ALLOW,
        getattr(old_decision, "value", str(old_decision)),
        compute_agreement(WOULD_ALLOW, old_outcome),
        error_class,
    )
