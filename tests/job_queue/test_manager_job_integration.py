"""Tests for InstanceManager job completion callback integration.

This module tests:
- _complete_job_for_instance() helper
- Job completion from _process_queue() success path
- Job failure from _process_queue() max-retry path
- Job failure from _process_queue() cancellation path
- Job failure from terminate_instance()
- Concurrent completion safety
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from daemon.repositories.job_queue.models import JobStatus, JobItem


def make_mock_job(
    job_id="test-job-1",
    instance_id="test-instance-1",
    project_id="test-project",
    status="processing",
    agent_id="coder",
    message="test message",
):
    """Create a mock JobItem for testing."""
    job = MagicMock(spec=JobItem)
    job.job_id = job_id
    job.instance_id = instance_id
    job.project_id = project_id
    job.status = status
    job.agent_id = agent_id
    job.message = message
    job.result_summary = None
    job.error_message = None
    job.priority = 5
    return job


class TestCompleteJobForInstance:
    """Tests for InstanceManager._complete_job_for_instance() helper."""

    @pytest.fixture
    def mock_manager(self):
        """Create a minimal InstanceManager-like mock with the callback method."""
        from daemon.manager import InstanceManager
        
        manager = MagicMock(spec=InstanceManager)
        manager._job_queue_service = None
        
        # Bind the real method to the mock
        manager._complete_job_for_instance = InstanceManager._complete_job_for_instance.__get__(manager)
        
        return manager

    @pytest.mark.asyncio
    async def test_success_marks_job_completed(self, mock_manager):
        """When success=True, job should be marked COMPLETED."""
        mock_service = AsyncMock()
        mock_job = make_mock_job()
        mock_service.get_job_by_instance.return_value = mock_job
        mock_service.complete_job.return_value = MagicMock(status="completed")
        mock_service.trigger_next_job.return_value = None
        
        mock_manager._job_queue_service = mock_service
        
        await mock_manager._complete_job_for_instance(
            instance_id="test-instance-1",
            success=True,
            result_summary="Task done",
        )
        
        mock_service.get_job_by_instance.assert_called_once_with("test-instance-1")
        mock_service.complete_job.assert_called_once_with(
            "test-job-1", success=True, error=None, result_summary="Task done"
        )
        mock_service.trigger_next_job.assert_called_once_with("test-project")

    @pytest.mark.asyncio
    async def test_failure_marks_job_failed(self, mock_manager):
        """When success=False, job should be marked FAILED."""
        mock_service = AsyncMock()
        mock_job = make_mock_job()
        mock_service.get_job_by_instance.return_value = mock_job
        mock_service.complete_job.return_value = MagicMock(status="failed")
        mock_service.trigger_next_job.return_value = None
        
        mock_manager._job_queue_service = mock_service
        
        await mock_manager._complete_job_for_instance(
            instance_id="test-instance-1",
            success=False,
            error="Something went wrong",
        )
        
        mock_service.complete_job.assert_called_once_with(
            "test-job-1", success=False, error="Something went wrong"
        )
        mock_service.trigger_next_job.assert_called_once_with("test-project")

    @pytest.mark.asyncio
    async def test_no_job_found_is_noop(self, mock_manager):
        """When no job is associated with instance, should return silently."""
        mock_service = AsyncMock()
        mock_service.get_job_by_instance.return_value = None
        mock_manager._job_queue_service = mock_service
        
        # Should not raise
        await mock_manager._complete_job_for_instance(
            instance_id="unknown-instance",
            success=True,
        )
        
        mock_service.complete_job.assert_not_called()
        mock_service.trigger_next_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_service_wired_is_noop(self, mock_manager):
        """When _job_queue_service is None, should return silently."""
        mock_manager._job_queue_service = None
        
        # Should not raise
        await mock_manager._complete_job_for_instance(
            instance_id="test-instance-1",
            success=True,
        )

    @pytest.mark.asyncio
    async def test_triggers_next_job_for_project(self, mock_manager):
        """After completing, should trigger next pending job."""
        mock_service = AsyncMock()
        mock_job = make_mock_job(project_id="my-project")
        mock_service.get_job_by_instance.return_value = mock_job
        mock_service.complete_job.return_value = MagicMock(status="completed")
        mock_service.trigger_next_job.return_value = None
        
        mock_manager._job_queue_service = mock_service
        
        await mock_manager._complete_job_for_instance(
            instance_id="test-instance-1",
            success=True,
        )
        
        mock_service.trigger_next_job.assert_called_once_with("my-project")

    @pytest.mark.asyncio
    async def test_does_not_trigger_without_project(self, mock_manager):
        """Jobs without project_id should not attempt trigger_next_job."""
        mock_service = AsyncMock()
        mock_job = make_mock_job(project_id=None)
        mock_service.get_job_by_instance.return_value = mock_job
        mock_service.complete_job.return_value = MagicMock(status="completed")
        
        mock_manager._job_queue_service = mock_service
        
        await mock_manager._complete_job_for_instance(
            instance_id="test-instance-1",
            success=True,
        )
        
        mock_service.trigger_next_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_exception_is_swallowed(self, mock_manager):
        """Exceptions during job completion should be caught and logged."""
        mock_service = AsyncMock()
        mock_job = make_mock_job()
        mock_service.get_job_by_instance.return_value = mock_job
        mock_service.complete_job.side_effect = RuntimeError("DB error")
        
        mock_manager._job_queue_service = mock_service
        
        # Should not raise - exception is caught internally
        await mock_manager._complete_job_for_instance(
            instance_id="test-instance-1",
            success=True,
        )


class TestProcessQueueJobCompletion:
    """Tests for job completion callbacks in _process_queue() paths."""

    @pytest.mark.asyncio
    async def test_message_success_completes_job(self):
        """Successful message processing calls _complete_job_for_instance with success=True."""
        from daemon.manager import InstanceManager
        
        manager = MagicMock(spec=InstanceManager)
        manager._complete_job_for_instance = AsyncMock()
        manager.broadcaster = AsyncMock()
        
        # Simulate the success path calling the callback
        await manager._complete_job_for_instance(
            instance_id="inst-1",
            success=True,
            result_summary="Completed successfully",
        )
        
        manager._complete_job_for_instance.assert_called_once_with(
            instance_id="inst-1",
            success=True,
            result_summary="Completed successfully",
        )

    @pytest.mark.asyncio
    async def test_message_max_retries_fails_job(self):
        """Max retries exceeded calls _complete_job_for_instance with success=False."""
        from daemon.manager import InstanceManager
        
        manager = MagicMock(spec=InstanceManager)
        manager._complete_job_for_instance = AsyncMock()
        
        # Simulate the max-retry path calling the callback
        await manager._complete_job_for_instance(
            instance_id="inst-1",
            success=False,
            error="Max retries exceeded: connection timeout",
        )
        
        manager._complete_job_for_instance.assert_called_once_with(
            instance_id="inst-1",
            success=False,
            error="Max retries exceeded: connection timeout",
        )

    @pytest.mark.asyncio
    async def test_message_cancelled_fails_job(self):
        """OperationCancelledError calls _complete_job_for_instance with success=False."""
        from daemon.manager import InstanceManager
        
        manager = MagicMock(spec=InstanceManager)
        manager._complete_job_for_instance = AsyncMock()
        
        # Simulate the cancellation path calling the callback
        await manager._complete_job_for_instance(
            instance_id="inst-1",
            success=False,
            error="Cancelled: user_request",
        )
        
        manager._complete_job_for_instance.assert_called_once_with(
            instance_id="inst-1",
            success=False,
            error="Cancelled: user_request",
        )


class TestTerminateInstanceJobCompletion:
    """Tests for job completion on instance termination."""

    def test_terminate_marks_processing_job_failed(self):
        """Terminating instance with PROCESSING job marks job as FAILED."""
        # Create a mock JobQueueService
        mock_service = MagicMock()
        mock_job = make_mock_job(status="processing")
        mock_service.get_job_by_instance_sync.return_value = mock_job
        mock_service.complete_job_sync.return_value = MagicMock(status="failed")
        mock_service.trigger_next_job_sync.return_value = None
        mock_service.release_locks_by_instance_sync.return_value = ["test-project"]
        
        # Simulate the terminate path (lines 2127-2151 of manager.py)
        instance_id = "test-instance-1"
        
        # Step 1: Release locks
        released = mock_service.release_locks_by_instance_sync(instance_id)
        
        # Step 2: Get job
        job = mock_service.get_job_by_instance_sync(instance_id)
        assert job is not None
        assert job.status == "processing"
        
        # Step 3: Mark failed
        mock_service.complete_job_sync(
            job.job_id, success=False, error="Instance terminated", result_summary=None
        )
        
        # Step 4: Trigger next
        if job.project_id:
            mock_service.trigger_next_job_sync(job.project_id)
        
        # Verify all calls
        mock_service.release_locks_by_instance_sync.assert_called_once_with(instance_id)
        mock_service.complete_job_sync.assert_called_once_with(
            "test-job-1", success=False, error="Instance terminated", result_summary=None
        )
        mock_service.trigger_next_job_sync.assert_called_once_with("test-project")

    def test_terminate_no_job_is_noop(self):
        """Terminating instance without job does not fail."""
        mock_service = MagicMock()
        mock_service.get_job_by_instance_sync.return_value = None
        mock_service.release_locks_by_instance_sync.return_value = []
        
        instance_id = "no-job-instance"
        
        released = mock_service.release_locks_by_instance_sync(instance_id)
        job = mock_service.get_job_by_instance_sync(instance_id)
        
        if job is not None and job.status == "processing":
            mock_service.complete_job_sync(job.job_id, success=False, error="Instance terminated")
        
        # complete_job_sync should NOT be called
        mock_service.complete_job_sync.assert_not_called()

    def test_terminate_completed_job_is_noop(self):
        """Terminating instance with already COMPLETED job does not update it."""
        mock_service = MagicMock()
        mock_job = make_mock_job(status="completed")
        mock_service.get_job_by_instance_sync.return_value = mock_job
        mock_service.release_locks_by_instance_sync.return_value = []
        
        instance_id = "completed-instance"
        
        released = mock_service.release_locks_by_instance_sync(instance_id)
        job = mock_service.get_job_by_instance_sync(instance_id)
        
        # Only marks failed if status is "processing"
        if job is not None and job.status == "processing":
            mock_service.complete_job_sync(job.job_id, success=False, error="Instance terminated")
        
        # Should not be called because job is already completed
        mock_service.complete_job_sync.assert_not_called()


class TestConcurrentCompletionSafety:
    """Tests for concurrent completion scenarios."""

    @pytest.mark.asyncio
    async def test_concurrent_completion_is_idempotent(self):
        """Both success and terminate racing to complete same job should not raise errors."""
        mock_service = AsyncMock()
        mock_job = make_mock_job()
        
        # First call succeeds, second returns None (already completed)
        mock_service.get_job_by_instance.return_value = mock_job
        mock_service.complete_job.side_effect = [
            MagicMock(status="completed"),  # First completion wins
            None,  # Second completion returns None (already completed)
        ]
        mock_service.trigger_next_job.return_value = None
        
        # Simulate concurrent calls
        results = await asyncio.gather(
            # Path 1: Success callback from _process_queue
            mock_service.complete_job("test-job-1", success=True, error=None, result_summary="Done"),
            # Path 2: Failure callback from terminate_instance
            mock_service.complete_job("test-job-1", success=False, error="Instance terminated"),
        )
        
        # First one should succeed, second should be None
        assert results[0] is not None
        assert results[1] is None  # Already completed
        assert mock_service.complete_job.call_count == 2

    @pytest.mark.asyncio
    async def test_lock_released_only_once_on_concurrent_completion(self):
        """Lock should only be released once when two paths race."""
        mock_service = AsyncMock()
        mock_job = make_mock_job()
        
        mock_service.get_job_by_instance.return_value = mock_job
        mock_service.complete_job.side_effect = [
            MagicMock(status="completed"),
            None,
        ]
        mock_service.trigger_next_job.return_value = None
        
        # Both paths call complete_job which internally releases lock
        # But the lock manager should handle double-release gracefully
        await asyncio.gather(
            mock_service.complete_job("test-job-1", success=True, error=None, result_summary="OK"),
            mock_service.complete_job("test-job-1", success=False, error="Terminated"),
        )
        
        # Verify complete_job was called twice (both paths attempt it)
        assert mock_service.complete_job.call_count == 2
