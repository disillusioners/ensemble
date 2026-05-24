"""Job Queue Service - Manages job queuing with per-queue locking.

This service provides the main interface for job queue operations,
coordinating between the database repository and the lock manager.
"""

from __future__ import annotations

import asyncio
import enum
import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from daemon.manager import InstanceManager
    from daemon.services.dispatch_event_bus import DispatchEventBus

from daemon.repositories.job_queue import JobRepository, JobQueueRepository, JobItem, JobStatus
from daemon.repositories.job_queue.watcher_models import ALL_TERMINAL_STATES
from daemon.services.job_lock_manager import JobLockManager
from daemon.services.job_state_machine import job_state_machine, InvalidTransitionError
from daemon.services.project_normalizer import normalize_project_id
from daemon.registry import get_registry

logger = logging.getLogger(__name__)


class DemandState(enum.Enum):
    """Job demand state for completion.
    
    Used by complete_job/complete_job_sync to specify the terminal state.
    CANCELLED does not trigger retry (unlike FAILED).
    """
    COMPLETED = "completed"   # Successful completion, no retry
    FAILED = "failed"        # Failed with error, may trigger retry
    CANCELLED = "cancelled"  # Cancelled, no retry


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
        instance_manager: "InstanceManager" | None = None,
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
        self._loop: asyncio.AbstractEventLoop | None = None
        self._dispatch_bus: "DispatchEventBus" | None = None  # Dispatch event bus for job notifications
        self._idempotency_key_ttl_hours: int = 24  # Default TTL for idempotency key deduplication
        self._project_repo: Any | None = None  # Project repository for pause state checks
        self._watcher_repo: Any | None = None  # Repository for job watchers
        self._message_job_handler: Any | None = None  # MessageJobHandler for MESSAGE jobs
    
    def set_retry_engine(self, retry_engine) -> None:
        """Set the retry engine for auto-retry functionality.
        
        Args:
            retry_engine: The JobRetryEngine instance to use for auto-retries.
        """
        self._retry_engine = retry_engine
    
    def set_project_repo(self, project_repo: Any) -> None:
        """Set the project repository for pause state checks.
        
        Args:
            project_repo: The SQLModelProjectRepository instance.
        """
        self._project_repo = project_repo
    
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
    
    def set_watcher_repo(self, watcher_repo: Any) -> None:
        """Set the watcher repository for job event notifications.
        
        Args:
            watcher_repo: The JobWatcherRepository instance.
        """
        self._watcher_repo = watcher_repo
    
    async def notify_watchers(self, job_id: str, status: str, error: str | None = None) -> int:
        """Notify ALL watchers for a job. Called from EVERY terminal path.
        
        Returns number of watchers notified.
        Safe to call even if no watchers exist (returns 0).
        If watching instance is not running, message queues in DB for later delivery.
        """
        if self._watcher_repo is None or self._instance_manager is None:
            return 0
        
        try:
            watchers = self._watcher_repo.get_watchers_for_job(job_id)
            if not watchers:
                return 0
            
            # Get job for notification details
            job = await asyncio.to_thread(self._repository.get, job_id)
            if job is None:
                return 0
            
            notified = 0
            for watcher in watchers:
                if status not in watcher.watch_events:
                    continue
                
                notification = (
                    f"[JOB_EVENT] Job {job_id[:8]}... reached status '{status}'.\n"
                    f"Agent: {job.agent_id}\n"
                    f"Result: {job.result_summary or 'N/A'}\n"
                    f"Error: {error or 'None'}\n"
                    f"\n"
                    f"```json\n"
                    f"{json.dumps({
                        'job_id': job_id,
                        'status': status,
                        'agent_id': job.agent_id,
                        'result': job.result_summary or '',
                        'error': error,
                        'timestamp': datetime.utcnow().isoformat()
                    }, ensure_ascii=False)}\n"
                    f"```"
                )
                
                await self._instance_manager.enqueue_message(
                    instance_id=watcher.instance_id,
                    message=notification,
                    source=f"internal_agent:job_event:{job_id}:{status}",
                )
                notified += 1
            
            # Cleanup: terminal states are final, no need to keep watches
            self._watcher_repo.remove_all_watches_for_job(job_id)
            return notified
            
        except Exception as e:
            logger.warning(f"Failed to notify watchers for job {job_id[:8]}...: {e}")
            return 0
    
    async def reconcile_terminal_watches(self) -> int:
        """Scan for watches where job is already terminal. Notify and cleanup."""
        if self._watcher_repo is None:
            return 0
        
        terminal_states = set(ALL_TERMINAL_STATES)
        
        all_watches = self._watcher_repo.get_all_active_watches()
        reconciled = 0
        
        for watch in all_watches:
            job = await asyncio.to_thread(self._repository.get, watch.job_id)
            if job and job.status in terminal_states:
                await self.notify_watchers(watch.job_id, job.status, job.error_message)
                reconciled += 1
        
        return reconciled
    
    # ========== Public API ==========
    
    def find_active_jobs_by_instance(self, instance_id: str, job_type: str | None = None) -> list[JobItem]:
        return self._repository.find_jobs_by_instance(instance_id, job_type)
    
    async def enqueue(
        self,
        agent_id: str,
        message: str,
        source: str = "api",
        project_id: str | None = None,
        priority: int = 5,
        metadata: dict[str, Any] | None = None,
        queue_id: str | None = None,
        idempotency_key: str | None = None,
        job_type: str = "task",
        instance_id: str | None = None,
    ) -> JobItem:
        """Submit a job for processing.
        
        Jobs are always created as PENDING. The JobProcessor picks up pending
        jobs, transitions them to PROCESSING, spawns instances, and enqueues
        messages for processing.
        
        If project_id is set but queue_id is None, the job is assigned to the
        project's "system_fifo_queue" (for TASK jobs) or "system_parallel_queue"
        (for MESSAGE jobs) automatically.

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
                     is set, defaults to the project's system queue.
            idempotency_key: Optional idempotency key for deduplication.
                           If a non-terminal job with this key exists, returns it.
            job_type: Job type ("task" or "message", default "task").
            instance_id: Optional pre-set instance ID (for MESSAGE jobs).

        Returns:
            JobItem with PENDING status (or existing non-terminal job if idempotent).

        Raises:
            ValueError: If project_id is set but system queue doesn't exist.
        """
        # Canonical normalization: ensures ALL callers get system_default_project for None/empty
        project_id = normalize_project_id(project_id)
        if project_id is None:
            raise ValueError("project_id must be normalized before enqueue. This indicates a normalization gap.")

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
        if queue_id is None:
            # project_id is always valid after normalize_project_id()
            if job_type == "message":
                # MESSAGE jobs → system_parallel_queue (parallel execution)
                queue = await asyncio.to_thread(
                    self._queue_repo.get_by_name, project_id, "system_parallel_queue"
                )
                queue_kind = "parallel"
            else:
                # TASK jobs → system_fifo_queue (serial execution, existing behavior)
                queue = await asyncio.to_thread(
                    self._queue_repo.get_by_name, project_id, "system_fifo_queue"
                )
                queue_kind = "fifo"
            if queue is not None:
                resolved_queue_id = queue.queue_id
            else:
                raise ValueError(
                    f"No system {queue_kind} queue found for project {project_id}. "
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
            job_type=job_type,
            instance_id=instance_id,
        )
        
        # Notify dispatch bus of new job (for event-driven processing)
        if self._dispatch_bus is not None:
            self._dispatch_bus.notify_new_job(project_id)
        
        return job
    
    async def get_job(self, job_id: str) -> JobItem | None:
        """Get job by ID.
        
        Args:
            job_id: Unique job identifier.
            
        Returns:
            JobItem if found, None otherwise.
        """
        return await asyncio.to_thread(self._repository.get, job_id)
    
    async def get_job_by_instance(self, instance_id: str) -> JobItem | None:
        """Get job by instance ID.
        
        Args:
            instance_id: Instance identifier.
            
        Returns:
            JobItem if found, None otherwise.
        """
        return await asyncio.to_thread(self._repository.get_by_instance, instance_id)
    
    def get_job_by_instance_sync(self, instance_id: str) -> JobItem | None:
        """Get job by instance ID (synchronous version).
        
        For use from synchronous callers like terminate_instance().
        
        Args:
            instance_id: Instance identifier.
            
        Returns:
            JobItem if found, None otherwise.
        """
        return self._repository.get_by_instance(instance_id)
    
    async def update_job(self, job_id: str, **updates) -> JobItem | None:
        """Update job fields.
        
        Args:
            job_id: Unique job identifier.
            **updates: Fields to update (e.g., status, result_summary).
            
        Returns:
            Updated JobItem if found, None otherwise.
        """
        return await asyncio.to_thread(self._repository.update, job_id, **updates)
    
    async def cancel_job(self, job_id: str) -> bool:
        """Cancel a job. Works for PENDING, PROCESSING, and FAILED states.
        
        For PROCESSING jobs with an alive instance, this cascades termination
        to the instance (cancelling active requests, terminating children,
        releasing locks) before marking the job as CANCELLED.
        
        For FAILED jobs, this stops any pending retries.
        
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
                # Notify watchers after successful transition
                await self.notify_watchers(job.job_id, "cancelled")
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
                # releases locks, marks job as CANCELLED via DemandState.CANCELLED)
                await self._instance_manager.terminate_instance(instance_id)
                # Job is now CANCELLED by instance_lifecycle, notify watchers
                await self.notify_watchers(job.job_id, "cancelled")
                return True
            else:
                # Instance already dead or never created — transition directly to CANCELLED
                try:
                    await asyncio.to_thread(
                        self._repository.atomic_transition,
                        job_id=job.job_id,
                        from_status=JobStatus.PROCESSING.value,
                        to_status=JobStatus.CANCELLED.value,
                    )
                except InvalidTransitionError:
                    return False
                await self.notify_watchers(job.job_id, "cancelled")
                return True
        
        elif job.status == JobStatus.FAILED.value:
            # FAILED: transition to CANCELLED to stop retries
            try:
                await asyncio.to_thread(
                    self._repository.atomic_transition,
                    job_id=job.job_id,
                    from_status=JobStatus.FAILED.value,
                    to_status=JobStatus.CANCELLED.value,
                )
                # Notify watchers after successful transition
                await self.notify_watchers(job.job_id, "cancelled")
                return True
            except InvalidTransitionError:
                return False
        
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
    
    async def retry_job(self, job_id: str) -> JobItem | None:
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
    
    async def soft_delete_job(self, job_id: str) -> JobItem | None:
        """Soft-delete a job by setting deleted_at timestamp.
        
        Args:
            job_id: Job identifier.
            
        Returns:
            Updated JobItem if successful, None if job not found.
        """
        return await asyncio.to_thread(self._repository.soft_delete, job_id)
    
    async def restore_job(self, job_id: str) -> JobItem | None:
        """Restore a soft-deleted job.
        
        Args:
            job_id: Job identifier.
            
        Returns:
            Updated JobItem if successful, None if job not found.
        """
        return await asyncio.to_thread(self._repository.restore, job_id)
    
    async def list_jobs(
        self,
        statuses: list[str] | None = None,
        project_id: str | None = None,
        queue_id: str | None = None,
        offset: int = 0,
        limit: int = 50,
        include_deleted: bool = False,
    ) -> list[JobItem]:
        """List jobs with optional filters.
        
        Args:
            statuses: Optional list of status filters.
            project_id: Optional project ID filter.
            queue_id: Optional queue ID filter.
            offset: Number of jobs to skip.
            limit: Maximum number of jobs to return.
            include_deleted: Whether to include soft-deleted jobs.
            
        Returns:
            List of JobItem objects.
        """
        jobs, _ = await asyncio.to_thread(
            self._repository.list,
            statuses=statuses,
            project_id=project_id,
            queue_id=queue_id,
            offset=offset,
            limit=limit,
            include_deleted=include_deleted,
        )
        return jobs
    
    # ========== Helper Methods ==========
    
    async def _release_job_lock(
        self,
        *,
        project_id: str | None,
        queue_id: str | None,
        job_id: str,
        instance_id: str | None = None,
        release_by_instance: bool = True,
    ) -> None:
        """Safely release a job's queue lock with backward-compatible fallback.
        
        Args:
            project_id: The project owning the lock.
            queue_id: The queue ID (if any).
            job_id: The job ID to release.
            instance_id: The instance ID (for release_by_instance mode).
            release_by_instance: If True, uses release_by_instance (from _complete_job);
                                 if False, uses release (from complete_job).
        """
        if queue_id and project_id:
            await self._lock_manager.release_queue_lock(
                project_id, queue_id, job_id
            )
        elif project_id:
            if release_by_instance:
                if instance_id:
                    await self._lock_manager.release_by_instance(instance_id)
                # else: do nothing (matches original Pattern A)
            else:
                await self._lock_manager.release(project_id, job_id)
    
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
    
    async def _complete_job(self, job: JobItem, result_summary: str | None) -> None:
        """Mark a job as completed and release its lock.
        
        Args:
            job: The processing job to complete.
            result_summary: Optional summary of the job result.
        """
        # Release the lock first
        await self._release_job_lock(
            project_id=job.project_id,
            queue_id=job.queue_id,
            job_id=job.job_id,
            instance_id=job.instance_id,
            release_by_instance=True,
        )

        # Mark job as completed
        await asyncio.to_thread(self._repository.complete_job, job.job_id, result_summary)
    
    async def _fail_job(self, job: JobItem, error_message: str) -> None:
        """Mark a job as failed and release its lock.
        
        Args:
            job: The processing job that failed.
            error_message: Error message describing the failure.
        """
        # Release the lock first
        await self._release_job_lock(
            project_id=job.project_id,
            queue_id=job.queue_id,
            job_id=job.job_id,
            instance_id=job.instance_id,
            release_by_instance=True,
        )

        # Mark job as failed
        await asyncio.to_thread(self._repository.fail_job, job.job_id, error_message)
    
    async def _get_next_job(
        self,
        project_id: str | None = None,
        queue_id: str | None = None,
    ) -> JobItem | None:
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
        job_id: str | None,
        project_id: str,
        queue_id: str | None = None,
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
    
    async def get_next_pending_job(self) -> JobItem | None:
        """Get the next pending job (highest priority, oldest first).
        
        Returns the first pending job from all projects, ordered by
        priority (descending) then created_at (ascending).
        
        Returns:
            Next JobItem to process, or None if no pending jobs.
        """
        pending = await asyncio.to_thread(self._repository.list_all_pending)
        return pending[0] if pending else None
    
    async def start_job(self, job_id: str) -> JobItem | None:
        """Mark job as processing and acquire lock.
        
        C1 Fix: Acquires the lock FIRST, then transitions the job atomically.
        This prevents the race condition where multiple workers transition the
        same job to PROCESSING but only one can acquire the lock, causing
        repeated PENDING→PROCESSING→rollback cycles.
        
        The flow is:
        1. Get job → check PENDING
        2. Acquire queue/project lock FIRST (if at capacity, don't transition)
        3. If lock acquired → THEN call start_job_atomic()
        4. If start fails → release the lock we acquired
        
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
        
        # CENTRALIZED PAUSE CHECK - protects ALL callers
        if self._project_repo is not None and job.project_id:
            project = await asyncio.to_thread(self._project_repo.get, job.project_id)
            if project and project.job_queue_paused:
                logger.debug(
                    f"start_job: project {job.project_id[:8]}... is paused, skipping"
                )
                return None
        
        # Warn if project_repo is not set (can't check pause state)
        if self._project_repo is None:
            logger.warning("start_job: project_repo not set, cannot check pause state")

        # Generate instance_id: MESSAGE jobs use pre-set instance_id, TASK jobs get new UUID
        if job.job_type == "message" and job.instance_id:
            instance_id = job.instance_id
        else:
            instance_id = str(uuid.uuid4())

        # Acquire lock FIRST - if we can't get it, don't transition the job
        lock_acquired = False
        if job.queue_id and job.project_id:
            concurrency_limit = await self._get_concurrency_limit(job.queue_id)
            
            lock_acquired = await self._lock_manager.acquire_queue_lock(
                project_id=job.project_id,
                queue_id=job.queue_id,
                job_id=job_id,
                instance_id=instance_id,
                concurrency_limit=concurrency_limit,
            )
            
            if not lock_acquired:
                # Can't acquire lock - another job is using the concurrency slot
                # Don't transition the job to avoid the rollback loop
                return None
        elif job.project_id:
            lock_acquired = await self._lock_manager.acquire(
                project_id=job.project_id,
                job_id=job_id,
                instance_id=instance_id,
            )
            
            if not lock_acquired:
                return None
        
        # Lock acquired (or no locking needed) - now try to start job atomically
        try:
            started_job = await asyncio.to_thread(
                self._repository.start_job_atomic, job_id, instance_id
            )
            return started_job
        except ValueError:
            # Job state changed (already started/cancelled) - release the lock we acquired
            if lock_acquired:
                if job.queue_id and job.project_id:
                    await self._lock_manager.release_queue_lock(
                        project_id=job.project_id,
                        queue_id=job.queue_id,
                        job_id=job_id,
                    )
                elif job.project_id:
                    await self._lock_manager.release(
                        project_id=job.project_id,
                        job_id=job_id,
                    )
            return None
    
    async def complete_job(
        self,
        job_id: str,
        demand_state: DemandState = DemandState.COMPLETED,
        error: str | None = None,
        result_summary: str | None = None,
    ) -> JobItem | None:
        """Mark job as completed/failed/cancelled and release lock.
        
        Args:
            job_id: The job ID to complete.
            demand_state: Terminal state (COMPLETED, FAILED, or CANCELLED).
            error: Error message if demand_state is FAILED or CANCELLED.
            result_summary: Optional summary text for completed jobs.
            
        Returns:
            Updated JobItem if completed successfully, None if
            job not found or not in a processable state.
        """
        job = await asyncio.to_thread(self._repository.get, job_id)
        if job is None:
            return None

        # Mark job based on demand_state FIRST (before lock release)
        result = None
        try:
            if demand_state == DemandState.COMPLETED:
                summary = result_summary or "Job completed successfully"
                result = await asyncio.to_thread(
                    self._repository.complete_job, job_id, result_summary=summary
                )
                # Notify watchers after successful transition
                await self.notify_watchers(job_id, "completed")
            elif demand_state == DemandState.FAILED:
                failed_job = await asyncio.to_thread(
                    self._repository.fail_job, job_id, error_message=error or "Unknown error"
                )

                # Try auto-retry if retry engine is configured
                if failed_job is not None and self._retry_engine is not None:
                    try:
                        retried = await asyncio.to_thread(self._retry_engine.maybe_retry, job_id)
                        if retried is not None:
                            result = retried
                    except Exception as e:
                        logger.error(f"Auto-retry failed for job {job_id}: {e}")

                # Notify watchers if job is still FAILED (retry didn't succeed)
                if failed_job is not None and result is None:
                    await self.notify_watchers(job_id, "failed", error)
                result = result if result is not None else failed_job
            elif demand_state == DemandState.CANCELLED:
                # CANCELLED state does not trigger retry
                result = await asyncio.to_thread(
                    self._repository.terminate_job, job_id, error_message=error or "Cancelled"
                )
                # Notify watchers after successful transition
                await self.notify_watchers(job_id, "cancelled", error)
        except (ValueError, InvalidTransitionError) as e:
            # Job state already changed — still need to release lock below
            logger.debug("Job %s already transitioned, skip: %s", job_id[:8], e)
        finally:
            # Release the per-queue lock AFTER state is committed
            try:
                await self._release_job_lock(
                    project_id=job.project_id,
                    queue_id=job.queue_id,
                    job_id=job_id,
                    release_by_instance=False,
                )
            except Exception as e:
                logger.warning("Failed to release lock for job %s: %s", job_id[:8], e)

        return result
    
    def complete_job_sync(
        self,
        job_id: str,
        demand_state: DemandState,
        error: str | None = None,
        result_summary: str | None = None,
    ) -> JobItem | None:
        """Mark job as completed/failed/cancelled and release lock (synchronous version).
        
        W6 Fix: Uses asyncio.run_coroutine_threadsafe() to properly release
        per-queue locks from synchronous context by scheduling the async
        release on the stored event loop.
        
        Args:
            job_id: The job ID to complete.
            demand_state: Terminal state (COMPLETED, FAILED, or CANCELLED).
            error: Error message if demand_state is FAILED or CANCELLED.
            result_summary: Optional summary of the job result (for COMPLETED).
            
        Returns:
            Updated JobItem if completed successfully, None if
            job not found or not in a processable state.
        """
        job = self._repository.get(job_id)
        if job is None:
            return None

        # Mark job based on demand_state FIRST (before lock release)
        result = None
        try:
            if demand_state == DemandState.COMPLETED:
                result = self._repository.complete_job(job_id, result_summary=result_summary)
                # Notify watchers after successful transition
                if self._loop and self._loop.is_running():
                    asyncio.run_coroutine_threadsafe(
                        self.notify_watchers(job_id, "completed"),
                        self._loop,
                    )
            elif demand_state == DemandState.FAILED:
                failed_job = self._repository.fail_job(job_id, error_message=error or "Unknown error")

                # Try auto-retry if retry engine is configured
                if failed_job is not None and self._retry_engine is not None:
                    try:
                        retried = self._retry_engine.maybe_retry(job_id)
                        if retried is not None:
                            result = retried
                    except Exception as e:
                        logger.error(f"Auto-retry failed for job {job_id}: {e}")

                # Notify watchers if job is still FAILED (retry didn't succeed)
                if failed_job is not None and result is None:
                    if self._loop and self._loop.is_running():
                        asyncio.run_coroutine_threadsafe(
                            self.notify_watchers(job_id, "failed", error),
                            self._loop,
                        )
                result = result if result is not None else failed_job
            elif demand_state == DemandState.CANCELLED:
                # CANCELLED state does not trigger retry
                result = self._repository.terminate_job(job_id, error_message=error or "Cancelled")
                # Notify watchers after successful transition
                if self._loop and self._loop.is_running():
                    asyncio.run_coroutine_threadsafe(
                        self.notify_watchers(job_id, "cancelled", error),
                        self._loop,
                    )
        except (ValueError, InvalidTransitionError) as e:
            # Job state already changed — still need to release lock below
            logger.debug("Job %s already transitioned, skip: %s", job_id[:8], e)
        finally:
            # Release the per-queue lock AFTER state is committed (W6 fix)
            try:
                if job.project_id and job.queue_id:
                    # Use asyncio.run_coroutine_threadsafe to release queue lock
                    if self._loop and self._loop.is_running():
                        future = asyncio.run_coroutine_threadsafe(
                            self._lock_manager.release_queue_lock(
                                job.project_id, job.queue_id, job_id
                            ),
                            self._loop,
                        )
                        future.result(timeout=5)  # Wait up to 5s for lock release
                elif job.project_id:
                    # Legacy project-level lock (backward compatibility)
                    if self._loop and self._loop.is_running():
                        future = asyncio.run_coroutine_threadsafe(
                            self._lock_manager.release(job.project_id, job_id),
                            self._loop,
                        )
                        future.result(timeout=5)
            except Exception as e:
                logger.warning("Failed to release lock for job %s: %s", job_id[:8], e)

        return result
    
    async def trigger_next_job(
        self,
        project_id: str,
        queue_id: str | None = None,
    ) -> JobItem | None:
        """Trigger the next pending job for a queue or project.
        
        Called after a job completes to process any waiting jobs
        for the same queue or project.
        
        Emits a dispatch event so JobProcessor wakes up to handle spawning
        the instance and sending the message.
        
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
        
        # Pause check is centralized in start_job()
        result = await self.start_job(next_job.job_id)
        
        # Emit dispatch event so JobProcessor wakes up immediately to
        # spawn instance and send the job message
        if result and self._dispatch_bus:
            self._dispatch_bus.notify_new_job(project_id)
        
        return result
    
    def trigger_next_job_sync(
        self,
        project_id: str,
        queue_id: str | None = None,
    ) -> JobItem | None:
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
        
        # PAUSE CHECK: Skip if project is paused (sync call - no wrapper needed)
        if self._project_repo is not None:
            project = self._project_repo.get(project_id)
            if project and project.job_queue_paused:
                logger.debug(
                    f"trigger_next_job_sync: project {project_id[:8]}... is paused, skipping"
                )
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
            # Use asyncio.run_coroutine_threadsafe to acquire async lock
            if self._loop and self._loop.is_running():
                future = asyncio.run_coroutine_threadsafe(
                    self._lock_manager.acquire(
                        project_id=job.project_id,
                        job_id=next_job.job_id,
                        instance_id=instance_id,
                    ),
                    self._loop,
                )
                try:
                    acquired = future.result(timeout=5)
                except Exception as e:
                    logger.error(f"Failed to acquire project lock for job {next_job.job_id}: {e}")
                    return None
            else:
                logger.warning(
                    f"Cannot acquire project lock for job {next_job.job_id} - no event loop available"
                )
                return None
            
            if not acquired:
                return None
            
            try:
                return self._repository.start_job(next_job.job_id, instance_id)
            except ValueError:
                # Release on failure - use async release
                if self._loop and self._loop.is_running():
                    release_future = asyncio.run_coroutine_threadsafe(
                        self._lock_manager.release(job.project_id, next_job.job_id),
                        self._loop,
                    )
                    try:
                        release_future.result(timeout=5)
                    except Exception as e:
                        logger.error(f"Failed to release project lock after start failure: {e}")
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

    async def cancel_message_job(self, job_id: str) -> None:
        """Cancel a MESSAGE-type job. Delegates to MessageJobHandler.

        This is the public API called by instance_lifecycle.terminate_instance()
        and any other external callers.

        Args:
            job_id: The job to cancel.
        """
        if self._message_job_handler is None:
            logger.warning(f"Cannot cancel MESSAGE job {job_id[:8]}... — no handler registered")
            return
        await self._message_job_handler.cancel_message_job(job_id)
