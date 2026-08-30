"""B.S.1-i — (b) terminal-child-aware waiting PREDICATE.

Wave 2 of ``wc-wake-report-integrity`` (decisions.md C2-D2.7 LOCKED
2026-08-30, phase2-plan §4.2). This module is the **predicate
function only** — no behavior change anywhere. No call site in
this codebase invokes :func:`evaluate_declared_waiting_violations`
yet; stages ii (log-attach at the COMPLETED stamp sites) and iii
(flag-gated enforcement with the pre-committed flip per D2.5-FLIP)
land as separate commits by later coders.

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

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sqlmodel import Session

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


__all__ = [
    "DeclaredWaitingViolationReport",
    "evaluate_declared_waiting_violations",
]
