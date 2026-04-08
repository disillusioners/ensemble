"""Job Queue Service - Manages job queuing with per-queue locking.

This service provides the main interface for job queue operations,
coordinating between the database repository and the lock manager.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any, Optional

from daemon.repositories.job_queue import JobRepository, JobQueueRepository, JobItem, JobStatus
from daemon.services.job_lock_manager import JobLockManager
from daemon.registry import get_registry

logger = logging.getLogger(__name__)


class JobQueueService:
    """Manages job queuing with per-queue locking.
    
    Provides the main interface for submitting, tracking, and managing
    jobs in a queue with per-queue serialization via locks.
    
    Attributes:
        _repository: Database repository for job persistence.
        _lock_manager: Lock manager for per-queue job serialization.
        _queue_repo: Queue repository for queue metadata and concurrency limits.
    """
    
    def __init__(
        self,
        repository: JobRepository,
        lock_manager: JobLockManager,
        queue_repo: JobQueueRepository,
    ):
        """Initialize the JobQueueService.
        
        Args:
            repository: Job repository for database operations.
            lock_manager: Lock manager for per-queue job serialization.
            queue_repo: Queue repository for queue metadata and concurrency limits.
        """
        self._repository = repository
        self._lock_manager = lock_manager
        self._queue_repo = queue_repo
    
    # ========== Public API ==========
    
    async def enqueue(
        self,
        agent_id: str,
        message: str,
        source: str = "api",
        project_id: Optional[str] = None,
        priority: int = 5,
        metadata: Optional[dict[str, Any]] = None,
        queue_id: Optional[str] = None,
    ) -> JobItem:
        """Submit a job for processing.
        
        Jobs are always created as PENDING. The JobProcessor picks up pending
        jobs, transitions them to PROCESSING, spawns instances, and enqueues
        messages for processing.
        
        If project_id is set but queue_id is None, the job is assigned to the
        project's "system_fifo_queue" automatically.
        
        Args:
            agent_id: Agent ID (e.g., 'coder').
            message: Job message/content.
            source: Source of the job ("api", "telegram", "scheduler", "webhook").
            project_id: Optional project ID for job serialization.
            priority: Job priority (1-10, default 5).
            metadata: Optional metadata dictionary.
            queue_id: Optional queue ID for job routing. If None and project_id
                     is set, defaults to the project's system_fifo_queue.
            
        Returns:
            JobItem with PENDING status.
            
        Raises:
            ValueError: If project_id is set but system_fifo_queue doesn't exist.
        """
        # Derive agent_dir from agent_id using registry
        registry = get_registry()
        agent_meta = registry.get(agent_id)
        if agent_meta is None:
            raise ValueError(f"Agent not found: {agent_id}")
        agent_dir = str(agent_meta.path)
        
        # Resolve queue_id for projects
        resolved_queue_id = queue_id
        if project_id and queue_id is None:
            # Default to system_fifo_queue for projects (if it exists)
            queue = await asyncio.to_thread(
                self._queue_repo.get_by_name, project_id, "system_fifo_queue"
            )
            if queue is not None:
                resolved_queue_id = queue.queue_id
            else:
                # System queue doesn't exist - log warning and continue without queue
                logger.warning(
                    f"System queue 'system_fifo_queue' not found for project '{project_id}'. "
                    f"Job will be queued without per-queue locking. "
                    f"Consider provisioning system queues for this project."
                )
        elif queue_id and project_id:
            # Validate queue exists and belongs to project
            queue = await asyncio.to_thread(self._queue_repo.get, queue_id)
            if queue is None:
                logger.warning(
                    f"Queue '{queue_id}' not found, job will be created without queue assignment"
                )
                resolved_queue_id = None
            elif queue.project_id != project_id:
                logger.warning(
                    f"Queue '{queue_id}' belongs to different project '{queue.project_id}', "
                    f"job project is '{project_id}'"
                )
                # Still use the queue_id as specified
        
        # Create job with PENDING status - JobProcessor will handle the rest
        job = await asyncio.to_thread(
            self._repository.create,
            agent_id=agent_id,
            agent_dir=agent_dir,
            message=message,
            source=source,
            project_id=project_id,
            priority=priority,
            job_metadata=metadata,
            queue_id=resolved_queue_id,
        )
        
        return job
    
    async def get_job(self, job_id: str) -> Optional[JobItem]:
        """Get job by ID.
        
        Args:
            job_id: Unique job identifier.
            
        Returns:
            JobItem if found, None otherwise.
        """
        return await asyncio.to_thread(self._repository.get, job_id)
    
    async def get_job_by_instance(self, instance_id: str) -> Optional[JobItem]:
        """Get job by instance ID.
        
        Args:
            instance_id: Instance identifier.
            
        Returns:
            JobItem if found, None otherwise.
        """
        return await asyncio.to_thread(self._repository.get_by_instance, instance_id)
    
    def get_job_by_instance_sync(self, instance_id: str) -> Optional[JobItem]:
        """Get job by instance ID (synchronous version).
        
        For use from synchronous callers like terminate_instance().
        
        Args:
            instance_id: Instance identifier.
            
        Returns:
            JobItem if found, None otherwise.
        """
        return self._repository.get_by_instance(instance_id)
    
    async def update_job(self, job_id: str, **updates) -> Optional[JobItem]:
        """Update job fields.
        
        Args:
            job_id: Unique job identifier.
            **updates: Fields to update (e.g., status, result_summary).
            
        Returns:
            Updated JobItem if found, None otherwise.
        """
        return await asyncio.to_thread(self._repository.update, job_id, **updates)
    
    async def cancel_job(self, job_id: str) -> bool:
        """Cancel a pending job or abort a running job.
        
        Args:
            job_id: Job identifier.
            
        Returns:
            True if cancelled successfully, False if job not found or
            not in a cancellable state.
        """
        job = await asyncio.to_thread(self._repository.get, job_id)
        if job is None:
            return False
        
        # Can only cancel PENDING jobs
        if job.status == JobStatus.PENDING.value:
            await asyncio.to_thread(self._repository.cancel_job, job_id)
            return True
        
        # Can abort PROCESSING jobs (release lock)
        if job.status == JobStatus.PROCESSING.value:
            # Release the per-queue lock held by this job's instance
            if job.instance_id:
                await self._lock_manager.release_by_instance(job.instance_id)
            
            # Release queue lock if job has queue_id
            if job.queue_id and job.project_id:
                await self._lock_manager.release_queue_lock(
                    job.project_id, job.queue_id, job.job_id
                )
            
            # Use update() instead of cancel_job() since PROCESSING jobs
            # can't be cancelled via cancel_job() (raises ValueError)
            await asyncio.to_thread(
                self._repository.update,
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
        job = await asyncio.to_thread(self._repository.get, job_id)
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
        jobs, _ = await asyncio.to_thread(
            self._repository.list,
            status=status_value,
            project_id=project_id,
            limit=limit,
        )
        return jobs
    
    # ========== Helper Methods ==========
    
    async def _get_concurrency_limit(self, queue_id: str) -> int:
        """Get the concurrency limit for a queue.
        
        Args:
            queue_id: The queue ID to get concurrency limit for.
            
        Returns:
            The concurrency limit, defaulting to 1 if queue not found.
        """
        queue = await asyncio.to_thread(self._queue_repo.get, queue_id)
        if queue is None:
            logger.warning(f"Queue '{queue_id}' not found, using default concurrency_limit=1")
            return 1
        return queue.concurrency_limit
    
    async def _try_start_job(self, job: JobItem) -> bool:
        """Try to start a pending job.
        
        Attempts to acquire the lock for the job's queue and start
        processing the job atomically.
        
        Args:
            job: The pending job to start.
            
        Returns:
            True if job was started, False otherwise.
        """
        instance_id = str(uuid.uuid4())
        
        # If job has queue_id, use per-queue locking with concurrency limit
        if job.queue_id and job.project_id:
            concurrency_limit = await self._get_concurrency_limit(job.queue_id)
            
            acquired = await self._lock_manager.acquire_queue_lock(
                project_id=job.project_id,
                queue_id=job.queue_id,
                job_id=job.job_id,
                instance_id=instance_id,
                concurrency_limit=concurrency_limit,
            )
            
            if not acquired:
                return False
            
            # Atomically start the job
            try:
                await asyncio.to_thread(
                    self._repository.start_job_atomic, job.job_id, instance_id
                )
                return True
            except ValueError:
                # Job state changed (already started/cancelled)
                await self._lock_manager.release_queue_lock(
                    job.project_id, job.queue_id, job.job_id
                )
                return False
        
        # If job has project_id but no queue_id, use backward-compatible project-based locking
        if job.project_id:
            acquired = await self._lock_manager.acquire(
                project_id=job.project_id,
                job_id=job.job_id,
                instance_id=instance_id,
            )
            
            if not acquired:
                return False
            
            try:
                await asyncio.to_thread(
                    self._repository.start_job_atomic, job.job_id, instance_id
                )
                return True
            except ValueError:
                await self._lock_manager.release(job.project_id, job.job_id)
                return False
        
        # No project_id - start immediately without locking
        try:
            await asyncio.to_thread(
                self._repository.start_job_atomic, job.job_id, instance_id
            )
            return True
        except ValueError:
            return False
    
    async def _complete_job(self, job: JobItem, result_summary: Optional[str]) -> None:
        """Mark a job as completed and release its lock.
        
        Args:
            job: The processing job to complete.
            result_summary: Optional summary of the job result.
        """
        # Release the lock first
        if job.queue_id and job.project_id:
            await self._lock_manager.release_queue_lock(
                job.project_id, job.queue_id, job.job_id
            )
        elif job.project_id:
            # Backward compatibility: project without queue - release by instance
            if job.instance_id:
                await self._lock_manager.release_by_instance(job.instance_id)
        
        # Mark job as completed
        await asyncio.to_thread(self._repository.complete_job, job.job_id, result_summary)
    
    async def _fail_job(self, job: JobItem, error_message: str) -> None:
        """Mark a job as failed and release its lock.
        
        Args:
            job: The processing job that failed.
            error_message: Error message describing the failure.
        """
        # Release the lock first
        if job.queue_id and job.project_id:
            await self._lock_manager.release_queue_lock(
                job.project_id, job.queue_id, job.job_id
            )
        elif job.project_id:
            # Backward compatibility: project without queue - release by instance
            if job.instance_id:
                await self._lock_manager.release_by_instance(job.instance_id)
        
        # Mark job as failed
        await asyncio.to_thread(self._repository.fail_job, job.job_id, error_message)
    
    async def _get_next_job(
        self,
        project_id: Optional[str] = None,
        queue_id: Optional[str] = None,
    ) -> Optional[JobItem]:
        """Get the next pending job for a queue or project.
        
        Args:
            project_id: Optional project ID to get next job for.
                       If None, gets next pending job regardless of project.
            queue_id: Optional queue ID to get next job for.
                     Takes precedence over project_id if specified.
            
        Returns:
            Next JobItem to process, or None if no pending jobs.
        """
        if queue_id:
            pending = await asyncio.to_thread(
                self._repository.list_pending_by_queue, queue_id
            )
            return pending[0] if pending else None
        elif project_id:
            pending = await asyncio.to_thread(
                self._repository.list_pending_by_project, project_id
            )
            return pending[0] if pending else None
        else:
            pending = await asyncio.to_thread(self._repository.list_all_pending)
            return pending[0] if pending else None
    
    async def _get_queue_position(
        self,
        job_id: Optional[str],
        project_id: str,
        queue_id: Optional[str] = None,
    ) -> int:
        """Get the queue position for a job in its queue.
        
        Returns the 1-based position of the job in the pending queue,
        ordered by priority (desc) then created_at (asc).
        
        Args:
            job_id: Optional job ID to find position for.
                    If None, returns count of pending jobs + 1.
            project_id: The project to get queue position for.
            queue_id: Optional specific queue to check position in.
            
        Returns:
            1-based queue position, or position after all pending jobs if job not found.
        """
        if queue_id:
            pending = await asyncio.to_thread(
                self._repository.list_pending_by_queue, queue_id
            )
        else:
            pending = await asyncio.to_thread(
                self._repository.list_pending_by_project, project_id
            )
        
        if job_id is None:
            # Return position as if this job was added to end
            return len(pending) + 1
        
        for i, job in enumerate(pending, start=1):
            if job.job_id == job_id:
                return i
        
        return len(pending) + 1

    # ========== JobProcessor Helper Methods ==========
    
    async def get_next_pending_job(self) -> Optional[JobItem]:
        """Get the next pending job (highest priority, oldest first).
        
        Returns the first pending job from all projects, ordered by
        priority (descending) then created_at (ascending).
        
        Returns:
            Next JobItem to process, or None if no pending jobs.
        """
        pending = await asyncio.to_thread(self._repository.list_all_pending)
        return pending[0] if pending else None
    
    async def start_job(self, job_id: str) -> Optional[JobItem]:
        """Mark job as processing and acquire lock.
        
        Attempts to acquire the lock for the job's queue and mark
        the job as PROCESSING atomically.
        
        Args:
            job_id: The job ID to start.
            
        Returns:
            Updated JobItem if started successfully, None if
            job not found, cancelled, or lock acquisition failed.
        """
        job = await asyncio.to_thread(self._repository.get, job_id)
        if job is None:
            return None
        
        # Check if job is still pending (could have been cancelled)
        if job.status != JobStatus.PENDING.value:
            return None
        
        # Generate new instance ID for this job
        instance_id = str(uuid.uuid4())
        
        # If job has queue_id, use per-queue locking with concurrency limit
        if job.queue_id and job.project_id:
            concurrency_limit = await self._get_concurrency_limit(job.queue_id)
            
            # Try to acquire queue lock
            acquired = await self._lock_manager.acquire_queue_lock(
                project_id=job.project_id,
                queue_id=job.queue_id,
                job_id=job_id,
                instance_id=instance_id,
                concurrency_limit=concurrency_limit,
            )
            
            if not acquired:
                # Queue is at capacity
                return None
            
            try:
                # Atomically start the job
                return await asyncio.to_thread(
                    self._repository.start_job_atomic, job_id, instance_id
                )
            except ValueError:
                # Job state changed between check and start
                await self._lock_manager.release_queue_lock(
                    job.project_id, job.queue_id, job_id
                )
                return None
        
        # If job has project_id but no queue_id, use backward-compatible project-based locking
        if job.project_id:
            acquired = await self._lock_manager.acquire(
                project_id=job.project_id,
                job_id=job_id,
                instance_id=instance_id,
            )
            
            if not acquired:
                return None
            
            try:
                return await asyncio.to_thread(
                    self._repository.start_job_atomic, job_id, instance_id
                )
            except ValueError:
                await self._lock_manager.release(job.project_id, job_id)
                return None
        
        # No project_id - start immediately without locking
        try:
            return await asyncio.to_thread(
                self._repository.start_job_atomic, job_id, instance_id
            )
        except ValueError:
            return None
    
    async def complete_job(
        self,
        job_id: str,
        success: bool = True,
        error: Optional[str] = None,
        result_summary: Optional[str] = None,
    ) -> Optional[JobItem]:
        """Mark job as completed or failed and release lock.
        
        Args:
            job_id: The job ID to complete.
            success: True to mark as completed, False to mark as failed.
            error: Error message if success=False.
            result_summary: Optional summary text for completed jobs.
            
        Returns:
            Updated JobItem if completed successfully, None if
            job not found or not in a processable state.
        """
        job = await asyncio.to_thread(self._repository.get, job_id)
        if job is None:
            return None
        
        # Release the per-queue lock first
        if job.queue_id and job.project_id:
            await self._lock_manager.release_queue_lock(
                job.project_id, job.queue_id, job_id
            )
        elif job.project_id:
            # Backward compatibility: project without queue
            await self._lock_manager.release(job.project_id, job_id)
        
        # Mark job based on success/failure
        try:
            if success:
                summary = result_summary or "Job completed successfully"
                return await asyncio.to_thread(
                    self._repository.complete_job, job_id, result_summary=summary
                )
            else:
                return await asyncio.to_thread(
                    self._repository.fail_job, job_id, error_message=error or "Unknown error"
                )
        except ValueError:
            # Job state changed (already completed/cancelled)
            return None
    
    def complete_job_sync(
        self,
        job_id: str,
        success: bool,
        error: Optional[str] = None,
        result_summary: Optional[str] = None,
    ) -> Optional[JobItem]:
        """Mark job as completed or failed and release lock (synchronous version).
        
        NOTE: This is a legacy method. The lock manager is now fully async.
        For new code, use the async complete_job() method instead.
        
        This is the synchronous counterpart to complete_job(), used when
        async context is not available (e.g., from terminate_instance).
        
        Args:
            job_id: The job ID to complete.
            success: True to mark as completed, False to mark as failed.
            error: Error message if success=False.
            result_summary: Optional summary of the job result (for success=True).
            
        Returns:
            Updated JobItem if completed successfully, None if
            job not found or not in a processable state.
            
        Raises:
            ValueError: If job is already completed or cancelled.
        """
        job = self._repository.get(job_id)
        if job is None:
            return None
        
        # NOTE: Lock release must be done asynchronously in production code.
        # This sync method cannot properly release per-queue locks.
        # TODO: Migrate all callers to async complete_job()
        if job.queue_id and job.project_id:
            logger.warning(
                f"complete_job_sync called for job {job_id} with queue_id. "
                "Lock release will not work properly. Use async complete_job() instead."
            )
        elif job.project_id:
            # Use synchronous release for backward compatibility
            self._lock_manager.release_sync(job.project_id, job_id)
        
        # Mark job based on success/failure
        try:
            if success:
                return self._repository.complete_job(job_id, result_summary=result_summary)
            else:
                return self._repository.fail_job(job_id, error_message=error or "Unknown error")
        except ValueError:
            # Job state changed (already completed/cancelled)
            return None
    
    async def trigger_next_job(
        self,
        project_id: str,
        queue_id: Optional[str] = None,
    ) -> Optional[JobItem]:
        """Trigger the next pending job for a queue or project.
        
        Called after a job completes to process any waiting jobs
        for the same queue or project.
        
        Args:
            project_id: The project to trigger next job for.
            queue_id: Optional specific queue to trigger next job for.
                     Takes precedence over project_id.
            
        Returns:
            The next JobItem started, or None if no pending jobs.
        """
        next_job = await self._get_next_job(project_id, queue_id)
        if next_job is None:
            return None
        
        return await self.start_job(next_job.job_id)
    
    def trigger_next_job_sync(
        self,
        project_id: str,
        queue_id: Optional[str] = None,
    ) -> Optional[JobItem]:
        """Trigger the next pending job for a queue or project (synchronous version).
        
        NOTE: This method has limitations with the new async-only lock manager.
        For new code, prefer the async trigger_next_job() method.
        
        Called after a job completes to process any waiting jobs
        for the same queue or project.
        
        Args:
            project_id: The project to trigger next job for.
            queue_id: Optional specific queue to trigger next job for.
            
        Returns:
            The next JobItem started, or None if no pending jobs.
        """
        # TODO: This sync method cannot properly use the async-only lock manager.
        # Migrate all callers to async trigger_next_job()
        
        # Get next pending job
        if queue_id:
            pending = self._repository.list_pending_by_queue(queue_id)
        else:
            pending = self._repository.list_pending_by_project(project_id)
        
        next_job = pending[0] if pending else None
        if next_job is None:
            return None
        
        # Get the job
        job = self._repository.get(next_job.job_id)
        if job is None:
            return None
        
        # Check if job is still pending
        if job.status != JobStatus.PENDING.value:
            return None
        
        # Generate new instance ID for this job
        instance_id = str(uuid.uuid4())
        
        # If job has queue_id, we can't properly acquire async lock in sync context
        if job.queue_id and job.project_id:
            logger.warning(
                f"trigger_next_job_sync called with queue_id for job {job.job_id}. "
                "Lock acquisition will not work properly. Use async trigger_next_job() instead."
            )
            # Still try to start job atomically
            try:
                return self._repository.start_job_atomic(next_job.job_id, instance_id)
            except ValueError:
                return None
        
        # If job has project_id but no queue_id, try backward-compatible locking
        if job.project_id:
            acquired = self._lock_manager.acquire_sync(
                project_id=job.project_id,
                job_id=next_job.job_id,
                instance_id=instance_id,
            )
            
            if not acquired:
                return None
            
            try:
                return self._repository.start_job(next_job.job_id, instance_id)
            except ValueError:
                self._lock_manager.release_sync(job.project_id, next_job.job_id)
                return None
        
        # No project_id - start immediately without locking
        try:
            return self._repository.start_job(next_job.job_id, instance_id)
        except ValueError:
            return None
    
    async def release_lock_by_instance(self, instance_id: str) -> list[str]:
        """Release any locks held by an instance.
        
        This method is called during instance termination to clean up
        any queue locks that the instance's jobs were holding.
        
        Args:
            instance_id: The instance to release locks for.
            
        Returns:
            List of project_ids that were released (deduplicated).
        """
        released = await self._lock_manager.release_by_instance(instance_id)
        # Return unique project_ids for backward compatibility
        return list(set(project_id for project_id, _ in released))
    
    def release_locks_by_instance_sync(self, instance_id: str) -> list[str]:
        """Release any locks held by an instance (synchronous version).
        
        NOTE: The lock manager is now fully async. This sync method cannot
        properly release locks. It logs a warning and returns an empty list.
        
        For production code, use the async release_lock_by_instance() method.
        
        Args:
            instance_id: The instance to release locks for.
            
        Returns:
            Empty list with a warning log. Lock release must be done asynchronously.
        """
        logger.warning(
            f"release_locks_by_instance_sync called for instance {instance_id}. "
            "Lock release cannot be done synchronously. Use async release_lock_by_instance() instead."
        )
        return []
