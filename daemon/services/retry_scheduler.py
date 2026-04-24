"""RetryScheduler - Background scheduler for checking and triggering retryable jobs."""

from __future__ import annotations

import asyncio
import fcntl
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from daemon.services.job_queue_service import JobQueueService
    from daemon.services.job_retry_engine import JobRetryEngine
    from daemon.services.dispatch_event_bus import DispatchEventBus

logger = logging.getLogger(__name__)

# Module-level lock state
_scheduler_lock_file: int | None = None
_scheduler_lock_path: Path | None = None


def _acquire_scheduler_lock(lock_dir: Path) -> bool:
    """Acquire an exclusive lock to prevent duplicate scheduler instances.
    
    Args:
        lock_dir: Directory to store the lock file.
        
    Returns:
        True if lock acquired, False if another scheduler is already running.
    """
    global _scheduler_lock_file, _scheduler_lock_path
    
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / "retry_scheduler.lock"
    _scheduler_lock_path = lock_path
    
    # Open lock file (create if doesn't exist)
    lock_file = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    _scheduler_lock_file = lock_file
    
    try:
        # Try non-blocking exclusive lock
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except (IOError, OSError):
        # Lock is held by another process
        os.close(lock_file)
        _scheduler_lock_file = None
        _scheduler_lock_path = None
        return False


def _release_scheduler_lock() -> None:
    """Release the scheduler lock if held."""
    global _scheduler_lock_file, _scheduler_lock_path
    
    if _scheduler_lock_file is not None:
        try:
            fcntl.flock(_scheduler_lock_file, fcntl.LOCK_UN)
            os.close(_scheduler_lock_file)
        except (IOError, OSError):
            pass
        finally:
            _scheduler_lock_file = None
            _scheduler_lock_path = None


class RetryScheduler:
    """Background scheduler that checks for retryable jobs and triggers processing.
    
    The scheduler periodically polls for jobs that are ready for retry
    (i.e., their next_retry_at timestamp has passed). For each project
    with retryable jobs, it triggers the job processor to pick them up.
    
    Note: Jobs are already transitioned from FAILED->PENDING by the retry
    engine's maybe_retry() method when the job first fails. This scheduler's
    job is to wake up the processor for projects that have jobs ready to retry.
    
    Attributes:
        _retry_engine: The retry engine for finding retryable jobs.
        _queue_service: Queue service for triggering job processing.
        _poll_interval: Seconds between retry checks (default: 60).
        _running: Flag to control the scheduler loop.
        _task: The asyncio task running the scheduler loop.
        _dispatch_bus: Optional DispatchEventBus for immediate wakeup.
    """
    
    def __init__(
        self,
        retry_engine: "JobRetryEngine",
        queue_service: "JobQueueService",
        poll_interval: float = 60.0,
        lock_dir: Path | None = None,
        dispatch_bus: "DispatchEventBus" | None = None,
    ):
        """Initialize RetryScheduler.
        
        Args:
            retry_engine: The retry engine for finding retryable jobs.
            queue_service: Queue service for triggering job processing.
            poll_interval: Seconds between retry checks (default: 60).
            lock_dir: Directory for lock file storage (default: ./data).
            dispatch_bus: Optional DispatchEventBus for immediate job processor wakeup.
        """
        self._retry_engine = retry_engine
        self._queue_service = queue_service
        self._poll_interval = poll_interval
        self._lock_dir = lock_dir or Path("./data")
        self._running = False
        self._task: asyncio.Task | None = None
        self._dispatch_bus = dispatch_bus
    
    async def start(self) -> None:
        """Start the background scheduler loop.
        
        Raises:
            RuntimeError: If another scheduler instance is already running.
        """
        if self._running:
            return
        
        # Acquire exclusive lock to prevent duplicate instances
        if not _acquire_scheduler_lock(self._lock_dir):
            logger.warning("Another RetryScheduler instance is already running. Exiting.")
            raise RuntimeError("Another RetryScheduler instance is already running")
        
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("RetryScheduler started")
    
    async def stop(self) -> None:
        """Stop the background scheduler loop gracefully."""
        if not self._running:
            _release_scheduler_lock()
            return
        
        self._running = False
        
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        
        _release_scheduler_lock()
        logger.info("RetryScheduler stopped")
    
    async def _run_loop(self) -> None:
        """Main scheduler loop - periodically checks for retryable jobs."""
        while self._running:
            try:
                await self._check_and_trigger()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception(f"Error in retry scheduler loop: {e}")
            
            await asyncio.sleep(self._poll_interval)
    
    async def _check_and_trigger(self) -> None:
        """Check for retryable jobs and trigger processing.
        
        Finds jobs ready for retry (where next_retry_at <= now) and
        triggers the job processor for each project that has retryable jobs.
        
        The jobs are already in PENDING state - we just need to wake up
        the processor to pick them up. Also fires dispatch events for
        immediate processor wakeup via event-driven dispatch.
        """
        # Find all jobs ready for retry (sync call, so wrap in to_thread)
        retryable_jobs = await asyncio.to_thread(self._retry_engine.find_retryable_jobs)
        
        if not retryable_jobs:
            return
        
        # Get unique project_ids
        # [R3] Post-migration: project_id is never None, this branch is unreachable
        project_ids = set(job.project_id for job in retryable_jobs if job.project_id)
        
        logger.info(f"Found {len(retryable_jobs)} retryable jobs in {len(project_ids)} projects")
        
        # Trigger processor for each project
        for project_id in project_ids:
            try:
                # trigger_next_job is async, so we can await it directly
                await self._queue_service.trigger_next_job(project_id)
                logger.debug(f"Triggered job processor for project {project_id}")
            except Exception as e:
                logger.error(f"Failed to trigger processor for project {project_id}: {e}")
        
        # Also notify dispatch bus for immediate wakeup
        if self._dispatch_bus is not None:
            for project_id in project_ids:
                self._dispatch_bus.notify_new_job(project_id)
            logger.debug(f"Fired dispatch events for {len(project_ids)} projects")
