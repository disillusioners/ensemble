"""JobWatcher repository for managing job subscriptions."""

from __future__ import annotations

import logging

from sqlalchemy import delete as sql_delete, func
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlmodel import Session as SQLModelSession, select

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

        Non-atomic with respect to ``get_watchers_for_job``: two
        concurrent callers can both read a non-empty watcher list and
        both proceed to notify. Kept for backward compatibility with
        callers that need a fire-and-forget cleanup (e.g. reconcile
        paths that don't notify). New terminal-state notification
        code should use ``claim_watchers_for_job`` instead so the
        read+notify+delete cycle is race-free.

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

    def claim_watchers_for_job(self, job_id: str) -> list[JobWatcher]:
        """Atomically claim and delete ALL watchers for a job.

        Phase 2 (Batch 1) of feature/virtual-job-management-surface:
        replaces the read-then-delete pattern in
        ``JobQueueService.notify_watchers`` with a single
        ``DELETE ... RETURNING`` operation so two concurrent terminal
        callers cannot both notify the same watcher.

        Race-free contract:

            * Caller A reads ``get_watchers_for_job`` (sees 2 rows),
              is suspended before notifying.
            * Caller B (terminal race) reads ``get_watchers_for_job``
              (sees 2 rows), notifies, then calls ``remove_all_watches_for_job``.
            * Caller A resumes, notifies the same 2 watchers again
              (DOUBLE-NOTIFY), then calls ``remove_all_watches_for_job``
              (no-op).

        With this method:

            * Caller A calls ``claim_watchers_for_job`` and receives
              the 2 watcher rows (and they are deleted in the same
              SQL operation).
            * Caller B calls ``claim_watchers_for_job`` and receives
              an empty list (Caller A already deleted them) — no
              double-notify.

        The atomicity comes from PostgreSQL's ``DELETE ... RETURNING``
        and SQLite's equivalent (supported since 3.35). Both drivers
        execute the statement as a single round-trip; there is no
        observable interleaving on the rows between SELECT and DELETE.

        N1 (duplicate-delivery window, 2026-09-03) — note on the
        CALLER-side ordering required for this method to enforce
        exactly-once delivery:

            This repo-level CAS only prevents double-notify when the
            caller invokes ``claim_watchers_for_job`` (or its
            filtered sibling ``claim_watchers_for_job_for_instances``)
            BEFORE the per-watcher ``enqueue_message`` calls. The
            ``notify_work_watchers`` helper in
            ``daemon.services.work_notifier`` follows this
            claim-first ordering on every terminal status — the
            claim runs first, the notification loop only delivers
            for rows the claim returned. A notify-then-claim
            ordering (the pre-N1 flow) re-opens the bounded ≤2
            duplicate-delivery window even with this CAS, because
            both racers pass the SELECT before either runs the
            DELETE.

            Do NOT add new notify-then-claim call sites that bypass
            ``notify_work_watchers`` — that re-introduces the bug.

        Note on the RETURNING clause:

            Earlier versions used ``.returning(JobWatcher)`` (passing
            the ORM class). SQLAlchemy treats that as a single-column
            RETURNING keyed by ``"JobWatcher"`` returning an empty
            JobWatcher instance per row — every column (``watch_events``,
            ``created_at``, ``instance_id``, …) is unreachable, and any
            downstream access on the returned rows raises
            ``AttributeError``. Phase 2 (Batch 3) hit this when wiring
            the resolver-based ``reconcile_terminal_watches`` path:
            ``notify_work_watchers`` would log
            ``failed to notify watchers ...: watch_events`` and notify
            nothing. The fix is to project the table's columns
            (``*JobWatcher.__table__.c``) so each returned ``Row``
            carries every populated column.

        Args:
            job_id: The job (work) ID to claim watchers for.

        Returns:
            List of JobWatcher rows that were deleted. Empty list if
            no watchers exist (already-claimed, never-existed, or
            cleared by a previous race winner). The caller iterates
            this list to send notifications and the list is also the
            authoritative count of "claimed" watchers.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = (
                sql_delete(JobWatcher)
                .where(JobWatcher.job_id == job_id)
                .returning(*JobWatcher.__table__.c)
            )
            rows = db_session.exec(stmt).all()
            db_session.commit()
            return [
                JobWatcher(**dict(row._mapping)) for row in rows
            ]

    def claim_watchers_for_job_for_instances(
        self, job_id: str, instance_ids: list[str],
    ) -> list[JobWatcher]:
        """Atomically claim and delete ONLY the listed-instance watchers for a job.

        N1 (duplicate-delivery window, 2026-09-03) — paired-with-filter
        companion to :meth:`claim_watchers_for_job`. Same CAS
        semantics (``DELETE ... RETURNING``), but limited to the
        subset whose ``instance_id`` is in ``instance_ids``. This is
        the repo primitive the new claim-first
        ``notify_work_watchers`` flow calls for: the caller does a
        read-only ``get_watchers_for_job`` first, partitions into
        "will notify" vs "will hold" (e.g. ``mission_terminal``
        opt-in with non-terminal mission liveness), and then
        atomically claims ONLY the matching subset.

        Without the filter, the alternative would be a
        read-then-delete-all flow that loses the held-for-mission
        rows (the mission has not yet reached terminal — the
        watcher must remain registered for the eventual terminal
        event). The per-instance WHERE clause preserves the
        atomic-CAS guarantee AND keeps the held rows intact.

        Race-free contract:

            * Caller A reads ``get_watchers_for_job`` (sees row R for
              instance I), partitions R into "matching".
            * Caller B reads ``get_watchers_for_job`` (sees R), also
              partitions R into "matching".
            * Caller A calls ``claim_watchers_for_job_for_instances``
              (job_id, [I]) → receives R, R is deleted.
            * Caller B calls ``claim_watchers_for_job_for_instances``
              (job_id, [I]) → receives ``[]`` (R already deleted)
              — no double-notify.

        Args:
            job_id: The job (work) ID.
            instance_ids: The ``instance_id``s to claim. Only rows
                whose ``instance_id`` is in this list are deleted;
                other watchers for ``job_id`` are preserved.

        Returns:
            List of JobWatcher rows that were actually deleted
            (i.e. the CAS winners). Empty list if no matching row
            existed or every matching row was already claimed by a
            prior race winner. The caller iterates THIS list to
            deliver notifications — never the pre-claim read —
            so the notify loop is guaranteed to be a subset of
            the rows that won the CAS.
        """
        if not instance_ids:
            return []
        with SQLModelSession(self.engine) as db_session:
            stmt = (
                sql_delete(JobWatcher)
                .where(JobWatcher.job_id == job_id)
                .where(JobWatcher.instance_id.in_(instance_ids))
                .returning(*JobWatcher.__table__.c)
            )
            rows = db_session.exec(stmt).all()
            db_session.commit()
            return [
                JobWatcher(**dict(row._mapping)) for row in rows
            ]

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
