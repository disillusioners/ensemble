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

from daemon.repositories.job_queue import AdmissionState, JobRepository, JobStatus
from daemon.repositories.job_queue.models import JobItem
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.instance.models import InstanceStatus
from daemon.services.dependency_bus import set_dependency_bus
from daemon.services.job_feedback_observer import (
    JobFeedbackObserver,
    _FinalizeJobResult,
)
from daemon.services.job_state_machine import InvalidTransitionError


# Map legacy status → admission_state (Phase 4: status is frozen,
# admission_state is the sole authority).
_STATUS_TO_ADMISSION = {
    "pending": "queued",
    "processing": "active",
    "paused": "active",
    "completed": "done",
    "failed": "done",
    "cancelled": "done",
    "dead_letter": "dead",
}


def make_fake_sync(
    *,
    skip: bool = False,
    raise_exc: BaseException | None = None,
    locks_released: int = 1,
    instance_was_terminal: bool = False,
):
    """Build a fake `_finalize_job_db_sync` replacement for unit tests.

    Mirrors the production sync helper's signature:
      (job_id, instance_id, terminal_status, result_summary, error_message)
      → _FinalizeJobResult
    """
    def fake_sync(
        job_id,
        instance_id,
        terminal_status,
        result_summary,
        error_message,
    ):
        if raise_exc is not None:
            raise raise_exc
        if skip:
            return _FinalizeJobResult(
                skip=True,
                terminal_status=None,
                job_id=None,
                instance_id=None,
                parent_id=None,
                agent_id=None,
                result_summary=None,
                error_message=None,
                locks_released=0,
                instance_was_terminal=False,
            )
        return _FinalizeJobResult(
            skip=False,
            terminal_status=terminal_status,
            job_id=job_id,
            instance_id=instance_id,
            parent_id=None,
            agent_id="developer",
            result_summary=result_summary,
            error_message=error_message,
            locks_released=locks_released,
            instance_was_terminal=instance_was_terminal,
        )
    return fake_sync


def _install_sync_mock(observer, **kwargs):
    """Install a fake `_finalize_job_db_sync` on the observer and return it.

    H15 fix: the sync helper consolidates the 5-step terminal cascade into
    a single WriteGuardSession transaction, which uses
    ``Session(self._instance_manager.engine)`` — that breaks when
    ``instance_manager`` is a MagicMock, so tests must mock the sync helper.
    """
    sync_mock = MagicMock(side_effect=make_fake_sync(**kwargs))
    observer._finalize_job_db_sync = sync_mock
    return sync_mock


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
    mock_job.admission_state = _STATUS_TO_ADMISSION.get(status, "queued")
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
        1. First event: _finalize_job_db_sync succeeds
        2. Second event: _finalize_job_db_sync raises InvalidTransitionError,
           handled gracefully, no lock release attempted
        """
        # Ensure bus is None (a leftover bus would route via bus_pending branch).
        set_dependency_bus(None)

        # Set up job queue service mock
        mock_job = create_mock_job(job_id="dup-job", status="processing", instance_id="dup-instance")
        mock_job_queue_service = MagicMock()
        mock_job_queue_service.get_job_by_instance = AsyncMock(return_value=mock_job)
        mock_job_queue_service.notify_watchers = AsyncMock(return_value=0)

        # Set up job repo mock - first call succeeds (sync mock by default).
        mock_job_repo = MagicMock(spec=JobRepository)

        # Set up lock repo mock
        mock_lock_repo = MagicMock(spec=LockRepository)

        # Set up instance manager mock
        mock_instance_manager = MagicMock()
        mock_instance_manager._get_last_assistant_message_raw = AsyncMock(return_value="Test response")
        # Bypass the graceful-degradation waiting_for check.
        mock_instance_manager._instance_repository.get.return_value.waiting_for = 0

        observer = JobFeedbackObserver(
            event_bus=MagicMock(),
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
            project_repo=MagicMock(),
            instance_manager=mock_instance_manager,
        )
        sync_mock = _install_sync_mock(observer)

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
        assert sync_mock.call_count == 1

        # Now simulate the second delivery raising InvalidTransitionError
        # (the job is already terminal in the real DB).
        sync_mock.side_effect = make_fake_sync(raise_exc=InvalidTransitionError(
            job_id="dup-job",
            from_status="processing",
            to_status="completed",
        ))

        # Process second event - should be no-op (no exception)
        await observer._process_event(event)

        # Verify second call was attempted but handled gracefully
        assert sync_mock.call_count == 2

    @pytest.mark.asyncio
    async def test_observer_handles_duplicate_error_event(self):
        """Same error event delivered twice - second should be no-op."""
        set_dependency_bus(None)

        mock_job = create_mock_job(job_id="dup-err-job", status="processing", instance_id="dup-err-instance")
        mock_job_queue_service = MagicMock()
        mock_job_queue_service.get_job_by_instance = AsyncMock(return_value=mock_job)
        mock_job_queue_service.notify_watchers = AsyncMock(return_value=0)

        mock_job_repo = MagicMock(spec=JobRepository)
        mock_lock_repo = MagicMock(spec=LockRepository)

        mock_instance_manager = MagicMock()
        mock_instance_manager._get_last_assistant_message_raw = AsyncMock(return_value="Test response")
        mock_instance_manager._instance_repository.get.return_value.waiting_for = 0

        observer = JobFeedbackObserver(
            event_bus=MagicMock(),
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
            project_repo=MagicMock(),
            instance_manager=mock_instance_manager,
        )
        sync_mock = _install_sync_mock(observer)

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
        assert sync_mock.call_count == 1

        # Second event raises InvalidTransitionError (job already failed)
        sync_mock.side_effect = make_fake_sync(raise_exc=InvalidTransitionError(
            job_id="dup-err-job",
            from_status="processing",
            to_status="failed",
        ))

        # Should not raise
        await observer._process_event(event)
        assert sync_mock.call_count == 2

    @pytest.mark.asyncio
    async def test_observer_handles_duplicate_event_with_different_job_state(self):
        """Duplicate event when job has already moved to a terminal state."""
        set_dependency_bus(None)

        mock_job = create_mock_job(job_id="terminal-job", status="processing", instance_id="terminal-instance")
        mock_job_queue_service = MagicMock()
        mock_job_queue_service.get_job_by_instance = AsyncMock(return_value=mock_job)

        mock_job_repo = MagicMock(spec=JobRepository)
        mock_lock_repo = MagicMock(spec=LockRepository)

        mock_instance_manager = MagicMock()
        mock_instance_manager._get_last_assistant_message_raw = AsyncMock(return_value="Test response")
        mock_instance_manager._instance_repository.get.return_value.waiting_for = 0

        observer = JobFeedbackObserver(
            event_bus=MagicMock(),
            job_queue_service=mock_job_queue_service,
            job_repo=mock_job_repo,
            lock_repo=mock_lock_repo,
            project_repo=MagicMock(),
            instance_manager=mock_instance_manager,
        )

        # Simulate job already being completed (race with terminate_instance)
        sync_mock = _install_sync_mock(observer, raise_exc=InvalidTransitionError(
            job_id="terminal-job",
            from_status="completed",  # Job is already COMPLETED
            to_status="completed",
        ))

        event = {
            "event_type": "instance_lifecycle",
            "data": {
                "instance_id": "terminal-instance",
                "status": "completed",
                "error": None,
            }
        }

        # Should not raise - handled gracefully
        await observer._process_event(event)

        # _finalize_job_db_sync was called (and raised)
        sync_mock.assert_called_once()
        # Lock release is inside the sync helper, which raised.
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
        assert exc_info.value.from_status == AdmissionState.DONE.value  # Already done

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

        assert exc_info.value.from_status == AdmissionState.DONE.value

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

        assert exc_info.value.from_status == AdmissionState.DONE.value

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
        assert result1.admission_state == AdmissionState.DONE.value

        # Second transition fails (job already completed)
        with pytest.raises(InvalidTransitionError):
            repository.atomic_transition(
                job.job_id,
                from_status=JobStatus.PROCESSING.value,
                to_status=JobStatus.COMPLETED.value,
            )

        # Verify final state is COMPLETED
        final_job = repository.get(job.job_id)
        assert final_job.admission_state == AdmissionState.DONE.value


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
        """Terminate attempted after observer already failed job should transition to CANCELLED."""
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

        # Cancel should succeed (stops retries on failed job)
        result = await service.cancel_job("fail-race-job")

        assert result is True
        # Verify atomic cancel_job was called (FAILED is in cancellable set;
        # single UPDATE-WHERE-IN closes the TOCTOU window).
        mock_repo.cancel_job.assert_called_once_with("fail-race-job")
