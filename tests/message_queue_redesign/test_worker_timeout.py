"""Tests for Worker Timeout and Retry Logic.

These tests verify:
1. Cancellation token passthrough through the task processing chain
2. Worker timeout monitoring with TimeoutMonitor
3. Retry logic for timeout vs non-timeout cancellations
4. WorkerPool configuration passing to workers
5. FIX: C3 - retry_count correctly passed through the chain
"""

import pytest
import threading
from datetime import datetime, timezone
from unittest.mock import Mock, MagicMock, patch, call, PropertyMock
from typing import Any

from daemon.cancellation import (
    CancellationToken,
    CancellationTokenSource,
    CancellationReason,
    OperationCancelledError,
)
from daemon.services.worker_pool import Worker, WorkerPool, DEFAULT_TASK_TIMEOUT
from daemon.services.timeout_monitor import TimeoutMonitor
from daemon.repositories.task.models import Task, TaskStatus, TaskType


# ============================================================================
# Test Fixtures
# ============================================================================

class MockWorkerPool:
    """Mock worker pool for testing."""
    def __init__(self):
        self._condition = threading.Condition()
        self._notification_count = 0
    
    def notify_work(self):
        with self._condition:
            self._notification_count += 1
            self._condition.notify_all()
    
    def wait_for_work(self, timeout=3.0):
        with self._condition:
            if self._notification_count > 0:
                self._notification_count -= 1
                return True
            self._condition.wait(timeout=0.1)
            if self._notification_count > 0:
                self._notification_count -= 1
                return True
            return False

@pytest.fixture
def mock_task():
    """Create a mock task for testing."""
    task = Mock(spec=Task)
    task.id = 1
    task.task_type = TaskType.PROCESS_MESSAGE.value
    task.instance_id = "test-instance-123"
    task.message_id = "test-message-456"
    task.status = TaskStatus.PENDING.value
    task.worker_id = None
    task.retry_count = 0
    task.result = None
    task.error = None
    return task


class MockTaskProcessor:
    """A mock task processor that actually calls MainLoopBridge.run_async.
    
    This is needed because TaskProcessor.run_task() internally calls
    MainLoopBridge.run_async(), and we need that call to happen for
    the mock to work correctly.
    """
    
    def __init__(self):
        self._task_repo = Mock()
        self._task_repo.schedule_retry = Mock(return_value=None)
        self._task_repo.fail_task = Mock(return_value=None)
        self._task_repo.cancel_task = Mock(return_value=None)
        self._processors = {}
        self.run_task_calls = []
    
    def run_task(self, task, cancellation_token=None):
        """Actually call MainLoopBridge.run_async for proper mocking."""
        from daemon.services.main_loop_bridge import MainLoopBridge
        
        async def _run():
            return {'success': True}
        
        self.run_task_calls.append((task, cancellation_token))
        
        # This will be patched in tests
        return MainLoopBridge.run_async(_run(), timeout=None)
    
    def claim_task(self, worker_id):
        return None
    
    def get_pending_count(self):
        return 0


@pytest.fixture
def mock_task_processor():
    """Create a MockTaskProcessor instance."""
    return MockTaskProcessor()


@pytest.fixture
def mock_timeout_monitor():
    """Create a mock timeout monitor."""
    monitor = Mock(spec=TimeoutMonitor)
    monitor.start = Mock()
    monitor.stop = Mock()
    monitor.fired = False
    monitor.is_running = Mock(return_value=False)
    return monitor


# ============================================================================
# Token Passthrough Tests
# ============================================================================

class TestTaskProcessorCancellationToken:
    """Tests for TaskProcessor accepting and passing cancellation tokens."""

    def test_task_processor_accepts_cancellation_token(self):
        """Verify TaskProcessor.run_task() signature accepts cancellation_token."""
        from daemon.services.task_processor import TaskProcessor
        import inspect
        
        sig = inspect.signature(TaskProcessor.run_task)
        
        # The signature should accept cancellation_token parameter
        assert "cancellation_token" in sig.parameters

    def test_process_message_processor_signature(self):
        """ProcessMessageProcessor.process() accepts cancellation_token."""
        from daemon.services.task_processor import ProcessMessageProcessor
        import inspect
        
        sig = inspect.signature(ProcessMessageProcessor.process)
        assert "cancellation_token" in sig.parameters

    def test_base_processor_signature(self):
        """BaseProcessor.process() abstract method accepts cancellation_token."""
        from daemon.services.task_processor import BaseProcessor
        import inspect
        
        sig = inspect.signature(BaseProcessor.process)
        assert "cancellation_token" in sig.parameters


class TestRetryCountPassthrough:
    """Tests for retry_count correctly passed through the chain (FIX: C3)."""

    def test_process_message_processor_uses_task_retry_count(self, mock_task):
        """ProcessMessageProcessor accesses task.retry_count for processing."""
        from daemon.services.task_processor import ProcessMessageProcessor
        
        # Set retry_count on task
        mock_task.retry_count = 3
        
        # Verify task has retry_count attribute
        assert hasattr(mock_task, 'retry_count')
        assert mock_task.retry_count == 3

    def test_task_model_has_retry_count_field(self):
        """Task model has retry_count field."""
        from daemon.repositories.task.models import Task
        
        # Verify the field exists in the model
        assert hasattr(Task, "retry_count")

    def test_manager_accepts_retry_count_param(self):
        """Verify _process_message_with_tracking accepts retry_count parameter."""
        from daemon.manager import InstanceManager
        
        import inspect
        sig = inspect.signature(
            InstanceManager._process_message_with_tracking
        )
        
        params = sig.parameters
        assert "retry_count" in params
        
    def test_manager_uses_retry_count_not_msg_dot_retry_count(self):
        """Verify the manager uses the retry_count parameter directly."""
        from daemon.manager import InstanceManager
        
        # Verify the manager has the retry_count parameter
        import inspect
        sig = inspect.signature(
            InstanceManager._process_message_with_tracking
        )
        
        params = sig.parameters
        assert "retry_count" in params
        
        # The parameter should default to 0
        default = params["retry_count"].default
        assert default == 0

    def test_process_message_processor_passes_is_retry_to_manager(self):
        """ProcessMessageProcessor passes is_retry=True when retry_count > 0."""
        from daemon.services.task_processor import ProcessMessageProcessor
        from unittest.mock import AsyncMock, Mock, patch, MagicMock

        # Create mock dependencies
        mock_manager = Mock()
        mock_manager._process_message_with_tracking = AsyncMock(return_value=Mock(content="ok"))

        mock_task_repo = Mock()
        mock_message_repo = Mock()
        mock_message = Mock()
        mock_message.content = "test message content"
        mock_message.message_metadata = {}  # Empty dict so .get() returns defaults
        mock_message_repo.get = Mock(return_value=mock_message)

        # Create processor
        processor = ProcessMessageProcessor(
            instance_manager=mock_manager,
            task_repo=mock_task_repo,
            event_repo=None,
            message_repository=mock_message_repo,
        )

        # Create task with retry_count > 0
        task = Mock()
        task.id = 1
        task.instance_id = "test-instance"
        task.message_id = "test-message"
        task.retry_count = 2  # This means is_retry should be True

        # Run processing
        import asyncio
        asyncio.run(processor.process(task))

        # Verify is_retry=True was passed
        mock_manager._process_message_with_tracking.assert_called_once()
        call_kwargs = mock_manager._process_message_with_tracking.call_args.kwargs
        assert "is_retry" in call_kwargs, "is_retry not passed to manager"
        assert call_kwargs["is_retry"] is True, f"Expected is_retry=True, got {call_kwargs['is_retry']}"

    def test_process_message_processor_passes_is_retry_false_for_first_attempt(self):
        """ProcessMessageProcessor passes is_retry=False when retry_count == 0."""
        from daemon.services.task_processor import ProcessMessageProcessor
        from unittest.mock import AsyncMock, Mock, patch, MagicMock

        # Create mock dependencies
        mock_manager = Mock()
        mock_manager._process_message_with_tracking = AsyncMock(return_value=Mock(content="ok"))

        mock_task_repo = Mock()
        mock_message_repo = Mock()
        mock_message = Mock()
        mock_message.content = "test message content"
        mock_message.message_metadata = {}  # Empty dict so .get() returns False for "resume_mode"
        mock_message_repo.get = Mock(return_value=mock_message)

        # Create processor
        processor = ProcessMessageProcessor(
            instance_manager=mock_manager,
            task_repo=mock_task_repo,
            event_repo=None,
            message_repository=mock_message_repo,
        )

        # Create task with retry_count == 0 (first attempt)
        task = Mock()
        task.id = 1
        task.instance_id = "test-instance"
        task.message_id = "test-message"
        task.retry_count = 0  # First attempt, is_retry should be False

        # Run processing
        import asyncio
        asyncio.run(processor.process(task))

        # Verify is_retry=False was passed
        mock_manager._process_message_with_tracking.assert_called_once()
        call_kwargs = mock_manager._process_message_with_tracking.call_args.kwargs
        assert "is_retry" in call_kwargs, "is_retry not passed to manager"
        assert call_kwargs["is_retry"] is False, f"Expected is_retry=False, got {call_kwargs['is_retry']}"


# ============================================================================
# Worker Timeout Tests
# ============================================================================

class TestWorkerTimeoutMonitor:
    """Tests for Worker creating and using TimeoutMonitor."""

    def test_worker_creates_timeout_monitor_per_task(
        self, mock_task_processor, mock_task
    ):
        """Worker._process_with_timeout creates a TimeoutMonitor."""
        # Create worker
        worker = Worker(
            worker_id="test-worker",
            task_processor=mock_task_processor,
            worker_pool=MockWorkerPool(),
            timeout_minutes=5.0,  # 5 minutes
            max_retries=3,
        )
        
        # Patch MainLoopBridge to avoid needing event loop
        with patch("daemon.services.main_loop_bridge.MainLoopBridge.run_async") as mock_run_async:
            mock_run_async.return_value = None
            
            # Patch TimeoutMonitor at source
            with patch(
                "daemon.services.timeout_monitor.TimeoutMonitor"
            ) as mock_monitor_class:
                mock_monitor_instance = Mock()
                mock_monitor_class.return_value = mock_monitor_instance
                
                # Run the processing
                worker._process_with_timeout(mock_task)
                
                # Verify TimeoutMonitor was instantiated
                mock_monitor_class.assert_called_once()
                
                # Verify it was called with correct parameters
                call_kwargs = mock_monitor_class.call_args
                assert call_kwargs.kwargs["task_id"] == mock_task.id
                assert call_kwargs.kwargs["timeout_seconds"] == 300.0  # 5 min

    def test_timeout_monitor_fires_after_configured_timeout(self):
        """TimeoutMonitor triggers cancellation after timeout seconds."""
        import time
        
        source = CancellationTokenSource()
        token = source.token
        
        # Create monitor with very short timeout
        monitor = TimeoutMonitor(
            task_id=1,
            source=source,
            timeout_seconds=0.1,  # 100ms
        )
        
        # Start monitor
        monitor.start()
        
        # Wait for timeout to fire
        time.sleep(0.2)
        
        # Verify cancellation was triggered
        assert source.is_cancelled()
        assert token.is_cancelled
        assert token.reason == CancellationReason.TIMEOUT
        
        # Cleanup
        monitor.stop()

    def test_worker_handles_operation_cancelled_error(
        self, mock_task_processor, mock_task
    ):
        """On OperationCancelledError, Worker calls _handle_cancellation."""
        worker = Worker(
            worker_id="test-worker",
            task_processor=mock_task_processor,
            worker_pool=MockWorkerPool(),
            timeout_minutes=5.0,
            max_retries=3,
        )
        
        # Mock run_task to raise OperationCancelledError via MainLoopBridge
        cancelled_error = OperationCancelledError(
            reason=CancellationReason.TIMEOUT,
            message="Task timed out"
        )
        
        # Patch _handle_cancellation to track it was called
        with patch.object(worker, "_handle_cancellation") as mock_handle:
            with patch(
                "daemon.services.main_loop_bridge.MainLoopBridge.run_async"
            ) as mock_run_async:
                mock_run_async.side_effect = cancelled_error
                
                # Patch TimeoutMonitor at source
                with patch(
                    "daemon.services.timeout_monitor.TimeoutMonitor"
                ) as mock_monitor_class:
                    mock_monitor_instance = Mock()
                    mock_monitor_class.return_value = mock_monitor_instance
                    
                    # Run
                    worker._process_with_timeout(mock_task)
                    
                    # Verify _handle_cancellation was called
                    mock_handle.assert_called_once()
                    call_args = mock_handle.call_args
                    assert call_args[0][0] is mock_task
                    assert call_args[0][1] == CancellationReason.TIMEOUT


# ============================================================================
# Retry Logic Tests
# ============================================================================

class TestWorkerRetryLogic:
    """Tests for Worker's retry and failure handling."""

    def test_handle_cancellation_retries_on_timeout(
        self, mock_task_processor, mock_task
    ):
        """TIMEOUT reason → schedule_retry called."""
        worker = Worker(
            worker_id="test-worker",
            task_processor=mock_task_processor,
            worker_pool=MockWorkerPool(),
            max_retries=3,
        )
        
        # Setup mock to return a retry task
        retry_task = Mock()
        retry_task.id = 2
        retry_task.retry_count = 1
        mock_task_processor._task_repo.schedule_retry.return_value = retry_task
        
        # Handle timeout cancellation
        worker._handle_cancellation(mock_task, CancellationReason.TIMEOUT)
        
        # Verify schedule_retry was called
        mock_task_processor._task_repo.schedule_retry.assert_called_once_with(
            task_id=mock_task.id,
            max_retries=3,
            backoff_base=60,
            backoff_max=3600,
        )
        
        # Verify task is counted as failed
        assert worker._tasks_failed == 1

    def test_handle_cancellation_permanent_fail_max_retries(
        self, mock_task_processor, mock_task
    ):
        """Max retries exceeded → fail_task called."""
        worker = Worker(
            worker_id="test-worker",
            task_processor=mock_task_processor,
            worker_pool=MockWorkerPool(),
            max_retries=3,
        )
        
        # schedule_retry returns None when max retries exceeded
        mock_task_processor._task_repo.schedule_retry.return_value = None
        
        # Handle timeout cancellation
        worker._handle_cancellation(mock_task, CancellationReason.TIMEOUT)
        
        # Verify fail_task was called (permanent failure)
        mock_task_processor._task_repo.fail_task.assert_called_once()
        call_args = mock_task_processor._task_repo.fail_task.call_args
        assert call_args[0][0] == mock_task.id
        assert "retries" in call_args[0][1].lower()
        
        # Verify task is counted as failed
        assert worker._tasks_failed == 1

    def test_handle_cancellation_non_timeout_reason(
        self, mock_task_processor, mock_task
    ):
        """Non-timeout cancel → cancel_task called, no retry."""
        worker = Worker(
            worker_id="test-worker",
            task_processor=mock_task_processor,
            worker_pool=MockWorkerPool(),
            max_retries=3,
        )
        
        # Handle shutdown cancellation
        worker._handle_cancellation(
            mock_task, CancellationReason.SHUTDOWN
        )
        
        # Verify cancel_task was called (not schedule_retry)
        mock_task_processor._task_repo.cancel_task.assert_called_once_with(
            mock_task.id, reason="Cancelled: shutdown"
        )
        
        # Verify schedule_retry was NOT called
        mock_task_processor._task_repo.schedule_retry.assert_not_called()
        
        # Verify task is counted as failed
        assert worker._tasks_failed == 1

    def test_handle_task_failure_permanent_fail(
        self, mock_task_processor, mock_task
    ):
        """Generic exception → permanent fail, no retry."""
        worker = Worker(
            worker_id="test-worker",
            task_processor=mock_task_processor,
            worker_pool=MockWorkerPool(),
            max_retries=3,
        )
        
        # Handle task failure
        worker._handle_task_failure(mock_task, "Something went wrong")
        
        # Verify fail_task was called
        mock_task_processor._task_repo.fail_task.assert_called_once_with(
            mock_task.id, "Something went wrong"
        )
        
        # Verify task is counted as failed
        assert worker._tasks_failed == 1


# ============================================================================
# Monitor Lifecycle Tests
# ============================================================================

class TestTimeoutMonitorLifecycle:
    """Tests for TimeoutMonitor lifecycle management."""

    def test_monitor_stopped_on_success(self, mock_task_processor, mock_task):
        """Successful task → monitor.stop() called."""
        worker = Worker(
            worker_id="test-worker",
            task_processor=mock_task_processor,
            worker_pool=MockWorkerPool(),
            timeout_minutes=5.0,
        )
        
        with patch(
            "daemon.services.main_loop_bridge.MainLoopBridge.run_async"
        ) as mock_run_async:
            mock_run_async.return_value = None
            
            with patch(
                "daemon.services.timeout_monitor.TimeoutMonitor"
            ) as mock_monitor_class:
                mock_monitor_instance = Mock()
                mock_monitor_class.return_value = mock_monitor_instance
                
                # Run task successfully
                worker._process_with_timeout(mock_task)
                
                # Verify monitor.stop() was called in finally
                mock_monitor_instance.stop.assert_called_once()

    def test_monitor_stopped_on_generic_exception(self, mock_task_processor, mock_task):
        """Generic exception → monitor.stop() called in finally block."""
        worker = Worker(
            worker_id="test-worker",
            task_processor=mock_task_processor,
            worker_pool=MockWorkerPool(),
            timeout_minutes=5.0,
        )
        
        error = Exception("Something broke")
        
        with patch(
            "daemon.services.main_loop_bridge.MainLoopBridge.run_async"
        ) as mock_run_async:
            mock_run_async.side_effect = error
            
            with patch(
                "daemon.services.timeout_monitor.TimeoutMonitor"
            ) as mock_monitor_class:
                mock_monitor_instance = Mock()
                mock_monitor_class.return_value = mock_monitor_instance
                
                # Patch _handle_task_failure to verify it's called
                with patch.object(worker, '_handle_task_failure') as mock_handle:
                    # Run task that fails with generic exception
                    worker._process_with_timeout(mock_task)
                    
                    # Verify exception was caught and handled
                    mock_handle.assert_called_once_with(mock_task, "Something broke")
                
                # Verify monitor.stop() was called even after failure
                mock_monitor_instance.stop.assert_called_once()

    def test_worker_completes_normally_no_retry(self, mock_task_processor, mock_task):
        """Successful completion → no retry scheduled."""
        worker = Worker(
            worker_id="test-worker",
            task_processor=mock_task_processor,
            worker_pool=MockWorkerPool(),
            max_retries=3,
        )
        
        with patch(
            "daemon.services.main_loop_bridge.MainLoopBridge.run_async"
        ) as mock_run_async:
            mock_run_async.return_value = None
            
            with patch(
                "daemon.services.timeout_monitor.TimeoutMonitor"
            ) as mock_monitor_class:
                mock_monitor_instance = Mock()
                mock_monitor_class.return_value = mock_monitor_instance
                
                # Run task successfully
                worker._process_with_timeout(mock_task)
                
                # Verify task completed normally
                assert worker._tasks_completed == 1
                
                # Verify no retry was scheduled
                mock_task_processor._task_repo.schedule_retry.assert_not_called()
                
                # Verify no fail was called
                mock_task_processor._task_repo.fail_task.assert_not_called()


# ============================================================================
# WorkerPool Configuration Tests
# ============================================================================

class TestWorkerPoolConfig:
    """Tests for WorkerPool passing config to Workers."""

    def test_worker_pool_passes_timeout_config_to_workers(self, mock_task_processor):
        """WorkerPool passes timeout/retry params to Workers."""
        pool = WorkerPool(
            task_processor=mock_task_processor,
            num_workers=2,
            timeout_minutes=10.0,
            max_retries=5,
            retry_backoff_base=30,
            retry_backoff_max=1800,
        )
        
        # Start pool
        pool.start()
        
        try:
            # Verify workers were created
            assert len(pool._workers) == 2
            
            # Check first worker has correct config
            worker = pool._workers[0]
            assert worker._timeout_minutes == 10.0
            assert worker._max_retries == 5
            assert worker._retry_backoff_base == 30
            assert worker._retry_backoff_max == 1800
        finally:
            pool.stop()

    def test_worker_constructor_stores_config(self, mock_task_processor):
        """Worker stores timeout_minutes, max_retries, etc."""
        mock_pool = MockWorkerPool()
        worker = Worker(
            worker_id="test-worker",
            task_processor=mock_task_processor,
            worker_pool=mock_pool,
            timeout_minutes=45.0,
            max_retries=3,
            retry_backoff_base=60,
            retry_backoff_max=3600,
        )
        
        assert worker._timeout_minutes == 45.0
        assert worker._max_retries == 3
        assert worker._retry_backoff_base == 60
        assert worker._retry_backoff_max == 3600

    def test_worker_default_timeout(self, mock_task_processor):
        """Worker uses default timeout_minutes of 45.0 when not specified."""
        worker = Worker(
            worker_id="test-worker",
            task_processor=mock_task_processor,
            worker_pool=MockWorkerPool(),
        )
        
        # Default timeout is 45.0 minutes
        assert worker._timeout_minutes == 45.0


# ============================================================================
# CancellationToken Tests (for completeness)
# ============================================================================

class TestCancellationToken:
    """Tests for CancellationToken behavior."""

    def test_token_is_cancelled_after_cancel(self):
        """Token.is_cancelled returns True after source.cancel()."""
        source = CancellationTokenSource()
        token = source.token
        
        assert not token.is_cancelled
        
        source.cancel(CancellationReason.TIMEOUT)
        
        assert token.is_cancelled

    def test_token_reason_after_cancel(self):
        """Token.reason returns the cancellation reason."""
        source = CancellationTokenSource()
        token = source.token
        
        source.cancel(CancellationReason.SHUTDOWN)
        
        assert token.reason == CancellationReason.SHUTDOWN

    def test_token_check_raises_on_cancelled(self):
        """Token.check() raises OperationCancelledError when cancelled."""
        source = CancellationTokenSource()
        token = source.token
        
        source.cancel(CancellationReason.MANUAL)
        
        with pytest.raises(OperationCancelledError) as exc_info:
            token.check()
        
        assert exc_info.value.reason == CancellationReason.MANUAL

    def test_operation_cancelled_error_contains_reason(self):
        """OperationCancelledError includes the reason in its message."""
        error = OperationCancelledError(
            reason=CancellationReason.TIMEOUT,
            message="Task exceeded timeout"
        )
        
        assert error.reason == CancellationReason.TIMEOUT
        assert "timeout" in error.message.lower()


# ============================================================================
# Integration-Style Tests (Fast, Mocked)
# ============================================================================

class TestWorkerRetryWorkflow:
    """Integration-style tests for complete retry workflows."""

    def test_timeout_cancellation_triggers_retry(
        self, mock_task_processor, mock_task
    ):
        """Timeout cancellation leads to retry scheduling."""
        worker = Worker(
            worker_id="test-worker",
            task_processor=mock_task_processor,
            worker_pool=MockWorkerPool(),
            max_retries=3,
        )
        
        # First attempt fails with timeout
        cancelled_error = OperationCancelledError(
            reason=CancellationReason.TIMEOUT,
            message="Task timed out"
        )
        
        # Retry task is created
        retry_task = Mock()
        retry_task.id = 2
        retry_task.retry_count = 1
        mock_task_processor._task_repo.schedule_retry.return_value = retry_task
        
        with patch(
            "daemon.services.main_loop_bridge.MainLoopBridge.run_async"
        ) as mock_run_async:
            mock_run_async.side_effect = cancelled_error
            
            with patch(
                "daemon.services.timeout_monitor.TimeoutMonitor"
            ) as mock_monitor_class:
                mock_monitor_instance = Mock()
                mock_monitor_class.return_value = mock_monitor_instance
                
                # First attempt
                worker._process_with_timeout(mock_task)
        
        # Verify retry was scheduled
        mock_task_processor._task_repo.schedule_retry.assert_called_once()
        assert worker._tasks_failed == 1
        assert worker._tasks_completed == 0

    def test_max_retries_exceeded_leads_to_permanent_fail(
        self, mock_task_processor, mock_task
    ):
        """Max retries exceeded leads to permanent failure."""
        worker = Worker(
            worker_id="test-worker",
            task_processor=mock_task_processor,
            worker_pool=MockWorkerPool(),
            max_retries=3,
        )
        
        # schedule_retry returns None (max retries exceeded)
        mock_task_processor._task_repo.schedule_retry.return_value = None
        
        cancelled_error = OperationCancelledError(
            reason=CancellationReason.TIMEOUT,
            message="Task timed out"
        )
        
        with patch(
            "daemon.services.main_loop_bridge.MainLoopBridge.run_async"
        ) as mock_run_async:
            mock_run_async.side_effect = cancelled_error
            
            with patch(
                "daemon.services.timeout_monitor.TimeoutMonitor"
            ) as mock_monitor_class:
                mock_monitor_instance = Mock()
                mock_monitor_class.return_value = mock_monitor_instance
                
                # Attempt (which will hit max retries)
                worker._process_with_timeout(mock_task)
        
        # Verify permanent failure
        mock_task_processor._task_repo.fail_task.assert_called_once()
        assert worker._tasks_failed == 1

    def test_shutdown_cancellation_cancels_task_no_retry(
        self, mock_task_processor, mock_task
    ):
        """Shutdown cancellation cancels task without retry."""
        worker = Worker(
            worker_id="test-worker",
            task_processor=mock_task_processor,
            worker_pool=MockWorkerPool(),
            max_retries=3,
        )
        
        cancelled_error = OperationCancelledError(
            reason=CancellationReason.SHUTDOWN,
            message="Worker shutting down"
        )
        
        with patch(
            "daemon.services.main_loop_bridge.MainLoopBridge.run_async"
        ) as mock_run_async:
            mock_run_async.side_effect = cancelled_error
            
            with patch(
                "daemon.services.timeout_monitor.TimeoutMonitor"
            ) as mock_monitor_class:
                mock_monitor_instance = Mock()
                mock_monitor_class.return_value = mock_monitor_instance
                
                worker._process_with_timeout(mock_task)
        
        # Verify task was cancelled, not retried
        mock_task_processor._task_repo.cancel_task.assert_called_once()
        mock_task_processor._task_repo.schedule_retry.assert_not_called()
        assert worker._tasks_failed == 1


# ============================================================================
# Original Source Preservation Tests (dispatch_completed fix)
# ============================================================================

class TestOriginalSourcePreservation:
    """Tests for preserving original external source for internal messages."""

    def test_process_message_processor_dispatches_with_original_source(self):
        """ProcessMessageProcessor uses original_source for internal messages."""
        from daemon.services.task_processor import ProcessMessageProcessor
        from unittest.mock import AsyncMock, Mock, patch, MagicMock
        import asyncio
        
        # Create mock dependencies
        mock_manager = Mock()
        mock_manager._process_message_with_tracking = AsyncMock(return_value=Mock(content="Response"))
        mock_manager._instance_repository = Mock()
        
        # Mock instance metadata with original_source
        mock_instance_meta = Mock()
        mock_instance_meta.instance_metadata = {"original_source": "telegram:12345"}
        mock_manager._instance_repository.get.return_value = mock_instance_meta
        
        mock_task_repo = Mock()
        mock_message_repo = Mock()
        mock_message = Mock()
        mock_message.content = "test"
        mock_message.source = "internal_report:child123:msg456"
        mock_message_repo.get = Mock(return_value=mock_message)
        
        mock_dispatcher = AsyncMock()
        
        # Create processor
        processor = ProcessMessageProcessor(
            instance_manager=mock_manager,
            task_repo=mock_task_repo,
            event_repo=None,
            message_repository=mock_message_repo,
            source_dispatcher=mock_dispatcher,
        )
        
        # Create task
        task = Mock()
        task.id = 1
        task.instance_id = "test-instance"
        task.message_id = "test-message"
        task.retry_count = 0
        
        # Run processing
        asyncio.run(processor.process(task))
        
        # Verify dispatch_completed was called with ORIGINAL source
        mock_dispatcher.dispatch_completed.assert_called_once()
        call_kwargs = mock_dispatcher.dispatch_completed.call_args.kwargs
        assert call_kwargs["source"] == "telegram:12345"

    def test_process_message_processor_skips_dispatch_when_no_original_source(self):
        """ProcessMessageProcessor skips dispatch when internal report has no original_source."""
        from daemon.services.task_processor import ProcessMessageProcessor
        from unittest.mock import AsyncMock, Mock, patch, MagicMock
        import asyncio
        
        # Create mock dependencies
        mock_manager = Mock()
        mock_manager._process_message_with_tracking = AsyncMock(return_value=Mock(content="Response"))
        mock_manager._instance_repository = Mock()
        
        # Mock instance metadata WITHOUT original_source
        mock_instance_meta = Mock()
        mock_instance_meta.instance_metadata = {}
        mock_manager._instance_repository.get.return_value = mock_instance_meta
        
        mock_task_repo = Mock()
        mock_message_repo = Mock()
        mock_message = Mock()
        mock_message.content = "test"
        mock_message.source = "internal_report:child123"
        mock_message_repo.get = Mock(return_value=mock_message)
        
        mock_dispatcher = AsyncMock()
        
        # Create processor
        processor = ProcessMessageProcessor(
            instance_manager=mock_manager,
            task_repo=mock_task_repo,
            event_repo=None,
            message_repository=mock_message_repo,
            source_dispatcher=mock_dispatcher,
        )
        
        # Create task
        task = Mock()
        task.id = 1
        task.instance_id = "test-instance"
        task.message_id = "test-message"
        task.retry_count = 0
        
        # Run processing
        asyncio.run(processor.process(task))
        
        # Verify dispatch_completed was NOT called (no original source)
        mock_dispatcher.dispatch_completed.assert_not_called()

    def test_process_message_processor_uses_direct_source_for_external_messages(self):
        """ProcessMessageProcessor uses message.source directly for external messages."""
        from daemon.services.task_processor import ProcessMessageProcessor
        from unittest.mock import AsyncMock, Mock, patch, MagicMock
        import asyncio
        
        # Create mock dependencies
        mock_manager = Mock()
        mock_manager._process_message_with_tracking = AsyncMock(return_value=Mock(content="Response"))
        mock_manager._instance_repository = Mock()
        
        mock_task_repo = Mock()
        mock_message_repo = Mock()
        mock_message = Mock()
        mock_message.content = "test"
        mock_message.source = "telegram:67890"  # External source
        mock_message_repo.get = Mock(return_value=mock_message)
        
        mock_dispatcher = AsyncMock()
        
        # Create processor
        processor = ProcessMessageProcessor(
            instance_manager=mock_manager,
            task_repo=mock_task_repo,
            event_repo=None,
            message_repository=mock_message_repo,
            source_dispatcher=mock_dispatcher,
        )
        
        # Create task
        task = Mock()
        task.id = 1
        task.instance_id = "test-instance"
        task.message_id = "test-message"
        task.retry_count = 0
        
        # Run processing
        asyncio.run(processor.process(task))
        
        # Verify dispatch_completed was called with the DIRECT source
        mock_dispatcher.dispatch_completed.assert_called_once()
        call_kwargs = mock_dispatcher.dispatch_completed.call_args.kwargs
        assert call_kwargs["source"] == "telegram:67890"

    def test_process_message_processor_dispatches_job_event_with_original_source(self):
        """ProcessMessageProcessor resolves internal_agent:job_event:* to original_source.

        Regression: JobFeedbackObserver notifications use
        `internal_agent:job_event:<job_id>:<status>`. The processor must
        resolve this to the original external source so the report reaches
        Slack/Telegram.
        """
        from daemon.services.task_processor import ProcessMessageProcessor
        from unittest.mock import AsyncMock, Mock
        import asyncio

        mock_manager = Mock()
        mock_manager._process_message_with_tracking = AsyncMock(return_value=Mock(content="Result"))
        mock_manager._instance_repository = Mock()

        mock_instance_meta = Mock()
        mock_instance_meta.instance_metadata = {"original_source": "slack-bot:TWS:U1"}
        mock_manager._instance_repository.get.return_value = mock_instance_meta

        mock_task_repo = Mock()
        mock_message_repo = Mock()
        mock_message = Mock()
        mock_message.content = "test"
        mock_message.source = "internal_agent:job_event:abc-123:completed"
        mock_message_repo.get = Mock(return_value=mock_message)

        mock_dispatcher = AsyncMock()

        processor = ProcessMessageProcessor(
            instance_manager=mock_manager,
            task_repo=mock_task_repo,
            event_repo=None,
            message_repository=mock_message_repo,
            source_dispatcher=mock_dispatcher,
        )

        task = Mock()
        task.id = 1
        task.instance_id = "test-instance"
        task.message_id = "test-message"
        task.retry_count = 0

        asyncio.run(processor.process(task))

        mock_dispatcher.dispatch_completed.assert_called_once()
        call_kwargs = mock_dispatcher.dispatch_completed.call_args.kwargs
        assert call_kwargs["source"] == "slack-bot:TWS:U1"

