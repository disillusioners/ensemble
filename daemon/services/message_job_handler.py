"""MessageJobHandler - Handles MESSAGE-type jobs from JobQueue."""

import asyncio
import logging

from daemon.cancellation import (
    CancellationTokenSource,
    CancellationReason,
    OperationCancelledError,
)
from daemon.manager import MessageResult
from daemon.models.instance import InstanceStatus
from daemon.repositories.execution_lease.models import LeaseHolderKind
from daemon.services.execution_gate import LeaseContention, LeaseLostError
from daemon.services.job_queue_service import DemandState

logger = logging.getLogger(__name__)


class MessageJobHandler:
    """Handles MESSAGE-type jobs by routing to existing instance's _process_message_with_tracking().

    Key design:
    - Concurrency gate is the Execution Gate
      (daemon/services/execution_gate.py). Both this handler and the
      WorkerPool's ProcessMessageProcessor must acquire the per-instance
      lease before calling ``graph.astream``. The two physical
      dispatchers can never both be driving the same langgraph thread.
    - The pre-flight DB check on other MESSAGE jobs (line below) is a
      legacy optimisation that avoids even starting this handler if a
      sibling MESSAGE job is mid-flight. The Gate's acquire is the
      authoritative safety net.
    - Stores active CancellationTokenSource for PROCESSING jobs (enables cancel_message_job)
    - Does NOT spawn instances — MESSAGE jobs target existing running instances
    - Reads instance_id from JobItem.instance_id column (set at enqueue time)
    - Calls _process_message_with_tracking() as-is (no modifications)
    """

    def __init__(self, manager, job_queue_service, job_repository, source_dispatcher=None):
        """Initialize the MessageJobHandler.

        Args:
            manager: InstanceManager facade with _process_message_with_tracking method.
            job_queue_service: JobQueueService for completing jobs.
            job_repository: JobRepository for DB queries.
            source_dispatcher: Optional ResponseDispatcher for external routing.
        """
        self._manager = manager
        self._job_service = job_queue_service
        self._job_repo = job_repository
        self._source_dispatcher = source_dispatcher
        self._active_tokens: dict[str, CancellationTokenSource] = {}  # job_id → CTS

    async def handle(self, job) -> None:
        """Process a MESSAGE job. Called from JobProcessor after start_job() succeeds.

        Args:
            job: JobItem with job_type="message" and instance_id set in column.
        """
        instance_id = job.instance_id

        # [TRACE] Log job entry
        logger.info(
            f"[TRACE] MessageJobHandler.handle: START job={job.job_id[:8]}... "
            f"instance={instance_id[:8]}... job_type={getattr(job, 'job_type', 'unknown')}"
        )

        if not instance_id:
            await self._job_service.complete_job(
                job.job_id,
                demand_state=DemandState.FAILED,
                error="MESSAGE job missing instance_id",
            )
            return

        # Pre-flight: if another MESSAGE job is already processing
        # for this instance, back off without acquiring the lease.
        # This is a fast-path optimisation; the Gate's
        # ``try_acquire`` is the authoritative safety net.
        active = await asyncio.to_thread(
            self._job_repo.find_processing_message_jobs_by_instance, instance_id
        )
        # Exclude self (we just transitioned to PROCESSING)
        active_other = [j for j in active if j.job_id != job.job_id]
        if active_other:
            await self._requeue_for_contention(
                job, f"another MESSAGE job is processing (job_id={active_other[0].job_id})"
            )
            return

        # Cross-dispatcher check: if a WorkerPool task is actively
        # running graph.astream for this instance, defer to it. The
        # task's running row means it's already in the middle of a
        # stream and we should not start a parallel one. We re-queue
        # so the next poll picks up the message after the task
        # completes. (See
        # docs/bugs/child-completion-report-lost-cross-dispatcher-jobqueue-vs-workerpool.md
        # for the bug this prevents.)
        task_repo = getattr(self._manager, "_task_repo", None)
        if task_repo is None:
            # Misconfigured: InstanceManager must expose _task_repo
            # (set in ``setup_worker_pool``) for the cross-dispatcher
            # pre-flight to work. Without it we lose the optimisation
            # but the Gate's ``try_acquire`` is still the
            # authoritative safety net. Log a warning so a future
            # operator notices.
            logger.warning(
                "MessageJobHandler: InstanceManager has no _task_repo; "
                "skipping cross-dispatcher pre-flight. The Execution "
                "Gate's try_acquire is the authoritative safety net."
            )
        else:
            running_task = await asyncio.to_thread(
                task_repo.find_running_by_instance, instance_id
            )
            if running_task is not None:
                await self._requeue_for_contention(
                    job, f"a task is RUNNING for this instance (task_id={running_task.id})"
                )
                return

        # Create CancellationToken for this job
        cts = CancellationTokenSource()
        self._active_tokens[job.job_id] = cts

        # [TRACE] Log before processing
        logger.info(
            f"[TRACE] MessageJobHandler: calling gate.run for "
            f"job={job.job_id[:8]}... instance={instance_id[:8]}..."
        )

        # Extract params from job metadata BEFORE the gate call so the
        # closure doesn't need to reach into the job object.
        message_id = job.job_metadata.get("message_id") if job.job_metadata else None
        message_source = job.job_metadata.get("source", "api") if job.job_metadata else "api"
        images = job.job_metadata.get("images") if job.job_metadata else None
        resume_mode = job.job_metadata.get("resume_mode", False) if job.job_metadata else False
        silent = job.job_metadata.get("silent", False) if job.job_metadata else False

        async def _do_process() -> "MessageResult":
            return await self._manager._process_message_with_tracking(
                instance_id=instance_id,
                message=job.message,
                message_id=message_id,
                cancellation_token=cts.token,
                is_retry=resume_mode,
                retry_count=0,
                message_source=message_source,
                images=images,
                silent=silent,
            )

        # Result of the gate call, or ``None`` if the gate raised.
        # We catch the gate's exceptions into local variables
        # rather than ``except`` blocks, so the original
        # ``except OperationCancelledError / asyncio.CancelledError
        # / Exception`` clauses below (which handle pause-vs-terminate
        # discrimination and FAILED-state reporting) see the
        # exception and run unchanged.
        #
        # TODO(refactor): the dual-variable control flow
        # (``gate_outcome`` / ``gate_raised``) is harder to read than
        # a single ``try / except`` around ``gate.run`` that
        # translates to the right terminal action. Preserved here
        # to keep the existing tested ``except`` clauses
        # untouched. Follow-up: refactor into a small state machine.
        gate_outcome: "MessageResult | LeaseContention | None" = None
        gate_raised: BaseException | None = None

        try:
            gate_outcome = await self._manager.execution_gate.run(
                instance_id=instance_id,
                holder_id=f"message_job:{job.job_id}",
                holder_kind=LeaseHolderKind.MESSAGE_JOB.value,
                work_fn=_do_process,
            )
        except BaseException as e:  # noqa: BLE001 - we re-raise below after body cleanup
            # Stash the exception; the post-processing below
            # checks ``gate_raised`` and translates to the right
            # ``complete_job`` call. We re-raise at the end of
            # the method so the original pause/terminate
            # discrimination in the except clauses still works.
            gate_raised = e

        # Handle LeaseContention: the lease was held by a sibling
        # dispatcher (e.g. a Task claimed this instance between
        # our pre-flight and the gate acquire). Re-queue the job.
        if gate_raised is None and isinstance(gate_outcome, LeaseContention):
            holder_summary = (
                f"holder_id={gate_outcome.holder_id} "
                f"holder_kind={gate_outcome.holder_kind}"
            )
            await self._requeue_for_contention(job, holder_summary)
            # Clean up the CTS we stored — we never used it.
            self._active_tokens.pop(job.job_id, None)
            return

        # Handle LeaseLostError: the gate detected (via the
        # in-flight heartbeat) that our lease row was cleared by
        # another process (most likely ``recover_stale_leases`` on a
        # different node). The in-flight ``work_fn`` was cancelled
        # by the gate before this point. Treat as transient: the
        # job should re-queue and retry — the next attempt will
        # acquire the lease fresh.
        if isinstance(gate_raised, LeaseLostError):
            logger.warning(
                f"MessageJobHandler: lease lost mid-execution for "
                f"job={job.job_id[:8]}... instance={instance_id[:8]}... "
                f"— re-queuing (another process cleared the lease)"
            )
            await self._requeue_for_contention(
                job, f"lease lost mid-execution: {gate_raised}"
            )
            self._active_tokens.pop(job.job_id, None)
            return

        result = gate_outcome  # may be None if gate_raised; checked below

        try:
            if gate_raised is None:
                # Happy path / normal processing.
                # Mark message as completed so pending_count queries don't keep counting it
                if (message_id
                        and hasattr(self._manager, '_queue_repository')
                        and self._manager._queue_repository):
                    try:
                        await asyncio.to_thread(
                            self._manager._queue_repository.complete, message_id
                        )
                    except Exception as e:
                        logger.warning(
                            f"Failed to mark message {message_id} as completed: {e}"
                        )

                # Dispatch completed message to external sources (Telegram, Discord, etc.)
                # For internal messages (completion reports, etc.), use the original external source
                dispatch_source = message_source
                is_internal_report = (
                    message_source.startswith("internal_report:")
                    or message_source.startswith("internal_error_report:")
                    or message_source.startswith("internal_agent:job_event:")
                )
                if is_internal_report:
                    # Retrieve original external source from instance metadata
                    instance_meta = await asyncio.to_thread(self._manager._instance_repository.get, instance_id)
                    # Use is not None check because empty dict {} is falsy
                    if instance_meta is not None and instance_meta.instance_metadata is not None:
                        dispatch_source = instance_meta.instance_metadata.get("original_source")
                    # Skip dispatch if no valid external source found (None, empty, or still internal)
                    if not dispatch_source or dispatch_source.startswith("internal_"):
                        logger.debug(
                            f"No original_source found for instance {instance_id[:8]}... "
                            f"(message_source={message_source})"
                        )
                        dispatch_source = None  # Skip dispatch if no original source

                if self._source_dispatcher and dispatch_source and result:
                    try:
                        await self._source_dispatcher.dispatch_completed(
                            instance_id=instance_id,
                            message_id=message_id,
                            source=dispatch_source,
                            content=result.content or "",
                            message_type="final",
                        )
                    except Exception as e:
                        logger.error(f"Error dispatching to external source: {e}", exc_info=True)
                        # Don't fail the task - dispatch is best-effort

                # Check if this instance is a child that has completed all work.
                # This may create a completion report task for the parent.
                try:
                    if hasattr(self._manager, '_process_child_completion_and_notify_parent'):
                        await self._manager._process_child_completion_and_notify_parent(
                            instance_id, message_id
                        )
                except Exception as e:
                    logger.error(
                        f"Completion check failed for job {job.job_id[:8]}...: {e}",
                        exc_info=True,
                    )
                    # Don't fail the task — the message was processed successfully

                # Check if this instance should transition (completed, waiting_children, etc.)
                # If instance is WAITING_CHILDREN, JobFeedbackObserver will complete the job
                # when all children finish and instance transitions to completed.
                skip_complete = False
                try:
                    instance = await asyncio.to_thread(
                        self._manager._instance_repository.get, instance_id
                    )
                    if instance and instance.status == InstanceStatus.WAITING_CHILDREN.value:
                        logger.info(
                            f"MessageJobHandler: instance {instance_id[:8]}... is WAITING_CHILDREN, "
                            f"deferring job completion for {job.job_id[:8]}..."
                        )
                        skip_complete = True
                    elif instance and (instance.waiting_for or 0) > 0:
                        logger.info(
                            f"MessageJobHandler: instance {instance_id[:8]}... has "
                            f"waiting_for={instance.waiting_for} (status={instance.status}), "
                            f"deferring completion for job {job.job_id[:8]}..."
                        )
                        skip_complete = True
                except Exception as e:
                    logger.warning(
                        f"MessageJobHandler: failed to check instance status for {instance_id[:8]}..., "
                        f"proceeding with job completion: {e}"
                    )

                if skip_complete:
                    # Emit in_progress notification so watchers know the instance finished
                    # its turn but child agents are still pending
                    wf = (instance.waiting_for or 0) if instance else 0
                    if wf > 0:
                        try:
                            await self._job_service.notify_watchers(
                                job.job_id,
                                status="in_progress",
                                progress=result.content if result else None,
                                waiting_for=wf,
                            )
                        except Exception as e:
                            logger.warning(f"MessageJobHandler: failed to emit in_progress notification: {e}")
                        return
                    # If wf == 0, fall through to normal completion handling below

                # Mark job complete
                await self._job_service.complete_job(
                    job.job_id,
                    demand_state=DemandState.COMPLETED,
                    result_summary=result.content,
                )
            else:
                # Gate raised an exception. Re-raise inside the
                # try so the matching except clause below
                # handles it (pause vs terminate discrimination,
                # FAILED-state for other exceptions, etc.).
                raise gate_raised

        except OperationCancelledError:
            # Job was cancelled via CancellationToken
            await self._job_service.complete_job(
                job.job_id,
                demand_state=DemandState.CANCELLED,
                error="Message processing cancelled",
            )
        except asyncio.CancelledError:
            instance = await asyncio.to_thread(self._manager._instance_repository.get, instance_id)
            if instance and instance.status == InstanceStatus.PAUSED.value:
                # PAUSE → leave PROCESSING for resume
                return
            else:
                # NOT PAUSE (terminate, shutdown, etc.) → complete job as CANCELLED
                try:
                    await self._job_service.complete_job(
                        job.job_id,
                        demand_state=DemandState.CANCELLED,
                        error="Message processing cancelled (instance terminated)",
                    )
                except Exception:
                    logger.warning(f"MessageJobHandler: failed to cancel job {job.job_id[:8]}...")
                logger.info(f"MessageJobHandler: job {job.job_id[:8]}... cancelled (not pause, status={instance.status if instance else 'unknown'})")
                raise
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

    async def _requeue_for_contention(self, job, reason: str) -> None:
        """Back-transition a MESSAGE job PROCESSING→PENDING and release
        the per-queue lock so the next poll picks it up.

        Used both for the legacy "another MESSAGE job is processing"
        case and the new "lease held by Task (cross-dispatcher)" case.
        Same atomic_transition+release pattern in both — the only
        difference is the ``reason`` string we log for diagnostics.

        Also wakes the JobProcessor dispatch bus so the next poll
        cycle runs immediately rather than waiting up to
        ``_poll_interval`` (default 30 s). Without this notification,
        a hot instance receiving frequent child reports would see
        ``_poll_interval`` of additional latency on every
        cross-dispatcher back-off, because the job simply sits in
        PENDING until the next scheduled poll.
        """
        logger.info(
            f"[TRACE] MessageJobHandler.handle: SKIP job {job.job_id[:8]}... — "
            f"instance {job.instance_id[:8]}... re-queuing ({reason})"
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
        if job.project_id and job.queue_id:
            await self._job_service._lock_manager.release_queue_lock(
                job.project_id, job.queue_id, job.job_id
            )
        # Wake the JobProcessor dispatch bus so the freshly-requeued
        # PENDING job is picked up on the next poll cycle, not at the
        # end of the current ``_poll_interval`` (default 30 s). The
        # bus is best-effort: if it's not wired in (e.g. test
        # fixture) the periodic poll still drains the job.
        bus = getattr(self._job_service, "_dispatch_bus", None)
        if bus is not None and job.project_id is not None:
            try:
                bus.notify_new_job(job.project_id)
            except Exception as e:
                logger.debug(
                    f"MessageJobHandler: dispatch bus notify failed "
                    f"(non-fatal): {e}"
                )

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
