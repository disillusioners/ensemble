"""JobProcessor - Background worker for processing queued jobs."""

import asyncio
import logging
from typing import Optional

from daemon.services.job_queue_service import JobQueueService
from daemon.services.job_lock_manager import JobLockManager
from daemon.manager import InstanceManager
from daemon.repositories import SQLModelProjectRepository

logger = logging.getLogger(__name__)


class JobProcessor:
    """Background worker that processes queued jobs.
    
    Continuously polls for pending jobs and processes them one at a time
    per worker instance. Acquires project locks before processing and
    releases them when complete. Skips jobs for projects with paused queues.
    
    Attributes:
        _queue_service: JobQueueService instance for job operations.
        _instance_manager: InstanceManager instance for spawning instances.
        _project_repo: SQLModelProjectRepository for checking project pause state.
        _poll_interval: Time in seconds between poll cycles.
        _running: Flag to control the processing loop.
    """
    
    def __init__(
        self,
        queue_service: JobQueueService,
        instance_manager: InstanceManager,
        project_repo: SQLModelProjectRepository,
        poll_interval: float = 2.0,
    ):
        """Initialize the JobProcessor.
        
        Args:
            queue_service: JobQueueService for job operations.
            instance_manager: InstanceManager for spawning instances.
            project_repo: SQLModelProjectRepository for checking project pause state.
            poll_interval: Seconds between poll cycles (default: 2.0).
        """
        self._queue_service = queue_service
        self._instance_manager = instance_manager
        self._project_repo = project_repo
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
        
        # Check if project's queue is paused before processing
        if job.project_id:
            project = await asyncio.to_thread(self._project_repo.get, job.project_id)
            if project and project.job_queue_paused:
                logger.info(
                    f"Skipping job {job.job_id} - project {job.project_id} queue is paused"
                )
                return
        
        logger.info(f"Processing job {job.job_id} for project {job.project_id}")
        
        try:
            # Acquire lock and start job
            started_job = await self._queue_service.start_job(job.job_id)
            if started_job is None:
                # Lock acquisition failed or job was cancelled
                logger.warning(f"Could not start job {job.job_id} - may be cancelled or lock held")
                return
            
            # Spawn instance for this job
            try:
                instance_id = await self._instance_manager.spawn_instance(
                    agent_id=job.agent_id,
                    instance_id=started_job.instance_id,
                )
            except Exception as e:
                logger.error(f"Failed to spawn instance for job {job.job_id}: {e}")
                await self._queue_service.complete_job(job.job_id, success=False, error=str(e))
                return
            
            # Send the job message to the instance
            try:
                await self._instance_manager.enqueue_message(
                    instance_id=instance_id,
                    message=job.message,
                    source=job.source,
                )
            except Exception as e:
                logger.error(f"Failed to enqueue message for job {job.job_id}: {e}")
                await self._queue_service.complete_job(job.job_id, success=False, error=str(e))
                return
            
            # Mark job as being processed (not yet complete - instance does the work)
            await self._queue_service.update_job(
                job.job_id,
                status="processing",
                result_summary="Job enqueued for processing"
            )
            logger.info(f"Job {job.job_id} queued successfully for instance {instance_id}")
            
            # Trigger next job for this project (if any)
            if job.project_id:
                await self._queue_service.trigger_next_job(job.project_id)
                
        except Exception as e:
            logger.exception(f"Failed to process job {job.job_id}: {e}")
            await self._queue_service.complete_job(job.job_id, success=False, error=str(e))


# Backward compatibility alias
TaskProcessor = JobProcessor
