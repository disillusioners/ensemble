"""Tests for resume_processing_job new queue flow behavior.

The new implementation routes through the normal queue flow:
- Child instances (no old_jobs): enqueue_message() via WorkerPool
- Parent instances (has old_jobs): cancel old jobs, complete stale messages, enqueue_message_via_jq()

The waiting_for > 0 check is now handled by JobFeedbackObserver, not resume_processing_job.
"""

import uuid
import pytest
from unittest.mock import MagicMock, AsyncMock
import logging

from daemon.manager import InstanceManager
from daemon.config import Config
from daemon.repositories.instance.models import InstanceStatus
from daemon.services.job_queue_service import DemandState


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


class MockAsyncMessageResult:
    """Mock async message result returned by enqueue_message."""

    def __init__(self, message_id: str = None):
        self.message_id = message_id or str(uuid.uuid4())
        self.instance_id = None
        self.status = "queued"


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
    # Mock enqueue_message for WorkerPool path
    manager.enqueue_message = AsyncMock(return_value=MockAsyncMessageResult())
    # Mock enqueue_message_via_jq for JobQueue path
    manager.enqueue_message_via_jq = AsyncMock(return_value=MockAsyncMessageResult())
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
    manager.enqueue_message_via_jq = mock_manager.enqueue_message_via_jq
    return manager


class TestResumeQueueFlow:
    """Test suite for new resume queue flow behavior.

    The new implementation routes through the normal queue flow:
    - Child instances (no old_jobs): enqueue_message() via WorkerPool
    - Parent instances (has old_jobs): cancel old jobs, complete stale messages, enqueue_message_via_jq()

    The waiting_for > 0 check is now handled by JobFeedbackObserver, not resume_processing_job.
    """

    @pytest.mark.asyncio
    async def test_parent_instance_with_old_jobs_enqueues_via_jobqueue(
        self, instance_manager, mock_manager
    ):
        """Parent instance with old_jobs should cancel them and enqueue via JobQueue."""
        instance_id = "parent-instance-123"
        job_id = "job-abc-789"
        message_id = "msg-xyz-456"

        # Setup: old_jobs returns a PROCESSING job
        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[MockJob(job_id=job_id, message_id=message_id)]
        )

        result = await instance_manager.resume_processing_job(
            instance_id, message="resume"
        )

        # Old job should be cancelled
        mock_manager._job_queue_service.complete_job.assert_called()
        call_args = mock_manager._job_queue_service.complete_job.call_args
        assert call_args[0][0] == job_id  # job_id
        assert call_args[0][1] == DemandState.CANCELLED  # cancelled

        # Should enqueue via JobQueue path
        mock_manager.enqueue_message_via_jq.assert_called_once()
        jq_kwargs = mock_manager.enqueue_message_via_jq.call_args[1]
        assert jq_kwargs["instance_id"] == instance_id
        assert jq_kwargs["message"] == "resume"
        assert jq_kwargs["source"] == "cascade_resume"

        # Return should include message_id from enqueue
        assert result["instance_id"] == instance_id
        assert result["message_id"] is not None

    @pytest.mark.asyncio
    async def test_child_instance_no_old_jobs_enqueues_via_workerpool(
        self, instance_manager, mock_manager
    ):
        """Child instance (no old_jobs) should enqueue via WorkerPool."""
        instance_id = "child-instance-456"

        # Setup: no old jobs (child instance uses WorkerPool)
        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[]
        )

        result = await instance_manager.resume_processing_job(
            instance_id, message="resume", silent=True
        )

        # Should NOT call JobQueue path
        mock_manager.enqueue_message_via_jq.assert_not_called()

        # Should enqueue via WorkerPool path
        mock_manager.enqueue_message.assert_called_once()
        wp_kwargs = mock_manager.enqueue_message.call_args[1]
        assert wp_kwargs["instance_id"] == instance_id
        assert wp_kwargs["message"] == "resume"
        assert wp_kwargs["source"] == "cascade_resume"
        assert wp_kwargs["metadata"]["resume_mode"] is True

        # Return should include message_id
        assert result["instance_id"] == instance_id
        assert result["job_id"] is None
        assert result["message_id"] is not None

    @pytest.mark.asyncio
    async def test_silent_mode_passes_resume_mode_true(
        self, instance_manager, mock_manager
    ):
        """silent=True should pass resume_mode=True to enqueue."""
        instance_id = "child-instance-silent"

        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[]
        )

        await instance_manager.resume_processing_job(
            instance_id, message="resume", silent=True
        )

        mock_manager.enqueue_message.assert_called_once()
        wp_kwargs = mock_manager.enqueue_message.call_args[1]
        assert wp_kwargs["metadata"]["resume_mode"] is True

    @pytest.mark.asyncio
    async def test_non_silent_mode_passes_resume_mode_false(
        self, instance_manager, mock_manager
    ):
        """silent=False should pass resume_mode=False to enqueue."""
        instance_id = "child-instance-non-silent"

        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[]
        )

        await instance_manager.resume_processing_job(
            instance_id, message="continue working", silent=False
        )

        mock_manager.enqueue_message.assert_called_once()
        wp_kwargs = mock_manager.enqueue_message.call_args[1]
        assert wp_kwargs["metadata"]["resume_mode"] is False

    @pytest.mark.asyncio
    async def test_multiple_old_jobs_all_cancelled(
        self, instance_manager, mock_manager
    ):
        """Multiple old jobs should all be cancelled."""
        instance_id = "parent-instance-multi"
        job_id_1 = "job-1-123"
        job_id_2 = "job-2-456"

        # Setup: multiple old jobs
        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[
                MockJob(job_id=job_id_1, message_id="msg-1"),
                MockJob(job_id=job_id_2, message_id="msg-2"),
            ]
        )

        result = await instance_manager.resume_processing_job(
            instance_id, message="resume"
        )

        # Both jobs should be cancelled
        assert mock_manager._job_queue_service.complete_job.call_count == 2

        # Should still enqueue via JobQueue
        mock_manager.enqueue_message_via_jq.assert_called_once()

    @pytest.mark.asyncio
    async def test_enqueue_failure_returns_none(
        self, instance_manager, mock_manager
    ):
        """When enqueue fails, should return None."""
        instance_id = "child-instance-fail"

        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[]
        )

        # Simulate enqueue failure
        mock_manager.enqueue_message.side_effect = RuntimeError("enqueue failed")

        result = await instance_manager.resume_processing_job(
            instance_id, message="resume"
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_parent_resume_with_waiting_for_uses_jobqueue_path(
        self, instance_manager, mock_manager
    ):
        """Parent with old_jobs uses JobQueue path regardless of waiting_for."""
        instance_id = "parent-waiting-for"
        job_id = "job-waiting-123"

        # Has old_jobs → uses JobQueue path
        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[MockJob(job_id=job_id, message_id="msg-1")]
        )

        # Even with waiting_for > 0, should use JobQueue path
        mock_manager._instance_repository.get = MagicMock(
            return_value=MockInstanceMeta(
                instance_id=instance_id,
                status=InstanceStatus.RUNNING.value,
                waiting_for=2
            )
        )

        result = await instance_manager.resume_processing_job(
            instance_id, message="resume"
        )

        # Should use JobQueue path
        mock_manager.enqueue_message_via_jq.assert_called_once()
        mock_manager.enqueue_message.assert_not_called()

        # Old job should be cancelled
        mock_manager._job_queue_service.complete_job.assert_called()

        # Result should have message_id
        assert result["message_id"] is not None
