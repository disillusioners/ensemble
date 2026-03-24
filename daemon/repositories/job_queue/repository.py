"""SQLModel-based JobQueue Repository implementation."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import delete as sql_delete, func
from sqlalchemy.engine import Engine
from sqlmodel import Session as SQLModelSession, select, col

from .models import JobItem, JobStatus


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

    def get_by_session(self, session_id: str) -> Optional[JobItem]:
        """Get a job by session ID.
        
        Args:
            session_id: Session identifier.
            
        Returns:
            JobItem if found, None otherwise.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = select(JobItem).where(JobItem.session_id == session_id)
            job = db_session.exec(stmt).first()
            return job

    # --------------------------------------------------------
    # LIST
    # --------------------------------------------------------

    def list(
        self,
        status: Optional[str] = None,
        project_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[JobItem], int]:
        """List jobs with optional filters and pagination.
        
        Args:
            status: Optional status filter.
            project_id: Optional project ID filter.
            limit: Maximum number of jobs to return.
            offset: Number of jobs to skip.
            
        Returns:
            Tuple of (list of jobs, total count).
        """
        with SQLModelSession(self.engine) as db_session:
            # Build count query
            count_stmt = select(func.count()).select_from(JobItem)
            if status:
                count_stmt = count_stmt.where(JobItem.status == status)
            if project_id:
                count_stmt = count_stmt.where(JobItem.project_id == project_id)
            total = db_session.exec(count_stmt).one()

            # Build list query with filters
            stmt = select(JobItem)
            if status:
                stmt = stmt.where(JobItem.status == status)
            if project_id:
                stmt = stmt.where(JobItem.project_id == project_id)
            
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
        session_id: str,
    ) -> Optional[JobItem]:
        """Mark a job as processing (started).
        
        Can only be called on PENDING jobs.
        
        Args:
            job_id: Job identifier.
            session_id: Session ID that is processing this job.
            
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
            session_id=session_id,
        )

    def complete_job(
        self,
        job_id: str,
        result_summary: Optional[str] = None,
    ) -> Optional[JobItem]:
        """Mark a job as completed.
        
        Can only be called on PROCESSING jobs.
        
        Args:
            job_id: Job identifier.
            result_summary: Optional summary of the job result.
            
        Returns:
            Updated JobItem if found, None otherwise.
            
        Raises:
            ValueError: If job is not in PROCESSING state.
        """
        job = self.get(job_id)
        if job is None:
            return None
        if job.status != JobStatus.PROCESSING.value:
            raise ValueError(
                f"Cannot complete job in '{job.status}' state, must be PROCESSING"
            )
        return self.update(
            job_id,
            status=JobStatus.COMPLETED.value,
            completed_at=datetime.utcnow().isoformat(),
            result_summary=result_summary,
        )

    def fail_job(
        self,
        job_id: str,
        error_message: str,
    ) -> Optional[JobItem]:
        """Mark a job as failed.
        
        Can only be called on PROCESSING jobs.
        
        Args:
            job_id: Job identifier.
            error_message: Error message describing the failure.
            
        Returns:
            Updated JobItem if found, None otherwise.
            
        Raises:
            ValueError: If job is not in PROCESSING state.
        """
        job = self.get(job_id)
        if job is None:
            return None
        if job.status != JobStatus.PROCESSING.value:
            raise ValueError(
                f"Cannot fail job in '{job.status}' state, must be PROCESSING"
            )
        return self.update(
            job_id,
            status=JobStatus.FAILED.value,
            completed_at=datetime.utcnow().isoformat(),
            error_message=error_message,
        )

    def cancel_job(self, job_id: str) -> Optional[JobItem]:
        """Mark a job as cancelled.
        
        Can only be called on PENDING jobs.
        
        Args:
            job_id: Job identifier.
            
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
                f"Cannot cancel job in '{job.status}' state, must be PENDING"
            )
        return self.update(
            job_id,
            status=JobStatus.CANCELLED.value,
            cancelled_at=datetime.utcnow().isoformat(),
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
