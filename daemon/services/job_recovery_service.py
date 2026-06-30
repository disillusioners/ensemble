"""Startup recovery service for orphaned jobs."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from daemon.repositories.instance.models import InstanceStatus
from daemon.repositories.job_queue.models import Decision
from daemon.services.job_state_machine import InvalidTransitionError

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

    from daemon.repositories.instance.repository import SQLModelInstanceRepository
    from daemon.repositories.job_queue.lock_repository import LockRepository
    from daemon.repositories.job_queue.models import JobItem
    from daemon.repositories.job_queue.repository import JobRepository
    from daemon.services.job_queue_service import JobQueueService

logger = logging.getLogger(__name__)

# Terminal instance statuses - instance is no longer active
_TERMINAL_INSTANCE_STATUSES: set[str] = {
    InstanceStatus.COMPLETED.value,
    InstanceStatus.ERROR.value,
    InstanceStatus.TERMINATED.value,
    InstanceStatus.FAILED.value,
}

# Alive instance statuses - instance is still running
_ALIVE_INSTANCE_STATUSES: set[str] = {
    InstanceStatus.IDLE.value,
    InstanceStatus.RUNNING.value,
    InstanceStatus.PAUSED.value,
    InstanceStatus.QUEUED.value,
    InstanceStatus.WAITING_CHILDREN.value,
}


class JobRecoveryService:
    """Service for recovering orphaned jobs on startup.
    
    Handles the case where jobs were left in PROCESSING state after
    a crash or unexpected shutdown, ensuring they are either
    re-assigned to a running instance or marked as FAILED.
    """

    def __init__(
        self,
        job_repository: "JobRepository",
        lock_repository: "LockRepository",
        instance_repository: "SQLModelInstanceRepository",
        job_queue_service: "JobQueueService | None" = None,
    ) -> None:
        """Initialize the recovery service.
        
        Args:
            job_repository: Repository for job operations.
            lock_repository: Repository for lock operations.
            instance_repository: Repository for instance operations.
            job_queue_service: Optional JobQueueService for watcher notifications.
        """
        self._job_repository = job_repository
        self._lock_repository = lock_repository
        self._instance_repository = instance_repository
        self._job_queue_service = job_queue_service

    def _is_instance_alive(self, instance_status: str | None) -> bool:
        """Check if an instance status indicates the instance is still alive.
        
        Args:
            instance_status: The instance status string.
            
        Returns:
            True if the instance is considered alive.
        """
        if instance_status is None:
            return False
        return instance_status in _ALIVE_INSTANCE_STATUSES

    def _is_instance_terminal(self, instance_status: str | None) -> bool:
        """Check if an instance status indicates a terminal state.
        
        Args:
            instance_status: The instance status string.
            
        Returns:
            True if the instance is in a terminal state.
        """
        if instance_status is None:
            return False
        return instance_status in _TERMINAL_INSTANCE_STATUSES

    async def recover_on_startup(self) -> dict:
        """Recover orphaned PROCESSING jobs on startup.

        Called once at daemon startup to handle jobs that were PROCESSING
        when the daemon crashed or was killed.

        For each PROCESSING job:
        - Check if its instance is still alive
        - If instance not found or terminal → mark job FAILED, release lock
        - If instance PAUSED → reconcile job PROCESSING → PAUSED (C2 fix)
        - If instance alive (RUNNING, IDLE, etc.) → leave as PROCESSING (observer handles)

        Returns:
            Dict with recovery stats: {"recovered": int, "alive": int, "total": int}
        """
        logger.info("Starting job recovery — checking PROCESSING jobs...")

        processing_jobs = await asyncio.to_thread(self._job_repository.find_processing_jobs)

        stats = {"recovered": 0, "alive": 0, "total": len(processing_jobs)}

        for job in processing_jobs:
            if not job.instance_id:
                # Job has no instance — orphaned, mark as failed
                logger.warning(f"Job {job.job_id[:8]}... has no instance_id, marking FAILED")
                await self._fail_orphaned_job(job, "Recovered: no instance assigned", stats)
                continue

            # Check instance liveness
            instance = await asyncio.to_thread(self._instance_repository.get, job.instance_id)

            if instance is None:
                # Instance not found — orphaned
                logger.warning(
                    f"Job {job.job_id[:8]}... instance {job.instance_id[:8]}... not found, marking FAILED"
                )
                await self._fail_orphaned_job(job, "Recovered: instance no longer exists", stats)
            elif instance.status in (
                InstanceStatus.COMPLETED.value,
                InstanceStatus.TERMINATED.value,
                InstanceStatus.ERROR.value,
                InstanceStatus.FAILED.value,
            ):
                # Instance is terminal — job is orphaned
                logger.warning(
                    f"Job {job.job_id[:8]}... instance {job.instance_id[:8]}... "
                    f"is terminal ({instance.status}), marking FAILED"
                )
                await self._fail_orphaned_job(job, f"Recovered: instance is {instance.status}", stats)
            elif instance.status == InstanceStatus.PAUSED.value:
                # C2 fix (Phase 6): instance is PAUSED but job is still PROCESSING.
                # This state arises from (a) the pre-Phase-2 hack where pause did
                # not touch jobs, or (b) a crash during the pause transition window
                # (after instance → PAUSED but before job → PAUSED committed).
                # Reconcile by transitioning the job to PAUSED so its status
                # matches the instance. The (PROCESSING, PAUSED) "pause" entry
                # is in the TRANSITIONS dict (Phase 1).
                logger.info(
                    f"Job {job.job_id[:8]}... instance {job.instance_id[:8]}... "
                    f"is PAUSED — reconciling job PROCESSING → PAUSED"
                )
                try:
                    # Phase 7b: under admission_state, PROCESSING→PAUSED
                    # maps to (active→active) — a same-state no-op. The
                    # state machine's ``can_transition`` treats
                    # ``from == to`` as implicitly valid (see
                    # ``job_state_machine.py``). Pause is an Instance-side
                    # concern; the job stays ``active`` in admission_state.
                    await asyncio.to_thread(
                        self._job_repository.atomic_transition,
                        job.job_id,
                        from_status="processing",
                        to_status="paused",
                    )
                    stats["recovered"] += 1
                except InvalidTransitionError:
                    # Job was already transitioned by another actor — expected
                    # during concurrent recovery (e.g., another node). The job
                    # is no longer PROCESSING so we leave it alone.
                    logger.debug(
                        f"Job {job.job_id[:8]}... already transitioned during "
                        f"PAUSED recovery, skipping"
                    )
            else:
                # Instance is truly alive (RUNNING, IDLE, QUEUED, WAITING_CHILDREN)
                # — leave as PROCESSING, the observer will resume pickup.
                logger.info(
                    f"Job {job.job_id[:8]}... instance {job.instance_id[:8]}... "
                    f"is alive ({instance.status}), leaving as PROCESSING"
                )
                stats["alive"] += 1

        logger.info(
            f"Job recovery complete: {stats['recovered']} recovered, "
            f"{stats['alive']} alive, {stats['total']} total"
        )
        return stats

    async def _fail_orphaned_job(
        self, job: "JobItem", error_message: str, stats: dict[str, int]
    ) -> bool:
        """Mark an orphaned job as failed and release its lock.

        Phase 4 (Job as Queue Proxy): routes through the single
        terminal-write boundary ``JobQueueService._finalize_terminal``
        with ``Decision.NO_RETRY``. The boundary handles the
        ``active → done`` write (admission_state='done',
        status='failed') AND the scoped per-job lock release in its
        finally block — guaranteeing the lock is released on every
        code path (success, ``InvalidTransitionError``, unexpected
        exceptions) WITHOUT touching sibling locks.

        C1 fix (Phase 2 follow-up): the previous implementation
        additionally called ``release_by_instance(job.instance_id)``
        in an outer ``finally`` block, unconditionally wiping ALL
        locks for the instance — the F4/F7 sibling-lock-deletion
        bug, reintroduced in the recovery path post-92cb026a. That
        outer block is removed. The legacy fallback branch (no
        ``_job_queue_service`` wired) now does a scoped
        ``release_by_job(project_id, queue_id, job_id)`` itself so
        the structural invariant — "lock for this job is released
        on success" — still holds in the test-only path.

        Pre-fix, this method issued ``atomic_transition(processing
        → failed)`` directly and released the lock in a ``finally``
        block. The structural guarantee is preserved (the lock is
        always released on success), but the work now goes through
        the boundary so a future recovery code path cannot silently
        bypass retry/DLQ handling or the per-job lock-scoping rule.

        Args:
            job: The job to fail.
            error_message: Reason for failure.
            stats: Stats dict to increment on success.

        Returns:
            True if job was successfully transitioned, False if transition was
            skipped (e.g., already transitioned by another actor) or failed.
        """
        # Phase 4: route through the single terminal-write boundary.
        # The recovery path never retries (the job's instance is
        # gone/terminal, so retrying would loop on the same dead
        # instance) — NO_RETRY is correct.
        #
        # Phase 7c: ``target_status='failed'`` is passed so the
        # boundary writes ``terminal_reason='failed'`` (via the
        # ``_derive_terminal_reason`` mapping on ``target_status``).
        # Without the override the boundary would derive the status
        # from a now-missing/terminal Instance, which can land on
        # ``'cancelled'`` (TERMINATED Instance) — wrong, the
        # recovery path is failing an orphan, not cancelling it.
        try:
            if self._job_queue_service is not None:
                # Preferred path: use the boundary on JobQueueService.
                # The boundary releases the lock scoped to this job
                # (release_by_job) in its own finally block — we must
                # NOT release here or we'd double-release / wipe sibling
                # locks. C1 fix removes the prior outer finally block.
                canonical_job_id, _ = await self._job_queue_service._finalize_terminal(
                    instance_id=job.instance_id or "",
                    decision=Decision.NO_RETRY,
                    job_id=job.job_id,
                    error_message=error_message,
                    target_status="failed",
                )
                if canonical_job_id is not None:
                    stats["recovered"] += 1
                    try:
                        await self._job_queue_service.notify_watchers(
                            job.job_id, "failed", error_message
                        )
                    except Exception as e:
                        logger.warning(
                            f"_fail_orphaned_job: notify_watchers failed "
                            f"for {job.job_id[:8]}...: {e}"
                        )
                    return True
                # Boundary returned None — the job was not in
                # admission_state='active' (already transitioned by
                # another actor). Fall through to the InvalidTransition
                # handling below.
                logger.debug(
                    f"_fail_orphaned_job: _finalize_terminal no-op for "
                    f"job {job.job_id[:8]}... (already transitioned)"
                )
                return False
            else:
                # Legacy fallback (rare — only in tests that construct
                # JobRecoveryService without a JobQueueService). The
                # legacy path does NOT route through ``_finalize_terminal``
                # so it must release the lock itself — C1 fix uses the
                # SCOPED ``release_by_job(project_id, queue_id, job_id)``
                # to honor the F4/F7 invariant. ``release_by_instance``
                # here would wipe sibling locks (different jobs on the
                # same instance) — that is the exact bug this fix
                # removes from the main path.
                now = datetime.now(timezone.utc).isoformat()
                await asyncio.to_thread(
                    self._job_repository.atomic_transition,
                    job.job_id,
                    from_status="processing",
                    to_status="failed",
                    completed_at=now,
                    error_message=error_message,
                )
                # C1 fix: scoped per-job lock release (F4/F7). Only
                # attempt release when we have all three key parts;
                # otherwise the lock row cannot be matched and the
                # call is a safe no-op.
                if job.project_id and job.queue_id and job.job_id:
                    try:
                        await asyncio.to_thread(
                            self._lock_repository.release_by_job,
                            job.project_id,
                            job.queue_id,
                            job.job_id,
                        )
                    except Exception as lock_err:
                        # Lock release failure must not mask the
                        # successful transition — log and continue.
                        logger.error(
                            f"_fail_orphaned_job (legacy): failed to "
                            f"release lock for job {job.job_id[:8]}...: "
                            f"{lock_err}"
                        )
                stats["recovered"] += 1
                return True
        except InvalidTransitionError:
            # Job was already transitioned by another actor — this is expected.
            # No lock release here: the actor that transitioned the job
            # already released its lock (per F4/F7 contract). Re-releasing
            # would be either a no-op or, with the buggy
            # ``release_by_instance``, a sibling-lock wipe.
            logger.info(
                f"Job {job.job_id[:8]}... already transitioned during recovery, skipping"
            )
            return False
        except Exception as e:
            logger.error(f"Failed to recover job {job.job_id[:8]}...: {e}")
            return False
        # C1 fix: REMOVED the outer ``finally`` block that called
        # ``release_by_instance(job.instance_id)``. That unconditional
        # call was reintroducing the F4/F7 sibling-lock-deletion bug
        # in the recovery path post-92cb026a:
        # - Main path (``_job_queue_service`` wired) already releases
        #   the lock scoped to this job inside ``_finalize_terminal``.
        # - Legacy path (``_job_queue_service is None``) now releases
        #   the lock scoped to this job above, before returning.
        # There is no path through this method that still needs an
        # instance-wide lock release — and any such path would be the
        # exact bug we are fixing.
