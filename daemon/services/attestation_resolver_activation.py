"""LCA unified resolver — activation predicate + the single fused path.

Implements the unified 3-source completion resolver (spec:
``.agents/shared/planning/leader-completion-attestation/
resolver-unification.md`` §4.1/§4.2/§4.3). **Stage 2 flipped
2026-09-16; Stage 3 retirement 2026-09-17** — the resolver's outcome
mapping is the SOLE completion path: this module computes the
predicate + fused bundle in the gate thread and the GRAPH NODE's fused
block invokes the fused judge + maps the outcome + emits the
structured ``event=leader_completion_resolver_eval`` row (with
``judge_invoked`` DERIVED from the real invocation flag). The two
legacy judge sites in ``daemon/graph.py`` are DELETED (Stage 3,
R1–R8 executed; see decisions.md D-RES4).

Scope (user-locked 2026-09-16 / Stage-3 executed 2026-09-17):
  * Δ1–Δ4 approved (evidence fusion shapes); Δ2 mirrored exactly — Source A
    is NOT busy-suppressed; Source B IS busy-muted.
  * DP-5 REJECTED — judge error/timeout stays conservative path-(d) deny;
    the rejected fail-safe-allow shape appears nowhere.
  * R1–R8 retirement EXECUTED (dead code deleted, tests re-contracted).
  * NO new env flags (repo convention n).

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
    The §4.3 NO-JUDGE mapping — the kill-switch-off / dry-mode reference
    outcome (deny band → ``would_deny_nudge`` /
    ``would_terminal`` at the SHARED bound predicate; marker/A bands →
    ``would_hint`` when route-(b) pending (pending ∨ wakeups ∨ live, the
    graph.py ``nothing_pending`` composition) else ``would_allow``).

``assemble_fused_bundle`` (pure)
    Spec §4.1 bundle: A child-report evidence ≤3000 chars (NEWEST
    internal_report ONLY per child, cross-resolved against the C tree
    rows — dual-autopsy B1 items 1+2, 2026-09-20) + B leader
    signals / last-3-AIMessages ≤6000 (each clipped at 2500 — B1
    item 5) + C first-10 per-descendant tree rows
    (id-redacted) + scalar counts ≤3000 + U the user's original request
    ≤2000 (incident 4dfded83, 2026-09-18 — the judge was intent-blind
    without it), total ≤14000 (U additive). U reuses the delegation
    scanner's last-real-user-message anchor (``is_real_user_message``);
    anchor absent ⇒ U omitted entirely (``user_message_included=False``).
    Stage 2 feeds this
    bundle to :func:`attestation_report_judge.judge_fused_bundle_async`
    (the ONE fused judge call site, invoked from the graph node).

``evaluate_resolver_activation``
    The gate-thread wiring: predicate → would-be outcome → agreement vs
    the old-path decision → conditional bundle assembly → ONE
    :class:`ResolverEvalSnapshot` (NO logging, NO LLM — the graph node
    owns both post-flip). Exception-isolated at the CALLER (the gate
    wraps it) — a resolver-side error logs
    ``event=leader_completion_resolver_eval_error`` and never propagates.

``emit_resolver_eval_row``
    The node-side row emission — byte-compatible with the Stage-1 row
    shape plus the additive ``judge_verdict=`` / ``resolver_outcome=``
    tail fields (Stage 2) and the additive ``bundle_u_chars=`` /
    ``user_message_included=`` bundle-witness fields (section U,
    incident 4dfded83, 2026-09-18); ``judge_invoked`` is the DERIVED
    real-invocation flag.

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
from .attestation_scanner import is_real_user_message
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
    "BUNDLE_U_SECTION_MAX",
    "C_TREE_ROW_FETCH_CAP",
    "ChildReportCheckEvidence",
    "SourceASignals",
    "SourceBSignals",
    "SourceCSignals",
    "FusedBundle",
    "RESOLVER_OUTCOME_ALLOW",
    "RESOLVER_OUTCOME_ALLOW_HINT",
    "RESOLVER_OUTCOME_DENY_NUDGE",
    "RESOLVER_OUTCOME_TERMINAL_AFTER_BOUND",
    "ResolverEvalSnapshot",
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
    "emit_resolver_eval_row",
    "evaluate_resolver_activation",
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
#: Section U (the user's original request, incident 4dfded83 2026-09-18)
#: is ADDITIVE to the A+B+C sum: the total cap was raised 12000 → 14000
#: by exactly the U cap (2000) so the pre-existing A/B/C caps and their
#: pins are untouched. The final hard clip still enforces
#: :data:`BUNDLE_TOTAL_MAX` over the assembled text.
BUNDLE_A_SECTION_MAX: int = 3000
BUNDLE_B_SECTION_MAX: int = 6000
BUNDLE_C_SECTION_MAX: int = 3000
BUNDLE_U_SECTION_MAX: int = 2000
BUNDLE_TOTAL_MAX: int = 14000
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

#: Per-message clip (chars) for each B-section AIMessage excerpt
#: (dual-autopsy B1 item 5, 2026-09-20: raised 1500 → 2500 — incident
#: acbf5627's final leader report was clipped at 1500 of 2372 chars,
#: losing the evidence tail + activation note from the judge bundle).
#: 3 × (2500 + label) still exceeds :data:`BUNDLE_B_SECTION_MAX` (6000),
#: so the external per-section cap remains the binding budget — only the
#: per-message truncation point moves.
_B_MESSAGE_CLIP: int = 2500

#: Operator-action sentence tokens (dual-autopsy B1 item 3, 2026-09-20).
#: A catalog hit whose containing sentence ALSO names one of these tokens
#: describes the OPERATOR's pending action (rebuild / restart / redeploy
#: activation), not undelivered child work — excluded from
#: ``contradiction_flag`` on :class:`SourceASignals` and disqualified from
#: the later-contradiction exception on the completed-child suppression.
#: Kept deliberately TIGHT (no bare ``deploy``/``activation`` — children
#: legitimately deploy and activate things themselves); the canonical
#: incident shape "pending: rebuild+restart activation" carries both
#: leading tokens.
_OPERATOR_ACTION_TOKENS: tuple[str, ...] = (
    "rebuild",
    "restart",
    "re-deploy",
    "redeploy",
)

#: Sentence splitter for operator-action scoping: [.!?\n;] end sentences
#: and clauses; colon deliberately does NOT (the canonical incident
#: sentence "pending: rebuild+restart activation" must stay one span).
_SENTENCE_SPLIT_RE: re.Pattern[str] = re.compile(r"[.!?\n;]+")

#: The single tree-row status that qualifies a child as DELIVERED for the
#: A-vs-C cross-resolution (dual-autopsy B1 item 2, 2026-09-20). Only the
#: clean-delivered status suppresses: terminated/error/failed children
#: with advisories keep them (their work genuinely did not finish), and a
#: child ABSENT from the rows keeps its advisories (cannot cross-resolve —
#: conservative). Mirrors the lowercase runtime vocabulary of
#: ``InstanceStatus`` (daemon/constants.py ``TERMINAL_INSTANCE_STATUSES``
#: lists the full terminal family; only ``completed`` suppresses).
_CROSS_RESOLVE_DELIVERED_STATUS: str = "completed"


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
    #: The subset of ``matched_terms`` whose containing sentence ALSO
    #: names an operator action (rebuild / restart / redeploy —
    #: :data:`_OPERATOR_ACTION_TOKENS`). Dual-autopsy B1 item 3
    #: (2026-09-20): such hits describe the OPERATOR's pending action,
    #: not undelivered child work — they are EXCLUDED from
    #: ``contradiction_flag`` on :class:`SourceASignals` and disqualified
    #: from the later-contradiction exception on the completed-child
    #: suppression. Empty on the legacy note path (server-authored note
    #: text — no child-report content to sentence-scope).
    operator_scoped_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceASignals:
    """Source A — child-terminal contradiction suspicion (§4.1).

    Spec §4.1 anticipated ``{advisory_present, contradiction_flag,
    phrase_promise_while_stopping, word_count_below_threshold,
    child_instance_id, report_excerpt}``.

    **Stage-0 landed producer** (deleted by D-CTD-7, 2026-09-18):
    ``advisory_present ≡ phrase_match`` (the mint-only producer
    contract); ``contradiction_flag`` and
    ``word_count_below_threshold`` were structurally dead (default
    False) — preserved on the struct for forward-compatibility with
    the spec's wider surface.

    **2026-09-18 evaluation-time transcript scan** (this commit):
    ALL four booleans are now live re-derivations from the gate's
    scan of the leader's in-context ``internal_report:``-stamped
    child-report messages:

    * ``advisory_present`` — at least one catalog-hit child-report.
    * ``phrase_match`` — at least one catalog-hit has matched terms
      (≡ ``advisory_present`` on the live producer; see the
      field-mapping table on :func:`collect_source_a_signals`).
    * ``contradiction_flag`` — at least one SURVIVING matched_terms
      set contains a GENUINE explicit-contradiction marker
      (see :data:`_CONTRADICTION_MARKERS`; operator-scoped hits —
      sentence names rebuild/restart/redeploy — are excluded,
      dual-autopsy B1 item 3).
    * ``word_count_below_threshold`` — at least one matched
      child-report's raw word count is below 150 (Source B's
      ``length_trigger`` mirror).

    The 4-field OR shape feeding :func:`activation_predicate`'s
    ``a_suspicion`` term is preserved (the predicate wire contract
    is unchanged — D2 SEMANTICS STAY, A-band not busy-suppressed,
    activates alone).
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

    #: Full bundle text (per-section caps applied; total ≤14000).
    text: str
    #: sha256 hex digest of ``text`` (the soak's evidence fingerprint).
    sha256: str
    #: ``len(text)`` — convenience witness so the log row never re-encodes.
    total_chars: int
    a_chars: int
    b_chars: int
    c_chars: int
    #: Section-U (user request) rendered-section length — 0 when U was
    #: omitted (anchor absent). Incident 4dfded83 witness.
    u_chars: int = 0
    #: True when section U was included (a real user message anchored the
    #: mission). False ⇒ U is absent from ``text`` entirely.
    user_message_included: bool = False


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


@dataclass(frozen=True)
class ResolverEvalSnapshot:
    """One resolver evaluation's compute output, ready for row emission.

    Stage 2 (the flip, 2026-09-16): the predicate + bundle are computed
    in the gate thread (``attestation_gate.evaluate`` canonical-path
    tail) and the structured ``event=leader_completion_resolver_eval``
    row is emitted by the GRAPH NODE — AFTER the fused judge decision —
    so ``judge_invoked`` is DERIVED from the real invocation flag
    (:attr:`FusedJudgeResult.invoked` via
    :func:`attestation_report_judge.judge_fused_bundle_async`) instead
    of the Stage-1 literal ``False`` (the Stage-1 review hazard pin).
    The snapshot carries everything both sides need: the node consumes
    :attr:`result` (band + bundle) for the judge plan and the outcome
    mapping; :func:`emit_resolver_eval_row` consumes the rest for the
    log row.
    """

    #: The activation predicate's structured output (with bundle when
    #: fired).
    result: ActivationResult
    #: §4.3 NO-JUDGE would-be outcome — post-flip this remains the
    #: kill-switch-off / dry-mode reference outcome (the would-be the
    #: resolver WOULD map without a judge), still compared against the
    #: gate ``decide()`` value for the agreement flag.
    would_be_outcome: str
    #: The gate ``decide()`` value's ``.value`` string (log-shaped).
    old_decision_value: str
    #: Agreement between :attr:`would_be_outcome` and the old decision
    #: mapped into the four-value space (Stage-1 semantics, kept —
    #: post-flip the old JUDGE paths are dead-but-present and no longer
    #: evaluated, so the flag compares the resolver's no-judge would-be
    #: against the still-computed gate decision; documented in
    #: decisions.md D-RES2 + docs/setup.md).
    agreement: bool
    instance_id: str | None
    gate_location: str
    leader_prompt_version: str
    mode: str


# ─────────────────────────────────────────────────────────────────────────────
# Source A collection — the LANDED Stage-0 producer contract (pure)
# ─────────────────────────────────────────────────────────────────────────────


#: Live child-report source prefix (set on
#: ``additional_kwargs["source"]`` by the report-injection drain at
#: ``daemon/graph.py:6950-6967`` and the fallback enqueue lane at
#: ``daemon/services/instance_messaging.py:525``). The A-scan uses
#: this prefix to disambiguate child-report messages from
#: user-injected notes (which carry the same bare-flag
#: ``injected_message=True``).
_INTERNAL_REPORT_SOURCE_PREFIX: str = "internal_report:"

#: Regex extracting the child instance id from a stamped ``source``
#: attribute on a drained child-report ``HumanMessage`` (live path).
#: The shape is ``internal_report:<uuid>:<completed_message_id>``
#: (``daemon/graph.py:6960`` / ``daemon/services/instance_messaging.py:525``
#: predecessor); only the leading uuid prefix is needed.
_INTERNAL_REPORT_ID_FROM_SOURCE_RE: re.Pattern[str] = re.compile(
    rf"^{_INTERNAL_REPORT_SOURCE_PREFIX}"
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})(?::|$)"
)

#: Markers that explicitly signal a "claimed-done-but-isn't" contradiction
#: in the child-terminal-report content. When ANY matched_terms item from
#: a scanned child-report falls in this set, ``contradiction_flag`` is
#: raised on :class:`SourceASignals` (the 4-field OR's most pointed
#: sub-signal — strongest A-band suspicion).
_CONTRADICTION_MARKERS: frozenset[str] = frozenset(
    {"still pending", "not yet complete", "interim"}
)

#: Word-count threshold mirroring Source B's ``length_trigger`` (the
#: 17-pattern catalog is the siblings of the 16-pattern marker catalog;
#: the same short-completion heuristic applies). A scanned child-report
#: whose raw word count is BELOW this threshold raises
#: ``word_count_below_threshold`` on :class:`SourceASignals`.
_SHORT_REPORT_WORD_THRESHOLD: int = 150


def _word_count(text: str) -> int:
    """Return the whitespace-separated word count of ``text``.

    Cheap approximation used by the A-scan's word-count threshold. Empty
    text returns 0 (defensive floor — matches the catalog scanner's
    floor on empty input). Mirrors the :func:`attestation_scanner.
    final_word_count` approximation discipline.
    """
    if not text:
        return 0
    return len(text.split())


def _is_child_report_check_note(message: BaseMessage) -> bool:
    """Detect a delivered Child Report Check note (Stage-0 producer).

    DEFENSE-IN-DEPTH ONLY (post-D-CTD-7, 2026-09-18 restoration): the
    note mint site is DELETED (graph-resident only — see
    ``daemon/services/child_reports.py`` ``Stage-0 mint-with-delivery``
    block), so no NEW notes carry this shape. The detector survives for
    any historical checkpoint state — long-running leaders whose
    mid-flight checkpoint still contains a note minted before the
    6a695b8f removal, surviving compaction due to its
    ``context_kind=child_report_check`` permanent-hoist flag
    (``daemon/services/compaction.py:130-148``). The LIVE A-signal
    source is :func:`_is_child_report_message`.

    Dual-surface: structured kwargs (``context_kind=child_report_check``
    on the canonical :func:`_make_context_message` output) OR the
    always-present ``[SYSTEM CONTEXT: Child Report Check]`` content
    prefix.
    """
    if not isinstance(message, HumanMessage):
        return False
    kwargs = getattr(message, "additional_kwargs", None) or {}
    if kwargs.get("context_kind") == CONTEXT_KIND_CHILD_REPORT_CHECK:
        return True
    content = message.content if isinstance(message.content, str) else ""
    return content.startswith(_CHILD_REPORT_CHECK_PREFIX)


def _is_child_report_message(message: BaseMessage) -> bool:
    """Detect a LIVE child completion-report ``HumanMessage``.

    Post-D-CTD-7 restoration (2026-09-18): the live A-signal source is
    the same stamped ``HumanMessage`` rows the A-section already
    consumes — the report-injection drain at :file:`daemon/graph.py`
    line ~6950 (``report_extra_kwargs = {"injected_message": True,
    "source": f"internal_report:{report_child_iid}"}``) and the
    fallback ``PROCESS_REPORT`` task's enqueue-lane stamping via
    :func:`_stamped_additional_kwargs` at
    ``daemon/services/instance_messaging.py:525``. The
    ``source``-prefix disambiguates child-report messages from
    user-injected notes (which carry the same bare-flag
    ``injected_message=True``).
    """
    if not isinstance(message, HumanMessage):
        return False
    kwargs = getattr(message, "additional_kwargs", None) or {}
    source = kwargs.get("source")
    return isinstance(source, str) and source.startswith(
        _INTERNAL_REPORT_SOURCE_PREFIX
    )


def _extract_child_id_from_source(source: str) -> str | None:
    """Pull the child instance uuid from an ``internal_report:`` source.

    Returns ``None`` when the regex doesn't match — the source prefix
    is preserved (the scan still runs); only the child-id annotation on
    the evidence is empty.
    """
    match = _INTERNAL_REPORT_ID_FROM_SOURCE_RE.match(source or "")
    return match.group(1) if match else None


def _excerpt(text: str, limit: int = 400) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _operator_scoped_terms(content: str, matched_terms: tuple[str, ...]) -> tuple[str, ...]:
    """Split catalog hits into operator-scoped vs genuine (B1 item 3).

    A hit is OPERATOR-SCOPED when its containing sentence (split on
    ``[.!?\\n;]``) also names an operator-action token
    (:data:`_OPERATOR_ACTION_TOKENS`) — e.g. ``"Still pending: "
    "rebuild+restart activation (operator action)."`` describes the
    OPERATOR's next step, not undelivered child work. Hits whose
    sentence carries no operator token are genuine (the default).
    """
    if not matched_terms or not content:
        return ()
    lower = content.lower()
    scoped: list[str] = []
    for term in matched_terms:
        for sentence in _SENTENCE_SPLIT_RE.split(lower):
            if term in sentence and any(
                token in sentence for token in _OPERATOR_ACTION_TOKENS
            ):
                scoped.append(term)
                break
    return tuple(scoped)


def _completed_child_ids(tree_rows: Sequence[dict[str, Any]] | None) -> frozenset[str]:
    """Extract the delivered-child id set from C tree rows (B1 item 2).

    A row qualifies when its ``status`` is
    :data:`_CROSS_RESOLVE_DELIVERED_STATUS` (``completed``) — the clean
    delivered status ONLY. Rows with missing/malformed fields are
    skipped (cross-resolution is conservative by construction).
    """
    completed: set[str] = set()
    for row in tree_rows or ():
        if not isinstance(row, dict):
            continue
        iid = row.get("instance_id")
        status = row.get("status")
        if not isinstance(iid, str) or not iid:
            continue
        if (
            isinstance(status, str)
            and status.strip().lower() == _CROSS_RESOLVE_DELIVERED_STATUS
        ):
            completed.add(iid)
    return frozenset(completed)


def _build_evidence_from_report_message(
    message: BaseMessage,
) -> tuple[ChildReportCheckEvidence, int] | None:
    """Build a :class:`ChildReportCheckEvidence` from a child-report
    ``HumanMessage``, OR ``None`` when the catalog scan finds nothing.

    Returns a ``(evidence, raw_word_count)`` tuple — the raw word count
    is captured BEFORE the excerpt truncation so the
    ``word_count_below_threshold`` flag on :class:`SourceASignals`
    answers the catalogue-shot heuristic on the original text (not the
    400-char excerpt). When the scan finds no promise marker, returns
    ``None`` and the message is skipped (no evidence row, no signal).
    The evidence's ``operator_scoped_terms`` carry the sentence-scoped
    operator-action subset (B1 item 3 — excluded from
    ``contradiction_flag`` downstream).
    """
    if not isinstance(message, HumanMessage):
        return None
    content = message.content if isinstance(message.content, str) else ""
    kwargs = getattr(message, "additional_kwargs", None) or {}
    scan = scan_child_terminal_report_for_promises(content)
    if not scan.promise_hit:
        return None
    source = kwargs.get("source")
    child_id = (
        _extract_child_id_from_source(source)
        if isinstance(source, str)
        else None
    )
    stable_id = getattr(message, "id", None)
    return (
        ChildReportCheckEvidence(
            child_instance_id=child_id,
            matched_terms=scan.matched_terms,
            note_excerpt=_excerpt(content),
            stable_id=stable_id if isinstance(stable_id, str) else None,
            kwargs_surface_seen=isinstance(source, str)
            and source.startswith("internal_report:"),
            operator_scoped_terms=_operator_scoped_terms(
                content, scan.matched_terms
            ),
        ),
        _word_count(content),
    )


def collect_source_a_signals(
    messages: Sequence[BaseMessage],
    tree_rows_provider: Callable[[], list[dict[str, Any]]] | None = None,
) -> SourceASignals:
    """Scan the leader's message history for Source-A triggers.

    Post-D-CTD-7 (2026-09-18, restoration 2026-09-18): scans the
    leader's in-context child-report ``HumanMessage`` rows (the
    ``internal_report:<child_iid>`` stamp emitted by
    ``daemon/graph.py:6950-6967`` and the fallback
    ``_stamped_additional_kwargs`` path at
    ``daemon/services/instance_messaging.py:525``) for the 17-pattern
    catalog at gate-evaluation time. The 17-entry catalog is BYTE-
    IDENTICAL to the deleted Stage-0 producer contract (existing
    ``test_catalog_byte_identical`` identity pin stays green).

    **Dual-autopsy B1 stale-A fix (2026-09-20)** — advisories previously
    NEVER cleared: superseded phase-stop reports accumulated and held
    ``a_suspicion`` into final quiet-tree evals (incident acbf5627:
    4/4 final advisories stale; incident fba90db8: "still pending on my
    ledger" for work later APPROVED). Three evidence-quality changes,
    predicate band structure UNCHANGED (``a_suspicion`` stays a band —
    only its evidence quality changes):

    * **Newest-report-only per child** (B1 item 1) — each child's
      LATEST ``internal_report`` message (transcript order) is the ONLY
      one scanned; superseded reports from the same child drop
      wholesale. An earlier report's catalog hit that the newest
      report does not repeat does NOT resurface (delivery supersedes
      promise — pinned in ``TestNewestReportOnlyPerChild``).
    * **Operator-action scoping** (B1 item 3) — a catalog hit whose
      containing sentence also names an operator action (rebuild /
      restart / redeploy — :data:`_OPERATOR_ACTION_TOKENS`) is
      operator-scoped: EXCLUDED from ``contradiction_flag``
      ("pending: rebuild+restart activation" is the OPERATOR's action,
      not undelivered child work) and disqualified from the
      later-contradiction exception below. Genuine hits keep every
      old semantic.
    * **Cross-resolution vs C tree rows** (B1 item 2) — when
      ``tree_rows_provider`` is wired (the real path via
      :func:`evaluate_resolver_activation`), advisories from a child
      whose tree-row status is :data:`_CROSS_RESOLVE_DELIVERED_STATUS`
      (``completed``) are suppressed UNLESS that child's newest report
      still carries a GENUINE (non-operator-scoped) hit — a completed
      child that lied at the end must still surface (the
      later-contradiction exception; the child-lie class pin).
      Provider invocation is LAZY (only when advisory candidates
      exist) and best-effort (any raise ⇒ no rows ⇒ suppression
      no-ops); without a provider (default) NO suppression happens —
      back-compat for direct callers.

    Field mapping (old note-stamped → new scan-derived) — preserved
    4-field OR shape on :class:`SourceASignals` (the predicate wire
    contract is unchanged; D2 semantics STAY — A-band not
    busy-suppressed, activates alone):

    +---------------------------+----------------------------------------------------+
    | Field (SourceASignals)    | New meaning (evaluation-time scan of transcript)   |
    +===========================+====================================================+
    | ``advisory_present``      | True iff at least one catalog-hit child-report     |
    |                           | SURVIVES newest-only + cross-resolution (old ≡     |
    |                           | phrase semantic preserved on survivors).           |
    +---------------------------+----------------------------------------------------+
    | ``phrase_match``          | True iff at least one SURVIVING catalog-hit        |
    |                           | child-report has a non-empty ``matched_terms`` set |
    |                           | (primary trigger; old semantic preserved).         |
    +---------------------------+----------------------------------------------------+
    | ``contradiction_flag``    | True iff any SURVIVING catalog-hit's GENUINE       |
    |                           | ``matched_terms`` (operator-scoped hits excluded)  |
    |                           | include an explicit-contradiction marker           |
    |                           | (:data:`_CONTRADICTION_MARKERS`). Raises the       |
    |                           | strongest "claimed-done-but-isn't" sub-signal.     |
    +---------------------------+----------------------------------------------------+
    | ``word_count_below_       | True iff any SURVIVING catalog-hit child-report's  |
    | threshold``               | raw word count is <                                |
    |                           | :data:`_SHORT_REPORT_WORD_THRESHOLD` (150). Mirror |
    |                           | of Source B's ``length_trigger``.                  |
    +---------------------------+----------------------------------------------------+

    Defense-in-depth: the legacy note path (:func:`_is_child_report_check_note`)
    is RETAINED — old notes may still ride in long-running checkpoints
    that survived the removal upgrade (they're ``context_kind``-
    hoisted above compaction). The LIVE A-signal source is the
    transcript scan; the note-path adds no new evidence rows in
    production. Note-path entries participate in the cross-resolution
    too: a completed child's note is suppressed unless that child's
    newest live report carries a genuine hit (or no live report exists
    for the child — conservative keep; the note text carries no
    child-report content to sentence-scope, so its own terms cannot
    prove the exception by themselves).

    Pure over ``messages``; the ONLY I/O is the optional lazily-invoked
    ``tree_rows_provider`` (the same best-effort provider the bundle's
    C-section consumes — shared, fetch-once per evaluation). No LLM,
    no direct DB. Reads the ``state["messages"]`` projection the gate
    already walks.
    """
    evidence: list[ChildReportCheckEvidence] = []
    raw_word_counts: list[int] = []

    # ── Pass 1 — legacy note detection (defense-in-depth only) ───────
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
        # Note-body word count is informational only on the legacy path
        # (the standard template is well above the threshold); the flag
        # below is honest if it ever trips on an unusual note body.
        raw_word_counts.append(_word_count(body))

    # ── Pass 2 — live child-report scan, NEWEST-REPORT-ONLY per child ─
    # B1 item 1: walk ALL internal_report messages FIRST and keep only
    # each child's LAST one (transcript order = delivery order), THEN
    # scan that newest report. A child whose newest report is CLEAN
    # (delivered) yields NO evidence even when its superseded reports
    # carried catalog hits (the acbf5627 phase-stop accumulation).
    # Reports whose child id cannot be parsed group under the ``None``
    # key (id-less reports are indistinguishable — newest wins).
    newest_report_by_child: dict[str | None, BaseMessage] = {}
    for message in messages:
        if not _is_child_report_message(message):
            continue
        kwargs = getattr(message, "additional_kwargs", None) or {}
        source = kwargs.get("source")
        child_id = (
            _extract_child_id_from_source(source)
            if isinstance(source, str)
            else None
        )
        newest_report_by_child[child_id] = message

    live_report_entry_ids: set[int] = set()
    for child_id, message in newest_report_by_child.items():
        if len(evidence) >= A_EVIDENCE_NOTES_CAP:
            break
        # Skip duplicates on stable_id when a note-path entry already
        # recorded the same row (rare — a parent that survived the
        # upgrade carries both shapes) — defense-in-depth bookkeeping.
        stable_attr = getattr(message, "id", None)
        if isinstance(stable_attr, str) and any(
            ev.stable_id == stable_attr for ev in evidence
        ):
            continue
        built = _build_evidence_from_report_message(message)
        if built is None:
            continue
        ev, raw_word_count = built
        evidence.append(ev)
        raw_word_counts.append(raw_word_count)
        live_report_entry_ids.add(id(ev))

    # ── Cross-resolution vs C tree rows (B1 item 2) ──────────────────
    # Suppress advisories from a DELIVERED child (tree status
    # ``completed``) unless the later-contradiction exception holds:
    # the child's newest report still carries a GENUINE (non-
    # operator-scoped) hit — a completed child that lied at the end
    # must still surface. Lazily invoked ONLY when advisory candidates
    # exist; any provider failure ⇒ no rows ⇒ suppression no-ops.
    if evidence and tree_rows_provider is not None:
        try:
            tree_rows = tree_rows_provider()
        except Exception:  # noqa: BLE001 — best-effort, mirrors the C provider
            tree_rows = []
        completed = _completed_child_ids(tree_rows)
        if completed:
            # Per child: the GENUINE hit set of the newest live report —
            # an EMPTY frozenset means "has a newest live report and it
            # carries no genuine hit" (delivered); an ABSENT key means
            # no live report exists for that child (nothing proves
            # delivery — conservative keep for its note entries).
            newest_genuine_terms: dict[str | None, frozenset[str]] = {}
            for cid, message in newest_report_by_child.items():
                built = _build_evidence_from_report_message(message)
                newest_genuine_terms[cid] = (
                    frozenset(built[0].matched_terms).difference(
                        built[0].operator_scoped_terms
                    )
                    if built is not None
                    else frozenset()
                )
            kept: list[ChildReportCheckEvidence] = []
            kept_counts: list[int] = []
            for idx, ev in enumerate(evidence):
                cid = ev.child_instance_id
                if cid in completed:
                    if id(ev) in live_report_entry_ids:
                        # Live entry: the exception reads THIS entry
                        # (it IS the child's newest report).
                        survives = bool(
                            frozenset(ev.matched_terms).difference(
                                ev.operator_scoped_terms
                            )
                        )
                    else:
                        # Note entry: the exception reads the child's
                        # newest LIVE report; when NO live report exists
                        # for the child, keep the note (conservative).
                        survives = (
                            cid not in newest_genuine_terms
                            or bool(newest_genuine_terms[cid])
                        )
                    if not survives:
                        continue
                kept.append(ev)
                kept_counts.append(raw_word_counts[idx])
            evidence = kept
            raw_word_counts = kept_counts

    if not evidence:
        return SourceASignals(
            advisory_present=False,
            phrase_match=False,
        )

    has_match = any(bool(ev.matched_terms) for ev in evidence)
    has_explicit_contradiction = has_match and any(
        t in _CONTRADICTION_MARKERS
        for ev in evidence
        for t in ev.matched_terms
        if t not in ev.operator_scoped_terms
    )
    has_short_word_count = has_match and any(
        wc < _SHORT_REPORT_WORD_THRESHOLD for wc in raw_word_counts
    )

    return SourceASignals(
        advisory_present=has_match,
        phrase_match=has_match,  # landed producer: catalog hit ≡ advisory
        contradiction_flag=has_explicit_contradiction,
        word_count_below_threshold=has_short_word_count,
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

    Per the 98b59dd7 evidence boundary: ALL id-bearing text is
    redacted — the structural fields (A-section child ids/stable ids,
    C-section tree rows) AND the B-section leader-prose excerpts (the
    leader may quote instance ids verbatim in its own prose; Stage-3
    ledger item (a), 2026-09-17, replaced the former docstring
    scope-note workaround from f926de24 with the real redaction).
    Non-UUID ids (short tokens, agent names) pass through — the
    id-bearing fields are UUIDs by construction.
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
        lines.append(
            f"[{shown + 1}] {redact_ids(_clip(content, _B_MESSAGE_CLIP), 'leader')}"
        )
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


#: Slot hint for :func:`redact_ids` on section U — the user's own prose
#: may legitimately quote instance ids (e.g. "what does agent
#: <uuid> see?"); the 98b59dd7 boundary redacts ALL id-bearing bundle
#: text, U included.
_USER_INTENT_SLOT_HINT = "user"


def _build_u_section(user_intent_message: BaseMessage | None) -> str | None:
    """Render section U — the user's original request — or ``None``.

    REUSES the delegation scanner's canonical real-user predicate
    (:func:`attestation_scanner.is_real_user_message` — the same
    exclusion ladder that anchors the delegation window) as a
    belt-and-braces re-check on the message the gate passes through
    from its already-computed ``delegation_scan.last_real_user_index``
    anchor. NO new source scans / DB queries — the caller passes the
    CONTENT of a message the scanner already walked.

    Returns ``None`` when the anchor is absent (no message passed, or
    it fails the real-user predicate) — the caller OMITS section U
    entirely in that case (``user_message_included=False``).
    """
    if user_intent_message is None:
        return None
    if not is_real_user_message(user_intent_message):
        # Fail-closed to omission: a non-user message must never render
        # as "the user's original request" (the judge would score
        # intent-fulfillment against an injected nudge/report instead).
        return None
    content = (
        user_intent_message.content
        if isinstance(user_intent_message.content, str)
        else str(user_intent_message.content or "")
    )
    lines: list[str] = []
    lines.append("=== SOURCE U: the user's original request for this mission ===")
    lines.append(redact_ids(content, _USER_INTENT_SLOT_HINT))
    lines.append("")
    return "\n".join(lines)


def assemble_fused_bundle(
    *,
    a_signals: SourceASignals | None,
    b_signals: SourceBSignals | None,
    c_signals: SourceCSignals | None,
    c_tree_rows: Sequence[dict[str, Any]],
    ai_tail_messages: Sequence[BaseMessage],
    user_intent_message: BaseMessage | None = None,
) -> FusedBundle:
    """Assemble the §4.1 fused evidence bundle (pure; caps; redacted).

    Per-section caps: A ≤3000, B ≤6000, C ≤3000, U ≤2000 (sum ≤14000
    total — U is ADDITIVE; the 12000→14000 raise is exactly the U cap so
    the pre-existing A/B/C caps are untouched — enforced defensively by
    a final hard clip with a truncation marker).
    ALL id-bearing text is redacted (:func:`redact_ids`): the
    structural fields (A-section child/stable ids, C-section tree
    rows), the B-section leader-prose excerpts (last-3 AIMessages, each
    clipped at :data:`_B_MESSAGE_CLIP` = 2500 chars — dual-autopsy B1
    item 5, 2026-09-20, raised from 1500 so a final report's evidence
    tail + activation note survive into the judge bundle; incident
    acbf5627 clipped at 1500/2372 — the leader may quote instance ids
    in its own prose; Stage-3 ledger item (a), 2026-09-17), AND the
    U-section user prose (incident 4dfded83, 2026-09-18).
    ``user_intent_message`` is the CONTENT of the last real user
    message (the delegation scanner's anchor — extracted by the gate
    from its already-computed ``last_real_user_index``). Anchor absent
    ⇒ U is OMITTED entirely (``user_message_included=False``) — the
    judge scores the other three sections unchanged.
    The graph node's fused block feeds this bundle
    VERBATIM to :func:`attestation_report_judge.judge_fused_bundle_async`
    — the ONE judge call site.
    """
    a_section = _clip(_build_a_section(a_signals), BUNDLE_A_SECTION_MAX)
    b_section = _clip(_build_b_section(b_signals, ai_tail_messages), BUNDLE_B_SECTION_MAX)
    c_section = _clip(_build_c_section(c_signals, c_tree_rows), BUNDLE_C_SECTION_MAX)
    u_section_full = _build_u_section(user_intent_message)
    u_section = (
        _clip(u_section_full, BUNDLE_U_SECTION_MAX) if u_section_full is not None else ""
    )
    # Emission order is U → A → B → C — intent-first reading so the
    # judge sees the user's original request before any of the
    # report-shape / tree-status evidence; matches the
    # ``FUSED_JUDGE_SYSTEM_PROMPT`` enumeration order ("SOURCE U …,
    # SOURCE A …, SOURCE B …, and SOURCE C …") and the
    # docs/setup.md "U+A+B+C evidence bundle" claim. A/B/C relative
    # order + inter-section blank-line discipline otherwise byte-
    # identical to the pre-feature shape — a pure emission-order
    # change. U is OMITTED (u_section="") when the anchor is absent.
    text = "[LCA FUSED EVIDENCE BUNDLE v1]\n"
    if u_section:
        text += u_section + "\n"
    text += (
        a_section
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
        u_chars=len(u_section),
        user_message_included=bool(u_section),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Stage-2 invocation seam — RETIRED into the graph-node fused block
# ─────────────────────────────────────────────────────────────────────────────

# Stage 1 shipped a structurally-present, provably-inert module-global seam
# (``STAGE2_JUDGE_SEAM`` + ``_maybe_invoke_stage2_judge``). Stage 2 (the
# flip, 2026-09-16) wires the REAL invocation at its natural home — the
# graph node's fused block (async context, mirrors the two legacy judge
# sites it replaces), calling
# :func:`attestation_report_judge.judge_fused_bundle_async` ONCE per
# evaluation. The module-global seam mechanism is retired by that wiring:
# the single judge call site IS the seam, and the invocation flag it
# produces (:attr:`FusedJudgeResult.invoked`) is the derivation source for
# the ``judge_invoked=`` field on ``event=leader_completion_resolver_eval``
# rows (the Stage-1 review hazard pin — no literals). See decisions.md
# D-RES2.


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


def evaluate_resolver_activation(
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
    user_intent_message: BaseMessage | None = None,
) -> ResolverEvalSnapshot:
    """Compute ONE resolver evaluation and return the log-ready snapshot.

    Called ONLY from the canonical path of
    ``attestation_gate.evaluate`` (post canonical log row, pre return —
    exactly where the existing gate evaluation runs; the meta-bypass /
    off-mode early returns never reach it). The C values are the gate's
    ALREADY-MATERIALIZED facade reads (zero new DB reads for the
    predicate); the A scan is a pure walk of the in-node ``messages``;
    the B values are the gate's already-computed marker/length results.
    The tree-rows provider runs LAZILY and ONLY when the predicate
    fires (bundle assembly time).

    Stage 2 (the flip): this function COMPUTES only — predicate →
    would-be outcome → agreement → conditional bundle assembly →
    snapshot. It does NOT log and does NOT invoke any LLM; the
    graph node's fused block consumes the snapshot (band + bundle),
    invokes the fused judge when the plan fires, and emits the
    structured row via :func:`emit_resolver_eval_row` with the
    DERIVED ``judge_invoked`` flag. The caller wraps this in its own
    try/except (exception isolation) — a resolver-side error is
    logged there as ``event=leader_completion_resolver_eval_error``
    and never propagates into gate control flow.

    ``user_intent_message``: the CONTENT of the last real user message —
    the gate extracts it from its ALREADY-COMPUTED
    ``delegation_scan.last_real_user_index`` anchor (zero re-walk, zero
    new DB reads) and the bundle renders it as section U (incident
    4dfded83). ``None`` ⇒ U omitted.

    **Tree-rows provider sharing (dual-autopsy B1, 2026-09-20):** the
    best-effort ``c_tree_rows_provider`` now has TWO consumers — the
    A-scan's cross-resolution (suppress delivered children's advisories,
    B1 item 2) and the bundle's C-section. A fetch-once cache wraps the
    provider so it runs at most ONE time per evaluation, and only when
    a consumer actually needs rows (A-scan: advisory candidates exist;
    bundle: the predicate fired). The DB cost still does not exist on
    clean no-advisory evaluations.
    """
    # Fetch-once cache shared by the A-scan cross-resolution + the
    # bundle's C-section (see docstring).
    tree_rows_cache: list[list[dict[str, Any]] | None] = [None]

    def _cached_tree_rows() -> list[dict[str, Any]]:
        if tree_rows_cache[0] is None:
            tree_rows_cache[0] = (
                c_tree_rows_provider()
                if c_tree_rows_provider is not None
                else []
            )
        return tree_rows_cache[0]

    result = activation_predicate(
        attestation_enabled=attestation_enabled,
        scope_applicable=scope_applicable,
        mode=mode,
        attestation_required=attestation_required,
        attested=attested,
        user_answer_pending=user_answer_pending,
        a_source=lambda: collect_source_a_signals(
            messages, tree_rows_provider=_cached_tree_rows
        ),
        b_source=lambda: b_values,
        c_source=lambda: c_values,
    )
    would_be = compute_would_be_outcome(result, denied_count=denied_count, deny_bound=deny_bound)
    old_outcome = map_old_decision_to_outcome(old_decision)

    bundle: FusedBundle | None = None
    if result.fired:
        bundle = assemble_fused_bundle(
            a_signals=result.a_signals,
            b_signals=result.b_signals,
            c_signals=result.c_signals,
            c_tree_rows=_cached_tree_rows(),
            ai_tail_messages=messages,
            user_intent_message=user_intent_message,
        )
        result = replace(result, bundle=bundle)

    return ResolverEvalSnapshot(
        result=result,
        would_be_outcome=would_be,
        old_decision_value=getattr(old_decision, "value", str(old_decision)),
        agreement=compute_agreement(would_be, old_outcome),
        instance_id=instance_id,
        gate_location=gate_location,
        leader_prompt_version=leader_prompt_version,
        mode=mode,
    )


#: Authoritative resolver outcome values (Stage 2). Emitted on the
#: ``resolver_outcome=`` field of the eval row — the soak watches these
#: directly post-flip (the agreement flag's role shrinks to the no-judge
#: reference comparison; see docs/setup.md).
RESOLVER_OUTCOME_ALLOW: str = "allow"
RESOLVER_OUTCOME_ALLOW_HINT: str = "allow_hint"
RESOLVER_OUTCOME_DENY_NUDGE: str = "deny_nudge"
RESOLVER_OUTCOME_TERMINAL_AFTER_BOUND: str = "terminal_after_bound"


def emit_resolver_eval_row(
    snapshot: ResolverEvalSnapshot,
    *,
    judge_invoked: bool,
    judge_verdict: str = "<none>",
    resolver_outcome: str = RESOLVER_OUTCOME_ALLOW,
) -> None:
    """Emit the ONE structured ``leader_completion_resolver_eval`` row.

    Stage 2: called by the graph node's fused block AFTER the judge
    decision (or the no-judge plan decision). ``judge_invoked`` MUST be
    the DERIVED real-invocation flag — ``fused_result.invoked`` when a
    :class:`~.attestation_report_judge.FusedJudgeResult` exists, else
    ``False`` — never a literal (Stage-1 review hazard pin). The row
    shape is byte-compatible with Stage 1 (same fields, same order);
    the two additive tail fields ``judge_verdict=`` and
    ``resolver_outcome=`` carry the post-flip authoritative decision so
    the soak can watch resolver outcomes directly.

    NOTE (grep hygiene): ``leader_completion_resolver_eval`` is a PREFIX
    of ``leader_completion_resolver_eval_error`` — dashboards must
    anchor greps on the TRAILING token (e.g. ``resolver_eval `` /
    ``resolver_eval_error``), never on the bare prefix. Documented in
    docs/setup.md (Stage-1 review hazard pin, carried forward).
    Additive-field evolution (key=value rows — grep consumers are
    field-name-anchored, so append/insert of new fields is safe):
    Stage 2 appended ``judge_verdict=`` / ``resolver_outcome=``; the
    section-U fix (incident 4dfded83, 2026-09-18) adds
    ``bundle_u_chars=`` / ``user_message_included=`` beside the other
    bundle witnesses.
    """
    result = snapshot.result
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
        "bundle_u_chars=%s user_message_included=%s "
        "judge_invoked=%s judge_verdict=%s resolver_outcome=%s",
        snapshot.instance_id,
        snapshot.gate_location,
        snapshot.leader_prompt_version,
        snapshot.mode,
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
        snapshot.would_be_outcome,
        snapshot.old_decision_value,
        snapshot.agreement,
        result.fail_open,
        result.bundle.sha256 if result.bundle else "<none>",
        result.bundle.total_chars if result.bundle else 0,
        result.bundle.a_chars if result.bundle else 0,
        result.bundle.b_chars if result.bundle else 0,
        result.bundle.c_chars if result.bundle else 0,
        result.bundle.u_chars if result.bundle else 0,
        result.bundle.user_message_included if result.bundle else False,
        judge_invoked,
        judge_verdict,
        resolver_outcome,
    )


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

    ``judge_invoked`` here is derived, not literal: a fail-open
    evaluation short-circuits BEFORE any resolver computation — no
    snapshot, no fused judge plan, no invocation record exists — so the
    derived value is the absence-of-record default (``False``), same
    derivation rule the node's fused block applies (no
    :class:`~.attestation_report_judge.FusedJudgeResult` ⇒ not invoked).
    """
    old_outcome = map_old_decision_to_outcome(old_decision)
    judge_invoked = False  # derived: no invocation record can exist on fail-open
    # NOTE: ``bundle_u_chars=`` and ``user_message_included=`` are DELIBERATELY
    # ABSENT from this fail-open row's log format. The fused bundle is
    # never assembled on the fail-open path (``bundle=None`` — short-circuit
    # BEFORE any resolver computation), so the values would be 0/False —
    # i.e. the absence-of-record default the row already implicitly
    # conveys via ``bundle_size_chars=0`` / ``bundle_sha256=<none>``. Mirrors
    # the omission of ``bundle_u_chars=`` on every other pre-U fail-open
    # row (a fail-open row never carries per-section sizes at all).
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
        "judge_invoked=%s error_class=%s",
        instance_id,
        gate_location,
        leader_prompt_version,
        mode,
        WOULD_ALLOW,
        getattr(old_decision, "value", str(old_decision)),
        compute_agreement(WOULD_ALLOW, old_outcome),
        judge_invoked,
        error_class,
    )
