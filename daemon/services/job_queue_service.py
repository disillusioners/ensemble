"""Job Queue Service - Manages job queuing with per-queue locking.

This service provides the main interface for job queue operations,
coordinating between the database repository and the lock manager.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from daemon.manager import InstanceManager
    from daemon.services.dispatch_event_bus import DispatchEventBus

from daemon.repositories.job_queue import JobRepository, JobQueueRepository, JobItem, JobStatus
from daemon.services.job_lock_manager import JobLockManager
from daemon.services.job_state_machine import job_state_machine, InvalidTransitionError
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
        instance_manager: Optional["InstanceManager"] = None,
    ):
        """Initialize the JobQueueService.
        
        Args:
            repository: Job repository for database operations.
            lock_manager: Lock manager for per-queue job serialization.
            queue_repo: Queue repository for queue metadata and concurrency limits.
            instance_manager: Optional instance manager for terminating PROCESSING jobs.
        """
        self._repository = repository
        self._lock_manager = lock_manager
        self._queue_repo = queue_repo
        self._instance_manager = instance_manager
        self._retry_engine = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._dispatch_bus: Optional["DispatchEventBus"] = None  # Dispatch event bus for job notifications
        self._idempotency_key_ttl_hours: int = 24  # Default TTL for idempotency key deduplication
    
    def set_retry_engine(self, retry_engine) -> None:
        """Set the retry engine for auto-retry functionality.
        
        Args:
            retry_engine: The JobRetryEngine instance to use for auto-retries.
        """
        self._retry_engine = retry_engine
    
    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Store the event loop for sync→async operations.
        
        Args:
            loop: The running event loop to use for async operations.
        """
        self._loop = loop
    
    def set_instance_manager(self, instance_manager) -> None:
        """Set the InstanceManager reference for cancellation cascade.
        
        Args:
            instance_manager: InstanceManager instance.
        """
        self._instance_manager = instance_manager
    
    def set_dispatch_bus(self, dispatch_bus: "DispatchEventBus") -> None:
        """Set the dispatch event bus for notifying new jobs.
        
        Args:
            dispatch_bus: DispatchEventBus instance.
        """
        self._dispatch_bus = dispatch_bus
    
    def set_config(self, config: Any) -> None:
        """Set job system config for TTL and other settings.
        
        Args:
            config: JobSystemConfig instance with idempotency_key_ttl_hours and other settings.
        """
        if hasattr(config, 'idempotency_key_ttl_hours'):
            self._idempotency_key_ttl_hours = config.idempotency_key_ttl_hours
    
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
        idempotency_key: Optional[str] = None,
    ) -> JobItem:
        """Submit a job for processing.
        
        Jobs are always created as PENDING. The JobProcessor picks up pending
        jobs, transitions them to PROCESSING, spawns instances, and enqueues
        messages for processing.
        
        If project_id is set but queue_id is None, the job is assigned to the
        project's "system_fifo_queue" automatically.
        
        With idempotency_key: if a job with the same key exists and is non-terminal,
        returns the existing job instead of creating a duplicate.
        
        Args:
            agent_id: Agent ID (e.g., 'coder').
            message: Job message/content.
            source: Source of the job ("api", "telegram", "scheduler", "webhook").
            project_id: Optional project ID for job serialization.
            priority: Job priority (1-10, default 5).
            metadata: Optional metadata dictionary.
            queue_id: Optional queue ID for job routing. If None and project_id
                     is set, defaults to the project's system_fifo_queue.
            idempotency_key: Optional idempotency key for deduplication.
                           If a non-terminal job with this key exists, returns it.
            
        Returns:
            JobItem with PENDING status (or existing non-terminal job if idempotent).
            
        Raises:
            ValueError: If project_id is set but system_fifo_queue doesn't exist.
        """
        # Idempotency check: if idempotency_key provided, check for existing job
        if idempotency_key:
            existing = await asyncio.to_thread(
                self._repository.find_by_idempotency_key, idempotency_key
            )
            if existing is not None:
                # Check TTL - jobs older than TTL are treated as new
                try:
                    created_time = datetime.fromisoformat(existing.created_at)
                    ttl_cutoff = datetime.utcnow() - timedelta(hours=self._idempotency_key_ttl_hours)
                    if created_time < ttl_cutoff:
                        # Job is older than TTL, treat as new
                        logger.info(
                            f"Idempotency key '{idempotency_key}' matched job {existing.job_id} "
                            f"but exceeded TTL ({self._idempotency_key_ttl_hours}h), creating new"
                        )
                        existing = None  # Reset so new job is created below
                except (ValueError, TypeError):
                    # If timestamp parsing fails, treat as existing
                    pass
                
                if existing is not None:
                    terminal_statuses = {JobStatus.COMPLETED.value, JobStatus.CANCELLED.value, JobStatus.DEAD_LETTER.value}
                    if existing.status not in terminal_statuses:
                        # Return existing non-terminal job (idempotent behavior)
                        logger.debug(
                            f"Idempotency key '{idempotency_key}' matched existing job {existing.job_id} "
                            f"(status={existing.status})"
                        )
                        return existing
                    # Terminal job with same key: allow creating new
                    logger.info(
                        f"Idempotency key '{idempotency_key}' matched terminal job {existing.job_id}, "
                        "creating new job"
                    )
        
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
                # C3: System queue doesn't exist - raise error, don't silently continue
                raise ValueError(
                    f"No system FIFO queue found for project {project_id}. "
                    f"Ensure system queues are provisioned."
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
                # C4: Queue belongs to different project - reject the request
                raise ValueError(
                    f"Queue {queue_id} does not belong to project {project_id}"
                )
        
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
            idempotency_key=idempotency_key,
        )
        
        # Notify dispatch bus of new job (for event-driven processing)
        if self._dispatch_bus is not None:
            self._dispatch_bus.notify_new_job(project_id)
        
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
        """Cancel a job. Works for both PENDING and PROCESSING states.
        
        For PROCESSING jobs with an alive instance, this cascades termination
        to the instance (cancelling active requests, terminating children,
        releasing locks) before marking the job as CANCELLED.
        
        Args:
            job_id: Job identifier.
            
        Returns:
            True if cancelled successfully, False if job not found or
            not in a cancellable state.
        """
        job = await asyncio.to_thread(self._repository.get, job_id)
        if job is None:
            return False
        
        # Pre-validate with state machine (for better error messages)
        if not job_state_machine.can_transition(job.status, JobStatus.CANCELLED.value):
            return False
        
        # Handle based on current status
        if job.status == JobStatus.PENDING.value:
            # PENDING: simple transition
            try:
                await asyncio.to_thread(
                    self._repository.atomic_transition,
                    job_id=job.job_id,
                    from_status=JobStatus.PENDING.value,
                    to_status=JobStatus.CANCELLED.value,
                )
                return True
            except InvalidTransitionError:
                return False
        
        elif job.status == JobStatus.PROCESSING.value:
            instance_id = job.instance_id
            
            # Release any locks held by this job first
            if job.queue_id and job.project_id:
                await self._lock_manager.release_queue_lock(
                    job.project_id, job.queue_id, job_id
                )
            elif job.project_id:
                await self._lock_manager.release(job.project_id, job_id)
            
            # Check if instance is still alive
            instance_alive = (
                instance_id is not None
                and self._instance_manager is not None
                and self._is_instance_alive(instance_id)
            )
            
            if instance_alive:
                # Terminate the instance (cascades to children, cancels requests,
                # releases locks, marks job FAILED)
                await self._instance_manager.terminate_instance(instance_id)
                
                # terminate_instance() marks job as FAILED.
                # For cancellation, we want CANCELLED status.
                # Attempt FAILED → CANCELLED transition.
                try:
                    await asyncio.to_thread(
                        self._repository.atomic_transition,
                        job_id=job.job_id,
                        from_status=JobStatus.FAILED.value,
                        to_status=JobStatus.CANCELLED.value,
                    )
                except InvalidTransitionError:
                    # Job may have already transitioned (e.g., was already FAILED
                    # and then completed the transition) — that's fine
                    logger.warning(
                        f"Could not transition job {job.job_id} from FAILED to CANCELLED; "
                        "may already be terminal"
                    )
            else:
                # Instance already dead or never created — transition directly
                try:
                    await asyncio.to_thread(
                        self._repository.atomic_transition,
                        job_id=job.job_id,
                        from_status=JobStatus.PROCESSING.value,
                        to_status=JobStatus.CANCELLED.value,
                    )
                except InvalidTransitionError:
                    return False
            
            return True
        
        # Terminal or non-cancellable states
        return False
    
    def _is_instance_alive(self, instance_id: str) -> bool:
        """Check if an instance exists and is not in a terminal state.
        
        Args:
            instance_id: The instance ID to check.
            
        Returns:
            True if instance is alive (exists with non-terminal status).
        """
        if not self._instance_manager or not hasattr(self._instance_manager, '_instance_repository'):
            return False
        
        meta = self._instance_manager._instance_repository.get(instance_id)
        if meta is None:
            return False
        
        terminal_statuses = {"completed", "error", "terminated", "failed"}
        return meta.status not in terminal_statuses
    
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
        # W13: Carry over the original queue_id to preserve queue routing
        new_job = await self.enqueue(
            agent_id=job.agent_id,
            message=job.message,
            source=job.source,
            project_id=job.project_id,
            queue_id=job.queue_id,  # Carry over the original queue
            priority=job.priority,
            metadata=job.job_metadata,
        )
        
        return new_job
    
    async def list_jobs(
        self,
        status: Optional[JobStatus] = None,
        project_id: Optional[str] = None,
        queue_id: Optional[str] = None,
        limit: int = 50,
    ) -> list[JobItem]:
        """List jobs with optional filters.
        
        Args:
            status: Optional status filter.
            project_id: Optional project ID filter.
            queue_id: Optional queue ID filter.
            limit: Maximum number of jobs to return.
            
        Returns:
            List of JobItem objects.
        """
        status_value = status.value if status else None
        jobs, _ = await asyncio.to_thread(
            self._repository.list,
            status=status_value,
            project_id=project_id,
            queue_id=queue_id,
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
        
        C1 Fix: Attempts to start the job atomically FIRST, then acquires
        the lock. This prevents phantom locks where a worker acquires a
        concurrency slot but fails to start the job, wasting capacity.
        
        The flow is:
        1. Get job → check PENDING
        2. Call start_job_atomic() FIRST (only one worker can succeed)
        3. If start succeeds → THEN acquire lock
        4. If lock fails → roll back job status to PENDING
        
        Args:
            job_id: The job ID to start.
            
        Returns:
            Updated JobItem if started successfully, None if
            job not found, cancelled, or start/lock acquisition failed.
        """
        job = await asyncio.to_thread(self._repository.get, job_id)
        if job is None:
            return None
        
        # Check if job is still pending (could have been cancelled)
        if job.status != JobStatus.PENDING.value:
            return None
        
        # Generate new instance ID for this job
        instance_id = str(uuid.uuid4())
        
        # C1: Try to start job atomically FIRST
        # This is the source of truth - only one worker can transition PENDING→PROCESSING
        try:
            started_job = await asyncio.to_thread(
                self._repository.start_job_atomic, job_id, instance_id
            )
        except ValueError:
            # Job state changed (already started/cancelled) - not an error
            return None
        
        # Start succeeded - now acquire the lock
        # If lock fails, roll back the job status
        if job.queue_id and job.project_id:
            concurrency_limit = await self._get_concurrency_limit(job.queue_id)
            
            acquired = await self._lock_manager.acquire_queue_lock(
                project_id=job.project_id,
                queue_id=job.queue_id,
                job_id=job_id,
                instance_id=instance_id,
                concurrency_limit=concurrency_limit,
            )
            
            if not acquired:
                # Lock acquisition failed - roll back job status
                logger.warning(
                    f"Lock acquisition failed for job {job_id}, rolling back to PENDING"
                )
                await asyncio.to_thread(
                    self._repository.update,
                    job_id,
                    status=JobStatus.PENDING.value,
                    instance_id=None,
                )
                return None
            
            return started_job
        
        # If job has project_id but no queue_id, use backward-compatible project-based locking
        if job.project_id:
            acquired = await self._lock_manager.acquire(
                project_id=job.project_id,
                job_id=job_id,
                instance_id=instance_id,
            )
            
            if not acquired:
                # Lock acquisition failed - roll back job status
                logger.warning(
                    f"Project-level lock acquisition failed for job {job_id}, rolling back"
                )
                await asyncio.to_thread(
                    self._repository.update,
                    job_id,
                    status=JobStatus.PENDING.value,
                    instance_id=None,
                )
                return None
            
            return started_job
        
        # No project_id - started successfully without locking
        return started_job
    
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
                failed_job = await asyncio.to_thread(
                    self._repository.fail_job, job_id, error_message=error or "Unknown error"
                )
                
                # Try auto-retry if retry engine is configured
                if failed_job is not None and self._retry_engine is not None:
                    try:
                        retried = await asyncio.to_thread(self._retry_engine.maybe_retry, job_id)
                        if retried is not None:
                            return retried
                    except Exception as e:
                        logger.error(f"Auto-retry failed for job {job_id}: {e}")
                
                return failed_job
        except (ValueError, InvalidTransitionError):
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
        
        W6 Fix: Uses asyncio.run_coroutine_threadsafe() to properly release
        per-queue locks from synchronous context by scheduling the async
        release on the stored event loop.
        
        Args:
            job_id: The job ID to complete.
            success: True to mark as completed, False to mark as failed.
            error: Error message if success=False.
            result_summary: Optional summary of the job result (for success=True).
            
        Returns:
            Updated JobItem if completed successfully, None if
            job not found or not in a processable state.
        """
        job = self._repository.get(job_id)
        if job is None:
            return None
        
        # W6: Properly release locks from sync context using event loop
        if job.project_id and job.queue_id:
            # Use asyncio.run_coroutine_threadsafe to release queue lock
            if self._loop and self._loop.is_running():
                future = asyncio.run_coroutine_threadsafe(
                    self._lock_manager.release_queue_lock(
                        job.project_id, job.queue_id, job_id
                    ),
                    self._loop,
                )
                try:
                    future.result(timeout=5)  # Wait up to 5s for lock release
                except Exception as e:
                    logger.error(f"Failed to release queue lock for job {job_id}: {e}")
            else:
                logger.warning(
                    f"Cannot release queue lock for job {job_id} - no event loop available"
                )
        elif job.project_id:
            # Legacy project-level lock (backward compatibility)
            self._lock_manager.release_sync(job.project_id, job_id)
        
        # Mark job based on success/failure
        try:
            if success:
                return self._repository.complete_job(job_id, result_summary=result_summary)
            else:
                failed_job = self._repository.fail_job(job_id, error_message=error or "Unknown error")
                
                # Try auto-retry if retry engine is configured
                if failed_job is not None and self._retry_engine is not None:
                    try:
                        retried = self._retry_engine.maybe_retry(job_id)
                        if retried is not None:
                            return retried
                    except Exception as e:
                        logger.error(f"Auto-retry failed for job {job_id}: {e}")
                
                return failed_job
        except (ValueError, InvalidTransitionError):
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
