"""SQLModel-based JobQueue Repository implementation."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete as sql_delete, func, update
from sqlalchemy.engine import Engine
from sqlmodel import Session as SQLModelSession, select, col

from .models import JobItem, JobQueue, JobStatus, QueueType


class JobQueueRepository:
    """SQLModel-based JobQueue repository for CRUD operations.
    
    Provides persistence for named job queues with support for
    per-project job isolation.
    """
    
    def __init__(self, engine: Engine):
        """Initialize repository with a database engine."""
        self.engine = engine

    # --------------------------------------------------------
    # CREATE
    # --------------------------------------------------------

    def create(
        self,
        project_id: str,
        queue_name: str,
        queue_type: str = QueueType.FIFO.value,
        concurrency_limit: int = 1,
        is_system: bool = False,
        is_paused: bool = False,
        description: str | None = None,
    ) -> JobQueue:
        """Create a new job queue.
        
        Args:
            project_id: Project ID that owns this queue.
            queue_name: Human-readable queue name.
            queue_type: Queue type ("fifo", "parallel", or "defer").
            concurrency_limit: Max concurrent jobs (1-20).
            is_system: Whether this is a system queue.
            is_paused: Whether the queue is paused.
            description: Optional description.
            
        Returns:
            Created JobQueue object.
        """
        now = datetime.now(timezone.utc).isoformat()
        
        with SQLModelSession(self.engine) as db_session:
            queue = JobQueue(
                project_id=project_id,
                queue_name=queue_name,
                queue_name_lower=queue_name.lower(),
                queue_type=queue_type,
                concurrency_limit=concurrency_limit,
                is_system=is_system,
                is_paused=is_paused,
                description=description,
                created_at=now,
                updated_at=now,
            )

            db_session.add(queue)
            db_session.commit()
            db_session.refresh(queue)

            return queue

    # --------------------------------------------------------
    # READ
    # --------------------------------------------------------

    def get(self, queue_id: str) -> JobQueue | None:
        """Get a queue by ID.
        
        Args:
            queue_id: Unique queue identifier.
            
        Returns:
            JobQueue if found, None otherwise.
        """
        with SQLModelSession(self.engine) as db_session:
            queue = db_session.get(JobQueue, queue_id)
            return queue

    def get_by_name(
        self,
        project_id: str,
        queue_name: str,
    ) -> JobQueue | None:
        """Get a queue by project ID and name (case-insensitive).
        
        Args:
            project_id: Project identifier.
            queue_name: Queue name (case-insensitive lookup).
            
        Returns:
            JobQueue if found, None otherwise.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = select(JobQueue).where(
                JobQueue.project_id == project_id,
                JobQueue.queue_name_lower == queue_name.lower(),
            )
            queue = db_session.exec(stmt).first()
            return queue

    def list_by_project(self, project_id: str) -> list[JobQueue]:
        """List all queues for a project.
        
        Args:
            project_id: Project identifier.
            
        Returns:
            List of JobQueue objects for the project.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = select(JobQueue).where(
                JobQueue.project_id == project_id
            ).order_by(JobQueue.queue_name_lower)
            queues = list(db_session.exec(stmt))
            return queues

    def get_system_queues(self, project_id: str) -> list[JobQueue]:
        """List all system queues for a project.
        
        Args:
            project_id: Project identifier.
            
        Returns:
            List of system JobQueue objects for the project.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = select(JobQueue).where(
                JobQueue.project_id == project_id,
                JobQueue.is_system == True,
            ).order_by(JobQueue.queue_name_lower)
            queues = list(db_session.exec(stmt))
            return queues

    # --------------------------------------------------------
    # UPDATE
    # --------------------------------------------------------

    def update(
        self,
        queue_id: str,
        **updates: Any,
    ) -> JobQueue | None:
        """Update a queue's fields.
        
        Args:
            queue_id: Queue identifier.
            **updates: Fields to update.
            
        Returns:
            Updated JobQueue if found, None otherwise.
        """
        with SQLModelSession(self.engine) as db_session:
            queue = db_session.get(JobQueue, queue_id)
            if queue is None:
                return None

            # Handle queue_name update (sync queue_name_lower)
            if 'queue_name' in updates:
                queue.queue_name = updates.pop('queue_name')
                queue.queue_name_lower = queue.queue_name.lower()

            # Update other fields
            for key, value in updates.items():
                if hasattr(queue, key):
                    setattr(queue, key, value)

            # Always update updated_at
            queue.updated_at = datetime.now(timezone.utc).isoformat()

            db_session.commit()
            db_session.refresh(queue)

            return queue

    # --------------------------------------------------------
    # DELETE
    # --------------------------------------------------------

    def delete(self, queue_id: str) -> dict[str, Any]:
        """Delete a queue by ID.
        
        Args:
            queue_id: Queue identifier.
            
        Returns:
            Dictionary with deletion status.
        """
        with SQLModelSession(self.engine) as db_session:
            queue = db_session.get(JobQueue, queue_id)
            if queue is None:
                return {"deleted": False, "queue_id": queue_id, "error": "Not found"}

            db_session.delete(queue)
            db_session.commit()

            return {
                "deleted": True,
                "queue_id": queue_id,
                "project_id": queue.project_id,
            }

    def delete_by_project(self, project_id: str) -> int:
        """Delete all queues for a project.
        
        Args:
            project_id: Project identifier.
            
        Returns:
            Number of queues deleted.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = sql_delete(JobQueue).where(JobQueue.project_id == project_id)
            result = db_session.exec(stmt)
            db_session.commit()
            return result.rowcount

    # --------------------------------------------------------
    # JOB STATISTICS
    # --------------------------------------------------------

    def count_jobs_by_status(self, queue_id: str) -> dict[str, int]:
        """Count jobs in a queue grouped by status.
        
        Args:
            queue_id: Queue identifier.
            
        Returns:
            Dictionary mapping status to count, e.g. {"pending": 5, "processing": 1}.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = (
                select(JobItem.status, func.count(JobItem.job_id))
                .where(JobItem.queue_id == queue_id)
                .group_by(JobItem.status)
            )
            results = db_session.exec(stmt).all()
            
            # Initialize with all statuses for consistency
            counts = {status.value: 0 for status in JobStatus}
            for status, count in results:
                counts[status] = count
            
            return counts

    def reassign_pending_jobs_atomic(
        self,
        from_queue_id: str,
        to_queue_id: str,
        target_statuses: list[str | None] = None,
    ) -> int:
        """Atomically reassign jobs from one queue to another.
        
        Uses a single SQL UPDATE statement for atomicity.
        
        Args:
            from_queue_id: Source queue ID.
            to_queue_id: Destination queue ID.
            target_statuses: List of statuses to reassign (default: ["pending"]).
            
        Returns:
            Number of jobs reassigned.
        """
        if target_statuses is None:
            target_statuses = [JobStatus.PENDING.value]
        
        with SQLModelSession(self.engine) as db_session:
            # Use SQLAlchemy Core update() for atomic execution
            stmt = (
                update(JobItem)
                .where(JobItem.queue_id == from_queue_id)
                .where(JobItem.status.in_(target_statuses))
                .values(queue_id=to_queue_id)
            )
            result = db_session.execute(stmt)
            db_session.commit()
            
            return result.rowcount
