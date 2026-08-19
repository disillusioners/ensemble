"""SQLModel-based ReportInjection repository.

Persistence layer for the ``report_injections`` table. Exposes the
three primitives the two delivery paths need:

* :meth:`enqueue` — register a new PENDING report (used by tests and
  callers that do not already hold a ``WriteGuardSession``; production
  enqueue happens inline in ``child_reports`` for transactional
  atomicity with the ``message_queue`` row + ``PROCESS_REPORT`` task).
* :meth:`claim_for_injection` — atomically claim ALL pending reports
  for a parent (the live agent-node drain) and mark their companion
  ``message_queue`` rows ``COMPLETED`` so they are not counted as
  pending own-queue work. Marks each row ``INJECTED``.
* :meth:`claim_for_task_delivery` — atomically claim a single report
  for the fallback ``PROCESS_REPORT`` task. Marks the row
  ``TASK_DELIVERED``; returns ``None`` when the row was already
  claimed by the injection path (exactly-once).

All claim methods use a guarded ``WHERE state = 'PENDING'`` UPDATE so
the two delivery paths cannot double-deliver a report, regardless of
which races ahead. The per-instance serialization guard in
``claim_pending_task`` (one RUNNING task per instance) guarantees the
agent-node drain and a fallback task for the SAME instance never run
concurrently, so the two claim methods are serialized per instance by
construction — the atomic claim is defense-in-depth for the
cross-instance / restart window.

The repository is intentionally sync; callers bridge to async via
``asyncio.to_thread`` (the project's standard pattern). The
agent-node drain runs inside the graph task on the event loop and
wraps the claim in ``asyncio.to_thread`` so the DB write does not
block the loop.

Phase 1 (pause-report-recovery) marker lifecycle
------------------------------------------------

The ``report_injections`` table is the home for the
DEFERRED delivery-obligation marker. State machine (mirrors the
model docstring):

* ``enqueue`` — creates a PENDING row WITH an artifact
  (``report_message_id`` set). Used by the regular completion path.
* :meth:`ensure_deferred` — creates a DEFERRED marker WITHOUT an
  artifact (``report_message_id`` NULL). The pause drop-site writers
  (Site 1, Variant B live site, Variant B idempotency guard) call
  this so a paused/cancelled obligation can be recovered later. The
  partial unique index ``uq_report_injections_oblig_triple`` (state
  IN ('PENDING','DEFERRED')) is the write-once gate: a concurrent
  duplicate ``ensure_deferred`` for the same triple raises
  ``sqlalchemy.exc.IntegrityError`` and this method absorbs it (W6).
* :meth:`find_deferred_for_parent` — Phase 2/3 recovery path lists
  the DEFERRED markers awaiting recovery for a parent.
* :meth:`transition_deferred_to_pending` — guarded ``UPDATE ...
  WHERE state='DEFERRED'`` plus a ``recovery_attempted_at`` stamp.
  rowcount=0 means another actor already recovered → returns False.

Contract: ``enqueue`` and ``ensure_deferred`` are the ONLY row-creation
paths on this table. Every non-terminal obligation is gated by the
triple unique index; every DEFERRED → INJECTED/TASK_DELIVERED
transition is forbidden by the ``state='PENDING'`` claim guards.

The recovery_attempted_at column is partial-indexed over PENDING rows
so the Phase 2 recovery sweep (``state='PENDING' AND
recovery_attempted_at IS NOT NULL``) stays cheap as the table grows.

Case-lockstep contract (C1): storage literals ('PENDING','DEFERRED',
reason values from ``daemon.constants``) and the app-layer constants
are UPPERCASE and must never drift in case. Any new state/reason value
is added to the storage enum / DDL AND the app constants in the same
change. The partial-index predicate uses the storage literals verbatim.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, NamedTuple

from sqlalchemy import update as sa_update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ..message_queue.models import MessageQueue, MessageStatus
from .models import ReportInjection, ReportInjectionState

logger = logging.getLogger(__name__)


# Module-level literals captured once to avoid re-evaluating the enums
# on every claim call.
_PENDING_STATE: str = ReportInjectionState.PENDING.value
_DEFERRED_STATE: str = ReportInjectionState.DEFERRED.value
_INJECTED_STATE: str = ReportInjectionState.INJECTED.value
_TASK_DELIVERED_STATE: str = ReportInjectionState.TASK_DELIVERED.value
_MSG_COMPLETED: str = MessageStatus.COMPLETED.value


class TaskDeliveryClaim(NamedTuple):
    """Tri-state result of :meth:`ReportInjectionRepository.claim_for_task_delivery`.

    Distinguishes three outcomes so the caller never silently loses a
    report by conflating "no row" with "already delivered":

    * ``"claimed"`` — a PENDING row existed and this call atomically
      transitioned it to ``TASK_DELIVERED``. The task OWNS delivery and
      proceeds with the normal message-processing pipeline. ``row`` is
      the claimed :class:`ReportInjection`.
    * ``"already_delivered"`` — a row exists but is terminal
      (``INJECTED`` by the live agent-node drain, or
      ``TASK_DELIVERED`` by a prior task run). The task MUST skip
      (dedup gate). ``row`` is ``None``.
    * ``"missing"`` — no ``report_injections`` row exists for this
      ``report_message_id`` at all (e.g. a ``PROCESS_REPORT`` task
      created by older code before this table existed, or a code path
      that forgot to enqueue). The task MUST proceed with normal
      delivery — treating "missing" as "delivered" would silently lose
      the report. ``row`` is ``None``.
    """

    status: str
    row: ReportInjection | None


class ReportInjectionRepository:
    """SQLModel-based repository for the ``report_injections`` table.

    All methods are synchronous; callers bridge to async via
    ``asyncio.to_thread`` or invoke from inside a worker-thread
    context.
    """

    def __init__(self, engine: Engine):
        """Initialize the repository with a database engine.

        Args:
            engine: SQLAlchemy Engine bound to a SQLite or PostgreSQL
                database. Should be the same shared engine used by the
                other repositories to avoid lock contention.
        """
        self.engine = engine

    # --------------------------------------------------------
    # INTERNAL HELPERS
    # --------------------------------------------------------

    def _now_iso(self) -> str:
        """Return current UTC time as ISO-8601 string."""
        return datetime.now(timezone.utc).isoformat()

    # --------------------------------------------------------
    # CREATE
    # --------------------------------------------------------

    def enqueue(
        self,
        parent_instance_id: str,
        child_instance_id: str,
        child_message_id: str,
        report_message_id: str,
        content: str,
    ) -> ReportInjection:
        """Insert a new PENDING report-injection row (WITH artifact).

        Production enqueue happens INLINE in
        ``child_reports._process_child_completion_db_sync`` (same
        ``WriteGuardSession`` transaction as the ``message_queue``
        row + ``PROCESS_REPORT`` task) for crash-consistency. This
        method exists for tests and for any caller that does not
        already hold a session.

        Phase 1 (pause-report-recovery): this is one of the ONLY two
        row-creation paths on this table (the other is
        :meth:`ensure_deferred`). The regular completion path uses
        ``enqueue`` because it has a real artifact
        (``report_message_id`` set, ``content`` non-null). Pause
        drop-site writers use ``ensure_deferred`` (no artifact yet —
        Phase 2 reconciliation fills it in).

        Args:
            parent_instance_id: The parent that should receive the
                report.
            child_instance_id: The child that produced the report.
            child_message_id: The child's completed ``message_id``.
            report_message_id: The ``message_id`` of the companion
                ``completion_report`` row in ``message_queue``.
            content: The report text to inject.

        Returns:
            The persisted :class:`ReportInjection` row (refreshed
            with any DB-side defaults).
        """
        row = ReportInjection(
            parent_instance_id=parent_instance_id,
            child_instance_id=child_instance_id,
            child_message_id=child_message_id,
            report_message_id=report_message_id,
            content=content,
        )
        with Session(self.engine) as session:
            session.add(row)
            session.commit()
            session.refresh(row)
        return row

    # --------------------------------------------------------
    # PHASE 1 — DEFERRED marker lifecycle
    # --------------------------------------------------------

    def ensure_deferred(
        self,
        parent_instance_id: str,
        child_instance_id: str,
        child_message_id: str,
        deferred_reason: str,
    ) -> ReportInjection | None:
        """Write-once insert (or in-place update) of a DEFERRED marker.

        Pause drop-site writers (Site 1 at
        ``message_processing_pipeline.py``, Variant B live site at
        ``child_reports.py:2106``, Variant B idempotency guard at
        ``child_reports.py:1626``) call this to record a
        delivery-obligation marker when the natural completion path
        is blocked. Phase 2's router/sweep will recover the marker
        back to PENDING via
        :meth:`transition_deferred_to_pending` and then complete the
        delivery.

        The partial unique index ``uq_report_injections_oblig_triple``
        (``WHERE state IN ('PENDING','DEFERRED')``) is the write-once
        gate. Two outcomes:

        * **No existing non-terminal row**: insert a fresh DEFERRED
          row with ``report_message_id=None``, ``content=None``.
        * **Concurrent duplicate** (e.g. router vs sweep vs Site 1
          racing the same triple): the partial unique index rejects
          the second INSERT with ``sqlalchemy.exc.IntegrityError``;
          this method absorbs the error (W6 — the child-keyed bus
          lock does NOT serialize the three actors, so the index is
          the only cross-actor gate) and returns ``None`` (no-op).
        * **Existing DEFERRED/PENDING row for the same triple**:
          re-fetch and (only if the ``deferred_reason`` differs) UPDATE
          the reason in-place. Never duplicates; never escalates the
          state.

        Args:
            parent_instance_id: The parent that should eventually
                receive the report.
            child_instance_id: The child that produced the report.
            child_message_id: The child's completed ``message_id``.
            deferred_reason: One of the ``DEFERRED_REASON_*``
                constants in ``daemon.constants`` (UPPERCASE;
                C1 case-lockstep contract — the value written here
                matches the storage enum literal verbatim).

        Returns:
            The persisted :class:`ReportInjection` row when this call
            inserted or updated; ``None`` when the call was a
            no-op (concurrent duplicate absorbed by W6).

        Raises:
            Any non-integrity DB error propagates. ``IntegrityError``
            on the obligation-triple index is the ONLY error caught
            here.
        """
        with Session(self.engine) as session:
            try:
                row = ReportInjection(
                    parent_instance_id=parent_instance_id,
                    child_instance_id=child_instance_id,
                    child_message_id=child_message_id,
                    report_message_id=None,
                    content=None,
                    state=_DEFERRED_STATE,
                    deferred_reason=deferred_reason,
                    delivered_at=None,
                    recovery_attempted_at=None,
                )
                session.add(row)
                session.commit()
                session.refresh(row)
                logger.info(
                    f"[ReportInjection] DEFERRED marker written: "
                    f"parent={parent_instance_id[:8]}..., "
                    f"child={child_instance_id[:8]}..., "
                    f"msg={child_message_id[:8]}..., "
                    f"reason={deferred_reason}"
                )
                return row
            except IntegrityError:
                # W6: concurrent duplicate (router/sweep/Site 1 races
                # the same triple). The child-keyed bus lock does
                # NOT serialize the three actors — this is the only
                # cross-actor gate. Roll back, then update the
                # existing non-terminal row's reason if it differs.
                session.rollback()
                existing = session.exec(
                    select(ReportInjection)
                    .where(
                        ReportInjection.parent_instance_id
                        == parent_instance_id
                    )
                    .where(
                        ReportInjection.child_instance_id
                        == child_instance_id
                    )
                    .where(
                        ReportInjection.child_message_id
                        == child_message_id
                    )
                    .where(
                        ReportInjection.state.in_([
                            _PENDING_STATE,
                            _DEFERRED_STATE,
                        ])
                    )
                ).first()
                if existing is None:
                    # Should not happen: the IntegrityError fired but
                    # the SELECT (post-rollback) finds no non-terminal
                    # row. The terminal rows exist (the index
                    # predicate excludes them), so a race escalated
                    # to terminal between the rolled-back INSERT and
                    # this SELECT. No-op — delivery has happened.
                    logger.info(
                        f"[ReportInjection] ensure_deferred no-op: "
                        f"concurrent terminal race for "
                        f"parent={parent_instance_id[:8]}..., "
                        f"child={child_instance_id[:8]}..., "
                        f"msg={child_message_id[:8]}... "
                        f"(original reason={deferred_reason})"
                    )
                    return None
                if existing.deferred_reason != deferred_reason:
                    logger.info(
                        f"[ReportInjection] ensure_deferred updating "
                        f"reason for existing row "
                        f"injection_id={existing.injection_id[:8]}... "
                        f"{existing.deferred_reason} -> {deferred_reason}"
                    )
                    existing.deferred_reason = deferred_reason
                    session.add(existing)
                    session.commit()
                    session.refresh(existing)
                    return existing
                logger.debug(
                    f"[ReportInjection] ensure_deferred absorbed "
                    f"duplicate for "
                    f"parent={parent_instance_id[:8]}..., "
                    f"child={child_instance_id[:8]}..., "
                    f"msg={child_message_id[:8]}... "
                    f"(reason={deferred_reason})"
                )
                return None

    def find_deferred_for_parent(
        self, parent_instance_id: str
    ) -> list[ReportInjection]:
        """Return all DEFERRED markers awaiting recovery for a parent.

        Phase 2/3 recovery path: router/sweep lists DEFERRED markers
        for a parent and transitions them back to PENDING via
        :meth:`transition_deferred_to_pending`. Ordered by
        ``created_at`` ascending (oldest first) — matches the drain
        order so the recovery sweep is stable across concurrent
        recovery actors.

        Diagnostic / non-hot-path. Used by the Phase 2 router re-entry
        and the Phase 2/3 sweep service.

        Args:
            parent_instance_id: The parent whose DEFERRED markers
                should be listed.

        Returns:
            List of :class:`ReportInjection` rows with
            ``state='DEFERRED'`` for the parent, oldest first. Empty
            list when none.
        """
        with Session(self.engine) as session:
            stmt = (
                select(ReportInjection)
                .where(ReportInjection.parent_instance_id == parent_instance_id)
                .where(ReportInjection.state == _DEFERRED_STATE)
                .order_by(ReportInjection.created_at.asc())
            )
            return list(session.exec(stmt).all())

    def transition_deferred_to_pending(
        self, injection_id: str
    ) -> bool:
        """Atomically transition DEFERRED → PENDING (guarded UPDATE).

        Recovery actor entry point. Stamps ``recovery_attempted_at``
        so the Phase 2 sweep can re-process mid-sweep-crash rows
        (FM-13). Exactly one of the concurrent recovery actors (router
        / sweep / FM-1-guarded path) wins the race; losers see
        ``rowcount=0`` and skip.

        Args:
            injection_id: The :class:`ReportInjection` to recover.

        Returns:
            ``True`` when this call atomically transitioned the row
            from DEFERRED to PENDING (caller OWNS the recovery —
            proceed to the full enqueue). ``False`` when rowcount=0 —
            another actor already recovered the row, or the row was
            never DEFERRED (terminal / missing). The caller MUST skip
            in that case.
        """
        now_iso = self._now_iso()
        with Session(self.engine) as session:
            result = session.execute(
                sa_update(ReportInjection)
                .where(ReportInjection.injection_id == injection_id)
                .where(ReportInjection.state == _DEFERRED_STATE)
                .values(
                    state=_PENDING_STATE,
                    recovery_attempted_at=now_iso,
                )
            )
            if result.rowcount == 0:
                session.rollback()
                return False
            session.commit()
            return True

    # --------------------------------------------------------
    # CLAIM — agent-node drain (live parent turn)
    # --------------------------------------------------------

    def claim_for_injection(
        self, parent_instance_id: str
    ) -> list[dict[str, Any]]:
        """Atomically claim ALL pending reports for a parent.

        Called by the parent's live agent-node (via the
        ``ReportInjectionSlot`` factory-closure handle) right before
        its LLM call, after the user-message injection pull. Drains
        every PENDING report for the parent in one transaction,
        transitions each to ``INJECTED``, and marks the companion
        ``message_queue.completion_report`` rows ``COMPLETED`` so they
        are not counted as pending own-queue work for the parent
        (which would otherwise wedge the parent in
        ``WAITING_CHILDREN``).

        Exactly-once vs the fallback task: the guarded
        ``WHERE state = 'PENDING'`` UPDATE means a report already
        claimed by :meth:`claim_for_task_delivery` (``TASK_DELIVERED``)
        is invisible here, and a report claimed here (``INJECTED``) is
        invisible to the task. The per-instance serialization guard
        further guarantees this method and a fallback task for the
        SAME instance never run concurrently.

        The companion ``message_queue`` UPDATE is guarded by
        ``status = 'ready'``: at drain time the report's
        ``completion_report`` row is still ``ready`` (the fallback
        task — the only other writer — is blocked by the per-instance
        guard while this parent's turn is live). If the row is not
        ``ready`` for any reason (concurrent writer, manual edit), the
        guarded UPDATE is a no-op for that row and the report is
        still delivered via the injection — the message-status update
        is best-effort hygiene, not a correctness requirement.

        Args:
            parent_instance_id: The parent whose pending reports
                should be drained.

        Returns:
            A list of ``{"content": str, "report_message_id": str}``
            dicts for every row transitioned to ``INJECTED``, in
            insertion order (oldest first). Empty list when no pending
            reports exist for the parent.
        """
        now_iso = self._now_iso()
        with Session(self.engine) as session:
            # Single atomic ``UPDATE ... RETURNING``: transitions every
            # PENDING report for this parent to INJECTED and returns
            # ONLY the rows this call actually claimed. This is symmetric
            # with :meth:`claim_for_task_delivery`'s guarded UPDATE — a
            # row concurrently claimed by the fallback task (already
            # non-PENDING) is invisible to RETURNING, so exactly-once
            # holds without relying on the per-instance serialization
            # guard. Both SQLite (>=3.35) and PostgreSQL support
            # RETURNING.
            stmt = (
                sa_update(ReportInjection)
                .where(ReportInjection.parent_instance_id == parent_instance_id)
                .where(ReportInjection.state == _PENDING_STATE)
                .values(state=_INJECTED_STATE, delivered_at=now_iso)
                .returning(
                    ReportInjection.content,
                    ReportInjection.report_message_id,
                    ReportInjection.created_at,
                )
            )
            claimed = list(session.execute(stmt).all())

            if not claimed:
                return []

            report_message_ids = [r.report_message_id for r in claimed]

            # Mark companion message_queue rows COMPLETED so the
            # parent's own-queue pending count does not include
            # already-delivered reports. Best-effort / guarded by
            # ``status = 'ready'`` — see method docstring.
            session.execute(
                sa_update(MessageQueue)
                .where(MessageQueue.message_id.in_(report_message_ids))
                .where(MessageQueue.status == MessageStatus.READY.value)
                .values(status=_MSG_COMPLETED)
            )

            session.commit()

            # Stable delivery order: oldest first. created_at is an ISO
            # timestamp (TEXT); lexicographic sort is chronological for
            # same-format ISO strings.
            claimed.sort(key=lambda r: r.created_at)
            drained = [
                {"content": r.content, "report_message_id": r.report_message_id}
                for r in claimed
            ]

        logger.info(
            f"[ReportInjection] Drained {len(drained)} pending "
            f"report(s) for parent {parent_instance_id[:8]}... "
            f"(marked INJECTED)"
        )
        return drained

    # --------------------------------------------------------
    # CLAIM — fallback PROCESS_REPORT task
    # --------------------------------------------------------

    def claim_for_task_delivery(
        self, report_message_id: str
    ) -> TaskDeliveryClaim:
        """Atomically claim a single report for fallback task delivery.

        Called by ``ProcessMessageProcessor`` at the start of a
        ``process_report`` task, before the normal message-processing
        pipeline runs. Returns a :class:`TaskDeliveryClaim` tri-state so
        the caller can distinguish three outcomes:

        * ``"claimed"`` — a PENDING row existed and was atomically
          transitioned to ``TASK_DELIVERED``; the task OWNS delivery and
          proceeds normally.
        * ``"already_delivered"`` — a row exists but is terminal
          (``INJECTED`` by the live agent-node drain, or
          ``TASK_DELIVERED`` by a prior run); the task MUST skip
          (dedup gate).
        * ``"missing"`` — no ``report_injections`` row exists for this
          ``report_message_id`` (a ``PROCESS_REPORT`` task from older
          code, or a path that did not enqueue); the task MUST proceed
          with normal delivery so the report is NOT lost.

        The ``"missing"`` distinction is critical: conflating it with
        ``"already_delivered"`` would silently drop reports for any
        task without a companion row. ``report_injections`` rows are
        created in the SAME transaction as the ``PROCESS_REPORT`` task
        (see ``child_reports``), so the live path always has a row;
        ``"missing"`` covers upgrade / legacy / future code paths.

        Exactly-once: the guarded ``WHERE state = 'PENDING'`` UPDATE
        means only one caller (this method or the injection drain) can
        transition a given row out of PENDING.

        Args:
            report_message_id: The ``message_id`` of the companion
                ``completion_report`` row this task is responsible for.

        Returns:
            A :class:`TaskDeliveryClaim` with ``status`` in
            ``{"claimed", "already_delivered", "missing"}`` and ``row``
            set only on ``"claimed"``.
        """
        now_iso = self._now_iso()
        with Session(self.engine) as session:
            # Does ANY row exist for this report (any state)? This
            # distinguishes "missing" (no row ever created) from
            # "already_delivered" (row exists but terminal). Rows are
            # created transactionally with the task, so for the live
            # path a row always exists by the time the task runs; the
            # missing case is upgrade/legacy.
            any_row = session.exec(
                select(ReportInjection).where(
                    ReportInjection.report_message_id == report_message_id
                )
            ).first()
            if any_row is None:
                return TaskDeliveryClaim("missing", None)

            # Guarded transition — only PENDING → TASK_DELIVERED. A row
            # that is already INJECTED/TASK_DELIVERED yields rowcount 0.
            result = session.execute(
                sa_update(ReportInjection)
                .where(ReportInjection.injection_id == any_row.injection_id)
                .where(ReportInjection.state == _PENDING_STATE)
                .values(state=_TASK_DELIVERED_STATE, delivered_at=now_iso)
            )
            if result.rowcount == 0:
                # Row exists but is terminal — the live turn (or a
                # prior task run) already delivered this report.
                return TaskDeliveryClaim("already_delivered", None)

            session.commit()
            session.refresh(any_row)
            logger.info(
                f"[ReportInjection] Report {report_message_id[:8]}... "
                f"claimed for TASK delivery (parent="
                f"{any_row.parent_instance_id[:8]}...)"
            )
            return TaskDeliveryClaim("claimed", any_row)

    # --------------------------------------------------------
    # DIAGNOSTIC
    # --------------------------------------------------------

    def count_pending_for_parent(self, parent_instance_id: str) -> int:
        """Return the number of delivery-owed reports for a parent.

        Phase 1 (pause-report-recovery): the count is broadened from
        ``PENDING`` only to ``PENDING ∪ DEFERRED`` — a DEFERRED marker
        is an outstanding delivery obligation (the recovery sweep
        will turn it back to PENDING). Counting only PENDING would
        understate the delivery backlog and could mislead
        observability / idle-gate decisions.

        Diagnostic helper (observability / tests). Not used on the
        hot path.

        Args:
            parent_instance_id: The parent to query.

        Returns:
            Non-negative count of PENDING ∪ DEFERRED report-injection
            rows for the parent.
        """
        from sqlalchemy import func

        with Session(self.engine) as session:
            stmt = (
                select(func.count())
                .select_from(ReportInjection)
                .where(ReportInjection.parent_instance_id == parent_instance_id)
                .where(
                    ReportInjection.state.in_([
                        _PENDING_STATE,
                        _DEFERRED_STATE,
                    ])
                )
            )
            return int(session.exec(stmt).one() or 0)
