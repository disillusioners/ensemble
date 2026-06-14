"""Repository for the per-instance execution lease (Execution Gate).

The lease table is the single source of truth for "which dispatcher is
currently driving ``graph.astream`` for this instance?". Acquisition and
release go through the methods here. The dual-driver safety story lives
at ``daemon/services/execution_gate.py``; this module is the DB plumbing
only.

Why raw SQL for the acquire/release paths (and not the SQLModel ORM)?

The atomic acquire needs ``INSERT ... ON CONFLICT DO NOTHING`` (Postgres)
or ``INSERT OR IGNORE`` (SQLite). Both convey the same semantics
("create the row iff no row exists for this PK") but the syntax differs.
Wrapping the dialect-specific form behind a single helper keeps the
callers dialect-agnostic and avoids a SQLModel session roundtrip for what
is otherwise a single-statement operation.

The conditional release needs ``DELETE WHERE instance_id = :id AND
holder_id = :hid`` so a stale loser (whose holder_id is no longer the
winner) cannot accidentally delete a fresh winner's lease. We also
filter on ``holder_kind`` as a belt-and-braces guard: if a caller passes
the wrong kind for its holder_id, the release is a no-op and we log it.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlmodel import Session as SQLModelSession

from .models import InstanceExecutionLease, LeaseHolderKind

logger = logging.getLogger(__name__)


class ExecutionLeaseRepository:
    """DB-backed per-instance execution lease for the Execution Gate.

    Methods are synchronous and thread-safe (SQLAlchemy sessions are
    scoped per call). Callers bridge to async via ``asyncio.to_thread``
    or call from inside the worker thread pool.
    """

    def __init__(self, engine: Engine):
        self.engine = engine

    # --------------------------------------------------------
    # ACQUIRE / RELEASE
    # --------------------------------------------------------

    def try_acquire(
        self,
        instance_id: str,
        holder_id: str,
        holder_kind: str,
        process_id: int | None = None,
    ) -> bool:
        """Atomically acquire the execution lease for ``instance_id``.

        Returns True if the caller now holds the lease — either by
        inserting a new row, or by being the existing holder (an
        idempotent re-acquire from the same holder). Returns False if
        the lease is held by a *different* holder.

        Implementation: ``INSERT ... ON CONFLICT DO NOTHING`` (Postgres)
        or ``INSERT OR IGNORE`` (SQLite). rowcount==1 means we
        inserted; rowcount==0 means a row already exists. We then
        follow up with a SELECT to see whether the existing row is
        ours (idempotent re-acquire, return True) or someone else's
        (return False). This costs one extra round-trip on the
        contention path, which is the rare path; the happy path
        stays at one round-trip.

        Args:
            instance_id: The langgraph thread_id (== instance_id).
            holder_id: Unique token of the caller. Use a stable identifier
                (e.g. ``message_job:{job_id}``, ``task:{task_id}``) so
                release can be conditional on it.
            holder_kind: One of ``LeaseHolderKind`` values.
            process_id: Optional OS PID for diagnostics. Not used for
                crash detection (heartbeat staleness is the signal).
        """
        now = datetime.now(timezone.utc)
        pid = process_id if process_id is not None else os.getpid()
        dialect = self.engine.dialect.name
        if dialect == "postgresql":
            stmt = text(
                """
                INSERT INTO instance_execution_leases
                    (instance_id, holder_id, holder_kind, acquired_at,
                     heartbeat_at, process_id)
                VALUES
                    (:instance_id, :holder_id, :holder_kind, :now,
                     :now, :process_id)
                ON CONFLICT (instance_id) DO NOTHING
                """
            )
        else:
            stmt = text(
                """
                INSERT OR IGNORE INTO instance_execution_leases
                    (instance_id, holder_id, holder_kind, acquired_at,
                     heartbeat_at, process_id)
                VALUES
                    (:instance_id, :holder_id, :holder_kind, :now,
                     :now, :process_id)
                """
            )
        with self.engine.begin() as conn:
            result = conn.execute(
                stmt,
                {
                    "instance_id": instance_id,
                    "holder_id": holder_id,
                    "holder_kind": holder_kind,
                    "now": now,
                    "process_id": pid,
                },
            )
            if (result.rowcount or 0) == 1:
                return True
            # Row already exists. Check if it's us.
            existing = conn.execute(
                text(
                    "SELECT holder_id FROM instance_execution_leases "
                    "WHERE instance_id = :instance_id"
                ),
                {"instance_id": instance_id},
            ).first()
            return existing is not None and existing[0] == holder_id

    def release(self, instance_id: str, holder_id: str) -> bool:
        """Conditionally release the lease.

        The DELETE is conditional on ``holder_id`` matching the current
        row, so a stale loser cannot accidentally delete a fresh
        winner's lease. Idempotent: returns False if there was no row
        to delete.

        Returns:
            True if a row was deleted, False otherwise.
        """
        stmt = text(
            """
            DELETE FROM instance_execution_leases
            WHERE instance_id = :instance_id
              AND holder_id = :holder_id
            """
        )
        with self.engine.begin() as conn:
            result = conn.execute(
                stmt,
                {"instance_id": instance_id, "holder_id": holder_id},
            )
            return (result.rowcount or 0) == 1

    # --------------------------------------------------------
    # HEARTBEAT
    # --------------------------------------------------------

    def heartbeat(self, instance_id: str, holder_id: str) -> bool:
        """Refresh the ``heartbeat_at`` column on the lease row.

        Conditional on ``holder_id`` matching the current row, same as
        release. Returns False if the lease is no longer held by the
        caller (it was stolen by recovery, or the caller raced and lost
        the lease to another holder).

        Note: the current implementation does NOT have a dedicated
        heartbeat thread for the lease — callers piggyback on the
        worker's task heartbeat. Crash recovery's "is this lease
        stale?" predicate therefore uses ``acquired_at`` as the floor
        and ``heartbeat_at`` as the more recent signal. If the caller
        never heartbeats, recovery's threshold determines when the
        lease is treated as stale.
        """
        now = datetime.now(timezone.utc)
        stmt = text(
            """
            UPDATE instance_execution_leases
            SET heartbeat_at = :now
            WHERE instance_id = :instance_id
              AND holder_id = :holder_id
            """
        )
        with self.engine.begin() as conn:
            result = conn.execute(
                stmt,
                {"instance_id": instance_id, "holder_id": holder_id, "now": now},
            )
            return (result.rowcount or 0) == 1

    # --------------------------------------------------------
    # READ
    # --------------------------------------------------------

    def get_holder(self, instance_id: str) -> InstanceExecutionLease | None:
        """Return the current lease row for the instance, or None.

        Used by contention handlers to know who currently holds the
        lease so they can decide how to back off. Cheap primary-key
        lookup; no scan.
        """
        with SQLModelSession(self.engine) as session:
            return session.get(InstanceExecutionLease, instance_id)

    def is_held_by(self, instance_id: str, holder_id: str) -> bool:
        """True iff the lease is currently held by ``holder_id``."""
        with SQLModelSession(self.engine) as session:
            row = session.get(InstanceExecutionLease, instance_id)
            return row is not None and row.holder_id == holder_id

    # --------------------------------------------------------
    # RECOVERY
    # --------------------------------------------------------

    def find_stale_leases(self, max_age_seconds: int) -> list[InstanceExecutionLease]:
        """Find leases whose heartbeat is older than the threshold.

        NOTE: no longer called by production code — production uses
        ``clear_stale_leases`` (single bulk DELETE) for startup
        recovery. This method is kept for tests and for ad-hoc
        diagnostic scripts that want to inspect what would be
        recovered without actually deleting anything.

        ``heartbeat_at`` is preferred over ``acquired_at`` because a
        holder that has been running for hours but is still
        heartbeating is NOT stale; a holder that died mid-run is. If
        the holder never heartbeats, ``acquired_at`` is the floor.

        Filtering is done in SQL using ``COALESCE(heartbeat_at,
        acquired_at)`` so the scan is bounded by the table size but
        we still correctly fall back to ``acquired_at`` if a row
        somehow ended up with a NULL ``heartbeat_at`` (the
        default_factory on the column makes this impossible on
        freshly-inserted rows, but rows from older schema versions
        or hand-edited data could in principle be NULL).
        """
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
        with SQLModelSession(self.engine) as session:
            stmt = text(
                """
                SELECT instance_id, holder_id, holder_kind, acquired_at,
                       heartbeat_at, process_id
                FROM instance_execution_leases
                WHERE COALESCE(heartbeat_at, acquired_at) < :cutoff
                """
            )
            rows = session.execute(stmt, {"cutoff": cutoff}).fetchall()
            return [
                InstanceExecutionLease(
                    instance_id=r[0],
                    holder_id=r[1],
                    holder_kind=r[2],
                    acquired_at=r[3],
                    heartbeat_at=r[4],
                    process_id=r[5],
                )
                for r in rows
            ]

    def clear_stale(self, instance_id: str) -> bool:
        """Delete a stale lease row outright (no holder_id check).

        NOTE: no longer called by production code — production uses
        ``clear_stale_leases`` (single bulk DELETE) for startup
        recovery. This method is kept for tests and for ad-hoc
        diagnostic / repair scripts.

        A normal dispatcher should use ``release()`` instead so it
        cannot accidentally evict a live lease. The ``holder_id``
        check is what makes ``release`` safe to call from a
        dispatcher; this method is the recovery primitive for
        operators and tests.
        """
        with self.engine.begin() as conn:
            result = conn.execute(
                text(
                    "DELETE FROM instance_execution_leases "
                    "WHERE instance_id = :instance_id"
                ),
                {"instance_id": instance_id},
            )
            return (result.rowcount or 0) == 1

    def clear_stale_leases(self, max_age_seconds: int) -> int:
        """Bulk-delete every lease whose holder has not heartbeated
        within ``max_age_seconds``. Single round-trip
        (no N+1). The DELETE matches the recovery predicate in
        ``find_stale_leases``: ``COALESCE(heartbeat_at, acquired_at)
        < :cutoff``.

        Used by ``ExecutionGateService.recover_stale_leases`` at
        startup. Returns the number of rows cleared.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
        with self.engine.begin() as conn:
            result = conn.execute(
                text(
                    "DELETE FROM instance_execution_leases "
                    "WHERE COALESCE(heartbeat_at, acquired_at) < :cutoff"
                ),
                {"cutoff": cutoff},
            )
            return result.rowcount or 0

    def list_all(self) -> list[InstanceExecutionLease]:
        """Return every lease row. Used by diagnostics and tests."""
        from sqlmodel import select
        with SQLModelSession(self.engine) as session:
            return list(session.exec(select(InstanceExecutionLease)).all())
