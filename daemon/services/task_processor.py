"""Task processor for message queue redesign - routes tasks to type-specific handlers."""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from .execution_gate import LeaseContention, LeaseLostError
from .main_loop_bridge import MainLoopBridge
from daemon.cancellation import CancellationToken, OperationCancelledError
from daemon.repositories.execution_lease.models import LeaseHolderKind
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

    This processor handles the actual message processing:
    1. Updates message status to PROCESSING
    2. Calls the message processing logic from manager
    3. Updates message status to COMPLETED or FAILED
    4. Creates events for state changes
    """

    def __init__(
        self,
        instance_manager,
        task_repo: "TaskRepository",
        event_repo: "EventRepository | None",
        message_repository=None,
        source_dispatcher=None,  # ResponseDispatcher for external routing
    ):
        """Initialize the message processor.

        Args:
            instance_manager: InstanceManager for message processing.
            task_repo: TaskRepository for task operations.
            event_repo: Optional EventRepository for event creation.
            message_repository: Optional MessageQueueRepository for message updates.
            source_dispatcher: Optional ResponseDispatcher for external routing.
        """
        self._manager = instance_manager
        self._task_repo = task_repo
        self._event_repo = event_repo
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

    async def process(self, task: "Task", cancellation_token: "CancellationToken | None" = None) -> dict[str, Any]:
        """Process a message task with full lifecycle.
        
        1. Get message content from repository
        2. Call manager's _process_message_with_tracking (LangGraph execution)
        3. On success: check child completion (may create parent task)
        4. On failure: record error event
        
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
        
        # Get message content via repository (thread-safe)
        message = None
        if self._message_repo:
            message = await asyncio.to_thread(
                self._message_repo.get, task.message_id
            )
        
        if not message:
            # Fallback: try task repo
            message = await asyncio.to_thread(
                self._task_repo.get_by_message, task.message_id
            )
        
        if not message:
            raise ValueError(
                f"Message {task.message_id} not found for task {task.id}"
            )
        
        message_content = message.content if message else ""
        message_source = message.source if message else None
        message_images = getattr(message, 'images', None) if message else None
        # resume_mode: if True in message metadata, treat as checkpoint resume
        message_metadata = getattr(message, 'message_metadata', None) if message else None
        resume_mode = message_metadata.get("resume_mode", False) if message_metadata else False
        is_retry = task.retry_count > 0 or resume_mode
        # silent: if True, skip message injection during checkpoint resume
        silent = message_metadata.get("silent", False) if message_metadata else False
        
        try:
            # Execution Gate: acquire the per-instance lease before
            # driving graph.astream. The lease is the single source of
            # truth for "who is driving graph.astream for this
            # instance?"; the Gate prevents the dual-dispatcher
            # checkpoint race documented in
            # docs/bugs/child-completion-report-lost-cross-dispatcher-jobqueue-vs-workerpool.md.
            #
            # If the lease is held by another dispatcher (most likely
            # a MessageJobHandler driving a sibling MESSAGE job), the
            # gate returns ``LeaseContention`` and we re-queue the
            # task in PENDING state so the next worker poll picks it
            # up after the holder releases.
            async def _do_process():
                return await self._manager._process_message_with_tracking(
                    instance_id=task.instance_id,
                    message=message_content,
                    message_id=task.message_id,
                    cancellation_token=cancellation_token,
                    is_retry=is_retry,
                    retry_count=task.retry_count,
                    message_source=message_source,
                    images=message_images,
                    silent=silent,
                )

            try:
                gate_outcome = await self._manager.execution_gate.run(
                    instance_id=task.instance_id,
                    holder_id=f"task:{task.id}",
                    holder_kind=LeaseHolderKind.TASK.value,
                    work_fn=_do_process,
                )
            except LeaseLostError as e:
                # Lease row was cleared by ``recover_stale_leases`` on
                # another node (or otherwise revoked) while we were
                # driving graph.astream. The in-flight work_fn was
                # cancelled by the gate. Re-queue with backoff — the
                # next attempt acquires a fresh lease.
                logger.warning(
                    f"ProcessMessageProcessor: lease lost mid-execution "
                    f"for task {task.id} instance={task.instance_id[:8]}... "
                    f"— re-queuing with backoff: {e}"
                )
                await asyncio.to_thread(
                    self._task_repo.requeue_task_with_backoff, task.id
                )
                return {
                    "success": False,
                    "requeued": True,
                    "content": None,
                    "message_id": task.message_id,
                }
            if isinstance(gate_outcome, LeaseContention):
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
                self._contention_counts[task.instance_id] = (
                    self._contention_counts.get(task.instance_id, 0) + 1
                )
                logger.debug(
                    f"ProcessMessageProcessor: lease contention for task {task.id} "
                    f"instance={task.instance_id[:8]}... "
                    f"(holder_id={gate_outcome.holder_id} "
                    f"holder_kind={gate_outcome.holder_kind}) — re-queuing with backoff"
                )
                now = time.monotonic()
                last = self._last_info_at.get(task.instance_id, 0.0)
                if now - last >= 60.0:
                    logger.info(
                        f"ProcessMessageProcessor: lease contention summary "
                        f"instance={task.instance_id[:8]}... "
                        f"count={self._contention_counts[task.instance_id]} "
                        f"in the last {int(now - last)}s"
                    )
                    self._last_info_at[task.instance_id] = now
                # Transition: RUNNING -> PENDING with a short
                # jittered backoff. ``requeue_task_with_backoff`` is
                # conditional on ``status='running'`` so a task that
                # has been completed/cancelled in the meantime is
                # left alone.
                await asyncio.to_thread(
                    self._task_repo.requeue_task_with_backoff, task.id
                )
                # Bail out of this task run without an exception —
                # the next worker poll will re-claim and retry.
                return {
                    "success": False,
                    "requeued": True,
                    "content": None,
                    "message_id": task.message_id,
                }
            result = gate_outcome
            
            # Mark message as completed so _process_child_completion_and_notify_parent can proceed
            if self._message_repo:
                await asyncio.to_thread(self._message_repo.complete, task.message_id)
            
            # Mark task as completed - THIS WAS THE BUG: complete_task was never called
            # causing tasks to stay in RUNNING status forever, making them appear "stale"
            await asyncio.to_thread(
                self._task_repo.complete_task,
                task.id,
                {"success": True, "message_id": task.message_id}
            )
            
            # Dispatch completed message to external sources (Telegram, Discord, etc.)
            # For internal messages (completion reports, etc.), use the original external source
            # Note: internal_agent:* is agent-to-agent communication, NOT a completion report
            logger.debug(f"[DISPATCH] task completed: instance={task.instance_id}, message_source={message_source}, result={'truthy' if result else 'falsy'}")
            dispatch_source = message_source
            # job_event notifications are completion reports from the watcher system —
            # they must be routed back to the original external source (e.g. Slack/Telegram)
            is_internal_report = (
                message_source.startswith("internal_report:")
                or message_source.startswith("internal_error_report:")
                or message_source.startswith("internal_agent:job_event:")
            )
            logger.debug(f"[DISPATCH] is_internal_report={is_internal_report}, dispatch_source={dispatch_source}")
            if is_internal_report:
                # Retrieve original external source from instance metadata
                instance_meta = self._manager._instance_repository.get(task.instance_id)
                # Use is not None check because empty dict {} is falsy
                if instance_meta is not None and instance_meta.instance_metadata is not None:
                    dispatch_source = instance_meta.instance_metadata.get("original_source")
                logger.debug(f"[DISPATCH] resolved original_source: {dispatch_source}")
                if not dispatch_source:
                    logger.warning(
                        f"No original_source found for instance {task.instance_id[:8]}... "
                        f"(message_source={message_source})"
                    )
                    dispatch_source = None  # Skip dispatch if no original source
            
            logger.debug(f"[DISPATCH] attempting dispatch: source={dispatch_source}, has_dispatcher={self._source_dispatcher is not None}")
            if not dispatch_source:
                logger.debug("[DISPATCH] SKIPPED: dispatch_source is None or empty")
            elif not result:
                logger.debug("[DISPATCH] SKIPPED: result is None or empty")
            elif self._source_dispatcher:
                try:
                    await self._source_dispatcher.dispatch_completed(
                        instance_id=task.instance_id,
                        message_id=task.message_id,
                        source=dispatch_source,
                        content=result.content or "",
                        message_type="final",
                    )
                except Exception as e:
                    logger.error(f"Error dispatching to external source: {e}", exc_info=True)
                    # Don't fail the task - dispatch is best-effort
            
            # Check if this instance is a child that has completed all work
            # This may create a completion report task for the parent
            try:
                if hasattr(self._manager, '_process_child_completion_and_notify_parent'):
                    await self._manager._process_child_completion_and_notify_parent(
                        task.instance_id, task.message_id
                    )
            except Exception as e:
                logger.error(
                    f"Error checking child completion for {task.instance_id[:8]}...: {e}",
                    exc_info=True,
                )
                # Don't fail the task — the message was processed successfully
            
            return {
                "success": True,
                "content": result.content if result else None,
                "message_id": task.message_id,
            }
            
        except OperationCancelledError:
            raise
        except asyncio.CancelledError:
            logger.info(
                f"Task {task.id} paused (instance {task.instance_id[:8]}...)"
            )
            raise
        except Exception as e:
            logger.error(
                f"Failed to process message task {task.id}: {e}", exc_info=True
            )

            # Phase 0 of CorrelationManager migration: the WorkerPool
            # and JobQueue paths now share the same three error
            # side-effects (DB error event, lifecycle event publish,
            # error report to parent) via
            # ``handle_message_processing_error``. The WorkerPool path
            # does NOT pass ``job_id`` — task completion is handled
            # separately by the WorkerPool via ``TaskRepository.complete_task``.
            await handle_message_processing_error(
                instance_manager=self._manager,
                instance_id=task.instance_id,
                error=e,
                message_id=task.message_id,
                task_id=task.id,
            )

            raise


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
