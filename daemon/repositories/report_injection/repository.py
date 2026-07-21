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
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import update as sa_update
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from ..message_queue.models import MessageQueue, MessageStatus
from .models import ReportInjection, ReportInjectionState

logger = logging.getLogger(__name__)


# Module-level literals captured once to avoid re-evaluating the enums
# on every claim call.
_PENDING_STATE: str = ReportInjectionState.PENDING.value
_INJECTED_STATE: str = ReportInjectionState.INJECTED.value
_TASK_DELIVERED_STATE: str = ReportInjectionState.TASK_DELIVERED.value
_MSG_COMPLETED: str = MessageStatus.COMPLETED.value


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
        """Insert a new PENDING report-injection row.

        Production enqueue happens INLINE in
        ``child_reports._process_child_completion_db_sync`` (same
        ``WriteGuardSession`` transaction as the ``message_queue``
        row + ``PROCESS_REPORT`` task) for crash-consistency. This
        method exists for tests and for any caller that does not
        already hold a session.

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
        drained: list[dict[str, Any]] = []
        with Session(self.engine) as session:
            # Read PENDING rows for this parent, oldest first (stable
            # delivery order so the parent sees reports in the order
            # workers completed). create_at is an ISO timestamp; the
            # primary-key UUID4 is monotonic-ish but create_at is the
            # intended ordering signal.
            stmt = (
                select(ReportInjection)
                .where(ReportInjection.parent_instance_id == parent_instance_id)
                .where(ReportInjection.state == _PENDING_STATE)
                .order_by(ReportInjection.created_at)
            )
            rows = list(session.exec(stmt))
            if not rows:
                return drained

            report_message_ids = [r.report_message_id for r in rows]

            # Mark each row INJECTED (guarded Core UPDATE — only
            # PENDING rows transition; a concurrent task-claim that
            # raced ahead leaves its row untouched here).
            session.execute(
                sa_update(ReportInjection)
                .where(ReportInjection.injection_id.in_([r.injection_id for r in rows]))
                .where(ReportInjection.state == _PENDING_STATE)
                .values(state=_INJECTED_STATE, delivered_at=now_iso)
            )

            # Mark companion message_queue rows COMPLETED so the
            # parent's own-queue pending count does not include
            # already-delivered reports. Best-effort / guarded — see
            # method docstring.
            session.execute(
                sa_update(MessageQueue)
                .where(MessageQueue.message_id.in_(report_message_ids))
                .where(MessageQueue.status == MessageStatus.READY.value)
                .values(status=_MSG_COMPLETED)
            )

            session.commit()

            for r in rows:
                drained.append(
                    {
                        "content": r.content,
                        "report_message_id": r.report_message_id,
                    }
                )

        if drained:
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
    ) -> ReportInjection | None:
        """Atomically claim a single report for fallback task delivery.

        Called by ``ProcessMessageProcessor`` at the start of a
        ``process_report`` task, before the normal message-processing
        pipeline runs. If this call returns a row, the task OWNS
        delivery and proceeds normally. If it returns ``None``, the
        report was already delivered by the injection path (the
        parent's live agent-node drained it) and the task MUST skip
        — calling this method is the dedup gate.

        Exactly-once: the guarded ``WHERE state = 'PENDING'`` UPDATE
        means only one caller (this method or the injection drain)
        can transition a given row out of PENDING. The loser sees
        ``None`` (no PENDING row matched) and skips.

        Args:
            report_message_id: The ``message_id`` of the companion
                ``completion_report`` row this task is responsible
                for.

        Returns:
            The claimed :class:`ReportInjection` row (now
            ``TASK_DELIVERED``) if this call won the race, or ``None``
            if the row was already terminal (injected by the live
            turn, or already delivered by a prior task run).
        """
        now_iso = self._now_iso()
        with Session(self.engine) as session:
            stmt = (
                select(ReportInjection)
                .where(ReportInjection.report_message_id == report_message_id)
                .where(ReportInjection.state == _PENDING_STATE)
            )
            row = session.exec(stmt).first()
            if row is None:
                return None

            # Guarded transition — only PENDING → TASK_DELIVERED.
            result = session.execute(
                sa_update(ReportInjection)
                .where(ReportInjection.injection_id == row.injection_id)
                .where(ReportInjection.state == _PENDING_STATE)
                .values(state=_TASK_DELIVERED_STATE, delivered_at=now_iso)
            )
            if result.rowcount == 0:
                # Lost the race to a concurrent injection drain
                # between the SELECT and the UPDATE — treat as
                # already-delivered.
                return None

            session.commit()
            session.refresh(row)
            logger.info(
                f"[ReportInjection] Report {report_message_id[:8]}... "
                f"claimed for TASK delivery (parent="
                f"{row.parent_instance_id[:8]}...)"
            )
            return row

    # --------------------------------------------------------
    # DIAGNOSTIC
    # --------------------------------------------------------

    def count_pending_for_parent(self, parent_instance_id: str) -> int:
        """Return the number of PENDING reports for a parent.

        Diagnostic helper (observability / tests). Not used on the
        hot path.

        Args:
            parent_instance_id: The parent to query.

        Returns:
            Non-negative count of PENDING report-injection rows.
        """
        from sqlalchemy import func

        with Session(self.engine) as session:
            stmt = (
                select(func.count())
                .select_from(ReportInjection)
                .where(ReportInjection.parent_instance_id == parent_instance_id)
                .where(ReportInjection.state == _PENDING_STATE)
            )
            return int(session.exec(stmt).one() or 0)
