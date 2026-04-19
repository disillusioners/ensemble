"""Phase 2 Feedback Loop Verification Tests.

This module verifies coverage for all Phase 2 functional, race condition,
and edge case scenarios in the Task↔Job Feedback Loop implementation.

Scenarios verified:
1. Job completes when instance completes
2. Job fails when instance errors
3. Job stays PROCESSING when instance terminates
4. Startup recovery for orphaned jobs
5. Cancellation cascade
6. Concurrent completion (race condition)
7. Double event delivery (race condition)
8. Observer drain on shutdown
9. No job for instance
10. Job already transitioned
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from daemon.repositories.job_queue import JobRepository, JobStatus
from daemon.repositories.job_queue.models import JobItem
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.services.job_feedback_observer import JobFeedbackObserver
from daemon.services.job_state_machine import InvalidTransitionError


def create_mock_job(
    job_id: str = "test-job-id",
    status: str = "processing",
    instance_id: str = "test-instance-id"
) -> MagicMock:
    """Create a mock JobItem with the specified attributes."""
    mock_job = MagicMock(spec=JobItem)
    mock_job.job_id = job_id
    mock_job.status = status
    mock_job.instance_id = instance_id
    return mock_job


class TestDoubleEventDelivery:
    """Tests for scenario 7: Double event delivery.

    When the same lifecycle event is delivered twice (e.g., due to
    network retries or message queue redelivery), the second delivery
    should be a no-op.
    """

    @pytest.mark.asyncio
    async def test_observer_handles_duplicate_completion_event(self):
        """Same completion event delivered twice - second should be no-op.

        This tests the race condition where:
        1. First event: atomic_transition succeeds, lock released
        2. Second event: atomic_transition raises InvalidTransitionError,
           handled gracefully, no lock release attempted
        """
        # Set up job queue service mock
        mock_job = create_mock_job(job_id="dup-job", status="processing", instance_id="dup-instance")
        mock_job_queue_service = MagicMock()
        mock_job_queue_service.get_job_by_instance = AsyncMock(return_value=mock_job)

        # Set up job repo mock - first call succeeds
        mock_job_repo = MagicMock(spec=JobRepository)
        mock_job_repo.atomic_transition.return_value = mock_job  # Success on first call

        # Set up lock repo mock
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
                "instance_id": "dup-instance",
                "status": "completed",
                "error": None,
            }
        }

        # Process first event - should succeed
        await observer._process_event(event)

        # Verify first call succeeded
        assert mock_job_repo.atomic_transition.call_count == 1
        assert mock_lock_repo.release_by_instance.call_count == 1

        # Now simulate the job already being COMPLETED
        # Second event arrives after job is already completed
        mock_job_repo.atomic_transition.side_effect = InvalidTransitionError(
            job_id="dup-job",
            from_status="processing",
            to_status="completed",
        )

        # Process second event - should be no-op (no exception)
        await observer._process_event(event)

        # Verify second call was attempted but handled gracefully
        assert mock_job_repo.atomic_transition.call_count == 2
        # Lock was NOT released again (already released)
        assert mock_lock_repo.release_by_instance.call_count == 1

    @pytest.mark.asyncio
    async def test_observer_handles_duplicate_error_event(self):
        """Same error event delivered twice - second should be no-op."""
        mock_job = create_mock_job(job_id="dup-err-job", status="processing", instance_id="dup-err-instance")
        mock_job_queue_service = MagicMock()
        mock_job_queue_service.get_job_by_instance = AsyncMock(return_value=mock_job)

        mock_job_repo = MagicMock(spec=JobRepository)
        mock_job_repo.atomic_transition.return_value = mock_job

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
                "instance_id": "dup-err-instance",
                "status": "error",
                "error": "Connection timeout",
            }
        }

        # First event succeeds
        await observer._process_event(event)
        assert mock_job_repo.atomic_transition.call_count == 1

        # Second event fails with InvalidTransitionError (job already failed)
        mock_job_repo.atomic_transition.side_effect = InvalidTransitionError(
            job_id="dup-err-job",
            from_status="processing",
            to_status="failed",
        )

        # Should not raise
        await observer._process_event(event)
        assert mock_job_repo.atomic_transition.call_count == 2

    @pytest.mark.asyncio
    async def test_observer_handles_duplicate_event_with_different_job_state(self):
        """Duplicate event when job has already moved to a terminal state."""
        mock_job = create_mock_job(job_id="terminal-job", status="processing", instance_id="terminal-instance")
        mock_job_queue_service = MagicMock()
        mock_job_queue_service.get_job_by_instance = AsyncMock(return_value=mock_job)

        mock_job_repo = MagicMock(spec=JobRepository)
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
                "instance_id": "terminal-instance",
                "status": "completed",
                "error": None,
            }
        }

        # Simulate job already being completed (race with terminate_instance)
        mock_job_repo.atomic_transition.side_effect = InvalidTransitionError(
            job_id="terminal-job",
            from_status="completed",  # Job is already COMPLETED
            to_status="completed",
        )

        # Should not raise - handled gracefully
        await observer._process_event(event)

        # atomic_transition was called
        mock_job_repo.atomic_transition.assert_called_once()
        # Lock release was NOT attempted (transition failed)
        mock_lock_repo.release_by_instance.assert_not_called()


class TestAtomicTransitionIntegration:
    """Integration tests using real database for atomic transition behavior.

    These tests verify the behavior when atomic_transition raises
    InvalidTransitionError, which happens when the job has already transitioned.
    """

    def test_atomic_transition_raises_when_job_already_completed(self, repository, sample_job_data):
        """atomic_transition raises InvalidTransitionError when job already completed.

        This verifies the DB-level behavior that the observer relies on.
        The observer catches this exception and skips the second event.
        """
        # Create job and transition to PROCESSING then COMPLETED
        job = repository.create(**sample_job_data)
        repository.start_job_atomic(job.job_id, "test-instance")
        repository.complete_job(job.job_id, "Success")

        # Try to transition again - should raise InvalidTransitionError
        with pytest.raises(InvalidTransitionError) as exc_info:
            repository.atomic_transition(
                job.job_id,
                from_status=JobStatus.PROCESSING.value,
                to_status=JobStatus.COMPLETED.value,
            )

        assert exc_info.value.job_id == job.job_id
        assert exc_info.value.from_status == JobStatus.COMPLETED.value  # Already completed

    def test_atomic_transition_raises_when_job_already_failed(self, repository, sample_job_data):
        """atomic_transition raises InvalidTransitionError when job already failed."""
        job = repository.create(**sample_job_data)
        repository.start_job_atomic(job.job_id, "test-instance")
        repository.fail_job(job.job_id, "Connection error")

        with pytest.raises(InvalidTransitionError) as exc_info:
            repository.atomic_transition(
                job.job_id,
                from_status=JobStatus.PROCESSING.value,
                to_status=JobStatus.FAILED.value,
            )

        assert exc_info.value.from_status == JobStatus.FAILED.value

    def test_atomic_transition_raises_when_job_cancelled(self, repository, sample_job_data):
        """atomic_transition raises InvalidTransitionError when job already cancelled."""
        job = repository.create(**sample_job_data)
        repository.start_job_atomic(job.job_id, "test-instance")
        repository.cancel_job(job.job_id)

        with pytest.raises(InvalidTransitionError) as exc_info:
            repository.atomic_transition(
                job.job_id,
                from_status=JobStatus.PROCESSING.value,
                to_status=JobStatus.CANCELLED.value,
            )

        assert exc_info.value.from_status == JobStatus.CANCELLED.value

    def test_concurrent_transitions_only_one_succeeds(self, repository, sample_job_data):
        """Only one concurrent transition should succeed.

        This simulates the race condition between observer and terminate_instance.
        """
        job = repository.create(**sample_job_data)
        repository.start_job_atomic(job.job_id, "test-instance")

        # First transition succeeds
        result1 = repository.atomic_transition(
            job.job_id,
            from_status=JobStatus.PROCESSING.value,
            to_status=JobStatus.COMPLETED.value,
        )
        assert result1 is not None
        assert result1.status == JobStatus.COMPLETED.value

        # Second transition fails (job already completed)
        with pytest.raises(InvalidTransitionError):
            repository.atomic_transition(
                job.job_id,
                from_status=JobStatus.PROCESSING.value,
                to_status=JobStatus.COMPLETED.value,
            )

        # Verify final state is COMPLETED
        final_job = repository.get(job.job_id)
        assert final_job.status == JobStatus.COMPLETED.value


class TestObserverObserverBehavior:
    """Additional tests verifying observer behavior with real DB patterns."""

    @pytest.mark.asyncio
    async def test_observer_skips_job_with_null_instance_id(self):
        """Event for job with null instance_id should be skipped gracefully."""
        mock_job_queue_service = MagicMock()
        mock_job_queue_service.get_job_by_instance = AsyncMock()
        mock_job_repo = MagicMock(spec=JobRepository)
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
                "instance_id": None,  # Null instance ID
                "status": "completed",
                "error": None,
            }
        }

        # Should not raise
        await observer._process_event(event)

        # Job lookup should not be called
        mock_job_queue_service.get_job_by_instance.assert_not_called()
        mock_job_repo.atomic_transition.assert_not_called()

    @pytest.mark.asyncio
    async def test_observer_handles_empty_instance_id(self):
        """Event with empty string instance_id should be skipped gracefully."""
        mock_job_queue_service = MagicMock()
        mock_job_queue_service.get_job_by_instance = AsyncMock()
        mock_job_repo = MagicMock(spec=JobRepository)
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
                "instance_id": "",  # Empty string
                "status": "completed",
                "error": None,
            }
        }

        await observer._process_event(event)

        mock_job_queue_service.get_job_by_instance.assert_not_called()
        mock_job_repo.atomic_transition.assert_not_called()

    @pytest.mark.asyncio
    async def test_observer_completion_then_termination_skips_termination(self):
        """After instance completes, termination event should be skipped.

        This tests the filter that skips 'terminated' status events.
        """
        mock_job_queue_service = MagicMock()
        mock_job_queue_service.get_job_by_instance = AsyncMock()
        mock_job_repo = MagicMock(spec=JobRepository)
        mock_lock_repo = MagicMock(spec=LockRepository)

        observer = JobFeedbackObserver(
            event_bus=MagicMock(),
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
            project_repo=MagicMock(),
            instance_manager=MagicMock(),
        )

        # Termination event
        event = {
            "event_type": "instance_lifecycle",
            "data": {
                "instance_id": "some-instance",
                "status": "terminated",
                "error": None,
            }
        }

        await observer._process_event(event)

        # Should not try to look up job for terminated events
        mock_job_queue_service.get_job_by_instance.assert_not_called()
        mock_job_repo.atomic_transition.assert_not_called()


class TestCancellationIntegration:
    """Integration tests for cancellation cascade behavior."""

    @pytest.mark.asyncio
    async def test_cancel_after_observer_completed_is_noop(self):
        """Cancel attempted after observer already completed job should fail gracefully."""
        from daemon.services.job_queue_service import JobQueueService

        # Simulate the race: observer already completed the job
        mock_repo = MagicMock()
        mock_repo.get.return_value = create_mock_job(
            job_id="race-job",
            status="completed",  # Already completed by observer
            instance_id="race-instance"
        )

        mock_lock_manager = MagicMock()
        mock_lock_manager.release = AsyncMock()
        mock_lock_manager.release_queue_lock = AsyncMock()
        mock_lock_manager.release_by_instance = AsyncMock()

        mock_queue_repo = MagicMock()
        mock_instance_manager = MagicMock()

        service = JobQueueService(
            repository=mock_repo,
            lock_manager=mock_lock_manager,
            queue_repo=mock_queue_repo,
            instance_manager=mock_instance_manager,
        )

        # Cancel should return False because job already completed
        result = await service.cancel_job("race-job")

        assert result is False

    @pytest.mark.asyncio
    async def test_terminate_after_observer_failed_is_noop(self):
        """Terminate attempted after observer already failed job should fail gracefully."""
        from daemon.services.job_queue_service import JobQueueService

        # Simulate: observer already failed the job
        mock_repo = MagicMock()
        mock_repo.get.return_value = create_mock_job(
            job_id="fail-race-job",
            status="failed",  # Already failed by observer
            instance_id="fail-instance"
        )

        mock_lock_manager = MagicMock()
        mock_lock_manager.release = AsyncMock()
        mock_lock_manager.release_queue_lock = AsyncMock()
        mock_lock_manager.release_by_instance = AsyncMock()

        mock_queue_repo = MagicMock()
        mock_instance_manager = MagicMock()

        service = JobQueueService(
            repository=mock_repo,
            lock_manager=mock_lock_manager,
            queue_repo=mock_queue_repo,
            instance_manager=mock_instance_manager,
        )

        # Cancel should return False because job already failed
        result = await service.cancel_job("fail-race-job")

        assert result is False
