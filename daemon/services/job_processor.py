"""JobProcessor - Background worker for processing queued jobs."""

import asyncio
import logging
from typing import Optional

from daemon.services.job_queue_service import JobQueueService
from daemon.services.job_lock_manager import JobLockManager
from daemon.manager import SessionManager

logger = logging.getLogger(__name__)


class JobProcessor:
    """Background worker that processes queued jobs.
    
    Continuously polls for pending jobs and processes them one at a time
    per worker instance. Acquires project locks before processing and
    releases them when complete.
    
    Attributes:
        _queue_service: JobQueueService instance for job operations.
        _session_manager: SessionManager instance for spawning sessions.
        _poll_interval: Time in seconds between poll cycles.
        _running: Flag to control the processing loop.
    """
    
    def __init__(
        self,
        queue_service: JobQueueService,
        session_manager: SessionManager,
        poll_interval: float = 2.0,
    ):
        """Initialize the JobProcessor.
        
        Args:
            queue_service: JobQueueService for job operations.
            session_manager: SessionManager for spawning sessions.
            poll_interval: Seconds between poll cycles (default: 2.0).
        """
        self._queue_service = queue_service
        self._session_manager = session_manager
        self._poll_interval = poll_interval
        self._running = False
        self._job: Optional[asyncio.Task] = None
    
    async def start(self) -> None:
        """Start the background processing loop."""
        if self._running:
            return
        
        self._running = True
        self._job = asyncio.create_task(self._process_loop())
        logger.info("JobProcessor started")
    
    async def stop(self) -> None:
        """Stop the background processing loop gracefully."""
        if not self._running:
            return
        
        self._running = False
        
        if self._job is not None:
            self._job.cancel()
            try:
                await self._job
            except asyncio.CancelledError:
                pass
            self._job = None
        
        logger.info("JobProcessor stopped")
    
    async def _process_loop(self) -> None:
        """Main processing loop - polls for and processes jobs."""
        while self._running:
            try:
                await self._process_next_job()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception(f"Error in processing loop: {e}")
            
            await asyncio.sleep(self._poll_interval)
    
    async def _process_next_job(self) -> None:
        """Get the next pending job and process it."""
        # Get next pending job (highest priority, oldest first)
        job = self._queue_service.get_next_pending_job()
        if job is None:
            return
        
        logger.info(f"Processing job {job.job_id} for project {job.project_id}")
        
        try:
            # Acquire lock and start job
            started_job = await self._queue_service.start_job(job.job_id)
            if started_job is None:
                # Lock acquisition failed or job was cancelled
                logger.warning(f"Could not start job {job.job_id} - may be cancelled or lock held")
                return
            
            # Spawn session for this job
            try:
                session_id = self._session_manager.spawn_session(
                    agent_dir=job.agent_dir,
                    session_id=started_job.session_id,
                )
            except Exception as e:
                logger.error(f"Failed to spawn session for job {job.job_id}: {e}")
                await self._queue_service.complete_job(job.job_id, success=False, error=str(e))
                return
            
            # Send the job message to the session
            try:
                await self._session_manager.enqueue_message(
                    session_id=session_id,
                    message=job.message,
                    source=job.source,
                )
            except Exception as e:
                logger.error(f"Failed to enqueue message for job {job.job_id}: {e}")
                await self._queue_service.complete_job(job.job_id, success=False, error=str(e))
                return
            
            # Mark job as being processed (not yet complete - session does the work)
            await self._queue_service.update_job(
                job.job_id,
                status="processing",
                result_summary="Job enqueued for processing"
            )
            logger.info(f"Job {job.job_id} queued successfully for session {session_id}")
            
            # Trigger next job for this project (if any)
            if job.project_id:
                await self._queue_service.trigger_next_job(job.project_id)
                
        except Exception as e:
            logger.exception(f"Failed to process job {job.job_id}: {e}")
            await self._queue_service.complete_job(job.job_id, success=False, error=str(e))


# Backward compatibility alias
TaskProcessor = JobProcessor
