"""Tests for resume_processing_job new queue flow behavior.

With the new implementation:
1. Child instances (no old_jobs): enqueue_message() via WorkerPool
2. Root instances (has old_jobs): resume from checkpoint via _process_message_with_tracking

The _process_child_completion_and_notify_parent is now called directly by resume_processing_job
for root instances, after processing completes successfully.
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


class MockAsyncMessageResult:
    """Mock async message result returned by enqueue_message."""

    def __init__(self, message_id: str = None):
        self.message_id = message_id or str(uuid.uuid4())
        self.instance_id = None
        self.status = "queued"


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
    repo.list_by_instance = MagicMock(return_value=[])
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
    # Mock enqueue_message for WorkerPool path (child instances)
    manager.enqueue_message = AsyncMock(return_value=MockAsyncMessageResult())
    # Mock _process_message_with_tracking for JobQueue path (root instances)
    manager._process_message_with_tracking = AsyncMock(return_value=MockMessageResult())
    # Mock _process_child_completion_and_notify_parent
    manager._process_child_completion_and_notify_parent = AsyncMock()
    return manager


@pytest.fixture
def instance_manager(mock_manager):
    """Create InstanceManager with mocked dependencies."""
    config = MagicMock(spec=Config)
    manager = InstanceManager.__new__(InstanceManager)
    manager._job_queue_service = mock_manager._job_queue_service
    manager._queue_repository = mock_manager._queue_repository
    manager._instance_repository = mock_manager._instance_repository
    manager.enqueue_message = mock_manager.enqueue_message
    manager._process_message_with_tracking = mock_manager._process_message_with_tracking
    manager._process_child_completion_and_notify_parent = mock_manager._process_child_completion_and_notify_parent
    return manager


class TestChildNotificationWorkerPoolPath:
    """Test suite for WorkerPool path (child instances) in new queue flow."""

    @pytest.mark.asyncio
    async def test_child_enqueues_via_workerpool(self, instance_manager, mock_manager):
        """Child instance (no old_jobs) should enqueue via WorkerPool."""
        instance_id = "child-instance-123"

        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[]
        )

        result = await instance_manager.resume_processing_job(
            instance_id, message="resume", silent=False
        )

        # Should use WorkerPool path
        mock_manager.enqueue_message.assert_called_once()
        mock_manager.enqueue_message_via_jq.assert_not_called()

        # Verify enqueue was called with correct args
        kwargs = mock_manager.enqueue_message.call_args[1]
        assert kwargs["instance_id"] == instance_id
        assert kwargs["message"] == "resume"
        assert kwargs["source"] == "cascade_resume"
        assert kwargs["metadata"]["resume_mode"] is False

        # Verify return
        assert result["instance_id"] == instance_id
        assert result["job_id"] is None
        assert result["message_id"] is not None

    @pytest.mark.asyncio
    async def test_child_silent_mode_skips_enqueue(self, instance_manager, mock_manager):
        """Silent mode for child instance skips enqueue entirely.
        
        When silent=True (cascade resume), the child should NOT receive a message
        enqueued. The parent's send_message tool will deliver the actual work.
        """
        instance_id = "child-instance-silent"

        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[]
        )

        result = await instance_manager.resume_processing_job(
            instance_id, message="resume", silent=True
        )

        # Silent mode should NOT enqueue any message
        mock_manager.enqueue_message.assert_not_called()
        
        # Should return a silent resume result
        assert result["instance_id"] == instance_id
        assert result["job_id"] is None
        assert result["message_id"] is None
        assert result["status"] == "silent_resume"

    @pytest.mark.asyncio
    async def test_child_enqueues_message_id_from_enqueue(self, instance_manager, mock_manager):
        """message_id should come from enqueue_message result."""
        instance_id = "child-instance-msgid"

        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[]
        )

        # Configure mock to return specific message_id
        expected_msg_id = str(uuid.uuid4())
        mock_manager.enqueue_message = AsyncMock(
            return_value=MockAsyncMessageResult(message_id=expected_msg_id)
        )
        instance_manager.enqueue_message = mock_manager.enqueue_message

        result = await instance_manager.resume_processing_job(
            instance_id, message="resume"
        )

        assert result["message_id"] == expected_msg_id


class TestChildNotificationJobQueuePath:
    """Test suite for JobQueue path (root instances) in new queue flow."""

    @pytest.mark.asyncio
    async def test_parent_resumes_from_checkpoint(self, instance_manager, mock_manager):
        """Root instance (has old_jobs) should resume from checkpoint via _process_message_with_tracking."""
        instance_id = "parent-instance-123"
        job_id = "job-abc-789"

        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[MockJob(job_id=job_id, message_id="msg-456")]
        )

        # Instance is complete (waiting_for=0)
        mock_manager._instance_repository.get = MagicMock(
            return_value=MockInstanceMeta(
                instance_id=instance_id,
                status=InstanceStatus.RUNNING.value,
                waiting_for=0
            )
        )

        result = await instance_manager.resume_processing_job(
            instance_id, message="resume", silent=False
        )

        # Should call _process_message_with_tracking with is_retry=True
        mock_manager._process_message_with_tracking.assert_called_once()
        call_kwargs = mock_manager._process_message_with_tracking.call_args[1]
        assert call_kwargs["instance_id"] == instance_id
        assert call_kwargs["is_retry"] is True
        assert call_kwargs["message_source"] == "cascade_resume"

        # Should complete the existing job as COMPLETED
        from daemon.services.job_queue_service import DemandState
        mock_manager._job_queue_service.complete_job.assert_called_once_with(
            job_id, DemandState.COMPLETED, result_summary="Resume completed"
        )

        # Should call _process_child_completion_and_notify_parent
        mock_manager._process_child_completion_and_notify_parent.assert_called_once()

        # Verify return
        assert result["instance_id"] == instance_id
        assert result["job_id"] == job_id
        assert result["message_id"] is not None

    @pytest.mark.asyncio
    async def test_parent_silent_mode_resumes_checkpoint(self, instance_manager, mock_manager):
        """Silent mode should pass empty message to _process_message_with_tracking."""
        instance_id = "parent-instance-silent"
        job_id = "job-xyz-789"

        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[MockJob(job_id=job_id, message_id="msg-456")]
        )

        # Instance is complete (waiting_for=0)
        mock_manager._instance_repository.get = MagicMock(
            return_value=MockInstanceMeta(
                instance_id=instance_id,
                status=InstanceStatus.RUNNING.value,
                waiting_for=0
            )
        )

        await instance_manager.resume_processing_job(
            instance_id, message="resume", silent=True
        )

        call_kwargs = mock_manager._process_message_with_tracking.call_args[1]
        # When silent=True, message should be empty string
        assert call_kwargs["message"] == ""


class TestChildNotificationErrorHandling:
    """Test suite for error handling in new queue flow."""

    @pytest.mark.asyncio
    async def test_workerpool_enqueue_failure_returns_none(self, instance_manager, mock_manager):
        """When enqueue_message fails, should return None."""
        instance_id = "child-instance-fail"

        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[]
        )

        mock_manager.enqueue_message.side_effect = RuntimeError("enqueue failed")

        result = await instance_manager.resume_processing_job(
            instance_id, message="resume"
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_jobqueue_process_failure_returns_none(self, instance_manager, mock_manager):
        """When _process_message_with_tracking fails, should mark job as FAILED and return None."""
        from daemon.services.job_queue_service import DemandState

        instance_id = "parent-instance-fail"
        job_id = "job-123"

        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[MockJob(job_id=job_id, message_id="msg-456")]
        )

        mock_manager._process_message_with_tracking.side_effect = RuntimeError("processing failed")

        result = await instance_manager.resume_processing_job(
            instance_id, message="resume"
        )

        assert result is None

        # Job should be marked as FAILED (not cancelled)
        # Note: error message is prefixed with "Resume failed: "
        mock_manager._job_queue_service.complete_job.assert_called_once()
        call_args = mock_manager._job_queue_service.complete_job.call_args
        assert call_args[0][0] == job_id
        assert call_args[0][1] == DemandState.FAILED
        assert "processing failed" in call_args[1]["error"]
