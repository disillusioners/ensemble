"""MessageJobHandler - Handles MESSAGE-type jobs from JobQueue."""

import asyncio
import logging

from daemon.cancellation import (
    CancellationTokenSource,
    CancellationReason,
    OperationCancelledError,
)
from daemon.services.job_queue_service import DemandState

logger = logging.getLogger(__name__)


class MessageJobHandler:
    """Handles MESSAGE-type jobs by routing to existing instance's _process_message_with_tracking().

    Key design:
    - Primary concurrency gate is in JobProcessor._process_next_job() BEFORE start_job()
    - This handler has a safety-net check for race conditions
    - Stores active CancellationTokenSource for PROCESSING jobs (enables cancel_message_job)
    - Does NOT spawn instances — MESSAGE jobs target existing running instances
    - Reads instance_id from JobItem.instance_id column (set at enqueue time)
    - Calls _process_message_with_tracking() as-is (no modifications)
    """

    def __init__(self, manager, job_queue_service, job_repository):
        """Initialize the MessageJobHandler.

        Args:
            manager: InstanceManager facade with _process_message_with_tracking method.
            job_queue_service: JobQueueService for completing jobs.
            job_repository: JobRepository for DB queries.
        """
        self._manager = manager
        self._job_service = job_queue_service
        self._job_repo = job_repository
        self._active_tokens: dict[str, CancellationTokenSource] = {}  # job_id → CTS

    async def handle(self, job) -> None:
        """Process a MESSAGE job. Called from JobProcessor after start_job() succeeds.

        Args:
            job: JobItem with job_type="message" and instance_id set in column.
        """
        instance_id = job.instance_id
        if not instance_id:
            await self._job_service.complete_job(
                job.job_id,
                demand_state=DemandState.FAILED,
                error="MESSAGE job missing instance_id",
            )
            return

        # DB-level concurrency gate: check if another MESSAGE is processing for this instance
        active = await asyncio.to_thread(
            self._job_repo.find_processing_message_jobs_by_instance, instance_id
        )
        # Exclude self (we just transitioned to PROCESSING)
        active_other = [j for j in active if j.job_id != job.job_id]
        if active_other:
            # Another MESSAGE job is processing for this instance.
            # Back-transition this job: PROCESSING → PENDING so it's picked up next poll cycle.
            # Do NOT fail it — this is a temporary condition.
            # Use atomic_transition (not update()) for race-safety under concurrency.
            logger.info(
                f"MessageJobHandler: instance {instance_id[:8]}... already has "
                f"MESSAGE job processing, re-queuing {job.job_id[:8]}..."
            )
            result = await asyncio.to_thread(
                self._job_repo.atomic_transition, job.job_id,
                from_status="processing", to_status="pending",
            )
            if result is None:
                # Job was already transitioned by another process — nothing to do
                logger.debug(
                    f"MessageJobHandler: job {job.job_id[:8]}... already transitioned, skipping"
                )
                return
            # Release the per-queue lock acquired by start_job()
            # release_queue_lock takes (project_id, queue_id, job_id)
            if job.project_id and job.queue_id:
                await self._job_service._lock_manager.release_queue_lock(
                    job.project_id, job.queue_id, job.job_id
                )
            return

        # Create CancellationToken for this job
        cts = CancellationTokenSource()
        self._active_tokens[job.job_id] = cts

        try:
            # Extract params from job metadata
            message_id = job.job_metadata.get("message_id") if job.job_metadata else None
            message_source = job.job_metadata.get("source", "api") if job.job_metadata else "api"
            images = job.job_metadata.get("images") if job.job_metadata else None

            # Call the shared processing function — NOT modified
            result = await self._manager._process_message_with_tracking(
                instance_id=instance_id,
                message=job.message,
                message_id=message_id,
                cancellation_token=cts.token,
                is_retry=False,
                retry_count=0,
                message_source=message_source,
                images=images,
            )

            # Check if this instance should transition (completed, waiting_children, etc.)
            if hasattr(self._manager, '_process_child_completion_and_notify_parent'):
                try:
                    await self._manager._process_child_completion_and_notify_parent(
                        instance_id, message_id
                    )
                except Exception as e:
                    logger.error("Completion check failed for job %s: %s", job.job_id[:8], e, exc_info=True)
            else:
                logger.debug("No completion check method for job %s", job.job_id[:8])

            # Mark job complete
            await self._job_service.complete_job(
                job.job_id,
                demand_state=DemandState.COMPLETED,
                result_summary=result.content,
            )

        except OperationCancelledError:
            # Job was cancelled via CancellationToken
            await self._job_service.complete_job(
                job.job_id,
                demand_state=DemandState.CANCELLED,
                error="Message processing cancelled",
            )
        except Exception as e:
            logger.error(
                f"MessageJobHandler: error processing MESSAGE job {job.job_id[:8]}...: {e}"
            )
            await self._job_service.complete_job(
                job.job_id,
                demand_state=DemandState.FAILED,
                error=str(e),
            )
        finally:
            self._active_tokens.pop(job.job_id, None)

    async def cancel_message_job(self, job_id: str) -> None:
        """Cancel a MESSAGE job. Lives on MessageJobHandler, called via JobQueueService.

        PENDING: repository cancel_job() for PENDING→CANCELLED transition.
        PROCESSING: signal CancellationToken, handler completes the job on its own.
        """
        job = await asyncio.to_thread(self._job_repo.get, job_id)
        if job is None:
            return

        if job.status == "pending":
            # PENDING→CANCELLED via repository (complete_job() only handles PROCESSING→terminal)
            await asyncio.to_thread(self._job_repo.cancel_job, job_id)
        elif job.status == "processing":
            # Signal CancellationToken — handler will catch OperationCancelledError
            cts = self._active_tokens.get(job_id)
            if cts:
                cts.cancel(reason=CancellationReason.MANUAL)
            else:
                # Token not found (edge case: handler crashed, token cleaned up)
                # Force-cancel via state transition
                await self._job_service.complete_job(
                    job_id,
                    demand_state=DemandState.CANCELLED,
                    error="Cancelled (force, no active token)",
                )
