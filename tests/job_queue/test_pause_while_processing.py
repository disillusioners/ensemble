"""Tests for pause-while-processing in the unified WorkerPool path.

These tests verify that when an instance is paused or terminated while a
task is processing through the WorkerPool path (the only path after
Phase D removed the MessageJobHandler):

1. The WP path's ``ProcessMessageProcessor`` propagates
   ``asyncio.CancelledError`` (it does not discriminate pause-vs-terminate
   the way the legacy JQ path did — the worker pool decides via the
   cancellation reason).
2. The Worker's ``_process_with_timeout`` does not mark tasks as failed
   on ``concurrent.futures.CancelledError``.
"""

import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock


class TestProcessMessageProcessorPause:
    """Tests for ProcessMessageProcessor handling of pause cancellation."""

    def test_process_message_processor_handles_cancelled_error(self):
        """Test that ProcessMessageProcessor properly handles CancelledError from pause."""
        import asyncio
        from daemon.services.task_processor import ProcessMessageProcessor
        from daemon.services.execution_gate import ExecutionGateService
        from unittest.mock import MagicMock, AsyncMock

        # Create mock task
        task = MagicMock()
        task.id = "task-123"
        task.message_id = "msg-123"
        task.instance_id = "test-instance"
        task.retry_count = 0

        # Create mock manager that raises CancelledError
        mock_manager = MagicMock()
        mock_manager._process_message_with_tracking = AsyncMock(
            side_effect=asyncio.CancelledError()
        )
        mock_manager._instance_repository = MagicMock()
        mock_manager._event_bus = None
        # Transparent Execution Gate: the work raises CancelledError,
        # which is exactly what we want to test.
        async def _passthrough(*args, **kwargs):
            work_fn = kwargs.get("work_fn")
            return await work_fn()
        gate = MagicMock(spec=ExecutionGateService)
        gate.run = AsyncMock(side_effect=_passthrough)
        mock_manager.execution_gate = gate

        # Create processor
        processor = ProcessMessageProcessor(
            instance_manager=mock_manager,
            task_repo=MagicMock(),
            event_repo=None,
            message_repository=MagicMock(),
            source_dispatcher=None,
        )

        # Run process - should raise CancelledError
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(processor.process(task))

        # Task should NOT be marked completed
        processor._task_repo.complete_task.assert_not_called()


class TestWorkerPoolPathCancellation:
    """Tests for WorkerPool handling of concurrent.futures.CancelledError."""

    def test_process_with_timeout_handles_concurrent_futures_cancelled_error(self):
        """Test that _process_with_timeout doesn't fail task on concurrent.futures.CancelledError."""
        import concurrent.futures
        from unittest.mock import MagicMock, AsyncMock, patch
        from daemon.services.worker_pool import Worker

        # Create a mock task
        mock_task = MagicMock()
        mock_task.id = "task-123"
        mock_task.instance_id = "test-instance"
        mock_task.task_type = "process_message"
        mock_task.message_id = "msg-123"

        # Create a mock task processor that raises concurrent.futures.CancelledError
        mock_processor = MagicMock()
        mock_processor._task_repo = MagicMock()
        mock_processor._task_repo.schedule_retry = MagicMock(return_value=None)

        # Create worker with mocks
        worker = Worker(
            worker_id="test-worker",
            task_processor=mock_processor,
            worker_pool=MagicMock(),
        )

        # Mock run_task to raise concurrent.futures.CancelledError
        mock_processor.run_task = MagicMock(
            side_effect=concurrent.futures.CancelledError()
        )

        # Process task - should NOT raise and should NOT fail the task
        worker._process_with_timeout(mock_task)

        # Verify: task should NOT be marked as failed
        mock_processor._task_repo.fail_task.assert_not_called()
        mock_processor._task_repo.schedule_retry.assert_not_called()
        mock_processor._task_repo.cancel_task.assert_not_called()

        # _tasks_completed and _tasks_failed should NOT be incremented
        assert worker._tasks_completed == 0
        assert worker._tasks_failed == 0
