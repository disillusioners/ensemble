"""Report-recipient resolver — completion-subsystem D1 (Phase 1).

Defect D1: a child's completion report was routed to the child's
STRUCTURAL PARENT — the instance recorded in ``instances.parent_id``.
When the work was commissioned by somebody ELSE (an ``instance_nudge``
from an out-of-tree agent), the report went to the wrong instance: the
commissioner never learned the work finished, and the structural parent
consumed a report that was never its own (the demonstrated fd302262
shape).

THE RULE (proposal-a §2.1)
-------------------------
1. ``n`` = the latest ``instance_ops_audit`` row with
   ``action='instance_nudge'`` AND ``outcome='committed'`` AND
   ``dry_run=false`` AND ``target=<child full id>`` AND
   ``created_at <= T_completion``.
2. ``T_prev`` = ``max(report_injections.created_at)`` for the same
   child (``-inf`` when the child never reported).
3. The recipient is ``n.created_by_instance_id`` iff ``n`` exists AND
   ``n.created_at > T_prev`` AND the nudger's instance row exists AND
   its status is not ``TERMINATED``; otherwise the STRUCTURAL PARENT.
4. ``created_at`` is TEXT ISO — it is PARSED IN PYTHON, never
   string-compared in SQL. Both columns (``instance_ops_audit`` and
   ``report_injections``) carry ``datetime.now(timezone.utc)
   .isoformat()`` — a fixed-width UTC format — but the rule is
   enforced in Python so a legacy / hand-written row with a different
   spelling cannot silently mis-order.

POSTURE — FAIL BACK TO THE STRUCTURAL PARENT
---------------------------------------------
ANY resolver read / parse / DB error falls back to the structural
parent. This is a DELIVERY-PATH routing decision, not a heal-probe:
a "miss" is the NORMAL case (99%+ of children were never nudged), and
the subsystem's documented posture is at-least-once delivery. Fail-back
is byte-for-byte the old behaviour.

The ``instance_ops_audit`` table is ASSUMED to exist (the DDL is at
HEAD and the PG schema is ``create_all``-additive — the table is
created at boot). If it is genuinely absent, the resolver falls back
to the structural parent by design (proposal-a §8 assumption 5).

SESSION ISOLATION — THE HAZARD THAT WAS REMOVED (F1)
-----------------------------------------------------
The resolver's audit read used to run on the CALLER's write session
(``child_reports.py`` handed it the ``WriteGuardSession`` it was
already holding). On PostgreSQL a statement-level error POISONS the
enclosing transaction, so the fail-back was a NO-OP: the resolver
returned the structural parent, but every SUBSEQUENT write on that
transaction — the report ``MessageQueue`` row, the ``PROCESS_REPORT``
Task, the ``ReportInjection`` row, the instance COMPLETED stamp —
then failed with "current transaction is aborted". The completion
write DIED instead of merely misrouting, which breaks
proposal-a §2.1's "fail-back = old behaviour exactly".

Every call site now resolves on the resolver's OWN session
(``session=None``), so a failed read can never poison a caller's
transaction: the fail-back is real and the completion is delivered to
the structural parent exactly as it was before D1. The in-repo
precedent is the Site-1 marker invocation in
``message_processing_pipeline.py`` (own session) and the recovery-path
invocation in ``report_delivery_recovery.py`` (own session).

DO NOT hand the resolver a session that sits inside a caller's WRITE
transaction — the hazard returns with it (see the ``session``
parameter's contract below).

CARRIER
-------
The resolved recipient is carried on the completion result
(``_ChildCompletionDbResult.report_recipient_id``) — computed ONCE at
the producer, threaded to every write-path site. The STRUCTURAL parent
is NEVER overloaded: the bus corrective emit and the ``child_completed``
SSE stay structural (they express the WATCHER obligation, not the
report's destination).

REJECTED (proposal-a §2.1): the dual-recipient + mission-tag candidate.
It keeps the demonstrated harm (the structural parent still consumes the
foreign report) and needs two recipient sets taught to the backstop.

ADOPTION DELTA (a) — TIE-BREAK PRECEDENCE vs THE MISSION-COMMISSION
ROWS (completion-subsystem Proposal B PR1)
----------------------------------------------------------
Proposal B PR1 now mints ``mission_commissions`` rows on the nudge and
send_message lanes (multiple OUTSTANDING rows per worker allowed; the
guarded-state lifecycle is the discriminator; the rung-2 lookup takes
the OLDEST outstanding row). THIS RESOLVER'S AUTHORITY IS UNCHANGED:
report routing still keys on the audit-ledger latest-committed-nudge
tie-break, NOT on commission rows. The commission ledger is
PR1-write-only (``ENSEMBLE_MISSION_RECORDS`` mode ladder, default
``dry``); the routing flip (commission-row lookup, with whatever
multi-commissioner tie-break is decided for the flip) is the PR2/PR3
boundary. Advisory backfill commission rows
(``advisory_backfill=True``, ``provenance='backfill'``) are EXCLUDED
from routing authority FOREVER — if a future flip ever consults
commission rows, it must filter them out (the "if dropped, the ledger
is worse than no ledger" rule).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlmodel import Session, select

from ..repositories.instance.models import Instance, InstanceStatus
from ..repositories.instance_ops.models import InstanceOpsAudit
from ..repositories.report_injection.models import ReportInjection

logger = logging.getLogger(__name__)

#: The audited action name the resolver reads.
NUDGE_ACTION = "instance_nudge"

#: The audit outcome the resolver accepts — a REAL act. The nudge
#: writes a PREVIEW row by default (``dry_run=True``,
#: ``outcome='previewed'``) and settles the real act to
#: ``outcome='committed'``. Only the committed row counts.
NUDGE_OUTCOME_COMMITTED = "committed"


def parse_iso_utc(value: Any) -> datetime | None:
    """Parse a TEXT ISO-8601 timestamp into an aware UTC datetime.

    ``None`` for a missing / unparsable value — the caller treats that
    as "no usable timestamp" (never as an error).

    Naive values are assumed UTC (the repo's timestamp convention:
    ``now_utc_naive()`` for naive columns, ``now_utc_iso()`` for TEXT
    columns — both UTC).

    Args:
        value: The raw column value.

    Returns:
        An aware UTC ``datetime``, or ``None``.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _latest_committed_nudge(
    session: Session,
    child_instance_id: str,
    completion_time: datetime | None,
) -> InstanceOpsAudit | None:
    """The latest committed nudge for ``child_instance_id``.

    The SQL does NOT compare ``created_at`` — the bound is applied in
    PYTHON (the design's rule 4). The candidate set is the committed,
    non-dry-run nudges for the child; the table is tiny (the
    all-time census found 6 rows), so the read is cheap and the
    ordering is decided on the PARSED timestamps.
    """
    rows = session.exec(
        select(InstanceOpsAudit).where(
            InstanceOpsAudit.action == NUDGE_ACTION,
            InstanceOpsAudit.outcome == NUDGE_OUTCOME_COMMITTED,
            InstanceOpsAudit.dry_run == False,
            InstanceOpsAudit.target == child_instance_id,
        )
    ).all()

    best: tuple[datetime, str, str] | None = None
    best_row: InstanceOpsAudit | None = None
    for row in rows:
        stamp = parse_iso_utc(getattr(row, "created_at", None))
        if stamp is None:
            continue
        if completion_time is not None and stamp > completion_time:
            continue
        raw = str(getattr(row, "created_at", "") or "")
        row_id = str(getattr(row, "id", "") or "")
        key = (stamp, raw, row_id)
        if best is None or key > best:
            best = key
            best_row = row
    return best_row


def _last_report_time(
    session: Session,
    child_instance_id: str,
) -> datetime | None:
    """``max(report_injections.created_at)`` for the child (``None``
    when the child never reported — the ``-inf`` case)."""
    rows = session.exec(
        select(ReportInjection.created_at).where(
            ReportInjection.child_instance_id == child_instance_id
        )
    ).all()
    stamps: list[datetime] = []
    for raw in rows:
        value = raw[0] if isinstance(raw, tuple) else raw
        parsed = parse_iso_utc(value)
        if parsed is not None:
            stamps.append(parsed)
    return max(stamps) if stamps else None


def resolve_report_recipient(
    *,
    engine: Any,
    child_instance_id: str,
    structural_parent_id: str | None,
    session: Session | None = None,
    completion_time: datetime | None = None,
) -> str | None:
    """Resolve the instance that should receive ``child_instance_id``'s
    completion report.

    See the module docstring for the rule. The return value is the
    RECIPIENT — the structural parent when the resolver cannot (or
    must not) decide.

    Args:
        engine: The SQLAlchemy engine (used when no ``session`` is
            supplied).
        child_instance_id: The child that produced the report.
        structural_parent_id: The child's structural parent (the
            pre-D1 behaviour — the fail-back target).
        session: OPTIONAL caller-owned session. When given, the reads
            run on THAT session (inside the caller's transaction) and
            the method does NOT commit. ``None`` opens its own
            session — this is the ONLY shape any daemon call site
            uses, and it is REQUIRED for a caller whose session sits
            inside a write transaction: on PostgreSQL a failed read on
            the caller's session poisons that transaction and the
            fail-back becomes a no-op (see the module docstring,
            "SESSION ISOLATION — THE HAZARD THAT WAS REMOVED").
        completion_time: The completion instant (``T_completion``).
            ``None`` means "now" — the resolver's own clock.

    Returns:
        The recipient instance id, or ``structural_parent_id`` on a
        miss or on ANY error (fail-back posture).
    """
    if not child_instance_id or not structural_parent_id:
        return structural_parent_id
    try:
        if session is not None:
            return _resolve_in_session(
                session,
                child_instance_id=child_instance_id,
                structural_parent_id=structural_parent_id,
                completion_time=completion_time,
            )
        with Session(engine) as own_session:
            return _resolve_in_session(
                own_session,
                child_instance_id=child_instance_id,
                structural_parent_id=structural_parent_id,
                completion_time=completion_time,
            )
    except Exception as exc:  # noqa: BLE001 — fail-back posture
        logger.warning(
            "report recipient resolver failed (child=%s) — falling back "
            "to the structural parent: %s",
            child_instance_id[:8],
            exc,
        )
        return structural_parent_id


def _resolve_in_session(
    session: Session,
    *,
    child_instance_id: str,
    structural_parent_id: str | None,
    completion_time: datetime | None,
) -> str | None:
    """The resolver body — runs on the caller's session."""
    if completion_time is None:
        completion_time = datetime.now(timezone.utc)

    nudge = _latest_committed_nudge(
        session, child_instance_id, completion_time
    )
    if nudge is None:
        return structural_parent_id

    nudger_id = getattr(nudge, "created_by_instance_id", None)
    if not isinstance(nudger_id, str) or not nudger_id:
        return structural_parent_id

    # Rule 3 — the nudge must be NEWER than the child's previous
    # report. A nudge older than the previous report describes work
    # that has already been reported; routing this report back to it
    # would re-deliver an old commission.
    nudge_stamp = parse_iso_utc(getattr(nudge, "created_at", None))
    previous_stamp = _last_report_time(session, child_instance_id)
    if (
        nudge_stamp is not None
        and previous_stamp is not None
        and nudge_stamp <= previous_stamp
    ):
        return structural_parent_id

    # Rule 3 — the nudger must still be a live recipient.
    nudger = session.get(Instance, nudger_id)
    if nudger is None:
        return structural_parent_id
    if getattr(nudger, "status", None) == InstanceStatus.TERMINATED.value:
        return structural_parent_id

    logger.info(
        "report recipient resolved to the commissioner (child=%s, "
        "commissioner=%s, structural_parent=%s)",
        child_instance_id[:8],
        nudger_id[:8],
        (structural_parent_id or "")[:8],
    )
    return nudger_id


__all__ = [
    "NUDGE_ACTION",
    "NUDGE_OUTCOME_COMMITTED",
    "parse_iso_utc",
    "resolve_report_recipient",
]
