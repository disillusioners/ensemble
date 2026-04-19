"""Comprehensive tests for JobFeedbackObserver.

Tests the observer that subscribes to EventBus and propagates
instance lifecycle events to job completion.

These tests focus on unit-testing the internal _process_event() method directly,
without relying on the full async event loop to avoid timing issues.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from daemon.repositories.job_queue import JobRepository, JobStatus
from daemon.repositories.job_queue.models import JobItem
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.services.job_feedback_observer import JobFeedbackObserver
from daemon.services.job_state_machine import InvalidTransitionError


def create_mock_job(job_id: str = "test-job-id", status: str = "processing", instance_id: str = "test-instance-id") -> MagicMock:
    """Create a mock JobItem with the specified attributes."""
    mock_job = MagicMock(spec=JobItem)
    mock_job.job_id = job_id
    mock_job.status = status
    mock_job.instance_id = instance_id
    return mock_job


def create_mock_observer(
    job_queue_service: AsyncMock = None,
    job_repo: MagicMock = None,
    lock_repo: MagicMock = None,
    project_repo: MagicMock = None,
    config: MagicMock = None,
    instance_manager: MagicMock = None,
) -> tuple[JobFeedbackObserver, MagicMock, MagicMock, MagicMock, MagicMock, AsyncMock]:
    """Create a JobFeedbackObserver with mocked dependencies."""
    mock_event_bus = MagicMock()
    mock_job_queue_service = job_queue_service or AsyncMock()
    mock_job_repo = job_repo or MagicMock(spec=JobRepository)
    mock_lock_repo = lock_repo or MagicMock(spec=LockRepository)
    mock_project_repo = project_repo or MagicMock()
    mock_instance_manager = instance_manager or MagicMock()
    
    observer = JobFeedbackObserver(
        event_bus=mock_event_bus,
        job_queue_service=mock_job_queue_service,
        job_repo=mock_job_repo,
        lock_repo=mock_lock_repo,
        project_repo=mock_project_repo,
        instance_manager=mock_instance_manager,
        config=config,
    )
    
    return observer, mock_event_bus, mock_job_repo, mock_lock_repo, mock_project_repo, mock_job_queue_service


class TestObserverFiltersLifecycleEvents:
    """Tests for event filtering."""

    @pytest.mark.asyncio
    async def test_observer_filters_lifecycle_events(self):
        """Only instance_lifecycle events should be processed."""
        mock_job = create_mock_job(job_id="job-123", status="processing", instance_id="instance-456")
        mock_job_queue_service = AsyncMock(return_value=mock_job)
        mock_job_repo = MagicMock(spec=JobRepository)
        mock_lock_repo = MagicMock(spec=LockRepository)
        
        observer, _, mock_job_repo, _, _, _ = create_mock_observer(
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
        )
        
        # Non-lifecycle event should be ignored
        event = {
            "event_type": "checkpoint",
            "data": {"instance_id": "instance-456"}
        }
        await observer._process_event(event)
        mock_job_queue_service.get_job_by_instance.assert_not_called()


class TestObserverCompletesJob:
    """Tests for job completion on instance completion."""

    @pytest.mark.asyncio
    async def test_observer_completes_job_on_instance_completed(self):
        """When instance completes, job transitions PROCESSING -> COMPLETED."""
        mock_job = create_mock_job(job_id="job-123", status="processing", instance_id="instance-456")
        mock_job_queue_service = MagicMock()
        mock_job_queue_service.get_job_by_instance = AsyncMock(return_value=mock_job)
        mock_job_repo = MagicMock(spec=JobRepository)
        mock_lock_repo = MagicMock(spec=LockRepository)
        mock_lock_repo.release_by_instance.return_value = 1
        
        observer = JobFeedbackObserver(
            event_bus=MagicMock(),
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
            project_repo=MagicMock(),
            instance_manager=MagicMock(),
        )
        
        event = {
            "event_type": "instance_lifecycle",
            "data": {
                "instance_id": "instance-456",
                "status": "completed",
                "error": None,
            }
        }
        
        await observer._process_event(event)
        
        mock_job_repo.atomic_transition.assert_called_once_with(
            job_id="job-123",
            from_status=JobStatus.PROCESSING.value,
            to_status=JobStatus.COMPLETED.value,
        )
        mock_lock_repo.release_by_instance.assert_called_once_with("instance-456")


class TestObserverFailsJob:
    """Tests for job failure on instance error."""

    @pytest.mark.asyncio
    async def test_observer_fails_job_on_instance_error(self):
        """When instance errors, job transitions PROCESSING -> FAILED."""
        mock_job = create_mock_job(job_id="job-123", status="processing", instance_id="instance-456")
        mock_job_queue_service = MagicMock()
        mock_job_queue_service.get_job_by_instance = AsyncMock(return_value=mock_job)
        mock_job_repo = MagicMock(spec=JobRepository)
        mock_lock_repo = MagicMock(spec=LockRepository)
        mock_lock_repo.release_by_instance.return_value = 1
        
        observer = JobFeedbackObserver(
            event_bus=MagicMock(),
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
            project_repo=MagicMock(),
            instance_manager=MagicMock(),
        )
        
        event = {
            "event_type": "instance_lifecycle",
            "data": {
                "instance_id": "instance-456",
                "status": "error",
                "error": "Something went wrong",
            }
        }
        
        await observer._process_event(event)
        
        mock_job_repo.atomic_transition.assert_called_once_with(
            job_id="job-123",
            from_status=JobStatus.PROCESSING.value,
            to_status=JobStatus.FAILED.value,
            error_message="Something went wrong",
        )
        mock_lock_repo.release_by_instance.assert_called_once_with("instance-456")


class TestObserverSkipsTerminated:
    """Tests for skipping terminated events."""

    @pytest.mark.asyncio
    async def test_observer_skips_terminated_status(self):
        """Terminated events are skipped (terminate_instance handles them)."""
        mock_job_queue_service = AsyncMock()
        mock_job_repo = MagicMock(spec=JobRepository)
        mock_lock_repo = MagicMock(spec=LockRepository)
        
        observer, _, mock_job_repo, _, _, _ = create_mock_observer(
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
        )
        
        event = {
            "event_type": "instance_lifecycle",
            "data": {
                "instance_id": "instance-456",
                "status": "terminated",
                "error": None,
            }
        }
        
        await observer._process_event(event)
        
        mock_job_queue_service.get_job_by_instance.assert_not_called()
        mock_job_repo.atomic_transition.assert_not_called()


class TestObserverSkipsNoJob:
    """Tests for skipping events with no associated job."""

    @pytest.mark.asyncio
    async def test_observer_skips_when_no_job_found(self):
        """Event for instance with no job -> no error, skip silently."""
        mock_job_queue_service = AsyncMock(return_value=None)
        mock_job_repo = MagicMock(spec=JobRepository)
        mock_lock_repo = MagicMock(spec=LockRepository)
        
        observer, _, mock_job_repo, _, _, mock_job_queue_service = create_mock_observer(
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
        )
        
        event = {
            "event_type": "instance_lifecycle",
            "data": {
                "instance_id": "instance-no-job",
                "status": "completed",
                "error": None,
            }
        }
        
        await observer._process_event(event)
        
        mock_job_queue_service.get_job_by_instance.assert_called_once_with("instance-no-job")
        mock_job_repo.atomic_transition.assert_not_called()
        mock_lock_repo.release_by_instance.assert_not_called()


class TestObserverSkipsNonProcessingJob:
    """Tests for skipping events when job is not in PROCESSING state."""

    @pytest.mark.asyncio
    async def test_observer_skips_when_job_not_processing(self):
        """Job already COMPLETED -> skip."""
        mock_job = create_mock_job(job_id="job-123", status="completed", instance_id="instance-456")
        mock_job_queue_service = AsyncMock(return_value=mock_job)
        mock_job_repo = MagicMock(spec=JobRepository)
        mock_lock_repo = MagicMock(spec=LockRepository)
        
        observer, _, mock_job_repo, _, _, _ = create_mock_observer(
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
        )
        
        event = {
            "event_type": "instance_lifecycle",
            "data": {
                "instance_id": "instance-456",
                "status": "completed",
                "error": None,
            }
        }
        
        await observer._process_event(event)
        
        mock_job_queue_service.get_job_by_instance.assert_called()
        mock_job_repo.atomic_transition.assert_not_called()


class TestObserverMissingDataHandling:
    """Tests for missing data field handling."""

    @pytest.mark.asyncio
    async def test_missing_data_handled(self):
        """Event with None data is handled gracefully."""
        mock_job_queue_service = AsyncMock()
        mock_job_repo = MagicMock(spec=JobRepository)
        mock_lock_repo = MagicMock(spec=LockRepository)
        
        observer, _, mock_job_repo, _, _, _ = create_mock_observer(
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
        )
        
        event = {
            "event_type": "instance_lifecycle",
            "data": None,
        }
        
        # Should not raise
        await observer._process_event(event)
        
        # Job should not be looked up
        mock_job_queue_service.get_job_by_instance.assert_not_called()
        mock_job_repo.atomic_transition.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_status_handled(self):
        """Event with missing status field is handled gracefully."""
        mock_job_queue_service = AsyncMock()
        mock_job_repo = MagicMock(spec=JobRepository)
        mock_lock_repo = MagicMock(spec=LockRepository)
        
        observer, _, mock_job_repo, _, _, _ = create_mock_observer(
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
        )
        
        event = {
            "event_type": "instance_lifecycle",
            "data": {
                "instance_id": "instance-456",
                # No status field
            }
        }
        
        # Should not raise
        await observer._process_event(event)
        
        # Job should not be looked up
        mock_job_queue_service.get_job_by_instance.assert_not_called()
        mock_job_repo.atomic_transition.assert_not_called()


class TestObserverLockRelease:
    """Tests for lock release behavior."""

    @pytest.mark.asyncio
    async def test_lock_release_called_after_success(self):
        """Lock should be released after successful job completion."""
        mock_job = create_mock_job(job_id="job-123", status="processing", instance_id="instance-456")
        mock_job_queue_service = MagicMock()
        mock_job_queue_service.get_job_by_instance = AsyncMock(return_value=mock_job)
        mock_job_repo = MagicMock(spec=JobRepository)
        mock_lock_repo = MagicMock(spec=LockRepository)
        mock_lock_repo.release_by_instance.return_value = 2  # Released 2 locks
        
        observer = JobFeedbackObserver(
            event_bus=MagicMock(),
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
            project_repo=MagicMock(),
            instance_manager=MagicMock(),
        )
        
        event = {
            "event_type": "instance_lifecycle",
            "data": {
                "instance_id": "instance-456",
                "status": "completed",
                "error": None,
            }
        }
        
        await observer._process_event(event)
        
        # Lock should be released after successful transition
        mock_lock_repo.release_by_instance.assert_called_once_with("instance-456")


class TestObserverRaceCondition:
    """Tests for race condition handling."""

    @pytest.mark.asyncio
    async def test_observer_race_condition(self):
        """Simulate concurrent completion - atomic_transition raises InvalidTransitionError -> skip silently."""
        mock_job = create_mock_job(job_id="job-123", status="processing", instance_id="instance-456")
        mock_job_queue_service = AsyncMock(return_value=mock_job)
        mock_job_repo = MagicMock(spec=JobRepository)
        mock_job_repo.atomic_transition.side_effect = InvalidTransitionError(
            job_id="job-123",
            from_status="processing",
            to_status="completed",
        )
        mock_lock_repo = MagicMock(spec=LockRepository)
        
        observer, _, mock_job_repo, mock_lock_repo, _, _ = create_mock_observer(
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
        )
        
        event = {
            "event_type": "instance_lifecycle",
            "data": {
                "instance_id": "instance-456",
                "status": "completed",
                "error": None,
            }
        }
        
        # Should not raise
        await observer._process_event(event)
        
        # Lock should NOT be released because transition failed
        mock_lock_repo.release_by_instance.assert_not_called()


class TestObserverExceptionHandling:
    """Tests for exception handling."""

    @pytest.mark.asyncio
    async def test_lock_release_failure_is_not_critical(self):
        """Lock release failure is logged but does not propagate."""
        mock_job = create_mock_job(job_id="job-123", status="processing", instance_id="instance-456")
        mock_job_queue_service = MagicMock()
        mock_job_queue_service.get_job_by_instance = AsyncMock(return_value=mock_job)
        mock_job_repo = MagicMock(spec=JobRepository)
        mock_lock_repo = MagicMock(spec=LockRepository)
        mock_lock_repo.release_by_instance.side_effect = Exception("Lock release failed")
        
        observer = JobFeedbackObserver(
            event_bus=MagicMock(),
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
            project_repo=MagicMock(),
            instance_manager=MagicMock(),
        )
        
        event = {
            "event_type": "instance_lifecycle",
            "data": {
                "instance_id": "instance-456",
                "status": "completed",
                "error": None,
            }
        }
        
        # Should not raise
        await observer._process_event(event)
        
        # atomic_transition was still called successfully
        mock_job_repo.atomic_transition.assert_called_once()


class TestObserverEdgeCases:
    """Tests for edge cases."""

    @pytest.mark.asyncio
    async def test_observer_handles_event_with_no_data(self):
        """Event with None data is handled gracefully."""
        mock_job_queue_service = AsyncMock()
        mock_job_repo = MagicMock(spec=JobRepository)
        mock_lock_repo = MagicMock(spec=LockRepository)
        
        observer, _, mock_job_repo, _, _, _ = create_mock_observer(
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
        )
        
        event = {
            "event_type": "instance_lifecycle",
            "data": None,
        }
        
        # Should not raise
        await observer._process_event(event)
        mock_job_queue_service.get_job_by_instance.assert_not_called()

    @pytest.mark.asyncio
    async def test_observer_handles_event_with_missing_instance_id(self):
        """Event with missing instance_id is handled gracefully."""
        mock_job_queue_service = AsyncMock()
        mock_job_repo = MagicMock(spec=JobRepository)
        mock_lock_repo = MagicMock(spec=LockRepository)
        
        observer, _, mock_job_repo, _, _, _ = create_mock_observer(
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
        )
        
        event = {
            "event_type": "instance_lifecycle",
            "data": {
                "status": "completed",
            }
        }
        
        await observer._process_event(event)
        mock_job_queue_service.get_job_by_instance.assert_not_called()

    @pytest.mark.asyncio
    async def test_observer_handles_unknown_status(self):
        """Event with unknown status is handled gracefully."""
        mock_job = create_mock_job(job_id="job-123", status="processing", instance_id="instance-456")
        mock_job_queue_service = AsyncMock(return_value=mock_job)
        mock_job_repo = MagicMock(spec=JobRepository)
        mock_lock_repo = MagicMock(spec=LockRepository)
        
        observer, _, mock_job_repo, _, _, _ = create_mock_observer(
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
        )
        
        event = {
            "event_type": "instance_lifecycle",
            "data": {
                "instance_id": "instance-456",
                "status": "unknown_status",
            }
        }
        
        await observer._process_event(event)
        mock_job_queue_service.get_job_by_instance.assert_called()
        mock_job_repo.atomic_transition.assert_not_called()


class TestObserverDefaultErrorMessage:
    """Tests for default error message handling."""

    @pytest.mark.asyncio
    async def test_error_message_with_no_error_provided(self):
        """Error instance with no error field uses default message."""
        mock_job = create_mock_job(job_id="job-123", status="processing", instance_id="instance-456")
        mock_job_queue_service = MagicMock()
        mock_job_queue_service.get_job_by_instance = AsyncMock(return_value=mock_job)
        mock_job_repo = MagicMock(spec=JobRepository)
        mock_lock_repo = MagicMock(spec=LockRepository)
        mock_lock_repo.release_by_instance.return_value = 1
        
        observer = JobFeedbackObserver(
            event_bus=MagicMock(),
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
            project_repo=MagicMock(),
            instance_manager=MagicMock(),
        )
        
        event = {
            "event_type": "instance_lifecycle",
            "data": {
                "instance_id": "instance-456",
                "status": "error",
                # No 'error' field provided
            }
        }
        
        await observer._process_event(event)
        
        mock_job_repo.atomic_transition.assert_called_once_with(
            job_id="job-123",
            from_status=JobStatus.PROCESSING.value,
            to_status=JobStatus.FAILED.value,
            error_message="Unknown error",
        )


class TestObserverStartStop:
    """Tests for observer lifecycle."""

    @pytest.mark.asyncio
    async def test_observer_start_subscribes(self):
        """Start subscribes to EventBus."""
        import asyncio
        
        mock_event_bus = MagicMock()
        mock_queue = MagicMock(spec=asyncio.Queue)
        mock_event_bus.subscribe_all.return_value = mock_queue
        mock_job_queue_service = AsyncMock()
        mock_job_repo = MagicMock(spec=JobRepository)
        mock_lock_repo = MagicMock(spec=LockRepository)
        
        observer = JobFeedbackObserver(
            event_bus=mock_event_bus,
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
            project_repo=MagicMock(),
            instance_manager=MagicMock(),
        )
        
        await observer.start()
        
        try:
            mock_event_bus.subscribe_all.assert_called_once_with("job_feedback_observer")
            assert observer._running is True
            assert observer._task is not None
            assert observer._queue is mock_queue
        finally:
            await observer.stop()

    @pytest.mark.asyncio
    async def test_observer_stop_unsubscribes(self):
        """Stop unsubscribes from EventBus."""
        import asyncio
        
        mock_event_bus = MagicMock()
        mock_queue = MagicMock(spec=asyncio.Queue)
        mock_event_bus.subscribe_all.return_value = mock_queue
        mock_job_queue_service = AsyncMock()
        mock_job_repo = MagicMock(spec=JobRepository)
        mock_lock_repo = MagicMock(spec=LockRepository)
        
        observer = JobFeedbackObserver(
            event_bus=mock_event_bus,
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
            project_repo=MagicMock(),
            instance_manager=MagicMock(),
        )
        
        await observer.start()
        await observer.stop()
        
        mock_event_bus.unsubscribe_all.assert_called_once_with("job_feedback_observer")
        assert observer._running is False

    @pytest.mark.asyncio
    async def test_observer_double_stop(self):
        """Double stop is safe."""
        import asyncio
        
        mock_event_bus = MagicMock()
        mock_queue = MagicMock(spec=asyncio.Queue)
        mock_event_bus.subscribe_all.return_value = mock_queue
        mock_job_queue_service = AsyncMock()
        mock_job_repo = MagicMock(spec=JobRepository)
        mock_lock_repo = MagicMock(spec=LockRepository)
        
        observer = JobFeedbackObserver(
            event_bus=mock_event_bus,
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
            project_repo=MagicMock(),
            instance_manager=MagicMock(),
        )
        
        await observer.start()
        await observer.stop()
        await observer.stop()  # Should not raise
        
        assert observer._running is False

    @pytest.mark.asyncio
    async def test_observer_stop_drains_pending_events(self):
        """Stop drains pending events from queue before cancelling task."""
        import asyncio
        
        mock_event_bus = MagicMock()
        mock_queue = asyncio.Queue()
        mock_event_bus.subscribe_all.return_value = mock_queue
        mock_job_repo = MagicMock(spec=JobRepository)
        mock_lock_repo = MagicMock(spec=LockRepository)
        
        # Create jobs for the events we'll queue
        mock_job_1 = create_mock_job(job_id="job-1", status="processing", instance_id="instance-1")
        mock_job_2 = create_mock_job(job_id="job-2", status="processing", instance_id="instance-2")
        mock_job_queue_service = MagicMock()
        mock_job_queue_service.get_job_by_instance = AsyncMock(
            side_effect=[mock_job_1, mock_job_2]
        )
        
        observer = JobFeedbackObserver(
            event_bus=mock_event_bus,
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
            project_repo=MagicMock(),
            instance_manager=MagicMock(),
        )
        
        # Set up queue manually (don't start to avoid event loop interference)
        observer._queue = mock_queue
        observer._running = False
        observer._task = None  # No task - we're testing drain only
        
        # Put events in the queue
        event1 = {
            "event_type": "instance_lifecycle",
            "data": {"instance_id": "instance-1", "status": "completed"},
        }
        event2 = {
            "event_type": "instance_lifecycle",
            "data": {"instance_id": "instance-2", "status": "completed"},
        }
        await mock_queue.put(event1)
        await mock_queue.put(event2)
        
        # Stop should drain events
        await observer.stop()
        
        # Both events should have been processed
        assert mock_job_queue_service.get_job_by_instance.call_count == 2
        assert mock_job_repo.atomic_transition.call_count == 2
        
        # Verify job completions
        mock_job_repo.atomic_transition.assert_any_call(
            job_id="job-1",
            from_status=JobStatus.PROCESSING.value,
            to_status=JobStatus.COMPLETED.value,
        )
        mock_job_repo.atomic_transition.assert_any_call(
            job_id="job-2",
            from_status=JobStatus.PROCESSING.value,
            to_status=JobStatus.COMPLETED.value,
        )


class TestObserverConfig:
    """Tests for observer configuration."""

    def test_observer_default_health_check_interval(self):
        """Observer has default health check interval."""
        observer = JobFeedbackObserver(
            event_bus=MagicMock(),
            job_queue_service=MagicMock(),
            job_repo=MagicMock(),
            lock_repo=MagicMock(),
            project_repo=MagicMock(),
            instance_manager=MagicMock(),
        )
        assert observer._health_check_interval == 300

    def test_observer_custom_health_check_interval(self):
        """Observer respects custom health check interval from config."""
        mock_config = MagicMock()
        mock_config.observer_health_check_interval_seconds = 60

        observer = JobFeedbackObserver(
            event_bus=MagicMock(),
            job_queue_service=MagicMock(),
            job_repo=MagicMock(),
            lock_repo=MagicMock(),
            project_repo=MagicMock(),
            instance_manager=MagicMock(),
            config=mock_config,
        )
        assert observer._health_check_interval == 60


class TestObserverLifecycleResilience:
    """Tests for observer lifecycle resilience.
    
    Note: The _process_event method itself catches exceptions from atomic_transition.
    The event loop catches other exceptions to ensure the observer survives.
    """

    @pytest.mark.asyncio
    async def test_invalid_transition_error_caught_in_process_event(self):
        """InvalidTransitionError is caught within _process_event, not propagated."""
        mock_job = create_mock_job(job_id="job-123", status="processing", instance_id="instance-456")
        mock_job_queue_service = MagicMock()
        mock_job_queue_service.get_job_by_instance = AsyncMock(return_value=mock_job)
        mock_job_repo = MagicMock(spec=JobRepository)
        mock_job_repo.atomic_transition.side_effect = InvalidTransitionError(
            job_id="job-123",
            from_status="processing",
            to_status="completed",
        )
        mock_lock_repo = MagicMock(spec=LockRepository)
        
        observer = JobFeedbackObserver(
            event_bus=MagicMock(),
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
            project_repo=MagicMock(),
            instance_manager=MagicMock(),
        )
        
        event = {
            "event_type": "instance_lifecycle",
            "data": {
                "instance_id": "instance-456",
                "status": "completed",
                "error": None,
            }
        }
        
        # Should not raise - InvalidTransitionError is caught within _process_event
        try:
            await observer._process_event(event)
        except InvalidTransitionError:
            pytest.fail("_process_event should catch InvalidTransitionError")
        
        # atomic_transition was called
        mock_job_repo.atomic_transition.assert_called_once()
        
        # Lock should NOT be released because transition failed
        mock_lock_repo.release_by_instance.assert_not_called()

    @pytest.mark.asyncio
    async def test_atomic_transition_generic_exception_caught(self):
        """Generic exceptions from atomic_transition are caught within _process_event."""
        mock_job = create_mock_job(job_id="job-123", status="processing", instance_id="instance-456")
        mock_job_queue_service = MagicMock()
        mock_job_queue_service.get_job_by_instance = AsyncMock(return_value=mock_job)
        mock_job_repo = MagicMock(spec=JobRepository)
        mock_job_repo.atomic_transition.side_effect = RuntimeError("Unexpected DB error")
        mock_lock_repo = MagicMock(spec=LockRepository)
        
        observer = JobFeedbackObserver(
            event_bus=MagicMock(),
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
            project_repo=MagicMock(),
            instance_manager=MagicMock(),
        )
        
        event = {
            "event_type": "instance_lifecycle",
            "data": {
                "instance_id": "instance-456",
                "status": "completed",
                "error": None,
            }
        }
        
        # Should not raise - exceptions are caught within _process_event
        try:
            await observer._process_event(event)
        except RuntimeError:
            pytest.fail("_process_event should catch RuntimeError from atomic_transition")
        
        # atomic_transition was called
        mock_job_repo.atomic_transition.assert_called_once()
        
        # Lock should NOT be released because transition failed
        mock_lock_repo.release_by_instance.assert_not_called()

    @pytest.mark.asyncio
    async def test_atomic_transition_exception_does_not_propagate(self):
        """If atomic_transition raises an unexpected error, _process_event should handle it."""
        mock_job = create_mock_job(job_id="job-123", status="processing", instance_id="instance-456")
        mock_job_queue_service = MagicMock()
        mock_job_queue_service.get_job_by_instance = AsyncMock(return_value=mock_job)
        mock_job_repo = MagicMock(spec=JobRepository)
        mock_job_repo.atomic_transition.side_effect = RuntimeError("Unexpected DB error")
        mock_lock_repo = MagicMock(spec=LockRepository)
        
        observer = JobFeedbackObserver(
            event_bus=MagicMock(),
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
            project_repo=MagicMock(),
            instance_manager=MagicMock(),
        )
        
        event = {
            "event_type": "instance_lifecycle",
            "data": {
                "instance_id": "instance-456",
                "status": "completed",
                "error": None,
            }
        }
        
        # Should not raise - the event loop catches exceptions
        try:
            await observer._process_event(event)
        except Exception as e:
            pytest.fail(f"_process_event should not raise, but got: {e}")
        
        # Verify atomic_transition was called
        mock_job_repo.atomic_transition.assert_called_once()
        
        # Lock should NOT be released because transition failed
        mock_lock_repo.release_by_instance.assert_not_called()


class TestObserverHealthCheck:
    """Tests for health check configuration."""

    def test_health_check_interval_zero_if_configured(self):
        """Observer uses configured health check interval (including zero)."""
        mock_config = MagicMock()
        mock_config.observer_health_check_interval_seconds = 0

        observer = JobFeedbackObserver(
            event_bus=MagicMock(),
            job_queue_service=MagicMock(),
            job_repo=MagicMock(),
            lock_repo=MagicMock(),
            project_repo=MagicMock(),
            instance_manager=MagicMock(),
            config=mock_config,
        )
        assert observer._health_check_interval == 0

    def test_health_check_interval_from_config(self):
        """Observer reads health check interval from config."""
        mock_config = MagicMock()
        mock_config.observer_health_check_interval_seconds = 120

        observer = JobFeedbackObserver(
            event_bus=MagicMock(),
            job_queue_service=MagicMock(),
            job_repo=MagicMock(),
            lock_repo=MagicMock(),
            project_repo=MagicMock(),
            instance_manager=MagicMock(),
            config=mock_config,
        )
        assert observer._health_check_interval == 120

    def test_health_check_interval_from_config_large_value(self):
        """Observer handles large health check interval from config."""
        mock_config = MagicMock()
        mock_config.observer_health_check_interval_seconds = 3600  # 1 hour

        observer = JobFeedbackObserver(
            event_bus=MagicMock(),
            job_queue_service=MagicMock(),
            job_repo=MagicMock(),
            lock_repo=MagicMock(),
            project_repo=MagicMock(),
            instance_manager=MagicMock(),
            config=mock_config,
        )
        assert observer._health_check_interval == 3600
