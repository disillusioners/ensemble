"""Task processor for message queue redesign - routes tasks to type-specific handlers."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from .main_loop_bridge import MainLoopBridge

if TYPE_CHECKING:
    from daemon.repositories.task.models import Task
    from daemon.repositories.task.repository import TaskRepository
    from daemon.repositories.event.repository import EventRepository

logger = logging.getLogger(__name__)


class BaseProcessor(ABC):
    """Base class for task processors."""

    @abstractmethod
    async def process(self, task: "Task") -> dict[str, Any]:
        """Process a task asynchronously.

        Args:
            task: The task to process.

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
    ):
        """Initialize the message processor.

        Args:
            instance_manager: InstanceManager for message processing.
            task_repo: TaskRepository for task operations.
            event_repo: Optional EventRepository for event creation.
            message_repository: Optional MessageQueueRepository for message updates.
        """
        self._manager = instance_manager
        self._task_repo = task_repo
        self._event_repo = event_repo
        self._message_repo = message_repository

    async def process(self, task: "Task") -> dict[str, Any]:
        """Process a message task.

        Args:
            task: The task with message_id to process.

        Returns:
            Result dictionary with processing outcome.
        """
        if not task.message_id:
            raise ValueError(f"Task {task.id} has no message_id")

        logger.info(
            f"Processing message task {task.id}: "
            f"message={task.message_id[:8]}..., instance={task.instance_id[:8]}..."
        )

        try:
            # Get message content for processing
            # Use the task repo to get the message (thread-safe)
            message = None
            if task.message_id:
                message = await asyncio.to_thread(
                    self._task_repo.get_by_message, task.message_id
                )

            # Create processing_started event
            if self._event_repo:
                await asyncio.to_thread(
                    self._event_repo.create_event,
                    instance_id=task.instance_id,
                    kind="processing_started",
                    data={
                        "task_id": task.id,
                        "message_id": task.message_id,
                        "worker_id": task.worker_id,
                    },
                )

            # Process the message via the manager's existing logic
            # This runs the LangGraph and handles all the complexity
            result = await self._manager._process_message_with_tracking(
                instance_id=task.instance_id,
                message=message.content if message else "",
                message_id=task.message_id,
                cancellation_token=None,
                is_retry=False,
            )

            # Create processing_completed event
            if self._event_repo:
                await asyncio.to_thread(
                    self._event_repo.create_event,
                    instance_id=task.instance_id,
                    kind="processing_completed",
                    data={
                        "task_id": task.id,
                        "message_id": task.message_id,
                        "success": True,
                    },
                )

            return {
                "success": True,
                "content": result.content if result else None,
                "message_id": task.message_id,
            }

        except Exception as e:
            logger.error(f"Failed to process message task {task.id}: {e}", exc_info=True)

            # Create error event
            if self._event_repo:
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
    ):
        self._manager = instance_manager
        self._task_repo = task_repo
        self._event_repo = event_repo

    async def process(self, task: "Task") -> dict[str, Any]:
        """Send a completion report to the parent instance.

        Args:
            task: The task with report data.

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

    async def process(self, task: "Task") -> dict[str, Any]:
        """Perform cleanup for an instance.

        Args:
            task: The task with cleanup instructions.

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
    ):
        """Initialize the task processor.

        Args:
            task_repo: TaskRepository for task operations.
            instance_manager: InstanceManager for message processing.
            event_repo: Optional EventRepository for event creation.
        """
        self._task_repo = task_repo
        self._instance_manager = instance_manager
        self._event_repo = event_repo

        # Create type-specific processors
        self._processors: dict[str, BaseProcessor] = {
            "process_message": ProcessMessageProcessor(
                instance_manager, task_repo, event_repo,
                message_repository=instance_manager._queue_repository,
            ),
            "send_report": SendReportProcessor(
                instance_manager, task_repo, event_repo
            ),
            "cleanup": CleanupProcessor(
                instance_manager, task_repo, event_repo
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

    def run_task(self, task: "Task") -> None:
        """Run a task asynchronously via the main event loop.

        This method is called from the worker thread. It uses
        MainLoopBridge to run the async processing code.

        Args:
            task: The task to run.

        Raises:
            Exception: If the task fails.
        """
        processor = self._processors.get(task.task_type)
        if processor is None:
            raise ValueError(f"Unknown task type: {task.task_type}")

        async def _run():
            result = await processor.process(task)
            # Complete the task with result
            self._task_repo.complete_task(task.id, result)
            return result

        # Bridge from worker thread to main event loop
        try:
            result = MainLoopBridge.run_async(_run(), timeout=300.0)
            return result
        except TimeoutError:
            self._task_repo.fail_task(task.id, "Task processing timed out (300s)")
            raise
        except Exception as e:
            self._task_repo.fail_task(task.id, str(e))
            raise

    def get_pending_count(self) -> int:
        """Get the number of pending tasks."""
        return self._task_repo.get_pending_count()
