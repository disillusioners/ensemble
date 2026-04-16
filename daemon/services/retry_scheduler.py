"""RetryScheduler - Background scheduler for checking and triggering retryable jobs."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from daemon.services.job_queue_service import JobQueueService
    from daemon.services.job_retry_engine import JobRetryEngine

logger = logging.getLogger(__name__)


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
    """
    
    def __init__(
        self,
        retry_engine: "JobRetryEngine",
        queue_service: "JobQueueService",
        poll_interval: float = 60.0,
    ):
        """Initialize RetryScheduler.
        
        Args:
            retry_engine: The retry engine for finding retryable jobs.
            queue_service: Queue service for triggering job processing.
            poll_interval: Seconds between retry checks (default: 60).
        """
        self._retry_engine = retry_engine
        self._queue_service = queue_service
        self._poll_interval = poll_interval
        self._running = False
        self._task: Optional[asyncio.Task] = None
    
    async def start(self) -> None:
        """Start the background scheduler loop."""
        if self._running:
            return
        
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("RetryScheduler started")
    
    async def stop(self) -> None:
        """Stop the background scheduler loop gracefully."""
        if not self._running:
            return
        
        self._running = False
        
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        
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
        the processor to pick them up.
        """
        # Find all jobs ready for retry (sync call, so wrap in to_thread)
        retryable_jobs = await asyncio.to_thread(self._retry_engine.find_retryable_jobs)
        
        if not retryable_jobs:
            return
        
        # Get unique project_ids
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
