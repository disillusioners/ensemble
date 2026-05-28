"""SQLModel-based Dead Letter Queue Repository implementation."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Tuple

from sqlalchemy import delete as sql_delete, func, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlmodel import Session as SQLModelSession, select

from .models import DeadLetterItem

logger = logging.getLogger(__name__)


class DeadLetterRepository:
    """Persistence layer for dead letter queue items."""

    def __init__(self, engine: Engine):
        """Initialize repository with a database engine."""
        self.engine = engine

    def enqueue(self, item: DeadLetterItem) -> DeadLetterItem:
        """Insert a new item into the dead letter queue.
        
        Args:
            item: DeadLetterItem to insert.
            
        Returns:
            The inserted DeadLetterItem with any DB-generated fields populated.
        """
        with SQLModelSession(self.engine) as session:
            session.add(item)
            session.commit()
            session.refresh(item)
            return item

    def get(self, dlq_id: str) -> DeadLetterItem | None:
        """Get a dead letter item by DLQ ID.
        
        Args:
            dlq_id: Unique DLQ identifier.
            
        Returns:
            DeadLetterItem if found, None otherwise.
        """
        with SQLModelSession(self.engine) as session:
            return session.get(DeadLetterItem, dlq_id)

    def get_by_job_id(self, job_id: str) -> DeadLetterItem | None:
        """Get a dead letter item by original job ID.
        
        Args:
            job_id: Original job identifier.
            
        Returns:
            DeadLetterItem if found, None otherwise.
        """
        with SQLModelSession(self.engine) as session:
            stmt = select(DeadLetterItem).where(DeadLetterItem.job_id == job_id)
            return session.exec(stmt).first()

    def list(
        self,
        project_id: str | None = None,
        queue_id: str | None = None,
        reason: str | None = None,
        min_age_hours: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[DeadLetterItem], int]:
        """List dead letter items with optional filters and pagination.
        
        Args:
            project_id: Optional project ID filter.
            queue_id: Optional queue ID filter.
            reason: Optional reason filter.
            min_age_hours: Optional minimum age filter in hours.
            limit: Maximum number of items to return.
            offset: Number of items to skip for pagination.
            
        Returns:
            Tuple of (list of matching items, total count).
        """
        with SQLModelSession(self.engine) as session:
            # Build base statement
            stmt = select(DeadLetterItem)
            count_stmt = select(func.count()).select_from(DeadLetterItem)
            
            if project_id:
                stmt = stmt.where(DeadLetterItem.project_id == project_id)
                count_stmt = count_stmt.where(DeadLetterItem.project_id == project_id)
            if queue_id:
                stmt = stmt.where(DeadLetterItem.queue_id == queue_id)
                count_stmt = count_stmt.where(DeadLetterItem.queue_id == queue_id)
            if reason:
                stmt = stmt.where(DeadLetterItem.reason == reason)
                count_stmt = count_stmt.where(DeadLetterItem.reason == reason)
            if min_age_hours is not None:
                cutoff = datetime.now(timezone.utc) - timedelta(hours=min_age_hours)
                cutoff_str = cutoff.isoformat()
                stmt = stmt.where(DeadLetterItem.moved_to_dlq_at <= cutoff_str)
                count_stmt = count_stmt.where(DeadLetterItem.moved_to_dlq_at <= cutoff_str)
            
            # Get total count
            total = session.exec(count_stmt).one()
            
            # Get paginated results
            stmt = stmt.order_by(DeadLetterItem.moved_to_dlq_at.desc()).offset(offset).limit(limit)
            items = list(session.exec(stmt))
            
            return items, total

    def delete(self, dlq_id: str) -> bool:
        """Delete a dead letter item by DLQ ID.
        
        Args:
            dlq_id: DLQ identifier.
            
        Returns:
            True if deleted, False if not found.
        """
        with SQLModelSession(self.engine) as session:
            item = session.get(DeadLetterItem, dlq_id)
            if item is None:
                return False
            session.delete(item)
            session.commit()
            return True

    def delete_by_job_id(self, job_id: str) -> bool:
        """Delete a dead letter item by original job ID.
        
        Args:
            job_id: Original job identifier.
            
        Returns:
            True if deleted, False if not found.
        """
        with SQLModelSession(self.engine) as session:
            stmt = select(DeadLetterItem).where(DeadLetterItem.job_id == job_id)
            item = session.exec(stmt).first()
            if item is None:
                return False
            session.delete(item)
            session.commit()
            return True

    def cleanup_by_age(
        self,
        max_age_hours: int,
        reason: str | None = None,
        project_id: str | None = None,
    ) -> int:
        """Delete dead letter items older than max_age_hours.
        
        Args:
            max_age_hours: Maximum age in hours for items to keep.
            reason: Optional reason filter to only delete items with specific reason.
            project_id: Optional project ID filter to only delete items for a specific project.
            
        Returns:
            Number of items deleted.
        """
        cutoff_date = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        cutoff_str = cutoff_date.isoformat()
        
        with SQLModelSession(self.engine) as session:
            stmt = sql_delete(DeadLetterItem).where(
                DeadLetterItem.moved_to_dlq_at < cutoff_str
            )
            if reason:
                stmt = stmt.where(DeadLetterItem.reason == reason)
            if project_id:
                stmt = stmt.where(DeadLetterItem.project_id == project_id)
            result = session.exec(stmt)
            session.commit()
            return result.rowcount

    def count(
        self,
        project_id: str | None = None,
        queue_id: str | None = None,
    ) -> int:
        """Count dead letter items with optional filters.
        
        Args:
            project_id: Optional project ID filter.
            queue_id: Optional queue ID filter.
            
        Returns:
            Count of matching items.
        """
        with SQLModelSession(self.engine) as session:
            stmt = select(func.count()).select_from(DeadLetterItem)
            
            if project_id:
                stmt = stmt.where(DeadLetterItem.project_id == project_id)
            if queue_id:
                stmt = stmt.where(DeadLetterItem.queue_id == queue_id)
            
            return session.exec(stmt).one()


    def delete_by_project(self, project_id: str) -> int:
        """Delete all dead letter items for a project.
        
        Args:
            project_id: Project identifier.
            
        Returns:
            Number of items deleted.
        """
        with SQLModelSession(self.engine) as session:
            stmt = sql_delete(DeadLetterItem).where(DeadLetterItem.project_id == project_id)
            result = session.exec(stmt)
            session.commit()
            return result.rowcount


# Module-level singleton for dependency injection
_repo: DeadLetterRepository | None = None


def get_dead_letter_repository() -> DeadLetterRepository:
    """Get the module-level DeadLetterRepository instance.
    
    Returns:
        The singleton DeadLetterRepository instance.
        
    Raises:
        RuntimeError: If repository has not been initialized.
    """
    if _repo is None:
        raise RuntimeError("DeadLetterRepository has not been initialized")
    return _repo


def set_dead_letter_repository(repo: DeadLetterRepository) -> None:
    """Set the module-level DeadLetterRepository instance.
    
    Args:
        repo: The repository instance to use.
    """
    global _repo
    _repo = repo
