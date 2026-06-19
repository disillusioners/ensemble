"""MessageJobHandler - Handles MESSAGE-type jobs from JobQueue.

Phase 5 of the CorrelationManager migration: the six shared stages
(acquire-lease, mark-message-completed, dispatch, child-completion
check, error reporting, contention/cancellation handling) are
delegated to :class:`MessageProcessingPipeline`. Path-specific
behaviour (JQ-specific requeue, the CM-aware deferral check in
``on_success``, pause-vs-terminate discrimination in ``on_cancel``)
stays in this handler as :class:`PipelineCallbacks`.
"""

import asyncio
import logging
from typing import TYPE_CHECKING

from daemon.cancellation import (
    CancellationTokenSource,
    CancellationReason,
    OperationCancelledError,
)
from daemon.repositories.instance.models import InstanceStatus
from daemon.repositories.execution_lease.models import LeaseHolderKind
from daemon.services.execution_gate import LeaseContention, LeaseLostError
from daemon.services.job_queue_service import DemandState
from daemon.services.message_processing_errors import (
    handle_message_processing_error,
)
from daemon.services.message_processing_pipeline import (
    MessageProcessingPipeline,
    PipelineCallbacks,
    ProcessingContext,
    ProcessingResult,
)

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

    def __init__(self, manager, job_queue_service, job_repository, source_dispatcher=None, pipeline=None):
        """Initialize the MessageJobHandler.

        Args:
            manager: InstanceManager facade with _process_message_with_tracking method.
            job_queue_service: JobQueueService for completing jobs.
            job_repository: JobRepository for DB queries.
            source_dispatcher: Optional ResponseDispatcher for external routing.
            pipeline: Optional pre-built :class:`MessageProcessingPipeline`.
                When ``None`` (default) the handler constructs one from
                ``manager.execution_gate``, ``manager``, ``source_dispatcher``,
                and ``manager._queue_repository``. Test/extension code may
                inject a custom pipeline.
        """
        self._manager = manager
        self._job_service = job_queue_service
        self._job_repo = job_repository
        self._source_dispatcher = source_dispatcher
        self._active_tokens: dict[str, CancellationTokenSource] = {}  # job_id → CTS
        # Expose the job_queue_service on the manager so the shared
        # error helper (``handle_message_processing_error``) can find
        # it via ``instance_manager._job_queue_service``. This mirrors
        # how ``_events_service`` is always set on the InstanceManager
        # in ``manager.py`` — every "facade-managed service" lives on
        # the manager so per-call helpers have a single lookup point.
        #
        # We force-set unconditionally because the real InstanceManager
        # initialises ``_job_queue_service`` to ``None`` (line 688 in
        # ``manager.py``) and assigns the real service later via
        # ``set_job_queue_service``. Setting it here is therefore safe
        # in both the real path (the real service wins) and the test
        # path (mock managers get the test mock). Tests that pass
        # ``MagicMock()`` as the manager also benefit — the
        # auto-generated ``_job_queue_service`` attribute that
        # ``MagicMock`` would otherwise return is a non-``AsyncMock``
        # that breaks ``await …complete_job(...)`` inside the helper.
        manager._job_queue_service = job_queue_service

        if pipeline is None:
            # Lazy: don't build the pipeline here because some
            # callers (notably tests) wire ``manager.execution_gate``
            # onto the manager AFTER ``__init__``. Building the
            # pipeline here would capture the unwired
            # ``MagicMock``/``None`` and never see the real gate.
            # ``handle()`` constructs the pipeline on first use when
            # the manager's attributes are guaranteed to be wired.
            self._pipeline: MessageProcessingPipeline | None = None
        else:
            self._pipeline = pipeline

    async def handle(self, job) -> None:
        """Process a MESSAGE job. Called from JobProcessor after start_job() succeeds.

        Phase 5 refactor: pre-pipeline work (JQ-specific pre-flights,
        CTS creation, pre-pickup status transition, metadata extraction)
        stays in this method. The six shared stages (lease acquisition,
        mark-message-completed, dispatch, child-completion check, error
        reporting, contention/cancellation handling) are delegated to
        :class:`MessageProcessingPipeline`. JQ-specific behaviour at
        the pipeline boundaries (CM-aware deferral, JQ requeue,
        pause-vs-terminate discrimination) is supplied via
        :class:`PipelineCallbacks`.

        Args:
            job: JobItem with job_type="message" and instance_id set in column.
        """
        instance_id = job.instance_id

        # [TRACE] Log job entry
        logger.info(
            f"[TRACE] MessageJobHandler.handle: START job={job.job_id[:8]}... "
            f"instance={instance_id[:8]}... job_type={getattr(job, 'job_type', 'unknown')}"
        )

        # ------------------------------------------------------------------
        # JQ-SPECIFIC PRE-FLIGHT: identifier validation
        # ------------------------------------------------------------------
        if not instance_id:
            await self._job_service.complete_job(
                job.job_id,
                demand_state=DemandState.FAILED,
                error="MESSAGE job missing instance_id",
            )
            return

        # ------------------------------------------------------------------
        # JQ-SPECIFIC PRE-FLIGHT: sibling MESSAGE check
        # ------------------------------------------------------------------
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

        # ------------------------------------------------------------------
        # JQ-SPECIFIC PRE-FLIGHT: cross-dispatcher (running task) check
        # ------------------------------------------------------------------
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

        # ------------------------------------------------------------------
        # JQ-SPECIFIC: CTS creation (cancellation ownership)
        # ------------------------------------------------------------------
        # Create CancellationToken for this job. Unlike WorkerPool
        # (which receives the token from the caller), JobQueue creates
        # a CTS internally and threads ``cts.token`` through the
        # pipeline via ``ProcessingContext.cancellation_token``.
        cts = CancellationTokenSource()
        self._active_tokens[job.job_id] = cts

        # ------------------------------------------------------------------
        # JQ-SPECIFIC: pre-pickup status transition
        # ------------------------------------------------------------------
        # Transition the instance to RUNNING before invoking the gate so
        # observers (live_hub, JobFeedbackObserver) see a status_change
        # event. The simple-agent self-continuation case relies on
        # WAITING_CHILDREN → RUNNING being observable: after the previous
        # turn completed with pending_count > 0, status is set to
        # WAITING_CHILDREN. When this handler picks up the queued message
        # we must flip back to RUNNING so the UI/observability is
        # consistent with the work that is about to happen. This makes
        # the liveness of the self-continuation path explicit rather than
        # implicit (see docs/bugs/root-instance-premature-completion-on-pending-message.md
        # Finding 5.3).
        #
        # Use a conditional UPDATE so we do not clobber a concurrent
        # ERROR/PAUSED/TERMINATED set by another path between the call
        # site and the DB write. ``transition_status_if`` is a single
        # atomic statement: if the row is missing or its current status
        # is not in ``allowed_from``, it returns ``None`` and we skip
        # the SSE emit. This is the safer replacement for the original
        # read-then-unconditional-update pattern (which had a TOCTOU
        # window where a concurrent ``error_reporting`` write could be
        # overwritten).
        try:
            updated = await asyncio.to_thread(
                self._manager._instance_repository.transition_status_if,
                instance_id,
                InstanceStatus.RUNNING.value,
                (InstanceStatus.WAITING_CHILDREN.value, InstanceStatus.IDLE.value),
            )
            if updated is not None and self._manager._live_hub:
                try:
                    await self._manager._live_hub.stream_status_change(
                        instance_id, InstanceStatus.RUNNING.value, agent_id=updated.agent_id
                    )
                except Exception as e:
                    logger.warning(
                        f"MessageJobHandler: failed to emit status_change → running: {e}"
                    )
        except Exception as e:
            logger.warning(
                f"MessageJobHandler: pre-pickup status transition to RUNNING "
                f"failed for {instance_id[:8]}... (non-fatal): {e}"
            )

        # [TRACE] Log before processing
        logger.info(
            f"[TRACE] MessageJobHandler: calling gate.run for "
            f"job={job.job_id[:8]}... instance={instance_id[:8]}..."
        )

        # ------------------------------------------------------------------
        # Extract params from job metadata for the ProcessingContext
        # ------------------------------------------------------------------
        message_id = job.job_metadata.get("message_id") if job.job_metadata else None
        message_source = job.job_metadata.get("source", "api") if job.job_metadata else "api"
        images = job.job_metadata.get("images") if job.job_metadata else None
        resume_mode = job.job_metadata.get("resume_mode", False) if job.job_metadata else False
        silent = job.job_metadata.get("silent", False) if job.job_metadata else False
        # Fix Phase-0 Bug #2: retry_count was hardcoded to 0 here, which
        # meant retried MESSAGE jobs always looked like first attempts
        # to ``_process_message_with_tracking`` (losing the is_retry
        # signal). Read from job_metadata (preferred — matches the
        # pattern used for message_id/source/images above) with a
        # defensive fallback to the model field for jobs that recorded
        # retries on the row rather than in metadata.
        retry_count = 0
        if job.job_metadata:
            metadata_retry = job.job_metadata.get("retry_count")
            if isinstance(metadata_retry, int) and metadata_retry >= 0:
                retry_count = metadata_retry
        if retry_count == 0 and getattr(job, "retry_count", 0):
            retry_count = job.retry_count

        # ------------------------------------------------------------------
        # Build pipeline input + path-specific callbacks
        # ------------------------------------------------------------------
        context = ProcessingContext(
            instance_id=instance_id,
            message_id=message_id,
            message=job.message,
            retry_count=retry_count,
            message_source=message_source,
            silent=silent,
            images=images,
            resume_mode=resume_mode,
            cancellation_token=cts.token,
        )
        # ------------------------------------------------------------------
        # Build the pipeline lazily (deferred from ``__init__``)
        # ------------------------------------------------------------------
        # Some callers wire ``manager.execution_gate`` AFTER
        # ``__init__`` (notably the existing test suite). Building
        # the pipeline here — on first use, when the manager's
        # attributes are guaranteed to be wired — captures the real
        # gate. Subsequent ``handle()`` calls reuse the cached
        # pipeline instance.
        pipeline = self._pipeline
        if pipeline is None:
            queue_repository = getattr(self._manager, "_queue_repository", None)
            pipeline = MessageProcessingPipeline(
                execution_gate=self._manager.execution_gate,
                manager=self._manager,
                source_dispatcher=self._source_dispatcher,
                queue_repository=queue_repository,
            )
            self._pipeline = pipeline

        # ------------------------------------------------------------------
        # Build path-specific callbacks
        # ------------------------------------------------------------------
        # ``on_cancel`` is intentionally left as ``None``: the
        # pipeline only invokes it for errors from stages 3-6
        # (post-processing). Stage-2 errors (gate.run / work_fn)
        # propagate out of ``execute()`` and are handled by the
        # outer try/except below — the same approach the WP path
        # uses (``ProcessMessageProcessor.process``).
        callbacks = self._build_callbacks(job, instance_id, message_id)

        # ------------------------------------------------------------------
        # Run the pipeline (the six shared stages) + outer error handling
        # ------------------------------------------------------------------
        # The pipeline handles: gate.run, mark-message-completed,
        # dispatch, child-completion check, error reporting, and
        # contention boundary handling. JQ-specific behaviour fires
        # via callbacks (``on_success``, ``on_contention``,
        # ``on_error``). The ``error_handler_id`` carries ``job_id``
        # so ``handle_message_processing_error`` can mark the job
        # FAILED for stage 3-6 errors.
        #
        # The outer try/except mirrors the original JQ handler's
        # behaviour for errors that the pipeline does NOT catch
        # (stage-2 errors: ``OperationCancelledError``,
        # ``asyncio.CancelledError``, and generic ``Exception``
        # raised by work_fn / gate.run).
        try:
            await pipeline.execute(
                context=context,
                holder_id=f"message_job:{job.job_id}",
                holder_kind=LeaseHolderKind.MESSAGE_JOB.value,
                callbacks=callbacks,
                error_handler_id={"job_id": job.job_id},
            )
        except OperationCancelledError:
            # Job was cancelled via CancellationToken (manual
            # cancel via ``cancel_message_job``). Token cancellation
            # fires from stage 2 (work_fn checks the token) and
            # propagates out of ``execute()`` because the pipeline
            # only catches ``OperationCancelledError`` from stages
            # 3-6. Always complete as CANCELLED — no pause
            # discrimination, mirroring the original distinct
            # ``except OperationCancelledError`` clause.
            try:
                await self._job_service.complete_job(
                    job.job_id,
                    demand_state=DemandState.CANCELLED,
                    error="Message processing cancelled",
                )
            except Exception:
                logger.warning(
                    f"MessageJobHandler: failed to cancel job {job.job_id[:8]}..."
                )
        except asyncio.CancelledError:
            # asyncio task cancellation (pause or shutdown). Stage
            # 2 raises this from work_fn. Discriminate pause vs
            # terminate by reading ``instance.status`` from the DB.
            # PAUSE → leave PROCESSING for resume (no complete_job,
            # no re-raise). Non-PAUSE (terminate, shutdown, etc.) →
            # complete as CANCELLED and re-raise.
            instance = await asyncio.to_thread(
                self._manager._instance_repository.get, instance_id
            )
            if instance and instance.status == InstanceStatus.PAUSED.value:
                # PAUSE → leave PROCESSING for resume. Swallow the
                # exception so the worker pool doesn't see a cancel
                # and mark the job FAILED.
                pass
            else:
                # NOT PAUSE (terminate, shutdown, etc.) → complete
                # job as CANCELLED and re-raise so the caller can
                # clean up.
                try:
                    await self._job_service.complete_job(
                        job.job_id,
                        demand_state=DemandState.CANCELLED,
                        error="Message processing cancelled (instance terminated)",
                    )
                except Exception:
                    logger.warning(
                        f"MessageJobHandler: failed to cancel job {job.job_id[:8]}..."
                    )
                logger.info(
                    f"MessageJobHandler: job {job.job_id[:8]}... cancelled "
                    f"(not pause, status={instance.status if instance else 'unknown'})"
                )
                raise
        except Exception as e:
            # Stage 2 error (gate.run / work_fn raised a non-cancel
            # exception). The pipeline did NOT run
            # ``handle_message_processing_error`` for this case
            # (stage 2 is outside the pipeline's post-processing
            # try/except). Run the unified error helper so the
            # three side-effects (DB error event, lifecycle event,
            # parent report) AND the ``complete_job(FAILED)`` all
            # fire — matching the original JQ handler's
            # ``except Exception`` clause.
            logger.error(
                f"MessageJobHandler: error processing MESSAGE job "
                f"{job.job_id[:8]}...: {e}",
                exc_info=True,
            )
            await handle_message_processing_error(
                instance_manager=self._manager,
                instance_id=instance_id,
                error=e,
                message_id=message_id,
                job_id=job.job_id,
            )
            # Do NOT re-raise — the original JQ handler swallowed
            # generic exceptions after running the error helper.
        finally:
            # JQ-SPECIFIC cleanup: always pop the CTS we stored. This
            # runs whether the pipeline succeeded, deferred, raised,
            # or was cancelled. Mirrors the original ``finally`` block.
            self._active_tokens.pop(job.job_id, None)

    def _build_callbacks(
        self, job, instance_id: str, message_id: str | None
    ) -> PipelineCallbacks:
        """Build :class:`PipelineCallbacks` for the JobQueue path.

        ``on_success``     - CM-aware skip-complete. The pipeline
        runs the six shared stages, then calls ``on_success`` with
        the happy-path result. JQ's success callback checks the
        CorrelationManager (authoritative when wired) or the legacy
        ``waiting_for`` DB column (graceful degradation when CM is
        None). When pending children are reported, the callback
        emits ``notify_watchers(status="in_progress", waiting_for=wf)``
        and returns WITHOUT calling ``complete_job`` — the job stays
        PROCESSING and the ``JobFeedbackObserver`` completes it via
        the CM callback when the last child resolves. When no
        pending children, the callback calls
        ``complete_job(COMPLETED, result_summary=...)``.

        ``on_error``       - no-op. The pipeline already runs
        ``handle_message_processing_error`` (which writes the DB
        error event, publishes the lifecycle event, sends the
        error report to the parent, AND marks the job FAILED when
        ``job_id`` is passed in ``error_handler_id``). No
        additional JQ-specific cleanup is required.

        ``on_contention``  - JQ requeue. Calls
        ``_requeue_for_contention`` (atomic_transition
        PROCESSING→PENDING + queue-lock release + dispatch-bus
        notify). Returns ``ProcessingResult(success=False,
        should_defer=True)`` so the pipeline short-circuits its
        post-processing. Covers both ``LeaseContention`` (returned
        from the gate) and ``LeaseLostError`` (raised by the gate)
        — the only difference is the log level/message.

        ``on_cancel`` is intentionally left as ``None``. All
        cancellation handling (token cancel via
        ``OperationCancelledError`` and asyncio task cancel via
        ``asyncio.CancelledError``) fires from stage 2
        (gate.run / work_fn) and propagates out of
        ``pipeline.execute()`` — the pipeline only invokes
        ``on_cancel`` for stage 3-6 errors. ``handle()``'s outer
        try/except discriminates pause vs terminate, matching the
        original JQ handler's behaviour.
        """
        job_id = job.job_id
        manager = self._manager
        job_service = self._job_service
        handler = self

        async def on_success(result: ProcessingResult) -> None:
            # CM-aware skip-complete. When the CM (authoritative when
            # wired) or the legacy ``waiting_for`` column reports
            # pending children, emit ``in_progress`` and leave the job
            # PROCESSING so the JobFeedbackObserver completes it via
            # the CM callback when the last child resolves.
            skip_complete = False
            wf = 0
            cm = None
            try:
                # Try CM first (authoritative) before hitting the DB.
                try:
                    from daemon.services.correlation_manager import (
                        get_correlation_manager,
                    )
                    cm = get_correlation_manager()
                except Exception:
                    cm = None

                if cm is not None:
                    wf = cm.get_pending_count(instance_id)
                    if wf > 0:
                        logger.info(
                            f"MessageJobHandler: CM reports "
                            f"{wf} unresolved child correlation(s) "
                            f"for instance {instance_id[:8]}..., "
                            f"deferring completion for job {job_id[:8]}..."
                        )
                        skip_complete = True
                else:
                    # Graceful degradation: legacy ``waiting_for`` DB read.
                    # The DB ``waiting_for`` snapshot can race against a
                    # concurrent ``register_message_send``, but the CM's
                    # in-memory pending set is the authoritative view
                    # inside the per-parent lock. When ``cm.is_complete``
                    # returns False, children are still pending — defer
                    # completion so the CM callback handles the terminal
                    # transition when the last child resolves.
                    #
                    # Phase 4: ``waiting_for`` is retained only as a
                    # fallback for graceful degradation (CM is None /
                    # disabled) and as the rebuild cache for
                    # ``rebuild_from_db()`` (ADR-011).
                    instance = await asyncio.to_thread(
                        manager._instance_repository.get, instance_id
                    )
                    if instance is not None:
                        wf = getattr(instance, "waiting_for", None) or 0
                        if wf > 0:
                            logger.info(
                                f"MessageJobHandler: instance "
                                f"{instance_id[:8]}... has waiting_for={wf} "
                                f"(status={instance.status}), "
                                f"deferring completion for job {job_id[:8]}..."
                            )
                            skip_complete = True
            except Exception as e:
                logger.warning(
                    f"MessageJobHandler: failed to check instance status for {instance_id[:8]}..., "
                    f"proceeding with job completion: {e}"
                )

            if skip_complete:
                # Emit in_progress notification so watchers know the
                # instance finished its turn but child agents are still
                # pending. ``wf`` is derived from CM above when
                # available, otherwise from the ``waiting_for`` DB column.
                if wf > 0:
                    try:
                        await job_service.notify_watchers(
                            job_id,
                            status="in_progress",
                            progress=result.result_content,
                            waiting_for=wf,
                        )
                    except Exception as e:
                        logger.warning(
                            f"MessageJobHandler: failed to emit in_progress notification: {e}"
                        )
                    return
                # If wf == 0, fall through to normal completion handling below

            # Mark job complete
            await job_service.complete_job(
                job_id,
                demand_state=DemandState.COMPLETED,
                result_summary=result.result_content,
            )

        async def on_error(result: ProcessingResult) -> None:
            # No-op: the pipeline already ran
            # ``handle_message_processing_error`` (with ``job_id``
            # in ``error_handler_id``), which writes the DB error
            # event, publishes the lifecycle event, sends the error
            # report to the parent, AND marks the job FAILED.
            # No additional JQ-specific cleanup is required.
            return

        async def on_contention(exc: Exception) -> ProcessingResult:
            if isinstance(exc, LeaseLostError):
                logger.warning(
                    f"MessageJobHandler: lease lost mid-execution for "
                    f"job={job_id[:8]}... instance={instance_id[:8]}... "
                    f"— re-queuing (another process cleared the lease)"
                )
                await handler._requeue_for_contention(
                    job, f"lease lost mid-execution: {exc}"
                )
            else:
                # LeaseContention: the lease was held by a sibling
                # dispatcher (e.g. a Task claimed this instance between
                # our pre-flight and the gate acquire). Re-queue the job.
                holder_summary = (
                    f"holder_id={exc.holder_id} "
                    f"holder_kind={exc.holder_kind}"
                )
                await handler._requeue_for_contention(job, holder_summary)
            return ProcessingResult(success=False, should_defer=True)

        return PipelineCallbacks(
            on_success=on_success,
            on_error=on_error,
            on_contention=on_contention,
        )

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

        M13: the per-queue lock is ALWAYS released, even if
        ``atomic_transition`` returns ``None`` (already transitioned
        by another process — our local acquisition was the cause of
        the contention, the lock row is still ours) or raises. A
        conditional skip on ``result is None`` leaks the lock
        permanently: the lock row has our ``job_id`` but no caller
        will ever clean it up. The lock release itself is wrapped in
        a try/except so an internal error in ``release_queue_lock``
        does not skip the dispatch bus notify or, worse, mask the
        original ``atomic_transition`` exception.
        """
        logger.info(
            f"[TRACE] MessageJobHandler.handle: SKIP job {job.job_id[:8]}... — "
            f"instance {job.instance_id[:8]}... re-queuing ({reason})"
        )
        try:
            result = await asyncio.to_thread(
                self._job_repo.atomic_transition, job.job_id,
                from_status="processing", to_status="pending",
            )
        except Exception as trans_err:  # noqa: BLE001
            logger.warning(
                f"MessageJobHandler: atomic_transition failed during "
                f"re-queue for job {job.job_id[:8]}...: "
                f"{type(trans_err).__name__}: {trans_err} — "
                "lock release will still be attempted"
            )
            result = None
        if result is None:
            # Job was already transitioned by another process, OR
            # the transition itself failed. Either way, the lock we
            # acquired at the start of this dispatch attempt is still
            # ours and must be released — see M13 in
            # docs/audits/postgresql-concurrency-audit-2026-06-18.
            logger.debug(
                f"MessageJobHandler: job {job.job_id[:8]}... already "
                "transitioned or transition failed; releasing lock "
                "unconditionally"
            )
        # ALWAYS release the lock if we know which (project, queue)
        # pair it belongs to. Wrapped in try/except so a release
        # error doesn't suppress the dispatch-bus notify or the
        # caller's exception context.
        if job.project_id and job.queue_id:
            try:
                await self._job_service._lock_manager.release_queue_lock(
                    job.project_id, job.queue_id, job.job_id
                )
            except Exception as release_err:  # noqa: BLE001
                logger.warning(
                    f"MessageJobHandler: failed to release queue lock "
                    f"for job {job.job_id[:8]}...: "
                    f"{type(release_err).__name__}: {release_err} — "
                    "lock may be orphaned and will be recovered at "
                    "next startup sweep"
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
