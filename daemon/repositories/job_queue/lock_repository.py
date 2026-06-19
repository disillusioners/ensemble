"""SQLModel-based JobLock Repository implementation."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlmodel import Session as SQLModelSession, select

from .models import JobLock

logger = logging.getLogger(__name__)


# Subquery used by ``clear_stale_job_locks`` / ``clear_terminal_job_locks``
# to identify job_ids that still hold an active (non-terminal, non-deleted)
# job. Matches the JobStatus enum values and the soft-delete convention
# (deleted_at IS NULL). Defined at module scope so both cleanup paths and
# tests share one source of truth.
_ACTIVE_JOB_IDS_SUBQUERY = (
    "SELECT job_id FROM job_queue_items "
    "WHERE status IN ('pending', 'processing') "
    "  AND deleted_at IS NULL"
)


class LockRepository:
    """Persistence layer for job locks."""

    def __init__(self, engine: Engine):
        """Initialize repository with a database engine."""
        self.engine = engine

    def acquire(self, lock: JobLock) -> JobLock:
        """Persist a lock record."""
        with SQLModelSession(self.engine) as session:
            session.add(lock)
            session.commit()
            session.refresh(lock)
            return lock

    def release(self, lock_id: str) -> bool:
        """Release a specific lock by ID. Returns True if found and deleted.

        F09 fix: converted from SELECT-then-DELETE to a single
        ``DELETE ... WHERE ...`` statement. The previous pattern held
        the row in the ORM identity map between read and write, which
        under concurrent callers could observe a row that another
        caller had already deleted (or fail to observe a row another
        caller had just inserted with the same id, depending on
        isolation level). Atomic DELETE makes the read+write a single
        statement so the result is consistent across SQLite and
        PostgreSQL.

        Returns True iff ``rowcount > 0``. We deliberately do NOT use
        ``DELETE ... RETURNING lock_id`` here even though both SQLite
        3.35+ and PostgreSQL support it: with the pysqlite driver,
        RETURNING leaves a cursor open on the connection, and when
        SQLAlchemy commits the transaction at the end of the
        ``engine.begin()`` block it raises
        ``sqlite3.OperationalError: cannot commit transaction - SQL
        statements in progress``. Using ``rowcount`` avoids the
        cursor entirely while preserving atomicity (it is still a
        single DELETE statement, no SELECT first).
        """
        with self.engine.begin() as conn:
            result = conn.execute(
                text(
                    "DELETE FROM job_locks "
                    "WHERE lock_id = :lock_id"
                ),
                {"lock_id": lock_id},
            )
            return (result.rowcount or 0) > 0

    def release_by_job(self, project_id: str, queue_id: str, job_id: str) -> bool:
        """Release lock by job identity. Returns True if found and deleted.

        F09 fix: atomic ``DELETE ... WHERE ...`` instead of
        SELECT-then-DELETE — see :meth:`release` for the rationale.
        Uses ``rowcount`` rather than ``RETURNING`` because the
        pysqlite/SQLite driver errors with "cannot commit
        transaction - SQL statements in progress" when a RETURNING
        cursor is still open at commit time.
        """
        with self.engine.begin() as conn:
            result = conn.execute(
                text(
                    "DELETE FROM job_locks "
                    "WHERE project_id = :project_id "
                    "  AND queue_id = :queue_id "
                    "  AND job_id = :job_id"
                ),
                {
                    "project_id": project_id,
                    "queue_id": queue_id,
                    "job_id": job_id,
                },
            )
            return (result.rowcount or 0) > 0

    def release_by_instance(self, instance_id: str) -> int:
        """Release all locks held by an instance. Returns count of released locks.

        F09 fix: atomic ``DELETE ... WHERE ...`` instead of
        SELECT-then-DELETE — see :meth:`release` for the rationale.
        Uses ``rowcount`` rather than ``RETURNING`` because the
        pysqlite/SQLite driver errors with "cannot commit
        transaction - SQL statements in progress" when a RETURNING
        cursor is still open at commit time.
        """
        with self.engine.begin() as conn:
            result = conn.execute(
                text(
                    "DELETE FROM job_locks "
                    "WHERE instance_id = :instance_id"
                ),
                {"instance_id": instance_id},
            )
            return result.rowcount or 0

    def get_active_locks(self, project_id: str, queue_id: str) -> List[JobLock]:
        """Get all active locks for a queue."""
        with SQLModelSession(self.engine) as session:
            stmt = select(JobLock).where(
                JobLock.project_id == project_id,
                JobLock.queue_id == queue_id,
            )
            return list(session.exec(stmt))

    def get_lock_count(self, project_id: str, queue_id: str) -> int:
        """Count active locks for a queue."""
        with SQLModelSession(self.engine) as session:
            stmt = select(JobLock).where(
                JobLock.project_id == project_id,
                JobLock.queue_id == queue_id,
            )
            return len(list(session.exec(stmt)))

    def get_all_locks(self) -> List[JobLock]:
        """Get all active locks (for startup reconciliation)."""
        with SQLModelSession(self.engine) as session:
            stmt = select(JobLock)
            return list(session.exec(stmt))

    def get_locks_by_instance(self, instance_id: str) -> List[JobLock]:
        """Get all locks held by an instance."""
        with SQLModelSession(self.engine) as session:
            stmt = select(JobLock).where(JobLock.instance_id == instance_id)
            return list(session.exec(stmt))

    def delete_by_project(self, project_id: str) -> int:
        """Delete all locks for a project.
        
        Args:
            project_id: Project identifier.
            
        Returns:
            Number of locks deleted.
        """
        from sqlalchemy import delete as sql_delete
        with SQLModelSession(self.engine) as session:
            stmt = sql_delete(JobLock).where(JobLock.project_id == project_id)
            result = session.exec(stmt)
            session.commit()
            return result.rowcount

    # --------------------------------------------------------
    # C5: ATOMIC PER-SLOT ACQUIRE
    # --------------------------------------------------------

    def try_acquire_slot(
        self,
        lock_id: str,
        project_id: str,
        queue_id: str,
        job_id: str,
        instance_id: str | None,
        slot: int,
    ) -> bool:
        """Atomically claim a specific ``slot`` in the (project_id,
        queue_id) lock space.

        The atomicity is provided by the DB engine via the
        ``uq_job_locks_slot`` UNIQUE constraint on
        ``(project_id, queue_id, lock_slot)`` (see JobLock model
        and migration ``20260619_000001_add_lock_slot_to_job_locks.sql``):

        - SQLite: ``INSERT OR IGNORE``. If a row already exists for
          this (project_id, queue_id, slot), the INSERT is silently
          dropped and rowcount is 0.
        - PostgreSQL: ``INSERT ... ON CONFLICT (project_id, queue_id,
          lock_slot) DO NOTHING``. Same semantics.

        Returns True iff we won the slot (rowcount == 1). Returns
        False if the slot was already taken by another holder.

        Mirrors the dialect-branching pattern in
        ``ExecutionLeaseRepository.try_acquire`` (raw ``text()``
        SQL, branch on ``self.engine.dialect.name``). The atomic
        INSERT is the *only* cross-process synchronisation point —
        the in-process ``asyncio.Lock`` in the manager is no longer
        required for correctness, only removed there for clarity.

        Args:
            lock_id: Pre-generated UUID for the new lock row.
            project_id: Owning project.
            queue_id: Owning queue.
            job_id: Job claiming the slot.
            instance_id: Instance running the job (None allowed).
            slot: Slot index in 0..concurrency_limit-1.
        """
        now = datetime.now(timezone.utc).isoformat()
        dialect = self.engine.dialect.name
        if dialect == "postgresql":
            stmt = text(
                """
                INSERT INTO job_locks
                    (lock_id, project_id, queue_id, job_id,
                     instance_id, lock_slot, acquired_at)
                VALUES
                    (:lock_id, :project_id, :queue_id, :job_id,
                     :instance_id, :slot, :now)
                ON CONFLICT (project_id, queue_id, lock_slot) DO NOTHING
                """
            )
        else:
            stmt = text(
                """
                INSERT OR IGNORE INTO job_locks
                    (lock_id, project_id, queue_id, job_id,
                     instance_id, lock_slot, acquired_at)
                VALUES
                    (:lock_id, :project_id, :queue_id, :job_id,
                     :instance_id, :slot, :now)
                """
            )
        with self.engine.begin() as conn:
            result = conn.execute(
                stmt,
                {
                    "lock_id": lock_id,
                    "project_id": project_id,
                    "queue_id": queue_id,
                    "job_id": job_id,
                    "instance_id": instance_id,
                    "slot": slot,
                    "now": now,
                },
            )
            return (result.rowcount or 0) == 1

    # --------------------------------------------------------
    # C12: STARTUP SWEEP / PERIODIC CLEANUP
    # --------------------------------------------------------

    def clear_stale_job_locks(self) -> int:
        """Bulk-delete every ``job_locks`` row whose job is no longer
        in an active (pending / processing) state.

        An "orphan" lock is one whose ``job_id`` either:

        - No longer exists in ``job_queue_items`` (job was hard-deleted
          or never existed in this DB — e.g. restored from a partial
          backup or written by a different process).
        - Has transitioned to a terminal status
          (``completed``/``failed``/``cancelled``/``dead_letter``).
          The lock should have been released when the job completed;
          a leftover row means the worker crashed between
          ``atomic_transition`` and ``release_queue_lock``, or
          ``release_queue_lock`` itself errored.
        - Has been soft-deleted (``deleted_at IS NOT NULL``).

        Used at daemon startup to clear orphans left behind by a
        previous process that died mid-execution. Idempotent: safe to
        call multiple times. Companion to
        ``InstanceExecutionLease.clear_stale_leases`` (different
        table, same purpose).

        Returns the number of rows deleted. Callers log a warning
        when this is non-zero so operators can audit when orphans
        had to be cleared.
        """
        stmt = text(
            f"""
            DELETE FROM job_locks
            WHERE job_id NOT IN ({_ACTIVE_JOB_IDS_SUBQUERY})
            """
        )
        with self.engine.begin() as conn:
            result = conn.execute(stmt)
            return result.rowcount or 0

    def clear_terminal_job_locks(self) -> int:
        """Periodic cleanup of ``job_locks`` rows for jobs in a
        terminal status.

        Same logic as ``clear_stale_job_locks`` — kept as a separate
        method so periodic-cleanup callers can log differently
        (INFO vs WARNING) and so the call sites document intent
        (startup sweep vs periodic cleanup). One-shot DELETE;
        idempotent.
        """
        return self.clear_stale_job_locks()
