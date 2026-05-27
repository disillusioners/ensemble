"""Tests for WAITING_CHILDREN skip path in resume_processing_job.

When a parent instance resumes and is already WAITING_CHILDREN, the code
should skip complete_job() and return early since JobFeedbackObserver will
complete the job when all children finish.

These tests verify the fix in the old_jobs branch of resume_processing_job.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock

from daemon.manager import InstanceManager
from daemon.config import Config
from daemon.repositories.instance.models import InstanceStatus
from daemon.services.job_queue_service import DemandState


class MockInstanceMeta:
    """Mock instance metadata returned by _instance_repository.get."""

    def __init__(self, instance_id: str = "test-instance", status: str = InstanceStatus.PAUSED.value):
        self.instance_id = instance_id
        self.status = status


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


class TestWaitingChildrenSkip:
    """Test suite for WAITING_CHILDREN skip behavior in parent resume."""

    @pytest.mark.asyncio
    async def test_parent_resume_waiting_children_skips_complete_job(
        self, instance_manager, mock_manager
    ):
        """Test: When instance is WAITING_CHILDREN, complete_job should NOT be called.

        This is the core bug fix - parent instances in WAITING_CHILDREN status
        should defer job completion to JobFeedbackObserver.
        """
        instance_id = "parent-instance-123"
        job_id = "job-abc-789"
        message_id = "msg-xyz-456"

        # Setup: old_jobs returns a PROCESSING job for this parent instance
        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[MockJob(job_id=job_id, message_id=message_id)]
        )

        # Instance is WAITING_CHILDREN - should skip complete_job
        mock_manager._instance_repository.get = MagicMock(
            return_value=MockInstanceMeta(
                instance_id=instance_id,
                status=InstanceStatus.WAITING_CHILDREN.value
            )
        )

        result = await instance_manager.resume_processing_job(
            instance_id, message="resume"
        )

        # Verify _process_message_with_tracking was called
        mock_manager._process_message_with_tracking.assert_called_once()

        # Core assertion: complete_job should NOT be called
        mock_manager._job_queue_service.complete_job.assert_not_called()

        # Verify return value
        assert result == {"job_id": job_id, "message_id": message_id, "status": "waiting_children"}

    @pytest.mark.asyncio
    async def test_parent_resume_normal_completes_job(self, instance_manager, mock_manager):
        """Test: When instance is RUNNING (not WAITING_CHILDREN), complete_job SHOULD be called."""
        instance_id = "parent-instance-normal"
        job_id = "job-normal-123"
        message_id = "msg-normal-456"

        # Setup: old_jobs returns a PROCESSING job
        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[MockJob(job_id=job_id, message_id=message_id)]
        )

        # Instance is RUNNING - normal path, should complete job
        mock_manager._instance_repository.get = MagicMock(
            return_value=MockInstanceMeta(
                instance_id=instance_id,
                status=InstanceStatus.RUNNING.value
            )
        )

        result = await instance_manager.resume_processing_job(
            instance_id, message="resume"
        )

        # Verify complete_job WAS called
        mock_manager._job_queue_service.complete_job.assert_called_once()
        call_args = mock_manager._job_queue_service.complete_job.call_args
        assert call_args[0][0] == job_id
        assert call_args[0][1] == DemandState.COMPLETED

        # Verify return value
        assert result["job_id"] == job_id
        assert result["message_id"] == message_id

    @pytest.mark.asyncio
    async def test_parent_resume_instance_not_found_completes_job(
        self, instance_manager, mock_manager
    ):
        """Test: When instance is None, complete_job SHOULD be called (falls through)."""
        instance_id = "parent-instance-none"
        job_id = "job-none-123"
        message_id = "msg-none-456"

        # Setup: old_jobs returns a PROCESSING job
        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[MockJob(job_id=job_id, message_id=message_id)]
        )

        # Instance not found (None) - should fall through to complete_job
        mock_manager._instance_repository.get = MagicMock(return_value=None)

        result = await instance_manager.resume_processing_job(
            instance_id, message="resume"
        )

        # Verify complete_job WAS called (exception handler allows completion)
        mock_manager._job_queue_service.complete_job.assert_called_once()

        # Verify return value has job_id
        assert result["job_id"] == job_id

    @pytest.mark.asyncio
    async def test_parent_resume_repository_exception_completes_job(
        self, instance_manager, mock_manager
    ):
        """Test: When instance_repository.get raises, complete_job SHOULD still be called."""
        instance_id = "parent-instance-exception"
        job_id = "job-exception-123"
        message_id = "msg-exception-456"

        # Setup: old_jobs returns a PROCESSING job
        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[MockJob(job_id=job_id, message_id=message_id)]
        )

        # Instance repository raises exception - should be caught, allow completion
        mock_manager._instance_repository.get = MagicMock(
            side_effect=RuntimeError("Database connection failed")
        )

        result = await instance_manager.resume_processing_job(
            instance_id, message="resume"
        )

        # Verify complete_job WAS called (exception handler allows it)
        mock_manager._job_queue_service.complete_job.assert_called_once()

        # Verify return value has job_id
        assert result["job_id"] == job_id

    @pytest.mark.asyncio
    async def test_parent_resume_paused_status_completes_job(
        self, instance_manager, mock_manager
    ):
        """Test: Only WAITING_CHILDREN should skip - PAUSED should still complete job."""
        instance_id = "parent-instance-paused"
        job_id = "job-paused-123"
        message_id = "msg-paused-456"

        # Setup: old_jobs returns a PROCESSING job
        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[MockJob(job_id=job_id, message_id=message_id)]
        )

        # Instance is PAUSED - should NOT skip (only WAITING_CHILDREN skips)
        mock_manager._instance_repository.get = MagicMock(
            return_value=MockInstanceMeta(
                instance_id=instance_id,
                status=InstanceStatus.PAUSED.value
            )
        )

        result = await instance_manager.resume_processing_job(
            instance_id, message="resume"
        )

        # Verify complete_job WAS called (PAUSED doesn't skip)
        mock_manager._job_queue_service.complete_job.assert_called_once()

        # Verify return value
        assert result["job_id"] == job_id

    @pytest.mark.asyncio
    async def test_parent_resume_waiting_children_returns_correct_job_id(
        self, instance_manager, mock_manager
    ):
        """Test: Returned dict should have exact job_id and message_id from original job."""
        instance_id = "parent-instance-correct"
        job_id = "specific-job-id-abc123"
        message_id = "specific-message-id-xyz789"

        # Setup: old_jobs returns job with specific IDs
        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[MockJob(job_id=job_id, message_id=message_id)]
        )

        # Instance is WAITING_CHILDREN
        mock_manager._instance_repository.get = MagicMock(
            return_value=MockInstanceMeta(
                instance_id=instance_id,
                status=InstanceStatus.WAITING_CHILDREN.value
            )
        )

        result = await instance_manager.resume_processing_job(
            instance_id, message="resume"
        )

        # Verify exact job_id and message_id in return value
        assert result["job_id"] == job_id
        assert result["message_id"] == message_id
        assert result["status"] == "waiting_children"

        # Verify no complete_job called
        mock_manager._job_queue_service.complete_job.assert_not_called()
