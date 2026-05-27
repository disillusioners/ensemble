"""Tests for waiting_for > 0 skip path in resume_processing_job (Round 2 fix).

The Round 2 fix changed from status-based check (status == WAITING_CHILDREN)
to waiting_for > 0 check. This is more correct because:
- status may be RUNNING during resume
- waiting_for accurately tracks pending child work regardless of status

When waiting_for > 0, complete_job() should be skipped because
JobFeedbackObserver will complete the job when all children finish.
"""

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


class TestWaitingForSkip:
    """Test suite for waiting_for > 0 skip behavior in parent resume (Round 2)."""

    @pytest.mark.asyncio
    async def test_waiting_for_one_skips_complete_job(
        self, instance_manager, mock_manager, caplog
    ):
        """CORE FIX TEST: waiting_for=1, status=RUNNING → complete_job() SKIPPED.

        This is the actual scenario that was failing before Round 2:
        - Status was RUNNING (not WAITING_CHILDREN)
        - But waiting_for was 1 (waiting for children)
        - The status check in Round 1 failed to detect this

        With waiting_for > 0 check, this now works correctly.
        """
        instance_id = "parent-instance-123"
        job_id = "job-abc-789"
        message_id = "msg-xyz-456"

        # Setup: old_jobs returns a PROCESSING job
        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[MockJob(job_id=job_id, message_id=message_id)]
        )

        # KEY: status=RUNNING, waiting_for=1 (the actual failing scenario)
        mock_manager._instance_repository.get = MagicMock(
            return_value=MockInstanceMeta(
                instance_id=instance_id,
                status=InstanceStatus.RUNNING.value,
                waiting_for=1
            )
        )

        with caplog.at_level(logging.INFO):
            result = await instance_manager.resume_processing_job(
                instance_id, message="resume"
            )

        # Verify _process_message_with_tracking was called
        mock_manager._process_message_with_tracking.assert_called_once()

        # Core assertion: complete_job should NOT be called
        mock_manager._job_queue_service.complete_job.assert_not_called()

        # Verify return value indicates waiting_children
        assert result == {"job_id": job_id, "message_id": message_id, "status": "waiting_children"}

        # Verify diagnostic log was emitted
        assert any(
            "waiting_for=1" in record.message and "skipping_completion=True" in record.message
            for record in caplog.records
        ), "Expected diagnostic log with waiting_for=1 and skipping_completion=True"

    @pytest.mark.asyncio
    async def test_waiting_for_zero_completes_job(self, instance_manager, mock_manager):
        """Normal path: waiting_for=0 → complete_job() SHOULD be called."""
        instance_id = "parent-instance-normal"
        job_id = "job-normal-123"
        message_id = "msg-normal-456"

        # Setup: old_jobs returns a PROCESSING job
        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[MockJob(job_id=job_id, message_id=message_id)]
        )

        # waiting_for=0 means no pending children, should complete
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

        # Verify complete_job WAS called
        mock_manager._job_queue_service.complete_job.assert_called_once()
        call_args = mock_manager._job_queue_service.complete_job.call_args
        assert call_args[0][0] == job_id
        assert call_args[0][1] == DemandState.COMPLETED

        # Verify return value has job_id
        assert result["job_id"] == job_id
        assert result["message_id"] == message_id

    @pytest.mark.asyncio
    async def test_waiting_for_none_completes_job(self, instance_manager, mock_manager):
        """Edge case: waiting_for=None should NOT skip (treat as 0)."""
        instance_id = "parent-instance-none-wf"
        job_id = "job-none-wf-123"
        message_id = "msg-none-wf-456"

        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[MockJob(job_id=job_id, message_id=message_id)]
        )

        # waiting_for=None should be treated as 0 (don't skip)
        mock_manager._instance_repository.get = MagicMock(
            return_value=MockInstanceMeta(
                instance_id=instance_id,
                status=InstanceStatus.RUNNING.value,
                waiting_for=None
            )
        )

        result = await instance_manager.resume_processing_job(
            instance_id, message="resume"
        )

        # complete_job should be called (None treated as 0)
        mock_manager._job_queue_service.complete_job.assert_called_once()

        # Verify return value has job_id
        assert result["job_id"] == job_id

    @pytest.mark.asyncio
    async def test_waiting_for_multiple_skips_complete_job(self, instance_manager, mock_manager):
        """Edge case: waiting_for=3 (multiple children) → should skip completion."""
        instance_id = "parent-instance-multi"
        job_id = "job-multi-123"
        message_id = "msg-multi-456"

        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[MockJob(job_id=job_id, message_id=message_id)]
        )

        # waiting_for=3 means waiting for 3 children
        mock_manager._instance_repository.get = MagicMock(
            return_value=MockInstanceMeta(
                instance_id=instance_id,
                status=InstanceStatus.RUNNING.value,
                waiting_for=3
            )
        )

        result = await instance_manager.resume_processing_job(
            instance_id, message="resume"
        )

        # complete_job should NOT be called
        mock_manager._job_queue_service.complete_job.assert_not_called()

        # Verify return value
        assert result["job_id"] == job_id
        assert result["status"] == "waiting_children"

    @pytest.mark.asyncio
    async def test_instance_not_found_completes_job(self, instance_manager, mock_manager):
        """When instance is None (not found), complete_job SHOULD be called (falls through)."""
        instance_id = "parent-instance-none"
        job_id = "job-none-123"
        message_id = "msg-none-456"

        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[MockJob(job_id=job_id, message_id=message_id)]
        )

        # Instance not found (None) - should fall through to complete_job
        mock_manager._instance_repository.get = MagicMock(return_value=None)

        result = await instance_manager.resume_processing_job(
            instance_id, message="resume"
        )

        # complete_job WAS called (exception handler or None default)
        mock_manager._job_queue_service.complete_job.assert_called_once()

        # Verify return value has job_id
        assert result["job_id"] == job_id

    @pytest.mark.asyncio
    async def test_repository_exception_completes_job(self, instance_manager, mock_manager):
        """When instance_repository.get raises, complete_job SHOULD still be called."""
        instance_id = "parent-instance-exception"
        job_id = "job-exception-123"
        message_id = "msg-exception-456"

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

        # complete_job WAS called (exception handler allows it)
        mock_manager._job_queue_service.complete_job.assert_called_once()

        # Verify return value has job_id
        assert result["job_id"] == job_id

    @pytest.mark.asyncio
    async def test_diagnostic_log_emitted_with_correct_values(
        self, instance_manager, mock_manager, caplog
    ):
        """Verify the diagnostic log message contains correct waiting_for and skip values."""
        instance_id = "parent-instance-diag"
        job_id = "job-diag-123"
        message_id = "msg-diag-456"

        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[MockJob(job_id=job_id, message_id=message_id)]
        )

        mock_manager._instance_repository.get = MagicMock(
            return_value=MockInstanceMeta(
                instance_id=instance_id,
                status=InstanceStatus.RUNNING.value,
                waiting_for=2
            )
        )

        with caplog.at_level(logging.INFO):
            result = await instance_manager.resume_processing_job(
                instance_id, message="resume"
            )

        # Verify complete_job was skipped
        mock_manager._job_queue_service.complete_job.assert_not_called()

        # Verify log contains expected values
        log_found = False
        for record in caplog.records:
            if f"waiting_for=2" in record.message and "skipping_completion=True" in record.message:
                log_found = True
                break

        assert log_found, "Expected log with waiting_for=2 and skipping_completion=True"

        # Verify return value
        assert result["status"] == "waiting_children"

    @pytest.mark.asyncio
    async def test_waiting_for_one_with_waiting_children_status_skips(
        self, instance_manager, mock_manager
    ):
        """Both waiting_for=1 AND status=WAITING_CHILDREN should skip (belt and suspenders)."""
        instance_id = "parent-instance-both"
        job_id = "job-both-123"
        message_id = "msg-both-456"

        mock_manager._job_queue_service._repository.find_processing_message_jobs_by_instance = MagicMock(
            return_value=[MockJob(job_id=job_id, message_id=message_id)]
        )

        # Both conditions present - should skip
        mock_manager._instance_repository.get = MagicMock(
            return_value=MockInstanceMeta(
                instance_id=instance_id,
                status=InstanceStatus.WAITING_CHILDREN.value,
                waiting_for=1
            )
        )

        result = await instance_manager.resume_processing_job(
            instance_id, message="resume"
        )

        # complete_job should NOT be called
        mock_manager._job_queue_service.complete_job.assert_not_called()

        # Verify return value
        assert result["job_id"] == job_id
        assert result["status"] == "waiting_children"
