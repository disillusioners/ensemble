"""Job Queue Service - Manages job queuing with per-queue locking.

This service provides the main interface for job queue operations,
coordinating between the database repository and the lock manager.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from daemon.manager import InstanceManager
    from daemon.services.dispatch_event_bus import DispatchEventBus

from daemon.repositories.job_queue import JobRepository, JobQueueRepository, JobItem, JobStatus
from daemon.repositories.job_queue.watcher_models import ALL_TERMINAL_STATES
from daemon.repositories.instance.models import InstanceStatus
from daemon.services.job_lock_manager import JobLockManager
from daemon.services.job_state_machine import job_state_machine, InvalidTransitionError
from daemon.services.project_normalizer import normalize_project_id
from daemon.registry import get_registry

logger = logging.getLogger(__name__)


# Shared terminal instance statuses — used across job_queue_service,
# instance_lifecycle, and job_processor for consistent cleanup behavior.
TERMINAL_STATUSES = frozenset([
    InstanceStatus.TERMINATED.value,
    InstanceStatus.COMPLETED.value,
    InstanceStatus.ERROR.value,
    InstanceStatus.FAILED.value,
])

# TERMINAL_CANCEL_STATUSES contains instance statuses that represent abnormal termination
# (not a normal completion). COMPLETED is NOT here because it's a normal completion.
TERMINAL_CANCEL_STATUSES = frozenset([
    InstanceStatus.TERMINATED.value,
])


# Natural-language aliases → canonical job status.
# Applied in normalize_statuses() so that agents/LLMs that pass
# "running" (meaning "processing") get correct results instead of empty lists.
# ``paused`` is an identity mapping so natural-language queries like
# ``status="paused"`` resolve to the canonical enum value; pause is
# non-terminal (see JobStatus.PAUSED docs at models.py:25-28).
STATUS_ALIASES: dict[str, str] = {
    "running": "processing",
    "active": "processing",
    "in_progress": "processing",
    "queued": "pending",
    "waiting": "pending",
    "done": "completed",
    "success": "completed",
    "finished": "completed",
    "error": "failed",
    "failed": "failed",  # identity, for safety
    "killed": "cancelled",
    "canceled": "cancelled",  # common misspelling
    "dlq": "dead_letter",
    "dead": "dead_letter",
    "paused": "paused",  # identity — see comment above
}


def normalize_statuses(statuses: list[str] | None) -> list[str] | None:
    """Resolve natural-language status aliases to canonical job status values.

    - Case-insensitive (lowercases before lookup)
    - If a status is already a canonical value, keeps it as-is (backward compatible)
    - If a status is not a known alias, passes it through unchanged (let SQL return empty)
    """
    if not statuses:
        return statuses
    out: list[str] = []
    for s in statuses:
        canonical = STATUS_ALIASES.get(s.lower(), s.lower())
        out.append(canonical)
    return out


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
    
    async def notify_watchers(
        self,
        job_id: str,
        status: str,
        error: str | None = None,
        progress: str | None = None,
    ) -> int:
        """Notify ALL watchers for a job. Called from EVERY terminal path.

        Returns number of watchers notified.
        Safe to call even if no watchers exist (returns 0).
        If watching instance is not running, message queues in DB for later delivery.
        """
        if self._watcher_repo is None or self._instance_manager is None:
            return 0

        try:
            # Wrap the sync DB read in asyncio.to_thread so SQLite WAL write
            # contention cannot block the event loop. See the deadlock analysis
            # in the experience docs for the full chain.
            watchers = await asyncio.to_thread(
                self._watcher_repo.get_watchers_for_job, job_id
            )
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

                # Build notification parts
                status_display = status
                if status == "completed":
                    status_display = "completed ✓"
                elif status == "failed":
                    status_display = "failed ✗"
                elif status == "in_progress":
                    status_display = "in progress ⟳"
                elif status == "paused":
                    # Pause is a non-terminal state — display with a
                    # distinguishing icon so watcher notifications don't
                    # look identical to terminal/active statuses.
                    status_display = "paused ⏸"

                notification_parts = [f"[JOB_EVENT] Job {job_id[:8]}... {status_display}"]
                notification_parts.append(f"  Agent: {job.agent_id}")

                if status == "in_progress":
                    if progress:
                        notification_parts.append(f"  Progress:\n{progress}")
                else:
                    if job.result_summary:
                        notification_parts.append(f"  Result:\n{job.result_summary}")
                    if error:
                        notification_parts.append(f"  Error: {error}")

                notification = "\n".join(notification_parts)
                
                await self._instance_manager.enqueue_message(
                    instance_id=watcher.instance_id,
                    message=notification,
                    source=f"internal_agent:job_event:{job_id}:{status}",
                )
                notified += 1
            
            # Cleanup: only remove watches for terminal states.
            # Non-terminal events (e.g., in_progress) must keep the watch alive
            # so the watcher receives the final terminal notification later.
            if status in ALL_TERMINAL_STATES:
                # Wrap the sync DB write in asyncio.to_thread so SQLite WAL
                # contention cannot block the event loop. See the deadlock
                # analysis in the experience docs for the full chain.
                await asyncio.to_thread(
                    self._watcher_repo.remove_all_watches_for_job, job_id
                )
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
        project's "system_fifo_queue" (for TASK jobs) automatically.

        With idempotency_key: if a job with the same key exists and is non-terminal,
        returns the existing job instead of creating a duplicate.

        D13 (Phase 2): ``job_type="message"`` is REJECTED with ``ValueError``.
        Messages no longer create ``JobItem`` rows — they create ``Task`` rows
        in the WorkerPool path (see :meth:`InstanceMessagingService.enqueue_message`).
        This guard is defense-in-depth: the only legitimate caller
        (``enqueue_message`` with ``dispatch_path="jobqueue"``) has been
        removed.

        Args:
            agent_id: Agent ID (e.g., 'developer').
            message: Job message/content.
            source: Source of the job ("api", "telegram", "scheduler", "webhook").
            project_id: Optional project ID for job serialization.
            priority: Job priority (1-10, default 5).
            metadata: Optional metadata dictionary.
            queue_id: Optional queue ID for job routing. If None and project_id
                     is set, defaults to the project's system FIFO queue.
            idempotency_key: Optional idempotency key for deduplication.
                           If a non-terminal job with this key exists, returns it.
            job_type: Job type — must be ``"task"`` (D13 rejects ``"message"``).
            instance_id: Optional pre-set instance ID.

        Returns:
            JobItem with PENDING status (or existing non-terminal job if idempotent).

        Raises:
            ValueError: If ``job_type == "message"`` (D13 — use
                :meth:`InstanceMessagingService.enqueue_message` instead).
        """
        # D13 defense-in-depth: messages must use enqueue_message (WorkerPool
        # Task row), not this JobItem-creating path. The only legitimate
        # caller (enqueue_message with dispatch_path="jobqueue") has been
        # removed. Raising here ensures any leftover caller fails loudly.
        if job_type == "message":
            raise ValueError(
                "enqueue_job no longer accepts job_type='message' — "
                "use enqueue_message instead (D13 architecture migration)"
            )

        # Canonical normalization: ensures ALL callers get system_default_project for None/empty
        project_id = normalize_project_id(project_id)
        if project_id is None:
            raise ValueError("project_id must be normalized before enqueue. This indicates a normalization gap.")

        # M6 fix: use atomic ``INSERT ... ON CONFLICT DO NOTHING`` instead
        # of the previous read-then-insert pattern. Two concurrent
        # ``enqueue`` calls with the same key would BOTH pass the
        # ``find_by_idempotency_key`` check, and the loser's INSERT
        # would raise an unhandled ``IntegrityError`` (surfacing as a
        # 500 to the caller). The atomic insert claims the partial
        # unique index ``idx_job_idempotency`` in one round trip.
        if idempotency_key:
            # Derive agent_dir from agent_id using registry before the
            # atomic insert — we still need it for both the insert path
            # and the registry validation below.
            # Resolve alias (backward compat for renamed agents like 'coder'→'developer')
            # since agent_id may come from a DB row that still has the old value.
            registry = get_registry()
            agent_meta = registry.get_resolved(agent_id)
            resolved_agent_id = registry.resolve_pure_id(agent_id) or agent_id
            if agent_meta is None:
                raise ValueError(f"Agent not found: {agent_id}")
            agent_dir = str(agent_meta.path)
            agent_id = resolved_agent_id

            # Resolve queue_id for projects (needed for the INSERT row)
            # D13: only TASK jobs reach this point (message jobs are
            # rejected by the guard above). Always route to the FIFO
            # system queue.
            resolved_queue_id = queue_id
            if resolved_queue_id is None:
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

            # Atomically claim the key. ``created`` is True iff we
            # inserted a fresh row.
            job, created = await asyncio.to_thread(
                self._repository.create_or_get_by_idempotency_key,
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

            if not created and job is not None:
                # Another writer beat us. Apply the same terminal-vs-TTL
                # policy as the previous read-then-insert code: if the
                # existing job is non-terminal AND within TTL, return it
                # (idempotent); otherwise fall through to a fresh insert.
                try:
                    created_time = datetime.fromisoformat(job.created_at)
                    ttl_cutoff = datetime.now(timezone.utc) - timedelta(
                        hours=self._idempotency_key_ttl_hours
                    )
                    if created_time < ttl_cutoff:
                        logger.info(
                            f"Idempotency key '{idempotency_key}' matched job {job.job_id} "
                            f"but exceeded TTL ({self._idempotency_key_ttl_hours}h), retrying insert"
                        )
                        job = None  # fall through to a fresh insert below
                except (ValueError, TypeError):
                    # If timestamp parsing fails, keep the existing job.
                    pass

                if job is not None:
                    terminal_statuses = {
                        JobStatus.COMPLETED.value,
                        JobStatus.CANCELLED.value,
                        JobStatus.DEAD_LETTER.value,
                    }
                    if job.status not in terminal_statuses:
                        logger.debug(
                            f"Idempotency key '{idempotency_key}' matched existing job {job.job_id} "
                            f"(status={job.status})"
                        )
                        return job
                    logger.info(
                        f"Idempotency key '{idempotency_key}' matched terminal job {job.job_id}, "
                        "creating new job"
                    )

            if created and job is not None:
                # Fresh insert succeeded — notify and return.
                if self._dispatch_bus is not None:
                    self._dispatch_bus.notify_new_job(project_id)
                return job

            # Fallthrough: existing job was terminal / TTL-expired and
            # we need a brand-new insert. Drop into the regular create
            # path below by clearing idempotency_key (otherwise the
            # legacy code below would loop on the same key).
            if job is not None:
                # Terminal existing job — bypass the unique index by
                # using a synthetic suffix so the new row has a unique
                # non-null key. This preserves the previous behavior
                # where a terminal job does NOT block a fresh submit.
                idempotency_key = f"{idempotency_key}#{uuid.uuid4().hex[:8]}"
            else:
                # TTL-expired job — same treatment.
                idempotency_key = f"{idempotency_key}#{uuid.uuid4().hex[:8]}"

        # Non-idempotency path (or terminal-fallback path above).
        # Derive agent_dir from agent_id using registry.
        # Resolve alias (backward compat for renamed agents like 'coder'→'developer')
        # since agent_id may come from a DB row that still has the old value.
        registry = get_registry()
        agent_meta = registry.get_resolved(agent_id)
        resolved_agent_id = registry.resolve_pure_id(agent_id) or agent_id
        if agent_meta is None:
            raise ValueError(f"Agent not found: {agent_id}")
        agent_dir = str(agent_meta.path)
        agent_id = resolved_agent_id

        # Resolve queue_id for projects
        # D13: only TASK jobs reach this point (message jobs are
        # rejected by the guard above). Always route to the FIFO
        # system queue.
        resolved_queue_id = queue_id
        if queue_id is None:
            # project_id is always valid after normalize_project_id()
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
        
        For PROCESSING jobs with a dead/terminal instance and for PENDING /
        FAILED jobs, this delegates to the atomic repository ``cancel_job``,
        which handles all cancellable states in a single UPDATE-WHERE-IN.
        The atomic repository method closes the TOCTOU window where a
        concurrent ``start_job`` would transition PENDING -> PROCESSING
        between this method's read and its dispatch, causing the cancel
        to be silently lost.
        
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
        
        # Pre-validate with state machine for better error messages. This
        # is a best-effort check; the atomic repo.cancel_job is the source
        # of truth and will raise ValueError for non-cancellable states.
        if not job_state_machine.can_transition(job.status, JobStatus.CANCELLED.value):
            return False
        
        # Special case: PROCESSING with an alive instance requires a
        # cascade — ``terminate_instance`` will mark the job CANCELLED
        # itself. Lock release happens first regardless of instance
        # liveness (matches pre-fix semantics).
        if job.status == JobStatus.PROCESSING.value:
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
                # Terminate the instance (cascades to children, cancels
                # requests, releases locks, marks job as CANCELLED via
                # DemandState.CANCELLED).
                await self._instance_manager.terminate_instance(instance_id)
                # Job is now CANCELLED by instance_lifecycle, notify watchers
                await self.notify_watchers(job.job_id, "cancelled")
                return True
            # else: instance already dead/terminal — fall through to atomic
            # repo.cancel_job which will handle PROCESSING -> CANCELLED.
        
        # All other cancellable states (PENDING, PROCESSING-dead, FAILED):
        # delegate to the atomic repository cancel_job which covers all
        # cancellable states in a single UPDATE, eliminating the TOCTOU
        # race against concurrent start_job transitions.
        try:
            await asyncio.to_thread(self._repository.cancel_job, job.job_id)
        except ValueError:
            return False
        await self.notify_watchers(job.job_id, "cancelled")
        return True
    
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
        
        return meta.status not in TERMINAL_STATUSES
    
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
        statuses = normalize_statuses(statuses)
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

            # C11: try/finally ensures lock is released on ALL failure paths
            # (not just ValueError — e.g. OperationalError, CancelledError, deadlocks).
            # The ``except ValueError`` preserves the original "return False"
            # behavior for callers; the finally block still drops the lock.
            started_ok = False
            try:
                await asyncio.to_thread(
                    self._repository.start_job_atomic, job.job_id, instance_id
                )
                started_ok = True
                return True
            except ValueError:
                # Job state changed (already started/cancelled)
                return False
            finally:
                if not started_ok:
                    await self._lock_manager.release_queue_lock(
                        job.project_id, job.queue_id, job.job_id
                    )

        # If job has project_id but no queue_id, use backward-compatible project-based locking
        if job.project_id:
            acquired = await self._lock_manager.acquire(
                project_id=job.project_id,
                job_id=job.job_id,
                instance_id=instance_id,
            )

            if not acquired:
                return False

            # C11: try/finally ensures lock is released on ALL failure paths.
            started_ok = False
            try:
                await asyncio.to_thread(
                    self._repository.start_job_atomic, job.job_id, instance_id
                )
                started_ok = True
                return True
            except ValueError:
                return False
            finally:
                if not started_ok:
                    await self._lock_manager.release(job.project_id, job.job_id)

        # No project_id - start immediately without locking
        # No lock held here; keep the original try/except ValueError semantics
        # so we still return False (not None) on a state mismatch.
        try:
            await asyncio.to_thread(
                self._repository.start_job_atomic, job.job_id, instance_id
            )
            return True
        except ValueError:
            return False
    
    async def _complete_job(self, job: JobItem, result_summary: str | None) -> None:
        """Mark a job as completed and release its lock.

        H9 Fix: Status transition FIRST, lock release in finally.
        The previous order (release → transition) created a race window where
        a failed transition would leave the job in PROCESSING with no lock,
        allowing a second worker to double-claim. By holding the lock until
        the status is committed, the recovery sweep (``recover_stale_locks``)
        remains the only path that can re-claim a stuck PROCESSING job.

        Args:
            job: The processing job to complete.
            result_summary: Optional summary of the job result.
        """
        try:
            # 1. Transition status FIRST (commit before releasing the lock)
            await asyncio.to_thread(
                self._repository.complete_job, job.job_id, result_summary
            )
        finally:
            # 2. Release lock AFTER transition attempt (success OR failure).
            # On failure, the job stays PROCESSING and the recovery sweep will
            # pick it up — which is the correct, race-free path.
            try:
                await self._release_job_lock(
                    project_id=job.project_id,
                    queue_id=job.queue_id,
                    job_id=job.job_id,
                    instance_id=job.instance_id,
                    release_by_instance=True,
                )
            except Exception as e:
                logger.warning(
                    f"Failed to release lock for job {job.job_id[:8]}...: {e}"
                )

    async def _fail_job(self, job: JobItem, error_message: str) -> None:
        """Mark a job as failed and release its lock.

        H9 Fix: Status transition FIRST, lock release in finally.
        Mirrors the ordering in ``_complete_job`` and the public
        ``complete_job`` (status-first, lock-second in finally). Holding the
        lock until the transition is committed prevents a failed transition
        from leaving a job in PROCESSING with no lock — which would let a
        second worker double-claim during the recovery window.

        Args:
            job: The processing job that failed.
            error_message: Error message describing the failure.
        """
        try:
            # 1. Transition status FIRST (commit before releasing the lock)
            await asyncio.to_thread(
                self._repository.fail_job, job.job_id, error_message
            )
        finally:
            # 2. Release lock AFTER transition attempt (success OR failure).
            try:
                await self._release_job_lock(
                    project_id=job.project_id,
                    queue_id=job.queue_id,
                    job_id=job.job_id,
                    instance_id=job.instance_id,
                    release_by_instance=True,
                )
            except Exception as e:
                logger.warning(
                    f"Failed to release lock for job {job.job_id[:8]}...: {e}"
                )
    
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
            return await self._select_next_eligible_job(pending, project_id)
        else:
            pending = await asyncio.to_thread(self._repository.list_all_pending)
            return pending[0] if pending else None

    async def _select_next_eligible_job(
        self,
        pending: list[JobItem],
        project_id: str,
    ) -> JobItem | None:
        """Select the next eligible job from pending list, respecting defer semantics.
        
        Defer jobs are only returned when no non-defer work (active or pending) exists.
        This ensures defer queues don't start processing while non-defer work is pending.
        
        Args:
            pending: List of pending jobs (ordered by priority desc, created_at asc).
            project_id: Project ID for idle check.
            
        Returns:
            Next eligible JobItem, or None if no eligible jobs.
        """
        if not pending:
            return None
        
        # Batch-fetch queue types to avoid N+1 queries
        unique_queue_ids = {job.queue_id for job in pending if job.queue_id}
        queue_type_map: dict[str, bool] = {}  # queue_id -> is_defer
        for qid in unique_queue_ids:
            queue = await asyncio.to_thread(self._queue_repo.get, qid)
            queue_type_map[qid] = queue.queue_type == "defer" if queue else False
        
        # Check once if non-defer work is active (used for defer jobs only)
        non_defer_active = 0
        for job in pending:
            is_defer = queue_type_map.get(job.queue_id, False)
            if is_defer:
                # Defer job found - check if non-defer work is active
                non_defer_active = await asyncio.to_thread(
                    self._repository.count_active_jobs_in_non_defer_queues, project_id
                )
                break
        
        # Iterate through pending jobs and select first eligible
        for job in pending:
            is_defer = queue_type_map.get(job.queue_id, False)
            if not is_defer:
                # Non-defer job - always safe to return
                return job
            else:
                # Defer job - only return if non-defer work is idle
                if non_defer_active == 0:
                    return job
                # Otherwise skip this defer job and continue checking
        return None
    
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
        # [TRACE] Log entry
        logger.debug(f"[TRACE] start_job: called for job_id={job_id[:8]}...")
        
        job = await asyncio.to_thread(self._repository.get, job_id)
        if job is None:
            logger.debug(f"[TRACE] start_job: job {job_id[:8]}... not found")
            return None
        
        # [TRACE] Log job details
        logger.debug(
            f"[TRACE] start_job: job={job_id[:8]}... status={job.status} "
            f"instance={job.instance_id[:8] if job.instance_id else 'N/A'}... job_type={getattr(job, 'job_type', 'task')}"
        )
        
        # Check if job is still pending (could have been cancelled)
        if job.status != JobStatus.PENDING.value:
            logger.debug(f"[TRACE] start_job: job {job_id[:8]}... SKIP — not PENDING (status={job.status})")
            return None
        
        # CENTRALIZED PAUSE CHECK - protects ALL callers
        if self._project_repo is not None and job.project_id:
            project = await asyncio.to_thread(self._project_repo.get, job.project_id)
            if project and project.job_queue_paused:
                logger.info(
                    f"[TRACE] start_job: job {job_id[:8]}... SKIP — project {job.project_id[:8]}... PAUSED"
                )
                return None
        
        # Warn if project_repo is not set (can't check pause state)
        if self._project_repo is None:
            logger.warning("start_job: project_repo not set, cannot check pause state")

        # INSTANCE STATUS CHECK - prevent starting jobs for terminated instances or paused instances
        # Check for all job types that have a target instance_id (TASK jobs may not have instance_id while PENDING)
        if job.instance_id:
            if (
                self._instance_manager is not None
                and hasattr(self._instance_manager, '_instance_repository')
                and self._instance_manager._instance_repository is not None
            ):
                try:
                    instance = await asyncio.to_thread(
                        self._instance_manager._instance_repository.get, job.instance_id
                    )
                except Exception as e:
                    logger.warning(
                        f"start_job: failed to fetch instance {job.instance_id[:8]}...: {e}"
                    )
                    instance = None

                if instance is None:
                    logger.info(
                        f"[TRACE] start_job: job {job_id[:8]}... SKIP — instance {job.instance_id[:8]}... NOT FOUND"
                    )
                    return None

                if instance.status in TERMINAL_STATUSES:
                    # D13: all jobs are TASK-type now (message-type jobs
                    # are rejected at enqueue). TASK jobs get fresh
                    # instances — clear stale ref and allow normal start.
                    logger.info(
                        f"[TRACE] start_job: clearing stale instance_id for TASK job {job_id[:8]}... "
                        f"(instance {job.instance_id[:8]}... is {instance.status})"
                    )
                    await asyncio.to_thread(self._repository.update, job.job_id, instance_id=None)
                    # Fall through to normal start logic below (don't return None)

                if instance.status == InstanceStatus.PAUSED.value:
                    logger.info(
                        f"[TRACE] start_job: job {job_id[:8]}... SKIP — instance {job.instance_id[:8]}... PAUSED"
                    )
                    return None

        # Generate instance_id: TASK jobs always get a new UUID.
        # D13: removed MESSAGE-specific ``if job.job_type == "message"
        # and job.instance_id`` branch — no MESSAGE jobs exist anymore,
        # so all jobs uniformly get a fresh UUID.
        instance_id = str(uuid.uuid4())
        
        # [TRACE] Log instance_id being used
        logger.info(
            f"[TRACE] start_job: using instance_id={instance_id[:8]}... "
            f"for job={job_id[:8]}... (job_type={job.job_type})"
        )

        # Acquire lock FIRST - if we can't get it, don't transition the job
        lock_acquired = False
        if job.queue_id and job.project_id:
            concurrency_limit = await self._get_concurrency_limit(job.queue_id)
            logger.info(
                f"[TRACE] start_job: acquiring queue lock for job {job_id[:8]}... "
                f"queue={job.queue_id[:8]}... concurrency_limit={concurrency_limit}"
            )
            
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
                logger.info(
                    f"[TRACE] start_job: job {job_id[:8]}... SKIP — lock NOT acquired (concurrency limit)"
                )
                return None
        elif job.project_id:
            logger.debug(f"[TRACE] start_job: acquiring project lock for job {job_id[:8]}...")
            lock_acquired = await self._lock_manager.acquire(
                project_id=job.project_id,
                job_id=job_id,
                instance_id=instance_id,
            )
            
            if not lock_acquired:
                logger.debug(
                    f"[TRACE] start_job: job {job_id[:8]}... SKIP — project lock NOT acquired"
                )
                return None
        
        # Lock acquired (or no locking needed) - now try to start job atomically
        logger.debug(f"[TRACE] start_job: attempting atomic transition PENDING→PROCESSING for job {job_id[:8]}...")
        # C11: try/finally ensures lock is released on ALL failure paths
        # (not just ValueError — e.g. OperationalError, CancelledError, deadlocks).
        # On success, the lock is intentionally kept (JobProcessor holds it until
        # the job completes/fails). ``started_ok`` guards against releasing on
        # the success path, matching the original lock-on-success semantics.
        # The ``except (ValueError, InvalidTransitionError)`` preserves the
        # original "return None" behavior for callers (lock contention /
        # already-started job / invalid status transition) — the lock release
        # happens in the finally block so we still drop it on the caught-exception
        # paths. ``InvalidTransitionError`` is NOT a ValueError subclass (see
        # daemon/services/job_state_machine.py:35), so it must be caught
        # explicitly when ``start_job_atomic`` raises it instead of ValueError.
        started_ok = False
        try:
            started_job = await asyncio.to_thread(
                self._repository.start_job_atomic, job_id, instance_id
            )
            started_ok = True
            logger.debug(
                f"[TRACE] start_job: SUCCESS job {job_id[:8]}... started with instance={instance_id[:8]}..."
            )
            return started_job
        except (ValueError, InvalidTransitionError):
            # Job state changed (already started/cancelled) — preserve original
            # behavior of returning None so callers can detect "lock held by
            # another worker" without having to catch an exception.
            return None
        finally:
            if not started_ok and lock_acquired:
                # Release the lock on ANY failure path: ValueError (caller
                # sees None), OperationalError / CancelledError (caller sees
                # the exception), or any other unexpected error. Without this
                # finally, a non-ValueError would leak the lock permanently.
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
        """Backward-compat shim for the legacy MESSAGE-specific cancel entry point.

        The :class:`MessageJobHandler` was removed; the per-job
        CancellationToken that ``MessageJobHandler.cancel_message_job``
        used to signal no longer exists (unified dispatcher owns the
        cancellation token via the WorkerPool). This shim delegates to
        the general-purpose :meth:`cancel_job` which handles both
        PENDING and PROCESSING states atomically and notifies watchers.

        Callers:
          * :func:`daemon.services.instance_lifecycle.terminate_instance`
            still calls this entry point after the bulk DB cancel.

        Args:
            job_id: The job to cancel.
        """
        await self.cancel_job(job_id)
