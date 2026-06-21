"""Task processor for message queue redesign - routes tasks to type-specific handlers."""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from .execution_gate import LeaseContention, LeaseHolderKind, LeaseLostError
from .main_loop_bridge import MainLoopBridge
from .message_processing_pipeline import (
    MessageProcessingPipeline,
    PipelineCallbacks,
    ProcessingContext,
    ProcessingResult,
)
from daemon.cancellation import CancellationToken, OperationCancelledError
from daemon.services.message_processing_errors import (
    handle_message_processing_error,
)

if TYPE_CHECKING:
    from daemon.repositories.task.models import Task
    from daemon.repositories.task.repository import TaskRepository
    from daemon.repositories.event.repository import EventRepository

logger = logging.getLogger(__name__)


class BaseProcessor(ABC):
    """Base class for task processors."""

    @abstractmethod
    async def process(self, task: "Task", cancellation_token: "CancellationToken | None" = None) -> dict[str, Any]:
        """Process a task asynchronously.

        Args:
            task: The task to process.
            cancellation_token: Optional token for cancellation.

        Returns:
            Result dictionary with processing outcome.
        """
        pass


class ProcessMessageProcessor(BaseProcessor):
    """Processor for process_message tasks.

    This processor handles the actual message processing. Phase 5 of
    the CorrelationManager migration: the six shared stages
    (acquire-lease, mark-message-completed, dispatch, child-completion
    check, error reporting, contention/cancellation handling) are
    delegated to :class:`MessageProcessingPipeline`. Path-specific
    behaviour (throttled lease-contention logging, the "task paused"
    log message on cancellation, task-completion writes via
    ``TaskRepository.complete_task``, error re-raise to the worker
    pool) lives in this class and is supplied to the pipeline as
    :class:`PipelineCallbacks`.
    """

    def __init__(
        self,
        instance_manager,
        task_repo: "TaskRepository",
        event_repo: "EventRepository | None" = None,
        message_repository=None,
        source_dispatcher=None,  # ResponseDispatcher for external routing
        pipeline: "MessageProcessingPipeline | None" = None,
    ):
        """Initialize the message processor.

        Args:
            instance_manager: InstanceManager for message processing.
            task_repo: TaskRepository for task operations.
            event_repo: Optional EventRepository for event creation.
                Accepted for API compatibility — the refactored
                implementation no longer writes events directly (the
                pipeline's error helper handles it for failure paths
                and no event is needed for success).
            message_repository: Optional MessageQueueRepository for
                message updates. Wired into the pipeline as
                ``queue_repository`` if ``pipeline`` is not provided.
            source_dispatcher: Optional ResponseDispatcher for
                external routing. Wired into the pipeline if
                ``pipeline`` is not provided.
            pipeline: Optional pre-built
                :class:`MessageProcessingPipeline`. When ``None``
                (default) the processor constructs one from
                ``instance_manager.execution_gate``,
                ``instance_manager``, ``source_dispatcher``, and
                ``message_repository``. Test/extension code may
                inject a custom pipeline.
        """
        self._manager = instance_manager
        self._task_repo = task_repo
        self._event_repo = event_repo  # accepted for API compat; unused
        self._message_repo = message_repository
        self._source_dispatcher = source_dispatcher
        # Per-instance contention counters. When a task hits
        # ``LeaseContention`` against the same sibling MESSAGE job
        # repeatedly (typical for a hot instance receiving many
        # concurrent child reports), the per-occurrence log is at
        # DEBUG and a periodic INFO summary is emitted instead so
        # we don't flood the log on a hot instance. ``_last_info_at``
        # throttles the summary to one per minute per instance.
        self._contention_counts: dict[str, int] = {}
        self._last_info_at: dict[str, float] = {}

        if pipeline is None:
            pipeline = MessageProcessingPipeline(
                execution_gate=instance_manager.execution_gate,
                manager=instance_manager,
                source_dispatcher=source_dispatcher,
                queue_repository=message_repository,
            )
        self._pipeline = pipeline

    async def process(self, task: "Task", cancellation_token: "CancellationToken | None" = None) -> dict[str, Any]:
        """Process a message task with full lifecycle.

        Phase 5 refactor: pre-pipeline work (loading the message,
        computing ``is_retry`` / ``silent`` flags) and post-pipeline
        mapping (ProcessingResult → worker dict, error re-raise to
        the worker pool) stay in this method. The shared stages
        (lease acquisition, mark-completed, dispatch, child
        completion, error reporting) are delegated to the pipeline.

        The worker loop expects a dict return:
        - ``{success: True, content, message_id}`` on success
        - ``{success: False, requeued: True, content: None, message_id}`` on contention
        - exception is raised on error or cancellation so the
          worker pool can mark the task FAILED / leave it RUNNING
          for resume, respectively.

        Args:
            task: The task with message_id to process.
            cancellation_token: Optional token for cancellation.

        Returns:
            Result dictionary with processing outcome.
        """
        if not task.message_id:
            raise ValueError(f"Task {task.id} has no message_id")

        logger.info(
            f"Processing message task {task.id}: "
            f"message={task.message_id[:8]}..., instance={task.instance_id[:8]}..."
        )

        # ---- Pre-pipeline: load message and compute flags ----
        # The Task row (this function's ``task`` arg) only carries
        # ``message_id`` as a foreign key — the Message itself (with
        # content, source, images, metadata) lives in the
        # ``message_queue`` table and must be fetched via the message
        # repository wired in at construction time.
        #
        # IMPORTANT: do NOT fall back to
        # ``self._task_repo.get_by_message`` here. That method returns a
        # ``Task`` object (per its signature in
        # ``daemon/repositories/task/repository.py:get_by_message``) —
        # NOT a ``MessageQueue``. Assigning a Task to ``message`` and
        # then accessing ``message.content`` raises
        # ``AttributeError: 'Task' object has no attribute 'content'``
        # and wedges message processing for every task that hit the
        # secondary lookup path. The previous code carried this broken
        # fallback since phase 3; once ``_message_repo.get`` returned
        # ``None`` for any reason (race with cleanup, missing wiring in
        # a test path, etc.) the fallback converted the situation into
        # an opaque crash with no actionable error. Fail fast instead.
        if self._message_repo is None:
            raise RuntimeError(
                f"ProcessMessageProcessor has no message_repository wired; "
                f"cannot load message {task.message_id} for task {task.id}"
            )

        message = await asyncio.to_thread(
            self._message_repo.get, task.message_id
        )

        if message is None:
            raise ValueError(
                f"Message {task.message_id} not found in message_queue "
                f"for task {task.id}"
            )

        message_content = message.content if message else ""
        message_source = message.source if message else None
        message_images = getattr(message, 'images', None) if message else None
        message_metadata = getattr(message, 'message_metadata', None) if message else None
        original_resume_mode = (
            message_metadata.get("resume_mode", False)
            if message_metadata else False
        )
        # ``is_retry`` drives ``_process_message_with_tracking``'s
        # checkpoint-resume path. The original WP code computed it as
        # ``retry_count > 0 or resume_mode``; the pipeline maps
        # ``ProcessingContext.resume_mode`` to the ``is_retry`` kwarg,
        # so we pre-compute the OR here to preserve identical
        # behaviour.
        is_retry = task.retry_count > 0 or original_resume_mode
        # silent: if True, skip message injection during checkpoint resume
        silent = (
            message_metadata.get("silent", False)
            if message_metadata else False
        )

        # ---- Build pipeline input ----
        context = ProcessingContext(
            instance_id=task.instance_id,
            message_id=task.message_id,
            message=message_content,
            retry_count=task.retry_count,
            message_source=message_source,
            silent=silent,
            images=message_images,
            resume_mode=is_retry,
            cancellation_token=cancellation_token,
        )
        callbacks = self._build_callbacks(task)

        # ---- Run the pipeline ----
        # The pipeline handles the six shared stages. Errors from
        # stage 2 (gate.run / work_fn) propagate as exceptions;
        # errors from stages 4-6 are caught inside the pipeline and
        # returned as ``ProcessingResult(success=False, error=e)``
        # (the pipeline runs ``handle_message_processing_error``
        # for those). ``on_cancel`` is left as ``None`` so the
        # pipeline re-raises cancellation and we can attach the
        # WorkerPool's "task paused" log message in the outer
        # except clause below.
        try:
            result = await self._pipeline.execute(
                context=context,
                holder_id=f"task:{task.id}",
                holder_kind=LeaseHolderKind.TASK.value,
                callbacks=callbacks,
                error_handler_id={"task_id": task.id},
            )
        except OperationCancelledError:
            # Cancellation requested via cancellation_token (pause /
            # shutdown). Re-raise silently — the worker pool's
            # ``_handle_cancellation`` distinguishes pause from
            # shutdown via the cancellation reason.
            raise
        except asyncio.CancelledError:
            # asynqio task cancellation (e.g. worker thread pool
            # cancellation during pause). Log a friendly message
            # so the operator can correlate the log line with the
            # task, then re-raise.
            logger.info(
                f"Task {task.id} paused (instance {task.instance_id[:8]}...)"
            )
            raise
        except Exception as e:
            # Work_fn / gate error: the pipeline did NOT run
            # ``handle_message_processing_error`` for this case (the
            # error bypassed the pipeline's post-processing try
            # block). Run the unified error helper so the three
            # side-effects (DB error event, lifecycle event, parent
            # report) still fire regardless of which stage raised.
            logger.error(
                f"Failed to process message task {task.id}: {e}", exc_info=True
            )
            await handle_message_processing_error(
                instance_manager=self._manager,
                instance_id=task.instance_id,
                error=e,
                message_id=task.message_id,
                task_id=task.id,
            )
            raise

        # If the pipeline returned a result with an error
        # (post-processing error from stages 4-6), the pipeline
        # already ran ``handle_message_processing_error``. Re-raise
        # so the worker pool's ``_handle_task_failure`` marks the
        # task FAILED via ``fail_task``.
        if result.error is not None:
            raise result.error

        # Map ``ProcessingResult`` back to the dict the worker loop
        # expects.
        if result.should_defer:
            # ``on_contention`` already re-queued the task with
            # jittered backoff. Return a dict that signals
            # "re-queued, not failed" so the worker loop can move
            # on to the next claim.
            return {
                "success": False,
                "requeued": True,
                "content": None,
                "message_id": task.message_id,
            }

        return {
            "success": True,
            "content": result.result_content,
            "message_id": task.message_id,
        }

    def _build_callbacks(self, task: "Task") -> PipelineCallbacks:
        """Build :class:`PipelineCallbacks` for the WorkerPool path.

        ``on_success``     - marks the task COMPLETED in the task
        repo with ``{"success": True, "message_id": ...}``. This
        happens AFTER the pipeline's mark-message-completed,
        dispatch, and child-completion stages, which matches the
        observable behaviour of the post-refactor implementation
        (the unified pipeline's success path ends with the
        ``on_success`` callback).

        ``on_error``       - no-op. The pipeline already runs
        ``handle_message_processing_error`` (which writes the DB
        error event, publishes the lifecycle event, and sends the
        error report to the parent), and the worker pool marks the
        task FAILED via ``fail_task`` when ``process()`` re-raises.
        No additional task-specific cleanup is required.

        ``on_contention``  - throttled lease-contention logging
        (per-instance count + 60-second INFO summary) plus
        ``requeue_task_with_backoff``. Returns
        ``ProcessingResult(success=False, should_defer=True)`` so
        the worker loop knows the task was re-queued. Covers both
        ``LeaseContention`` (returned from the gate) and
        ``LeaseLostError`` (raised by the gate) — the only
        difference is the log level/message.

        ``on_cancel`` is intentionally left as ``None``: the
        pipeline re-raises cancellation, and ``process()``'s outer
        try/except attaches the WorkerPool-specific "task paused"
        log message for ``asyncio.CancelledError``.
        """
        task_id = task.id
        instance_id = task.instance_id
        message_id = task.message_id
        task_repo = self._task_repo
        counts = self._contention_counts
        last_info = self._last_info_at

        async def on_success(result: ProcessingResult) -> None:
            await asyncio.to_thread(
                task_repo.complete_task,
                task_id,
                {"success": True, "message_id": message_id},
            )

        async def on_error(result: ProcessingResult) -> None:
            # The pipeline already ran ``handle_message_processing_error``
            # before invoking this callback, and ``process()`` re-raises
            # ``result.error`` afterwards so the worker pool can mark
            # the task FAILED. There is no task-specific cleanup to do
            # here.
            return

        async def on_contention(exc: Exception) -> ProcessingResult:
            if isinstance(exc, LeaseLostError):
                # Lease row was cleared by ``recover_stale_leases`` on
                # another node (or otherwise revoked) while we were
                # driving graph.astream. The in-flight work_fn was
                # cancelled by the gate. Re-queue with backoff — the
                # next attempt acquires a fresh lease.
                logger.warning(
                    f"ProcessMessageProcessor: lease lost mid-execution "
                    f"for task {task_id} instance={instance_id[:8]}... "
                    f"— re-queuing with backoff: {exc}"
                )
            elif isinstance(exc, LeaseContention):
                # Cross-dispatcher contention: a MESSAGE job is
                # currently driving graph.astream for this instance.
                # Back off: re-queue the task to PENDING with a
                # jittered ``next_retry_at`` (0.5–2.0 s) so the worker
                # poll does NOT re-claim the same task immediately
                # and busy-spin against the holding MESSAGE job. The
                # MESSAGE job side is self-limiting (JobQueue polls
                # every ~30 s) but the task side was not — without
                # this backoff a task for the same instance would
                # re-claim, re-run, hit contention, and re-queue in
                # a tight loop for the entire duration of the sibling
                # MESSAGE job.
                #
                # Log at DEBUG per occurrence to avoid flooding on a
                # hot instance; emit a throttled INFO summary at most
                # once per minute per instance.
                counts[instance_id] = counts.get(instance_id, 0) + 1
                logger.debug(
                    f"ProcessMessageProcessor: lease contention for task {task_id} "
                    f"instance={instance_id[:8]}... "
                    f"(holder_id={exc.holder_id} "
                    f"holder_kind={exc.holder_kind}) — re-queuing with backoff"
                )
                now = time.monotonic()
                last = last_info.get(instance_id, 0.0)
                if now - last >= 60.0:
                    logger.info(
                        f"ProcessMessageProcessor: lease contention summary "
                        f"instance={instance_id[:8]}... "
                        f"count={counts[instance_id]} "
                        f"in the last {int(now - last)}s"
                    )
                    last_info[instance_id] = now

            # Transition: RUNNING -> PENDING with a short jittered
            # backoff. ``requeue_task_with_backoff`` is conditional
            # on ``status='running'`` so a task that has been
            # completed/cancelled in the meantime is left alone.
            await asyncio.to_thread(
                task_repo.requeue_task_with_backoff, task_id
            )
            # Bail out of this task run without an exception — the
            # next worker poll will re-claim and retry.
            return ProcessingResult(success=False, should_defer=True)

        return PipelineCallbacks(
            on_success=on_success,
            on_error=on_error,
            on_contention=on_contention,
        )


class SendReportProcessor(BaseProcessor):
    """Processor for send_report tasks.

    Sends completion reports to parent instances.
    """

    def __init__(
        self,
        instance_manager,
        task_repo: "TaskRepository",
        event_repo: "EventRepository | None",
    ):
        self._manager = instance_manager
        self._task_repo = task_repo
        self._event_repo = event_repo

    async def process(self, task: "Task", cancellation_token: "CancellationToken | None" = None) -> dict[str, Any]:
        """Send a completion report to the parent instance.

        Args:
            task: The task with report data.
            cancellation_token: Optional token for cancellation.

        Returns:
            Result dictionary.
        """
        logger.info(f"Sending report for task {task.id}")

        raise NotImplementedError(f"SendReportProcessor.process() not yet implemented for task {task.id}")


class CleanupProcessor(BaseProcessor):
    """Processor for cleanup tasks.

    Handles instance termination and resource cleanup.
    """

    def __init__(
        self,
        instance_manager,
        task_repo: "TaskRepository",
        event_repo: "EventRepository | None",
    ):
        self._manager = instance_manager
        self._task_repo = task_repo
        self._event_repo = event_repo

    async def process(self, task: "Task", cancellation_token: "CancellationToken | None" = None) -> dict[str, Any]:
        """Perform cleanup for an instance.

        Args:
            task: The task with cleanup instructions.
            cancellation_token: Optional token for cancellation.

        Returns:
            Result dictionary.
        """
        logger.info(f"Cleanup task {task.id} for instance {task.instance_id[:8]}...")

        raise NotImplementedError(f"CleanupProcessor.process() not yet implemented for task {task.id}")


class TaskProcessor:
    """Routes tasks to type-specific processors and provides thread-safe execution.

    TaskProcessor is the main interface for the WorkerPool. It:
    1. Claims pending tasks from the database (thread-safe)
    2. Routes tasks to type-specific processors
    3. Provides the thread-to-async bridge for processing
    4. Updates task status in the database
    """

    def __init__(
        self,
        task_repo: "TaskRepository",
        instance_manager,
        event_repo: "EventRepository | None" = None,
        graph_timeout_minutes: float = 40.0,
        source_dispatcher=None,  # ResponseDispatcher for external routing
    ):
        """Initialize the task processor.

        Args:
            task_repo: TaskRepository for task operations.
            instance_manager: InstanceManager for message processing.
            event_repo: Optional EventRepository for event creation.
            graph_timeout_minutes: Hard timeout for LangGraph execution (MainLoopBridge).
            source_dispatcher: Optional ResponseDispatcher for external routing.
        """
        self._task_repo = task_repo
        self._instance_manager = instance_manager
        self._event_repo = event_repo
        self._graph_timeout_minutes = graph_timeout_minutes
        self._source_dispatcher = source_dispatcher

        # Create type-specific processors
        self._processors: dict[str, BaseProcessor] = {
            "process_message": ProcessMessageProcessor(
                instance_manager, task_repo, event_repo,
                message_repository=instance_manager._queue_repository,
                source_dispatcher=source_dispatcher,
            ),
            "send_report": SendReportProcessor(
                instance_manager, task_repo, event_repo,
            ),
            "cleanup": CleanupProcessor(
                instance_manager, task_repo, event_repo,
            ),
        }

    def claim_task(self, worker_id: str) -> "Task | None":
        """Atomically claim the next pending task.

        This is called from the worker thread (synchronous).

        Args:
            worker_id: The worker claiming the task.

        Returns:
            The claimed task, or None if no tasks available.
        """
        return self._task_repo.claim_pending_task(worker_id)

    def run_task(self, task: "Task", cancellation_token: "CancellationToken | None" = None) -> None:
        """Run a task asynchronously via the main event loop.

        This method is called from the worker thread. It uses
        MainLoopBridge to run the async processing code.

        Args:
            task: The task to run.
            cancellation_token: Optional token for cancellation.

        Raises:
            Exception: If the task fails.
        """
        processor = self._processors.get(task.task_type)
        if processor is None:
            raise ValueError(f"Unknown task type: {task.task_type}")

        async def _run():
            return await processor.process(task, cancellation_token=cancellation_token)

        # Bridge from worker thread to main event loop
        # Use config-based graph timeout (0 = no timeout)
        timeout = self._graph_timeout_minutes * 60.0 if self._graph_timeout_minutes > 0 else None
        return MainLoopBridge.run_async(_run(), timeout=timeout)

    def get_pending_count(self) -> int:
        """Get the number of pending tasks."""
        return self._task_repo.get_pending_count()
