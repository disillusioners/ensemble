"""Startup recovery service for orphaned jobs."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from daemon.repositories.instance.models import InstanceStatus
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
        - If instance alive → leave as PROCESSING (observer will handle)
        
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
            else:
                # Instance is alive — observer will handle
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
        """Mark an orphaned job as FAILED and release its lock.

        Lock release ordering is critical: the status transition runs FIRST
        and the lock is released SECOND (in a ``finally`` block). This
        prevents a double-claim window where the job sits in PROCESSING
        state with no lock to protect it.

        If the transition raises, the job stays in PROCESSING and will be
        picked up by the next recovery cycle. The ``finally`` block
        guarantees the lock is released on ALL code paths (success,
        ``InvalidTransitionError``, and unexpected exceptions).

        Args:
            job: The job to fail.
            error_message: Reason for failure.
            stats: Stats dict to increment on success.

        Returns:
            True if job was successfully transitioned, False if transition was
            skipped (e.g., already transitioned by another actor) or failed.
        """
        try:
            # 1. Transition status FIRST. If this raises, the job remains in
            #    PROCESSING and the finally-block still releases the lock so
            #    a later recovery cycle can retry.
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

            # 2. Notify watchers after successful transition.
            if self._job_queue_service is not None:
                await self._job_queue_service.notify_watchers(
                    job.job_id, "failed", error_message
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
            # 3. Release lock AFTER the transition attempt. This runs on
            #    success, InvalidTransitionError, and unexpected exception
            #    paths, guaranteeing the lock is never leaked.
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
