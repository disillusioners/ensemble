"""Startup recovery service for orphaned jobs."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from daemon.repositories.instance.models import InstanceStatus
from daemon.repositories.job_queue.models import Decision, JobStatus
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
                    await asyncio.to_thread(
                        self._job_repository.atomic_transition,
                        job.job_id,
                        from_status=JobStatus.PROCESSING.value,
                        to_status=JobStatus.PAUSED.value,
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
        status='failed') and the lock release in its finally block
        — guaranteeing the lock is released on every code path
        (success, ``InvalidTransitionError``, unexpected exceptions).

        Pre-fix, this method issued ``atomic_transition(processing
        → failed)`` directly and released the lock in a ``finally``
        block. The structural guarantee is preserved (the lock is
        always released), but the work now goes through the
        boundary so a future recovery code path cannot silently
        bypass retry/DLQ handling.

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
        try:
            if self._job_queue_service is not None:
                # Preferred path: use the boundary on JobQueueService.
                canonical_job_id, _ = await self._job_queue_service._finalize_terminal(
                    instance_id=job.instance_id or "",
                    decision=Decision.NO_RETRY,
                    job_id=job.job_id,
                    error_message=error_message,
                )
                if canonical_job_id is not None:
                    stats["recovered"] += 1
                    if self._job_queue_service is not None:
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
                # No JobQueueService wired (rare — only in tests).
                # Fall back to the legacy direct atomic_transition +
                # lock release. Preserves the pre-fix semantics so
                # tests that construct JobRecoveryService without a
                # JobQueueService keep working.
                now = datetime.now(timezone.utc).isoformat()
                await asyncio.to_thread(
                    self._job_repository.atomic_transition,
                    job.job_id,
                    from_status="processing",
                    to_status="failed",
                    completed_at=now,
                    error_message=error_message,
                )
                stats["recovered"] += 1
                if self._job_queue_service is not None:
                    try:
                        await self._job_queue_service.notify_watchers(
                            job.job_id, "failed", error_message
                        )
                    except Exception as e:
                        logger.warning(
                            f"_fail_orphaned_job (legacy): notify_watchers failed "
                            f"for {job.job_id[:8]}...: {e}"
                        )
                return True
        except InvalidTransitionError:
            # Job was already transitioned by another actor — this is expected.
            logger.info(
                f"Job {job.job_id[:8]}... already transitioned during recovery, skipping"
            )
            return False
        except Exception as e:
            logger.error(f"Failed to recover job {job.job_id[:8]}...: {e}")
            return False
        finally:
            # 3. Release lock AFTER the transition attempt. The
            #    ``_finalize_terminal`` boundary already releases in
            #    its own finally block; this is a defense-in-depth
            #    guarantee for the legacy fallback path (where the
            #    boundary is bypassed). The lock is keyed by
            #    ``instance_id`` (matches the start_job contract).
            if job.instance_id:
                try:
                    await asyncio.to_thread(
                        self._lock_repository.release_by_instance, job.instance_id
                    )
                except Exception as lock_err:
                    # Do not mask the original exception; just log so an
                    # operator can investigate stuck lock rows.
                    logger.error(
                        f"Failed to release lock for instance {job.instance_id} "
                        f"during recovery of job {job.job_id[:8]}...: {lock_err}"
                    )
