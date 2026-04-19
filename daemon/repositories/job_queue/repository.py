"""SQLModel-based JobQueue Repository implementation."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import delete as sql_delete, func, select as sql_select
from sqlalchemy.engine import Engine
from sqlmodel import Session as SQLModelSession, select, col

from .models import JobItem, JobStatus

logger = logging.getLogger(__name__)


class JobRepository:
    """SQLModel-based Job Queue repository for CRUD operations.
    
    Provides persistence for job queue items with support for
    project-based job serialization.
    """
    
    def __init__(self, engine: Engine):
        """Initialize repository with a database engine."""
        self.engine = engine

    # --------------------------------------------------------
    # CREATE
    # --------------------------------------------------------

    def create(
        self,
        agent_id: str,
        agent_dir: str,
        message: str,
        source: str = "api",
        project_id: Optional[str] = None,
        priority: int = 5,
        job_metadata: Optional[dict[str, Any]] = None,
        queue_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> JobItem:
        """Create a new job queue item.
        
        Args:
            agent_id: Agent ID (e.g., 'coder').
            agent_dir: Path to the agent directory.
            message: Job message/content.
            source: Source of the job ("api", "telegram", "scheduler", "webhook").
            project_id: Optional project ID for job serialization.
            priority: Job priority (1-10, default 5).
            job_metadata: Optional metadata dictionary.
            queue_id: Optional queue ID for job routing.
            idempotency_key: Optional idempotency key for deduplication.
            
        Returns:
            Created JobItem object.
        """
        with SQLModelSession(self.engine) as db_session:
            job = JobItem(
                agent_id=agent_id,
                agent_dir=agent_dir,
                message=message,
                source=source,
                project_id=project_id,
                priority=priority,
                status=JobStatus.PENDING.value,
                job_metadata=job_metadata or {},
                queue_id=queue_id,
                idempotency_key=idempotency_key,
            )

            db_session.add(job)
            db_session.commit()
            db_session.refresh(job)

            return job

    # --------------------------------------------------------
    # READ
    # --------------------------------------------------------

    def get(self, job_id: str) -> Optional[JobItem]:
        """Get a job by ID.
        
        Args:
            job_id: Unique job identifier.
            
        Returns:
            JobItem if found, None otherwise.
        """
        with SQLModelSession(self.engine) as db_session:
            job = db_session.get(JobItem, job_id)
            return job

    def get_by_instance(self, instance_id: str) -> Optional[JobItem]:
        """Get a job by instance ID.
        
        Args:
            instance_id: Instance identifier.
            
        Returns:
            JobItem if found, None otherwise.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = select(JobItem).where(JobItem.instance_id == instance_id)
            job = db_session.exec(stmt).first()
            return job

    def find_by_idempotency_key(self, idempotency_key: str) -> Optional[JobItem]:
        """Find a job by its idempotency key.
        
        Used for idempotent enqueue: before creating a new job, check if one
        already exists with the same key.
        
        Args:
            idempotency_key: The idempotency key to search for.
            
        Returns:
            JobItem if found, None otherwise.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = select(JobItem).where(JobItem.idempotency_key == idempotency_key)
            job = db_session.exec(stmt).first()
            return job

    # --------------------------------------------------------
    # LIST
    # --------------------------------------------------------

    def list(
        self,
        statuses: Optional[list[str]] = None,
        project_id: Optional[str] = None,
        queue_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[JobItem], int]:
        """List jobs with optional filters and pagination.
        
        Args:
            statuses: Optional list of status filters.
            project_id: Optional project ID filter.
            queue_id: Optional queue ID filter.
            limit: Maximum number of jobs to return.
            offset: Number of jobs to skip.
            
        Returns:
            Tuple of (list of jobs, total count).
        """
        with SQLModelSession(self.engine) as db_session:
            # Build count query
            count_stmt = select(func.count()).select_from(JobItem)
            if statuses:
                count_stmt = count_stmt.where(JobItem.status.in_(statuses))
            if project_id:
                count_stmt = count_stmt.where(JobItem.project_id == project_id)
            if queue_id:
                count_stmt = count_stmt.where(JobItem.queue_id == queue_id)
            total = db_session.exec(count_stmt).one()

            # Build list query with filters
            stmt = select(JobItem)
            if statuses:
                stmt = stmt.where(JobItem.status.in_(statuses))
            if project_id:
                stmt = stmt.where(JobItem.project_id == project_id)
            if queue_id:
                stmt = stmt.where(JobItem.queue_id == queue_id)
            
            stmt = stmt.order_by(
                col(JobItem.priority).desc(),
                col(JobItem.created_at).asc()
            ).offset(offset).limit(limit)
            
            jobs = list(db_session.exec(stmt))
            
            return jobs, total

    def list_pending_by_project(self, project_id: str) -> list[JobItem]:
        """List pending jobs for a specific project, ordered by priority.
        
        Args:
            project_id: Project identifier.
            
        Returns:
            List of pending JobItem objects for the project.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = (
                select(JobItem)
                .where(JobItem.project_id == project_id)
                .where(JobItem.status == JobStatus.PENDING.value)
                .order_by(col(JobItem.priority).desc(), JobItem.created_at.asc())
            )
            jobs = list(db_session.exec(stmt))
            return jobs

    def list_all_pending(self) -> list[JobItem]:
        """List all pending jobs (for jobs without project_id).
        
        Returns:
            List of all pending JobItem objects.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = (
                select(JobItem)
                .where(JobItem.status == JobStatus.PENDING.value)
                .order_by(col(JobItem.priority).desc())
            )
            jobs = list(db_session.exec(stmt))
            return jobs

    def find_processing_jobs(self) -> list[JobItem]:
        """Find all jobs currently in PROCESSING status.
        
        Used for startup recovery to identify orphaned jobs.
        
        Returns:
            List of all processing JobItem objects.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = select(JobItem).where(JobItem.status == JobStatus.PROCESSING.value)
            jobs = list(db_session.exec(stmt))
            return jobs

    def list_pending_by_queue(self, queue_id: str) -> list[JobItem]:
        """List pending jobs for a specific queue, ordered by priority.
        
        Args:
            queue_id: Queue identifier.
            
        Returns:
            List of pending JobItem objects for the queue.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = (
                select(JobItem)
                .where(JobItem.queue_id == queue_id)
                .where(JobItem.status == JobStatus.PENDING.value)
                .order_by(col(JobItem.priority).desc(), JobItem.created_at.asc())
            )
            jobs = list(db_session.exec(stmt))
            return jobs

    def list_by_queue(
        self,
        queue_id: str,
        statuses: Optional[list[str]] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[JobItem], int]:
        """List jobs for a specific queue with optional filters and pagination.
        
        Args:
            queue_id: Queue identifier.
            statuses: Optional list of status filters.
            limit: Maximum number of jobs to return.
            offset: Number of jobs to skip.
            
        Returns:
            Tuple of (list of jobs, total count).
        """
        with SQLModelSession(self.engine) as db_session:
            # Build count query
            count_stmt = select(func.count()).select_from(JobItem)
            count_stmt = count_stmt.where(JobItem.queue_id == queue_id)
            if statuses:
                count_stmt = count_stmt.where(JobItem.status.in_(statuses))
            total = db_session.exec(count_stmt).one()

            # Build list query with filters
            stmt = select(JobItem).where(JobItem.queue_id == queue_id)
            if statuses:
                stmt = stmt.where(JobItem.status.in_(statuses))
            
            stmt = stmt.order_by(
                col(JobItem.priority).desc(),
                col(JobItem.created_at).asc()
            ).offset(offset).limit(limit)
            
            jobs = list(db_session.exec(stmt))
            
            return jobs, total

    # --------------------------------------------------------
    # STATE TRANSITIONS
    # --------------------------------------------------------

    def atomic_transition(
        self,
        job_id: str,
        from_status: Optional[str],
        to_status: str,
        **extra_updates: Any,
    ) -> Optional[JobItem]:
        """
        Atomically transition a job's status within a single session.

        Uses SELECT + UPDATE within the same session to ensure atomicity.
        Checks current status to detect concurrent modification or stale state.

        Args:
            job_id: The job to transition.
            from_status: Current expected status (None for creation).
            to_status: Target status.
            **extra_updates: Additional fields to set in the same statement.

        Returns:
            The updated JobItem, or None if job not found.

        Raises:
            InvalidTransitionError: If the transition is invalid or rowcount=0.
        """
        # Lazy import to avoid circular dependency with services package
        from daemon.services.job_state_machine import job_state_machine, InvalidTransitionError

        transition_name = job_state_machine.get_transition_name(from_status, to_status)

        with SQLModelSession(self.engine) as session:
            job = session.get(JobItem, job_id)
            if job is None:
                return None

            # Verify current status matches expected
            if job.status != from_status:
                raise InvalidTransitionError(
                    job_id=job_id,
                    from_status=job.status,
                    to_status=to_status,
                )

            # Validate transition is allowed
            job_state_machine.validate_transition(from_status, to_status)

            # Apply the transition
            job.status = to_status
            for key, value in extra_updates.items():
                setattr(job, key, value)

            session.commit()
            session.refresh(job)

            logger.info(
                "Job transition: %s | %s -> %s (%s) | extra_fields=%s",
                job_id, from_status, to_status, transition_name, list(extra_updates.keys())
            )

            return job

    # --------------------------------------------------------
    # UPDATE
    # --------------------------------------------------------

    def update(self, job_id: str, **updates) -> Optional[JobItem]:
        """Update a job's fields.
        
        Args:
            job_id: Job identifier.
            **updates: Fields to update.
            
        Returns:
            Updated JobItem if found, None otherwise.
        """
        with SQLModelSession(self.engine) as db_session:
            job = db_session.get(JobItem, job_id)
            if job is None:
                return None

            if 'status' in updates and not JobStatus.is_valid(updates['status']):
                raise ValueError(f"Invalid status: {updates['status']}")

            for key, value in updates.items():
                if hasattr(job, key):
                    setattr(job, key, value)

            db_session.commit()
            db_session.refresh(job)

            return job

    def start_job(
        self,
        job_id: str,
        instance_id: str,
    ) -> Optional[JobItem]:
        """Mark a job as processing (started).
        
        Can only be called on PENDING jobs.
        
        Args:
            job_id: Job identifier.
            instance_id: Instance ID that is processing this job.
            
        Returns:
            Updated JobItem if found, None otherwise.
            
        Raises:
            ValueError: If job is not in PENDING state.
        """
        job = self.get(job_id)
        if job is None:
            return None
        if job.status != JobStatus.PENDING.value:
            raise ValueError(
                f"Cannot start job in '{job.status}' state, must be PENDING"
            )
        return self.update(
            job_id,
            status=JobStatus.PROCESSING.value,
            started_at=datetime.utcnow().isoformat(),
            instance_id=instance_id,
        )

    def start_job_atomic(
        self,
        job_id: str,
        instance_id: str,
    ) -> Optional[JobItem]:
        """Start a job atomically (PENDING -> PROCESSING)."""
        now = datetime.utcnow().isoformat()
        return self.atomic_transition(
            job_id,
            from_status=JobStatus.PENDING.value,
            to_status=JobStatus.PROCESSING.value,
            started_at=now,
            instance_id=instance_id,
        )

    def complete_job(
        self,
        job_id: str,
        result_summary: Optional[str] = None,
    ) -> Optional[JobItem]:
        """Complete a job (PROCESSING -> COMPLETED)."""
        now = datetime.utcnow().isoformat()
        return self.atomic_transition(
            job_id,
            from_status=JobStatus.PROCESSING.value,
            to_status=JobStatus.COMPLETED.value,
            completed_at=now,
            result_summary=result_summary,
        )

    def fail_job(
        self,
        job_id: str,
        error_message: str,
    ) -> Optional[JobItem]:
        """Fail a job (PROCESSING -> FAILED)."""
        now = datetime.utcnow().isoformat()
        return self.atomic_transition(
            job_id,
            from_status=JobStatus.PROCESSING.value,
            to_status=JobStatus.FAILED.value,
            completed_at=now,
            error_message=error_message,
        )

    def cancel_job(self, job_id: str) -> Optional[JobItem]:
        """Cancel a job. Works for both PENDING and PROCESSING states."""
        job = self.get(job_id)
        if job is None:
            return None

        now = datetime.utcnow().isoformat()

        if job.status == JobStatus.PENDING.value:
            return self.atomic_transition(
                job_id,
                from_status=JobStatus.PENDING.value,
                to_status=JobStatus.CANCELLED.value,
                cancelled_at=now,
            )
        elif job.status == JobStatus.PROCESSING.value:
            return self.atomic_transition(
                job_id,
                from_status=JobStatus.PROCESSING.value,
                to_status=JobStatus.CANCELLED.value,
                cancelled_at=now,
            )
        else:
            raise ValueError(
                f"Cannot cancel job in '{job.status}' state, must be PENDING or PROCESSING"
            )

    # --------------------------------------------------------
    # DELETE
    # --------------------------------------------------------

    def delete(self, job_id: str) -> dict[str, Any]:
        """Delete a job by ID.
        
        Args:
            job_id: Job identifier.
            
        Returns:
            Dictionary with deletion status.
        """
        with SQLModelSession(self.engine) as db_session:
            job = db_session.get(JobItem, job_id)
            if job is None:
                return {"deleted": False, "job_id": job_id, "error": "Not found"}

            db_session.delete(job)
            db_session.commit()

            return {
                "deleted": True,
                "job_id": job_id,
                "agent_dir": job.agent_dir,
            }

    def delete_completed(self) -> int:
        """Delete all completed jobs.
        
        Returns:
            Number of jobs deleted.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = sql_delete(JobItem).where(
                JobItem.status == JobStatus.COMPLETED.value
            )
            result = db_session.exec(stmt)
            db_session.commit()
            return result.rowcount

    def delete_by_project(self, project_id: str) -> int:
        """Delete all jobs for a specific project.
        
        Args:
            project_id: Project identifier.
            
        Returns:
            Number of jobs deleted.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = sql_delete(JobItem).where(
                JobItem.project_id == project_id
            )
            result = db_session.exec(stmt)
            db_session.commit()
            return result.rowcount

    def find_retryable_jobs(self, project_id: str = None) -> list[JobItem]:
        """Find jobs eligible for retry (FAILED with next_retry_at <= now).
        
        IMPORTANT: This method only finds jobs that are FAILED with next_retry_at
        set and passed. Jobs that are being cancelled (transitioning to CANCELLED)
        are naturally excluded because their status will not be FAILED.
        
        Args:
            project_id: Optional project ID to filter by.
            
        Returns:
            List of JobItem objects that are FAILED and their next_retry_at
            has passed.
        """
        with SQLModelSession(self.engine) as session:
            now = datetime.utcnow().isoformat()
            stmt = (
                select(JobItem)
                .where(JobItem.status == JobStatus.FAILED.value)
                .where(JobItem.next_retry_at.is_not(None))
                .where(col(JobItem.next_retry_at) <= now)
            )
            
            if project_id is not None:
                stmt = stmt.where(JobItem.project_id == project_id)
            
            stmt = stmt.order_by(
                col(JobItem.priority).desc(), JobItem.created_at.asc()
            )
            return list(session.exec(stmt))
