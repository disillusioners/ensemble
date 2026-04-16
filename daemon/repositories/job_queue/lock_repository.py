"""SQLModel-based JobLock Repository implementation."""

from __future__ import annotations

import logging
from typing import List, Optional

from sqlalchemy.engine import Engine
from sqlmodel import Session as SQLModelSession, select

from .models import JobLock

logger = logging.getLogger(__name__)


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
        """Release a specific lock by ID. Returns True if found and deleted."""
        with SQLModelSession(self.engine) as session:
            lock = session.get(JobLock, lock_id)
            if lock is None:
                return False
            session.delete(lock)
            session.commit()
            return True

    def release_by_job(self, project_id: str, queue_id: str, job_id: str) -> bool:
        """Release lock by job identity. Returns True if found and deleted."""
        with SQLModelSession(self.engine) as session:
            stmt = select(JobLock).where(
                JobLock.project_id == project_id,
                JobLock.queue_id == queue_id,
                JobLock.job_id == job_id,
            )
            lock = session.exec(stmt).first()
            if lock is None:
                return False
            session.delete(lock)
            session.commit()
            return True

    def release_by_instance(self, instance_id: str) -> int:
        """Release all locks held by an instance. Returns count of released locks."""
        with SQLModelSession(self.engine) as session:
            stmt = select(JobLock).where(JobLock.instance_id == instance_id)
            locks = session.exec(stmt).all()
            count = len(locks)
            for lock in locks:
                session.delete(lock)
            session.commit()
            return count

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
