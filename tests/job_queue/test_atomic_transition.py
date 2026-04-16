"""Comprehensive tests for atomic transition functionality.

This module tests the atomic state transition methods in JobRepository.
"""

import pytest

from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.job_queue.models import JobStatus
from daemon.services.job_state_machine import InvalidTransitionError


class TestAtomicTransition:
    """Tests for atomic_transition() method."""

    def test_atomic_transition_pending_to_processing(self, repository, sample_job_data):
        """Test successful PENDING -> PROCESSING transition via atomic_transition."""
        job = repository.create(**sample_job_data)
        
        result = repository.atomic_transition(
            job.job_id,
            from_status=JobStatus.PENDING.value,
            to_status=JobStatus.PROCESSING.value,
            started_at="2024-01-01T00:00:00",
            instance_id="test-instance",
        )
        
        assert result is not None
        assert result.status == JobStatus.PROCESSING.value
        assert result.started_at == "2024-01-01T00:00:00"
        assert result.instance_id == "test-instance"

    def test_atomic_transition_processing_to_completed(self, repository, sample_job_data):
        """Test successful PROCESSING -> COMPLETED transition."""
        job = repository.create(**sample_job_data)
        
        # First transition to PROCESSING
        repository.atomic_transition(
            job.job_id,
            from_status=JobStatus.PENDING.value,
            to_status=JobStatus.PROCESSING.value,
            started_at="2024-01-01T00:00:00",
            instance_id="test-instance",
        )
        
        # Then transition to COMPLETED
        result = repository.atomic_transition(
            job.job_id,
            from_status=JobStatus.PROCESSING.value,
            to_status=JobStatus.COMPLETED.value,
            completed_at="2024-01-01T00:01:00",
            result_summary="Success",
        )
        
        assert result is not None
        assert result.status == JobStatus.COMPLETED.value
        assert result.completed_at == "2024-01-01T00:01:00"
        assert result.result_summary == "Success"

    def test_atomic_transition_processing_to_failed(self, repository, sample_job_data):
        """Test successful PROCESSING -> FAILED transition."""
        job = repository.create(**sample_job_data)
        
        # First transition to PROCESSING
        repository.atomic_transition(
            job.job_id,
            from_status=JobStatus.PENDING.value,
            to_status=JobStatus.PROCESSING.value,
            started_at="2024-01-01T00:00:00",
            instance_id="test-instance",
        )
        
        # Then transition to FAILED
        result = repository.atomic_transition(
            job.job_id,
            from_status=JobStatus.PROCESSING.value,
            to_status=JobStatus.FAILED.value,
            completed_at="2024-01-01T00:01:00",
            error_message="Something went wrong",
        )
        
        assert result is not None
        assert result.status == JobStatus.FAILED.value
        assert result.completed_at == "2024-01-01T00:01:00"
        assert result.error_message == "Something went wrong"

    def test_atomic_transition_pending_to_cancelled(self, repository, sample_job_data):
        """Test successful PENDING -> CANCELLED transition."""
        job = repository.create(**sample_job_data)
        
        result = repository.atomic_transition(
            job.job_id,
            from_status=JobStatus.PENDING.value,
            to_status=JobStatus.CANCELLED.value,
            cancelled_at="2024-01-01T00:00:30",
        )
        
        assert result is not None
        assert result.status == JobStatus.CANCELLED.value
        assert result.cancelled_at == "2024-01-01T00:00:30"

    def test_atomic_transition_processing_to_cancelled(self, repository, sample_job_data):
        """Test successful PROCESSING -> CANCELLED transition (key fix for abort)."""
        job = repository.create(**sample_job_data)
        
        # First transition to PROCESSING
        repository.atomic_transition(
            job.job_id,
            from_status=JobStatus.PENDING.value,
            to_status=JobStatus.PROCESSING.value,
            started_at="2024-01-01T00:00:00",
            instance_id="test-instance",
        )
        
        # Then transition to CANCELLED (abort)
        result = repository.atomic_transition(
            job.job_id,
            from_status=JobStatus.PROCESSING.value,
            to_status=JobStatus.CANCELLED.value,
            cancelled_at="2024-01-01T00:00:30",
        )
        
        assert result is not None
        assert result.status == JobStatus.CANCELLED.value
        assert result.cancelled_at == "2024-01-01T00:00:30"

    def test_atomic_transition_wrong_from_status_raises(self, repository, sample_job_data):
        """Test transition with wrong from_status raises InvalidTransitionError."""
        job = repository.create(**sample_job_data)
        
        # Try to complete without starting first
        with pytest.raises(InvalidTransitionError) as exc_info:
            repository.atomic_transition(
                job.job_id,
                from_status=JobStatus.PROCESSING.value,  # Wrong - job is PENDING
                to_status=JobStatus.COMPLETED.value,
            )
        
        assert exc_info.value.job_id == job.job_id
        assert exc_info.value.from_status == JobStatus.PENDING.value
        assert exc_info.value.to_status == JobStatus.COMPLETED.value

    def test_atomic_transition_nonexistent_job_returns_none(self, repository):
        """Test transition on non-existent job returns None."""
        result = repository.atomic_transition(
            "nonexistent-job-id",
            from_status=JobStatus.PENDING.value,
            to_status=JobStatus.PROCESSING.value,
        )
        
        assert result is None

    def test_atomic_transition_applies_extra_updates(self, repository, sample_job_data):
        """Test that extra_updates are applied correctly."""
        job = repository.create(**sample_job_data)
        
        result = repository.atomic_transition(
            job.job_id,
            from_status=JobStatus.PENDING.value,
            to_status=JobStatus.PROCESSING.value,
            started_at="2024-01-01T00:00:00",
            instance_id="instance-123",
            priority=10,  # Extra field
        )
        
        assert result is not None
        assert result.started_at == "2024-01-01T00:00:00"
        assert result.instance_id == "instance-123"
        assert result.priority == 10


class TestStartJobAtomic:
    """Tests for start_job_atomic() method."""

    def test_start_job_atomic_success(self, repository, sample_job_data):
        """Test start_job_atomic transitions PENDING -> PROCESSING."""
        job = repository.create(**sample_job_data)
        
        result = repository.start_job_atomic(job.job_id, "test-instance")
        
        assert result is not None
        assert result.status == JobStatus.PROCESSING.value
        assert result.instance_id == "test-instance"
        assert result.started_at is not None

    def test_start_job_atomic_sets_instance_id(self, repository, sample_job_data):
        """Test start_job_atomic sets the instance_id correctly."""
        job = repository.create(**sample_job_data)
        
        result = repository.start_job_atomic(job.job_id, "my-instance-id")
        
        assert result.instance_id == "my-instance-id"

    def test_start_job_atomic_already_started_raises(self, repository, sample_job_data):
        """Test start_job_atomic raises if job already started."""
        job = repository.create(**sample_job_data)
        
        # Start the job
        repository.start_job_atomic(job.job_id, "instance-1")
        
        # Try to start again
        with pytest.raises(InvalidTransitionError):
            repository.start_job_atomic(job.job_id, "instance-2")


class TestCompleteJob:
    """Tests for complete_job() method."""

    def test_complete_job_success(self, repository, sample_job_data):
        """Test complete_job transitions PROCESSING -> COMPLETED."""
        job = repository.create(**sample_job_data)
        repository.start_job_atomic(job.job_id, "test-instance")
        
        result = repository.complete_job(job.job_id, "Task completed successfully")
        
        assert result is not None
        assert result.status == JobStatus.COMPLETED.value
        assert result.result_summary == "Task completed successfully"
        assert result.completed_at is not None

    def test_complete_job_without_summary(self, repository, sample_job_data):
        """Test complete_job works without result_summary."""
        job = repository.create(**sample_job_data)
        repository.start_job_atomic(job.job_id, "test-instance")
        
        result = repository.complete_job(job.job_id)
        
        assert result is not None
        assert result.status == JobStatus.COMPLETED.value
        assert result.result_summary is None

    def test_complete_job_not_started_raises(self, repository, sample_job_data):
        """Test complete_job raises if job not started."""
        job = repository.create(**sample_job_data)
        
        with pytest.raises(InvalidTransitionError):
            repository.complete_job(job.job_id)


class TestFailJob:
    """Tests for fail_job() method."""

    def test_fail_job_success(self, repository, sample_job_data):
        """Test fail_job transitions PROCESSING -> FAILED."""
        job = repository.create(**sample_job_data)
        repository.start_job_atomic(job.job_id, "test-instance")
        
        result = repository.fail_job(job.job_id, "Connection timeout")
        
        assert result is not None
        assert result.status == JobStatus.FAILED.value
        assert result.error_message == "Connection timeout"
        assert result.completed_at is not None

    def test_fail_job_not_started_raises(self, repository, sample_job_data):
        """Test fail_job raises if job not started."""
        job = repository.create(**sample_job_data)
        
        with pytest.raises(InvalidTransitionError):
            repository.fail_job(job.job_id, "Some error")


class TestCancelJob:
    """Tests for cancel_job() method."""

    def test_cancel_job_from_pending(self, repository, sample_job_data):
        """Test cancel_job works from PENDING state."""
        job = repository.create(**sample_job_data)
        
        result = repository.cancel_job(job.job_id)
        
        assert result is not None
        assert result.status == JobStatus.CANCELLED.value
        assert result.cancelled_at is not None

    def test_cancel_job_from_processing(self, repository, sample_job_data):
        """Test cancel_job works from PROCESSING state (the key fix)."""
        job = repository.create(**sample_job_data)
        repository.start_job_atomic(job.job_id, "test-instance")
        
        result = repository.cancel_job(job.job_id)
        
        assert result is not None
        assert result.status == JobStatus.CANCELLED.value
        assert result.cancelled_at is not None

    def test_cancel_job_nonexistent_returns_none(self, repository):
        """Test cancel_job returns None for nonexistent job."""
        result = repository.cancel_job("nonexistent-id")
        assert result is None

    def test_cancel_job_completed_raises(self, repository, sample_job_data):
        """Test cancel_job raises if job already completed."""
        job = repository.create(**sample_job_data)
        repository.start_job_atomic(job.job_id, "test-instance")
        repository.complete_job(job.job_id)
        
        with pytest.raises(ValueError) as exc_info:
            repository.cancel_job(job.job_id)
        
        assert "Cannot cancel job" in str(exc_info.value)

    def test_cancel_job_failed_raises(self, repository, sample_job_data):
        """Test cancel_job raises if job already failed."""
        job = repository.create(**sample_job_data)
        repository.start_job_atomic(job.job_id, "test-instance")
        repository.fail_job(job.job_id, "Some error")
        
        with pytest.raises(ValueError) as exc_info:
            repository.cancel_job(job.job_id)
        
        assert "Cannot cancel job" in str(exc_info.value)


class TestAtomicTransitionPreservesData:
    """Tests verifying atomic transitions preserve job data."""

    def test_atomic_transition_preserves_agent_fields(self, repository, sample_job_data):
        """Test that atomic transition preserves agent_id and agent_dir."""
        job = repository.create(**sample_job_data)
        
        result = repository.start_job_atomic(job.job_id, "test-instance")
        
        assert result.agent_id == sample_job_data["agent_id"]
        assert result.agent_dir == sample_job_data["agent_dir"]

    def test_atomic_transition_preserves_message(self, repository, sample_job_data):
        """Test that atomic transition preserves message."""
        job = repository.create(**sample_job_data)
        
        result = repository.start_job_atomic(job.job_id, "test-instance")
        
        assert result.message == sample_job_data["message"]

    def test_atomic_transition_preserves_metadata(self, repository, sample_job_data):
        """Test that atomic transition preserves job_metadata."""
        job = repository.create(**sample_job_data)
        
        result = repository.start_job_atomic(job.job_id, "test-instance")
        
        assert result.job_metadata == sample_job_data["job_metadata"]
