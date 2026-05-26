"""Task processor for message queue redesign - routes tasks to type-specific handlers."""

from __future__ import annotations

import asyncio
import logging
import re
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from .main_loop_bridge import MainLoopBridge
from daemon.cancellation import CancellationToken, OperationCancelledError
from daemon.constants import MAX_ERROR_LEN

if TYPE_CHECKING:
    from daemon.repositories.task.models import Task
    from daemon.repositories.task.repository import TaskRepository
    from daemon.repositories.event.repository import EventRepository

logger = logging.getLogger(__name__)


def _truncate_error(error: str, max_len: int = MAX_ERROR_LEN) -> str:
    """Truncate error message, stripping HTML if present."""
    if "<" in error and ">" in error:
        error = error.replace("<", " <").replace(">", "> ")
        error = re.sub(r"<[^>]+>", "", error)
        error = " ".join(error.split())
    if len(error) > max_len:
        return error[:max_len] + "..."
    return error


def _classify_error_type(e: Exception) -> str:
    """Classify an exception into an error_type string for _send_error_report.

    Args:
        e: The exception to classify.

    Returns:
        Error type string (e.g., "payload_too_large", "timeout_exhausted").
    """
    import openai
    import httpx

    exc_type = type(e)
    exc_name = exc_type.__name__

    # API status errors (includes 413, 401, 403, 404, 400, etc.)
    if isinstance(e, openai.APIStatusError):
        status = getattr(e, 'status_code', None)
        if status == 413:
            return "payload_too_large"
        if status == 401:
            return "authentication_error"
        if status == 403:
            return "forbidden"
        if status == 404:
            return "endpoint_not_found"
        if status == 400:
            return "bad_request"
        if status == 429:
            return "rate_limit"
        if status and 500 <= status < 600:
            return "server_error"
        return f"api_error_{status}" if status else "api_error"

    # Timeout errors
    if isinstance(e, (openai.APITimeoutError, httpx.TimeoutException, TimeoutError)):
        return "timeout_exhausted"

    # Context length errors
    if exc_name == "ContextLengthExceededError":
        return "context_length_exceeded"

    # Circuit breaker errors
    if exc_name == "CircuitOpenError":
        return "circuit_breaker_open"

    # Connection errors
    if isinstance(e, (openai.APIConnectionError, ConnectionResetError, BrokenPipeError, ConnectionAbortedError)):
        return "connection_error"

    # Bad request (non-context)
    if isinstance(e, openai.BadRequestError):
        return "bad_request"

    # Validation errors
    if exc_name in ("LLMResponseValidationError", "APIResponseValidationError"):
        return "validation_error"

    # Transient API errors (shouldn't reach here, but just in case)
    if exc_name == "TransientAPIError":
        return "transient_error"

    # Infrastructure / processing errors (Category C from error catalog)
    if isinstance(e, KeyError):
        return "instance_not_found"
    if isinstance(e, ValueError):
        return "invalid_data"
    if isinstance(e, RuntimeError):
        return "runtime_error"

    # Default
    return "execution_error"


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
        is_retry = task.retry_count > 0
        
        try:
            # Process the message via manager's existing logic (LangGraph execution)
            result = await self._manager._process_message_with_tracking(
                instance_id=task.instance_id,
                message=message_content,
                message_id=task.message_id,
                cancellation_token=cancellation_token,
                is_retry=is_retry,
                retry_count=task.retry_count,
                message_source=message_source,
                images=message_images,
            )
            
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
            dispatch_source = message_source
            is_internal_report = (
                message_source.startswith("internal_report:") or
                message_source.startswith("internal_error_report:")
            )
            if is_internal_report:
                # Retrieve original external source from instance metadata
                instance_meta = self._manager._instance_repository.get(task.instance_id)
                # Use is not None check because empty dict {} is falsy
                if instance_meta is not None and instance_meta.instance_metadata is not None:
                    dispatch_source = instance_meta.instance_metadata.get("original_source")
                if not dispatch_source:
                    logger.warning(
                        f"No original_source found for instance {task.instance_id[:8]}... "
                        f"(message_source={message_source})"
                    )
                    dispatch_source = None  # Skip dispatch if no original source
            
            if self._source_dispatcher and dispatch_source and result:
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
            # Graph was cancelled by pause_instance_cascade.
            # Per spec: "pausing instance still keeps the job on processing state"
            # For tasks in WorkerPool, this means task stays RUNNING (not completed).
            # Re-raise to propagate to worker thread cleanly.
            logger.info(
                f"Task {task.id} left RUNNING (instance {task.instance_id[:8]}... was paused)"
            )
            raise
        except Exception as e:
            error_msg = _truncate_error(str(e))
            logger.error(f"Failed to process message task {task.id}: {error_msg}", exc_info=True)

            # Create error event
            if self._manager._event_bus:
                await self._manager._event_bus.create_error_event(
                    instance_id=task.instance_id,
                    error={
                        "task_id": task.id,
                        "message_id": task.message_id,
                        "error": error_msg,
                    },
                )
            elif self._event_repo:
                await asyncio.to_thread(
                    self._event_repo.create_event,
                    instance_id=task.instance_id,
                    kind="error",
                    data={
                        "task_id": task.id,
                        "message_id": task.message_id,
                        "error": str(e),
                    },
                )
            
            # Publish instance lifecycle event for the failed instance
            # (for child instances, _send_error_report handles lifecycle events)
            if hasattr(self._manager, '_publish_instance_lifecycle_event'):
                try:
                    meta = self._manager._instance_repository.get(task.instance_id)
                    parent_id = meta.parent_id if meta else None
                    await self._manager._publish_instance_lifecycle_event(
                        instance_id=task.instance_id,
                        status="error",
                        error=error_msg,
                        parent_id=parent_id,
                    )
                except Exception as lifecycle_err:
                    logger.warning(f"Failed to publish lifecycle event for error: {lifecycle_err}")

            # Send error report to parent (child failure notification)
            # TaskProcessor is the primary reporting layer for processing-phase errors.
            # This prevents the parent from staying stuck in WAITING_CHILDREN forever.
            if hasattr(self._manager, '_send_error_report'):
                try:
                    await self._manager._send_error_report(
                        instance_id=task.instance_id,
                        error=error_msg,
                        error_type=_classify_error_type(e),
                        message_id=task.message_id,
                    )
                except Exception as report_err:
                    logger.warning(f"Failed to send error report to parent: {report_err}")

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
