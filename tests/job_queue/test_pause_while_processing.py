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
    def mock_manager(self, monkeypatch):
        """Create mock manager with _process_message_with_tracking."""
        manager = MagicMock()
        manager._process_message_with_tracking = AsyncMock()
        manager._queue_repository = MagicMock()
        manager._instance_repository = MagicMock()
        manager._process_child_completion_and_notify_parent = AsyncMock()
        # Execution Gate stub: transparent, runs the work.
        from daemon.services.execution_gate import ExecutionGateService

        async def _passthrough(*args, **kwargs):
            work_fn = kwargs.get("work_fn")
            return await work_fn()

        gate = MagicMock(spec=ExecutionGateService)
        gate.run = AsyncMock(side_effect=_passthrough)
        manager.execution_gate = gate
        # Cross-dispatcher pre-flight now reads
        # ``TaskRepository.find_running_by_instance`` via
        # ``manager._task_repo``. Stub the repo so the handler takes
        # the happy path.
        task_repo_stub = MagicMock()
        task_repo_stub.find_running_by_instance = MagicMock(return_value=None)
        manager._task_repo = task_repo_stub
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

        # But instance is WAITING_CHILDREN with children pending
        mock_instance = MagicMock()
        mock_instance.status = InstanceStatus.WAITING_CHILDREN.value
        mock_instance.waiting_for = 1
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


class TestPauseVsShutdownDistinction:
    """Tests that distinguish pause cancellation from shutdown cancellation."""

    @pytest.fixture(autouse=True)
    def _patch_running_task_check(self, monkeypatch):
        """Stub the cross-dispatcher task check to return None.

        The default MagicMock engine returns a truthy value from the
        SQL query, which would trigger the re-queue-for-contention
        path. The tests in this class don't exercise that path — they
        exercise the gate's CancelledError → pause/terminate
        discrimination — so we patch the repository method to return
        None via the manager's ``_task_repo`` attribute.
        """
        # Autouse for this class — patches ``TaskRepository.find_running_by_instance``
        # globally on the class itself (any instance, including the
        # ad-hoc ``MagicMock()`` managers created inside the test
        # bodies) so the SQL short-circuits to None.
        from daemon.repositories.task.repository import TaskRepository
        monkeypatch.setattr(
            TaskRepository, "find_running_by_instance",
            lambda self, instance_id: None,
        )

    @pytest.mark.asyncio
    async def test_message_job_handler_pause_leaves_processing(self):
        """Test that CancelledError with PAUSED instance leaves job PROCESSING."""
        from daemon.services.execution_gate import ExecutionGateService
        handler = MessageJobHandler(
            manager=MagicMock(),
            job_queue_service=MagicMock(),
            job_repository=MagicMock(),
        )
        # No task_repo on this ad-hoc mock — the handler logs a
        # warning and skips the cross-dispatcher pre-flight (the
        # Gate's try_acquire is still the authoritative safety net).
        handler._manager._task_repo = None

        job = MockJob("job-123", instance_id="test-instance")

        # Mock manager to raise CancelledError
        handler._manager._process_message_with_tracking = AsyncMock(
            side_effect=asyncio.CancelledError()
        )
        handler._manager._queue_repository = MagicMock()
        handler._manager._instance_repository = MagicMock()
        handler._manager._process_child_completion_and_notify_parent = AsyncMock()
        # Transparent Execution Gate (the work raises CancelledError,
        # which is what this test is exercising).
        async def _passthrough(*args, **kwargs):
            work_fn = kwargs.get("work_fn")
            return await work_fn()
        gate = MagicMock(spec=ExecutionGateService)
        gate.run = AsyncMock(side_effect=_passthrough)
        handler._manager.execution_gate = gate

        # Instance is PAUSED
        mock_instance = MagicMock()
        mock_instance.status = InstanceStatus.PAUSED.value
        handler._manager._instance_repository.get.return_value = mock_instance

        # Call handle
        await handler.handle(job)

        # Job should NOT be completed - stays PROCESSING
        handler._job_service.complete_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_message_job_handler_shutdown_propagates_cancelled_error(self):
        """Test that CancelledError with RUNNING instance propagates and completes job as CANCELLED."""
        from daemon.services.execution_gate import ExecutionGateService
        handler = MessageJobHandler(
            manager=MagicMock(),
            job_queue_service=MagicMock(),
            job_repository=MagicMock(),
        )
        # No task_repo on this ad-hoc mock — see the previous test.
        handler._manager._task_repo = None

        job = MockJob("job-123", instance_id="test-instance")

        # Mock manager to raise CancelledError
        handler._manager._process_message_with_tracking = AsyncMock(
            side_effect=asyncio.CancelledError()
        )
        handler._manager._queue_repository = MagicMock()
        handler._manager._instance_repository = MagicMock()
        handler._manager._process_child_completion_and_notify_parent = AsyncMock()
        # Transparent Execution Gate: the work raises CancelledError,
        # which is what this test is exercising.
        async def _passthrough(*args, **kwargs):
            work_fn = kwargs.get("work_fn")
            return await work_fn()
        gate = MagicMock(spec=ExecutionGateService)
        gate.run = AsyncMock(side_effect=_passthrough)
        handler._manager.execution_gate = gate

        # Instance is RUNNING (not paused - simulating shutdown scenario)
        mock_instance = MagicMock()
        mock_instance.status = InstanceStatus.RUNNING.value
        handler._manager._instance_repository.get.return_value = mock_instance

        # Call handle - should raise CancelledError
        with pytest.raises(asyncio.CancelledError):
            await handler.handle(job)

        # complete_job SHOULD be called with CANCELLED state
        handler._job_service.complete_job.assert_called_once_with(
            'job-123',
            demand_state=DemandState.CANCELLED,
            error="Message processing cancelled (instance terminated)"
        )

    def test_process_message_processor_pause_raises_cancelled_error(self):
        """Test that ProcessMessageProcessor raises CancelledError when paused."""
        import asyncio
        from daemon.services.task_processor import ProcessMessageProcessor
        from daemon.services.execution_gate import ExecutionGateService

        task = MagicMock()
        task.id = "task-123"
        task.message_id = "msg-123"
        task.instance_id = "test-instance"
        task.retry_count = 0

        mock_manager = MagicMock()
        mock_manager._process_message_with_tracking = AsyncMock(
            side_effect=asyncio.CancelledError()
        )
        mock_manager._instance_repository = MagicMock()
        mock_manager._event_bus = None
        # Transparent Execution Gate: the work raises CancelledError,
        # which is what this test is exercising.
        async def _passthrough(*args, **kwargs):
            work_fn = kwargs.get("work_fn")
            return await work_fn()
        gate = MagicMock(spec=ExecutionGateService)
        gate.run = AsyncMock(side_effect=_passthrough)
        mock_manager.execution_gate = gate

        # Instance is PAUSED
        mock_instance = MagicMock()
        mock_instance.status = InstanceStatus.PAUSED.value
        mock_manager._instance_repository.get.return_value = mock_instance

        processor = ProcessMessageProcessor(
            instance_manager=mock_manager,
            task_repo=MagicMock(),
            event_repo=None,
            message_repository=MagicMock(),
            source_dispatcher=None,
        )

        # Should raise CancelledError (to propagate to worker thread)
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(processor.process(task))

    def test_process_message_processor_shutdown_raises_cancelled_error(self):
        """Test that ProcessMessageProcessor raises CancelledError when shutting down (not paused)."""
        import asyncio
        from daemon.services.task_processor import ProcessMessageProcessor
        from daemon.services.execution_gate import ExecutionGateService

        task = MagicMock()
        task.id = "task-123"
        task.message_id = "msg-123"
        task.instance_id = "test-instance"
        task.retry_count = 0

        mock_manager = MagicMock()
        mock_manager._process_message_with_tracking = AsyncMock(
            side_effect=asyncio.CancelledError()
        )
        mock_manager._instance_repository = MagicMock()
        mock_manager._event_bus = None
        # Transparent Execution Gate: the work raises CancelledError,
        # which is what this test is exercising.
        async def _passthrough(*args, **kwargs):
            work_fn = kwargs.get("work_fn")
            return await work_fn()
        gate = MagicMock(spec=ExecutionGateService)
        gate.run = AsyncMock(side_effect=_passthrough)
        mock_manager.execution_gate = gate

        # Instance is RUNNING (not paused - simulating shutdown)
        mock_instance = MagicMock()
        mock_instance.status = InstanceStatus.RUNNING.value
        mock_manager._instance_repository.get.return_value = mock_instance

        processor = ProcessMessageProcessor(
            instance_manager=mock_manager,
            task_repo=MagicMock(),
            event_repo=None,
            message_repository=MagicMock(),
            source_dispatcher=None,
        )

        # Should also raise CancelledError (not treating as pause)
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(processor.process(task))
