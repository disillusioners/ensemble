"""The ``instance_ops_audit`` writer — the TWO-TIER audit contract (D-15).

The writer exposes THREE methods, one per tier (plus the two
single-transaction convenience writers for previews and refusals):

======================  ======  =============================================
Method                  Tier    Semantics
======================  ======  =============================================
``write_audit_and_act``   1     The ``INSERT`` joins the ACT's own
                                  transaction. Any exception — INCLUDING
                                  the ``INSERT`` failing — rolls back
                                  BOTH. A destructive act that cannot be
                                  audited NEVER happens (AC-9a).
``write_audit_ahead``      2     ``INSERT FIRST`` in its own transaction
                                  with ``outcome="pending"`` and the full
                                  intent. Returns the ``row_id``.
``settle_audit``           2     ``UPDATE`` the pending row to
                                  ``"committed"`` / ``"error"``. A crash
                                  between the two leaves the ``pending``
                                  row durable (AC-9b).
======================  ======  =============================================

Two single-transaction convenience writers round the contract out:
``write_preview`` (``dry_run=True`` + ``outcome="previewed"``) and
``write_refusal`` (``outcome="refused"`` + the refusal text) — the
ledger records ATTEMPTS, not just successes.

Why two tiers (D-15, restated): the original uniform "the act and its
audit row commit together" claim was PARTLY UNSOUND. The ens-db lane
gets away with it because the ens-db act IS a SQL statement in the
tool's own session. ``instance_ops`` acts are daemon SERVICE CALLS
that own their own transactions — and ``instance_terminate``'s DB half
runs on a worker thread via ``asyncio.to_thread``, where a SQLAlchemy
Session cannot be shared. Threading a session into the service and
moving the transaction boundary into the tool are both dead ends; the
in-service ``audit_sink`` hook is the only Tier-1 shape for terminate.

The honest rule that survives: **an act that can be neither
transaction-coupled NOR honestly write-ahead must be EXCLUDED from the
category, not silently de-coupled.** Every act shipping now satisfies
one of the two tiers, and the mapping is declared in the model
docstring, the DDL comment and this module's docstring.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import Engine, func
from sqlmodel import Session, select

from .models import InstanceOpsAudit

logger = logging.getLogger(__name__)

#: The five outcome values of the ledger.
OUTCOME_PREVIEWED = "previewed"
OUTCOME_PENDING = "pending"
OUTCOME_COMMITTED = "committed"
OUTCOME_REFUSED = "refused"
OUTCOME_ERROR = "error"

#: The action name of the destructive act — the budget rail counts
#: rows with THIS action (D-16).
ACTION_TERMINATE = "instance_terminate"


class InstanceOpsAuditRepository:
    """Writer + readers for the ``instance_ops_audit`` ledger.

    Constructed with the daemon engine (the same engine the instance
    repository uses). Every method NEVER raises for an expected
    failure — the audit-write failure is surfaced to the caller as an
    exception so the TIER-1 caller can roll the act back (that
    fail-closed behaviour is the POINT of the Tier-1 contract), while
    the read-side helpers are defensive and return safe defaults.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # ─────────────────────────────────────────────────────────────────
    # TIER 1 — the INSERT joins the act's own transaction
    # ─────────────────────────────────────────────────────────────────
    def write_audit_and_act(
        self,
        session: Session,
        *,
        action: str,
        target: str,
        reason: str = "",
        dry_run: bool = False,
        before: Any | None = None,
        after: Any | None = None,
        outcome: str = OUTCOME_COMMITTED,
        error_message: str | None = None,
        actor_instance_id: str | None = None,
        actor_agent_id: str | None = None,
        confirmed_target_id: str | None = None,
        provenance_class: str | None = None,
        provenance_depth: int | None = None,
        root_source: str | None = None,
        root_message_id: str | None = None,
        preview_run_id: str | None = None,
        act: Callable[[Session], None] | None = None,
    ) -> str:
        """INSERT the audit row and run ``act`` inside ``session``.

        The caller OWNS the session and its transaction. This method
        only ``add``s the row and flushes it — the caller's commit
        (or rollback) decides the outcome. The act callable receives
        the SAME session, so a failure in either direction rolls both
        back (AC-9a).

        Args:
            session: The caller's session (the act's own session).
            act: Optional callable invoked with ``session`` AFTER the
                audit row is added. Any exception it raises propagates
                and the caller's transaction rolls back both.

        Returns:
            The audit row id.
        """
        row = self._build_row(
            action=action,
            target=target,
            reason=reason,
            dry_run=dry_run,
            before=before,
            after=after,
            outcome=outcome,
            error_message=error_message,
            actor_instance_id=actor_instance_id,
            actor_agent_id=actor_agent_id,
            confirmed_target_id=confirmed_target_id,
            provenance_class=provenance_class,
            provenance_depth=provenance_depth,
            root_source=root_source,
            root_message_id=root_message_id,
            preview_run_id=preview_run_id,
        )
        session.add(row)
        # Flush so an INSERT failure surfaces HERE (inside the act's
        # transaction) rather than at the caller's commit — the rollback
        # must cover both halves.
        session.flush()
        if act is not None:
            act(session)
        return row.id

    # ─────────────────────────────────────────────────────────────────
    # TIER 2 — write-ahead
    # ─────────────────────────────────────────────────────────────────
    def write_audit_ahead(
        self,
        *,
        action: str,
        target: str,
        reason: str = "",
        before: Any | None = None,
        actor_instance_id: str | None = None,
        actor_agent_id: str | None = None,
        confirmed_target_id: str | None = None,
        provenance_class: str | None = None,
        provenance_depth: int | None = None,
        root_source: str | None = None,
        root_message_id: str | None = None,
        preview_run_id: str | None = None,
    ) -> str:
        """INSERT the intent FIRST, with ``outcome="pending"``.

        The act runs AFTER this returns, and the row is settled by
        :meth:`settle_audit`. A crash between the two leaves a durable
        ``pending`` row — the ledger always contains the intent.
        """
        row = self._build_row(
            action=action,
            target=target,
            reason=reason,
            dry_run=False,
            before=before,
            after=None,
            outcome=OUTCOME_PENDING,
            error_message=None,
            actor_instance_id=actor_instance_id,
            actor_agent_id=actor_agent_id,
            confirmed_target_id=confirmed_target_id,
            provenance_class=provenance_class,
            provenance_depth=provenance_depth,
            root_source=root_source,
            root_message_id=root_message_id,
            preview_run_id=preview_run_id,
        )
        with Session(self._engine) as session:
            session.add(row)
            session.commit()
            # Capture the id INSIDE the session — commit expires the row's
            # attributes and a post-close read raises DetachedInstanceError.
            row_id = row.id
        return row_id

    def settle_audit(
        self,
        row_id: str,
        outcome: str,
        error_message: str | None = None,
        after: Any | None = None,
    ) -> None:
        """Settle a pending row to ``"committed"`` / ``"error"``.

        Defensive: a settle failure is logged, never raised — the row
        stays ``pending`` and the reconciliation surface
        (``fleet_capacity_summary.audit_pending``) surfaces it.
        """
        try:
            with Session(self._engine) as session:
                row = session.get(InstanceOpsAudit, row_id)
                if row is None:
                    logger.warning(
                        "instance_ops_audit: settle target %s not found", row_id
                    )
                    return
                row.outcome = outcome
                if error_message is not None:
                    row.error_message = error_message[:512]
                if after is not None:
                    row.after_snapshot = after
                session.add(row)
                session.commit()
        except Exception as exc:  # noqa: BLE001 — settle is best-effort
            logger.error(
                "instance_ops_audit: settle failed for row %s: %s: %s",
                row_id,
                type(exc).__name__,
                exc,
            )

    # ─────────────────────────────────────────────────────────────────
    # Single-transaction convenience writers
    # ─────────────────────────────────────────────────────────────────
    def write_preview(
        self,
        *,
        action: str,
        target: str,
        reason: str = "",
        before: Any | None = None,
        actor_instance_id: str | None = None,
        actor_agent_id: str | None = None,
        confirmed_target_id: str | None = None,
        provenance_class: str | None = None,
        provenance_depth: int | None = None,
        root_source: str | None = None,
        root_message_id: str | None = None,
    ) -> str:
        """Write a dry-run preview row (``dry_run=True`` + ``previewed``)."""
        row = self._build_row(
            action=action,
            target=target,
            reason=reason,
            dry_run=True,
            before=before,
            after=None,
            outcome=OUTCOME_PREVIEWED,
            error_message=None,
            actor_instance_id=actor_instance_id,
            actor_agent_id=actor_agent_id,
            confirmed_target_id=confirmed_target_id,
            provenance_class=provenance_class,
            provenance_depth=provenance_depth,
            root_source=root_source,
            root_message_id=root_message_id,
            preview_run_id=None,
        )
        with Session(self._engine) as session:
            session.add(row)
            session.commit()
            row_id = row.id
        return row_id

    def write_refusal(
        self,
        *,
        action: str,
        target: str,
        reason: str = "",
        error_message: str,
        before: Any | None = None,
        actor_instance_id: str | None = None,
        actor_agent_id: str | None = None,
        confirmed_target_id: str | None = None,
        provenance_class: str | None = None,
        provenance_depth: int | None = None,
        root_source: str | None = None,
        root_message_id: str | None = None,
    ) -> str:
        """Write a refusal row (``outcome="refused"`` + the refusal text)."""
        row = self._build_row(
            action=action,
            target=target,
            reason=reason,
            dry_run=False,
            before=before,
            after=None,
            outcome=OUTCOME_REFUSED,
            error_message=error_message,
            actor_instance_id=actor_instance_id,
            actor_agent_id=actor_agent_id,
            confirmed_target_id=confirmed_target_id,
            provenance_class=provenance_class,
            provenance_depth=provenance_depth,
            root_source=root_source,
            root_message_id=root_message_id,
            preview_run_id=None,
        )
        with Session(self._engine) as session:
            session.add(row)
            session.commit()
            row_id = row.id
        return row_id

    # ─────────────────────────────────────────────────────────────────
    # Readers — the budget rail (D-16) and the reconciliation surface
    # ─────────────────────────────────────────────────────────────────
    def count_committed_terminates(self, *, hours: int = 24) -> int:
        """Committed terminates in the trailing window (D-16 budget)."""
        cutoff = self._cutoff_iso(hours)
        try:
            with Session(self._engine) as session:
                stmt = select(func.count()).select_from(InstanceOpsAudit).where(
                    InstanceOpsAudit.action == ACTION_TERMINATE,
                    InstanceOpsAudit.outcome == OUTCOME_COMMITTED,
                    InstanceOpsAudit.created_at > cutoff,
                )
                return int(session.exec(stmt).one())
        except Exception as exc:  # noqa: BLE001 — the rail fails OPEN
            logger.error(
                "instance_ops_audit: terminate budget count failed: %s: %s",
                type(exc).__name__,
                exc,
            )
            return 0

    def count_committed_protected_terminates(self, *, hours: int = 24) -> int:
        """Committed PROTECTED-target terminates in the trailing window.

        The protected determination is carried in the row's
        ``before_snapshot["protected"]`` flag (the verdict block's
        protected determination) — no extra column, and the count is
        dialect-agnostic because the rows are read in Python. The
        window is bounded by the budget itself (a handful of rows).
        """
        cutoff = self._cutoff_iso(hours)
        try:
            with Session(self._engine) as session:
                stmt = select(InstanceOpsAudit).where(
                    InstanceOpsAudit.action == ACTION_TERMINATE,
                    InstanceOpsAudit.outcome == OUTCOME_COMMITTED,
                    InstanceOpsAudit.created_at > cutoff,
                )
                rows = list(session.exec(stmt).all())
        except Exception as exc:  # noqa: BLE001 — the rail fails OPEN
            logger.error(
                "instance_ops_audit: protected terminate count failed: %s: %s",
                type(exc).__name__,
                exc,
            )
            return 0
        return sum(1 for row in rows if self._row_is_protected(row))

    def count_pending(self) -> int:
        """Rows still sitting at ``outcome="pending"`` (W4)."""
        try:
            with Session(self._engine) as session:
                stmt = select(func.count()).select_from(InstanceOpsAudit).where(
                    InstanceOpsAudit.outcome == OUTCOME_PENDING
                )
                return int(session.exec(stmt).one())
        except Exception as exc:  # noqa: BLE001 — defensive
            logger.error(
                "instance_ops_audit: pending count failed: %s: %s",
                type(exc).__name__,
                exc,
            )
            return 0

    def oldest_pending_age_seconds(self) -> int | None:
        """Age of the OLDEST ``pending`` row, or ``None`` when none."""
        try:
            with Session(self._engine) as session:
                stmt = select(InstanceOpsAudit.created_at).where(
                    InstanceOpsAudit.outcome == OUTCOME_PENDING
                ).order_by(InstanceOpsAudit.created_at.asc()).limit(1)
                created_at = session.exec(stmt).first()
        except Exception as exc:  # noqa: BLE001 — defensive
            logger.error(
                "instance_ops_audit: oldest pending read failed: %s: %s",
                type(exc).__name__,
                exc,
            )
            return None
        if not created_at:
            return None
        return self._age_seconds(created_at)

    # ─────────────────────────────────────────────────────────────────
    # Internals
    # ─────────────────────────────────────────────────────────────────
    @staticmethod
    def _row_is_protected(row: InstanceOpsAudit) -> bool:
        snapshot = row.before_snapshot
        if not isinstance(snapshot, dict):
            return False
        return snapshot.get("protected") is True

    @staticmethod
    def _cutoff_iso(hours: int) -> str:
        return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

    @staticmethod
    def _age_seconds(created_at: str) -> int | None:
        try:
            stamp = datetime.fromisoformat(created_at)
        except (TypeError, ValueError):
            return None
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - stamp
        return max(0, int(delta.total_seconds()))

    def _build_row(
        self,
        *,
        action: str,
        target: str,
        reason: str,
        dry_run: bool,
        before: Any | None,
        after: Any | None,
        outcome: str,
        error_message: str | None,
        actor_instance_id: str | None,
        actor_agent_id: str | None,
        confirmed_target_id: str | None,
        provenance_class: str | None,
        provenance_depth: int | None,
        root_source: str | None,
        root_message_id: str | None,
        preview_run_id: str | None,
    ) -> InstanceOpsAudit:
        return InstanceOpsAudit(
            action=action,
            target=target,
            reason=(reason or "")[:1024],
            dry_run=dry_run,
            before_snapshot=before,
            after_snapshot=after,
            outcome=outcome,
            error_message=error_message[:512] if error_message else None,
            created_by_instance_id=actor_instance_id,
            created_by_agent_id=actor_agent_id,
            confirmed_target_id=confirmed_target_id,
            provenance_class=provenance_class,
            provenance_depth=provenance_depth,
            root_source=root_source,
            root_message_id=root_message_id,
            preview_run_id=preview_run_id,
        )
