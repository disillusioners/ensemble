"""Job Retry Engine - Handles automatic retry of failed jobs with backoff."""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone
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

        Audit H5 fix (P1): the prior implementation read the job into
        a Python-side ORM session, performed ``retry_count += 1`` in
        Python, and committed. Under concurrent retry decisions
        (retry sweep + explicit retry from ``JobFeedbackObserver``)
        two callers both observed ``retry_count = N``, both computed
        ``N + 1``, and both wrote ``N + 1`` — the true ``N + 2``
        increment was lost. Retry-exhaustion decisions were then
        off-by-one and a job could be retried past ``max_retries``.

        New flow (status guard + atomic retry_count++ in SQL):

        1. Read the job for **decision only** (status check,
           ``should_retry()``, ``calculate_backoff()``, and
           ``get_max_retries()`` — which carries the fallback chain
           ``job.max_retries → queue.default_max_retries →
           config.default_max_retries → 3``). No mutation.
        2. If ``should_retry`` is True:
           * Compute ``next_retry_at`` from
             ``calculate_backoff(current_retry_count)``.
           * Compute effective ``max_retries`` for the SQL guard.
           * Call ``JobRepository.atomic_retry``, which issues a
             single guarded UPDATE
             ``SET status='pending', retry_count=retry_count+1, ...
              WHERE job_id=:job_id AND status='failed'
                    AND retry_count < :max_retries``.
             Two concurrent callers cannot both succeed — the SQL
             predicates are re-evaluated after the row lock is
             acquired (PostgreSQL EvalPlanQual) or the
             single-statement UPDATE is atomic at the database
             level (SQLite). Returns ``None`` if the row's status
             flipped concurrently (CANCELLED / DEAD_LETTER) or
             ``retry_count`` already hit ``max_retries``.
           * If ``atomic_retry`` returns ``None``, the job was
             concurrently mutated — return ``None`` (no DLQ retry
             here, the caller that flipped the status owns the
             transition).
        3. If ``should_retry`` is False (retries exhausted):
           * Call ``DeadLetterService.move_to_dlq`` (which already
             holds a row lock + status check, see
             ``daemon/services/dead_letter_service.py``). The
             ``UPDATE job_queue_items SET status='dead_letter'``
             inside that helper is therefore safe — no additional
             guard is required at this layer.

        Args:
            job_id: The job ID to retry.
            queue: Optional queue for queue-level defaults.
            config: Optional config override. Uses self._config if not provided.

        Returns:
            The updated JobItem if retry was triggered, None if job not found,
            not FAILED, concurrently cancelled / dead_lettered, or moved to DLQ.
        """
        from daemon.services.job_state_machine import job_state_machine

        # 1. Read-only decision pass. We do NOT mutate anything in
        # this session — the actual retry is a single guarded
        # UPDATE issued by JobRepository.atomic_retry below.
        with SQLModelSession(self._job_repo.engine) as session:
            from daemon.repositories.job_queue.models import JobItem

            job = session.get(JobItem, job_id)

            if job is None:
                return None

            if job.status != "failed":
                return None

        # 2. Decide retry vs DLQ. should_retry() and the backoff /
        # max_retries helpers operate on the in-memory JobItem
        # snapshot — that's fine, they're decision-only and the
        # SQL-level guard in atomic_retry is the actual race-safety
        # boundary.
        if self.should_retry(job, queue, config):
            # Backoff is computed from the CURRENT retry_count
            # (before increment) — matches the prior Python
            # implementation's semantics exactly.
            delay_seconds = self.calculate_backoff(job.retry_count, config)
            next_retry = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
            next_retry_at = next_retry.isoformat()

            # Resolve effective max_retries via the fallback chain
            # so the SQL guard matches the decision.
            max_retries = self.get_max_retries(job, queue, config)

            # Validate transition is allowed (cheap fail-fast before
            # opening a session / issuing the UPDATE).
            job_state_machine.validate_transition("failed", "pending")

            # 3. Atomic UPDATE with status + retry_count guards.
            updated_job = self._job_repo.atomic_retry(
                job_id=job_id,
                max_retries=max_retries,
                next_retry_at=next_retry_at,
            )

            if updated_job is None:
                # The row's status flipped (concurrent CANCELLED or
                # DEAD_LETTER) between our read and the UPDATE, or
                # retry_count already reached max_retries. In all
                # three cases, no further action is taken here —
                # the owning writer is responsible for the next
                # transition.
                logger.debug(
                    "Job %s atomic retry no-op (concurrent transition "
                    "or retry_count at cap)",
                    job_id,
                )
                return None

            logger.info(
                f"Job {job_id} scheduled for retry (attempt {updated_job.retry_count}), "
                f"next_retry_at={next_retry_at}"
            )

            return updated_job

        # 4. Retries exhausted — move to DLQ.
        # DeadLetterService.move_to_dlq holds a row lock
        # (with_for_update) and re-checks status == 'failed' under
        # the lock, so the FAILED → DEAD_LETTER transition is safe
        # against concurrent retries / cancellations. The atomic
        # UPDATE inside atomic_retry above (which also enforces
        # status='failed' + retry_count < max_retries) and the lock
        # + status check inside move_to_dlq together cover the full
        # maybe_retry → DLQ path.
        try:
            with SQLModelSession(self._job_repo.engine) as session:
                self._dlq_service.move_to_dlq(session, job_id, reason="MAX_RETRIES")
                session.commit()

            # Re-read after commit to capture the dead-letter state
            # for notification context (error_message, retry_count).
            with SQLModelSession(self._job_repo.engine) as session:
                notified = session.get(JobItem, job_id)
                error_msg = notified.error_message if notified else None

            logger.info(
                f"Job {job_id} moved to DLQ after "
                f"{notified.retry_count if notified else '?'} retries"
            )

            # Notify watchers after successful DLQ commit
            if self._job_queue_service and self._loop and self._loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    self._job_queue_service.notify_watchers(job_id, "dead_letter", error_msg),
                    self._loop,
                )
        except Exception as e:
            logger.error(
                f"Failed to move job {job_id} to DLQ, rolled back: {e}"
            )
            raise

        return None
    
    def find_retryable_jobs(self, project_id: str | None = None) -> list[JobItem]:
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
