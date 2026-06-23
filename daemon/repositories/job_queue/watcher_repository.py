"""JobWatcher repository for managing job subscriptions."""

from __future__ import annotations

import logging

from sqlalchemy import delete as sql_delete, func
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlmodel import Session as SQLModelSession, select

from .models import JobItem, JobStatus
from .watcher_models import JobWatcher, ALL_WATCHABLE_EVENTS

logger = logging.getLogger(__name__)


class JobWatcherRepository:
    """Repository for JobWatcher CRUD operations.

    Provides persistence for job-instance watch pairs with JSON event filtering.
    """

    def __init__(self, engine: Engine):
        """Initialize repository with a database engine."""
        self.engine = engine

    @staticmethod
    def _get_dialect_insert(session: SQLModelSession):
        """Get dialect-appropriate insert callable for upsert.

        Generic ``sqlalchemy.insert()`` lacks ``on_conflict_do_update()`` —
        that is a dialect-specific method. Returns the SQLite or PostgreSQL
        dialect insert that supports ``on_conflict_do_update`` for both
        ``job_watchers`` UPSERTs.

        Args:
            session: SQLAlchemy/SQLModel Session whose bound engine
                determines dialect.

        Returns:
            Dialect-specific insert callable (``sqlite_insert`` or
            ``pg_insert``). Both support ``on_conflict_do_update``.
        """
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as pg_insert
            return pg_insert
        return sqlite_insert

    def add_watch(
        self,
        job_id: str,
        instance_id: str,
        watch_events: list[str] | None = None,
    ) -> JobWatcher:
        """Add a watch for a job by an instance.

        Atomic upsert: if a watch already exists for (job_id, instance_id),
        updates the events; otherwise inserts. Replaces the previous
        check-then-insert pattern that could create duplicate rows under
        concurrent calls (H13). Backed by the UNIQUE constraint on
        (job_id, instance_id) plus dialect-aware INSERT ... ON CONFLICT
        DO UPDATE, which is race-free across processes and threads.

        Concurrency note (M12):
            The ``watch_events`` JSON column is **always written as a full
            replace** on both branches of the UPSERT — the INSERT side via
            the ``VALUES`` clause and the UPDATE side via
            ``excluded.watch_events``. There is no partial-list append path
            here; callers are expected to compute the desired full event
            list and pass it in. Because the whole row mutation is a single
            ``INSERT ... ON CONFLICT DO UPDATE`` statement, the write is
            atomic with no read-modify-write window — **safe by design**.

        Args:
            job_id: The job ID to watch.
            instance_id: The instance ID that wants notifications.
            watch_events: List of terminal states to watch for.
                Defaults to all watchable events (terminal + in_progress).

        Returns:
            The created or updated JobWatcher.
        """
        default_events = list(ALL_WATCHABLE_EVENTS)
        events = watch_events or default_events

        with SQLModelSession(self.engine) as db_session:
            insert_fn = self._get_dialect_insert(db_session)
            insert_stmt = insert_fn(JobWatcher).values(
                job_id=job_id,
                instance_id=instance_id,
                watch_events=list(events),
            )
            # ON CONFLICT DO UPDATE mirrors the legacy UPDATE branch:
            # only `watch_events` is touched; created_at is preserved.
            # Using ``insert_stmt.excluded.watch_events`` instead of
            # re-binding the value avoids SQLite's "bad parameter or
            # other API misuse" error when the same JSON column appears
            # in both the INSERT VALUES and the UPDATE SET clauses.
            stmt = insert_stmt.on_conflict_do_update(
                index_elements=["job_id", "instance_id"],
                set_={"watch_events": insert_stmt.excluded.watch_events},
            )
            db_session.execute(stmt)
            db_session.commit()

            watch = db_session.exec(
                select(JobWatcher).where(
                    JobWatcher.job_id == job_id,
                    JobWatcher.instance_id == instance_id,
                )
            ).first()
            return watch

    def remove_watch(self, job_id: str, instance_id: str) -> bool:
        """Remove a specific watch.

        Args:
            job_id: The job ID.
            instance_id: The instance ID.

        Returns:
            True if a watch was removed, False if not found.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = sql_delete(JobWatcher).where(
                JobWatcher.job_id == job_id,
                JobWatcher.instance_id == instance_id,
            )
            result = db_session.exec(stmt)
            db_session.commit()
            return result.rowcount > 0

    def get_watchers_for_job(self, job_id: str) -> list[JobWatcher]:
        """Get all watchers for a job.

        Args:
            job_id: The job ID.

        Returns:
            List of JobWatcher records for this job.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = select(JobWatcher).where(JobWatcher.job_id == job_id)
            return list(db_session.exec(stmt).all())

    def get_watches_for_instance(self, instance_id: str) -> list[JobWatcher]:
        """Get all watches for an instance.

        Args:
            instance_id: The instance ID.

        Returns:
            List of JobWatcher records for this instance.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = select(JobWatcher).where(JobWatcher.instance_id == instance_id)
            return list(db_session.exec(stmt).all())

    def remove_all_watches_for_instance(self, instance_id: str) -> int:
        """Remove all watches for an instance (cleanup on termination).

        Args:
            instance_id: The instance ID.

        Returns:
            Number of watches removed.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = sql_delete(JobWatcher).where(JobWatcher.instance_id == instance_id)
            result = db_session.exec(stmt)
            db_session.commit()
            return result.rowcount

    def remove_all_watches_for_job(self, job_id: str) -> int:
        """Remove all watches for a job (after terminal state reached).

        Args:
            job_id: The job ID.

        Returns:
            Number of watches removed.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = sql_delete(JobWatcher).where(JobWatcher.job_id == job_id)
            result = db_session.exec(stmt)
            db_session.commit()
            return result.rowcount

    def count_watches_for_instance(self, instance_id: str) -> int:
        """Count watches for an instance.

        Used to enforce max 50 watches per instance.

        Args:
            instance_id: The instance ID.

        Returns:
            Number of watches for this instance.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = select(func.count()).select_from(JobWatcher).where(
                JobWatcher.instance_id == instance_id
            )
            return db_session.exec(stmt).one()

    def get_all_active_watches(self) -> list[JobWatcher]:
        """Get all watches (for reconciliation scan).

        Returns:
            List of all JobWatcher records.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = select(JobWatcher)
            return list(db_session.exec(stmt).all())

    def get_watched_processing_job_ids(self, watcher_instance_id: str) -> list[str]:
        """Return job_ids that ``watcher_instance_id`` is watching AND still PROCESSING.

        B1 (Phase B): Used by ``CorrelationManager.rebuild_from_db`` to
        reconstruct the ``pending_jobs`` set on daemon restart. The CM
        is the single source of truth for parent completion — a watched
        job that is still in PROCESSING keeps the parent's ``is_complete``
        returning ``False`` (mirroring an outstanding message correlation).
        Without this rebuild hook, a daemon crash during a parent job that
        has watched children would leave the parent's ``pending_jobs``
        empty after restart; the first ``is_complete`` call would fire
        the terminal transition prematurely, and the late watched
        job's terminal event would find no PROCESSING job to resolve.

        Semantics: returns the ``job_id`` (string) of every job that

        1. has a ``JobWatcher`` row with ``instance_id == watcher_instance_id``
           (i.e. this instance is actively subscribed to the job's events),
        2. is currently in ``PROCESSING`` status, and
        3. is not soft-deleted (``deleted_at IS NULL``).

        The ``JobWatcher`` and ``JobItem`` tables are joined via
        ``JobWatcher.job_id == JobItem.job_id``. The result is a flat
        list of job_id strings — duplicates (a watcher can have at most
        one row per job, enforced by the UNIQUE constraint on
        ``(job_id, instance_id)``, so this is defensive) are de-duplicated
        via ``dict.fromkeys`` to keep the result idempotent.

        Args:
            watcher_instance_id: The instance ID of the watcher (parent).

        Returns:
            List of job_ids (strings) for matching jobs. Empty list if the
            watcher has no in-flight watches or all watches are already
            terminal.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = (
                select(JobWatcher.job_id)
                .join(JobItem, JobWatcher.job_id == JobItem.job_id)
                .where(JobWatcher.instance_id == watcher_instance_id)
                .where(JobItem.status == JobStatus.PROCESSING.value)
                .where(JobItem.deleted_at.is_(None))
            )
            # dict.fromkeys preserves order while de-duplicating. The
            # downstream consumer (CM.rebuild_from_db) feeds this into
            # a set, so ordering is not semantically meaningful, but
            # stable order helps test assertions.
            return list(dict.fromkeys(db_session.exec(stmt).all()))
