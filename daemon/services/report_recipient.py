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
ROWS (completion-subsystem Proposal B PR1; AMENDED PR3 — THE FLIP)
--------------------------------------------------------------------------
PR1 minted ``mission_commissions`` rows on the nudge and send_message
lanes with routing authority deliberately left with THIS resolver's
audit-ledger latest-committed-nudge tie-break ("the routing flip … is
the PR2/PR3 boundary").

**PR3 IS THE FLIP.** The routing authority now reads the record first
(Proposal B §3 D1 + the reconciliation-sweep design §3, "Lane 2's
commission-first leg"):

  LEG 1 (the record — armed ONLY in ``enforce``): the child's OLDEST
  OUTSTANDING commission row
  (``mission_commissions.worker_instance_id == child``,
  ``advisory_backfill = False`` — advisory backfill rows are EXCLUDED
  from routing authority FOREVER) → ``commissioner_instance_id``.
  Multi-commissioner tie-break = ruling ④: the OLDEST-outstanding
  discriminator. NO liveness filter on this leg: a TERMINATED
  commissioner is handled downstream by the write path's dead-recipient
  guard (which falls back to the structural parent and leaves the
  commission outstanding for the reconciliation sweep — the honest
  shape; routing a dead commissioner's report straight to the parent
  here would silently satisfy nothing and strand the obligation).

  LEG 2 (the DECLARED LEGACY FALLBACK — this module's audit-recency
  heuristic, UNCHANGED): the latest committed ``instance_nudge`` audit
  rule, exactly as documented above.

  LEG 3: the STRUCTURAL PARENT (the audit leg's own fail-back).

Mode ladder: in ``dry`` (the ship default) and ``off`` leg 1 is INERT —
the resolution is byte-for-byte the PR1/PR2 behavior (the audit
heuristic + the structural-parent fail-back). ``enforce`` is an
operator promotion (restart-read).

FAIL-OPEN POSTURE (unchanged): leg 1 fails OPEN to leg 2 on any read
error (a records-side problem must never break delivery); leg 2 keeps
failing back to the structural parent on any error. At-least-once
delivery preserved on every path.

RESOLVED REPORT DESTINATION (PR3)
---------------------------------
:func:`resolve_report_recipient_with_commission` exposes the FULL
resolution: the recipient id PLUS the deciding commission row (``None``
when a legacy leg decided). The natural completion write path uses it
to write ``report_injections.mission_id`` /
``report_injections.commissioner_instance_id`` (the PR1 columns gain
their writers) and to SATISFY the routing commission when the report is
actually delivered to the commissioner (the PR3 terminator — guarded
``UPDATE ... WHERE state='outstanding'``, fail-open).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, NamedTuple

from sqlmodel import Session, select

from ..repositories.instance.models import Instance, InstanceStatus
from ..repositories.instance_ops.models import InstanceOpsAudit
from ..repositories.mission_record.models import MissionCommission
from ..repositories.mission_record.repository import MissionRecordRepository
from ..repositories.report_injection.models import ReportInjection
# The mode ladder has ONE owner (``mission_record_service``); the reads
# module re-exports the gate for read-side consumers. IMPORTED, never
# redefined — a second definition would be a shadowed knob.
from .mission_record_reads import mission_records_enforce_enabled

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


class ResolvedReportDestination(NamedTuple):
    """The FULL routing resolution (PR3 D1 flip).

    ``recipient_id`` — the instance the report should be delivered to
    (the structural parent when no leg decided otherwise).
    ``commission`` — the deciding LEG-1 commission row (``None`` when
    the audit heuristic or the structural parent decided). The natural
    write path uses it for the ``report_injections`` mission-provenance
    writers + the PR3 delivery terminator.
    """

    recipient_id: str | None
    commission: MissionCommission | None


def _resolve_commission_leg(
    engine: Any,
    child_instance_id: str,
) -> MissionCommission | None:
    """LEG 1 — the child's oldest outstanding (non-advisory) commission.

    Armed ONLY in ``enforce`` (the mode ladder gates the read sites);
    runs on its OWN session (the mission-record data plane is a
    separate store from the caller's transaction — the F1 session
    -isolation discipline); fails OPEN to ``None`` (→ the audit leg)
    on ANY error.
    """
    try:
        if not mission_records_enforce_enabled():
            return None
        commission = MissionRecordRepository(
            engine
        ).find_routing_commission_for_worker(child_instance_id)
        if commission is None:
            return None
        commissioner_id = getattr(
            commission, "commissioner_instance_id", None
        )
        if not isinstance(commissioner_id, str) or not commissioner_id:
            # A malformed row must not route the report at None —
            # fail open to the audit leg.
            logger.warning(
                "report recipient commission leg: commission %s… has a "
                "malformed commissioner id — falling through to the "
                "audit leg",
                str(getattr(commission, "commission_id", ""))[:8],
            )
            return None
        return commission
    except Exception as exc:  # noqa: BLE001 — fail-open (leg 1 → leg 2)
        logger.warning(
            "report recipient commission leg failed (child=%s) — "
            "falling through to the audit leg: %s: %s",
            child_instance_id[:8],
            type(exc).__name__,
            exc,
        )
        return None


def resolve_report_recipient_with_commission(
    *,
    engine: Any,
    child_instance_id: str,
    structural_parent_id: str | None,
    session: Session | None = None,
    completion_time: datetime | None = None,
) -> ResolvedReportDestination:
    """The FULL three-leg resolution (see the module docstring).

    Leg 1 (enforce only): the child's oldest outstanding commission →
    its commissioner. Leg 2: the audit-recency heuristic (the declared
    legacy fallback). Leg 3: the structural parent. Every failure is
    fail-open to the next leg; delivery is never broken by a
    records-side problem.
    """
    if not child_instance_id or not structural_parent_id:
        return ResolvedReportDestination(structural_parent_id, None)
    commission = _resolve_commission_leg(engine, child_instance_id)
    if commission is not None:
        recipient = commission.commissioner_instance_id
        logger.info(
            "report recipient resolved to the mission commissioner "
            "(child=%s, commissioner=%s, structural_parent=%s, "
            "mission=%s…, commission=%s…)",
            child_instance_id[:8],
            recipient[:8],
            (structural_parent_id or "")[:8],
            str(getattr(commission, "mission_id", ""))[:8],
            str(getattr(commission, "commission_id", ""))[:8],
        )
        return ResolvedReportDestination(recipient, commission)
    # Legs 2 + 3 — the legacy audit resolver, byte-identical.
    return ResolvedReportDestination(
        _resolve_legacy_audit_recipient(
            engine=engine,
            child_instance_id=child_instance_id,
            structural_parent_id=structural_parent_id,
            session=session,
            completion_time=completion_time,
        ),
        None,
    )


def _resolve_legacy_audit_recipient(
    *,
    engine: Any,
    child_instance_id: str,
    structural_parent_id: str | None,
    session: Session | None = None,
    completion_time: datetime | None = None,
) -> str | None:
    """LEG 2 + LEG 3 — the PR1/PR2 audit-recency resolver, unchanged.

    Demoted to the declared legacy fallback by the PR3 flip (see the
    module docstring, "ADOPTION DELTA (a) — … AMENDED PR3"); its body
    is byte-identical to the pre-flip ``resolve_report_recipient``.
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


def resolve_report_recipient(
    *,
    engine: Any,
    child_instance_id: str,
    structural_parent_id: str | None,
    session: Session | None = None,
    completion_time: datetime | None = None,
) -> str | None:
    """Resolve the instance that should receive ``child_instance_id``'s
    completion report — the PR3 three-leg resolution (commission-first
    in ``enforce``; the audit heuristic + structural parent otherwise).

    The signature and return type are UNCHANGED from PR1/PR2 — every
    production call site (``child_reports`` producer,
    ``message_processing_pipeline`` Site-1 marker,
    ``report_delivery_recovery`` lane 2) inherits the commission-first
    leg without call-site churn, and its downstream recipient-aware
    guards keep reading the resolved recipient (the design's "teach the
    RESOLUTION, not the downstream guards" property).

    See :func:`resolve_report_recipient_with_commission` for the full
    result (the deciding commission row) and the module docstring for
    the rule + posture.

    Args:
        engine: The SQLAlchemy engine (used when no ``session`` is
            supplied).
        child_instance_id: The child that produced the report.
        structural_parent_id: The child's structural parent (the
            pre-D1 behaviour — the fail-back target).
        session: OPTIONAL caller-owned session — applies to the LEGACY
            audit leg only (the commission leg ALWAYS opens its own
            session: the mission-record data plane must never join a
            caller's write transaction). ``None`` opens its own
            session — this is the ONLY shape any daemon call site
            uses (see the module docstring, "SESSION ISOLATION").
        completion_time: The completion instant (``T_completion``).
            ``None`` means "now" — the resolver's own clock.

    Returns:
        The recipient instance id, or ``structural_parent_id`` on a
        miss or on ANY error (fail-back posture).
    """
    return resolve_report_recipient_with_commission(
        engine=engine,
        child_instance_id=child_instance_id,
        structural_parent_id=structural_parent_id,
        session=session,
        completion_time=completion_time,
    ).recipient_id


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
    "ResolvedReportDestination",
    "parse_iso_utc",
    "resolve_report_recipient",
    "resolve_report_recipient_with_commission",
]
