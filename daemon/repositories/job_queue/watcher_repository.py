"""JobWatcher repository for managing job subscriptions."""

from __future__ import annotations

import logging

from sqlalchemy import delete as sql_delete, func
from sqlalchemy.engine import Engine
from sqlmodel import Session as SQLModelSession, select

from .watcher_models import JobWatcher, ALL_TERMINAL_STATES, ALL_WATCHABLE_EVENTS

logger = logging.getLogger(__name__)


class JobWatcherRepository:
    """Repository for JobWatcher CRUD operations.

    Provides persistence for job-instance watch pairs with JSON event filtering.
    """

    def __init__(self, engine: Engine):
        """Initialize repository with a database engine."""
        self.engine = engine

    def add_watch(
        self,
        job_id: str,
        instance_id: str,
        watch_events: list[str] | None = None,
    ) -> JobWatcher:
        """Add a watch for a job by an instance.

        If a watch already exists for (job_id, instance_id), updates the events.
        Returns the created/updated watch.

        Args:
            job_id: The job ID to watch.
            instance_id: The instance ID that wants notifications.
            watch_events: List of terminal states to watch for.
                Defaults to all terminal states.

        Returns:
            The created or updated JobWatcher.
        """
        default_events = list(ALL_WATCHABLE_EVENTS)
        events = watch_events or default_events

        with SQLModelSession(self.engine) as db_session:
            # Check if watch already exists
            stmt = select(JobWatcher).where(
                JobWatcher.job_id == job_id,
                JobWatcher.instance_id == instance_id,
            )
            existing = db_session.exec(stmt).first()

            if existing:
                existing.watch_events = events
                db_session.add(existing)
                db_session.commit()
                db_session.refresh(existing)
                return existing

            # Create new watch
            watch = JobWatcher(
                job_id=job_id,
                instance_id=instance_id,
                watch_events=events,
            )
            db_session.add(watch)
            db_session.commit()
            db_session.refresh(watch)
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
