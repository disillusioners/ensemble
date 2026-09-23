"""Attestation gate — the R2 decision logic + gate composition (Phase 2).

Phase 2 of the leader completion attestation feature, tasks 2.2 + 2.3
(+ 2.3.1 docstring contract). This module is the SINGLE HOME of the
canonical decision enum and the gate evaluation glue:

* :class:`Decision` — the canonical 5-value enum from Phase 4 task 4.5
  (``allowed | denied | terminal_after_bound | dry_log |
  allowed_legitimate_pending_wakeup``), defined HERE and referenced
  verbatim by every other phase. Later phases IMPORT this definition —
  they must not restate it.
* :func:`decide` — the pure decision function over the R2 inputs.
* :func:`evaluate` — the composition glue: scanner → manager facades →
  ``decide`` → canonical structured log entry.
* :class:`GateSettings` / :func:`resolve_gate_settings` — the MINIMAL
  Phase 2 stand-in for the Phase 4 resolver (window/bound/mode with
  defaults 3/3/dry). Phase 4 replaces the resolver, not the settings
  shape consumed by :func:`evaluate`.

R2 (deny-input) semantics
-------------------------

Deny fires ONLY when ALL of: not attested AND ``pending_children == 0``
AND ``queued_or_expected_wakeups == 0`` AND ``live_descendants == 0``.
Any non-zero pending input means a legitimate wakeup is en route and
the turn-end is allowed without attestation (the nudge-flood kill).
The three-input R2 predicate (``live_descendants`` added 2026-09-06)
closes the 809e2a59 waiting_children false-deny incident class — see
``decide()`` arg ``live_descendants`` and the manager facade
``InstanceManager.count_live_descendants``.

Counter-reset semantics (leader ruling 1, SUPERSEDES plan prose)
----------------------------------------------------------------

``attestation_denied_count`` resets ONLY on the four triggers:

1. attested allow (``allowed`` under enforce after a scanner hit) —
   ``next_denied_count = 0``;
2. ``terminal_after_bound`` finalization (escalation path) —
   ``next_denied_count = 0`` (the same single reset op clears the
   ``completion_gate_escalated`` flag per leader ruling 2);
3. revive-from-COMPLETED via a NEW top-level user/mission message
   (Phase 3 / lifecycle, not this module);
4. instance creation (Phase 3 migration default, not this module).

``allowed_legitimate_pending_wakeup`` (R2 un-attested allow) MUST NOT
reset the counter — that non-reset IS the loop protection: a leader
that keeps hallucinating completions between legitimate wakeups still
accumulates denials toward the bound. In this module that means
``decide(...)` returns ``next_denied_count == denied_count`` (unchanged)
for every value EXCEPT attested-allow and terminal-after-bound.

Fail-open (C3)
--------------

Every failure path in :func:`evaluate` (and at the graph wiring call
site) resolves to ``allowed``: an unhandled scanner/gate/DB exception
on the would-be-END routing path must never error every leader mission
(D2's outage class). DB-read failures are logged as
``event=leader_completion_gate_db_error``; scanner/decide failures as
``event=leader_completion_gate_error`` — both carry ``error_class``.
``KeyboardInterrupt``/``SystemExit`` are BaseException and propagate
(fail-closed on interpreter shutdown) because every handler here is
``except Exception``.

aget_state ban (live-defect avoidance)
--------------------------------------

The gate reads ``state["messages"]`` from the in-node LangGraph state
argument ONLY. It NEVER calls ``aget_state``: the known live defect
(namespace-mismatched ``aget_state`` reads returning EMPTY checkpoint
state — ``graph.py`` hook class of bug) would make every gate
evaluation see zero messages and either deny-loop or mis-log. The O8
unit guard (``tests/unit/test_attestation_gate.py``) asserts the gate
config shape carries NO ``checkpoint_ns`` key.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, NamedTuple

from langchain_core.messages import AIMessage, BaseMessage

from .attestation_scanner import (
    DEFAULT_ATTESTATION_TOOL_NAME,
    DEFAULT_DELEGATION_TOOL_NAME,
    scan_delegation_after_last_user,
    scan_for_attestation_detailed,
)

# Phase 6 fastfollow (2026-09-11, incident b08f40fe) — mid-work marker
# scanner. Pure function over the AIMessage tail; case-insensitive
# substring match against a curated catalog. The marker scan is the
# TRIGGER half of a two-stage disambiguator on the gate's ALLOW
# paths. The judge verdict (the existing inline-LLM judge service at
# :mod:`daemon.services.attestation_report_judge`) is the VERDICT half.
#
# Length trigger (2026-09-12, user request) — word-count signal on the
# LAST AIMessage. Orthogonal to the marker scan: markers catch phrasing,
# length catches brevity. The two are composed via ``OR`` on the gate's
# ALLOW paths (``marker_hit OR length_trigger`` → judge fires).
from .attestation_marker_scanner import (
    MID_WORK_MARKERS,
    MARKER_TERMS_LIST_CAP,
    SHORT_REPORT_WORD_THRESHOLD,
    MarkerScanResult,
    scan_for_mid_work_markers,
    scan_for_short_final_ai,
)

# Phase 4 — canonical resolver lives in its own module (Pattern C, single
# source of truth for the mode/window/deny_bound env triple). The gate
# re-imports the resolver via ``resolve_gate_settings`` below (back-compat
# shim) and via :func:`attestation_resolver.record_promotion_metric` for
# the dry-mode and enforce-denied promotion metrics (task 4.6). Anything
# that needs the boot log calls :func:`attestation_resolver.emit_attestation_boot_log`
# directly from the manager-init path — keeping the resolver as the
# single source of truth.
from .attestation_resolver import (
    DEFAULT_MODE,
    DEFAULT_WINDOW,
    DEFAULT_DENY_BOUND,
    METRIC_DRY_LOG_TOTAL,
    METRIC_DRY_LOG_DENY_PREDICATE_TOTAL,
    METRIC_ENFORCE_DENIED_TOTAL,
    AttestationConfig,
    get_config as _resolver_get_config,
    record_promotion_metric,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Canonical decision enum (Phase 4 task 4.5 — SINGLE shared definition)
# ─────────────────────────────────────────────────────────────────────────────


class Decision(str, Enum):
    """Canonical gate decision enum (Phase 4 task 4.5, verbatim).

    Exactly five values; every gate evaluation resolves to one of them.
    This is the single shared definition — phases 3/4/5 import it from
    here and must NOT redefine it in a second place.
    """

    #: meta-condition skip (gate off / out of scope) OR attested allow
    #: under enforce — terminal-status write proceeds.
    ALLOWED = "allowed"
    #: R2 deny under enforce — terminal-status write NOT performed;
    #: in-graph nudge injected; execution routes back to ``agent``.
    DENIED = "denied"
    #: 2026-09-19 (attest-first contract, c5d9a38a remediation) —
    #: attestation IS present, but the final AIMessage is NOT a
    #: standalone text report (it's either the attest-call message
    #: itself with no subsequent text, or the bundled shape where
    #: the AIMessage carried text + the tool_call). The gate injects
    #: a short reminder (counter-INDEPENDENT — does NOT increment
    #: ``attestation_denied_count``; capped at
    #: ``ATTESTATION_REMINDER_CAP`` per mission) and routes back to
    #: ``agent``. Completion (meta_bypass allow) fires ONLY when
    #: attestation_present AND the final AIMessage is a standalone
    #: text report (no tool calls, non-trivial length >=
    #: ``SHORT_REPORT_WORD_THRESHOLD``).
    HOLD = "hold"
    #: escalation path under enforce — allow terminal +
    #: ``completion_gate_escalated=true`` + counter reset (Phase 3
    #: persists both; this module only emits the decision value).
    TERMINAL_AFTER_BOUND = "terminal_after_bound"
    #: dry-mode evaluation: allow terminal + zero side effects (D2/D8).
    DRY_LOG = "dry_log"
    #: R2 allow under enforce — ``pending_children > 0`` OR
    #: ``queued_or_expected_wakeups > 0`` (nudge-flood kill per R2).
    ALLOWED_LEGITIMATE_PENDING_WAKEUP = "allowed_legitimate_pending_wakeup"


# ─────────────────────────────────────────────────────────────────────────────
# Settings — Phase 4 canonical resolver lives in
# ``daemon.services.attestation_resolver``. The legacy NamedTuple below
# is preserved as the public seam the gate (``build_instance_graph``,
# ``create_attestation_gate_node``) and Phase 2 unit tests
# (``tests/unit/test_attestation_gate.py``) already consume.
# ─────────────────────────────────────────────────────────────────────────────


class GateSettings(NamedTuple):
    """Public seam: the NamedTuple the gate consumes (Phase 2 stand-in,
    Phase 4-preserved shape).

    Phase 4 ships the real Pattern-C resolver module
    (:mod:`daemon.services.attestation_resolver`) as the SINGLE home of
    the three env knobs. This NamedTuple IS the resolver output shape
    that ``build_instance_graph`` threads through to the gate node;
    Phase 4 keeps it so the gate seam is unchanged (the resolver
    produces an :class:`AttestationConfig`; :func:`resolve_gate_settings`
    maps it to a :class:`GateSettings`).
    """

    #: tri-state mode: "off" | "dry" | "enforce" (D2, default dry)
    mode: str = DEFAULT_MODE
    #: attestation window N (D4, default 3)
    window: int = DEFAULT_WINDOW
    #: deny bound (D5, default 3)
    deny_bound: int = DEFAULT_DENY_BOUND


def _config_to_settings(config: AttestationConfig) -> GateSettings:
    """Map the canonical resolver output to the legacy NamedTuple shape."""
    return GateSettings(
        mode=config.mode,
        window=config.window,
        deny_bound=config.deny_bound,
    )


#: Cache the NamedTuple view so identity comparisons
#: (``first is second``) survive — preserves the Phase 2 unit-test
#: contract that "the cached value wins across calls". The cache is
#: invalidated by :func:`_reset_gate_settings_for_tests` (test-only).
_CACHED_GATE_SETTINGS: GateSettings | None = None


def resolve_gate_settings() -> GateSettings:
    """Resolve the gate settings (Phase 4 back-compat shim).

    Phase 2 introduced this function as the minimal stand-in resolver;
    Phase 4 replaces its body with a thin delegation to the canonical
    Pattern-C resolver in :mod:`daemon.services.attestation_resolver`.
    The function signature, return shape, restart-read semantics, AND
    identity preservation (``first is second`` after env mutation —
    the Phase 2 unit-test contract) are preserved — the gate node and
    the existing unit tests (``tests/unit/test_attestation_gate.py``)
    keep working unchanged.

    Returns:
        :class:`GateSettings` — a NamedTuple view of the canonical
        resolver's :class:`AttestationConfig` (mode/window/deny_bound).
    """
    global _CACHED_GATE_SETTINGS
    if _CACHED_GATE_SETTINGS is None:
        _CACHED_GATE_SETTINGS = _config_to_settings(_resolver_get_config())
    return _CACHED_GATE_SETTINGS


#: Default Phase 4 settings — equivalent to ``resolve_gate_settings()``
#: when no env is set (window 3 / bound 3 / mode dry per D2/D4/D5).
#: Back-compat export for tests that import ``DEFAULT_GATE_SETTINGS``
#: to thread through ``build_gate_config`` / ``evaluate`` without going
#: through the resolver (which mutates the cache).
DEFAULT_GATE_SETTINGS = GateSettings(
    mode=DEFAULT_MODE,
    window=DEFAULT_WINDOW,
    deny_bound=DEFAULT_DENY_BOUND,
)


# ─────────────────────────────────────────────────────────────────────────────
# Back-compat test helpers — Phase 2 unit tests reset the stand-in cache
# by calling ``_reset_gate_settings_for_tests``. Phase 4 delegates to the
# canonical resolver's reset helper; the function name is preserved so
# the existing tests do not need to change.
# ─────────────────────────────────────────────────────────────────────────────


def _reset_gate_settings_for_tests() -> None:
    """Clear the canonical resolver cache so tests can re-resolve after
    mutating the env. Test-only — production code never invokes this."""
    global _CACHED_GATE_SETTINGS
    _CACHED_GATE_SETTINGS = None
    from .attestation_resolver import reset_attestation_resolver_for_tests

    reset_attestation_resolver_for_tests()


# ─────────────────────────────────────────────────────────────────────────────
# Gate decision object
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GateDecision:
    """The gate's evaluation output (plan 2.2 field set + log-ready inputs).

    ``decide()`` fills the decision core; ``evaluate()`` attaches the
    scanner diagnostics and R2 inputs so the canonical log entry can be
    emitted from one object.
    """

    #: canonical enum value (Phase 4 task 4.5 — single definition above)
    decision: Decision
    #: counter value AFTER the decision (see the reset semantics in the
    #: module docstring — ONLY attested-allow and terminal-after-bound
    #: produce a value different from the input counter).
    next_denied_count: int
    #: True ONLY for :attr:`Decision.DENIED` — the nudge guard reads
    #: this, never the raw enum, so ``terminal_after_bound`` and
    #: ``dry_log`` structurally cannot nudge.
    should_inject_nudge: bool
    #: 2026-09-19 (attest-first contract, c5d9a38a remediation) —
    #: True ONLY for :attr:`Decision.HOLD` — the in-graph reminder
    #: injection guard reads this, never the raw enum, so the
    #: remind path is structurally exclusive to HOLD. Counter-
    #: INDEPENDENT: the HOLD injection does NOT increment
    #: ``attestation_denied_count`` and never engages the bound or
    #: escalation machinery. Capped at ``ATTESTATION_REMINDER_CAP``
    #: per mission (the graph node enforces the cap, falling
    #: through to plain ``meta_bypass`` allow on the cap).
    should_inject_reminder: bool = False
    #: 2026-09-19 — when ``should_inject_reminder`` is True, the
    #: reminder text to inject (``ATTESTATION_FINAL_REPORT_REMINDER``
    #: for the clean-call HOLD, ``ATTESTATION_BUNDLED_REMINDER``
    #: for the bundled-shape HOLD). ``None`` on every other path.
    reminder_text: str | None = None
    #: 2026-09-19 — True when the final AIMessage carried non-empty
    #: content AND an attest_completion tool_call (the c5d9a38a
    #: bundled shape). Drives the choice of reminder text. Logged
    #: alongside the decision row for forensics.
    is_bundled_call: bool = False
    #: 2026-09-19 — True when the final AIMessage is a standalone
    #: text report (no tool_calls, non-trivial length >=
    #: ``SHORT_REPORT_WORD_THRESHOLD``). The attested-allow path
    #: requires this to be True (the only path to ``Decision.
    #: ALLOWED`` when ``attestation_present=True``); the gate
    #: falls into ``Decision.HOLD`` otherwise. Logged alongside
    #: the decision row for forensics.
    final_ai_is_text_report: bool = False
    #: 2026-09-19 — True when the last AIMessage in the window IS
    #: the attest-call message itself (the AIMessage that carried
    #: the ``attest_completion`` tool_call). Drives the HOLD branch:
    #: the gate knows to remind the leader to deliver the report as
    #: a subsequent standalone AI message.
    final_ai_is_attest_call: bool = False
    #: 2026-09-19 — the in-window attestation scan's index of the
    #: AIMessage that carries the attest_completion tool_call
    #: (``-1`` when ``attestation_present=False``). The graph node
    #: uses this + the in-window ordering to determine whether the
    #: attest-call message is the LAST AIMessage in the tail (HOLD
    #: case) or a non-last AIMessage with subsequent text after it
    #: (allow case when the subsequent text is a standalone report).
    attestation_index: int = -1
    # Transient error marker: a fail-open evaluation remains visible in the
    # checkpoint and in the operator log after a crash/resume.
    gate_exception_seen: bool = False
    # ——— scanner diagnostics (canonical schema fields) ———
    scanner_window_truncated: bool = False
    scanner_summary_seen: bool = False
    attestation_present: bool = False
    messages_scanned: int = 0
    scanned_window_size: int = 0
    #: 2026-09-06 conditional-attestation flag (FR-3 conditionality) —
    #: ``True`` when the leader dispatched a child since the last real
    #: user message (the gate is then ON for this turn-end). ``False``
    #: means no delegation happened this mission — attestation is not
    #: required (the gate allows END without demanding
    #: ``attest_completion``). The 17th canonical log schema field.
    #: Defaults to False so a never-evaluated path never accidentally
    #: nudges on stale state.
    attestation_required: bool = False
    # ——— R2 inputs (canonical schema fields) ———
    pending_children: int = 0
    queued_or_expected_wakeups: int = 0
    # Live descendants — the third R2 input (2026-09-06, option-a
    # additive fix for the 809e2a59 waiting_children false-deny
    # incident class). Live = NOT IN {COMPLETED, TERMINATED, ERROR,
    # FAILED}. Always surfaces the count in the schema; 0 means
    # either no descendants or every descendant terminal.
    live_descendants: int = 0
    denied_count: int = 0
    # ——— Conditional-attestation scanner diagnostics (logged; NOT in the
    # canonical 17-field schema tuple). The integration layer surfaces
    # them in the format-string log line for dry-mode soak diagnostics
    # and the FR-3 conditionality audit. Last real user index is -1 when
    # no real user message exists (the gate's conservative fallback).
    #: Mirror of :attr:`DelegationScanResult.delegation_since_last_user`
    #: — a flat bool for tests + log lines that want the high-level
    #: "did this mission dispatch a child" verdict without indexing
    #: into the first_delegation_after_last_user_index sentinel
    #: (-1 vs >= 0). Logged alongside the index fields; like the other
    #: diagnostic fields, NOT in the canonical 17-field schema tuple.
    delegation_since_last_user: bool = False
    last_real_user_found: bool = False
    last_real_user_index: int = -1
    first_delegation_after_last_user_index: int = -1
    delegation_tool_call_total: int = 0
    # ——— Mid-work marker scanner diagnostics (2026-09-11, incident
    # b08f40fe; additive to the canonical schema tuple). Stage 3
    # (2026-09-17, resolver-unification R6): the scanners are
    # ACTIVATION-SIGNAL producers for the unified predicate — the
    # (a)/(b)/(c)/(d) route enum, the trigger-source derivation, and
    # the busy-suppression bookkeeping retired with the legacy judge
    # plumbing; these fields carry the raw scan results the predicate's
    #: ``b_fires`` term consumes.
    marker_hit: bool = False
    marker_terms: tuple[str, ...] = ()
    #: 2026-09-12 length trigger — True when the LAST AIMessage word
    #: count is strictly less than :data:`SHORT_REPORT_WORD_THRESHOLD`
    #: (i.e. brevity-class). Logged alongside the marker fields;
    #: additive to the canonical schema tuple.
    length_trigger: bool = False
    #: 2026-09-12 length trigger — word count of the flattened LAST
    #: AIMessage content. ``0`` on degenerate empty message lists or
    #: when the last AIMessage has empty content. Logged alongside the
    #: marker fields; additive to the canonical schema tuple.
    final_word_count: int = 0
    #: 2026-09-12 LCA busy count — the count of descendants in the
    #: unconditional-busy subset ``{RUNNING, WAITING, WAITING_CHILDREN}``
    #: ONLY (PAUSED is NOT busy — see
    #: ``InstanceManager.count_busy_descendants``). Stage 3 (R5): the
    #: busy-suppression SEMANTICS live in the predicate's ``b_fires``
    #: term (``(marker_hit ∨ length_trigger) ∧ busy_descendants == 0``
    #: — Source B is busy-muted, Source A deliberately is NOT, approved
    #: Δ2); this field carries the raw count for the predicate input
    #: and log forensics. Always surfaces the count; 0 means either no
    #: descendants or every descendant terminal/PAUSED/dormant.
    busy_descendants: int = 0
    #: 2026-09-16 answer-gate blindness fix (incident 6a0d60c9,
    #: FIX-2) — True when the leader has an OPEN awaiting-answer
    #: suspension handle at gate time (the FIFTH legitimate-pending
    #: input). Plain allow: no marker scan, no judge, no nudge, no
    #: hint, no counter movement. Logged as the 18th canonical
    #: schema field.
    user_answer_pending: bool = False
    #: 2026-09-16 LCA resolver Stage-2 flip — the unified resolver's
    #: evaluation snapshot (``ResolverEvalSnapshot``: activation
    #: result with the fused bundle, would-be outcome, agreement, row
    #: context), computed at the §(vi) seam of the canonical
    #: ``evaluate()`` path. ``None`` on the meta-bypass / off-mode
    #: early returns, the C-read DB-error fail-open branch, and every
    #: pre-Stage-2 construction site (additive default). The graph
    #: node's fused block consumes it as the AUTHORITATIVE completion
    #: outcome source (band + bundle → fused judge → outcome mapping)
    #: and emits the ``event=leader_completion_resolver_eval`` row
    #: from it. NOT part of any log schema (the row carries the
    #: fields); this field is control-flow plumbing only.
    resolver: "Any | None" = None


# ─────────────────────────────────────────────────────────────────────────────
# 2.2 — the pure decision function
# ─────────────────────────────────────────────────────────────────────────────


def deny_bound_exceeded(denied_count: int, bound: int) -> bool:
    """Shared deny_bound escalation predicate (2026-09-16, FIX-1).

    Incident 6a0d60c9: the deny bound was enforced ONLY on the
    ``decide()`` step-(6) path. The marker-path allow→deny conversions
    in ``daemon/graph.py`` (routes (a) and (d)) incremented the deny
    counter WITHOUT consulting the bound, so a leader whose every
    turn-end trips a mid-work marker could deny-nudge forever — 123
    gate evaluations / 115 deny+nudge injections over 27 min with the
    ``terminal_after_bound`` backstop never firing anywhere in the
    fleet log.

    This predicate is the SINGLE shared definition of "this deny is
    the one that trips the bound" — extracted verbatim from the
    former inline ``denied_count + 1 > bound`` check in ``decide()``
    step (6). ALL deny producers consult it:

      1. ``decide()`` step (6) — the canonical path (unchanged
         semantics, now via this helper);
      2. the fused block's belt-and-braces marker/A-band
         nothing-pending deny-flip in ``daemon/graph.py`` (Stage 3:
         the sole survivor of the legacy (a)/(d) conversions — the
         two legacy judge sites retired with R7);
      3. :func:`daemon.services.attestation_resolver_activation.
         compute_would_be_outcome` (the would-terminal reference
         mapping — imported, never re-implemented).

    When it returns True the caller MUST produce the canonical
    terminal outcome — mirroring ``decide()`` step (6) EXACTLY:
    ``Decision.TERMINAL_AFTER_BOUND`` with ``next_denied_count = 0``
    and ``should_inject_nudge = False`` (no nudge re-injection), so
    the existing terminal machinery in the gate node (ledger
    ``set_escalated_and_reset`` + the
    ``event=leader_completion_gate_terminal_after_bound`` operator
    event + plain allow END) runs unchanged.

    Args:
        denied_count: Current ``attestation_denied_count``.
        bound: Deny bound (D5, default 3).

    Returns:
        True when this deny would exceed the bound.
    """
    return denied_count + 1 > bound


def decide(
    attested: bool,
    pending_children: int,
    queued_or_expected_wakeups: int,
    live_descendants: int,
    denied_count: int,
    bound: int,
    *,
    attestation_required: bool,
    user_answer_pending: bool = False,
    final_ai_is_text_report: bool = False,
    final_ai_is_attest_call: bool = False,
    is_bundled_call: bool = False,
    reminder_text_clean: str = "",
    reminder_text_bundled: str = "",
) -> GateDecision:
    """Pure ENFORCE-tree decision over the R2 inputs (Stage 3 shape).

    Stage 3 (2026-09-17, resolver-unification R2/R3/R4): the
    meta-condition branches retired out of this function — the
    outermost activation terms now live in the unified predicate
    (``daemon.services.attestation_resolver_activation.activation_
    predicate``, Term 0 and Term 1) with :func:`evaluate` as the
    composition layer that mirrors them. ``decide()`` itself is the
    pure enforce decision tree:

    1. ``user_answer_pending`` (2026-09-16, incident 6a0d60c9, FIX-2)
       → :attr:`Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP` with the
       counter UNCHANGED and zero nudge/hint. The pending party is the
       USER; the leader cannot progress alone, so the awaiting answer
       is the FIFTH legitimate-pending input. Fires BEFORE every
       other branch deliberately: the plain-allow contract is ZERO
       counter movement — an attested-allow reset (trigger 1) does not
       run while an answer is pending.
    2. any pending wakeup input > 0 (THREE-input R2 —
       ``pending_children``, ``queued_or_expected_wakeups``, OR
       ``live_descendants``) →
       :attr:`Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP` with the
       counter UNCHANGED (ruling 1: the R2 non-reset IS the loop
       protection). Fires BEFORE the attested-split step (3)
       deliberately (2026-09-20, N1 ratification): an attest issued
       DURING an active mission is premature — the mission
       continues, the attest either stays in-window or falls out
       and re-attestation happens at true completion (the
       quiet-tree end state). The ``live_descendants`` arm closes
       the 809e2a59 waiting_children false-deny incident class:
       deferral fires the parent's watcher (so ``pending_children``
       drops to 0) while a deeper grandchild still runs, AND
       deferral emits no report/task row (so
       ``queued_or_expected_wakeups`` stays 0). NO reminder is
       injected on this branch — the leader is not done; the
       attest was premature.
    3. attested (and step (2) was quiet — no pending wakeups)
       → IF the final AIMessage is a standalone text report (no
       tool calls, non-trivial length >=
       ``SHORT_REPORT_WORD_THRESHOLD``) →
       :attr:`Decision.ALLOWED` with ``next_denied_count = 0``
       (reset trigger 1; the report-completion reset is bound to
       this path ONLY — the R2 allow in step (2) does NOT fire the
       counter reset on a premature-attest input). OTHERWISE
       (attested but final AIMessage is the attest-call message
       itself, OR the bundled shape where the AIMessage carried
       text + tool_call) → :attr:`Decision.HOLD` with
       ``next_denied_count = denied_count`` (counter UNCHANGED —
       HOLD is not a denial) and ``should_inject_reminder = True``.
       The reminder text is ``reminder_text_bundled`` when
       ``is_bundled_call`` else ``reminder_text_clean``. The
       2026-09-19 attest-first contract (closes incident
       c5d9a38a — prompt-only fixes failed twice, so system-side
       enforcement via HOLD now). HOLD exists ONLY for the
       quiet-tree end state: the tree must be QUIET (R2 inputs all
       zero) for this branch to fire.
    4. not attested + no pending wakeups +
       ``denied_count + 1 > bound`` → :attr:`Decision.TERMINAL_AFTER_BOUND`
       with ``next_denied_count = 0`` (reset trigger 2; the same reset
       clears the escalated flag per ruling 2 — persistence is Phase 3).
    5. otherwise → :attr:`Decision.DENIED` with
       ``next_denied_count = denied_count + 1`` and
       ``should_inject_nudge = True``.

    The retired branches (for the record — behavior preserved at the
    composition layer, see :func:`evaluate`): the meta-condition
    bypass (master flag / leader scope / off mode), the dry-mode
    mapping (DRY_LOG — now the mode layer in :func:`evaluate`), the
    unknown-mode fail-open, and the no-delegation arm (now the
    predicate's Term 1, mirrored by the composition layer's
    meta-bypass).

    Args:
        attested: Scanner verdict — attestation tool call in window.
        pending_children: R2 input from ``manager.count_pending_children``.
        queued_or_expected_wakeups: R2 input from
            ``manager.get_queued_or_expected_wakeups``.
        live_descendants: R2 third input from
            ``manager.count_live_descendants`` — count of WORK-BEARING
            descendants (two-set semantics, incident b08f40fe
            2026-09-11: RUNNING/WAITING/WAITING_CHILDREN/PAUSED
            unconditionally; dormant IDLE/QUEUED only with an
            unprocessed message row or an unsettled QUEUED/ACTIVE job;
            terminal excluded). Closes the watcher-fire-on-defer gap.
        denied_count: Current ``attestation_denied_count``.
        bound: Deny bound (D5, default 3).
        attestation_required: Conditional-attestation gate flag (keyword
            only — mandatory input from the wiring layer so an omitted
            kwarg is a loud failure). STAMP-ONLY at this layer (the
            17th canonical log schema field); the delegation gate
            itself is the predicate's Term 1.
        user_answer_pending: Keyword-only, default False (2026-09-16,
            incident 6a0d60c9 FIX-2). True when the leader has an OPEN
            awaiting-answer suspension handle at gate time (DB-backed:
            ``task.suspension_reason='awaiting_answer'`` +
            ``resume_target_turn_id IS NOT NULL`` + ``status='paused'``
            + freshness guard, read via
            ``InstanceManager.has_open_user_answer``). Arms the plain
            allow (step 1) with ZERO counter movement.
        final_ai_is_text_report: 2026-09-19 — True when the final
            AIMessage is a standalone text report (no tool calls,
            non-trivial length >= ``SHORT_REPORT_WORD_THRESHOLD``).
            The attested-allow path requires this True (step 2
            positive arm). Computed by the caller
            (:func:`evaluate`) from the messages list.
        final_ai_is_attest_call: 2026-09-19 — True when the LAST
            AIMessage in the window IS the attest-call message
            itself (carries an ``attest_completion`` tool_call).
            Drives the HOLD branch when attested but the final AI
            is not a text report. Computed by the caller
            (:func:`evaluate`).
        is_bundled_call: 2026-09-19 — True when the final AIMessage
            carried non-empty content AND an attest_completion
            tool_call (the c5d9a38a bundled shape). Drives the
            choice of reminder text on the HOLD path.
        reminder_text_clean: 2026-09-19 — the reminder text for the
            clean-call HOLD (final AI is the attest-call message
            with empty content, no subsequent text). The graph node
            injects this via :func:`_make_attestation_final_report_
            reminder_message`. Canonical home:
            ``daemon/graph.py:ATTESTATION_FINAL_REPORT_REMINDER``.
        reminder_text_bundled: 2026-09-19 — the reminder text for
            the bundled-shape HOLD (final AI carried text + tool
            call). Canonical home:
            ``daemon/graph.py:ATTESTATION_BUNDLED_REMINDER``.

    Returns:
        :class:`GateDecision` — the decision core.
    """
    # ——— enforce tree (Stage 3: meta terms live in the predicate) ———

    # (1) user answer pending — the FIFTH legitimate-pending input
    # (2026-09-16, incident 6a0d60c9, FIX-2 answer-gate blindness).
    # The leader has an OPEN awaiting-answer suspension handle: the
    # pending party is the USER and the leader cannot progress alone.
    # PLAIN ALLOW with the counter UNCHANGED (zero counter movement —
    # deliberately BEFORE every other branch so even an attested-allow
    # reset does not run while an answer is pending). The evaluate()
    # glue additionally skips the marker/length scan entirely on this
    # input (no scan, no judge, no nudge, no hint).
    if user_answer_pending:
        return GateDecision(
            decision=Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP,
            next_denied_count=denied_count,
            should_inject_nudge=False,
            attestation_required=attestation_required,
        )

    # (2) R2 allow — legitimate pending wakeup (THREE-input predicate:
    # pending_children OR queued_or_expected_wakeups OR live_descendants).
    # Fires BEFORE the attested-split step (3) deliberately
    # (2026-09-20, N1 ratification): a premature attest during an
    # active mission would otherwise fight the R2 legit-pending
    # allow — the mission continues, the attest either stays in
    # window or falls out and re-attestation happens at true
    # completion (the quiet-tree end state). Counter unchanged
    # (ruling 1: the R2 non-reset IS the loop protection). The
    # attested counter-reset (trigger 1) is NOT bound to this path;
    # the report-completion reset is reserved for the attested +
    # standalone-text-report ALLOWED path at step (3). NO reminder
    # is injected on this branch — the leader is not done; the
    # attest was premature. The ``live_descendants`` arm closes
    # the 809e2a59 waiting_children false-deny incident class:
    # deferral fires the parent's watcher (so ``pending_children``
    # drops to 0) while a deeper grandchild still runs, AND
    # deferral emits no report/task row (so
    # ``queued_or_expected_wakeups`` stays 0).
    if (
        pending_children > 0
        or queued_or_expected_wakeups > 0
        or live_descendants > 0
    ):
        return GateDecision(
            decision=Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP,
            next_denied_count=denied_count,
            should_inject_nudge=False,
            should_inject_reminder=False,  # premature-attest: no reminder (the leader is not done)
            attestation_required=attestation_required,
        )

    # (3) attested — the 2026-09-19 attest-first contract split. By
    # construction this branch is ONLY reachable with quiet R2 inputs
    # (no pending wakeups — step (2) caught them). IF the final
    # AIMessage is a standalone text report (no tool calls,
    # non-trivial length >= SHORT_REPORT_WORD_THRESHOLD) → plain
    # ALLOWED with counter reset (trigger 1; the
    # report-completion reset). OTHERWISE → HOLD with a
    # counter-INDEPENDENT reminder injection. Counter does NOT
    # increment; the bound/escalation machinery is NEVER touched
    # on HOLD. The graph node enforces the per-mission reminder
    # cap (ATTESTATION_REMINDER_CAP) and falls through to plain
    # meta_bypass allow on the cap. HOLD exists ONLY for the
    # quiet-tree end state (the R2 inputs were all zero at step
    # (2)).
    if attested:
        if final_ai_is_text_report:
            return GateDecision(
                decision=Decision.ALLOWED,
                next_denied_count=0,
                should_inject_nudge=False,
                attestation_required=attestation_required,
                final_ai_is_text_report=True,
                final_ai_is_attest_call=final_ai_is_attest_call,
                is_bundled_call=is_bundled_call,
            )
        # Attested but the final AIMessage is NOT a standalone text
        # report — HOLD. The reminder text depends on whether the
        # final AIMessage was the bundled shape (text + tool_call
        # in ONE message, c5d9a38a) or the clean attest-call
        # message with no subsequent text yet.
        chosen_reminder = (
            reminder_text_bundled if is_bundled_call else reminder_text_clean
        )
        return GateDecision(
            decision=Decision.HOLD,
            next_denied_count=denied_count,  # UNCHANGED — HOLD is not a denial
            should_inject_nudge=False,
            should_inject_reminder=bool(chosen_reminder),
            reminder_text=chosen_reminder or None,
            attestation_required=attestation_required,
            final_ai_is_text_report=False,
            final_ai_is_attest_call=final_ai_is_attest_call,
            is_bundled_call=is_bundled_call,
        )

    # (4) bound exceeded — escalation; counter reset (trigger 2).
    # FIX-1 (2026-09-16, incident 6a0d60c9): the bound predicate is
    # the SHARED helper ``deny_bound_exceeded`` — the fused path's
    # allow→deny conversion in graph.py consults the SAME predicate so
    # the bound backstop can never be bypassed.
    if deny_bound_exceeded(denied_count, bound):
        return GateDecision(
            decision=Decision.TERMINAL_AFTER_BOUND,
            next_denied_count=0,
            should_inject_nudge=False,
            attestation_required=attestation_required,
        )

    # (5) deny — increment + nudge.
    return GateDecision(
        decision=Decision.DENIED,
        next_denied_count=denied_count + 1,
        should_inject_nudge=True,
        attestation_required=attestation_required,
    )


def classify_final_ai_shape(
    messages: list[BaseMessage],
    window: int,
    attested: bool,
) -> tuple[bool, bool, bool, int]:
    """Classify the FINAL AIMessage in the (bounded) message tail.

    2026-09-19 (attest-first contract, c5d9a38a remediation): the
    attested-allow path requires the final AIMessage to be a
    standalone text report (no tool calls, non-trivial length >=
    ``SHORT_REPORT_WORD_THRESHOLD``). This helper returns the four
    facts the gate's HOLD branch needs:

    * ``final_ai_is_text_report`` — the final AIMessage has no
      ``attest_completion`` (and no other) tool calls AND its
      flattened content word count is >=
      ``SHORT_REPORT_WORD_THRESHOLD``. The attestation must be in
      the window (the last-3 AIMessages) but the report itself
      must NOT carry tool calls.
    * ``final_ai_is_attest_call`` — the LAST AIMessage in the tail
      IS the AIMessage that carries the ``attest_completion``
      tool_call (regardless of whether it has content). The gate
      uses this to drive the HOLD reminder shape.
    * ``is_bundled_call`` — the LAST AIMessage carries an
      ``attest_completion`` tool_call AND has non-empty content
      (the c5d9a38a bundled shape — report + attest in ONE
      message). Drives the choice of reminder text on the HOLD
      path.
    * ``attestation_index`` — the index of the AIMessage that
      carries the ``attest_completion`` tool_call within the
      bounded window (``-1`` when ``attested=False``). Used by the
      gate for forensics + log schema.

    Pure function; no I/O. Walks the messages tail backward up to
    ``window`` AIMessages (mirroring the existing scanners'
    bounded-walk semantics). The "final AIMessage" is the newest
    AIMessage in the tail — the same shape the length scanner's
    ``_flatten_ai_content`` sees. List-of-blocks content
    (LangChain text + reasoning blocks) is flattened inline so
    the helper is self-contained.

    Args:
        messages: The in-node message list (``state["messages"]``).
        window: How many tail AIMessages to inspect when locating
            the attestation call. Values ``< 1`` are clamped to 1.
        attested: Scanner verdict — whether ``attest_completion``
            appears in the bounded window. When ``False``, the
            helper short-circuits with ``(False, False, False, -1)``.

    Returns:
        Tuple ``(final_ai_is_text_report, final_ai_is_attest_call,
        is_bundled_call, attestation_index)`` — all four fields the
        gate's HOLD branch reads.
    """
    bounded_window = max(1, int(window) if window is not None else 1)
    if not attested:
        return (False, False, False, -1)

    # Walk backward through the bounded tail. Find:
    #   1. the LAST AIMessage in the tail (the "final AI");
    #   2. the index of the AIMessage that carries the attestation
    #      tool_call.
    final_ai: AIMessage | None = None
    final_ai_index: int = -1
    attest_ai: AIMessage | None = None
    attest_ai_index: int = -1
    ai_count = 0
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if not isinstance(message, AIMessage):
            continue
        if final_ai is None:
            final_ai = message
            final_ai_index = index
        ai_count += 1
        if attest_ai is None and _has_attest_completion_call(message):
            attest_ai = message
            attest_ai_index = index
        if ai_count >= bounded_window and attest_ai is not None and final_ai is not None:
            break

    if attest_ai is None:
        # ``attested`` was True at the call site but we couldn't
        # locate the AIMessage that carries the attestation call
        # in the bounded walk — degenerate state (e.g. the scanner
        # saw a stale cross-window call and the messages list has
        # compacted since). Be conservative: treat as
        # NOT-text-report (HOLD on the attested path).
        return (False, False, False, -1)

    # ``final_ai_is_attest_call``: the LAST AIMessage in the tail
    # IS the attestation-carrying message. When the last AI is a
    # DIFFERENT message than the attestation-carrying one, the
    # leader has already delivered a subsequent standalone AI
    # message after the attest call (the "happy path").
    final_ai_is_attest_call = (
        final_ai is not None and final_ai_index == attest_ai_index
    )

    # ``is_bundled_call``: the LAST AIMessage carries the
    # attestation tool_call AND has non-empty flattened content.
    is_bundled = False
    if final_ai_is_attest_call and final_ai is not None:
        flat = _flatten_for_classify(getattr(final_ai, "content", ""))
        is_bundled = bool(flat.strip())

    # ``final_ai_is_text_report``: the LAST AIMessage has no
    # tool calls at all AND its flattened word count is >=
    # SHORT_REPORT_WORD_THRESHOLD. The attestation-carrying AI
    # itself (which carries tool calls) NEVER satisfies this
    # even when its content is long.
    final_ai_is_text_report = False
    if (
        final_ai is not None
        and not _has_attest_completion_call(final_ai)
        and not (getattr(final_ai, "tool_calls", None) or [])
    ):
        flat = _flatten_for_classify(getattr(final_ai, "content", ""))
        word_count = len(flat.split()) if flat else 0
        final_ai_is_text_report = word_count >= _CLASSIFY_REPORT_WORD_THRESHOLD

    return (
        final_ai_is_text_report,
        final_ai_is_attest_call,
        is_bundled,
        attest_ai_index,
    )


#: The word-count threshold for ``final_ai_is_text_report`` (2026-09-19).
#: Mirrors :data:`daemon.services.attestation_marker_scanner.
#: SHORT_REPORT_WORD_THRESHOLD` — the SAME 150-word threshold the
#: gate's length trigger uses. Aliased here (not imported) so this
#: helper stays dependency-light on the hot classify path.
_CLASSIFY_REPORT_WORD_THRESHOLD: int = 150


def _flatten_for_classify(content: object) -> str:
    """Flatten an AIMessage's content into a plain string (classify-only).

    Mirrors :func:`daemon.services.attestation_marker_scanner._flatten_ai_content`.
    Kept inline to avoid the cross-module import on the hot
    classify path; if the canonical helper ever changes, this
    mirror must be updated in lockstep (pinned by
    :data:`_CLASSIFY_REPORT_WORD_THRESHOLD`).
    """
    if isinstance(content, list):
        flat_parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                flat_parts.append(str(block.get("text", "")))
            else:
                flat_parts.append(str(block))
        return " ".join(flat_parts)
    return str(content) if content else ""


def _has_attest_completion_call(message: BaseMessage) -> bool:
    """True iff ``message`` carries an ``attest_completion`` tool call."""
    for tool_call in getattr(message, "tool_calls", None) or []:
        if isinstance(tool_call, dict) and tool_call.get("name") == "attest_completion":
            return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Gate config shape (O8 unit-assertion surface)
# ─────────────────────────────────────────────────────────────────────────────

#: Keys that MAY appear in the gate config dict. The O8 unit guard
#: asserts the ACTUAL produced config never carries ``checkpoint_ns``:
#: the in-node seam must not thread LangGraph checkpoint namespaces into
#: the scanner config (the namespace-mismatched-empty-state defect class).
GATE_CONFIG_KEYS = (
    "instance_id",
    "tool_name",
    "window",
    "deny_bound",
    "mode",
    "leader_prompt_version",
    "gate_location",
    # Phase 6 fastfollow (2026-09-07): inline-LLM completion-report
    # judge kill-switch boolean. Wired through ``build_gate_config``
    # so the gate node can read it via ``gate_config.get(
    # "llm_judge_enabled", True)``. Default ``True`` on read — back-compat
    # with test embeddings built without the new key.
    "llm_judge_enabled",
)

#: Canonical ``gate_location`` value (Phase 4 task 4.5 schema).
GATE_LOCATION_GRAPH_END_CANDIDATE = "graph_end_candidate"

#: Canonical log schema field set (Phase 4 task 4.5 — single source of truth).
#:
#: Every ``event=leader_completion_gate`` log line emitted by
#: :func:`evaluate` MUST carry every field below — the
#: ``tests/integration/test_attestation_runbook_drift.py`` drift
#: assertion and the Phase 5 matrix inspect the literal log line, so
#: a missing key trips the audit. The schema is the SINGLE shared
#: definition — Phase 2/3/4/5 tasks reference this tuple, not a
#: paraphrase. Fields in display order (matches the format string in
#: :func:`evaluate`).
#:
#: Stage 3 (2026-09-17, resolver-unification R1): the outside-window
#: stale-attestation diagnostic field retired — it was a
#: log-only surface with no decision weight anywhere (the gate
#: consumes only the in-window attestation scan result). The schema
#: drops from 18 to 17 canonical fields.
CANONICAL_LOG_SCHEMA_FIELDS: tuple[str, ...] = (
    "event",
    "decision",
    "instance_id",
    "attestation_present",
    "denied_count",
    "gate_location",
    "leader_prompt_version",
    "pending_children",
    "queued_or_expected_wakeups",
    "live_descendants",  # 2026-09-06 third R2 input (option-a fix)
    "attestation_required",  # 2026-09-06 conditional-attestation flag
    "messages_scanned",
    "scanned_window_size",
    "mode",
    "scanner_window_truncated",
    "scanner_summary_seen",
    # 2026-09-16 answer-gate blindness fix (incident 6a0d60c9, FIX-2)
    # — the FIFTH legitimate-pending input; canonical field.
    "user_answer_pending",
)


def build_gate_config(
    instance_id: str | None,
    settings: GateSettings,
    *,
    tool_name: str = DEFAULT_ATTESTATION_TOOL_NAME,
    leader_prompt_version: str = "",
    llm_judge_enabled: bool = True,
) -> dict[str, Any]:
    """Build the gate's config dict (the O8-audited shape).

    Deliberately contains NO ``checkpoint_ns`` (and no other LangGraph
    checkpoint namespace material): the gate reads in-node
    ``state["messages"]`` only. ``tests/unit/test_attestation_gate.py``
    pins this via :data:`GATE_CONFIG_KEYS` and a direct key assertion.

    Stage 3 (2026-09-17, resolver-unification R2): the
    ``attestation_enabled`` / ``scope_applicable`` keys retired — the
    C2 master flag and the D3 leader-scope check are enforced at
    GRAPH-BUILD time (``build_instance_graph`` wires the gate node
    only for leaders with the feature master on; the wrapper is not
    even applied otherwise) and re-encoded as the predicate's Term 0.
    Carrying them inside the gate config duplicated that decision one
    layer down for no behavioral effect (production values were always
    ``True``).

    Args:
        instance_id: The leader instance id (for log fields).
        settings: :class:`GateSettings` (window/bound/mode).
        tool_name: Attestation tool name.
        leader_prompt_version: ``agents/leader/meta.json`` version for
            the canonical log schema.
        llm_judge_enabled: Phase 6 fastfollow (2026-09-07) — whether
            the inline-LLM completion-report judge runs on the
            would-be-deny path. Pattern C kill-switch
            (``ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED``,
            default ON). The wiring layer resolves the env flag and
            threads the boolean through; the gate node reads
            ``gate_config["llm_judge_enabled"]`` (default ``True`` for
            back-compat with legacy test embeddings that build the
            node without the new flag).
    """
    return {
        "instance_id": instance_id,
        "tool_name": tool_name,
        "window": settings.window,
        "deny_bound": settings.deny_bound,
        "mode": settings.mode,
        "leader_prompt_version": leader_prompt_version,
        "gate_location": GATE_LOCATION_GRAPH_END_CANDIDATE,
        "llm_judge_enabled": llm_judge_enabled,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 2.3 — the composition glue
# ─────────────────────────────────────────────────────────────────────────────


def evaluate(
    instance_id: str | None,
    denied_count: int,
    messages: list[BaseMessage],
    mode_resolver: GateSettings,
    manager: Any,
    *,
    attestation_enabled: bool = True,
    scope_applicable: bool = True,
    tool_name: str = DEFAULT_ATTESTATION_TOOL_NAME,
    leader_prompt_version: str = "",
    gate_location: str = GATE_LOCATION_GRAPH_END_CANDIDATE,
    ledger: Any = None,
) -> GateDecision:
    """Glue: scanner → R2 facade reads → decide → canonical log entry.

    Read sequence (CR-2 TOCTOU contract, mandatory ordering):
    ``messages → pending_children → queued_or_expected_wakeups →
    live_descendants``. The in-node ``messages`` list is the caller's
    LangGraph state argument — evaluated first, synchronously, before
    any manager facade call.

    **TOCTOU race contract (task 2.3.1, CR-2):** a leader that dispatches
    a child and ENDs in the same cycle is protected because watcher
    registration (``daemon/tools/instance.py``, ``_register_child_
    completion_watcher``) runs POST-COMMIT in its own WriteGuardSession
    transaction that commits BEFORE the dispatch tool result returns to
    the LLM — hence before the leader's next AIMessage and before this
    gate's ``pending_children`` read. There is NO same-txn-with-spawn
    atomicity (the completion-gate deferral reads in ``child_reports``
    are NOT watcher registration and must never be cited as such); the
    PENDING row is visible to this reader by sequencing, not by shared
    transaction. Residual windows (crash between tool completion and
    gate read; silent registration failure) are accepted + documented
    in the phase plan and covered by Phase 5 task 5.7's DB read-back
    guard — NOT closed here.

    Args:
        instance_id: The leader instance under evaluation.
        denied_count: Current ``attestation_denied_count`` (Phase 2:
            the wiring passes 0; Phase 3 threads the ledger getter).
        messages: In-node ``state["messages"]`` — NEVER an
            ``aget_state`` result (see module docstring).
        mode_resolver: :class:`GateSettings` (window/bound/mode).
        manager: Handle exposing the THREE NEW facades
            (``count_pending_children`` / ``get_queued_or_expected_
            wakeups`` / ``count_live_descendants``). May be ``None``
            ONLY in degenerate embeddings — the response is fail-open
            allow (R2 inputs unreadable).
        attestation_enabled: C2 flag — Term-0 input to the unified
            activation predicate (mirrored by the early return below;
            enforced primarily at graph-build time — the gate node is
            only wired when the master flag is on).
        scope_applicable: D3 flag — Term-0 input to the unified
            activation predicate (same mirroring; leader-scope is
            enforced primarily at graph-build time).
        tool_name: Attestation tool name.
        leader_prompt_version: ``agents/leader/meta.json`` version for
            the canonical log schema.
        gate_location: Canonical log field (default
            ``graph_end_candidate``).

    Returns:
        :class:`GateDecision` — log-ready (scanner diagnostics + R2
        inputs attached). On ANY failure: fail-open allow with the
        error logged (``leader_completion_gate_db_error`` for facade
        read failures with ``pending_children``/``queued_or_expected_
        wakeups`` reported as ``-1`` = "unknown due to error";
        ``leader_completion_gate_error`` for scanner/decide failures).
    """
    meta = {
        "instance_id": instance_id,
        "gate_location": gate_location,
        "leader_prompt_version": leader_prompt_version,
        "mode": mode_resolver.mode,
    }

    # A meta-condition bypass is not a gate evaluation.  In particular,
    # off-mode must not even enter the scanner/facade path or emit a
    # decision entry; this is the byte-equivalent OFF baseline. Stage 3
    # (R2): this early return is the composition-layer MIRROR of the
    # unified predicate's Term 0 (``¬attestation_enabled ∨
    # ¬scope_applicable ∨ mode="off"``) — decide() no longer carries
    # its own copy of the check, and production callers never reach it
    # with a False value (graph-build-time enforcement wires the gate
    # only for in-scope leaders with the master flag on).
    if not attestation_enabled or not scope_applicable or mode_resolver.mode == "off":
        return GateDecision(
            decision=Decision.ALLOWED,
            next_denied_count=denied_count,
            should_inject_nudge=False,
            scanned_window_size=mode_resolver.window,
            denied_count=denied_count,
        )

    # Fail-open for degenerate embeddings: without a manager handle the
    # R2 inputs are unreadable. Allow + note (never raise).
    if manager is None:
        logger.warning(
            "event=leader_completion_gate_error error_class=ManagerUnavailable "
            "instance_id=%s gate_location=%s decision=fail_open_allowed "
            "detail=no manager handle; R2 inputs unreadable",
            instance_id,
            gate_location,
        )
        return GateDecision(
            decision=Decision.ALLOWED,
            next_denied_count=denied_count,
            should_inject_nudge=False,
            messages_scanned=0,
            scanned_window_size=mode_resolver.window,
            denied_count=denied_count,
        )

    try:
        # (i) scanner — bounded in-window scan (AC-2.5).
        scan = scan_for_attestation_detailed(messages, mode_resolver.window, tool_name)

        # 2026-09-19 (attest-first contract, c5d9a38a remediation) —
        # classify the FINAL AIMessage shape so the attested-allow
        # path can require a standalone text report (no tool calls,
        # non-trivial length). Runs BEFORE the conditional /
        # not-required bypasses because the bypasses return ALLOWED
        # with `attestation_present=False` (the gate is OFF for those
        # missions — the classify output is moot on those paths).
        # Always computed when ``scan.attested=True``; cheap when
        # attested=False (short-circuits to all-False / -1).
        (
            final_ai_is_text_report,
            final_ai_is_attest_call,
            is_bundled_call,
            attestation_index,
        ) = classify_final_ai_shape(
            messages, mode_resolver.window, scan.attested
        )

        # (v.b) Conditional-attestation scanner (2026-09-06, FR-3
        # conditionality) — pure walk of the messages list that
        # answers: was ``send_message`` called since the last REAL user
        # message? The delegation scan is INDEPENDENT of the bounded
        # in-window attestation scan above: it walks the FULL tail at-
        # or-after the last real user message (no N-window bound),
        # because the delegation check answers "did this mission
        # dispatch children", not "did the LLM attest recently".
        delegation_scan = scan_delegation_after_last_user(messages)

        # Conditional gate ON iff delegation happened since the last
        # real user message. When no real user message exists at all
        # (degenerate state — should never happen for a live leader),
        # the scan reports ``last_real_user_found=False``; we keep
        # the conservative semantics of "if ANY delegation seen
        # anywhere in the tail, require attestation" by inheriting
        # the ``delegation_since_last_user`` boolean (which becomes
        # True on the first send_message anywhere in the whole list
        # when no real user message anchors the tail — the tail_start
        # fallback is 0).
        attestation_required = delegation_scan.delegation_since_last_user

        # (ii) R2 inputs — the THREE NEW manager facades (SYNC reads; the
        # graph node bridges this whole function via asyncio.to_thread).
        # DB seam: `except Exception` (KeyboardInterrupt stays fail-closed).
        # ``busy_descendants`` (2026-09-12) is the FOURTH input — the
        # trigger-suppression signal sourced from the SAME BFS as
        # ``live_descendants`` (shared helper
        # ``InstanceManager._count_descendants_busy_and_live`` — one
        # BFS pass per method invocation).
        try:
            pending_children = manager.count_pending_children(instance_id)
            queued_or_expected_wakeups = manager.get_queued_or_expected_wakeups(
                instance_id
            )
            live_descendants = manager.count_live_descendants(instance_id)
            busy_descendants = manager.count_busy_descendants(instance_id)
            # FIX-2 (2026-09-16, incident 6a0d60c9) — the FIFTH
            # legitimate-pending input: an OPEN awaiting-answer
            # suspension handle. Read via the manager facade (same DB
            # seam + fail-open contract as the sibling R2 reads; the
            # facade mirrors ``count_busy_descendants`` — errors
            # propagate into the ``except`` below and the whole
            # evaluation fail-open-allows with the -1 sentinels).
            #
            # Duck-typing guard: the value arms the plain-allow bypass
            # ONLY when it is the literal ``True``. A MagicMock /
            # test-double return (truthy-but-not-True) reads as False
            # so the deny path stays reachable — a missing or mocked
            # facade can NEVER false-positive into a permanent allow
            # bypass (the new silent-completion hole this fix must
            # not open). A facade that is absent entirely reads as
            # False too.
            _answer_reader = getattr(manager, "has_open_user_answer", None)
            user_answer_pending = (
                _answer_reader(instance_id) is True
                if callable(_answer_reader)
                else False
            )
        except Exception as db_exc:  # noqa: BLE001 — fail-open at the DB seam (whole-eval C-read failure ⇒ plain allow; the predicate's §4.2 C-read branch mirrors this)
            error_class = type(db_exc).__name__
            logger.error(
                "event=leader_completion_gate_db_error "
                "error_class=%s "
                "instance_id=%s "
                "gate_location=%s "
                "mode=%s "
                "leader_prompt_version=%s "
                "attestation_present=%s "
                "denied_count=%s "
                "pending_children=-1 "
                "queued_or_expected_wakeups=-1 "
                "live_descendants=-1 "
                "busy_descendants=-1 "
                "messages_scanned=%s "
                "scanned_window_size=%s "
                "scanner_window_truncated=%s "
                "scanner_summary_seen=%s "
                "decision=fail_open_allowed "
                "detail=%s: %s",
                error_class,
                instance_id,
                gate_location,
                mode_resolver.mode,
                leader_prompt_version,
                scan.attested,
                denied_count,
                scan.messages_scanned,
                mode_resolver.window,
                scan.window_truncated,
                scan.summary_seen,
                error_class,
                db_exc,
            )
            # Stage-1 shadow fail-open row (additive): the gate's OWN
            # C facade reads failed — the whole evaluation is
            # fail-open plain-allow (the return below, untouched).
            # The unified predicate's §4.2 C-read-failure branch
            # carries the SAME semantics; this row records it
            # (fail_open=True / would_be_outcome=would_allow) so the
            # dry soak sees every evaluation class. Exception-isolated
            # like the canonical seam.
            try:
                from .attestation_resolver_activation import (
                    log_shadow_fail_open,
                )

                log_shadow_fail_open(
                    instance_id=instance_id,
                    gate_location=gate_location,
                    leader_prompt_version=leader_prompt_version,
                    mode=mode_resolver.mode,
                    error_class=error_class,
                    old_decision=Decision.ALLOWED,
                )
            except Exception as shadow_exc:  # noqa: BLE001 — never load-bearing
                logger.error(
                    "event=leader_completion_resolver_eval_error "
                    "error_class=%s instance_id=%s gate_location=%s "
                    "mode=%s detail=fail_open_row: %s",
                    type(shadow_exc).__name__,
                    instance_id,
                    gate_location,
                    mode_resolver.mode,
                    shadow_exc,
                )
            return GateDecision(
                decision=Decision.ALLOWED,
                next_denied_count=denied_count,
                should_inject_nudge=False,
                scanner_window_truncated=scan.window_truncated,
                scanner_summary_seen=scan.summary_seen,
                attestation_present=scan.attested,
                messages_scanned=scan.messages_scanned,
                scanned_window_size=mode_resolver.window,
                pending_children=-1,
                queued_or_expected_wakeups=-1,
                live_descendants=-1,
                busy_descendants=-1,
                # FIX-2: DB-seam failure reads as NOT pending — the
                # allow here is the pre-existing whole-eval fail-open,
                # not an answer-pending bypass.
                user_answer_pending=False,
                denied_count=denied_count,
                gate_exception_seen=True,
            )

        # (iii) the decision — Stage 3 composition (2026-09-17,
        # resolver-unification R2/R3/R4). decide() is the PURE enforce
        # tree; the retired meta branches live HERE, at the layer that
        # owns the mode resolver and mirrors the unified predicate's
        # outermost terms:
        #   * unknown mode (typo'd resolver value) → fail OPEN
        #     (the resolver already WARNed once at boot);
        #   * ¬attestation_required (predicate Term 1, the R4 fold):
        #     no delegation since the last real user message ⇒ the
        #     gate is OFF for this turn-end — plain ALLOW, counter
        #     untouched (a non-fire is not one of the four reset
        #     triggers per ruling 1), no nudge. The D10 mirror: the
        #     marker/length scan below is SKIPPED on this branch
        #     (suspicion signals are not even evaluated on
        #     non-delegated missions — same exclusion the predicate's
        #     Term-1 short-circuit applies to its A/B providers);
        #   * dry mode (the R3 fold): the enforce-tree decision is
        #     computed with FULL diagnostics, then mapped to DRY_LOG
        #     with the counter frozen at its input value — zero side
        #     effects, same observable contract and log-row shape as
        #     the retired decide()-level branch.
        if mode_resolver.mode not in ("dry", "enforce"):
            # Unknown mode (off is caught by the early return above) —
            # fail OPEN (resolver already WARNed).
            result = GateDecision(
                decision=Decision.ALLOWED,
                next_denied_count=denied_count,
                should_inject_nudge=False,
                attestation_required=attestation_required,
            )
        elif not attestation_required:
            # Delegation gate OFF — predicate Term 1 mirror (FR-3
            # conditionality, 2026-09-06; R4 fold 2026-09-17).
            result = GateDecision(
                decision=Decision.ALLOWED,
                next_denied_count=denied_count,
                should_inject_nudge=False,
                attestation_required=False,
            )
        else:
            # Lazy import: the reminder-text constants live in
            # ``daemon/graph.py`` (the canonical home — NFR-6 parity
            # with ``ATTESTATION_NUDGE_TEXT``). We pass the strings
            # through to ``decide()`` rather than reading the constants
            # here to keep ``attestation_gate.py`` dependency-light
            # (graph.py is the runtime seam that already owns the
            # reminder-text body).
            from daemon.graph import (
                ATTESTATION_BUNDLED_REMINDER,
                ATTESTATION_FINAL_REPORT_REMINDER,
            )

            result = decide(
                attested=scan.attested,
                pending_children=pending_children,
                queued_or_expected_wakeups=queued_or_expected_wakeups,
                live_descendants=live_descendants,
                denied_count=denied_count,
                bound=mode_resolver.deny_bound,
                attestation_required=attestation_required,
                user_answer_pending=user_answer_pending,
                final_ai_is_text_report=final_ai_is_text_report,
                final_ai_is_attest_call=final_ai_is_attest_call,
                is_bundled_call=is_bundled_call,
                reminder_text_clean=ATTESTATION_FINAL_REPORT_REMINDER,
                reminder_text_bundled=ATTESTATION_BUNDLED_REMINDER,
            )
        if mode_resolver.mode == "dry":
            # R3 fold: dry is a property of the resolver/mode layer —
            # predicate + decision computed & logged, node skipped,
            # zero effects. The counter is frozen at its input value
            # and the nudge disarmed; every other diagnostic field
            # (delegation scan, would-be outcome inputs) stays intact
            # for the dry-mode soak.
            # 2026-09-19 (attest-first contract): the HOLD-state
            # reminder is also disarmed here — dry mode is a
            # passive observer (D2/D8), it never injects any
            # side-effect (no nudge, no hint, no reminder). The
            # decision is computed (so the dry-log row carries the
            # full forensic surface — including the would-be
            # reminder text) but the injection flag is forced
            # off so the graph node's HOLD branch never fires in
            # dry mode.
            result = replace(
                result,
                decision=Decision.DRY_LOG,
                next_denied_count=denied_count,
                should_inject_nudge=False,
                should_inject_reminder=False,
            )

        # Attach diagnostics + R2 inputs → log-ready object.
        # ``busy_descendants`` is stamped on EVERY evaluation — the
        # predicate's ``b_fires`` term consumes it (R5: Source B is
        # busy-muted; Source A deliberately is NOT — approved Δ2) and
        # the log row carries the count for forensics.
        # The 2026-09-19 HOLD-state fields (``final_ai_is_text_report``,
        # ``final_ai_is_attest_call``, ``is_bundled_call``,
        # ``attestation_index``) are also stamped here — they're
        # forensic-only (NOT in the canonical 17-field schema) but the
        # graph node reads them to drive the reminder injection
        # (post-evaluate path).
        result = replace(
            result,
            scanner_window_truncated=scan.window_truncated,
            scanner_summary_seen=scan.summary_seen,
            attestation_present=scan.attested,
            messages_scanned=scan.messages_scanned,
            scanned_window_size=mode_resolver.window,
            pending_children=pending_children,
            queued_or_expected_wakeups=queued_or_expected_wakeups,
            live_descendants=live_descendants,
            busy_descendants=busy_descendants,
            user_answer_pending=user_answer_pending,
            denied_count=denied_count,
            delegation_since_last_user=delegation_scan.delegation_since_last_user,
            last_real_user_found=delegation_scan.last_real_user_found,
            last_real_user_index=delegation_scan.last_real_user_index,
            first_delegation_after_last_user_index=delegation_scan.first_delegation_after_last_user_index,
            delegation_tool_call_total=delegation_scan.delegation_tool_call_total,
            final_ai_is_text_report=final_ai_is_text_report,
            final_ai_is_attest_call=final_ai_is_attest_call,
            is_bundled_call=is_bundled_call,
            attestation_index=attestation_index,
        )

        # (iii.b) Source-B activation-signal scans (2026-09-11,
        # incident b08f40fe; Stage-3 R6 shape). The marker scan and the
        # length trigger are ACTIVATION-SIGNAL producers for the
        # unified predicate's ``b_fires`` term — the legacy
        # trigger→judge plumbing retired with R6/R7 (the fused judge
        # owns the verdict; the predicate owns the busy-muting).
        # The scans run ONLY when the decision is in the allow family
        # (NOT on DENIED — the deny band fires on the C term alone and
        # leader-prose markers are moot there) AND the allow is NOT an
        # attested contract (attested_allow is an explicit success
        # path) AND no answer is pending (FIX-2: the awaiting answer is
        # the whole turn's purpose — no suspicion work) AND the
        # delegation gate is ON.
        #
        # Stage-3 D10 mirror (R4 guard): when
        # ``attestation_required=False`` (no delegation since the last
        # real user message), the scans are SKIPPED — suspicion
        # signals are not even evaluated on non-delegated missions,
        # mirroring the predicate's Term-1 short-circuit of its A/B
        # providers (pinned by the Stage-3 census + marker-wiring
        # tests).
        #
        # Cost control (decision tree e): NEITHER signal fires ⇒ the
        # predicate's ``b_fires`` term is False ⇒ NO judge call (the
        # judge is best-effort + costly; the cheap scans are the
        # gate). NO judge call ⇒ no behavioral change vs baseline.
        if (
            result.decision
            in (
                Decision.ALLOWED,
                Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP,
                Decision.DRY_LOG,
            )
            and not result.attestation_present
            # FIX-2 (2026-09-16, incident 6a0d60c9) — answer-gate
            # blindness: an OPEN awaiting-answer handle means the
            # pending party is the USER. PLAIN ALLOW before ANY
            # scan/judge work: NO marker scan, NO length trigger,
            # NO judge, NO nudge, NO hint, NO counter movement. The
            # awaiting answer is the whole turn's purpose.
            and not result.user_answer_pending
            # D10 mirror (R4): the delegation gate is the predicate's
            # outermost term — non-delegated missions skip suspicion
            # evaluation entirely.
            and result.attestation_required
        ):
            # (iii.c.1) — Mid-work marker scan (2026-09-11, incident
            # b08f40fe): cheap substring match over the tail
            # AIMessages.
            #
            # (iii.c.2) — Length trigger (2026-09-12, user request):
            # word-count signal on the LAST AIMessage; orthogonal to
            # the marker scan (markers catch phrasing, length catches
            # brevity). The predicate composes the two halves via OR
            # inside ``b_fires`` — either signal arming (with
            # busy_descendants == 0) fires the marker band.
            marker_result = scan_for_mid_work_markers(
                messages, mode_resolver.window
            )
            length_result = scan_for_short_final_ai(
                messages, mode_resolver.window
            )
            result = replace(
                result,
                marker_hit=marker_result.marker_hit,
                marker_terms=marker_result.marker_terms,
                length_trigger=length_result.length_trigger,
                final_word_count=length_result.final_word_count,
            )


        # (iv) canonical structured log entry (Phase 4 task 4.5 schema —
        # every canonical field; the conditional-attestation
        # supplementary fields are emitted alongside for dry-mode soak
        # and the FR-3 conditionality audit log; the Source-B signal
        # fields (2026-09-11, incident b08f40fe) ride alongside in the
        # same additive shape — marker_hit / marker_terms are NOT in
        # the canonical tuple but the format string is the single log
        # source of truth so dry-mode soak data is grep-able. Stage 3
        # (2026-09-17): the outside-window diagnostic, the marker
        # route enum, the judge-verdict stamp fields, and the
        # trigger-derivation fields retired with R1/R5/R6/R7).
        logger.info(
            "event=leader_completion_gate decision=%s instance_id=%s "
            "gate_location=%s leader_prompt_version=%s mode=%s "
            "attestation_present=%s denied_count=%s next_denied_count=%s "
            "pending_children=%s queued_or_expected_wakeups=%s "
            "live_descendants=%s busy_descendants=%s "
            "attestation_required=%s "
            "last_real_user_found=%s last_real_user_index=%s "
            "first_delegation_after_last_user_index=%s "
            "delegation_tool_call_total=%s "
            "messages_scanned=%s "
            "scanned_window_size=%s scanner_window_truncated=%s "
            "scanner_summary_seen=%s should_inject_nudge=%s "
            "marker_hit=%s marker_terms=%s "
            "length_trigger=%s final_word_count=%s "
            "user_answer_pending=%s",
            result.decision.value,
            instance_id,
            gate_location,
            leader_prompt_version,
            mode_resolver.mode,
            result.attestation_present,
            result.denied_count,
            result.next_denied_count,
            result.pending_children,
            result.queued_or_expected_wakeups,
            result.live_descendants,
            result.busy_descendants,
            result.attestation_required,
            result.last_real_user_found,
            result.last_real_user_index,
            result.first_delegation_after_last_user_index,
            result.delegation_tool_call_total,
            result.messages_scanned,
            result.scanned_window_size,
            result.scanner_window_truncated,
            result.scanner_summary_seen,
            result.should_inject_nudge,
            result.marker_hit,
            ",".join(result.marker_terms) if result.marker_terms else "<none>",
            result.length_trigger,
            result.final_word_count,
            result.user_answer_pending,
            extra=meta,
        )

        # (v) promotion-metric increments (Phase 4 task 4.6). Only the
        # THREE canonical metric names are emitted; counter math lives in
        # ``daemon.services.attestation_resolver``. Dry-mode passive
        # observer: ``dry_log_total`` ticks on every dry evaluation;
        # ``dry_log_deny_predicate_total`` ticks on the SUBSET whose R2
        # deny predicate would have fired under ``enforce``
        # (``attestation_required == True AND not attested AND
        # pending_children == 0 AND queued_or_expected_wakeups == 0 AND
        # live_descendants == 0`` — the FOUR-input R2 predicate with the
        # 2026-09-06 conditional-attestation arm). The
        # ``attestation_required == True`` arm is the documented
        # conditional predicate: the soak signal measures
        # "would-have-denied AMONG delegated missions" so the
        # non-delegating traffic the feature exempts does NOT inflate
        # the deny ratio (matching ``docs/setup.md`` dry-mode section).
        # Enforce-mode denied: ``enforce_denied_total`` ticks on
        # ``Decision.DENIED`` only — ``terminal_after_bound`` is the
        # escalation path, NOT a "denied under enforce" event.
        if attestation_enabled and scope_applicable:
            if mode_resolver.mode == "dry":
                record_promotion_metric(METRIC_DRY_LOG_TOTAL)
                if (
                    result.attestation_required
                    and not result.attestation_present
                    and result.pending_children == 0
                    and result.queued_or_expected_wakeups == 0
                    and result.live_descendants == 0
                    # FIX-2 (2026-09-16): an open awaiting-answer handle
                    # blocks denial under the NEW enforce semantics —
                    # exclude it from the would-have-denied subset so
                    # the dry→enforce adjudication signal stays honest.
                    and not result.user_answer_pending
                ):
                    record_promotion_metric(METRIC_DRY_LOG_DENY_PREDICATE_TOTAL)
            elif (
                mode_resolver.mode == "enforce"
                and result.decision is Decision.DENIED
            ):
                record_promotion_metric(METRIC_ENFORCE_DENIED_TOTAL)

        # (vi) LCA unified-resolver evaluation (Stage-1 shadow seam,
        # Stage-2 flip, 2026-09-16, resolver-unification §4). Runs
        # EXACTLY where the existing gate evaluation runs (the
        # canonical-path tail; the meta-bypass / off-mode early returns
        # above never reach it). Computes the activation predicate +
        # fused bundle and attaches the :class:`ResolverEvalSnapshot`
        # to the decision — the GRAPH NODE's fused block (the
        # AUTHORITATIVE completion outcome source post-flip) invokes
        # the fused judge from it and emits the ONE structured
        # ``event=leader_completion_resolver_eval`` row. Any
        # resolver-side error is logged
        # (``..._resolver_eval_error``) and NEVER propagates into gate
        # control flow. Zero new DB reads for the predicate (C values
        # are the already-materialized facade reads; A is a pure
        # message-walk; B values are the already-computed marker/
        # length results); the tree-rows provider runs lazily and only
        # on would-fire.
        try:
            from .attestation_resolver_activation import (
                SourceBSignals,
                SourceCSignals,
                evaluate_resolver_activation,
                make_tree_rows_provider,
            )

            resolver_snapshot = evaluate_resolver_activation(
                instance_id=instance_id,
                gate_location=gate_location,
                leader_prompt_version=leader_prompt_version,
                messages=messages,
                mode=mode_resolver.mode,
                attestation_enabled=attestation_enabled,
                scope_applicable=scope_applicable,
                attestation_required=attestation_required,
                attested=scan.attested,
                user_answer_pending=user_answer_pending,
                c_values=SourceCSignals(
                    pending_children=pending_children,
                    queued_or_expected_wakeups=queued_or_expected_wakeups,
                    live_descendants=live_descendants,
                    busy_descendants=busy_descendants,
                    user_answer_pending=user_answer_pending,
                ),
                b_values=SourceBSignals(
                    marker_hit=result.marker_hit,
                    marker_terms=result.marker_terms,
                    length_trigger=result.length_trigger,
                    final_word_count=result.final_word_count,
                    attested=scan.attested,
                ),
                denied_count=denied_count,
                deny_bound=mode_resolver.deny_bound,
                old_decision=result.decision,
                c_tree_rows_provider=make_tree_rows_provider(
                    manager, instance_id
                ),
                # Section U (incident 4dfded83, 2026-09-18): the
                # delegation scanner's already-computed
                # ``last_real_user_index`` anchors the user's original
                # request — extract its CONTENT here (no re-walk, no new
                # DB reads) and pass it through; ``assemble_fused_bundle``
                # re-checks it with the scanner's own
                # ``is_real_user_message`` predicate (fail-closed to
                # omission). ``None`` ⇒ U omitted entirely.
                user_intent_message=(
                    messages[delegation_scan.last_real_user_index]
                    if delegation_scan.last_real_user_found
                    and 0
                    <= delegation_scan.last_real_user_index
                    < len(messages)
                    else None
                ),
            )
            # Stage-2 flip: the snapshot rides the decision to the
            # graph node (frozen dataclass — ``replace`` rebuilds).
            result = replace(result, resolver=resolver_snapshot)
        except Exception as shadow_exc:  # noqa: BLE001 — resolver compute is never load-bearing
            logger.error(
                "event=leader_completion_resolver_eval_error "
                "error_class=%s instance_id=%s gate_location=%s "
                "mode=%s gate_exception_seen=true detail=%s: %s",
                type(shadow_exc).__name__,
                instance_id,
                gate_location,
                mode_resolver.mode,
                type(shadow_exc).__name__,
                shadow_exc,
            )
            # F-C (2026-09-16) — stamp the transient
            # ``gate_exception_seen`` marker on the instance row via
            # the shared helper (FR-13 contract: "set a transient
            # gate_exception_seen=true flag on the instance row").
            # The caller (``graph.py:attestation_gate_node``) passes
            # the ``ledger`` kwarg — if absent (legacy callers, the
            # standalone ``evaluate`` unit tests), the marker write
            # is a no-op (the unit tests already assert the row
            # emission directly; the marker write is the canonical
            # observability stamp for the production graph path).
            #
            # NOTE: we deliberately do NOT also flip
            # ``decision.gate_exception_seen=True`` here — that
            # field is the early-return trigger at graph.py:5170
            # (the FR-13 full fail-open allow path on the
            # scanner/decide seam). Setting it here would bypass
            # Phase-3 deny+nudge (DP-5 REJECTED — resolver-fault on
            # the deny band stays conservative deny via the existing
            # ledger+nudge machinery; the marker stamp is the
            # observability side, not the outcome flip). The
            # ledger write is the canonical FR-13 stamp; the
            # outcome stays whatever ``decide()`` returned.
            if ledger is not None:
                try:
                    from daemon.graph import (
                        _persist_gate_exception_marker,
                    )

                    _persist_gate_exception_marker(ledger, instance_id)
                except Exception as marker_exc:  # noqa: BLE001 — marker is diagnostic only
                    logger.warning(
                        "event=leader_completion_gate_db_error "
                        "method=persist_gate_exception_marker "
                        "instance_id=%s detail=%s: %s",
                        instance_id,
                        type(marker_exc).__name__,
                        marker_exc,
                    )

        return result

    except Exception as exc:  # noqa: BLE001 — C3 fail-open (scanner/decide)
        logger.error(
            "event=leader_completion_gate_error error_class=%s "
            "instance_id=%s gate_location=%s mode=%s "
            "leader_prompt_version=%s denied_count=%s gate_exception_seen=true "
            "decision=fail_open_allowed detail=%s: %s",
            type(exc).__name__,
            instance_id,
            gate_location,
            mode_resolver.mode,
            leader_prompt_version,
            denied_count,
            type(exc).__name__,
            exc,
        )
        return GateDecision(
            decision=Decision.ALLOWED,
            next_denied_count=denied_count,
            should_inject_nudge=False,
            denied_count=denied_count,
            # Scanner/R2 diagnostics UNKNOWN on this path — report the
            # -1 "unknown due to error" sentinel (the DB-seam fail-open
            # convention above), never the 0 default: 0 is a MEANINGFUL
            # R2 value ("no wakeups") and would feed the dry-mode
            # deny-predicate metric a false positive.
            messages_scanned=-1,
            scanned_window_size=mode_resolver.window,
            pending_children=-1,
            queued_or_expected_wakeups=-1,
            live_descendants=-1,
            busy_descendants=-1,
            gate_exception_seen=True,
        )
