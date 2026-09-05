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
AND ``queued_or_expected_wakeups == 0``. Any non-zero pending input
means a legitimate wakeup is en route and the turn-end is allowed
without attestation (the nudge-flood kill).

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

from langchain_core.messages import BaseMessage

from .attestation_scanner import (
    DEFAULT_ATTESTATION_TOOL_NAME,
    attestation_seen_outside_window,
    scan_for_attestation_detailed,
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
    ENSEMBLE_ATTESTATION_MODE_ENV,
    ENSEMBLE_ATTESTATION_WINDOW_ENV,
    ENSEMBLE_ATTESTATION_DENY_BOUND_ENV,
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
    # ——— scanner diagnostics (canonical schema fields) ———
    scanner_window_truncated: bool = False
    scanner_summary_seen: bool = False
    attest_seen_outside_window: bool = False
    attestation_present: bool = False
    messages_scanned: int = 0
    scanned_window_size: int = 0
    # ——— R2 inputs (canonical schema fields) ———
    pending_children: int = 0
    queued_or_expected_wakeups: int = 0
    denied_count: int = 0


# ─────────────────────────────────────────────────────────────────────────────
# 2.2 — the pure decision function
# ─────────────────────────────────────────────────────────────────────────────


def decide(
    attested: bool,
    pending_children: int,
    queued_or_expected_wakeups: int,
    denied_count: int,
    bound: int,
    scope_applicable: bool,
    mode: str,
    attestation_enabled: bool,
) -> GateDecision:
    """Pure gate decision over the R2 inputs (plan 2.2 logic tree).

    Logic tree (plan 2.2, with leader ruling 1 counter semantics):

    1. Meta-conditions — ``attestation_enabled=False`` (C2 bypass),
       ``scope_applicable=False`` (D3 non-leader), or ``mode="off"``
       → :attr:`Decision.ALLOWED` with the counter UNCHANGED (the gate
       did not run; a non-run is NOT one of the four reset triggers).
    2. ``mode="dry"`` → :attr:`Decision.DRY_LOG` — evaluation recorded,
       ZERO side effects, counter unchanged.
    3. enforce + attested → :attr:`Decision.ALLOWED` with
       ``next_denied_count = 0`` (reset trigger 1).
    4. enforce + not attested + any pending wakeup input > 0 →
       :attr:`Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP` with the
       counter UNCHANGED (ruling 1: the R2 non-reset IS the loop
       protection).
    5. enforce + not attested + no pending wakeups +
       ``denied_count + 1 > bound`` → :attr:`Decision.TERMINAL_AFTER_BOUND`
       with ``next_denied_count = 0`` (reset trigger 2; the same reset
       clears the escalated flag per ruling 2 — persistence is Phase 3).
    6. otherwise → :attr:`Decision.DENIED` with
       ``next_denied_count = denied_count + 1`` and
       ``should_inject_nudge = True``.

    An unrecognized ``mode`` fails OPEN to :attr:`Decision.ALLOWED`
    (mirrors the resolver's fail-open ruling); the resolver's one-shot
    WARN is the typo safety net.

    Args:
        attested: Scanner verdict — attestation tool call in window.
        pending_children: R2 input from ``manager.count_pending_children``.
        queued_or_expected_wakeups: R2 input from
            ``manager.get_queued_or_expected_wakeups``.
        denied_count: Current ``attestation_denied_count`` (Phase 2
            stand-in: the caller passes 0; Phase 3 threads the ledger).
        bound: Deny bound (D5, default 3).
        scope_applicable: D3 scope — leader-only.
        mode: "off" | "dry" | "enforce" (validated upstream).
        attestation_enabled: C2 master flag from graph assembly.

    Returns:
        :class:`GateDecision` — the decision core.
    """
    # (1) meta-conditions — gate not applied; counter untouched.
    if (not attestation_enabled) or (not scope_applicable) or mode == "off":
        return GateDecision(
            decision=Decision.ALLOWED,
            next_denied_count=denied_count,
            should_inject_nudge=False,
        )

    # (2) dry — evaluate + log only; zero side effects.
    if mode == "dry":
        return GateDecision(
            decision=Decision.DRY_LOG,
            next_denied_count=denied_count,
            should_inject_nudge=False,
        )

    if mode != "enforce":
        # Unknown mode — fail OPEN (resolver already WARNed).
        return GateDecision(
            decision=Decision.ALLOWED,
            next_denied_count=denied_count,
            should_inject_nudge=False,
        )

    # ——— enforce ———

    # (3) attested allow — reset trigger 1.
    if attested:
        return GateDecision(
            decision=Decision.ALLOWED,
            next_denied_count=0,
            should_inject_nudge=False,
        )

    # (4) R2 allow — legitimate pending wakeup; counter unchanged.
    if pending_children > 0 or queued_or_expected_wakeups > 0:
        return GateDecision(
            decision=Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP,
            next_denied_count=denied_count,
            should_inject_nudge=False,
        )

    # (5) bound exceeded — escalation; counter reset (trigger 2).
    if denied_count + 1 > bound:
        return GateDecision(
            decision=Decision.TERMINAL_AFTER_BOUND,
            next_denied_count=0,
            should_inject_nudge=False,
        )

    # (6) deny — increment + nudge.
    return GateDecision(
        decision=Decision.DENIED,
        next_denied_count=denied_count + 1,
        should_inject_nudge=True,
    )


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
    "attestation_enabled",
    "scope_applicable",
    "leader_prompt_version",
    "gate_location",
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
    "attest_seen_outside_window",
    "messages_scanned",
    "scanned_window_size",
    "mode",
    "scanner_window_truncated",
    "scanner_summary_seen",
)


def build_gate_config(
    instance_id: str | None,
    settings: GateSettings,
    *,
    tool_name: str = DEFAULT_ATTESTATION_TOOL_NAME,
    attestation_enabled: bool = True,
    scope_applicable: bool = True,
    leader_prompt_version: str = "",
) -> dict[str, Any]:
    """Build the gate's config dict (the O8-audited shape).

    Deliberately contains NO ``checkpoint_ns`` (and no other LangGraph
    checkpoint namespace material): the gate reads in-node
    ``state["messages"]`` only. ``tests/unit/test_attestation_gate.py``
    pins this via :data:`GATE_CONFIG_KEYS` and a direct key assertion.
    """
    return {
        "instance_id": instance_id,
        "tool_name": tool_name,
        "window": settings.window,
        "deny_bound": settings.deny_bound,
        "mode": settings.mode,
        "attestation_enabled": attestation_enabled,
        "scope_applicable": scope_applicable,
        "leader_prompt_version": leader_prompt_version,
        "gate_location": GATE_LOCATION_GRAPH_END_CANDIDATE,
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
) -> GateDecision:
    """Glue: scanner → R2 facade reads → decide → canonical log entry.

    Read sequence (CR-2 TOCTOU contract, mandatory ordering):
    ``messages → pending_children → queued_or_expected_wakeups``. The
    in-node ``messages`` list is the caller's LangGraph state argument —
    evaluated first, synchronously, before any manager facade call.

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
        manager: Handle exposing the two NEW facades
            (``count_pending_children`` / ``get_queued_or_expected_
            wakeups``). May be ``None`` ONLY in degenerate embeddings —
            the response is fail-open allow (R2 inputs unreadable).
        attestation_enabled: C2 flag (False bypasses everything).
        scope_applicable: D3 flag (False bypasses everything).
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

        # (v) O3 diagnostic — stale attestation outside the window.
        seen_outside = attestation_seen_outside_window(
            messages, mode_resolver.window, tool_name
        )

        # (ii) R2 inputs — the two NEW manager facades (SYNC reads; the
        # graph node bridges this whole function via asyncio.to_thread).
        # DB seam: `except Exception` (KeyboardInterrupt stays fail-closed).
        try:
            pending_children = manager.count_pending_children(instance_id)
            queued_or_expected_wakeups = manager.get_queued_or_expected_wakeups(
                instance_id
            )
        except Exception as db_exc:  # noqa: BLE001 — fail-open at the DB seam
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
                "attest_seen_outside_window=%s "
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
                seen_outside,
                scan.messages_scanned,
                mode_resolver.window,
                scan.window_truncated,
                scan.summary_seen,
                error_class,
                db_exc,
            )
            return GateDecision(
                decision=Decision.ALLOWED,
                next_denied_count=denied_count,
                should_inject_nudge=False,
                scanner_window_truncated=scan.window_truncated,
                scanner_summary_seen=scan.summary_seen,
                attest_seen_outside_window=seen_outside,
                attestation_present=scan.attested,
                messages_scanned=scan.messages_scanned,
                scanned_window_size=mode_resolver.window,
                pending_children=-1,
                queued_or_expected_wakeups=-1,
                denied_count=denied_count,
            )

        # (iii) the pure decision.
        result = decide(
            attested=scan.attested,
            pending_children=pending_children,
            queued_or_expected_wakeups=queued_or_expected_wakeups,
            denied_count=denied_count,
            bound=mode_resolver.deny_bound,
            scope_applicable=scope_applicable,
            mode=mode_resolver.mode,
            attestation_enabled=attestation_enabled,
        )

        # Attach diagnostics + R2 inputs → log-ready object.
        result = replace(
            result,
            scanner_window_truncated=scan.window_truncated,
            scanner_summary_seen=scan.summary_seen,
            attest_seen_outside_window=seen_outside,
            attestation_present=scan.attested,
            messages_scanned=scan.messages_scanned,
            scanned_window_size=mode_resolver.window,
            pending_children=pending_children,
            queued_or_expected_wakeups=queued_or_expected_wakeups,
            denied_count=denied_count,
        )

        # (iv) canonical structured log entry (Phase 4 task 4.5 schema —
        # every field, every evaluation, no omissions).
        logger.info(
            "event=leader_completion_gate decision=%s instance_id=%s "
            "gate_location=%s leader_prompt_version=%s mode=%s "
            "attestation_present=%s denied_count=%s next_denied_count=%s "
            "pending_children=%s queued_or_expected_wakeups=%s "
            "attest_seen_outside_window=%s messages_scanned=%s "
            "scanned_window_size=%s scanner_window_truncated=%s "
            "scanner_summary_seen=%s should_inject_nudge=%s",
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
            result.attest_seen_outside_window,
            result.messages_scanned,
            result.scanned_window_size,
            result.scanner_window_truncated,
            result.scanner_summary_seen,
            result.should_inject_nudge,
            extra=meta,
        )

        # (v) promotion-metric increments (Phase 4 task 4.6). Only the
        # THREE canonical metric names are emitted; counter math lives in
        # ``daemon.services.attestation_resolver``. Dry-mode passive
        # observer: ``dry_log_total`` ticks on every dry evaluation;
        # ``dry_log_deny_predicate_total`` ticks on the SUBSET whose R2
        # deny predicate would have fired under ``enforce``
        # (``not attested AND pending_children == 0 AND
        # queued_or_expected_wakeups == 0``). Enforce-mode denied:
        # ``enforce_denied_total`` ticks on ``Decision.DENIED`` only —
        # ``terminal_after_bound`` is the escalation path, NOT a "denied
        # under enforce" event.
        if attestation_enabled and scope_applicable:
            if mode_resolver.mode == "dry":
                record_promotion_metric(METRIC_DRY_LOG_TOTAL)
                if (
                    not result.attestation_present
                    and result.pending_children == 0
                    and result.queued_or_expected_wakeups == 0
                ):
                    record_promotion_metric(METRIC_DRY_LOG_DENY_PREDICATE_TOTAL)
            elif (
                mode_resolver.mode == "enforce"
                and result.decision is Decision.DENIED
            ):
                record_promotion_metric(METRIC_ENFORCE_DENIED_TOTAL)

        return result

    except Exception as exc:  # noqa: BLE001 — C3 fail-open (scanner/decide)
        logger.error(
            "event=leader_completion_gate_error error_class=%s "
            "instance_id=%s gate_location=%s mode=%s "
            "leader_prompt_version=%s denied_count=%s "
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
            scanned_window_size=mode_resolver.window,
        )
