"""Tests for _process_child_completion_and_notify_parent in resume_processing_job.

The fix ensures that when a child instance completes after being resumed,
the parent gets notified (waiting_for decremented, report message sent).

These tests verify that _process_child_completion_and_notify_parent is called
correctly in both branches of resume_processing_job:
1. The `if old_jobs` branch (parent/JobQueue path)
2. The `else` branch (child/WorkerPool path)
"""

import uuid
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from daemon.manager import InstanceManager
from daemon.config import Config
from daemon.cancellation import CancellationTokenSource
from daemon.repositories.instance.models import InstanceStatus


class MockInstanceMeta:
    """Mock instance metadata returned by _instance_repository.get."""

    def __init__(
        self,
        instance_id: str = "test-instance",
        status: str = InstanceStatus.PAUSED.value,
        waiting_for: int = 0
    ):
        self.instance_id = instance_id
        self.status = status
        self.waiting_for = waiting_for


class MockMessageResult:
    """Mock message result returned by _process_message_with_tracking."""

    def __init__(self, content: str = "Resume completed"):
        self.content = content


class MockJob:
    """Mock job returned by find_processing_message_jobs_by_instance."""

    def __init__(self, job_id: str = "test-job-123", message_id: str = "test-msg-456"):
        self.job_id = job_id
        self.job_metadata = {"message_id": message_id}


@pytest.fixture
def mock_job_queue_service():
    """Create mock job queue service with repository."""
    service = MagicMock()
    service._repository = MagicMock()
    service.complete_job = AsyncMock()
    return service


@pytest.fixture
def mock_queue_repository():
    """Create mock queue repository."""
    repo = MagicMock()
    repo.complete = MagicMock()
    return repo


@pytest.fixture
def mock_instance_repository():
    """Create mock instance repository."""
    repo = MagicMock()
    return repo


@pytest.fixture
def mock_process_child_completion():
    """Create mock for _process_child_completion_and_notify_parent."""
    return AsyncMock()


@pytest.fixture
def mock_manager(mock_job_queue_service, mock_queue_repository, mock_instance_repository, mock_process_child_completion):
    """Create mock manager with all required dependencies."""
    manager = MagicMock()
    manager._job_queue_service = mock_job_queue_service
    manager._queue_repository = mock_queue_repository
    manager._instance_repository = mock_instance_repository
    manager._process_message_with_tracking = AsyncMock(return_value=MockMessageResult())
    manager._process_child_completion_and_notify_parent = mock_process_child_completion
    return manager


@pytest.fixture
def instance_manager(mock_manager):
    """Create InstanceManager with mocked dependencies."""
    config = MagicMock(spec=Config)
    manager = InstanceManager.__new__(InstanceManager)
    manager._job_queue_service = mock_manager._job_queue_service
    manager._queue_repository = mock_manager._queue_repository
    manager._instance_repository = mock_manager._instance_repository
    manager._process_message_with_tracking = mock_manager._process_message_with_tracking
    manager._process_child_completion_and_notify_parent = mock_manager._process_child_completion_and_notify_parent
    return manager


class TestChildNotificationWorkerPoolPath:
    """Test suite for _process_child_completion_and_notify_parent in WorkerPool (else) branch.

    When there are no old_jobs, the code takes the child/WorkerPool path.
    _process_child_completion_and_notify_parent should be called with the
    freshly generated message_id.
    """

    @pytest.mark.asyncio
    async def test_notification_called_with_correct_instance_and_message_id(
        self, instance_manager, mock_manager
    ):
        """Scenario 1: Child resumes and completes - notification IS called with correct args.

        Verify _process_child_completion_and_notify_parent is called with the
        instance_id and the message_id returned from _process_message_with_tracking.
        """
        instance_id = "child-instance-123"

        # No old jobs (child instance uses WorkerPool)
        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[]
        )

        mock_manager._instance_repository.get = MagicMock(
            return_value=MockInstanceMeta(instance_id=instance_id, status=InstanceStatus.PAUSED.value)
        )

        # Call resume_processing_job
        result = await instance_manager.resume_processing_job(
            instance_id, message="resume", silent=False
        )

        # Verify _process_message_with_tracking was called first
        mock_manager._process_message_with_tracking.assert_called_once()

        # Verify _process_child_completion_and_notify_parent WAS called
        mock_manager._process_child_completion_and_notify_parent.assert_called_once()

        # Get the message_id from the _process_message_with_tracking call
        call_kwargs = mock_manager._process_message_with_tracking.call_args.kwargs
        expected_message_id = call_kwargs["message_id"]

        # Verify correct args: instance_id and the fresh message_id
        mock_manager._process_child_completion_and_notify_parent.assert_called_with(
            instance_id, expected_message_id
        )

        # Verify return value
        assert result["instance_id"] == instance_id
        assert result["job_id"] is None

    @pytest.mark.asyncio
    async def test_notification_called_when_child_has_no_parent(
        self, instance_manager, mock_manager
    ):
        """Scenario 2: Child with no parent - notification IS called but handles gracefully.

        The function should be called regardless of whether the instance has a parent.
        The function itself handles the no-parent case (graceful no-op).
        """
        instance_id = "child-no-parent-456"

        # No old jobs (child instance)
        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[]
        )

        mock_manager._instance_repository.get = MagicMock(
            return_value=MockInstanceMeta(instance_id=instance_id, status=InstanceStatus.RUNNING.value)
        )

        # Replace with new mock that returns None (simulating no-parent graceful no-op)
        new_mock = AsyncMock(return_value=None)
        mock_manager._process_child_completion_and_notify_parent = new_mock
        instance_manager._process_child_completion_and_notify_parent = new_mock

        result = await instance_manager.resume_processing_job(
            instance_id, message="resume", silent=True
        )

        # Verify the notification function was still called (even though no parent)
        new_mock.assert_called_once()

        # Verify the function has the correct instance_id
        call_args = new_mock.call_args
        assert call_args[0][0] == instance_id

    @pytest.mark.asyncio
    async def test_notification_called_multiple_resumes(
        self, instance_manager, mock_manager
    ):
        """Scenario 3: Multiple resumes - notification called each time with unique message_id."""
        instance_id = "child-multi-resume-789"

        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[]
        )

        mock_manager._instance_repository.get = MagicMock(
            return_value=MockInstanceMeta(instance_id=instance_id)
        )

        # First resume
        result1 = await instance_manager.resume_processing_job(
            instance_id, message="resume", silent=False
        )
        msg_id1 = mock_manager._process_message_with_tracking.call_args.kwargs["message_id"]

        # Second resume (don't reset mock - we want to verify cumulative calls)
        result2 = await instance_manager.resume_processing_job(
            instance_id, message="resume", silent=False
        )
        msg_id2 = mock_manager._process_message_with_tracking.call_args.kwargs["message_id"]

        # Verify notification was called twice with different message_ids
        assert mock_manager._process_child_completion_and_notify_parent.call_count == 2

        # First call with first message_id
        mock_manager._process_child_completion_and_notify_parent.assert_any_call(
            instance_id, msg_id1
        )
        # Second call with second message_id
        mock_manager._process_child_completion_and_notify_parent.assert_any_call(
            instance_id, msg_id2
        )


class TestChildNotificationJobQueuePath:
    """Test suite for _process_child_completion_and_notify_parent in JobQueue (if) branch.

    When there are old_jobs, the code takes the parent/JobQueue path.
    _process_child_completion_and_notify_parent should be called with the
    message_id from the old job's metadata.
    """

    @pytest.mark.asyncio
    async def test_notification_called_in_parent_resume_path(
        self, instance_manager, mock_manager
    ):
        """Scenario 4: Parent resume with old_jobs - notification IS called.

        Verify _process_child_completion_and_notify_parent is called after
        _process_message_with_tracking completes, with the message_id from old_job.
        """
        instance_id = "parent-instance-123"
        job_id = "job-abc-789"
        message_id = "msg-xyz-456"

        # Has old jobs (parent instance uses JobQueue)
        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[MockJob(job_id=job_id, message_id=message_id)]
        )

        # waiting_for=0 means no pending children, can complete
        mock_manager._instance_repository.get = MagicMock(
            return_value=MockInstanceMeta(
                instance_id=instance_id,
                status=InstanceStatus.RUNNING.value,
                waiting_for=0
            )
        )

        result = await instance_manager.resume_processing_job(
            instance_id, message="resume"
        )

        # Verify _process_message_with_tracking was called first
        mock_manager._process_message_with_tracking.assert_called_once()

        # Verify _process_child_completion_and_notify_parent WAS called
        mock_manager._process_child_completion_and_notify_parent.assert_called_once()

        # Verify correct args: instance_id and message_id from old_job
        mock_manager._process_child_completion_and_notify_parent.assert_called_with(
            instance_id, message_id
        )

    @pytest.mark.asyncio
    async def test_notification_called_when_parent_has_no_parent(
        self, instance_manager, mock_manager
    ):
        """Scenario 5: Top-level instance (no parent) - notification IS called but handles gracefully.

        Even when the instance has no parent, the function should still be called.
        The function itself returns early for no-parent cases.
        """
        instance_id = "top-level-instance"

        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[MockJob()]
        )

        mock_manager._instance_repository.get = MagicMock(
            return_value=MockInstanceMeta(instance_id=instance_id, waiting_for=0)
        )

        # Replace with new mock that returns None (simulating no-parent graceful no-op)
        new_mock = AsyncMock(return_value=None)
        mock_manager._process_child_completion_and_notify_parent = new_mock
        instance_manager._process_child_completion_and_notify_parent = new_mock

        result = await instance_manager.resume_processing_job(
            instance_id, message="resume"
        )

        # Verify the notification function was still called
        new_mock.assert_called_once()

        # Verify the function has the correct instance_id
        call_args = new_mock.call_args
        assert call_args[0][0] == instance_id


class TestChildNotificationErrorHandling:
    """Test suite for error handling in _process_child_completion_and_notify_parent calls.

    When the notification function throws an exception, resume_processing_job
    should catch it, log the error, and NOT propagate the exception.
    """

    @pytest.mark.asyncio
    async def test_notification_error_does_not_propagate_worker_pool(
        self, instance_manager, mock_manager, caplog
    ):
        """Scenario 6: WorkerPool path - notification error is caught and logged.

        If _process_child_completion_and_notify_parent raises an exception,
        resume_processing_job should catch it and log the error, not propagate.
        """
        import logging
        instance_id = "child-error-123"

        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[]
        )

        mock_manager._instance_repository.get = MagicMock(
            return_value=MockInstanceMeta(instance_id=instance_id)
        )

        # Make _process_child_completion_and_notify_parent raise an exception
        mock_manager._process_child_completion_and_notify_parent.side_effect = RuntimeError(
            "Database error in notification"
        )

        # Should NOT raise - error should be caught and logged
        with caplog.at_level(logging.ERROR):
            result = await instance_manager.resume_processing_job(
                instance_id, message="resume", silent=False
            )

        # Verify resume still completed successfully
        assert result["instance_id"] == instance_id
        assert result["job_id"] is None

        # Verify error was logged
        assert any(
            "child completion notification FAILED" in record.message
            for record in caplog.records
        ), "Expected error log from notification failure"

    @pytest.mark.asyncio
    async def test_notification_error_does_not_propagate_job_queue(
        self, instance_manager, mock_manager, caplog
    ):
        """Scenario 7: JobQueue path - notification error is caught and logged."""
        import logging
        instance_id = "parent-error-456"

        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[MockJob()]
        )

        mock_manager._instance_repository.get = MagicMock(
            return_value=MockInstanceMeta(instance_id=instance_id, waiting_for=0)
        )

        # Make _process_child_completion_and_notify_parent raise an exception
        mock_manager._process_child_completion_and_notify_parent.side_effect = RuntimeError(
            "Notification service unavailable"
        )

        # Should NOT raise - error should be caught and logged
        with caplog.at_level(logging.ERROR):
            result = await instance_manager.resume_processing_job(
                instance_id, message="resume"
            )

        # Verify resume still completed (job completed)
        assert result["job_id"] is not None

        # Verify error was logged
        assert any(
            "child completion notification FAILED" in record.message
            for record in caplog.records
        ), "Expected error log from notification failure"

    @pytest.mark.asyncio
    async def test_notification_error_with_exception_details(
        self, instance_manager, mock_manager, caplog
    ):
        """Scenario 8: Verify error log contains exception details."""
        import logging
        import traceback
        instance_id = "child-error-detailed"

        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[]
        )

        mock_manager._instance_repository.get = MagicMock(
            return_value=MockInstanceMeta(instance_id=instance_id)
        )

        # Create a detailed exception
        error_message = "Connection refused to parent service"
        mock_manager._process_child_completion_and_notify_parent.side_effect = ConnectionError(
            error_message
        )

        with caplog.at_level(logging.ERROR):
            result = await instance_manager.resume_processing_job(
                instance_id, message="resume", silent=False
            )

        # Verify resume completed despite error
        assert result["instance_id"] == instance_id

        # Verify the exception message is in the log (exc_info=True logs traceback)
        log_found = any(
            "child completion notification FAILED" in record.message and
            error_message in record.message
            for record in caplog.records
        )
        assert log_found, f"Expected log with '{error_message}', got: {[r.message for r in caplog.records]}"

    @pytest.mark.asyncio
    async def test_notification_error_preserves_job_completion(
        self, instance_manager, mock_manager
    ):
        """Scenario 9: JobQueue path - job still completes even if notification fails.

        When notification throws, the job should still be completed normally.
        """
        instance_id = "parent-complete-despite-error"

        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[MockJob()]
        )

        mock_manager._instance_repository.get = MagicMock(
            return_value=MockInstanceMeta(instance_id=instance_id, waiting_for=0)
        )

        # Notification fails
        mock_manager._process_child_completion_and_notify_parent.side_effect = RuntimeError(
            "Notification failed"
        )

        result = await instance_manager.resume_processing_job(
            instance_id, message="resume"
        )

        # Verify job was still completed
        mock_manager._job_queue_service.complete_job.assert_called_once()

        # Verify return has job_id
        assert result["job_id"] is not None
