"""Synchronous repository for the pending-experience queue (C3).

Phase 2 of the Project Blueprint evolution. Implements the durable
claim/acknowledge contract that the blueprinter worker uses to
drain accumulated experience/history/manual rows without double
processing.

Contract
--------

* ``enqueue``         — write a new row, status ``available``.
* ``claim_batch``     — atomically grab the N oldest ``available`` /
                        ``retryable`` rows for a project and stamp
                        the ``run_token`` on them. Returns the
                        claimed rows.
* ``acknowledge_batch`` — mark rows as ``applied`` (sets
                        ``processed_at``). Scoped to the caller's
                        ``run_token`` so concurrent claims cannot
                        accidentally ack each other's rows.
* ``abandon_batch``   — mark rows as ``abandoned`` (dead-letter)
                        for records that cannot be processed.
* ``get_pending_count`` — count of active rows (the smart-scan
                        trigger threshold).
* ``prune_processed``  — hard-delete rows whose ``processed_at`` is
                        older than N days (crash recovery for the
                        soft-delete).
* ``prune_excess``     — cap per-project pending rows (FIFO on
                        unprocessed rows).

The claim/acknowledge contract is atomic per row (single Session
transaction). Two concurrent workers calling ``claim_batch`` are
guaranteed to claim disjoint sets as long as the oldest-first
ordering holds; this is the standard SQL "claim by update order"
pattern, intentionally simple for the queue's current scale.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func, text, update
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from .pending_models import (
    DEFAULT_LEASE_TIMEOUT_MINUTES,
    DEFAULT_MAX_RETRIES,
    PENDING_ACTIVE_STATUSES,
    PENDING_CLAIMABLE_STATUSES,
    PENDING_STATUS_ABANDONED,
    PENDING_STATUS_APPLIED,
    PENDING_STATUS_AVAILABLE,
    PENDING_STATUS_CLAIMED,
    PENDING_STATUS_RETRYABLE,
    BlueprintPendingUpdate,
)

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class BlueprintPendingRepository:
    """Synchronous CRUD over ``project_blueprint_pending_updates``.

    The class is constructed once at startup with the shared engine
    and lives for the lifetime of the daemon. All public methods
    open their own short-lived ``Session`` — there is no
    instance-level state, so the repo is safe to share across
    threads.
    """

    def __init__(self, engine: Engine):
        self.engine = engine

    # ── Writes ────────────────────────────────────────────────────

    def enqueue(
        self,
        project_id: str,
        source_type: str,
        source_payload: dict[str, Any],
    ) -> BlueprintPendingUpdate:
        """INSERT a new pending update with status ``available``.

        ``source_payload`` is preserved verbatim so the blueprinter
        can read the original event without joining against the
        source table.
        """
        record = BlueprintPendingUpdate(
            project_id=project_id,
            source_type=source_type,
            source_payload=source_payload,
            status=PENDING_STATUS_AVAILABLE,
        )
        with Session(self.engine) as session:
            session.add(record)
            session.commit()
            session.refresh(record)
        return record

    # ── Claim / acknowledge (C3) ──────────────────────────────────

    def claim_batch(
        self,
        project_id: str,
        batch_size: int = 50,
        run_token: str = "",
    ) -> list[BlueprintPendingUpdate]:
        """Atomically claim the N oldest ``available`` / ``retryable`` rows.

        Sets ``status='claimed'``, ``claimed_at=now``, ``run_token``
        and increments ``retry_count`` for rows that came from
        ``retryable``. Returns the claimed rows in oldest-first order.

        If fewer than ``batch_size`` rows are available, returns
        whatever is claimable. The whole claim is a single Session
        transaction — concurrent callers will see disjoint sets as
        long as the oldest-first ordering is preserved.
        """
        now = _now_iso()
        with Session(self.engine) as session:
            # Pick the IDs in oldest-first order so the subsequent
            # UPDATE is deterministic. The flow is two steps within
            # ONE Session transaction:
            #   1. SELECT the N oldest claimable IDs (oldest-first).
            #   2. UPDATE those rows: SET status='claimed',
            #      run_token, claimed_at; CASE on retry_count.
            # The UPDATE's WHERE clause re-checks status IN (available,
            # retryable) so concurrent callers claim disjoint sets.
            subq = (
                select(BlueprintPendingUpdate.id)
                .where(BlueprintPendingUpdate.project_id == project_id)
                .where(col(BlueprintPendingUpdate.status).in_(PENDING_CLAIMABLE_STATUSES))
                .order_by(col(BlueprintPendingUpdate.created_at).asc())
                .limit(batch_size)
            )
            ids = [row for row in session.exec(subq).all()]

            if not ids:
                return []

            # Single UPDATE statement, atomic across all rows.
            # The CASE expression increments retry_count only for
            # rows that were already 'retryable' (avoids inflating
            # the counter on first-claim rows). The expanding bind
            # keeps the IN-list dialect-portable (SQLite + PostgreSQL).
            from sqlalchemy import bindparam

            stmt = text(
                "UPDATE project_blueprint_pending_updates "
                "SET status = :claimed, "
                "    run_token = :token, "
                "    claimed_at = :now, "
                "    retry_count = CASE "
                "        WHEN status = :retryable THEN retry_count + 1 "
                "        ELSE retry_count "
                "    END "
                "WHERE id IN ("
                "    SELECT id FROM project_blueprint_pending_updates "
                "    WHERE id IN :ids "
                "      AND status IN (:available, :retryable) "
                "    ORDER BY created_at ASC "
                "    LIMIT :batch_size"
                ") "
                "AND status IN (:available, :retryable)"
            ).bindparams(bindparam("ids", expanding=True))

            session.execute(
                stmt,
                {
                    "claimed": PENDING_STATUS_CLAIMED,
                    "token": run_token,
                    "now": now,
                    "retryable": PENDING_STATUS_RETRYABLE,
                    "available": PENDING_STATUS_AVAILABLE,
                    "ids": ids,
                    "batch_size": batch_size,
                },
            )
            session.commit()

            # Read back ONLY rows this caller actually claimed (run_token +
            # status guard). Without these filters, a concurrent caller
            # could see phantom rows stamped with the other caller's
            # token under PostgreSQL READ COMMITTED isolation.
            claimed = list(
                session.exec(
                    select(BlueprintPendingUpdate)
                    .where(col(BlueprintPendingUpdate.id).in_(ids))
                    .where(BlueprintPendingUpdate.run_token == run_token)
                    .where(BlueprintPendingUpdate.status == PENDING_STATUS_CLAIMED)
                    .order_by(col(BlueprintPendingUpdate.created_at).asc())
                )
            )
        return claimed

    def acknowledge_batch(
        self,
        run_token: str,
        record_ids: Optional[list[str]] = None,
    ) -> int:
        """Mark rows as ``applied`` (sets ``processed_at``).

        If ``record_ids`` is ``None``, ack every row holding the
        ``run_token``. If a list is provided, only those IDs are
        considered (the rest stay ``claimed`` until lease timeout).

        Scoped to ``run_token``: rows owned by a different token are
        left untouched.

        Returns the count acknowledged.
        """
        now = _now_iso()
        with Session(self.engine) as session:
            stmt = (
                update(BlueprintPendingUpdate)
                .where(BlueprintPendingUpdate.run_token == run_token)
                .where(BlueprintPendingUpdate.status == PENDING_STATUS_CLAIMED)
                .values(status=PENDING_STATUS_APPLIED, processed_at=now)
            )
            if record_ids is not None:
                stmt = stmt.where(col(BlueprintPendingUpdate.id).in_(record_ids))
            result = session.execute(stmt)
            session.commit()
            return int(result.rowcount or 0)

    def abandon_batch(
        self,
        run_token: str,
        reason: str = "",
    ) -> int:
        """Mark rows as ``abandoned`` (dead-letter).

        Used when the worker cannot process a batch and wants to
        prevent retry. Stays scoped to ``run_token`` for consistency
        with ``acknowledge_batch``.
        """
        with Session(self.engine) as session:
            stmt = (
                update(BlueprintPendingUpdate)
                .where(BlueprintPendingUpdate.run_token == run_token)
                .where(
                    col(BlueprintPendingUpdate.status).in_(
                        (PENDING_STATUS_CLAIMED, PENDING_STATUS_RETRYABLE)
                    )
                )
                .values(status=PENDING_STATUS_ABANDONED)
            )
            result = session.execute(stmt)
            session.commit()
            count = int(result.rowcount or 0)
        if count and reason:
            logger.warning(
                "Abandoned %d pending update(s) (run_token=%s): %s",
                count, run_token, reason,
            )
        return count

    # ── Counts / lists ────────────────────────────────────────────

    def get_pending_count(self, project_id: str) -> int:
        """Count of active rows for a project (the smart-scan trigger).

        Returns the count of rows with status IN
        (``available``, ``retryable``). This is the trigger
        predicate the blueprinter scans for before deciding to
        schedule a batch.
        """
        with Session(self.engine) as session:
            stmt = (
                select(func.count())
                .select_from(BlueprintPendingUpdate)
                .where(BlueprintPendingUpdate.project_id == project_id)
                .where(
                    col(BlueprintPendingUpdate.status).in_(PENDING_ACTIVE_STATUSES)
                )
            )
            return session.exec(stmt).one()

    def list_pending(
        self,
        project_id: str,
        limit: int = 100,
    ) -> list[BlueprintPendingUpdate]:
        """Active rows for a project, oldest-first."""
        with Session(self.engine) as session:
            return list(
                session.exec(
                    select(BlueprintPendingUpdate)
                    .where(BlueprintPendingUpdate.project_id == project_id)
                    .where(
                        col(BlueprintPendingUpdate.status).in_(PENDING_ACTIVE_STATUSES)
                    )
                    .order_by(col(BlueprintPendingUpdate.created_at).asc())
                    .limit(limit)
                )
            )

    def get_by_id(self, record_id: str) -> Optional[BlueprintPendingUpdate]:
        with Session(self.engine) as session:
            return session.get(BlueprintPendingUpdate, record_id)

    def get_pending_records(
        self, record_ids: list[str]
    ) -> list[BlueprintPendingUpdate]:
        """Fetch full rows for a list of IDs (used by the blueprinter
        to read the claimed batch)."""
        if not record_ids:
            return []
        with Session(self.engine) as session:
            return list(
                session.exec(
                    select(BlueprintPendingUpdate).where(
                        col(BlueprintPendingUpdate.id).in_(record_ids)
                    )
                )
            )

    # ── Lease timeout / retry transitions (C3) ────────────────────

    def mark_retryable(
        self,
        lease_timeout_minutes: float = DEFAULT_LEASE_TIMEOUT_MINUTES,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> int:
        """Sweep ``claimed`` rows whose lease has expired.

        Transition: ``claimed`` → ``retryable`` (increment
        ``retry_count``) when ``claimed_at`` is older than the lease
        timeout. Rows whose ``retry_count`` already meets
        ``max_retries`` go to ``abandoned`` instead.

        Returns the number of rows transitioned.
        """
        # SQLite/PostgreSQL both support ISO-string comparison here
        # only because we always store UTC ISO strings (the same
        # format on the way in). If we ever switch to a real
        # timestamp type, this comparison becomes a real datetime.
        threshold = (
            datetime.now(timezone.utc) - timedelta(minutes=lease_timeout_minutes)
        ).isoformat()
        with Session(self.engine) as session:
            # First: rows that have hit the retry cap → abandoned.
            abandoned_rows = list(
                session.exec(
                    select(BlueprintPendingUpdate)
                    .where(BlueprintPendingUpdate.status == PENDING_STATUS_CLAIMED)
                    .where(BlueprintPendingUpdate.claimed_at < threshold)
                    .where(BlueprintPendingUpdate.retry_count >= max_retries)
                )
            )
            for row in abandoned_rows:
                row.status = PENDING_STATUS_ABANDONED
                session.add(row)

            # Second: rows that have lease-expired but still under
            # the retry cap → retryable.
            retryable_rows = list(
                session.exec(
                    select(BlueprintPendingUpdate)
                    .where(BlueprintPendingUpdate.status == PENDING_STATUS_CLAIMED)
                    .where(BlueprintPendingUpdate.claimed_at < threshold)
                    .where(BlueprintPendingUpdate.retry_count < max_retries)
                )
            )
            for row in retryable_rows:
                row.status = PENDING_STATUS_RETRYABLE
                row.run_token = None
                row.claimed_at = None
                # NOTE: retry_count is incremented at the next
                # claim_batch for the same row, so the counter
                # reflects the number of *retries* (not the current
                # attempt). This keeps the meter stable across the
                # duration of a claim.
                session.add(row)

            session.commit()
            transition_count = len(abandoned_rows) + len(retryable_rows)
        if transition_count:
            logger.info(
                "mark_retryable: %d rows transitioned (abandoned=%d, retryable=%d, threshold=%s)",
                transition_count, len(abandoned_rows), len(retryable_rows), threshold,
            )
        return transition_count

    # ── Cleanup / pruning ─────────────────────────────────────────

    def prune_processed(
        self,
        project_id: str,
        older_than_days: int = 7,
    ) -> int:
        """Hard-delete rows whose ``processed_at`` is older than N days.

        Crash-recovery cleanup for the soft-delete pattern. Returns
        the count deleted.
        """
        threshold = (
            datetime.now(timezone.utc) - timedelta(days=older_than_days)
        ).isoformat()
        with Session(self.engine) as session:
            stmt = (
                text(
                    "DELETE FROM project_blueprint_pending_updates "
                    "WHERE project_id = :project_id "
                    "AND processed_at IS NOT NULL "
                    "AND processed_at < :threshold"
                )
            )
            result = session.execute(
                stmt, {"project_id": project_id, "threshold": threshold}
            )
            session.commit()
            return int(result.rowcount or 0)

    def prune_excess(
        self,
        project_id: str,
        max_records: int = 100,
    ) -> int:
        """Cap per-project pending rows.

        FIFO — deletes the oldest **unprocessed** rows over the cap.
        Only rows with status IN (``available``, ``retryable``) are
        considered; ``claimed`` / ``applied`` / ``abandoned`` rows
        are left alone (the worker is still processing them, or
        they are preserved for audit).
        """
        with Session(self.engine) as session:
            count_stmt = (
                select(BlueprintPendingUpdate)
                .where(BlueprintPendingUpdate.project_id == project_id)
                .where(
                    col(BlueprintPendingUpdate.status).in_(PENDING_ACTIVE_STATUSES)
                )
            )
            existing = len(list(session.exec(count_stmt)))
            if existing <= max_records:
                return 0

            overflow = existing - max_records
            # Fetch the oldest overflow IDs in a single SELECT, then
            # DELETE by ID. This is the standard "FIFO by oldest"
            # pattern; the SELECT is bounded by `overflow` so the
            # query is cheap at the typical cap (100).
            overflow_stmt = (
                select(BlueprintPendingUpdate.id)
                .where(BlueprintPendingUpdate.project_id == project_id)
                .where(
                    col(BlueprintPendingUpdate.status).in_(PENDING_ACTIVE_STATUSES)
                )
                .order_by(col(BlueprintPendingUpdate.created_at).asc())
                .limit(overflow)
            )
            ids_to_delete = [row for row in session.exec(overflow_stmt).all()]
            if not ids_to_delete:
                return 0
            from sqlalchemy import bindparam

            delete_stmt = text(
                "DELETE FROM project_blueprint_pending_updates "
                "WHERE id IN :ids"
            ).bindparams(bindparam("ids", expanding=True))
            result = session.execute(delete_stmt, {"ids": ids_to_delete})
            session.commit()
            deleted = int(result.rowcount or 0)
        if deleted:
            logger.info(
                "prune_excess: deleted %d pending row(s) for project %s (cap=%d)",
                deleted, project_id, max_records,
            )
        return deleted


def create_blueprint_pending_repository(
    engine: Engine,
) -> BlueprintPendingRepository:
    """Factory matching the ``create_blueprint_repository`` convention.

    The pending table is auto-created on first use via
    ``SQLModel.metadata.create_all`` (called by the manager's
    startup hook). The factory itself does not call ``create_all``
    — the manager is responsible for that.
    """
    return BlueprintPendingRepository(engine=engine)
