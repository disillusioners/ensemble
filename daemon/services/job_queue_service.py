"""Job Queue Service - Manages job queuing with per-project locking.

This service provides the main interface for job queue operations,
coordinating between the database repository and the lock manager.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from daemon.repositories.job_queue import JobRepository, JobItem, JobStatus
from daemon.services.job_lock_manager import JobLockManager
from daemon.registry import get_registry


class JobQueueService:
    """Manages job queuing with per-project locking.
    
    Provides the main interface for submitting, tracking, and managing
    jobs in a queue with per-project serialization via locks.
    
    Attributes:
        _repository: Database repository for job persistence.
        _lock_manager: Lock manager for per-project job serialization.
    """
    
    def __init__(
        self,
        repository: JobRepository,
        lock_manager: JobLockManager,
    ):
        """Initialize the JobQueueService.
        
        Args:
            repository: Job repository for database operations.
            lock_manager: Lock manager for per-project job serialization.
        """
        self._repository = repository
        self._lock_manager = lock_manager
    
    # ========== Public API ==========
    
    async def enqueue(
        self,
        agent_id: str,
        message: str,
        source: str = "api",
        project_id: Optional[str] = None,
        priority: int = 5,
        metadata: Optional[dict[str, Any]] = None,
    ) -> JobItem:
        """Submit a job for processing.
        
        If project_id is None: create PROCESSING job immediately.
        If project_id provided and lock available: acquire lock, create PROCESSING job.
        If project_id provided and lock held: create PENDING job with queue position.
        
        Args:
            agent_id: Agent ID (e.g., 'coder').
            message: Job message/content.
            source: Source of the job ("api", "telegram", "scheduler", "webhook").
            project_id: Optional project ID for job serialization.
            priority: Job priority (1-10, default 5).
            metadata: Optional metadata dictionary.
            
        Returns:
            JobItem with status and (if immediate) instance_id.
        """
        # Derive agent_dir from agent_id using registry
        registry = get_registry()
        agent_meta = registry.get(agent_id)
        if agent_meta is None:
            raise ValueError(f"Agent not found: {agent_id}")
        agent_dir = str(agent_meta.path)
        
        # Create job once (status defaults to PENDING in repository)
        job = self._repository.create(
            agent_id=agent_id,
            agent_dir=agent_dir,
            message=message,
            source=source,
            project_id=project_id,
            priority=priority,
            job_metadata=metadata,
        )
        
        # If no project_id, execute immediately without locking
        if project_id is None:
            instance_id = str(uuid.uuid4())
            started_job = self._repository.start_job(job.job_id, instance_id)
            assert started_job is not None, f"Failed to start job {job.job_id}"
            return started_job
        
        # Try to acquire lock for this project
        instance_id = str(uuid.uuid4())
        acquired = await self._lock_manager.acquire(
            project_id=project_id,
            job_id=job.job_id,
            instance_id=instance_id,
        )
        
        if acquired:
            try:
                started_job = self._repository.start_job(job.job_id, instance_id)
                assert started_job is not None, f"Failed to start job {job.job_id}"
                return started_job
            except Exception:
                # Release lock on error and re-raise
                await self._lock_manager.release(project_id, job.job_id)
                raise
        
        # Lock is held by another job - keep job as PENDING
        return job
    
    async def get_job(self, job_id: str) -> Optional[JobItem]:
        """Get job by ID.
        
        Args:
            job_id: Unique job identifier.
            
        Returns:
            JobItem if found, None otherwise.
        """
        return self._repository.get(job_id)
    
    async def update_job(self, job_id: str, **updates) -> Optional[JobItem]:
        """Update job fields.
        
        Args:
            job_id: Unique job identifier.
            **updates: Fields to update (e.g., status, result_summary).
            
        Returns:
            Updated JobItem if found, None otherwise.
        """
        return self._repository.update(job_id, **updates)
    
    async def cancel_job(self, job_id: str) -> bool:
        """Cancel a pending job or abort a running job.
        
        Args:
            job_id: Job identifier.
            
        Returns:
            True if cancelled successfully, False if job not found or
            not in a cancellable state.
        """
        job = self._repository.get(job_id)
        if job is None:
            return False
        
        # Can only cancel PENDING jobs
        if job.status == JobStatus.PENDING.value:
            self._repository.cancel_job(job_id)
            return True
        
        # Can abort PROCESSING jobs (release lock)
        if job.status == JobStatus.PROCESSING.value:
            # Release the lock held by this job's instance
            if job.instance_id:
                await self._lock_manager.release_by_instance(job.instance_id)
            # Use update() instead of cancel_job() since PROCESSING jobs
            # can't be cancelled via cancel_job() (raises ValueError)
            self._repository.update(
                job_id,
                status=JobStatus.CANCELLED.value,
                cancelled_at=datetime.utcnow().isoformat(),
            )
            return True
        
        return False
    
    async def retry_job(self, job_id: str) -> Optional[JobItem]:
        """Retry a failed job by creating a new job with the same parameters.
        
        Creates a new job with the same parameters and starts it immediately
        if possible (no lock contention), otherwise queues it.
        
        Args:
            job_id: Job identifier of the failed job to retry.
            
        Returns:
            New JobItem if retry successful, None if job not found or
            not in a retryable state (not FAILED).
        """
        job = self._repository.get(job_id)
        if job is None:
            return None
        
        # Can only retry FAILED jobs
        if job.status != JobStatus.FAILED.value:
            return None
        
        # Create a new job and use enqueue logic to determine if it should
        # start immediately or be queued
        new_job = await self.enqueue(
            agent_id=job.agent_id,
            message=job.message,
            source=job.source,
            project_id=job.project_id,
            priority=job.priority,
            metadata=job.job_metadata,
        )
        
        return new_job
    
    async def list_jobs(
        self,
        status: Optional[JobStatus] = None,
        project_id: Optional[str] = None,
        limit: int = 50,
    ) -> list[JobItem]:
        """List jobs with optional filters.
        
        Args:
            status: Optional status filter.
            project_id: Optional project ID filter.
            limit: Maximum number of jobs to return.
            
        Returns:
            List of JobItem objects.
        """
        status_value = status.value if status else None
        jobs, _ = self._repository.list(
            status=status_value,
            project_id=project_id,
            limit=limit,
        )
        return jobs
    
    # ========== Helper Methods ==========
    
    def _try_start_job(self, job: JobItem) -> bool:
        """Try to start a pending job.
        
        Attempts to acquire the lock for the job's project and start
        processing the job.
        
        Args:
            job: The pending job to start.
            
        Returns:
            True if job was started, False otherwise.
        """
        if job.project_id is None:
            # No project, can start immediately
            instance_id = str(uuid.uuid4())
            self._repository.start_job(job.job_id, instance_id)
            return True
        
        # Try to acquire lock
        instance_id = str(uuid.uuid4())
        # Use synchronous acquire since we're in a sync context
        acquired = self._lock_manager.acquire_sync(
            project_id=job.project_id,
            job_id=job.job_id,
            instance_id=instance_id,
        )
        
        if acquired:
            self._repository.start_job(job.job_id, instance_id)
            return True
        
        return False
    
    def _complete_job(self, job: JobItem, result_summary: Optional[str]) -> None:
        """Mark a job as completed and release its lock.
        
        Args:
            job: The processing job to complete.
            result_summary: Optional summary of the job result.
        """
        # Release the lock first
        if job.project_id and job.instance_id:
            self._lock_manager.release_sync(job.project_id, job.job_id)
        
        # Mark job as completed
        self._repository.complete_job(job.job_id, result_summary)
    
    def _fail_job(self, job: JobItem, error_message: str) -> None:
        """Mark a job as failed and release its lock.
        
        Args:
            job: The processing job that failed.
            error_message: Error message describing the failure.
        """
        # Release the lock first
        if job.project_id and job.instance_id:
            self._lock_manager.release_sync(job.project_id, job.job_id)
        
        # Mark job as failed
        self._repository.fail_job(job.job_id, error_message)
    
    def _get_next_job(self, project_id: Optional[str]) -> Optional[JobItem]:
        """Get the next pending job for a project.
        
        Args:
            project_id: Optional project ID to get next job for.
                       If None, gets next pending job regardless of project.
            
        Returns:
            Next JobItem to process, or None if no pending jobs.
        """
        if project_id:
            pending = self._repository.list_pending_by_project(project_id)
            return pending[0] if pending else None
        else:
            pending = self._repository.list_all_pending()
            return pending[0] if pending else None
    
    def _get_queue_position(self, job_id: Optional[str], project_id: str) -> int:
        """Get the queue position for a job in its project.
        
        Returns the 1-based position of the job in the pending queue
        for its project, ordered by priority (desc) then created_at (asc).
        
        Args:
            job_id: Optional job ID to find position for.
                    If None, returns count of pending jobs + 1.
            project_id: The project to get queue position for.
            
        Returns:
            1-based queue position, or None if job not found in pending queue.
        """
        pending = self._repository.list_pending_by_project(project_id)
        
        if job_id is None:
            # Return position as if this job was added to end
            return len(pending) + 1
        
        for i, job in enumerate(pending, start=1):
            if job.job_id == job_id:
                return i
        
        return len(pending) + 1

    # ========== JobProcessor Helper Methods ==========
    
    def get_next_pending_job(self) -> Optional[JobItem]:
        """Get the next pending job (highest priority, oldest first).
        
        Returns the first pending job from all projects, ordered by
        priority (descending) then created_at (ascending).
        
        Returns:
            Next JobItem to process, or None if no pending jobs.
        """
        pending = self._repository.list_all_pending()
        return pending[0] if pending else None
    
    async def start_job(self, job_id: str) -> Optional[JobItem]:
        """Mark job as processing and acquire lock.
        
        Attempts to acquire the lock for the job's project and mark
        the job as PROCESSING.
        
        Args:
            job_id: The job ID to start.
            
        Returns:
            Updated JobItem if started successfully, None if
            job not found, cancelled, or lock acquisition failed.
        """
        job = self._repository.get(job_id)
        if job is None:
            return None
        
        # Check if job is still pending (could have been cancelled)
        if job.status != JobStatus.PENDING.value:
            return None
        
        # Generate new instance ID for this job
        instance_id = str(uuid.uuid4())
        
        # If no project_id, start immediately without locking
        if job.project_id is None:
            try:
                return self._repository.start_job(job_id, instance_id)
            except ValueError:
                return None
        
        # Try to acquire lock
        acquired = await self._lock_manager.acquire(
            project_id=job.project_id,
            job_id=job_id,
            instance_id=instance_id,
        )
        
        if not acquired:
            # Lock is held by another job
            return None
        
        try:
            return self._repository.start_job(job_id, instance_id)
        except ValueError:
            # Job state changed between check and start
            await self._lock_manager.release(job.project_id, job_id)
            return None
    
    async def complete_job(
        self,
        job_id: str,
        success: bool = True,
        error: Optional[str] = None,
    ) -> Optional[JobItem]:
        """Mark job as completed or failed and release lock.
        
        Args:
            job_id: The job ID to complete.
            success: True to mark as completed, False to mark as failed.
            error: Error message if success=False.
            
        Returns:
            Updated JobItem if completed successfully, None if
            job not found or not in a processable state.
        """
        job = self._repository.get(job_id)
        if job is None:
            return None
        
        # Release the lock first
        if job.project_id:
            await self._lock_manager.release(job.project_id, job_id)
        
        # Mark job based on success/failure
        try:
            if success:
                return self._repository.complete_job(job_id, result_summary="Job queued successfully")
            else:
                return self._repository.fail_job(job_id, error_message=error or "Unknown error")
        except ValueError:
            # Job state changed (already completed/cancelled)
            return None
    
    async def trigger_next_job(self, project_id: str) -> Optional[JobItem]:
        """Trigger the next pending job for a project.
        
        Called after a job completes to process any waiting jobs
        for the same project.
        
        Args:
            project_id: The project to trigger next job for.
            
        Returns:
            The next JobItem started, or None if no pending jobs.
        """
        next_job = self._get_next_job(project_id)
        if next_job is None:
            return None
        
        return await self.start_job(next_job.job_id)
    
    async def release_lock_by_instance(self, instance_id: str) -> list[str]:
        """Release any locks held by an instance.
        
        This method is called during instance termination to clean up
        any project locks that the instance's jobs were holding.
        
        Args:
            instance_id: The instance to release locks for.
            
        Returns:
            List of project_ids that were released.
        """
        return await self._lock_manager.release_by_instance(instance_id)
    
    def release_locks_by_instance_sync(self, instance_id: str) -> list[str]:
        """Release any locks held by an instance (synchronous version).
        
        This method is used during manager shutdown when async context
        is not available. Waiter notification is scheduled via the event loop.
        
        Args:
            instance_id: The instance to release locks for.
            
        Returns:
            List of project_ids that were released.
        """
        return self._lock_manager.release_by_instance_sync(instance_id)
