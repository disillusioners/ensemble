"""Tests for child instance resume in resume_processing_job.

Child instances (sub-agents in a tree) don't have JobQueue entries — they use
WorkerPool directly. When resuming a child instance, the code should call
_process_message_with_tracking directly instead of looking for old jobs.

These tests verify the fix in the `if not old_jobs:` branch of resume_processing_job.
"""

import uuid
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from daemon.manager import InstanceManager
from daemon.config import Config
from daemon.cancellation import CancellationTokenSource
from daemon.repositories.instance.models import InstanceStatus


class MockInstanceMeta:
    """Mock instance metadata returned by _instance_repository.get."""

    def __init__(self, instance_id: str = "test-instance", status: str = InstanceStatus.PAUSED.value):
        self.instance_id = instance_id
        self.status = status


class MockMessageResult:
    """Mock message result returned by _process_message_with_tracking."""

    def __init__(self, content: str = "Resume completed"):
        self.content = content


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
def mock_manager(mock_job_queue_service, mock_queue_repository, mock_instance_repository):
    """Create mock manager with all required dependencies."""
    manager = MagicMock()
    manager._job_queue_service = mock_job_queue_service
    manager._queue_repository = mock_queue_repository
    manager._instance_repository = mock_instance_repository
    manager._process_message_with_tracking = AsyncMock(return_value=MockMessageResult())
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
    return manager


class TestChildInstanceResume:
    """Test suite for child instance resume behavior."""

    @pytest.mark.asyncio
    async def test_child_resume_non_silent_target_resume(self, instance_manager, mock_manager):
        """Scenario 1: Child instance resume (non-silent / target resume).

        Verify _process_message_with_tracking is called with correct args
        when resuming a child instance with silent=False.
        """
        instance_id = "child-instance-123"

        # No old jobs (child instance uses WorkerPool)
        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[]
        )

        # Instance meta exists with PAUSED status
        mock_manager._instance_repository.get = MagicMock(
            return_value=MockInstanceMeta(instance_id=instance_id, status=InstanceStatus.PAUSED.value)
        )

        # Call resume_processing_job with silent=False
        result = await instance_manager.resume_processing_job(
            instance_id, message="continue working", silent=False
        )

        # Verify _process_message_with_tracking was called
        mock_manager._process_message_with_tracking.assert_called_once()
        call_kwargs = mock_manager._process_message_with_tracking.call_args.kwargs

        # Verify correct args
        assert call_kwargs["instance_id"] == instance_id
        assert call_kwargs["message"] == "continue working"
        assert call_kwargs["is_retry"] is False  # silent=False means is_retry=False
        assert call_kwargs["message_source"] == "cascade_resume"

        # Verify message_id is a fresh UUID
        try:
            uuid.UUID(call_kwargs["message_id"])
        except ValueError:
            pytest.fail(f"message_id should be a valid UUID, got: {call_kwargs['message_id']}")

        # Verify cancellation_token is a CancellationToken
        assert hasattr(call_kwargs["cancellation_token"], "is_cancelled")

        # Verify return value includes the generated message_id
        assert result["instance_id"] == instance_id
        assert result["job_id"] is None
        assert result["message_id"] == call_kwargs["message_id"]

    @pytest.mark.asyncio
    async def test_child_resume_silent_cascade_resume(self, instance_manager, mock_manager):
        """Scenario 2: Child instance resume (silent / cascade resume).

        Verify is_retry=True when silent=True (cascade resume for children).
        """
        instance_id = "child-instance-456"

        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[]
        )

        # Instance meta exists with RUNNING status
        mock_manager._instance_repository.get = MagicMock(
            return_value=MockInstanceMeta(instance_id=instance_id, status=InstanceStatus.RUNNING.value)
        )

        result = await instance_manager.resume_processing_job(
            instance_id, message="resume", silent=True
        )

        mock_manager._process_message_with_tracking.assert_called_once()
        call_kwargs = mock_manager._process_message_with_tracking.call_args.kwargs

        # Verify is_retry=True for silent mode
        assert call_kwargs["is_retry"] is True
        assert call_kwargs["message_source"] == "cascade_resume"

        # Verify return value includes the generated message_id
        assert result["instance_id"] == instance_id
        assert result["job_id"] is None
        assert result["message_id"] == call_kwargs["message_id"]

    @pytest.mark.asyncio
    async def test_child_resume_cancelled_error_handling(self, instance_manager, mock_manager):
        """Scenario 3: CancelledError handling.

        Verify that asyncio.CancelledError is caught and returns None
        instead of propagating.
        """
        instance_id = "child-instance-789"

        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[]
        )

        mock_manager._instance_repository.get = MagicMock(
            return_value=MockInstanceMeta(instance_id=instance_id)
        )

        # Simulate CancelledError from _process_message_with_tracking
        mock_manager._process_message_with_tracking.side_effect = asyncio.CancelledError()

        # Should return None, not raise
        result = await instance_manager.resume_processing_job(
            instance_id, message="resume", silent=True
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_child_resume_general_exception_raised(self, instance_manager, mock_manager):
        """Scenario 4: General exception re-raised.

        Verify RuntimeError and other exceptions are re-raised.
        """
        instance_id = "child-instance-error"

        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[]
        )

        mock_manager._instance_repository.get = MagicMock(
            return_value=MockInstanceMeta(instance_id=instance_id)
        )

        # Simulate RuntimeError
        mock_manager._process_message_with_tracking.side_effect = RuntimeError("something broke")

        # Should re-raise RuntimeError
        with pytest.raises(RuntimeError, match="something broke"):
            await instance_manager.resume_processing_job(
                instance_id, message="resume", silent=True
            )

    @pytest.mark.asyncio
    async def test_child_resume_instance_not_found(self, instance_manager, mock_manager):
        """Scenario 5: Instance not found (meta is None).

        Verify the code handles missing instance metadata gracefully.
        """
        instance_id = "nonexistent-instance"

        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[]
        )

        # Instance meta is None
        mock_manager._instance_repository.get = MagicMock(return_value=None)

        # Should not crash, still calls _process_message_with_tracking
        result = await instance_manager.resume_processing_job(
            instance_id, message="resume", silent=True
        )

        # Verify it proceeded (even though meta was None)
        mock_manager._process_message_with_tracking.assert_called_once()
        call_kwargs = mock_manager._process_message_with_tracking.call_args.kwargs
        assert call_kwargs["instance_id"] == instance_id

    @pytest.mark.asyncio
    async def test_child_resume_unexpected_state(self, instance_manager, mock_manager):
        """Scenario 6: Instance in unexpected state.

        Verify the code logs a warning but still proceeds when instance
        is in a state other than PAUSED or RUNNING.
        """
        instance_id = "completed-instance"

        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[]
        )

        # Instance meta with COMPLETED status (unexpected)
        mock_manager._instance_repository.get = MagicMock(
            return_value=MockInstanceMeta(instance_id=instance_id, status="COMPLETED")
        )

        # Should not crash, still proceeds
        result = await instance_manager.resume_processing_job(
            instance_id, message="resume", silent=True
        )

        # Verify it proceeded despite unexpected state
        mock_manager._process_message_with_tracking.assert_called_once()

    @pytest.mark.asyncio
    async def test_child_resume_fresh_uuid_each_call(self, instance_manager, mock_manager):
        """Scenario 7: Fresh UUID generated for message_id.

        Verify that each call to resume_processing_job generates a different message_id.
        """
        instance_id = "child-instance-duplicate"

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
        call_kwargs1 = mock_manager._process_message_with_tracking.call_args.kwargs
        message_id1 = call_kwargs1["message_id"]

        # Reset mock for second call
        mock_manager._process_message_with_tracking.reset_mock()

        # Second resume
        result2 = await instance_manager.resume_processing_job(
            instance_id, message="resume", silent=False
        )
        call_kwargs2 = mock_manager._process_message_with_tracking.call_args.kwargs
        message_id2 = call_kwargs2["message_id"]

        # Both should be valid UUIDs and different from each other
        assert message_id1 != message_id2, "Each resume should generate a unique message_id"

        try:
            uuid.UUID(message_id1)
            uuid.UUID(message_id2)
        except ValueError:
            pytest.fail("message_ids should be valid UUIDs")

    @pytest.mark.asyncio
    async def test_child_resume_cancellation_token_created(self, instance_manager, mock_manager):
        """Scenario 8: CancellationTokenSource created.

        Verify that a CancellationTokenSource is created and its token
        is passed to _process_message_with_tracking.
        """
        instance_id = "child-instance-token"

        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[]
        )

        mock_manager._instance_repository.get = MagicMock(
            return_value=MockInstanceMeta(instance_id=instance_id)
        )

        result = await instance_manager.resume_processing_job(
            instance_id, message="resume", silent=False
        )

        mock_manager._process_message_with_tracking.assert_called_once()
        call_kwargs = mock_manager._process_message_with_tracking.call_args.kwargs

        # Verify cancellation_token is a real CancellationToken (not just a mock)
        token = call_kwargs["cancellation_token"]
        assert token is not None
        # The token should have the interface of CancellationToken
        assert hasattr(token, "is_cancelled")
        # Fresh token should not be cancelled (is_cancelled is a property, not a method)
        assert token.is_cancelled is False
