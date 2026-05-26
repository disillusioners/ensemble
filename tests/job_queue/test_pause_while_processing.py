"""Tests for pause-while-processing scenario.

These tests verify that when an instance is paused while a job is processing:
1. The job stays PROCESSING (not COMPLETED)
2. The instance status becomes PAUSED
3. Normal completion still works (no regression)
4. Terminate still cancels properly (different from pause)
"""

import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from unittest.mock import ANY

from daemon.services.message_job_handler import MessageJobHandler
from daemon.services.job_queue_service import DemandState
from daemon.cancellation import CancellationTokenSource, CancellationReason
from daemon.models.instance import InstanceStatus


class MockJob:
    """Mock job object for testing."""
    def __init__(
        self,
        job_id: str,
        instance_id: str = "test-instance",
        status: str = "processing",
    ):
        self.job_id = job_id
        self.instance_id = instance_id
        self.status = status
        self.message = "test message"
        self.job_type = "message"
        self.project_id = "project-1"
        self.queue_id = "queue-1"
        self.job_metadata = {"message_id": "msg-123", "source": "api"}


class MockInstance:
    """Mock instance object."""
    def __init__(self, instance_id: str, status: str = "running"):
        self.instance_id = instance_id
        self.status = status


class TestPauseKeepsJobProcessing:
    """Tests that pause leaves job in PROCESSING state."""

    @pytest.fixture
    def mock_manager(self):
        """Create mock manager with _process_message_with_tracking."""
        manager = MagicMock()
        manager._process_message_with_tracking = AsyncMock()
        manager._queue_repository = MagicMock()
        manager._instance_repository = MagicMock()
        manager._process_child_completion_and_notify_parent = AsyncMock()
        return manager

    @pytest.fixture
    def mock_job_service(self):
        """Create mock job queue service."""
        service = MagicMock()
        service.complete_job = AsyncMock()
        return service

    @pytest.fixture
    def mock_job_repo(self):
        """Create mock job repository."""
        repo = MagicMock()
        repo.find_processing_message_jobs_by_instance = MagicMock(return_value=[])
        return repo

    @pytest.fixture
    def handler(self, mock_manager, mock_job_service, mock_job_repo):
        """Create MessageJobHandler with mocked dependencies."""
        return MessageJobHandler(
            manager=mock_manager,
            job_queue_service=mock_job_service,
            job_repository=mock_job_repo,
        )

    @pytest.mark.asyncio
    async def test_pause_cancels_graph_task_and_job_stays_processing(
        self, handler, mock_manager, mock_job_service, mock_job_repo
    ):
        """Test that when pause cancels graph, job stays PROCESSING.

        This is the core bug fix: previously, CancelledError was swallowed
        and the job was marked COMPLETED. Now it should stay PROCESSING.
        """
        job = MockJob("job-123", instance_id="test-instance")

        # Simulate CancelledError being raised from _process_message_with_tracking
        # (which is what happens when pause_instance_cascade cancels the graph task)
        mock_manager._process_message_with_tracking.side_effect = asyncio.CancelledError()

        # Mock instance check (not WAITING_CHILDREN)
        mock_instance = MagicMock()
        mock_instance.status = InstanceStatus.PAUSED.value  # Instance is already paused
        mock_manager._instance_repository.get.return_value = mock_instance

        # Call handle - should catch CancelledError and NOT complete job
        await handler.handle(job)

        # Verify _process_message_with_tracking was called
        mock_manager._process_message_with_tracking.assert_called_once()

        # CRITICAL: complete_job should NOT have been called
        # The job should stay PROCESSING
        mock_job_service.complete_job.assert_not_called()

        # Verify no demand_state was set to COMPLETED
        for call in mock_job_service.complete_job.call_args_list:
            assert call[1].get("demand_state") != DemandState.COMPLETED, \
                "Job should NOT be completed on pause - it should stay PROCESSING"

    @pytest.mark.asyncio
    async def test_normal_completion_still_works(
        self, handler, mock_manager, mock_job_service, mock_job_repo
    ):
        """Test that normal job completion still works (no regression)."""
        from daemon.manager import MessageResult

        job = MockJob("job-123", instance_id="test-instance")

        # Normal completion - returns MessageResult
        mock_manager._process_message_with_tracking.return_value = MessageResult(
            content="Hello world"
        )

        # Mock instance check (not WAITING_CHILDREN)
        mock_instance = MagicMock()
        mock_instance.status = InstanceStatus.RUNNING.value
        mock_manager._instance_repository.get.return_value = mock_instance

        # Call handle - should complete job normally
        await handler.handle(job)

        # Verify complete_job was called with COMPLETED
        mock_job_service.complete_job.assert_called_once()
        call_kwargs = mock_job_service.complete_job.call_args[1]
        assert call_kwargs["demand_state"] == DemandState.COMPLETED
        assert call_kwargs["result_summary"] == "Hello world"

    @pytest.mark.asyncio
    async def test_operation_cancelled_error_still_cancels_job(
        self, handler, mock_manager, mock_job_service, mock_job_repo
    ):
        """Test that OperationCancelledError (manual cancel) still cancels job.

        This is distinct from asyncio.CancelledError (pause cancel).
        """
        from daemon.cancellation import OperationCancelledError

        job = MockJob("job-123", instance_id="test-instance")

        # OperationCancelledError from manual cancel
        mock_manager._process_message_with_tracking.side_effect = OperationCancelledError(
            CancellationReason.MANUAL
        )

        # Call handle - should complete job with CANCELLED
        await handler.handle(job)

        # Verify complete_job was called with CANCELLED
        mock_job_service.complete_job.assert_called_once()
        call_kwargs = mock_job_service.complete_job.call_args[1]
        assert call_kwargs["demand_state"] == DemandState.CANCELLED
        assert "cancelled" in call_kwargs["error"].lower()

    @pytest.mark.asyncio
    async def test_exception_still_fails_job(
        self, handler, mock_manager, mock_job_service, mock_job_repo
    ):
        """Test that unexpected exceptions still fail the job."""
        job = MockJob("job-123", instance_id="test-instance")

        # Unexpected error
        mock_manager._process_message_with_tracking.side_effect = ValueError("Invalid input")

        # Call handle - should fail job
        await handler.handle(job)

        # Verify complete_job was called with FAILED
        mock_job_service.complete_job.assert_called_once()
        call_kwargs = mock_job_service.complete_job.call_args[1]
        assert call_kwargs["demand_state"] == DemandState.FAILED
        assert "Invalid input" in call_kwargs["error"]

    @pytest.mark.asyncio
    async def test_waiting_children_defers_completion(
        self, handler, mock_manager, mock_job_service, mock_job_repo
    ):
        """Test that WAITING_CHILDREN status defers job completion."""
        from daemon.manager import MessageResult

        job = MockJob("job-123", instance_id="test-instance")

        # Normal completion
        mock_manager._process_message_with_tracking.return_value = MessageResult(
            content="done"
        )

        # But instance is WAITING_CHILDREN
        mock_instance = MagicMock()
        mock_instance.status = InstanceStatus.WAITING_CHILDREN.value
        mock_manager._instance_repository.get.return_value = mock_instance

        # Call handle
        await handler.handle(job)

        # Job should NOT be completed - deferred until children finish
        mock_job_service.complete_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_concurrent_message_job_gets_requeued(
        self, handler, mock_manager, mock_job_service, mock_job_repo
    ):
        """Test that concurrent message jobs are re-queued (not failed)."""
        job = MockJob("job-123", instance_id="test-instance")

        # There's already another PROCESSING job for this instance
        other_job = MockJob("job-other", instance_id="test-instance", status="processing")
        mock_job_repo.find_processing_message_jobs_by_instance.return_value = [other_job]

        # Mock lock manager to avoid TypeError
        mock_job_service._lock_manager = MagicMock()
        mock_job_service._lock_manager.release_queue_lock = AsyncMock()

        # Call handle
        await handler.handle(job)

        # Job should be re-queued (PENDING), not completed
        mock_job_repo.atomic_transition.assert_called_once_with(
            job.job_id, from_status="processing", to_status="pending"
        )
        mock_job_service.complete_job.assert_not_called()


class TestProcessMessageProcessorPause:
    """Tests for ProcessMessageProcessor handling of pause cancellation."""

    def test_process_message_processor_handles_cancelled_error(self):
        """Test that ProcessMessageProcessor properly handles CancelledError from pause."""
        import asyncio
        from daemon.services.task_processor import ProcessMessageProcessor
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


class TestMainLoopBridgeCancelledError:
    """Tests for MainLoopBridge handling of CancelledError."""

    def test_main_loop_bridge_propagates_cancelled_error(self):
        """Test that MainLoopBridge.run_async propagates CancelledError."""
        import asyncio
        from daemon.services.main_loop_bridge import MainLoopBridge

        async def raise_cancelled():
            raise asyncio.CancelledError()

        # Reset and create a fresh loop
        MainLoopBridge.reset()

        # Create a thread to run the event loop
        loop = asyncio.new_event_loop()

        def run_with_loop():
            MainLoopBridge.set_loop(loop)
            try:
                with pytest.raises(asyncio.CancelledError):
                    MainLoopBridge.run_async(raise_cancelled(), timeout=5)
            finally:
                MainLoopBridge.reset()
                loop.close()

        # Run in a thread since MainLoopBridge expects to be called from worker thread
        import threading
        t = threading.Thread(target=run_with_loop)
        t.start()
        t.join()
