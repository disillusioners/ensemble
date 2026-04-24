"""Job Retry Engine - Handles automatic retry of failed jobs with backoff."""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlmodel import Session as SQLModelSession

from daemon.config import JobSystemConfig
from daemon.repositories.job_queue import JobItem, JobRepository
from daemon.repositories.job_queue.dead_letter_repository import DeadLetterRepository
from daemon.services.dead_letter_service import DeadLetterService

if TYPE_CHECKING:
    from daemon.repositories.job_queue.queue_repository import JobQueueRepository

logger = logging.getLogger(__name__)


class JobRetryEngine:
    """Handles automatic retry of failed jobs with exponential backoff.
    
    The retry engine is responsible for:
    - Calculating retry backoff delays
    - Determining if a job should be retried
    - Executing retry operations (transition FAILED -> PENDING)
    - Moving exhausted jobs to the dead letter queue
    
    Exponential backoff formula:
        delay = min(base_seconds * 2^retry_count * multiplier + jitter, max_seconds)
        jitter = random.uniform(0, base_seconds * 0.5)
        next_retry_at = failed_at + delay
    """
    
    def __init__(
        self,
        job_repo: JobRepository,
        queue_repo: JobQueueRepository,
        dlq_service: DeadLetterService,
        config: JobSystemConfig,
        job_queue_service: Any = None,
        loop: asyncio.AbstractEventLoop | None = None,
    ):
        """Initialize the JobRetryEngine.
        
        Args:
            job_repo: Repository for job persistence.
            queue_repo: Repository for queue metadata.
            dlq_service: Service for dead letter queue operations.
            config: Job system configuration.
            job_queue_service: Optional JobQueueService for watcher notifications.
            loop: Optional event loop for async notifications.
        """
        self._job_repo = job_repo
        self._queue_repo = queue_repo
        self._dlq_service = dlq_service
        self._config = config
        self._job_queue_service = job_queue_service
        self._loop = loop
    
    def calculate_backoff(self, retry_count: int, config: JobSystemConfig = None) -> float:
        """Calculate backoff delay in seconds using exponential backoff + jitter.
        
        Formula: min(base * 2^retry_count * multiplier + jitter, max)
        - base = config.retry_backoff_base_seconds (default 60)
        - max = config.retry_backoff_max_seconds (default 3600)
        - multiplier = config.retry_backoff_multiplier (default 2.0)
        - jitter = random.uniform(0, base * 0.5)
        
        Args:
            retry_count: Number of retries already attempted.
            config: Optional config override. Uses self._config if not provided.
            
        Returns:
            Delay in seconds before the next retry.
        """
        cfg = config if config is not None else self._config
        base = cfg.retry_backoff_base_seconds
        max_delay = cfg.retry_backoff_max_seconds
        multiplier = cfg.retry_backoff_multiplier
        
        # Calculate exponential backoff: base * multiplier^retry_count
        delay = base * (multiplier ** retry_count)
        
        # Add jitter (up to 50% of base)
        jitter = random.uniform(0, base * 0.5)
        delay += jitter
        
        # Cap at max
        return min(delay, max_delay)
    
    def get_max_retries(
        self,
        job: JobItem,
        queue: "JobQueueRepository | None" = None,
        config: JobSystemConfig = None,
    ) -> int:
        """Get effective max retries using fallback chain.
        
        Fallback chain: job.max_retries -> queue.default_max_retries -> config.default_max_retries -> 3
        Hard cap at 100.
        
        Args:
            job: The job to evaluate.
            queue: Optional queue for queue-level defaults.
            config: Optional config override. Uses self._config if not provided.
            
        Returns:
            Maximum retry count (hard capped at 100).
        """
        cfg = config if config is not None else self._config
        
        # Start with job's max_retries
        resolved_max_retries = job.max_retries
        
        # Fall back to queue default
        if resolved_max_retries is None and queue is not None and job.queue_id:
            queue_obj = self._queue_repo.get(job.queue_id) if callable(self._queue_repo.get) else None
            if queue_obj and queue_obj.default_max_retries is not None:
                resolved_max_retries = queue_obj.default_max_retries
        
        # Fall back to config default
        if resolved_max_retries is None:
            resolved_max_retries = cfg.default_max_retries
        
        # Final fallback to 3
        if resolved_max_retries is None:
            resolved_max_retries = 3
        
        # Hard cap at 100
        return min(resolved_max_retries, 100)
    
    def should_retry(
        self,
        job: JobItem,
        queue: "JobQueueRepository | None" = None,
        config: JobSystemConfig = None,
    ) -> bool:
        """Determine if a job should be retried.
        
        Returns True if job status is FAILED and retry_count < max_retries.
        
        Args:
            job: The job to evaluate.
            queue: Optional queue for queue-level defaults.
            config: Optional config override. Uses self._config if not provided.
            
        Returns:
            True if the job should be retried, False otherwise.
        """
        cfg = config if config is not None else self._config
        
        # If DLQ is disabled, no auto-retry
        if not cfg.dlq_enabled:
            return False
        
        # Check if retries are explicitly disabled (max_retries = 0)
        if job.max_retries == 0:
            return False
        
        # Must be in FAILED state
        if job.status != "failed":
            return False
        
        # Check retry count vs max
        max_retries = self.get_max_retries(job, queue, config)
        return job.retry_count < max_retries
    
    def maybe_retry(
        self,
        job_id: str,
        queue: "JobQueueRepository | None" = None,
        config: JobSystemConfig = None,
    ) -> JobItem | None:
        """Attempt to retry a failed job atomically.
        
        This method executes in a single SQLite session/transaction:
        1. Read the job from the session
        2. If job is None or not FAILED, return None
        3. Call should_retry():
           - If True: Calculate backoff, compute next_retry_at, transition FAILED->PENDING
           - If False: Call dlq_service.move_to_dlq() with the same session
        4. session.commit()
        5. Return the updated job (or None if moved to DLQ)
        
        Args:
            job_id: The job ID to retry.
            queue: Optional queue for queue-level defaults.
            config: Optional config override. Uses self._config if not provided.
            
        Returns:
            The updated JobItem if retry was triggered, None if job not found,
            not FAILED, or moved to DLQ.
        """
        from daemon.services.job_state_machine import job_state_machine
        
        with SQLModelSession(self._job_repo.engine) as session:
            from daemon.repositories.job_queue.models import JobItem
            
            # Read the job from the session
            job = session.get(JobItem, job_id)
            
            if job is None:
                return None
            
            if job.status != "failed":
                return None
            
            # Check if we should retry or move to DLQ
            if self.should_retry(job, queue, config):
                # Calculate backoff and next_retry_at
                delay_seconds = self.calculate_backoff(job.retry_count, config)
                next_retry = datetime.utcnow() + timedelta(seconds=delay_seconds)
                next_retry_at = next_retry.isoformat()
                
                # Validate transition is allowed
                job_state_machine.validate_transition("failed", "pending")
                
                # Update job status and fields
                job.status = "pending"
                job.retry_count += 1
                job.next_retry_at = next_retry_at
                job.failed_at = None  # clear for next attempt
                job.error_message = None  # clear error for fresh retry
                
                session.commit()
                session.refresh(job)
                
                logger.info(
                    f"Job {job_id} scheduled for retry (attempt {job.retry_count}), "
                    f"next_retry_at={next_retry_at}"
                )
                
                return job
            else:
                # No more retries - move to DLQ
                try:
                    self._dlq_service.move_to_dlq(session, job_id, reason="MAX_RETRIES")
                    session.commit()
                    logger.info(f"Job {job_id} moved to DLQ after {job.retry_count} retries")
                    
                    # Notify watchers after successful DLQ commit
                    if self._job_queue_service and self._loop and self._loop.is_running():
                        asyncio.run_coroutine_threadsafe(
                            self._job_queue_service.notify_watchers(job_id, "dead_letter", job.error_message),
                            self._loop,
                        )
                except Exception as e:
                    session.rollback()
                    logger.error(
                        f"Failed to move job {job_id} to DLQ, rolled back: {e}"
                    )
                    raise
                
                return None
    
    def find_retryable_jobs(self, project_id: str = None) -> list[JobItem]:
        """Find FAILED jobs ready for retry (next_retry_at <= now).
        
        IMPORTANT: Only returns jobs that are genuinely FAILED with a clear
        path to PENDING. Jobs being cancelled (transitioning to CANCELLED)
        are excluded by checking the status is still FAILED.
        
        Args:
            project_id: Optional project ID to filter by.
            
        Returns:
            List of JobItem objects that are FAILED and their next_retry_at
            has passed.
        """
        return self._job_repo.find_retryable_jobs(project_id=project_id)


# Module-level singleton for dependency injection
_engine: JobRetryEngine | None = None


def get_retry_engine() -> JobRetryEngine:
    """Get the module-level JobRetryEngine instance.
    
    Returns:
        The singleton JobRetryEngine instance.
        
    Raises:
        RuntimeError: If engine has not been initialized.
    """
    if _engine is None:
        raise RuntimeError("JobRetryEngine has not been initialized")
    return _engine


def set_retry_engine(engine: JobRetryEngine) -> None:
    """Set the module-level JobRetryEngine instance.
    
    Args:
        engine: The engine instance to use.
    """
    global _engine
    _engine = engine
