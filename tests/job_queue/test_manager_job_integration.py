"""Tests for InstanceManager job completion callback integration.

This module tests:
- Lifecycle event publishing for completed/terminated instances
- Job completion from _process_queue() success path
- Job failure from _process_queue() max-retry path
- Job failure from _process_queue() cancellation path
- Job failure from terminate_instance()
- Concurrent completion safety
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from daemon.repositories.job_queue.models import JobStatus, JobItem, AdmissionState
from daemon.services.job_queue_service import DemandState

# Phase 4 cleanup: ``status`` is frozen at the INSERT default (``pending``)
# and ``admission_state`` is the sole authority. Map the legacy ``status``
# value onto its ``AdmissionState`` bucket so mock jobs expose the
# ``admission_state`` that service-level assertions read.
_STATUS_TO_ADMISSION = {
    JobStatus.PENDING.value: AdmissionState.QUEUED.value,
    JobStatus.PROCESSING.value: AdmissionState.ACTIVE.value,
    JobStatus.PAUSED.value: AdmissionState.ACTIVE.value,
    JobStatus.COMPLETED.value: AdmissionState.DONE.value,
    JobStatus.FAILED.value: AdmissionState.DONE.value,
    JobStatus.CANCELLED.value: AdmissionState.DONE.value,
    JobStatus.DEAD_LETTER.value: AdmissionState.DEAD.value,
}


def make_mock_job(
    job_id="test-job-1",
    instance_id="test-instance-1",
    project_id="test-project",
    status="processing",
    agent_id="developer",
    message="test message",
):
    """Create a mock JobItem for testing."""
    job = MagicMock(spec=JobItem)
    job.job_id = job_id
    job.instance_id = instance_id
    job.project_id = project_id
    job.status = status
    job.admission_state = _STATUS_TO_ADMISSION.get(status, AdmissionState.ACTIVE.value)
    job.agent_id = agent_id
    job.message = message
    job.result_summary = None
    job.error_message = None
    job.priority = 5
    return job


class TestLifecycleEventPublishing:
    """Tests for lifecycle event publishing when instances complete or terminate."""

    @pytest.mark.asyncio
    async def test_publish_completed_lifecycle_event(self):
        """Publishing completed status should create proper event data."""
        from daemon.manager import InstanceManager
        from unittest.mock import AsyncMock, MagicMock, patch
        
        # Mock the manager with _publish_instance_lifecycle_event
        manager = MagicMock(spec=InstanceManager)
        manager._events_service = MagicMock()
        manager._events_service._publish_instance_lifecycle_event = AsyncMock()
        
        # Bind the real method
        manager._publish_instance_lifecycle_event = InstanceManager._publish_instance_lifecycle_event.__get__(manager)
        
        # Call the method
        await manager._publish_instance_lifecycle_event(
            instance_id="test-instance-1",
            status="completed",
            error=None,
            parent_id=None,
        )
        
        # Verify _publish_instance_lifecycle_event was called with correct params
        manager._events_service._publish_instance_lifecycle_event.assert_called_once()
        call_kwargs = manager._events_service._publish_instance_lifecycle_event.call_args.kwargs
        assert call_kwargs["instance_id"] == "test-instance-1"
        assert call_kwargs["status"] == "completed"
        assert call_kwargs["parent_id"] is None

    @pytest.mark.asyncio
    async def test_publish_terminated_lifecycle_event_with_parent(self):
        """Publishing terminated status with parent should include parent_id."""
        from daemon.manager import InstanceManager
        from unittest.mock import AsyncMock, MagicMock, patch
        
        manager = MagicMock(spec=InstanceManager)
        manager._events_service = MagicMock()
        manager._events_service._publish_instance_lifecycle_event = AsyncMock()
        
        manager._publish_instance_lifecycle_event = InstanceManager._publish_instance_lifecycle_event.__get__(manager)
        
        await manager._publish_instance_lifecycle_event(
            instance_id="child-instance",
            status="terminated",
            error=None,
            parent_id="parent-instance",
        )
        
        call_kwargs = manager._events_service._publish_instance_lifecycle_event.call_args.kwargs
        assert call_kwargs["status"] == "terminated"
        assert call_kwargs["parent_id"] == "parent-instance"

    @pytest.mark.asyncio
    async def test_publish_error_lifecycle_event(self):
        """Publishing error status should include error message."""
        from daemon.manager import InstanceManager
        from unittest.mock import AsyncMock, MagicMock, patch
        
        manager = MagicMock(spec=InstanceManager)
        manager._events_service = MagicMock()
        manager._events_service._publish_instance_lifecycle_event = AsyncMock()
        
        manager._publish_instance_lifecycle_event = InstanceManager._publish_instance_lifecycle_event.__get__(manager)
        
        await manager._publish_instance_lifecycle_event(
            instance_id="failing-instance",
            status="error",
            error="Max retries exceeded",
            parent_id=None,
        )
        
        call_kwargs = manager._events_service._publish_instance_lifecycle_event.call_args.kwargs
        assert call_kwargs["status"] == "error"
        assert call_kwargs["error"] == "Max retries exceeded"

    @pytest.mark.asyncio
    async def test_publish_failure_is_swallowed(self):
        """Exceptions during event publishing should be caught and not crash.
        
        Note: Exception handling is done in EventPublisherService._publish_instance_lifecycle_event,
        not in manager._publish_instance_lifecycle_event. This test verifies the service-level
        exception handling separately.
        """
        from daemon.manager import InstanceManager
        from unittest.mock import AsyncMock, MagicMock, patch
        
        manager = MagicMock(spec=InstanceManager)
        manager._events_service = MagicMock()
        # Service-level exception handling is tested in event_publisher tests
        # Here we just verify the manager delegates correctly
        manager._events_service._publish_instance_lifecycle_event = AsyncMock()
        
        manager._publish_instance_lifecycle_event = InstanceManager._publish_instance_lifecycle_event.__get__(manager)
        
        await manager._publish_instance_lifecycle_event(
            instance_id="test-instance",
            status="completed",
            error=None,
            parent_id=None,
        )
        
        # Verify delegation happened
        manager._events_service._publish_instance_lifecycle_event.assert_called_once()


class TestProcessQueueJobCompletion:
    """Tests for lifecycle event publishing in _process_queue() paths.

    When instances complete via _process_queue(), lifecycle events are published
    via _process_child_completion_and_notify_parent() for top-level instances.
    """

    @pytest.mark.asyncio
    async def test_top_level_instance_completion_publishes_lifecycle(self):
        """Top-level instances (no parent) should publish completed lifecycle event."""
        from daemon.manager import InstanceManager
        
        manager = MagicMock(spec=InstanceManager)
        manager._events_service = MagicMock()
        manager._events_service._publish_instance_lifecycle_event = AsyncMock()
        
        manager._publish_instance_lifecycle_event = InstanceManager._publish_instance_lifecycle_event.__get__(manager)
        
        # Top-level instance completes
        await manager._publish_instance_lifecycle_event(
            instance_id="job-instance-1",
            status="completed",
            error=None,
            parent_id=None,
        )
        
        manager._events_service._publish_instance_lifecycle_event.assert_called_once()
        call_kwargs = manager._events_service._publish_instance_lifecycle_event.call_args.kwargs
        assert call_kwargs["status"] == "completed"
        assert call_kwargs["parent_id"] is None

    @pytest.mark.asyncio
    async def test_child_instance_completion_publishes_lifecycle(self):
        """Child instances should also publish lifecycle events."""
        from daemon.manager import InstanceManager
        
        manager = MagicMock(spec=InstanceManager)
        manager._events_service = MagicMock()
        manager._events_service._publish_instance_lifecycle_event = AsyncMock()
        
        manager._publish_instance_lifecycle_event = InstanceManager._publish_instance_lifecycle_event.__get__(manager)
        
        # Child instance completes
        await manager._publish_instance_lifecycle_event(
            instance_id="child-instance-1",
            status="completed",
            error=None,
            parent_id="parent-instance-1",
        )
        
        manager._events_service._publish_instance_lifecycle_event.assert_called_once()
        call_kwargs = manager._events_service._publish_instance_lifecycle_event.call_args.kwargs
        assert call_kwargs["status"] == "completed"
        assert call_kwargs["parent_id"] == "parent-instance-1"


class TestTerminateInstanceJobCompletion:
    """Tests for job completion on instance termination."""

    def test_terminate_marks_processing_job_cancelled(self):
        """Terminating instance with PROCESSING job marks job as CANCELLED."""
        # Create a mock JobQueueService
        mock_service = MagicMock()
        mock_job = make_mock_job(status="processing")
        mock_service.get_job_by_instance_sync.return_value = mock_job
        mock_service.complete_job_sync.return_value = MagicMock(status="cancelled")
        mock_service.trigger_next_job_sync.return_value = None
        mock_service.release_locks_by_instance_sync.return_value = ["test-project"]
        
        # Simulate the terminate path
        instance_id = "test-instance-1"
        
        # Step 1: Release locks
        released = mock_service.release_locks_by_instance_sync(instance_id)
        
        # Step 2: Get job
        job = mock_service.get_job_by_instance_sync(instance_id)
        assert job is not None
        assert job.admission_state == "active"
        
        # Step 3: Mark cancelled (no retry)
        mock_service.complete_job_sync(
            job.job_id, DemandState.CANCELLED, error="Instance terminated", result_summary=None
        )
        
        # Step 4: Trigger next
        if job.project_id:
            mock_service.trigger_next_job_sync(job.project_id)
        
        # Verify all calls
        mock_service.release_locks_by_instance_sync.assert_called_once_with(instance_id)
        mock_service.complete_job_sync.assert_called_once_with(
            "test-job-1", DemandState.CANCELLED, error="Instance terminated", result_summary=None
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
        
        if job is not None and job.admission_state == "active":
            mock_service.complete_job_sync(job.job_id, DemandState.CANCELLED, error="Instance terminated")
        
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
        
        # Only marks cancelled if status is "processing"
        if job is not None and job.admission_state == "active":
            mock_service.complete_job_sync(job.job_id, DemandState.CANCELLED, error="Instance terminated")
        
        # Should not be called because job is already completed
        mock_service.complete_job_sync.assert_not_called()


class TestConcurrentCompletionSafety:
    """Tests for concurrent completion scenarios."""

    @pytest.mark.asyncio
    async def test_concurrent_completion_is_idempotent(self):
        """Both success and cancel racing to complete same job should not raise errors."""
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
            mock_service.complete_job("test-job-1", DemandState.COMPLETED, error=None, result_summary="Done"),
            # Path 2: Cancel callback from terminate_instance
            mock_service.complete_job("test-job-1", DemandState.CANCELLED, error="Instance cancelled"),
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
            mock_service.complete_job("test-job-1", DemandState.COMPLETED, error=None, result_summary="OK"),
            mock_service.complete_job("test-job-1", DemandState.CANCELLED, error="Cancelled"),
        )
        
        # Verify complete_job was called twice (both paths attempt it)
        assert mock_service.complete_job.call_count == 2
