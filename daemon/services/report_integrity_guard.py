"""B.S.1-i — (b) terminal-child-aware waiting PREDICATE.

Wave 2 of ``wc-wake-report-integrity`` (decisions.md C2-D2.7 LOCKED
2026-08-30, phase2-plan §4.2). This module holds the **predicate
function** (:func:`evaluate_declared_waiting_violations`), the
stage-ii **LOG-ONLY helper** (:func:`log_declared_waiting_violations`)
that completion-stamp sites call to surface violations, and — since
B.S.1-iii — the **flag-gated ENFORCEMENT action**
(:func:`enforce_declared_waiting_violations`) plus the kill-switch
wiring (B.S.2).

STAGE-III CONTRACT (D2.6 LOCKED — fail-OPEN, never block):

* The evaluation stays at the stage-ii INLINE SAME-TX position
  (B.S.7 — do NOT move it). The stamp transaction ALWAYS proceeds.
* The enforcement ACTION (the adjudication-notice enqueue) happens
  AFTER the stamp has committed, in the caller's async post-commit
  context, wrapped in a bounded 5s wait.
* Flag OFF (ship default) = byte-parity with stage ii: the ONLY
  added work is one cached-boolean check; NO notice work happens.

Why a separate module:

* The predicate is **content-blind** (D2.18 LOCKED): it reads
  delivery/declaration state ONLY — never message content, never
  ``tool_calls``. Keeping the predicate isolated from the
  report-shape paths (``child_reports``, ``error_reporting``,
  ``report_delivery_recovery``) prevents accidental coupling and
  makes the (b) blast radius explicit.
* The predicate is **pure** — given a session + parent id it
  returns a structured dataclass. No DB writes, no event
  emission, no metric increment, no logging side effect. That
  purity is what makes the B.S.3 fail-OPEN suite cheap to
  write in stage iii and what makes the same-tx invocation
  (B.S.7) safe inside the completion transaction.
* The predicate is **fail-OPEN** at stage iii (D2.6 LOCKED) —
  but at THIS stage it is a library function; normal exceptions
  propagate, no try/except wrapper, no hot-path wiring.

Composition (D2.7 LOCKED, two signals):

* **PRIMARY:** ``report_injections`` rows for the parent with
  ``state IN ('PENDING','DEFERRED')`` whose child instance is
  terminal (COMPLETED / FAILED / ERROR / TERMINATED). Sourced via
  :meth:`ReportInjectionRepository.count_pending_for_parent_with_terminal_child`.
* **CORROBORATING:** ``dependency_watchers`` rows for the parent
  with status FIRED ∧ ``enqueued_at IS NULL`` (the
  FIRED-but-unenqueued inter-report-gap shape). Sourced via
  :meth:`DependencyWatcherRepository.count_fired_unenqueued_for_parent`.

Both signals are read from a CALLER-PROVIDED session (B.S.7
binding): in stage ii/iii, the completion transaction invokes the
predicate; any uncommitted writes the transaction has staged are
visible to the predicate without an intermediate ``commit()``.

Why NOT ``dependency_bus.pending_watchers`` (D2.7 LOCKED
rationale): the bus's in-memory cache is purged post-
``emit_terminal`` (``daemon/services/dependency_bus.py:709``) and
read cache-first (``daemon/services/dependency_bus.py:960-961``).
In the inter-report gap, the cache is EMPTY for exactly the
scenario the predicate must detect. The durable rows are the
source of truth — they survive cache eviction, restart, and the
cache-purge window.

Why NOT ``instances.status = WAITING_CHILDREN`` (D2.7 LOCKED
rationale, technical-analysis §"Technical Debt" item 1):
``WAITING_CHILDREN`` is deprecated as a control-flow signal. The
declared-waiting shape lives on the durable report / watcher
rows, not on the instance status column.

Structured return shape (OQ-6 disposition, decisions.md
bottom): :class:`DeclaredWaitingViolationReport` carries the
per-child detail for stage iii's enforcement notice. The notice
text parameterizes by the child's terminal status (COMPLETED /
FAILED / ERROR / TERMINATED each have a distinct adjudication
playbook). The predicate exposes ``child_terminal_status`` for
the PRIMARY rows so the stage-iii notice builder can pick the
right playbook without re-querying.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlmodel import Session

from daemon.constants import (
    WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED,
)
from daemon.repositories.dependency_bus.repository import (
    DependencyWatcherRepository,
)
from daemon.repositories.report_injection.repository import (
    ReportInjectionRepository,
)

if TYPE_CHECKING:  # pragma: no cover — type-check only
    from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Structured return — the contract stage iii consumes.
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DeclaredWaitingViolationReport:
    """Structured result of :func:`evaluate_declared_waiting_violations`.

    The predicate is CONTENT-BLIND (D2.18 LOCKED): this dataclass
    exposes delivery/declaration state ONLY. No message ``content``,
    no ``tool_calls``, no LLM-visible payload — those belong to
    stage iii's notice builder, which composes the adjudication
    text from this report plus the (c) marker.

    Attributes:
        parent_instance_id: The parent that was evaluated. Echoed
            so the stage-iii log line can cite it directly.
        pending_with_terminal_child: List of ``{"injection_id",
            "child_instance_id", "state", "child_terminal_status"}``
            dicts — one per PRIMARY-signal obligation
            (``report_injections`` PENDING ∪ DEFERRED whose child
            is terminal). Empty on healthy paths. The
            ``child_terminal_status`` field is the verbatim
            ``InstanceStatus`` value (COMPLETED / FAILED / ERROR
            / TERMINATED) so stage iii's notice builder can pick
            the right adjudication playbook without re-querying
            (OQ-6 dispositions, decisions.md bottom).
        fired_unenqueued: List of ``{"watch_id", "source_task_id",
            "state", "fired_at"}`` dicts — one per
            CORROBORATING-signal obligation (``dependency_watchers``
            FIRED ∧ ``enqueued_at IS NULL``). Empty on healthy
            paths.
        is_violation: ``True`` iff at least one obligation (either
            signal) is present. Stage iii will use this as the
            log-signal condition (B.S.1-ii) and the
            enforcement-flip gate (B.S.1-iii).

    The dataclass is ``frozen=True`` so the predicate's
    structured return cannot be mutated by downstream consumers —
    keeps the "evaluated once, cited many" contract honest.
    """

    parent_instance_id: str
    pending_with_terminal_child: list[dict[str, str]] = field(default_factory=list)
    fired_unenqueued: list[dict[str, str]] = field(default_factory=list)

    @property
    def count(self) -> int:
        """Total obligation count across both signals."""
        return len(self.pending_with_terminal_child) + len(self.fired_unenqueued)

    @property
    def is_violation(self) -> bool:
        """``True`` iff at least one obligation is present (either signal)."""
        return self.count > 0


# ─────────────────────────────────────────────────────────────────────────────
# Predicate function
# ─────────────────────────────────────────────────────────────────────────────


def evaluate_declared_waiting_violations(
    session: Session,
    parent_instance_id: str,
    *,
    report_repo: ReportInjectionRepository | None = None,
    watcher_repo: DependencyWatcherRepository | None = None,
    engine: "Engine | None" = None,
) -> DeclaredWaitingViolationReport:
    """Evaluate the (b) declared-waiting predicate for a parent.

    Returns a structured :class:`DeclaredWaitingViolationReport`.
    EMPTY on healthy paths; NON-EMPTY in the incident shape.

    **Pure / same-tx (B.S.1-i binding).** Reads both signals on
    the CALLER-PROVIDED ``session``. No new transaction is
    opened; no commit / rollback is issued. Stage ii/iii's
    completion transaction may invoke the predicate and rely on
    its staged writes being visible.

    **Repo injection (test seam only).** ``report_repo`` and
    ``watcher_repo`` are accepted as optional kwargs so the unit
    tests can pin a stub without spinning up a full repo. When
    omitted, the predicate resolves them from ``engine`` (or the
    ``session.get_bind().engine`` if ``engine`` is also omitted).
    Production callers pass ``engine`` explicitly so the predicate
    has the same shared engine every other repository does.

    **No logging, no metrics, no event emission.** At this stage
    the predicate is a library function. Stage ii attaches the
    log; stage iii attaches the metric + enforcement flip. None
    of those are wired here.

    **Failure mode (stage-iii fail-OPEN).** Normal exceptions
    propagate to the caller. The stage-iii caller is responsible
    for wrapping this in a ``try / except`` that defaults to
    "no violation, proceed" (D2.6 LOCKED). At THIS stage, no
    such wrapper exists — the predicate is honest about its
    behavior, the fail-OPEN is a stage-iii policy.

    Args:
        session: An open SQLModel/SQLAlchemy ``Session`` on the
            same engine that holds the repositories. The session
            is owned by the caller — the predicate MUST NOT
            commit / rollback / close it.
        parent_instance_id: The parent whose declared-waiting
            obligations to evaluate.
        report_repo: Optional pre-constructed
            :class:`ReportInjectionRepository`. Defaults to
            ``ReportInjectionRepository(engine)``.
        watcher_repo: Optional pre-constructed
            :class:`DependencyWatcherRepository`. Defaults to
            ``DependencyWatcherRepository(engine)``.
        engine: Optional SQLAlchemy ``Engine``. Used when
            ``report_repo`` / ``watcher_repo`` are not provided.
            If all three are omitted, the predicate falls back to
            ``session.get_bind().engine`` (the engine the caller's
            session is bound to).

    Returns:
        A :class:`DeclaredWaitingViolationReport`. The dataclass
        is frozen; callers MUST NOT mutate it.

    Raises:
        Any DB error (connection lost, constraint violation,
        session closed, etc.) propagates to the caller. The
        predicate does not swallow exceptions — at THIS stage
        the caller is responsible for the fail-OPEN policy
        (stage iii).
    """
    if report_repo is None:
        if engine is None:
            engine = session.get_bind()
        report_repo = ReportInjectionRepository(engine)
    if watcher_repo is None:
        if engine is None:
            engine = session.get_bind()
        watcher_repo = DependencyWatcherRepository(engine)

    primary_rows = (
        report_repo.count_pending_for_parent_with_terminal_child(
            session, parent_instance_id
        )
    )
    corroborating_rows = watcher_repo.count_fired_unenqueued_for_parent(
        session, parent_instance_id
    )

    return DeclaredWaitingViolationReport(
        parent_instance_id=parent_instance_id,
        pending_with_terminal_child=list(primary_rows),
        fired_unenqueued=list(corroborating_rows),
    )


# ─────────────────────────────────────────────────────────────────────────────
# B.S.1-ii — stage-ii LOG helper (the only stage-ii observable effect)
# ─────────────────────────────────────────────────────────────────────────────

# Greppable prefix for every guard line. The stage-ii soak (≤2 weeks,
# D2.5-FLIP) correlates violations across stamp sites by grepping THIS
# prefix, so it must never be reworded casually.
_LOG_PREFIX = "[ReportIntegrityGuard]"


def log_declared_waiting_violations(
    session: Session,
    parent_instance_id: str,
    *,
    context_tag: str,
    engine: "Engine | None" = None,
) -> "DeclaredWaitingViolationReport | None":
    """Stage-ii LOG attach + stage-iii evaluation hand-off.

    Evaluates :func:`evaluate_declared_waiting_violations` on the
    CALLER-PROVIDED ``session`` (B.S.7 same-tx binding: the read runs
    INSIDE the completion transaction, so the soak validates the REAL
    predicate position) and, when the report is NON-EMPTY, emits ONE
    structured WARNING log line.

    Stage-ii observable contract (unchanged): **LOG ONLY** at the
    stamp sites — no injection, no status write, no enqueue, no flag
    read, no metric, no mutation of any kind. Healthy paths (EMPTY
    report) emit NOTHING.

    Stage-iii extension (B.S.1-iii): the helper **returns** the
    evaluated :class:`DeclaredWaitingViolationReport` (``None`` on a
    predicate failure, a malformed predicate result, or a healthy
    path) so the site can hand the SAME evaluation — re-read is
    forbidden, B.S.7 pins one evaluation at the inline same-tx
    position — to the post-commit enforcement action
    (:func:`enforce_declared_waiting_violations`). With the
    kill-switch OFF the return value is simply ignored by sites that
    do not opt into enforcement, and the behavior is byte-identical
    to stage ii.

    Line shape (greppable, one line per stamp-site evaluation)::

        [ReportIntegrityGuard] declared-waiting violation at
        <context_tag>: parent=<id> count=<n> detail=[child=<id>
        status=<terminal> evidence=PRIMARY; watch=<id>
        task=<id> evidence=CORROBORATING]

    ``context_tag`` names the stamp site (e.g.
    ``"child_reports.root_completion"``, ``"observer_finalize_job"``)
    so soak analysis can attribute a firing to the exact completion
    path.

    Fail-OPEN policy (D2.6 LOCKED): the predicate call is wrapped in
    ``try / except`` — on ANY exception the helper emits a WARNING
    (predicate failed, completion proceeds) and RETURNS ``None``. A
    MALFORMED predicate result (not a
    :class:`DeclaredWaitingViolationReport`) is likewise dropped with
    a WARNING — it is never trusted, never attribute-probed into a
    secondary raise. The helper never raises into the completion
    path, never blocks, never mutates anything.

    Args:
        session: The caller's open session (the completion
            transaction). Owned by the caller — never committed,
            rolled back, or closed here.
        parent_instance_id: The parent whose completion is being
            stamped.
        context_tag: Which stamp site is evaluating (free-form short
            string; include it verbatim in soak greps).
        engine: Optional engine override forwarded to the predicate;
            defaults to the predicate's own ``session.get_bind()``
            fallback.

    Returns:
        The evaluated :class:`DeclaredWaitingViolationReport` when
        the predicate found violations (``is_violation`` True);
        ``None`` on a healthy path, a predicate failure, or a
        malformed predicate result.
    """
    try:
        report = evaluate_declared_waiting_violations(
            session, parent_instance_id, engine=engine
        )
    except Exception as exc:  # noqa: BLE001 — fail-OPEN (D2.6): never raise into completion
        logger.warning(
            "%s predicate FAILED at %s for parent=%s — fail-OPEN, "
            "completion proceeds: %s: %s",
            _LOG_PREFIX,
            context_tag,
            parent_instance_id,
            type(exc).__name__,
            exc,
        )
        return None

    if not isinstance(report, DeclaredWaitingViolationReport):
        # Stage-iii hardening (B.S.3 scenario 2): a malformed predicate
        # result is dropped — never trusted, never probed.
        logger.warning(
            "%s predicate returned MALFORMED result at %s for parent=%s "
            "(%s) — fail-OPEN, completion proceeds",
            _LOG_PREFIX,
            context_tag,
            parent_instance_id,
            type(report).__name__,
        )
        return None

    if not report.is_violation:
        # Healthy path — zero noise.
        return None

    details: list[str] = []
    for row in report.pending_with_terminal_child:
        details.append(
            f"child={row.get('child_instance_id')} "
            f"status={row.get('child_terminal_status')} "
            f"state={row.get('state')} evidence=PRIMARY"
        )
    for row in report.fired_unenqueued:
        details.append(
            f"watch={row.get('watch_id')} "
            f"task={row.get('source_task_id')} "
            f"state={row.get('state')} evidence=CORROBORATING"
        )

    logger.warning(
        "%s declared-waiting violation at %s: parent=%s count=%d detail=[%s]",
        _LOG_PREFIX,
        context_tag,
        parent_instance_id,
        report.count,
        "; ".join(details),
    )
    return report


# ─────────────────────────────────────────────────────────────────────────────
# B.S.2 — kill-switch wiring (flag-gated enforcement, DORMANT by default)
# ─────────────────────────────────────────────────────────────────────────────
#
# Mirrors the ``ENSEMBLE_WC_WAKE_ENQUEUE`` resolver shape (env const →
# resolve-once + cache → boot INFO log) with the default-OFF direction
# of that precedent (NOT the governor's default-ON): the ship state is
# stage-ii log-only (decisions.md C2-D2.5, C2-D2.5-FLIP). Restart-
# required semantics: the resolved boolean is cached for the process
# lifetime — flipping the env mid-flight has no effect until restart.
#
# NO AUTO-FLIP EXISTS: nothing in this module (or anywhere in the
# daemon) sets the flag truthy, schedules a flip, or reads a clock to
# decide the flag state. The ONLY writer of the flag is the operator's
# environment (D2.5-FLIP: operator-owned soak/flip policy). Tests must
# reset the cached resolver (``_B_GUARD_ENABLED`` to ``None``, as the
# test fixtures do via monkeypatch) on teardown to observe env flips
# within a session.

# The env NAME comes from daemon/constants.py — no literal fork (pinned
# by tests/unit/services/test_b_kill_switch_registry.py).
_B_GUARD_ENV = WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED
_B_GUARD_ENABLED: bool | None = None
_B_GUARD_BOOT_LOG_EMITTED: bool = False


def resolve_report_integrity_b_guard_enabled() -> bool:
    """Resolve and cache the (b) enforcement kill-switch (B.S.2).

    Returns:
        ``True`` ONLY when the operator explicitly flipped the env ON
        (truthy: ``1``/``true``/``yes``/``on``). Unset, blank, falsy
        (``0``/``false``/``no``/``off``), and unknown values all
        resolve ``False`` — the OFF default is the ship state
        (stage-ii log-only) and blanking the env mid-incident is the
        instant-revert INTENT (the cache still requires a restart to
        observe the change). Unknown non-blank values additionally
        WARN once (cached on first access).

    The cached value lives for the daemon's lifetime: restart
    required to flip (mirror of the WC-wake / governor-guard
    precedents).
    """
    global _B_GUARD_ENABLED
    if _B_GUARD_ENABLED is not None:
        return _B_GUARD_ENABLED
    raw = os.environ.get(_B_GUARD_ENV, "0").strip().lower()
    if raw in ("0", "false", "no", "off"):
        _B_GUARD_ENABLED = False
    elif raw in ("1", "true", "yes", "on"):
        _B_GUARD_ENABLED = True
    else:
        logger.warning(
            "%s=%r is not a recognized truthy/falsy value; falling back "
            "to OFF (default — stage-ii log-only mode). Valid falsy: "
            "0/false/no/off. Valid truthy: 1/true/yes/on.",
            _B_GUARD_ENV,
            raw,
        )
        _B_GUARD_ENABLED = False
    return _B_GUARD_ENABLED


def is_report_integrity_b_enforcement_active(manager: Any = None) -> bool:
    """The SINGLE runtime gate for (b) enforcement (B.S.2 dual-read).

    ANDs the cached env resolver with the boot-loaded config section
    (``Config.report_integrity.b_terminal_waiting_guard_enabled``,
    itself an env-bound Pydantic field) — the same dual-read shape as
    the ``LIMITS_GOVERNOR_RECURSION_GUARD_ENABLED`` precedent:

    * env OFF (default) → ``False`` — log-only, zero notice work.
    * env ON + config section absent (tests, partial harnesses) →
      ``True`` — the env flip is the documented operator path.
    * env ON + explicit config ``false`` → ``False`` — an explicit
      YAML veto is defense-in-depth; a YAML ``true`` alone NEVER
      enables (the env must still be flipped).

    Args:
        manager: Optional manager-like object carrying ``.config``
            (the real ``InstanceManager`` exposes it). Callers without
            a config surface may pass ``None`` — the gate then reduces
            to the env resolver.
    """
    if not resolve_report_integrity_b_guard_enabled():
        return False
    if manager is None:
        return True
    cfg = getattr(manager, "config", None)
    if cfg is None:
        return True
    section = getattr(cfg, "report_integrity", None)
    if section is None:
        return True
    return bool(getattr(section, "b_terminal_waiting_guard_enabled", True))


def emit_report_integrity_b_guard_boot_log() -> None:
    """Emit the one-time boot-time INFO log naming the resolved state.

    Called from ``InstanceManager.__init__`` (mirrors
    ``emit_wc_wake_enqueue_boot_log`` / the governor-guard wrapper).
    Restart-required semantics — flipping the env mid-flight has no
    effect on the cached boolean or on this one-shot log.
    """
    global _B_GUARD_BOOT_LOG_EMITTED
    if _B_GUARD_BOOT_LOG_EMITTED:
        return
    _B_GUARD_BOOT_LOG_EMITTED = True
    enabled = resolve_report_integrity_b_guard_enabled()
    state_line = (
        "enabled (enforcement: one adjudication notice injected to the "
        "parent at the completion stamp; never blocks)"
        if enabled
        else "DISABLED (default — log-only mode: the [ReportIntegrityGuard] "
        "soak log still fires at completion stamps; no notice is ever "
        "injected)"
    )
    logger.info(
        "Report-integrity (b) terminal-waiting guard resolved: %s "
        "(env %s=%s). Restart required to flip. See docs/setup.md "
        "(WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED).",
        state_line,
        _B_GUARD_ENV,
        os.environ.get(_B_GUARD_ENV, "<unset>"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# B.S.1-iii — flag-gated ENFORCEMENT (the adjudication notice)
# ─────────────────────────────────────────────────────────────────────────────
#
# Channel ruling (C2-D2.2 (a1) + D2.9): system-authored enqueue-style
# message — the SAME channel family as the ``system:watchdog`` hang
# notice — NEVER inside the ``[SYSTEM NOTE]`` frame. The source value
# satisfies the reserved-origin contract (``RESERVED_SOURCE_PREFIXES``
# ``"system:"`` prefix family) and the strictly-additive
# ``system:*`` dispatch-source guard in ``instance_messaging.py``
# (system-origin notices resolve their external dispatch target from
# ``instance_metadata.original_source`` instead of stamping
# themselves into it).

#: Durable-enqueue provenance (JAFP: internal infra path via
#: ``manager.enqueue_message`` — MessageQueue + Task rows, NO JobItem).
#: Echoed into the ``MESSAGE_RECEIVED`` event data and stamped on the
#: durable row's ``source`` column + ``message_metadata`` audit dict.
REPORT_INTEGRITY_GUARD_NOTICE_SOURCE: str = "system:report-integrity-guard"

#: The 5s budget around the enforcement ACTION (B.S.3 scenario 3): the
#: bounded wait wraps ONLY the async notice enqueue — never the
#: evaluation (stage-ii position, B.S.7) and never the stamp (D2.6:
#: the stamp ALWAYS proceeds first).
NOTICE_ENQUEUE_BUDGET_SECONDS: float = 5.0

#: Dedupe ledger (ONE notice per completion-episode): parent id → the
#: violation-set signature of the last notice actually enqueued.
#: In-process (a restart re-arms at-most-once per stamp — the stamp
#: sites are once-per-completion, so the primary dedupe is the
#: once-per-stamp path; this ledger is the belt-and-braces against a
#: site re-firing on the SAME violation set). Cleared when a later
#: same-tx evaluation for the parent comes back CLEAN (episode over)
#: and replaced when a DIFFERENT violation set fires (new episode).
#: Consumed by the watchdog wedge predicate (B.S.5 shared cooldown,
#: OQ-4: no double-fire for the same episode).
_B_NOTICE_LEDGER: dict[str, str] = {}


def _violation_signature(report: DeclaredWaitingViolationReport) -> str:
    """Stable per-(parent, episode) signature of a violation report."""
    parts = [
        f"{row.get('child_instance_id')}:{row.get('state')}"
        f":{row.get('child_terminal_status')}"
        for row in report.pending_with_terminal_child
    ]
    parts += [
        f"watch:{row.get('watch_id')}:{row.get('state')}"
        for row in report.fired_unenqueued
    ]
    return "|".join(sorted(parts))


def parent_has_active_b_notice(parent_instance_id: str) -> bool:
    """B.S.5 shared-cooldown read: does ``(b)`` have an ACTIVE notice
    episode for this parent?

    The watchdog wedge predicate consults this so a parent that (b)
    just noticed does not ALSO get a wedge notice for the same episode
    (OQ-4 disposition: no double-fire). The episode ends when a later
    same-tx evaluation for the parent comes back CLEAN (the stamp
    sites clear the ledger) or is replaced by a different violation
    set.
    """
    return parent_instance_id in _B_NOTICE_LEDGER


def _clear_b_notice_if_clean(parent_instance_id: str, report: Any) -> None:
    """Episode-end hook: a CLEAN evaluation closes any open episode.

    Called by the stamp sites' same-tx evaluation path — a parent
    whose declared-waiting obligations RESOLVED (healthy stamp) must
    not keep suppressing the wedge for a stale episode.
    """
    if report is None:
        return
    if (
        isinstance(report, DeclaredWaitingViolationReport)
        and not report.is_violation
        and parent_instance_id in _B_NOTICE_LEDGER
    ):
        del _B_NOTICE_LEDGER[parent_instance_id]
        logger.info(
            "%s declared-waiting episode CLOSED for parent=%s "
            "(evaluation clean — obligations resolved)",
            _LOG_PREFIX,
            parent_instance_id,
        )


# OQ-6 disposition: the notice text is parameterized by the child's
# terminal status — each terminal-but-different outcome has a DISTINCT
# adjudication playbook for the parent. Values are static strings;
# the predicate stays content-blind (D2.18) — the playbook is chosen
# from the durable row's ``child_terminal_status``, never from report
# content.
_PLAYBOOK_BY_STATUS: dict[str, str] = {
    "completed": (
        "The child reports completion, but its report was never "
        "delivered to you (durable delivery obligation still open). "
        "Treat the child's 'done' as UNVERIFIED: ask the child to "
        "re-deliver its report, or re-verify the outcome yourself "
        "before closing your own task."
    ),
    "failed": (
        "The child FAILED before delivering any report. Decide "
        "explicitly: re-dispatch the work to a fresh child, degrade "
        "the task, or accept the loss — do not assume the work "
        "happened."
    ),
    "error": (
        "The child ended in ERROR mid-work, before delivering a "
        "report. Adjudicate the error: retry, respawn a replacement, "
        "or drop the task — any partial results are stranded with the "
        "child and were never delivered to you."
    ),
    "terminated": (
        "The child was TERMINATED (cancel/cascade) with its report "
        "undelivered. The waiting obligation is stale, not a delivery "
        "promise: decide whether the work needs a replacement child "
        "or can be retired."
    ),
}


def _build_adjudication_notice(
    report: DeclaredWaitingViolationReport,
    *,
    context_tag: str,
) -> str:
    """Compose the ONE adjudication notice for a completion episode.

    Content contract (D2.9 + OQ-6 + D2.18):

    * names the parent and each violated child with its terminal
      status (from the durable rows — the predicate's structured
      report; per-child playbook per OQ-6);
    * states what was detected (declared-waiting obligation with a
      terminal child, undelivered report);
    * STATICALLY cites the Wave-1 (c) marker pattern so the parent
      knows what to look for in a (re)delivered child report — the
      citation is a fixed string; the guard NEVER reads report
      content to decide whether the marker is present (content-blind,
      D2.18);
    * plain system-authored text — NEVER inside the ``[SYSTEM NOTE]``
      frame (C2-D2.2).
    """
    lines: list[str] = [
        f"[{REPORT_INTEGRITY_GUARD_NOTICE_SOURCE}] Report-integrity "
        f"notice — a completion stamp at `{context_tag}` found "
        f"declared-waiting obligations with terminal child(ren) and "
        f"undelivered report(s).",
        "",
        f"Parent: {report.parent_instance_id}",
    ]
    for row in report.pending_with_terminal_child:
        status = str(row.get("child_terminal_status", "")).lower()
        playbook = _PLAYBOOK_BY_STATUS.get(
            status,
            "Adjudicate explicitly: the child is terminal but its "
            "report was never delivered to you.",
        )
        lines.append("")
        lines.append(
            f"- Child {row.get('child_instance_id')} — terminal status "
            f"`{row.get('child_terminal_status')}`, injection state "
            f"`{row.get('state')}`: {playbook}"
        )
    for row in report.fired_unenqueued:
        lines.append("")
        lines.append(
            f"- Watcher {row.get('watch_id')} (task "
            f"{row.get('source_task_id')}, state `{row.get('state')}`) "
            f"fired but its follow-up was never enqueued: the child's "
            f"outcome may have reached no one. Adjudicate the task "
            f"explicitly."
        )
    lines.append("")
    lines.append(
        "How to verify a (re)delivered child report: look for the "
        f"integrity marker `{constants_marker_text()}` — its presence "
        "means the report was sent with ZERO tool-call evidence in "
        "the child's source history, so treat the report as an "
        "interim acknowledgement, not evidence of completed work."
    )
    lines.append("")
    lines.append(
        "(Automated notice — you are not blocked; adjudicate the "
        "obligation(s) above and carry on.)"
    )
    return "\n".join(lines)


def constants_marker_text() -> str:
    """Static (c) marker citation for the notice text (D2.9).

    Reads the Wave-1 constant so the notice cannot drift from the
    marker actually appended to reports. Deliberately NOT a read of
    any report content (D2.18 content-blindness is about report
    payload; this is a compile-time constant).
    """
    from daemon.constants import REPORT_SANITY_MARKER

    return REPORT_SANITY_MARKER


async def enforce_declared_waiting_violations(
    report: "DeclaredWaitingViolationReport | None",
    *,
    manager: Any,
    parent_instance_id: str,
    context_tag: str,
) -> bool:
    """B.S.1-iii enforcement action: inject ONE adjudication notice.

    Called by the stamp sites AFTER the stamp transaction has
    committed (D2.6: the stamp ALWAYS proceeds — this action can
    never block, delay beyond the budget, or raise into the
    completion path).

    Flow per call:

    1. Kill-switch OFF (ship default) → return immediately — zero
       notice work (the stage-ii hot-path bound is preserved).
    2. ``None`` / healthy / malformed report → no-op (fail-OPEN).
    3. Dedupe: the same violation set already noticed for this
       parent → no-op (ONE notice per completion episode; see
       :data:`_B_NOTICE_LEDGER`).
    4. Compose the OQ-6-parameterized notice and enqueue it via the
       durable wake path (``manager.enqueue_message``, source
       ``system:report-integrity-guard``, priority 0, structured
       ``message_metadata`` provenance) under a 5s budget.
    5. ANY failure (enqueue raise, budget timeout) → one WARNING,
       return ``False``; completion already stands.

    Returns:
        ``True`` iff a notice was actually enqueued (test/observability
        convenience — production callers ignore it).
    """
    # (1) Kill-switch short-circuit — OFF means zero notice work.
    if not is_report_integrity_b_enforcement_active(manager):
        return False

    # (2) Fail-OPEN guards — nothing actionable, nothing trusted blindly.
    if report is None:
        return False
    if not isinstance(report, DeclaredWaitingViolationReport):
        logger.warning(
            "%s enforcement received MALFORMED report at %s for parent=%s "
            "(%s) — fail-OPEN, completion proceeds",
            _LOG_PREFIX,
            context_tag,
            parent_instance_id,
            type(report).__name__,
        )
        return False
    if not report.is_violation:
        return False

    # (3) Dedupe — one notice per completion episode.
    signature = _violation_signature(report)
    if _B_NOTICE_LEDGER.get(parent_instance_id) == signature:
        logger.debug(
            "%s notice already enqueued for parent=%s with the same "
            "violation set — dedupe, no re-notify",
            _LOG_PREFIX,
            parent_instance_id,
        )
        return False

    # (4) Compose + enqueue under the bounded budget.
    notice = _build_adjudication_notice(report, context_tag=context_tag)
    try:
        await asyncio.wait_for(
            manager.enqueue_message(
                instance_id=parent_instance_id,
                message=notice,
                source=REPORT_INTEGRITY_GUARD_NOTICE_SOURCE,
                priority=0,
                metadata={
                    "report_integrity_notice": True,
                    "context_tag": context_tag,
                    "violations": {
                        "pending_with_terminal_child": list(
                            report.pending_with_terminal_child
                        ),
                        "fired_unenqueued": list(report.fired_unenqueued),
                    },
                },
            ),
            timeout=NOTICE_ENQUEUE_BUDGET_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 — fail-OPEN (D2.6): never raise into completion
        logger.warning(
            "%s enforcement notice enqueue FAILED at %s for parent=%s — "
            "fail-OPEN, completion already proceeded: %s: %s",
            _LOG_PREFIX,
            context_tag,
            parent_instance_id,
            type(exc).__name__,
            exc,
        )
        return False

    # (5) Success — record the episode + one INFO for the soak trail.
    _B_NOTICE_LEDGER[parent_instance_id] = signature
    logger.info(
        "%s enforcement notice enqueued at %s: parent=%s count=%d "
        "source=%s budget=%.1fs",
        _LOG_PREFIX,
        context_tag,
        parent_instance_id,
        report.count,
        REPORT_INTEGRITY_GUARD_NOTICE_SOURCE,
        NOTICE_ENQUEUE_BUDGET_SECONDS,
    )
    return True


__all__ = [
    "DeclaredWaitingViolationReport",
    "NOTICE_ENQUEUE_BUDGET_SECONDS",
    "REPORT_INTEGRITY_GUARD_NOTICE_SOURCE",
    "enforce_declared_waiting_violations",
    "emit_report_integrity_b_guard_boot_log",
    "evaluate_declared_waiting_violations",
    "is_report_integrity_b_enforcement_active",
    "log_declared_waiting_violations",
    "parent_has_active_b_notice",
    "resolve_report_integrity_b_guard_enabled",
]
