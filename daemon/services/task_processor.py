"""Task processor for message queue redesign - routes tasks to type-specific handlers."""

from __future__ import annotations

import asyncio
import logging
import uuid
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from .event_bus import EventBus
from .main_loop_bridge import MainLoopBridge
from daemon.cancellation import CancellationToken, OperationCancelledError
from daemon.message_models import ToolCallInfo

if TYPE_CHECKING:
    from daemon.repositories.task.models import Task
    from daemon.repositories.task.repository import TaskRepository
    from daemon.repositories.event.repository import EventRepository
    from daemon.services.message_service import MessageService

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
        event_bus: "EventBus | None" = None,
        message_service: "MessageService | None" = None,
    ):
        """Initialize the message processor.

        Args:
            instance_manager: InstanceManager for message processing.
            task_repo: TaskRepository for task operations.
            event_repo: Optional EventRepository for event creation.
            message_repository: Optional MessageQueueRepository for message updates.
            event_bus: Optional EventBus for event creation.
            message_service: Optional MessageService for unified SSE emission.
        """
        self._manager = instance_manager
        self._task_repo = task_repo
        self._event_repo = event_repo
        self._message_repo = message_repository
        self._event_bus = event_bus
        self._message_service = message_service

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
        is_retry = task.retry_count > 0
        
        # Create processing_started event
        if self._event_bus:
            await self._event_bus.create_processing_started_event(
                instance_id=task.instance_id,
                message_id=task.message_id,
            )
        elif self._event_repo:
            await asyncio.to_thread(
                self._event_repo.create_event,
                instance_id=task.instance_id,
                kind="processing_started",
                data={
                    "task_id": task.id,
                    "message_id": task.message_id,
                    "worker_id": task.worker_id,
                    "is_retry": is_retry,
                },
            )
        
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
            )
            
            # NEW: Emit message_completed event with full assistant response
            # This broadcasts the complete message (content, thinking, tool_calls) via SSE
            # NOTE: on_assistant_message_completed also emits processing_completed internally
            # so we don't need a separate create_processing_completed_event call here
            if self._message_service and result:
                tool_calls = None
                if getattr(result, 'tool_calls', None):
                    tool_calls = [
                        ToolCallInfo(
                            id=tc.get("id", str(uuid.uuid4())),
                            name=tc.get("name", ""),
                            arguments=tc.get("arguments", {}),
                            output=tc.get("output"),
                        )
                        for tc in result.tool_calls
                    ]
                
                await self._message_service.on_assistant_message_completed(
                    instance_id=task.instance_id,
                    original_message_id=task.message_id,
                    content=result.content or "",
                    thinking=getattr(result, 'thinking', None),
                    thinking_extracted=getattr(result, 'thinking_extracted', None),
                    tool_calls=tool_calls,
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
        except Exception as e:
            logger.error(f"Failed to process message task {task.id}: {e}", exc_info=True)
            
            # Create error event
            if self._event_bus:
                await self._event_bus.create_error_event(
                    instance_id=task.instance_id,
                    error={
                        "task_id": task.id,
                        "message_id": task.message_id,
                        "error": str(e),
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
        event_bus: "EventBus | None" = None,
    ):
        self._manager = instance_manager
        self._task_repo = task_repo
        self._event_repo = event_repo
        self._event_bus = event_bus

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
        event_bus: "EventBus | None" = None,
    ):
        self._manager = instance_manager
        self._task_repo = task_repo
        self._event_repo = event_repo
        self._event_bus = event_bus

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
        event_bus: "EventBus | None" = None,
        message_service: "MessageService | None" = None,
    ):
        """Initialize the task processor.

        Args:
            task_repo: TaskRepository for task operations.
            instance_manager: InstanceManager for message processing.
            event_repo: Optional EventRepository for event creation.
            event_bus: Optional EventBus for event creation.
            message_service: Optional MessageService for unified SSE emission.
        """
        self._task_repo = task_repo
        self._instance_manager = instance_manager
        self._event_repo = event_repo
        self._event_bus = event_bus
        self._message_service = message_service

        # Create type-specific processors
        self._processors: dict[str, BaseProcessor] = {
            "process_message": ProcessMessageProcessor(
                instance_manager, task_repo, event_repo,
                message_repository=instance_manager._queue_repository,
                event_bus=event_bus,
                message_service=message_service,
            ),
            "send_report": SendReportProcessor(
                instance_manager, task_repo, event_repo,
                event_bus=event_bus,
            ),
            "cleanup": CleanupProcessor(
                instance_manager, task_repo, event_repo,
                event_bus=event_bus,
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
        # No timeout here - let TimeoutMonitor handle it
        return MainLoopBridge.run_async(_run(), timeout=None)

    def get_pending_count(self) -> int:
        """Get the number of pending tasks."""
        return self._task_repo.get_pending_count()
