"""Comprehensive tests for atomic transition functionality.

This module tests the atomic state transition methods in JobRepository.
"""

import threading
import uuid

import pytest
from sqlalchemy import create_engine
from sqlmodel import SQLModel

from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.job_queue.models import AdmissionState, JobItem
from daemon.services.job_state_machine import InvalidTransitionError


class TestAtomicTransition:
    """Tests for atomic_transition() method."""

    def test_atomic_transition_pending_to_processing(self, repository, sample_job_data):
        """Test successful PENDING -> PROCESSING transition via atomic_transition."""
        job = repository.create(**sample_job_data)
        
        result = repository.atomic_transition(
            job.job_id,
            from_status=AdmissionState.QUEUED.value,
            to_status=AdmissionState.ACTIVE.value,
            instance_id="test-instance",
        )

        assert result is not None
        assert result.admission_state == AdmissionState.ACTIVE.value
        assert result.instance_id == "test-instance"

    def test_atomic_transition_processing_to_completed(self, repository, sample_job_data):
        """Test successful PROCESSING -> COMPLETED transition."""
        job = repository.create(**sample_job_data)
        
        # First transition to PROCESSING
        repository.atomic_transition(
            job.job_id,
            from_status=AdmissionState.QUEUED.value,
            to_status=AdmissionState.ACTIVE.value,
            instance_id="test-instance",
        )

        # Then transition to COMPLETED
        result = repository.atomic_transition(
            job.job_id,
            from_status=AdmissionState.ACTIVE.value,
            to_status=AdmissionState.DONE.value,
        )

        assert result is not None
        assert result.admission_state == AdmissionState.DONE.value

    def test_atomic_transition_processing_to_failed(self, repository, sample_job_data):
        """Test successful PROCESSING -> FAILED transition."""
        job = repository.create(**sample_job_data)
        
        # First transition to PROCESSING
        repository.atomic_transition(
            job.job_id,
            from_status=AdmissionState.QUEUED.value,
            to_status=AdmissionState.ACTIVE.value,
            instance_id="test-instance",
        )

        # Then transition to FAILED
        result = repository.atomic_transition(
            job.job_id,
            from_status=AdmissionState.ACTIVE.value,
            to_status=AdmissionState.DONE.value,
        )

        assert result is not None
        assert result.admission_state == AdmissionState.DONE.value

    def test_atomic_transition_pending_to_cancelled(self, repository, sample_job_data):
        """Test successful PENDING -> CANCELLED transition."""
        job = repository.create(**sample_job_data)
        
        result = repository.atomic_transition(
            job.job_id,
            from_status=AdmissionState.QUEUED.value,
            to_status=AdmissionState.DONE.value,
        )

        assert result is not None
        assert result.admission_state == AdmissionState.DONE.value

    def test_atomic_transition_processing_to_cancelled(self, repository, sample_job_data):
        """Test successful PROCESSING -> CANCELLED transition (key fix for abort)."""
        job = repository.create(**sample_job_data)
        
        # First transition to PROCESSING
        repository.atomic_transition(
            job.job_id,
            from_status=AdmissionState.QUEUED.value,
            to_status=AdmissionState.ACTIVE.value,
            instance_id="test-instance",
        )

        # Then transition to CANCELLED (abort)
        result = repository.atomic_transition(
            job.job_id,
            from_status=AdmissionState.ACTIVE.value,
            to_status=AdmissionState.DONE.value,
        )

        assert result is not None
        assert result.admission_state == AdmissionState.DONE.value

    def test_atomic_transition_wrong_from_status_raises(self, repository, sample_job_data):
        """Test transition with wrong from_status raises InvalidTransitionError."""
        job = repository.create(**sample_job_data)
        
        # Try to complete without starting first
        with pytest.raises(InvalidTransitionError) as exc_info:
            repository.atomic_transition(
                job.job_id,
                from_status=AdmissionState.ACTIVE.value,  # Wrong - job is PENDING
                to_status=AdmissionState.DONE.value,
            )
        
        assert exc_info.value.job_id == job.job_id
        assert exc_info.value.from_state == AdmissionState.QUEUED.value
        assert exc_info.value.to_state == AdmissionState.DONE.value

    def test_atomic_transition_nonexistent_job_returns_none(self, repository):
        """Test transition on non-existent job returns None."""
        result = repository.atomic_transition(
            "nonexistent-job-id",
            from_status=AdmissionState.QUEUED.value,
            to_status=AdmissionState.ACTIVE.value,
        )
        
        assert result is None

    def test_atomic_transition_applies_extra_updates(self, repository, sample_job_data):
        """Test that extra_updates are applied correctly."""
        job = repository.create(**sample_job_data)
        
        result = repository.atomic_transition(
            job.job_id,
            from_status=AdmissionState.QUEUED.value,
            to_status=AdmissionState.ACTIVE.value,
            instance_id="instance-123",
            priority=10,  # Extra field
        )

        assert result is not None
        assert result.instance_id == "instance-123"
        assert result.priority == 10


class TestStartJobAtomic:
    """Tests for start_job_atomic() method."""

    def test_start_job_atomic_success(self, repository, sample_job_data):
        """Test start_job_atomic transitions PENDING -> PROCESSING."""
        job = repository.create(**sample_job_data)
        
        result = repository.start_job_atomic(job.job_id, "test-instance")

        assert result is not None
        assert result.admission_state == AdmissionState.ACTIVE.value
        assert result.instance_id == "test-instance"

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
        assert result.admission_state == AdmissionState.DONE.value

    def test_complete_job_without_summary(self, repository, sample_job_data):
        """Test complete_job works without result_summary."""
        job = repository.create(**sample_job_data)
        repository.start_job_atomic(job.job_id, "test-instance")

        result = repository.complete_job(job.job_id)

        assert result is not None
        assert result.admission_state == AdmissionState.DONE.value

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
        assert result.admission_state == AdmissionState.DONE.value

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
        assert result.admission_state == AdmissionState.DONE.value

    def test_cancel_job_from_processing(self, repository, sample_job_data):
        """Test cancel_job works from PROCESSING state (the key fix)."""
        job = repository.create(**sample_job_data)
        repository.start_job_atomic(job.job_id, "test-instance")

        result = repository.cancel_job(job.job_id)

        assert result is not None
        assert result.admission_state == AdmissionState.DONE.value

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
        """Test cancel_job from a terminal FAILED state raises ValueError.

        Under the admission-state model, fail_job sets admission_state='done'
        (terminal). cancel_job's guard only allows admission_state IN
        ('queued', 'active') — 'done' is terminal and cannot be cancelled.
        A failed job awaiting retry is in admission_state='queued' (set by
        atomic_retry), which remains cancellable; a terminal failed job is
        'done' (not cancellable).
        """
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


@pytest.fixture
def concurrent_engine(tmp_path):
    """File-backed SQLite engine (default QueuePool) for concurrent tests."""
    db_path = tmp_path / "cancel_job_toctou.db"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def concurrent_repository(concurrent_engine) -> JobRepository:
    """JobRepository backed by a file-backed SQLite engine (F11)."""
    return JobRepository(concurrent_engine)


class TestCancelJobToctou:
    """H1 — cancel_job vs concurrent start_job must NOT lose the cancel.

    Pre-fix: ``cancel_job`` did ``get()`` then dispatched to
    ``atomic_transition`` based on the READ status. A concurrent
    ``start_job`` could transition PENDING -> PROCESSING between the read
    and the UPDATE, causing the dispatched ``PENDING -> CANCELLED``
    transition to no-op (rowcount=0) and raise ``InvalidTransitionError``.
    The cancel was silently LOST.

    Post-fix: ``cancel_job`` issues a single atomic
    ``UPDATE … WHERE status IN ('pending','processing','failed')``. As
    long as the row is in any cancellable state when the UPDATE commits,
    the cancel succeeds. The start_job may or may not win the race
    (depending on commit order), but the cancel is ALWAYS preserved and
    the final status is always CANCELLED.

    These tests run against a file-backed engine with the default
    QueuePool so two threads actually get distinct SQLite connections
    and the cross-connection UPDATE race is observable.
    """

    def test_cancel_not_lost_under_concurrent_start(
        self, concurrent_repository: JobRepository, sample_job_data
    ):
        """Cancel issued while start_job transitions PENDING->PROCESSING is NOT lost."""
        repo = concurrent_repository
        job = repo.create(**sample_job_data)
        assert job.admission_state == AdmissionState.QUEUED.value

        results: dict[str, object] = {}
        results_lock = threading.Lock()
        barrier = threading.Barrier(2)

        def start_worker() -> None:
            barrier.wait()
            try:
                started = repo.start_job_atomic(job.job_id, "test-instance")
                with results_lock:
                    results["start"] = started
            except InvalidTransitionError as e:
                with results_lock:
                    results["start"] = e

        def cancel_worker() -> None:
            barrier.wait()
            try:
                cancelled = repo.cancel_job(job.job_id)
                with results_lock:
                    results["cancel"] = cancelled
            except ValueError as e:
                with results_lock:
                    results["cancel"] = e

        t_start = threading.Thread(target=start_worker)
        t_cancel = threading.Thread(target=cancel_worker)
        t_start.start()
        t_cancel.start()
        t_start.join()
        t_cancel.join()

        # Cancel must ALWAYS succeed.
        cancel_result = results["cancel"]
        assert cancel_result is not None, (
            f"cancel_job was LOST (got None); results={results}"
        )
        assert not isinstance(cancel_result, ValueError), (
            f"cancel_job raised ValueError; results={results}"
        )
        assert isinstance(cancel_result, JobItem)
        assert cancel_result.admission_state == AdmissionState.DONE.value

        # Final row state must be CANCELLED.
        from sqlmodel import Session as SQLModelSession, select
        with SQLModelSession(repo.engine) as session:
            final = session.exec(
                select(JobItem).where(JobItem.job_id == job.job_id)
            ).one()
            assert final.admission_state == AdmissionState.DONE.value, (
                f"Final admission_state is {final.admission_state!r}, "
                f"expected 'done'; results={results}"
            )

    def test_concurrent_cancels_exactly_one_succeeds(
        self, concurrent_repository: JobRepository, sample_job_data
    ):
        """N concurrent cancels from PENDING: exactly one transitions,
        the rest raise ValueError (idempotent no-op)."""
        repo = concurrent_repository
        job = repo.create(**sample_job_data)

        n = 4
        results: list[object] = []
        results_lock = threading.Lock()
        barrier = threading.Barrier(n)

        def worker() -> None:
            barrier.wait()
            try:
                cancelled = repo.cancel_job(job.job_id)
                with results_lock:
                    results.append(cancelled)
            except ValueError as e:
                with results_lock:
                    results.append(e)

        threads = [threading.Thread(target=worker) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        from sqlmodel import Session as SQLModelSession, select
        with SQLModelSession(repo.engine) as session:
            final = session.exec(
                select(JobItem).where(JobItem.job_id == job.job_id)
            ).one()
            assert final.admission_state == AdmissionState.DONE.value

        successful_cancels = [
            r for r in results
            if isinstance(r, JobItem) and r.admission_state == AdmissionState.DONE.value
        ]
        assert len(successful_cancels) == 1, (
            f"Expected exactly one successful cancel, got {len(successful_cancels)}; "
            f"results={results}"
        )

        failed_with_value_error = [
            r for r in results if isinstance(r, ValueError)
        ]
        assert len(failed_with_value_error) == n - 1, (
            f"Expected {n - 1} ValueErrors, got {len(failed_with_value_error)}; "
            f"results={results}"
        )
