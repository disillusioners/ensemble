"""Job Retry Engine - Handles automatic retry of failed jobs with backoff."""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from sqlmodel import Session as SQLModelSession

from daemon.config import JobSystemConfig
from daemon.repositories.job_queue import AdmissionState, JobItem, JobRepository
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
        task_repo: Any = None,
    ):
        """Initialize the JobRetryEngine.

        Args:
            job_repo: Repository for job persistence.
            queue_repo: Repository for queue metadata.
            dlq_service: Service for dead letter queue operations.
            config: Job system configuration.
            job_queue_service: Optional JobQueueService for watcher notifications.
            loop: Optional event loop for async notifications.
            task_repo: Optional TaskRepository used by the F12 fix to
                cancel stale PENDING tasks on the retried instance
                before re-admission. Defaults to ``None`` (F12 cancel
                becomes a no-op for older wirings — the test suite
                wires the real repo, and ``daemon/api.py`` is updated
                to pass it).
        """
        self._job_repo = job_repo
        self._queue_repo = queue_repo
        self._dlq_service = dlq_service
        self._config = config
        self._job_queue_service = job_queue_service
        self._loop = loop
        self._task_repo = task_repo
    
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
        
        # Phase 4 cleanup: a DONE-admission row is retryable IFF it
        # carries the ``failed_at`` marker — the timestamp set by
        # ``atomic_retry`` / the legacy ``fail_job`` helper when a
        # job transitions through the FAILED bucket. Without the
        # ``failed_at`` marker, the row reached ``done`` via the
        # COMPLETED or CANCELLED path and is NOT retryable. The
        # previous ``status='failed'`` check lost this distinction
        # once Phase 4 cleanup froze the ``status`` column.
        if job.admission_state != AdmissionState.DONE.value:
            return False
        if job.failed_at is None:
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

        1. Read the job for **decision only** (``admission_state`` check,
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
             ``SET status='pending', admission_state='queued', retry_count=retry_count+1, ...
              WHERE job_id=:job_id AND admission_state='active'
                    AND retry_count < :max_retries``.
             Two concurrent callers cannot both succeed — the SQL
             predicates are re-evaluated after the row lock is
             acquired (PostgreSQL EvalPlanQual) or the
             single-statement UPDATE is atomic at the database
             level (SQLite). Returns ``None`` if the row's
             admission_state flipped concurrently (DONE / DEAD) or
             ``retry_count`` already hit ``max_retries``.
           * If ``atomic_retry`` returns ``None``, the job was
             concurrently mutated — return ``None`` (no DLQ retry
             here, the caller that flipped the admission_state owns
             the transition).
        3. If ``should_retry`` is False (retries exhausted):
           * Call ``DeadLetterService.move_to_dlq`` (which already
             holds a row lock + admission_state check, see
             ``daemon/services/dead_letter_service.py``). The
             ``UPDATE job_queue_items SET admission_state='dead'``
             inside that helper is therefore safe — no additional
             guard is required at this layer.

        Phase 4 (Job as Queue Proxy): the eligibility check moved
        from ``status == 'failed'`` to ``admission_state == 'active'``
        — Plan §3.2 retry-without-instance guarantee removes the
        intermediate FAILED state. The atomic_retry SQL guard moved in
        lockstep (see ``JobRepository.atomic_retry``).

        Args:
            job_id: The job ID to retry.
            queue: Optional queue for queue-level defaults.
            config: Optional config override. Uses self._config if not provided.

        Returns:
            The updated JobItem if retry was triggered, None if job not found,
            not ACTIVE, concurrently transitioned to terminal, or moved to DLQ.
        """
        from daemon.services.job_state_machine import job_state_machine
        from daemon.repositories.job_queue.models import AdmissionState

        # 1. Read-only decision pass. We do NOT mutate anything in
        # this session — the actual retry is a single guarded
        # UPDATE issued by JobRepository.atomic_retry below.
        with SQLModelSession(self._job_repo.engine) as session:
            from daemon.repositories.job_queue.models import JobItem

            job = session.get(JobItem, job_id)

            if job is None:
                return None

            # Phase 4 cleanup: eligibility is keyed on
            # ``admission_state`` and the ``failed_at`` marker
            # together. The previous ``status='failed'`` co-check
            # is removed because the legacy ``status`` column is no
            # longer written — every JobItem row's ``status`` is
            # frozen at the INSERT default (``"pending"``), so the
            # check would reject every new retryable row. The
            # ``failed_at`` marker set by ``fail_job`` /
            # ``atomic_retry`` distinguishes FAILED-path
            # ``done``-admission rows (retryable / DLQ-eligible)
            # from COMPLETED / CANCELLED ``done``-admission rows
            # (terminal, neither). The production ``atomic_retry``
            # SQL guard enforces the matching predicate at COMMIT.
            if job.admission_state not in (
                AdmissionState.ACTIVE.value,
                AdmissionState.DONE.value,
            ) or job.failed_at is None:
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
            # Phase 7b: legacy 7-value job status vocab retired (the enum was
            # removed); ``failed → pending`` (the legacy retry path)
            # now corresponds to ``done → queued`` on the admission
            # vocabulary. Validating ``(done, queued)`` is on
            # :data:`VALID_TRANSITIONS` (the "replay from done" entry).
            job_state_machine.validate_transition(
                AdmissionState.DONE.value, AdmissionState.QUEUED.value
            )

            # 3. Atomic UPDATE with admission_state + retry_count guards.
            # Phase 4: the legacy default ``from_admission_state='done'``
            # matches the dual-write mirror for ``status='failed'``
            # (the ``fail_job`` helper's output). New Phase 4 callers
            # operating on a freshly-finalized active job (via
            # ``_finalize_terminal(Decision.RETRY)``) go directly
            # ``active → queued`` through the dual-write co-move and
            # don't call this method.
            updated_job = self._job_repo.atomic_retry(
                job_id=job_id,
                max_retries=max_retries,
                next_retry_at=next_retry_at,
            )

            if updated_job is None:
                # The row's admission_state flipped (concurrent DONE or
                # DEAD) between our read and the UPDATE, or
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

            # F12 fix (Phase 3, 2026-07-01): cancel stale PENDING
            # tasks on the retried instance BEFORE the orchestrator
            # calls ``start_job`` to spawn a fresh instance/Task.
            # ``atomic_retry`` above flipped the JobItem back to
            # ``queued``; the downstream caller will then call
            # ``start_job`` to mint a new Task and drive
            # ``graph.astream`` for this instance. A leftover
            # PENDING retry child on the same ``instance_id`` would
            # otherwise survive — ``claim_pending_task``'s
            # per-instance guard blocks only RUNNING tasks, not
            # PENDING ones, so the stale PENDING and the fresh retry
            # Task can both become claimable and contest the same
            # LangGraph checkpoint (``thread_id`` = ``instance_id``
            # in the Postgres checkpointer; two concurrent
            # ``astream`` calls shadow each other's channel writes).
            #
            # Ordering: this cancel MUST happen between
            # ``atomic_retry`` (which unblocks the queue entry) and
            # ``start_job`` (which creates the new Task). Doing the
            # cancel AFTER ``start_job`` is too late — the new Task
            # is already claimable. Doing it BEFORE ``atomic_retry``
            # is also wrong — the stale PENDING is still in the
            # same instance's task table as the previous retry's
            # child, and the cancel would race against
            # ``claim_pending_task``'s read of that row.
            #
            # ``task_repo`` is optional in the constructor (older
            # wirings pre-F12 leave it ``None``); when absent we log
            # a WARNING and skip the cancel — better than crashing,
            # but the F12 invariant is unenforced until the wiring
            # is updated (see ``daemon/api.py``).
            if self._task_repo is not None and updated_job.instance_id:
                try:
                    cancelled_count = self._task_repo.cancel_pending_tasks_for_instance(
                        updated_job.instance_id
                    )
                    if cancelled_count > 0:
                        logger.info(
                            "F12 fix: cancelled %d stale PENDING task(s) "
                            "on instance %s before retry re-admission of job %s",
                            cancelled_count,
                            updated_job.instance_id,
                            job_id,
                        )
                except Exception as e:
                    # Best-effort — log and continue. The retry
                    # transition already committed; a failed
                    # cancel here leaves the stale PENDING Task in
                    # place (the F12 invariant is unenforced for
                    # this one retry), but the JobItem is in a
                    # valid queued state and the next
                    # ``_process_next_job`` tick will resolve the
                    # orphan via the existing recovery paths.
                    logger.warning(
                        "F12 fix: cancel_pending_tasks_for_instance "
                        "failed for instance %s on job %s: %s",
                        updated_job.instance_id,
                        job_id,
                        e,
                    )
            elif self._task_repo is None:
                logger.debug(
                    "JobRetryEngine.maybe_retry: task_repo not wired; "
                    "F12 stale-PENDING cancel is a no-op for job %s",
                    job_id,
                )

            logger.info(
                f"Job {job_id} scheduled for retry (attempt {updated_job.retry_count}), "
                f"next_retry_at={next_retry_at}"
            )

            return updated_job

        # 4. Retries exhausted — move to DLQ.
        # DeadLetterService.move_to_dlq holds a row lock
        # (with_for_update) and re-checks admission_state == 'active'
        # under the lock, so the ACTIVE → DEAD transition is safe
        # against concurrent retries / cancellations. The atomic
        # UPDATE inside atomic_retry above (which also enforces
        # admission_state='active' + retry_count < max_retries) and
        # the lock + admission_state check inside move_to_dlq
        # together cover the full maybe_retry → DLQ path.
        #
        # Phase 4: the ACTIVE → DEAD transition is direct (no
        # DLQ transition: the legacy ``maybe_retry`` path operates on a
        # ``status='failed'`` row (the ``fail_job`` helper's output).
        # Pass ``failed`` as the ``from_admission_state`` so
        # ``move_to_dlq``'s eligibility check (legacy status check)
        # accepts the row.
        try:
            with SQLModelSession(self._job_repo.engine) as session:
                self._dlq_service.move_to_dlq(
                    session,
                    job_id,
                    reason="MAX_RETRIES",
                    from_admission_state="failed",
                )
                session.commit()

            # Re-read after commit to capture the dead-letter state
            # for notification context (error_message, retry_count).
            with SQLModelSession(self._job_repo.engine) as session:
                notified = session.get(JobItem, job_id)
                # Phase 5: ``error_message`` was dropped from the
                # JobItem model in Phase B; use ``getattr`` to stay
                # resilient against rows that still carry the
                # attribute in fixture data. Notification context
                # also falls back to ``retry_count`` (which still
                # lives on JobItem) for the log line below.
                error_msg = (
                    getattr(notified, 'error_message', None) if notified else None
                )

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
