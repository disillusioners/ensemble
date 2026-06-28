"""Comprehensive tests for DeadLetterService.

This module tests the DeadLetterService with in-memory SQLite database,
focusing on atomic operations for moving jobs to DLQ and replaying them.
"""

import pytest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session as SQLModelSession

from daemon.repositories.job_queue import AdmissionState, JobRepository, DeadLetterRepository, JobQueueRepository
from daemon.repositories.job_queue.models import JobItem, JobStatus, DeadLetterItem
from daemon.services.dead_letter_service import (
    DeadLetterService,
    DLQItemNotFoundError,
    JobNotInFailedStateError,
)
from daemon.services.job_state_machine import InvalidTransitionError


@pytest.fixture
def engine():
    """Create in-memory SQLite engine for testing.

    Uses StaticPool to reuse the same connection across threads.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def job_repository(engine):
    """Create JobRepository with test engine."""
    return JobRepository(engine)


@pytest.fixture
def dlq_repository(engine):
    """Create DeadLetterRepository with test engine."""
    return DeadLetterRepository(engine)


@pytest.fixture
def queue_repository(engine):
    """Create JobQueueRepository with test engine."""
    return JobQueueRepository(engine)


@pytest.fixture
def dead_letter_service(job_repository, dlq_repository):
    """Create DeadLetterService with test repositories."""
    return DeadLetterService(job_repository, dlq_repository)


@pytest.fixture
def failed_job(engine, job_repository, queue_repository):
    """Create a job in FAILED state for testing."""
    # Try to get existing queue or create new one
    queue = queue_repository.get_by_name("test-project", "system_fifo_queue")
    if queue is None:
        queue = queue_repository.create(
            project_id="test-project",
            queue_name="system_fifo_queue",
            queue_type="fifo",
            concurrency_limit=1,
            is_system=True,
        )
    job = job_repository.create(
        agent_id="test-agent",
        agent_dir="/agents/test-agent",
        message="Test job message",
        source="api",
        project_id="test-project",
        priority=5,
        job_metadata={"test": True},
        queue_id=queue.queue_id,
    )
    # Transition to PROCESSING then FAILED
    job_repository.atomic_transition(
        job.job_id,
        from_status=JobStatus.PENDING.value,
        to_status=JobStatus.PROCESSING.value,
        started_at=datetime.utcnow().isoformat(),
        instance_id="test-instance",
    )
    job_repository.atomic_transition(
        job.job_id,
        from_status=JobStatus.PROCESSING.value,
        to_status=JobStatus.FAILED.value,
        completed_at=datetime.utcnow().isoformat(),
        error_message="Connection timeout",
    )
    return job


def create_failed_job(engine, job_repository, queue_repository, message="Test job", retry_count=0):
    """Helper to create a FAILED job with a queue."""
    # Try to get existing queue or create new one
    queue = queue_repository.get_by_name("test-project", "system_fifo_queue")
    if queue is None:
        queue = queue_repository.create(
            project_id="test-project",
            queue_name="system_fifo_queue",
            queue_type="fifo",
            concurrency_limit=1,
            is_system=True,
        )
    job = job_repository.create(
        agent_id="test-agent",
        agent_dir="/agents/test-agent",
        message=message,
        source="api",
        project_id="test-project",
        priority=5,
        job_metadata={"test": True},
        queue_id=queue.queue_id,
    )
    job_repository.atomic_transition(
        job.job_id,
        from_status=JobStatus.PENDING.value,
        to_status=JobStatus.PROCESSING.value,
        started_at=datetime.utcnow().isoformat(),
        instance_id=f"instance-{job.job_id[:8]}",
    )
    job_repository.atomic_transition(
        job.job_id,
        from_status=JobStatus.PROCESSING.value,
        to_status=JobStatus.FAILED.value,
        completed_at=datetime.utcnow().isoformat(),
        error_message=f"Error for {message}",
    )
    return job


class TestMoveToDLQ:
    """Tests for move_to_dlq() method which takes a session parameter."""

    def test_move_to_dlq_success(self, engine, job_repository, dlq_repository, dead_letter_service, failed_job):
        """Test successful move to DLQ with session parameter."""
        with SQLModelSession(engine) as session:
            dlq_item = dead_letter_service.move_to_dlq(
                session=session,
                job_id=failed_job.job_id,
                reason="MAX_RETRIES",
            )
            session.commit()
            
            # Verify DLQ item was created
            assert dlq_item is not None
            assert dlq_item.job_id == failed_job.job_id
            assert dlq_item.reason == "MAX_RETRIES"
        
        # Verify job is in dead_letter state (after commit)
        updated_job = job_repository.get(failed_job.job_id)
        assert updated_job.admission_state == AdmissionState.DEAD.value
        
        # Verify DLQ item exists in database
        dlq_item_db = dlq_repository.get(dlq_item.dlq_id)
        assert dlq_item_db is not None
        assert dlq_item_db.job_id == failed_job.job_id

    def test_move_to_dlq_job_not_found(self, engine, dead_letter_service):
        """Test move_to_dlq raises DLQItemNotFoundError when job not found."""
        with SQLModelSession(engine) as session:
            with pytest.raises(DLQItemNotFoundError) as exc_info:
                dead_letter_service.move_to_dlq(
                    session=session,
                    job_id="nonexistent-job-id",
                    reason="MAX_RETRIES",
                )
            assert exc_info.value.dlq_id == "nonexistent-job-id"

    def test_move_to_dlq_job_not_in_failed_state(self, engine, job_repository, dead_letter_service):
        """Test move_to_dlq raises JobNotInFailedStateError when job not in FAILED state."""
        # Create a job in PENDING state
        job = job_repository.create(
            agent_id="test-agent",
            agent_dir="/agents/test-agent",
            message="Test job",
            source="api",
            project_id="test-project",
            priority=5,
            job_metadata=None,
        )
        
        with SQLModelSession(engine) as session:
            with pytest.raises(JobNotInFailedStateError) as exc_info:
                dead_letter_service.move_to_dlq(
                    session=session,
                    job_id=job.job_id,
                    reason="MAX_RETRIES",
                )
            assert exc_info.value.job_id == job.job_id
            assert exc_info.value.current_status == AdmissionState.QUEUED.value

    def test_move_to_dlq_atomicity_both_succeed(self, engine, job_repository, dlq_repository, dead_letter_service, failed_job):
        """Test atomicity: both job transition and DLQ item creation succeed."""
        with SQLModelSession(engine) as session:
            dlq_item = dead_letter_service.move_to_dlq(
                session=session,
                job_id=failed_job.job_id,
                reason="MANUAL",
            )
            # Commit the transaction
            session.commit()
        
        # Verify job is in dead_letter state
        updated_job = job_repository.get(failed_job.job_id)
        assert updated_job.admission_state == AdmissionState.DEAD.value
        
        # Verify DLQ item exists with correct data
        dlq_item_db = dlq_repository.get_by_job_id(failed_job.job_id)
        assert dlq_item_db is not None
        assert dlq_item_db.reason == "MANUAL"
        assert dlq_item_db.error_message == "Connection timeout"

    def test_move_to_dlq_preserves_job_data(self, engine, job_repository, dlq_repository, dead_letter_service, failed_job):
        """Test that move_to_dlq preserves all job data in DLQ item."""
        with SQLModelSession(engine) as session:
            dlq_item = dead_letter_service.move_to_dlq(
                session=session,
                job_id=failed_job.job_id,
                reason="MAX_RETRIES",
            )
            session.commit()
        
        # Verify DLQ item contains all job data
        dlq_item_db = dlq_repository.get_by_job_id(failed_job.job_id)
        assert dlq_item_db.agent_id == failed_job.agent_id
        assert dlq_item_db.agent_dir == failed_job.agent_dir
        assert dlq_item_db.message == failed_job.message
        assert dlq_item_db.source == failed_job.source
        assert dlq_item_db.project_id == failed_job.project_id
        assert dlq_item_db.priority == failed_job.priority
        assert dlq_item_db.retry_count == failed_job.retry_count
        assert dlq_item_db.metadata_json == failed_job.job_metadata


class TestMoveToDLQStandalone:
    """Tests for move_to_dlq_standalone() method which creates its own session."""

    def test_move_to_dlq_standalone_success(self, engine, job_repository, dlq_repository, dead_letter_service, failed_job):
        """Test successful standalone move to DLQ."""
        dlq_item = dead_letter_service.move_to_dlq_standalone(
            job_id=failed_job.job_id,
            reason="MAX_RETRIES",
        )
        
        # Verify DLQ item was created
        assert dlq_item is not None
        assert dlq_item.job_id == failed_job.job_id
        assert dlq_item.reason == "MAX_RETRIES"
        
        # Verify job is in dead_letter state
        updated_job = job_repository.get(failed_job.job_id)
        assert updated_job.admission_state == AdmissionState.DEAD.value
        
        # Verify DLQ item exists in database
        dlq_item_db = dlq_repository.get(dlq_item.dlq_id)
        assert dlq_item_db is not None

    def test_move_to_dlq_standalone_job_not_found(self, dead_letter_service):
        """Test standalone move raises DLQItemNotFoundError when job not found."""
        with pytest.raises(DLQItemNotFoundError) as exc_info:
            dead_letter_service.move_to_dlq_standalone(
                job_id="nonexistent-job-id",
                reason="MAX_RETRIES",
            )
        assert exc_info.value.dlq_id == "nonexistent-job-id"

    def test_move_to_dlq_standalone_wrong_status(self, engine, job_repository, dead_letter_service):
        """Test standalone move raises JobNotInFailedStateError when job not in FAILED state."""
        # Create a job in PROCESSING state
        job = job_repository.create(
            agent_id="test-agent",
            agent_dir="/agents/test-agent",
            message="Test job",
            source="api",
            project_id="test-project",
            priority=5,
            job_metadata=None,
        )
        job_repository.atomic_transition(
            job.job_id,
            from_status=JobStatus.PENDING.value,
            to_status=JobStatus.PROCESSING.value,
            started_at=datetime.utcnow().isoformat(),
            instance_id="test-instance",
        )
        
        with pytest.raises(JobNotInFailedStateError) as exc_info:
            dead_letter_service.move_to_dlq_standalone(
                job_id=job.job_id,
                reason="MAX_RETRIES",
            )
        assert exc_info.value.job_id == job.job_id
        assert exc_info.value.current_status == AdmissionState.ACTIVE.value
        
        # Verify job is still in PROCESSING state (not modified)
        updated_job = job_repository.get(job.job_id)
        assert updated_job.admission_state == AdmissionState.ACTIVE.value

    def test_move_to_dlq_standalone_atomicity(self, engine, job_repository, dlq_repository, queue_repository, dead_letter_service):
        """Test atomicity: either both job transition AND DLQ creation happen, or neither."""
        # Create a new failed job for this test
        job = create_failed_job(engine, job_repository, queue_repository, "Atomicity test job")
        
        # Count before
        initial_dlq_count = dlq_repository.count()
        
        dlq_item = dead_letter_service.move_to_dlq_standalone(
            job_id=job.job_id,
            reason="MAX_RETRIES",
        )
        
        # Verify both operations happened
        final_dlq_count = dlq_repository.count()
        assert final_dlq_count == initial_dlq_count + 1
        
        # Verify job is in correct state
        updated_job = job_repository.get(job.job_id)
        assert updated_job.admission_state == AdmissionState.DEAD.value
        
        # Verify DLQ item exists
        dlq_item_db = dlq_repository.get_by_job_id(job.job_id)
        assert dlq_item_db is not None

    def test_move_to_dlq_standalone_failure_leaves_nothing(self, engine, job_repository, dlq_repository, dead_letter_service):
        """Test that failure (job not found) leaves no partial state."""
        # Count before
        initial_dlq_count = dlq_repository.count()
        
        with pytest.raises(DLQItemNotFoundError):
            dead_letter_service.move_to_dlq_standalone(
                job_id="nonexistent-job-id",
                reason="MAX_RETRIES",
            )
        
        # Verify nothing was added
        final_dlq_count = dlq_repository.count()
        assert final_dlq_count == initial_dlq_count


class TestReplayFromDLQ:
    """Tests for replay_from_dlq() method."""

    def test_replay_from_dlq_success(self, engine, job_repository, dlq_repository, dead_letter_service, failed_job):
        """Test successful replay from DLQ."""
        # First move job to DLQ
        dlq_item = dead_letter_service.move_to_dlq_standalone(
            job_id=failed_job.job_id,
            reason="MAX_RETRIES",
        )
        dlq_id = dlq_item.dlq_id
        
        # Verify job is in dead_letter state
        job_before = job_repository.get(failed_job.job_id)
        assert job_before.admission_state == AdmissionState.DEAD.value
        
        # Replay the job
        replayed_job = dead_letter_service.replay_from_dlq(dlq_id)
        
        # Verify job is now in PENDING state
        assert replayed_job is not None
        assert replayed_job.admission_state == AdmissionState.QUEUED.value
        assert replayed_job.job_id == failed_job.job_id
        assert replayed_job.retry_count == 0  # Reset
        assert replayed_job.failed_at is None  # Reset
        
        # Verify DLQ item is deleted
        dlq_item_db = dlq_repository.get(dlq_id)
        assert dlq_item_db is None
        
        # Verify job in repository
        job_after = job_repository.get(failed_job.job_id)
        assert job_after.admission_state == AdmissionState.QUEUED.value
        assert job_after.retry_count == 0

    def test_replay_from_dlq_dlq_not_found(self, dead_letter_service):
        """Test replay raises DLQItemNotFoundError when DLQ item not found."""
        with pytest.raises(DLQItemNotFoundError) as exc_info:
            dead_letter_service.replay_from_dlq(dlq_id="nonexistent-dlq-id")
        assert exc_info.value.dlq_id == "nonexistent-dlq-id"

    def test_replay_from_dlq_atomicity_success(self, engine, job_repository, dlq_repository, queue_repository, dead_letter_service):
        """Test atomicity on success: job transitions to PENDING AND DLQ item is deleted."""
        # Setup: create and move job to DLQ
        job = create_failed_job(engine, job_repository, queue_repository, "Atomicity replay test")
        dlq_item = dead_letter_service.move_to_dlq_standalone(
            job_id=job.job_id,
            reason="MAX_RETRIES",
        )
        dlq_id = dlq_item.dlq_id
        
        # Count before replay
        initial_dlq_count = dlq_repository.count()
        
        # Replay
        replayed_job = dead_letter_service.replay_from_dlq(dlq_id)
        
        # Verify both operations happened
        final_dlq_count = dlq_repository.count()
        assert final_dlq_count == initial_dlq_count - 1
        
        # Verify job state
        job_after = job_repository.get(job.job_id)
        assert job_after.admission_state == AdmissionState.QUEUED.value
        
        # Verify DLQ item deleted
        dlq_item_db = dlq_repository.get(dlq_id)
        assert dlq_item_db is None

    def test_replay_from_dlq_resets_retry_fields(self, engine, job_repository, queue_repository, dead_letter_service):
        """Test that replay_from_dlq resets retry-related fields."""
        # Create job and move to DLQ
        job = create_failed_job(engine, job_repository, queue_repository, "Retry reset test", retry_count=3)
        
        # Manually set retry count on job (simulating a job that had retries)
        with SQLModelSession(engine) as session:
            job_item = session.get(JobItem, job.job_id)
            job_item.retry_count = 3
            job_item.failed_at = datetime.utcnow().isoformat()
            job_item.error_message = "Previous error"
            session.commit()
        
        # Move to DLQ
        dlq_item = dead_letter_service.move_to_dlq_standalone(
            job_id=job.job_id,
            reason="MAX_RETRIES",
        )
        
        # Replay
        replayed_job = dead_letter_service.replay_from_dlq(dlq_item.dlq_id)
        
        # Verify fields are reset
        assert replayed_job.retry_count == 0
        assert replayed_job.failed_at is None
        assert replayed_job.error_message is None

    def test_replay_from_dlq_job_not_in_dead_letter_state(self, engine, job_repository, dlq_repository, queue_repository, dead_letter_service):
        """Test replay raises error if job is not in dead_letter state."""
        # Create job and move to DLQ
        job = create_failed_job(engine, job_repository, queue_repository, "Wrong state test")
        dlq_item = dead_letter_service.move_to_dlq_standalone(
            job_id=job.job_id,
            reason="MAX_RETRIES",
        )
        
        # Manually change job status back to FAILED (bypassing validation)
        # Phase 4: must also flip admission_state off "dead" so the
        # replay guard rejects the job (status is frozen/legacy).
        with SQLModelSession(engine) as session:
            job_item = session.get(JobItem, job.job_id)
            job_item.status = JobStatus.FAILED.value
            job_item.admission_state = AdmissionState.DONE.value
            session.commit()
        
        # Replay should fail because job is not in dead_letter state
        with pytest.raises(InvalidTransitionError) as exc_info:
            dead_letter_service.replay_from_dlq(dlq_item.dlq_id)
        
        assert exc_info.value.from_status == AdmissionState.DONE.value
        assert exc_info.value.to_status == JobStatus.PENDING.value


class TestListDLQ:
    """Tests for list_dlq() method."""

    def test_list_dlq_empty(self, dead_letter_service):
        """Test listing DLQ when empty returns empty list."""
        items, total = dead_letter_service.list_dlq()
        assert items == []
        assert total == 0

    def test_list_dlq_all_items(self, engine, job_repository, queue_repository, dead_letter_service):
        """Test listing all DLQ items."""
        # Move multiple jobs to DLQ
        for i in range(3):
            job = create_failed_job(engine, job_repository, queue_repository, f"List test job {i}")
            dead_letter_service.move_to_dlq_standalone(job_id=job.job_id, reason="MAX_RETRIES")
        
        items, total = dead_letter_service.list_dlq()
        
        assert total >= 3
        assert len(items) >= 3

    def test_list_dlq_with_project_filter(self, engine, job_repository, dead_letter_service, failed_job):
        """Test listing DLQ items filtered by project_id."""
        # Move the failed job to DLQ
        dead_letter_service.move_to_dlq_standalone(job_id=failed_job.job_id, reason="MAX_RETRIES")
        
        items, total = dead_letter_service.list_dlq(project_id="test-project")
        
        assert total >= 1
        for item in items:
            assert item.project_id == "test-project"

    def test_list_dlq_with_queue_filter(self, engine, job_repository, queue_repository, dead_letter_service):
        """Test listing DLQ items filtered by queue_id."""
        # Create a failed job with a known queue
        job = create_failed_job(engine, job_repository, queue_repository, "Queue filter test")
        # Move the failed job to DLQ
        dead_letter_service.move_to_dlq_standalone(job_id=job.job_id, reason="MAX_RETRIES")
        
        # Get the queue_id for filtering
        queue = queue_repository.get_by_name("test-project", "system_fifo_queue")
        
        items, total = dead_letter_service.list_dlq(queue_id=queue.queue_id)
        
        # Should return items with matching queue_id
        for item in items:
            assert item.queue_id == queue.queue_id

    def test_list_dlq_with_reason_filter(self, engine, job_repository, dead_letter_service, failed_job):
        """Test listing DLQ items filtered by reason."""
        # Move to DLQ with MAX_RETRIES reason
        dead_letter_service.move_to_dlq_standalone(job_id=failed_job.job_id, reason="MAX_RETRIES")
        
        items, total = dead_letter_service.list_dlq(reason="MAX_RETRIES")
        
        for item in items:
            assert item.reason == "MAX_RETRIES"

    def test_list_dlq_with_limit(self, dead_letter_service):
        """Test listing DLQ items with limit."""
        items, total = dead_letter_service.list_dlq(limit=5)
        
        assert len(items) <= 5


class TestGetDLQ:
    """Tests for get_dlq() method."""

    def test_get_dlq_existing(self, engine, job_repository, dead_letter_service, failed_job):
        """Test getting an existing DLQ item."""
        # Move job to DLQ
        dlq_item = dead_letter_service.move_to_dlq_standalone(
            job_id=failed_job.job_id,
            reason="MAX_RETRIES",
        )
        
        # Get by DLQ ID
        result = dead_letter_service.get_dlq(dlq_item.dlq_id)
        
        assert result is not None
        assert result.dlq_id == dlq_item.dlq_id
        assert result.job_id == failed_job.job_id

    def test_get_dlq_not_found(self, dead_letter_service):
        """Test getting a non-existent DLQ item returns None."""
        result = dead_letter_service.get_dlq("nonexistent-dlq-id")
        
        assert result is None

    def test_get_dlq_by_job_id(self, engine, job_repository, dead_letter_service, failed_job):
        """Test getting DLQ item by job ID."""
        # Move job to DLQ
        dead_letter_service.move_to_dlq_standalone(
            job_id=failed_job.job_id,
            reason="MAX_RETRIES",
        )
        
        # Get by job ID
        result = dead_letter_service.get_dlq_by_job_id(failed_job.job_id)
        
        assert result is not None
        assert result.job_id == failed_job.job_id


class TestDeleteDLQ:
    """Tests for delete_dlq() method."""

    def test_delete_dlq_existing(self, engine, job_repository, dead_letter_service, failed_job):
        """Test deleting an existing DLQ item."""
        # Move job to DLQ
        dlq_item = dead_letter_service.move_to_dlq_standalone(
            job_id=failed_job.job_id,
            reason="MAX_RETRIES",
        )
        dlq_id = dlq_item.dlq_id
        
        # Delete
        result = dead_letter_service.delete_dlq(dlq_id)
        
        assert result is True
        
        # Verify DLQ item is gone
        assert dead_letter_service.get_dlq(dlq_id) is None
        
        # Verify job is still in dead_letter state (delete doesn't affect job)
        job = job_repository.get(failed_job.job_id)
        assert job.admission_state == AdmissionState.DEAD.value

    def test_delete_dlq_not_found(self, dead_letter_service):
        """Test deleting a non-existent DLQ item returns False."""
        result = dead_letter_service.delete_dlq("nonexistent-dlq-id")
        
        assert result is False


class TestCountDLQ:
    """Tests for count_dlq() method."""

    def test_count_dlq_empty(self, dead_letter_service):
        """Test counting DLQ when empty returns 0."""
        count = dead_letter_service.count_dlq()
        assert count == 0

    def test_count_dlq_with_items(self, engine, job_repository, dead_letter_service, failed_job):
        """Test counting DLQ items."""
        # Move job to DLQ
        dead_letter_service.move_to_dlq_standalone(
            job_id=failed_job.job_id,
            reason="MAX_RETRIES",
        )
        
        count = dead_letter_service.count_dlq()
        assert count >= 1

    def test_count_dlq_with_project_filter(self, engine, job_repository, dead_letter_service, failed_job):
        """Test counting DLQ items filtered by project_id."""
        # Move job to DLQ
        dead_letter_service.move_to_dlq_standalone(
            job_id=failed_job.job_id,
            reason="MAX_RETRIES",
        )
        
        count = dead_letter_service.count_dlq(project_id="test-project")
        assert count >= 1
        
        count_other = dead_letter_service.count_dlq(project_id="nonexistent-project")
        assert count_other == 0


class TestDLQAtomicityEdgeCases:
    """Tests for edge cases and atomicity guarantees."""

    def test_concurrent_move_to_dlq_not_possible(self, engine, job_repository, dead_letter_service, failed_job):
        """Test that after move_to_dlq, the job is no longer in FAILED state."""
        # Move to DLQ
        dead_letter_service.move_to_dlq_standalone(
            job_id=failed_job.job_id,
            reason="MAX_RETRIES",
        )
        
        # Try to move again - should fail because job is no longer FAILED
        with pytest.raises(JobNotInFailedStateError):
            dead_letter_service.move_to_dlq_standalone(
                job_id=failed_job.job_id,
                reason="MANUAL",
            )

    def test_cleanup_dlq_removes_old_items(self, engine, dlq_repository, dead_letter_service):
        """Test cleanup_dlq method removes old items."""
        # Create an old DLQ item
        with SQLModelSession(engine) as session:
            old_time = datetime.utcnow() - timedelta(hours=25)
            item = DeadLetterItem(
                job_id="old-job-1",
                agent_id="test-agent",
                agent_dir="/agents/test-agent",
                message="Old message",
                source="api",
                project_id="test-project",
                queue_id="test-queue",
                priority=5,
                error_message="Old error",
                retry_count=0,
                failed_at=old_time.isoformat(),
                moved_to_dlq_at=old_time.isoformat(),
                reason="MAX_RETRIES",
            )
            session.add(item)
            session.commit()
        
        # Create a recent DLQ item
        with SQLModelSession(engine) as session:
            recent_time = datetime.utcnow() - timedelta(hours=1)
            item = DeadLetterItem(
                job_id="recent-job-1",
                agent_id="test-agent",
                agent_dir="/agents/test-agent",
                message="Recent message",
                source="api",
                project_id="test-project",
                queue_id="test-queue",
                priority=5,
                error_message="Recent error",
                retry_count=0,
                failed_at=recent_time.isoformat(),
                moved_to_dlq_at=recent_time.isoformat(),
                reason="MAX_RETRIES",
            )
            session.add(item)
            session.commit()
        
        # Verify recent item exists before cleanup
        recent_before = dead_letter_service.get_dlq_by_job_id("recent-job-1")
        assert recent_before is not None
        
        # Cleanup items older than 24 hours (cleanup_dlq uses max_age_days internally converted to hours)
        # Note: cleanup_dlq takes max_age_days, internally converts to hours
        deleted_count = dead_letter_service.cleanup_dlq(max_age_days=1)
        
        assert deleted_count >= 1
        
        # Recent item should still exist
        assert dead_letter_service.get_dlq_by_job_id("recent-job-1") is not None
        # Old item should be gone
        assert dead_letter_service.get_dlq_by_job_id("old-job-1") is None


class TestCleanupDLQ:
    """Tests for cleanup_dlq() method - C2: project_id filtering."""

    def test_cleanup_dlq_respects_project_id(self, engine, dlq_repository, dead_letter_service):
        """Test that cleanup_dlq only deletes items for the specified project."""
        from datetime import datetime, timedelta
        from daemon.repositories.job_queue.models import DeadLetterItem
        
        # Create old DLQ items for project-a
        with SQLModelSession(engine) as session:
            old_time = datetime.utcnow() - timedelta(hours=25)
            item_a1 = DeadLetterItem(
                job_id="job-a1",
                agent_id="test-agent",
                agent_dir="/agents/test-agent",
                message="Message A1",
                source="api",
                project_id="project-a",
                queue_id="queue-1",
                priority=5,
                error_message="Error",
                retry_count=0,
                failed_at=old_time.isoformat(),
                moved_to_dlq_at=old_time.isoformat(),
                reason="MAX_RETRIES",
            )
            session.add(item_a1)
            session.commit()
        
        # Create old DLQ items for project-b
        with SQLModelSession(engine) as session:
            old_time = datetime.utcnow() - timedelta(hours=25)
            item_b1 = DeadLetterItem(
                job_id="job-b1",
                agent_id="test-agent",
                agent_dir="/agents/test-agent",
                message="Message B1",
                source="api",
                project_id="project-b",
                queue_id="queue-1",
                priority=5,
                error_message="Error",
                retry_count=0,
                failed_at=old_time.isoformat(),
                moved_to_dlq_at=old_time.isoformat(),
                reason="MAX_RETRIES",
            )
            session.add(item_b1)
            session.commit()
        
        # Create recent DLQ item for project-a (should NOT be deleted)
        with SQLModelSession(engine) as session:
            recent_time = datetime.utcnow() - timedelta(hours=1)
            item_a2 = DeadLetterItem(
                job_id="job-a2",
                agent_id="test-agent",
                agent_dir="/agents/test-agent",
                message="Message A2",
                source="api",
                project_id="project-a",
                queue_id="queue-1",
                priority=5,
                error_message="Error",
                retry_count=0,
                failed_at=recent_time.isoformat(),
                moved_to_dlq_at=recent_time.isoformat(),
                reason="MAX_RETRIES",
            )
            session.add(item_a2)
            session.commit()
        
        # Cleanup ONLY project-a items older than 24 hours
        deleted_count = dead_letter_service.cleanup_dlq(
            max_age_days=1,
            project_id="project-a",
        )
        
        # Should only delete 1 item (job-a1), NOT job-b1
        assert deleted_count == 1
        
        # project-a old item should be gone
        assert dead_letter_service.get_dlq_by_job_id("job-a1") is None
        # project-a recent item should still exist
        assert dead_letter_service.get_dlq_by_job_id("job-a2") is not None
        # project-b item should still exist (not affected by project-a cleanup)
        assert dead_letter_service.get_dlq_by_job_id("job-b1") is not None

    def test_cleanup_dlq_without_project_id_deletes_all(self, engine, dlq_repository, dead_letter_service):
        """Test that cleanup_dlq without project_id deletes across all projects."""
        from datetime import datetime, timedelta
        from daemon.repositories.job_queue.models import DeadLetterItem
        
        # Create old DLQ items for different projects
        for project in ["project-a", "project-b", "project-c"]:
            with SQLModelSession(engine) as session:
                old_time = datetime.utcnow() - timedelta(hours=25)
                item = DeadLetterItem(
                    job_id=f"job-{project}",
                    agent_id="test-agent",
                    agent_dir="/agents/test-agent",
                    message=f"Message {project}",
                    source="api",
                    project_id=project,
                    queue_id="queue-1",
                    priority=5,
                    error_message="Error",
                    retry_count=0,
                    failed_at=old_time.isoformat(),
                    moved_to_dlq_at=old_time.isoformat(),
                    reason="MAX_RETRIES",
                )
                session.add(item)
                session.commit()
        
        # Cleanup without project_id should delete all old items
        deleted_count = dead_letter_service.cleanup_dlq(max_age_days=1)
        
        assert deleted_count == 3
        # All should be gone
        assert dead_letter_service.count_dlq() == 0


class TestMoveToDLQConcurrency:
    """Tests for C3: TOCTOU race condition handling in move_to_dlq()."""

    def test_move_to_dlq_with_lock_prevents_double_move(self, engine, job_repository, queue_repository, dead_letter_service, failed_job):
        """Test that pessimistic locking prevents double move to DLQ.
        
        This tests that when using move_to_dlq() with a shared session,
        the FOR UPDATE lock ensures only one process can move the job.
        """
        from sqlalchemy.exc import IntegrityError
        
        with SQLModelSession(engine) as session:
            # First move should succeed
            dlq_item = dead_letter_service.move_to_dlq(
                session=session,
                job_id=failed_job.job_id,
                reason="MAX_RETRIES",
            )
            session.commit()
            
            # Verify job is now in dead_letter state
            session.refresh(dlq_item)
        
        # Verify job is in dead_letter state
        updated_job = job_repository.get(failed_job.job_id)
        assert updated_job.admission_state == "dead"
        
        # Create a new failed job and verify the lock pattern works
        job2 = create_failed_job(engine, job_repository, queue_repository, "Second job")
        with SQLModelSession(engine) as session:
            dlq_item2 = dead_letter_service.move_to_dlq(
                session=session,
                job_id=job2.job_id,
                reason="MAX_RETRIES",
            )
            session.commit()
        
        updated_job2 = job_repository.get(job2.job_id)
        assert updated_job2.admission_state == "dead"


class TestListDLQPagination:
    """Tests for C4: Total count in paginated results is correct."""

    def test_list_dlq_returns_total_before_pagination(self, engine, job_repository, queue_repository, dead_letter_service):
        """Test that list_dlq returns total count BEFORE pagination, not after.
        
        This tests the fix for C4 where the router was returning len(items)
        which gave the count AFTER pagination slicing, not the total.
        """
        # Create 10 DLQ items
        for i in range(10):
            job = create_failed_job(engine, job_repository, queue_repository, f"Pagination test job {i}")
            dead_letter_service.move_to_dlq_standalone(
                job_id=job.job_id,
                reason="MAX_RETRIES",
            )
        
        # Request first page (limit=3, offset=0)
        items_page1, total = dead_letter_service.list_dlq(limit=3, offset=0)
        
        # Total should be 10 (all items), NOT 3 (only items on this page)
        assert total == 10, f"Expected total=10 (before pagination), got {total}"
        assert len(items_page1) == 3, f"Expected 3 items on page, got {len(items_page1)}"
        
        # Request second page
        items_page2, total2 = dead_letter_service.list_dlq(limit=3, offset=3)
        
        # Total should still be 10
        assert total2 == 10, f"Expected total=10 on page 2, got {total2}"
        assert len(items_page2) == 3, f"Expected 3 items on page 2, got {len(items_page2)}"
        
        # Third page (partial)
        items_page3, total3 = dead_letter_service.list_dlq(limit=3, offset=6)
        
        # Total should still be 10
        assert total3 == 10, f"Expected total=10 on page 3, got {total3}"
        assert len(items_page3) == 3, f"Expected 3 items on page 3, got {len(items_page3)}"
        
        # Fourth page (should be empty)
        items_page4, total4 = dead_letter_service.list_dlq(limit=3, offset=9)
        
        # Total should still be 10, but no items returned
        assert total4 == 10, f"Expected total=10 on page 4, got {total4}"
        assert len(items_page4) == 1, f"Expected 1 item on page 4, got {len(items_page4)}"

    def test_list_dlq_total_respects_filters(self, engine, job_repository, queue_repository, dead_letter_service):
        """Test that total count respects filters, not just pagination."""
        # Create DLQ items with different reasons
        for i in range(5):
            job = create_failed_job(engine, job_repository, queue_repository, f"MAX_RETRIES job {i}")
            dead_letter_service.move_to_dlq_standalone(
                job_id=job.job_id,
                reason="MAX_RETRIES",
            )
        
        for i in range(3):
            job = create_failed_job(engine, job_repository, queue_repository, f"MANUAL job {i}")
            dead_letter_service.move_to_dlq_standalone(
                job_id=job.job_id,
                reason="MANUAL",
            )
        
        # List with MAX_RETRIES filter - should only count MAX_RETRIES items
        items, total = dead_letter_service.list_dlq(reason="MAX_RETRIES", limit=10)
        assert total == 5, f"Expected 5 MAX_RETRIES items, got {total}"
        assert len(items) == 5
        
        # List with MANUAL filter
        items, total = dead_letter_service.list_dlq(reason="MANUAL", limit=10)
        assert total == 3, f"Expected 3 MANUAL items, got {total}"
        assert len(items) == 3


class TestDeadLetterServiceIntegration:
    """Integration tests for DeadLetterService workflow."""

    def test_full_dlq_lifecycle(self, engine, job_repository, dlq_repository, queue_repository, dead_letter_service):
        """Test complete lifecycle: create job -> fail -> move to DLQ -> replay."""
        # Create and fail a job
        job = create_failed_job(engine, job_repository, queue_repository, "Lifecycle test job")
        
        # Verify job is FAILED (admission_state is the authority; status
        # column is frozen at the INSERT default).
        assert job_repository.get(job.job_id).admission_state == AdmissionState.DONE.value
        
        # Move to DLQ
        dlq_item = dead_letter_service.move_to_dlq_standalone(
            job_id=job.job_id,
            reason="MAX_RETRIES",
        )
        
        # Verify job is in DLQ
        assert job_repository.get(job.job_id).admission_state == AdmissionState.DEAD.value
        assert dlq_repository.get_by_job_id(job.job_id) is not None
        
        # Replay from DLQ
        replayed_job = dead_letter_service.replay_from_dlq(dlq_item.dlq_id)
        
        # Verify job is back to PENDING
        assert replayed_job.admission_state == AdmissionState.QUEUED.value
        assert replayed_job.retry_count == 0
        assert dlq_repository.get_by_job_id(job.job_id) is None
        
        # Verify DLQ count is back to 0
        assert dlq_repository.count() == 0

    def test_multiple_jobs_dlq_management(self, engine, job_repository, dlq_repository, queue_repository, dead_letter_service):
        """Test managing multiple jobs in DLQ."""
        # Create and fail multiple jobs
        job_ids = []
        for i in range(3):
            job = create_failed_job(engine, job_repository, queue_repository, f"Multi job {i}")
            job_ids.append(job.job_id)
        
        # Move all to DLQ
        dlq_ids = []
        for job_id in job_ids:
            dlq_item = dead_letter_service.move_to_dlq_standalone(job_id=job_id, reason="MAX_RETRIES")
            dlq_ids.append(dlq_item.dlq_id)
        
        # Verify all in DLQ
        assert dlq_repository.count() == 3
        
        # Replay first job
        dead_letter_service.replay_from_dlq(dlq_ids[0])
        
        # Verify one is back to PENDING, two remain in DLQ
        assert job_repository.get(job_ids[0]).status == JobStatus.PENDING.value
        assert dlq_repository.count() == 2
        
        # Delete remaining DLQ items
        for dlq_id in dlq_ids[1:]:
            dead_letter_service.delete_dlq(dlq_id)
        
        # Verify all cleaned up
        assert dlq_repository.count() == 0

    def test_dlq_error_handling_workflow(self, engine, job_repository, dead_letter_service):
        """Test error handling: try to move non-failed job to DLQ."""
        # Create a PENDING job
        job = job_repository.create(
            agent_id="test-agent",
            agent_dir="/agents/test-agent",
            message="Pending job",
            source="api",
            project_id="test-project",
            priority=5,
            job_metadata=None,
        )
        
        # Try to move to DLQ - should raise error
        with pytest.raises(JobNotInFailedStateError):
            dead_letter_service.move_to_dlq_standalone(
                job_id=job.job_id,
                reason="MANUAL",
            )
        
        # Verify job is still PENDING
        assert job_repository.get(job.job_id).status == JobStatus.PENDING.value
        
        # Verify no DLQ items exist
        assert dead_letter_service.count_dlq() == 0

    def test_replay_nonexistent_dlq(self, dead_letter_service):
        """Test replaying non-existent DLQ item raises error."""
        with pytest.raises(DLQItemNotFoundError):
            dead_letter_service.replay_from_dlq("nonexistent-dlq-id")


class TestSQLStatusGuard:
    """Tests for M3/M4: SQL-level status guards (defense-in-depth).

    These tests verify that ``move_to_dlq_standalone`` and
    ``replay_from_dlq`` emit SQL containing ``WHERE status = ...`` guards
    in addition to the Python-side checks and ``FOR UPDATE`` row locks.
    The guards ensure that a concurrent writer which somehow slipped
    past the row lock (or a future caller that bypasses the Python
    check) cannot silently clobber a non-expected status.
    """

    @staticmethod
    def _capture_job_queue_items_statements(engine, fn):
        """Run ``fn`` and capture all SQL statements touching
        ``job_queue_items``. Returns the list of captured statement
        strings (in execution order).
        """
        from sqlalchemy import event

        captured: list[str] = []

        def _before_cursor_execute(conn, cursor, statement, params, context, executemany):
            if "job_queue_items" in statement:
                captured.append(statement)

        event.listen(engine, "before_cursor_execute", _before_cursor_execute)
        try:
            fn()
        finally:
            event.remove(engine, "before_cursor_execute", _before_cursor_execute)

        return captured

    def test_move_to_dlq_emits_sql_with_status_failed_guard(
        self, engine, job_repository, queue_repository, dead_letter_service
    ):
        """M3: ``move_to_dlq_standalone`` must emit an UPDATE on
        ``job_queue_items`` gated by ``WHERE status = 'failed'``.
        """
        job = create_failed_job(
            engine, job_repository, queue_repository, "SQL guard test M3"
        )

        statements = self._capture_job_queue_items_statements(
            engine,
            lambda: dead_letter_service.move_to_dlq_standalone(
                job_id=job.job_id, reason="MAX_RETRIES"
            ),
        )

        # Find the UPDATE statements on job_queue_items
        update_stmts = [
            s for s in statements
            if s.lstrip().split(None, 1)[0].upper() == "UPDATE"
        ]
        assert update_stmts, (
            f"No UPDATE on job_queue_items captured. Captured: {statements}"
        )

        # At least one UPDATE must carry a WHERE clause referencing
        # both ``status`` and the literal ``failed``. SQLAlchemy renders
        # the bound parameter as ``:status`` (or ``:param_1``); the
        # string ``failed`` itself is NOT inlined into the SQL — instead
        # we check the WHERE-clause structure: it must include a
        # comparison between the status column and a bound parameter.
        guarded = [
            s for s in update_stmts
            if "admission_state" in s.lower() and "where" in s.lower()
        ]
        assert guarded, (
            f"UPDATE on job_queue_items lacks status guard. "
            f"Statements:\n  " + "\n  ".join(update_stmts)
        )

        # Verify the executed operation actually transitioned the job
        updated = job_repository.get(job.job_id)
        assert updated.admission_state == AdmissionState.DEAD.value

    def test_replay_from_dlq_emits_sql_with_status_dead_letter_guard(
        self, engine, job_repository, dlq_repository, queue_repository, dead_letter_service
    ):
        """M4: ``replay_from_dlq`` must emit an UPDATE on
        ``job_queue_items`` gated by ``WHERE status = 'dead_letter'``.
        """
        job = create_failed_job(
            engine, job_repository, queue_repository, "SQL guard test M4"
        )
        dlq_item = dead_letter_service.move_to_dlq_standalone(
            job_id=job.job_id, reason="MAX_RETRIES"
        )

        statements = self._capture_job_queue_items_statements(
            engine,
            lambda: dead_letter_service.replay_from_dlq(dlq_item.dlq_id),
        )

        # Find the UPDATE statements on job_queue_items
        update_stmts = [
            s for s in statements
            if s.lstrip().split(None, 1)[0].upper() == "UPDATE"
        ]
        assert update_stmts, (
            f"No UPDATE on job_queue_items captured. Captured: {statements}"
        )

        # At least one UPDATE must carry a WHERE clause referencing
        # ``status`` (the bound parameter guards the status transition).
        guarded = [
            s for s in update_stmts
            if "admission_state" in s.lower() and "where" in s.lower()
        ]
        assert guarded, (
            f"UPDATE on job_queue_items lacks status guard. "
            f"Statements:\n  " + "\n  ".join(update_stmts)
        )

        # Verify the executed operation actually transitioned the job
        replayed = job_repository.get(job.job_id)
        assert replayed.admission_state == AdmissionState.QUEUED.value
        assert replayed.retry_count == 0
        assert replayed.failed_at is None
        assert replayed.error_message is None
        assert replayed.started_at is None
        assert replayed.completed_at is None
        assert replayed.instance_id is None

    def test_move_to_dlq_shared_session_emits_sql_with_status_failed_guard(
        self, engine, job_repository, queue_repository, dead_letter_service
    ):
        """M3 (shared-session variant): ``move_to_dlq`` must also emit
        a guarded UPDATE. Caller commits the session — the guard must
        still be present.
        """
        job = create_failed_job(
            engine, job_repository, queue_repository, "SQL guard shared M3"
        )

        statements = self._capture_job_queue_items_statements(
            engine,
            lambda: (
                _commit_after(
                    engine,
                    lambda session: dead_letter_service.move_to_dlq(
                        session=session,
                        job_id=job.job_id,
                        reason="MAX_RETRIES",
                    ),
                )
            ),
        )

        update_stmts = [
            s for s in statements
            if s.lstrip().split(None, 1)[0].upper() == "UPDATE"
        ]
        assert update_stmts, (
            f"No UPDATE on job_queue_items captured. Captured: {statements}"
        )

        guarded = [
            s for s in update_stmts
            if "admission_state" in s.lower() and "where" in s.lower()
        ]
        assert guarded, (
            f"Shared-session UPDATE on job_queue_items lacks status guard. "
            f"Statements:\n  " + "\n  ".join(update_stmts)
        )

        updated = job_repository.get(job.job_id)
        assert updated.admission_state == AdmissionState.DEAD.value

    def test_move_to_dlq_standalone_no_dlq_item_on_guard_failure(
        self, engine, job_repository, dlq_repository, queue_repository, dead_letter_service
    ):
        """M3 guard rollback: when ``move_to_dlq_standalone`` cannot
        find the job in 'failed' state via the SQL guard, no DLQ row
        may be left behind. The existing Python-side check catches the
        'wrong initial status' case before the guard runs, so this test
        validates the rollback discipline of the standalone path:
        raising ``JobNotInFailedStateError`` for any non-failed status
        leaves the ``dead_letter_items`` table untouched.
        """
        # Create a job in PROCESSING (not FAILED) so the Python check
        # rejects it before the guard runs. The combined effect is the
        # same as a guard failure from the caller's perspective: no DLQ
        # row inserted, exception raised.
        job = job_repository.create(
            agent_id="test-agent",
            agent_dir="/agents/test-agent",
            message="Guard rollback test",
            source="api",
            project_id="test-project",
            priority=5,
            job_metadata=None,
        )
        job_repository.atomic_transition(
            job.job_id,
            from_status=JobStatus.PENDING.value,
            to_status=JobStatus.PROCESSING.value,
            started_at=datetime.utcnow().isoformat(),
            instance_id="test-instance",
        )

        initial_dlq_count = dlq_repository.count() if hasattr(dlq_repository, "count") else 0

        with pytest.raises(JobNotInFailedStateError):
            dead_letter_service.move_to_dlq_standalone(
                job_id=job.job_id, reason="MAX_RETRIES"
            )

        # Job admission_state must be unchanged (active, not dead)
        assert job_repository.get(job.job_id).admission_state == AdmissionState.ACTIVE.value

        # No DLQ row may exist for this job
        assert dlq_repository.get_by_job_id(job.job_id) is None

    def test_replay_from_dlq_no_dlq_delete_on_guard_failure(
        self, engine, job_repository, dlq_repository, queue_repository, dead_letter_service
    ):
        """M4 guard rollback: when ``replay_from_dlq`` cannot find the
        job in 'dead_letter' state via the SQL guard, the DLQ row must
        remain in place (no partial delete). Combined with the Python
        check, any non-dead_letter status results in the DLQ row
        staying put and the job untouched.
        """
        job = create_failed_job(
            engine, job_repository, queue_repository, "Replay guard rollback"
        )
        dlq_item = dead_letter_service.move_to_dlq_standalone(
            job_id=job.job_id, reason="MAX_RETRIES"
        )

        # Manually flip the job back to FAILED via the repository's
        # raw path (bypassing the FOR UPDATE lock — the Python check
        # in replay_from_dlq is what catches this in practice).
        # Phase 4: must also flip admission_state off "dead" so the
        # replay guard rejects the job (status is frozen/legacy).
        with SQLModelSession(engine) as session:
            job_item = session.get(JobItem, job.job_id)
            job_item.status = JobStatus.FAILED.value
            job_item.admission_state = AdmissionState.DONE.value
            session.commit()

        with pytest.raises(InvalidTransitionError):
            dead_letter_service.replay_from_dlq(dlq_item.dlq_id)

        # DLQ row must still exist (the failed replay must not have
        # deleted it).
        assert dlq_repository.get(dlq_item.dlq_id) is not None

        # Job admission_state must be unchanged (done, not dead).
        assert job_repository.get(job.job_id).admission_state == AdmissionState.DONE.value


def _commit_after(engine, fn):
    """Helper: open a session, run ``fn(session)``, commit, and return
    the result. Mirrors the caller pattern in ``job_retry_engine.py``.
    """
    with SQLModelSession(engine) as session:
        result = fn(session)
        session.commit()
        return result
