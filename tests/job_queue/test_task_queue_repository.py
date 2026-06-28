"""Tests for JobRepository.

This module tests the SQLModel-based repository for job queue CRUD operations.
"""

import pytest
import time

from daemon.repositories.job_queue import AdmissionState, JobRepository
from daemon.repositories.job_queue.models import JobStatus, JobItem
from daemon.services.job_state_machine import InvalidTransitionError


class TestRepositoryCreate:
    """Tests for job creation."""

    def test_create_job_basic(self, repository, sample_job_data):
        """Test creating a basic job."""
        job = repository.create(**sample_job_data)
        
        assert job.job_id is not None
        assert job.agent_dir == sample_job_data["agent_dir"]
        assert job.message == sample_job_data["message"]
        assert job.source == sample_job_data["source"]
        assert job.project_id == sample_job_data["project_id"]
        assert job.priority == sample_job_data["priority"]
        assert job.admission_state == AdmissionState.QUEUED.value
        assert job.job_metadata == sample_job_data["job_metadata"]

    def test_create_job_without_project(self, repository, sample_job_data_no_project):
        """Test creating a job without project_id."""
        job = repository.create(**sample_job_data_no_project)
        
        assert job.job_id is not None
        assert job.project_id is None
        assert job.admission_state == AdmissionState.QUEUED.value

    def test_create_job_default_values(self, repository):
        """Test creating job with minimal parameters."""
        job = repository.create(
            agent_id="test-agent",
            agent_dir="/test/agent",
            message="Test message"
        )
        
        assert job.job_id is not None
        assert job.source == "api"  # Default value
        assert job.priority == 5  # Default value
        assert job.admission_state == AdmissionState.QUEUED.value
        assert job.job_metadata == {}  # Default empty dict

    def test_create_job_generates_timestamps(self, repository, sample_job_data):
        """Test that create generates created_at timestamp."""
        job = repository.create(**sample_job_data)
        
        assert job.created_at is not None
        assert job.started_at is None
        assert job.completed_at is None

    def test_create_job_uuid_format(self, repository, sample_job_data):
        """Test that job_id is a valid UUID."""
        job = repository.create(**sample_job_data)
        
        # Should be a valid UUID format (36 chars with hyphens)
        assert len(job.job_id) == 36
        assert job.job_id.count("-") == 4

    def test_create_multiple_jobs_unique_ids(self, repository, sample_job_data):
        """Test that multiple created jobs have unique IDs."""
        job1 = repository.create(**sample_job_data)
        job2 = repository.create(**sample_job_data)
        job3 = repository.create(**sample_job_data)
        
        assert job1.job_id != job2.job_id
        assert job2.job_id != job3.job_id
        assert job1.job_id != job3.job_id


class TestRepositoryRead:
    """Tests for job retrieval."""

    def test_get_existing_job(self, repository, sample_job_data):
        """Test getting an existing job by ID."""
        created = repository.create(**sample_job_data)
        
        retrieved = repository.get(created.job_id)
        
        assert retrieved is not None
        assert retrieved.job_id == created.job_id
        assert retrieved.message == created.message

    def test_get_nonexistent_job(self, repository):
        """Test getting a non-existent job returns None."""
        result = repository.get("nonexistent-id")
        assert result is None

    def test_get_by_instance_existing(self, repository, sample_job_data):
        """Test getting job by instance ID."""
        created = repository.create(**sample_job_data)
        started = repository.start_job(created.job_id, "test-instance")
        
        retrieved = repository.get_by_instance("test-instance")
        
        assert retrieved is not None
        assert retrieved.job_id == created.job_id

    def test_get_by_instance_nonexistent(self, repository):
        """Test getting by non-existent instance returns None."""
        result = repository.get_by_instance("nonexistent-instance")
        assert result is None


class TestRepositoryList:
    """Tests for job listing."""

    def test_list_all_jobs(self, repository, sample_job_data):
        """Test listing all jobs."""
        repository.create(**sample_job_data)
        repository.create(**sample_job_data)
        repository.create(**sample_job_data)
        
        jobs, total = repository.list()
        
        assert len(jobs) == 3
        assert total == 3

    def test_list_by_status(self, repository, sample_job_data):
        """Test listing jobs filtered by status."""
        job1 = repository.create(**sample_job_data)
        job2 = repository.create(**sample_job_data)
        
        # Start job1
        repository.start_job(job1.job_id, "instance-1")
        
        pending_jobs, total = repository.list(statuses=[JobStatus.PENDING.value])
        processing_jobs, _ = repository.list(statuses=[JobStatus.PROCESSING.value])
        
        assert len(pending_jobs) == 1
        assert pending_jobs[0].job_id == job2.job_id
        assert len(processing_jobs) == 1
        assert processing_jobs[0].job_id == job1.job_id

    def test_list_by_project(self, repository, sample_job_data):
        """Test listing jobs filtered by project."""
        job1 = repository.create(**sample_job_data)
        job2 = repository.create(
            **{
                **sample_job_data,
                "project_id": "other-project"
            }
        )
        
        jobs, total = repository.list(project_id="test-project")
        
        assert len(jobs) == 1
        assert jobs[0].job_id == job1.job_id

    def test_list_with_pagination(self, repository, sample_job_data):
        """Test listing with limit and offset."""
        for i in range(5):
            repository.create(**sample_job_data)
        
        # Get first page
        page1, total = repository.list(limit=2, offset=0)
        assert len(page1) == 2
        assert total == 5
        
        # Get second page
        page2, _ = repository.list(limit=2, offset=2)
        assert len(page2) == 2
        
        # Get last item
        page3, _ = repository.list(limit=2, offset=4)
        assert len(page3) == 1

    def test_list_empty_queue(self, repository):
        """Test listing when no jobs exist."""
        jobs, total = repository.list()
        
        assert jobs == []
        assert total == 0

    def test_list_pending_by_project(self, repository, sample_job_data):
        """Test listing pending jobs for a specific project."""
        # Create multiple jobs for same project
        job1 = repository.create(**sample_job_data)  # priority=5
        job2 = repository.create(**sample_job_data)  # priority=5
        
        # Create job for different project
        repository.create(**{**sample_job_data, "project_id": "other"})
        
        pending = repository.list_pending_by_project("test-project")
        
        assert len(pending) == 2
        assert all(j.admission_state == AdmissionState.QUEUED.value for j in pending)

    def test_list_pending_ordered_by_priority(self, repository):
        """Test that pending jobs are ordered by priority descending."""
        # Create jobs with different priorities
        repository.create(
            agent_id="test-agent", agent_dir="/test", message="low",
            project_id="test", priority=1
        )
        repository.create(
            agent_id="test-agent", agent_dir="/test", message="high",
            project_id="test", priority=10
        )
        repository.create(
            agent_id="test-agent", agent_dir="/test", message="medium",
            project_id="test", priority=5
        )
        
        pending = repository.list_pending_by_project("test")
        
        assert len(pending) == 3
        assert pending[0].message == "high"  # priority=10
        assert pending[1].message == "medium"  # priority=5
        assert pending[2].message == "low"  # priority=1

    def test_list_all_pending(self, repository, sample_job_data):
        """Test listing all pending jobs regardless of project."""
        # Create jobs for different projects
        job1 = repository.create(**sample_job_data)
        job2 = repository.create(
            **{
                **sample_job_data,
                "project_id": "other-project",
                "priority": 10  # Higher priority
            }
        )
        
        # Start job1
        repository.start_job(job1.job_id, "instance-1")
        
        pending = repository.list_all_pending()
        
        # Should only return job2 (job1 is now processing)
        assert len(pending) == 1
        assert pending[0].job_id == job2.job_id


class TestRepositoryUpdate:
    """Tests for job updates."""

    def test_update_single_field(self, repository, sample_job_data):
        """Test updating a single field."""
        job = repository.create(**sample_job_data)
        
        updated = repository.update(job.job_id, priority=8)
        
        assert updated is not None
        assert updated.priority == 8
        assert updated.message == sample_job_data["message"]  # Unchanged

    def test_update_multiple_fields(self, repository, sample_job_data):
        """Test updating multiple fields."""
        job = repository.create(**sample_job_data)
        
        updated = repository.update(
            job.job_id,
            priority=3,
            message="Updated message"
        )
        
        assert updated.priority == 3
        assert updated.message == "Updated message"

    def test_update_nonexistent_job(self, repository):
        """Test updating non-existent job returns None."""
        result = repository.update("nonexistent-id", priority=10)
        assert result is None

    def test_update_with_status_kwarg_raises(self, repository, sample_job_data):
        """Test that update() refuses status kwarg (use atomic_transition).

        L1 guard: callers must NOT bypass atomic_transition by writing
        ``status=`` directly via the generic ``update()``. Status changes
        are routed through ``atomic_transition`` so the SQL-level
        ``WHERE status = :from_status`` guard prevents concurrent
        clobbering of terminal statuses. The generic update() now
        raises ``ValueError`` for any ``status=`` kwarg regardless of
        whether the value is a valid JobStatus enum member.
        """
        job = repository.create(**sample_job_data)

        with pytest.raises(ValueError) as exc_info:
            # Even a valid status value is rejected — the guard fires
            # before any value validation.
            repository.update(job.job_id, status=JobStatus.COMPLETED.value)

        assert "atomic_transition" in str(exc_info.value)

    def test_update_with_invalid_status_kwarg_raises(self, repository, sample_job_data):
        """L1 guard fires BEFORE value validation — even an invalid
        status string is rejected with the same ``Use atomic_transition``
        message. The previous "Invalid status" branch was reachable
        only via the unprotected path; now the guard is the only path.
        """
        job = repository.create(**sample_job_data)

        with pytest.raises(ValueError) as exc_info:
            repository.update(job.job_id, status="bogus-status-value")

        # The new guard message references atomic_transition, not the
        # old "Invalid status" text — the value-validation branch is
        # no longer reachable for status= kwargs.
        assert "atomic_transition" in str(exc_info.value)

    def test_update_with_non_status_kwargs_still_works(self, repository, sample_job_data):
        """L1 guard is targeted: only ``status=`` is rejected. Other
        field updates (``priority``, ``message``, ``job_metadata``,
        etc.) continue to work via ``update()`` — those are not part
        of the state-machine contract and don't need the
        ``WHERE status = :from_status`` guard.
        """
        job = repository.create(**sample_job_data)

        # Update non-status fields — must succeed and persist.
        updated = repository.update(
            job.job_id,
            priority=8,
            message="new-message",
        )

        assert updated is not None
        assert updated.priority == 8
        assert updated.message == "new-message"
        # Status must NOT have been touched by the update.
        assert updated.admission_state == AdmissionState.QUEUED.value


class TestRepositoryJobLifecycle:
    """Tests for job lifecycle transitions."""

    def test_start_pending_job(self, repository, sample_job_data):
        """Test starting a pending job."""
        job = repository.create(**sample_job_data)
        
        started = repository.start_job(job.job_id, "instance-1")
        
        assert started is not None
        assert started.admission_state == AdmissionState.ACTIVE.value
        assert started.instance_id == "instance-1"
        assert started.started_at is not None

    def test_start_already_started_job_raises(self, repository, sample_job_data):
        """Test starting an already started job raises ValueError."""
        job = repository.create(**sample_job_data)
        repository.start_job(job.job_id, "instance-1")
        
        with pytest.raises(ValueError) as exc_info:
            repository.start_job(job.job_id, "instance-2")
        
        assert "Cannot start job" in str(exc_info.value)
        assert "active" in str(exc_info.value)

    def test_start_completed_job_raises(self, repository, sample_job_data):
        """Test starting a completed job raises ValueError."""
        job = repository.create(**sample_job_data)
        started = repository.start_job(job.job_id, "instance-1")
        repository.complete_job(started.job_id)
        
        with pytest.raises(ValueError) as exc_info:
            repository.start_job(job.job_id, "instance-2")
        
        assert "Cannot start job" in str(exc_info.value)
        assert "done" in str(exc_info.value)

    def test_complete_processing_job(self, repository, sample_job_data):
        """Test completing a processing job."""
        job = repository.create(**sample_job_data)
        started = repository.start_job(job.job_id, "instance-1")
        
        completed = repository.complete_job(
            started.job_id,
            result_summary="Job completed successfully"
        )
        
        assert completed is not None
        assert completed.admission_state == AdmissionState.DONE.value
        assert completed.completed_at is not None
        assert completed.result_summary == "Job completed successfully"

    def test_complete_pending_job_raises(self, repository, sample_job_data):
        """Test completing a pending job raises InvalidTransitionError."""
        job = repository.create(**sample_job_data)
        
        with pytest.raises(InvalidTransitionError) as exc_info:
            repository.complete_job(job.job_id)
        
        assert exc_info.value.from_status == "queued"
        assert exc_info.value.to_status == "completed"

    def test_fail_processing_job(self, repository, sample_job_data):
        """Test failing a processing job."""
        job = repository.create(**sample_job_data)
        started = repository.start_job(job.job_id, "instance-1")
        
        failed = repository.fail_job(
            started.job_id,
            error_message="Something went wrong"
        )
        
        assert failed is not None
        assert failed.admission_state == AdmissionState.DONE.value
        assert failed.completed_at is not None
        assert failed.error_message == "Something went wrong"

    def test_fail_pending_job_raises(self, repository, sample_job_data):
        """Test failing a pending job raises InvalidTransitionError."""
        job = repository.create(**sample_job_data)
        
        with pytest.raises(InvalidTransitionError) as exc_info:
            repository.fail_job(job.job_id, "Error")
        
        assert exc_info.value.from_status == "queued"
        assert exc_info.value.to_status == "failed"

    def test_cancel_pending_job(self, repository, sample_job_data):
        """Test cancelling a pending job."""
        job = repository.create(**sample_job_data)
        
        cancelled = repository.cancel_job(job.job_id)
        
        assert cancelled is not None
        assert cancelled.admission_state == AdmissionState.DONE.value
        assert cancelled.cancelled_at is not None

    def test_cancel_processing_job(self, repository, sample_job_data):
        """Test cancelling a processing job succeeds (PROCESSING -> CANCELLED)."""
        job = repository.create(**sample_job_data)
        repository.start_job(job.job_id, "instance-1")
        
        cancelled = repository.cancel_job(job.job_id)
        
        assert cancelled is not None
        assert cancelled.admission_state == AdmissionState.DONE.value
        assert cancelled.cancelled_at is not None


class TestRepositoryDelete:
    """Tests for job hard deletion."""

    def test_hard_delete_existing_job(self, repository, sample_job_data):
        """Test hard_delete() removes existing job permanently."""
        job = repository.create(**sample_job_data)
        
        result = repository.hard_delete(job.job_id)
        
        assert result["deleted"] is True
        assert result["job_id"] == job.job_id
        
        # Verify job is gone
        assert repository.get(job.job_id) is None

    def test_hard_delete_nonexistent_job(self, repository):
        """Test hard_delete() on non-existent job returns error."""
        result = repository.hard_delete("nonexistent-id")
        
        assert result["deleted"] is False
        assert "error" in result

    def test_hard_delete_terminal_jobs(self, repository, sample_job_data):
        """Test hard_delete_terminal() removes all terminal jobs."""
        # Create and complete some jobs
        job1 = repository.create(**sample_job_data)
        job2 = repository.create(**sample_job_data)
        job3 = repository.create(**sample_job_data)
        
        repository.start_job(job1.job_id, "i1")
        repository.start_job(job2.job_id, "i2")
        repository.complete_job(job1.job_id)
        repository.complete_job(job2.job_id)
        # job3 remains pending
        
        deleted_count = repository.hard_delete_terminal()
        
        assert deleted_count == 2
        assert repository.get(job1.job_id) is None
        assert repository.get(job2.job_id) is None
        assert repository.get(job3.job_id) is not None

    def test_hard_delete_by_project(self, repository, sample_job_data):
        """Test hard_delete_by_project() removes all jobs for a project."""
        # Create jobs for multiple projects
        job1 = repository.create(**sample_job_data)  # test-project
        job2 = repository.create(**sample_job_data)  # test-project
        job3 = repository.create(
            **{**sample_job_data, "project_id": "other"}
        )
        
        deleted_count = repository.hard_delete_by_project("test-project")
        
        assert deleted_count == 2
        assert repository.get(job1.job_id) is None
        assert repository.get(job2.job_id) is None
        assert repository.get(job3.job_id) is not None

    def test_hard_delete_terminal_when_none(self, repository):
        """Test hard_delete_terminal() when no terminal jobs exist."""
        count = repository.hard_delete_terminal()
        assert count == 0


class TestRepositoryEdgeCases:
    """Tests for edge cases and error conditions."""

    def test_create_job_with_extreme_priority(self, repository, sample_job_data):
        """Test creating jobs with boundary priority values."""
        low_job = repository.create(**{**sample_job_data, "priority": 1})
        high_job = repository.create(**{**sample_job_data, "priority": 10})
        
        assert low_job.priority == 1
        assert high_job.priority == 10

    def test_create_job_with_metadata(self, repository):
        """Test creating job with complex metadata."""
        metadata = {
            "user_id": "user-123",
            "tags": ["urgent", "backend"],
            "config": {"timeout": 30, "retries": 3}
        }
        
        job = repository.create(
            agent_id="test-agent",
            agent_dir="/test",
            message="Test",
            job_metadata=metadata
        )
        
        assert job.job_metadata == metadata

    def test_start_job_with_empty_instance(self, repository, sample_job_data):
        """Test starting job with empty instance ID."""
        job = repository.create(**sample_job_data)
        
        # Empty string should be allowed
        started = repository.start_job(job.job_id, "")
        assert started is not None
        assert started.instance_id == ""

    def test_update_job_metadata(self, repository, sample_job_data):
        """Test updating job metadata."""
        job = repository.create(**sample_job_data)
        
        updated = repository.update(
            job.job_id,
            job_metadata={"new_key": "new_value"}
        )
        
        assert updated.job_metadata == {"new_key": "new_value"}

    def test_list_with_filters_combined(self, repository, sample_job_data):
        """Test listing with multiple filters combined."""
        # Create job in different states
        job1 = repository.create(**sample_job_data)
        repository.create(**{**sample_job_data, "project_id": "other"})
        
        repository.start_job(job1.job_id, "instance-1")
        
        # Filter by both status and project
        jobs, total = repository.list(
            statuses=[JobStatus.PENDING.value],
            project_id="test-project"
        )
        
        assert total == 0  # No pending jobs for test-project

    def test_get_job_idempotent(self, repository, sample_job_data):
        """Test that getting same job multiple times works."""
        job = repository.create(**sample_job_data)
        
        result1 = repository.get(job.job_id)
        result2 = repository.get(job.job_id)
        result3 = repository.get(job.job_id)
        
        assert result1.job_id == result2.job_id == result3.job_id

    def test_start_nonexistent_job(self, repository):
        """Test starting non-existent job returns None."""
        result = repository.start_job("nonexistent-id", "instance")
        assert result is None

    def test_complete_nonexistent_job(self, repository):
        """Test completing non-existent job returns None."""
        result = repository.complete_job("nonexistent-id")
        assert result is None

    def test_fail_nonexistent_job(self, repository):
        """Test failing non-existent job returns None."""
        result = repository.fail_job("nonexistent-id", "error")
        assert result is None

    def test_cancel_nonexistent_job(self, repository):
        """Test cancelling non-existent job returns None."""
        result = repository.cancel_job("nonexistent-id")
        assert result is None


class TestRepositoryConcurrency:
    """Tests for concurrent access patterns."""

    def test_rapid_create_operations(self, repository, sample_job_data):
        """Test creating many jobs rapidly."""
        jobs = []
        for i in range(100):
            jobs.append(repository.create(**sample_job_data))
        
        assert len(jobs) == 100
        assert len(set(j.job_id for j in jobs)) == 100  # All unique

    def test_job_status_consistency(self, repository, sample_job_data):
        """Test that job status transitions are consistent."""
        job = repository.create(**sample_job_data)
        
        # Verify initial state
        assert job.admission_state == AdmissionState.QUEUED.value
        
        # Start job
        started = repository.start_job(job.job_id, "instance-1")
        assert started.admission_state == AdmissionState.ACTIVE.value
        
        # Complete job
        completed = repository.complete_job(job.job_id, "Done")
        assert completed.admission_state == AdmissionState.DONE.value
        
        # Verify final state persists
        final = repository.get(job.job_id)
        assert final.admission_state == AdmissionState.DONE.value
        assert final.completed_at is not None


class TestJobStatusValidation:
    """Tests for JobStatus enum validation."""

    def test_valid_status_values(self):
        """Test all valid status values."""
        assert JobStatus.is_valid("pending")
        assert JobStatus.is_valid("processing")
        assert JobStatus.is_valid("completed")
        assert JobStatus.is_valid("failed")
        assert JobStatus.is_valid("cancelled")

    def test_invalid_status_values(self):
        """Test invalid status values return False."""
        assert JobStatus.is_valid("invalid") is False
        assert JobStatus.is_valid("") is False
        assert JobStatus.is_valid("PENDING") is False  # Case sensitive
        assert JobStatus.is_valid("Pending") is False  # Case sensitive


class TestJobItem:
    """Tests for JobItem model."""

    def test_to_dict(self, repository, sample_job_data):
        """Test JobItem.to_dict() method."""
        job = repository.create(**sample_job_data)
        
        job_dict = job.to_dict()
        
        assert isinstance(job_dict, dict)
        assert job_dict["job_id"] == job.job_id
        assert job_dict["message"] == job.message
        assert job_dict["status"] == job.status
        assert job_dict["metadata"] == job.job_metadata


class TestRepositoryListPendingByQueue:
    """Tests for listing pending jobs by queue."""

    def test_list_pending_by_queue_returns_only_queue_jobs(self, repository, queue_repository):
        """Test list_pending_by_queue returns only jobs for specified queue."""
        # Create two queues
        queue1 = queue_repository.create(project_id="test-project", queue_name="queue1")
        queue2 = queue_repository.create(project_id="test-project", queue_name="queue2")
        
        # Create jobs in queue1 and queue2
        job1 = repository.create(
            agent_id="test-agent", agent_dir="/test", message="job1",
            project_id="test-project", queue_id=queue1.queue_id
        )
        job2 = repository.create(
            agent_id="test-agent", agent_dir="/test", message="job2",
            project_id="test-project", queue_id=queue1.queue_id
        )
        job3 = repository.create(
            agent_id="test-agent", agent_dir="/test", message="job3",
            project_id="test-project", queue_id=queue2.queue_id
        )
        
        # List pending jobs for queue1 only
        pending = repository.list_pending_by_queue(queue1.queue_id)
        
        assert len(pending) == 2
        job_ids = [j.job_id for j in pending]
        assert job1.job_id in job_ids
        assert job2.job_id in job_ids
        assert job3.job_id not in job_ids

    def test_list_pending_by_queue_excludes_other_queues(self, repository, queue_repository):
        """Test list_pending_by_queue does not include jobs from other queues."""
        # Create queues for different projects
        queue_a = queue_repository.create(project_id="project-a", queue_name="default")
        queue_b = queue_repository.create(project_id="project-b", queue_name="default")
        
        # Create jobs for each queue
        job_a = repository.create(
            agent_id="test-agent", agent_dir="/test", message="job-a",
            project_id="project-a", queue_id=queue_a.queue_id
        )
        repository.create(
            agent_id="test-agent", agent_dir="/test", message="job-b",
            project_id="project-b", queue_id=queue_b.queue_id
        )
        
        # Only queue_a jobs should be returned
        pending = repository.list_pending_by_queue(queue_a.queue_id)
        
        assert len(pending) == 1
        assert pending[0].job_id == job_a.job_id

    def test_list_pending_by_queue_ordered_by_priority(self, repository, queue_repository):
        """Test list_pending_by_queue returns jobs ordered by priority descending."""
        queue = queue_repository.create(project_id="test-project", queue_name="priority-queue")
        
        # Create jobs with different priorities (out of order)
        repository.create(
            agent_id="test-agent", agent_dir="/test", message="low-priority",
            project_id="test-project", priority=1, queue_id=queue.queue_id
        )
        repository.create(
            agent_id="test-agent", agent_dir="/test", message="high-priority",
            project_id="test-project", priority=10, queue_id=queue.queue_id
        )
        repository.create(
            agent_id="test-agent", agent_dir="/test", message="medium-priority",
            project_id="test-project", priority=5, queue_id=queue.queue_id
        )
        
        pending = repository.list_pending_by_queue(queue.queue_id)
        
        assert len(pending) == 3
        assert pending[0].message == "high-priority"  # priority=10
        assert pending[1].message == "medium-priority"  # priority=5
        assert pending[2].message == "low-priority"  # priority=1


class TestRepositoryListByQueue:
    """Tests for listing jobs by queue with filters."""

    def test_list_by_queue_basic(self, repository, queue_repository):
        """Test list_by_queue returns all jobs for a queue."""
        queue = queue_repository.create(project_id="test-project", queue_name="list-queue")
        
        # Create 3 jobs with that queue_id
        job1 = repository.create(
            agent_id="test-agent", agent_dir="/test", message="job1",
            project_id="test-project", queue_id=queue.queue_id
        )
        job2 = repository.create(
            agent_id="test-agent", agent_dir="/test", message="job2",
            project_id="test-project", queue_id=queue.queue_id
        )
        job3 = repository.create(
            agent_id="test-agent", agent_dir="/test", message="job3",
            project_id="test-project", queue_id=queue.queue_id
        )
        
        jobs, total = repository.list_by_queue(queue.queue_id)
        
        assert total == 3
        assert len(jobs) == 3
        job_ids = [j.job_id for j in jobs]
        assert job1.job_id in job_ids
        assert job2.job_id in job_ids
        assert job3.job_id in job_ids

    def test_list_by_queue_with_status_filter(self, repository, queue_repository):
        """Test list_by_queue filters by status correctly."""
        queue = queue_repository.create(project_id="test-project", queue_name="filter-queue")
        
        # Create jobs in PENDING and PROCESSING states
        pending_job = repository.create(
            agent_id="test-agent", agent_dir="/test", message="pending",
            project_id="test-project", queue_id=queue.queue_id
        )
        processing_job = repository.create(
            agent_id="test-agent", agent_dir="/test", message="processing",
            project_id="test-project", queue_id=queue.queue_id
        )
        repository.create(
            agent_id="test-agent", agent_dir="/test", message="another-pending",
            project_id="test-project", queue_id=queue.queue_id
        )
        
        # Start one job to make it PROCESSING
        repository.start_job(processing_job.job_id, "test-instance")
        
        # Filter by PENDING status
        pending_jobs, pending_total = repository.list_by_queue(
            queue.queue_id, statuses=[JobStatus.PENDING.value]
        )
        assert pending_total == 2
        assert len(pending_jobs) == 2
        
        # Filter by PROCESSING status
        processing_jobs, processing_total = repository.list_by_queue(
            queue.queue_id, statuses=[JobStatus.PROCESSING.value]
        )
        assert processing_total == 1
        assert len(processing_jobs) == 1
        assert processing_jobs[0].job_id == processing_job.job_id

    def test_list_by_queue_with_limit(self, repository, queue_repository):
        """Test list_by_queue respects limit and offset parameters."""
        queue = queue_repository.create(project_id="test-project", queue_name="limit-queue")
        
        # Create 5 jobs
        jobs = []
        for i in range(5):
            job = repository.create(
                agent_id="test-agent", agent_dir="/test", message=f"job-{i}",
                project_id="test-project", priority=i + 1, queue_id=queue.queue_id
            )
            jobs.append(job)
        
        # Request only 2 jobs
        result_jobs, total = repository.list_by_queue(queue.queue_id, limit=2)
        
        assert total == 5  # Total count should be 5
        assert len(result_jobs) == 2  # But only 2 returned
        
        # Verify offset works - get jobs at offset 2
        page2, _ = repository.list_by_queue(queue.queue_id, limit=2, offset=2)
        assert len(page2) == 2


class TestRepositoryStartJobAtomic:
    """Tests for atomic job starting."""

    def test_start_job_atomic_success(self, repository, sample_job_data):
        """Test start_job_atomic successfully starts a PENDING job."""
        job = repository.create(**sample_job_data)
        
        assert job.admission_state == AdmissionState.QUEUED.value
        
        started = repository.start_job_atomic(job.job_id, "test-instance")
        
        assert started is not None
        assert started.admission_state == AdmissionState.ACTIVE.value
        assert started.instance_id == "test-instance"
        assert started.started_at is not None

    def test_start_job_atomic_wrong_status(self, repository, sample_job_data):
        """Test start_job_atomic raises InvalidTransitionError for non-PENDING job."""
        job = repository.create(**sample_job_data)
        # Start the job first
        repository.start_job(job.job_id, "instance-1")
        
        # Try to start again - should fail with InvalidTransitionError
        with pytest.raises(InvalidTransitionError) as exc_info:
            repository.start_job_atomic(job.job_id, "instance-2")

        assert exc_info.value.from_status == "active"
        assert exc_info.value.to_status == "processing"

    def test_start_job_atomic_concurrent_safety(self, repository, sample_job_data):
        """Test start_job_atomic ensures only one concurrent start succeeds."""
        job = repository.create(**sample_job_data)
        
        assert job.admission_state == AdmissionState.QUEUED.value
        
        # First call should succeed
        first_started = repository.start_job_atomic(job.job_id, "instance-1")
        assert first_started is not None
        assert first_started.admission_state == AdmissionState.ACTIVE.value
        assert first_started.instance_id == "instance-1"
        
        # Second call should raise InvalidTransitionError (job no longer pending)
        with pytest.raises(InvalidTransitionError) as exc_info:
            repository.start_job_atomic(job.job_id, "instance-2")

        assert exc_info.value.from_status == "active"
        assert exc_info.value.to_status == "processing"

        # Verify only one job was started
        retrieved = repository.get(job.job_id)
        assert retrieved.instance_id == "instance-1"


class TestRepositoryGetByInstanceRecent:
    """Tests for the ``get_by_instance`` and ``get_active_by_instance`` ordering fix.

    Branch: fix/revive-stale-job-lookup. ``get_by_instance`` must return the MOST
    RECENT non-deleted job (ORDER BY created_at DESC) instead of an
    implementation-defined row, so a CANCELLED job left from a prior terminate
    does NOT shadow a fresh PROCESSING job from a revive. ``get_active_by_instance``
    filters further to PENDING/PROCESSING only.
    """

    def test_get_by_instance_returns_most_recent_when_multiple_exist(self, repository, sample_job_data):
        """When 2+ jobs exist for the same instance_id, return the newest."""
        # Job A: started, then cancelled (older).
        job_a = repository.create(**sample_job_data, instance_id="inst-shared")
        repository.start_job(job_a.job_id, "inst-shared")
        repository.cancel_job(job_a.job_id)

        # Job B: created AFTER job A (newer), still PENDING.
        job_b = repository.create(**sample_job_data, instance_id="inst-shared")

        result = repository.get_by_instance("inst-shared")

        assert result is not None
        assert result.job_id == job_b.job_id, (
            f"Expected most recent job_id={job_b.job_id}, got {result.job_id}"
        )

    def test_get_by_instance_orders_by_created_at_desc_across_statuses(
        self, repository, sample_job_data
    ):
        """Mixed statuses (PROCESSING newer, CANCELLED older) → PROCESSING wins."""
        # Create a PENDING job, then PROCESSING it, then CANCELLED it (older).
        old_job = repository.create(**sample_job_data, instance_id="inst-mix")
        repository.start_job(old_job.job_id, "inst-mix")
        repository.cancel_job(old_job.job_id)

        # Create a fresh PENDING job (newer).
        new_job = repository.create(**sample_job_data, instance_id="inst-mix")

        # Most recent wins regardless of status.
        result = repository.get_by_instance("inst-mix")

        assert result.job_id == new_job.job_id
        assert result.admission_state == AdmissionState.QUEUED.value

    def test_get_by_instance_excludes_soft_deleted(self, repository, sample_job_data):
        """Soft-deleted jobs must NOT be returned (deleted_at IS NULL filter)."""
        # Create a soft-deleted job (older).
        deleted_job = repository.create(
            **sample_job_data, instance_id="inst-softdel"
        )
        repository.start_job(deleted_job.job_id, "inst-softdel")
        repository.soft_delete(deleted_job.job_id)

        # Create a non-deleted job (newer).
        kept_job = repository.create(
            **sample_job_data, instance_id="inst-softdel"
        )

        result = repository.get_by_instance("inst-softdel")

        assert result is not None
        assert result.job_id == kept_job.job_id, (
            "Soft-deleted jobs must be filtered out"
        )

    def test_get_by_instance_breaks_ties_with_job_id_when_created_at_identical(
        self, repository, sample_job_data
    ):
        """When 2+ jobs share an identical ``created_at`` (same microsecond),
        the secondary sort on ``job_id`` ASC must deterministically pick the
        lexicographically LOWEST ``job_id`` (the row SQLAlchemy returns from
        ``.order_by(created_at.desc(), job_id).first()``).

        Branch: fix/revive-stale-job-lookup Round 2. Without an explicit
        tie-breaker, two PROCESSING jobs inserted in the same microsecond
        for the same instance would have non-deterministic ordering across
        SQLite/PostgreSQL and across replays — the terminate→revive
        defense-in-depth re-query depends on a stable ordering.

        The fix adds ``.order_by(JobItem.job_id)`` as the secondary key.
        SQL semantics: ``ORDER BY created_at DESC, job_id ASC`` with
        ``LIMIT 1`` returns MAX(created_at), and within ties MIN(job_id)
        — i.e. the lexicographically LOWER ``job_id`` wins.
        """
        # Two jobs for the same instance, both PROCESSING.
        job_a = repository.create(**sample_job_data, instance_id="inst-tie")
        job_b = repository.create(**sample_job_data, instance_id="inst-tie")
        repository.start_job(job_a.job_id, "inst-tie")
        repository.start_job(job_b.job_id, "inst-tie")

        # Force both rows to share an IDENTICAL created_at (same microsecond
        # string). The repository's ``create()`` sets created_at to the
        # current wall clock — two consecutive creates almost always
        # differ at the microsecond granularity on most platforms, so we
        # overwrite via ``update()`` to make the tie deterministic.
        fixed_ts = "2026-06-18T12:00:00.000000+00:00"
        repository.update(job_a.job_id, created_at=fixed_ts)
        repository.update(job_b.job_id, created_at=fixed_ts)

        # Sanity check: timestamps are now identical.
        assert job_a.job_id != job_b.job_id
        refreshed_a = repository.get(job_a.job_id)
        refreshed_b = repository.get(job_b.job_id)
        assert refreshed_a is not None and refreshed_b is not None
        assert refreshed_a.created_at == refreshed_b.created_at == fixed_ts

        # The deterministic winner is the lexicographically LOWER job_id
        # (secondary sort ASC → first row has MIN).
        expected_winner_id = min(job_a.job_id, job_b.job_id)
        expected_loser_id = max(job_a.job_id, job_b.job_id)

        # get_by_instance: tie-break on job_id ASC.
        result = repository.get_by_instance("inst-tie")
        assert result is not None
        assert result.job_id == expected_winner_id, (
            f"Tie-break mismatch: expected job_id={expected_winner_id} "
            f"(lexicographically lower of [{job_a.job_id}, {job_b.job_id}]), "
            f"got {result.job_id}"
        )
        assert result.job_id != expected_loser_id

        # get_active_by_instance must apply the SAME tie-break — both jobs
        # are PROCESSING, so the same row wins.
        active_result = repository.get_active_by_instance("inst-tie")
        assert active_result is not None
        assert active_result.job_id == expected_winner_id, (
            f"get_active_by_instance tie-break mismatch: expected "
            f"job_id={expected_winner_id}, got {active_result.job_id}"
        )


class TestRepositoryGetActiveByInstance:
    """Tests for the new ``get_active_by_instance`` method (Fix 1).

    Returns the most recent non-deleted job in PENDING or PROCESSING. Terminal
    states (COMPLETED, FAILED, CANCELLED, DEAD_LETTER) and soft-deleted rows are
    excluded.
    """

    def test_get_active_returns_pending_job(self, repository, sample_job_data):
        """A PENDING job (newest) is returned by get_active_by_instance."""
        pending_job = repository.create(
            **sample_job_data, instance_id="inst-pending"
        )
        # pending_job.status is PENDING by default.

        result = repository.get_active_by_instance("inst-pending")

        assert result is not None
        assert result.job_id == pending_job.job_id
        assert result.admission_state == AdmissionState.QUEUED.value

    def test_get_active_returns_processing_job(self, repository, sample_job_data):
        """A PROCESSING job is returned by get_active_by_instance."""
        job = repository.create(**sample_job_data, instance_id="inst-proc")
        repository.start_job(job.job_id, "inst-proc")

        result = repository.get_active_by_instance("inst-proc")

        assert result is not None
        assert result.job_id == job.job_id
        assert result.admission_state == AdmissionState.ACTIVE.value

    def test_get_active_returns_only_active_across_mixed_statuses(
        self, repository, sample_job_data
    ):
        """When COMPLETED, FAILED, CANCELLED, PENDING, PROCESSING all exist,
        only the most recent ACTIVE (PENDING/PROCESSING) job is returned.
        """
        # Terminal jobs (older).
        completed = repository.create(
            **sample_job_data, instance_id="inst-multi"
        )
        repository.start_job(completed.job_id, "inst-multi")
        repository.complete_job(completed.job_id, "done")

        failed = repository.create(
            **sample_job_data, instance_id="inst-multi"
        )
        repository.start_job(failed.job_id, "inst-multi")
        repository.fail_job(failed.job_id, "oops")

        cancelled = repository.create(
            **sample_job_data, instance_id="inst-multi"
        )
        repository.start_job(cancelled.job_id, "inst-multi")
        repository.cancel_job(cancelled.job_id)

        # Active jobs (newer).
        processing = repository.create(
            **sample_job_data, instance_id="inst-multi"
        )
        repository.start_job(processing.job_id, "inst-multi")

        pending = repository.create(
            **sample_job_data, instance_id="inst-multi"
        )  # newest

        result = repository.get_active_by_instance("inst-multi")

        assert result is not None
        assert result.job_id == pending.job_id, (
            f"Expected newest active={pending.job_id}, got {result.job_id}"
        )
        assert result.admission_state == AdmissionState.QUEUED.value

    def test_get_active_returns_none_when_no_active_exists(
        self, repository, sample_job_data
    ):
        """All jobs in terminal states → get_active_by_instance returns None."""
        completed = repository.create(
            **sample_job_data, instance_id="inst-allterm"
        )
        repository.start_job(completed.job_id, "inst-allterm")
        repository.complete_job(completed.job_id)

        cancelled = repository.create(
            **sample_job_data, instance_id="inst-allterm"
        )
        repository.start_job(cancelled.job_id, "inst-allterm")
        repository.cancel_job(cancelled.job_id)

        failed = repository.create(
            **sample_job_data, instance_id="inst-allterm"
        )
        repository.start_job(failed.job_id, "inst-allterm")
        repository.fail_job(failed.job_id, "x")

        result = repository.get_active_by_instance("inst-allterm")

        assert result is None, (
            "No active jobs exist; expected None"
        )

    def test_get_active_returns_none_for_unknown_instance(self, repository):
        """Unknown instance_id → None (no rows match)."""
        assert repository.get_active_by_instance("nope") is None

    def test_get_active_excludes_soft_deleted(self, repository, sample_job_data):
        """A soft-deleted PROCESSING job is NOT returned."""
        job = repository.create(**sample_job_data, instance_id="inst-softproc")
        repository.start_job(job.job_id, "inst-softproc")
        repository.soft_delete(job.job_id)

        assert repository.get_active_by_instance("inst-softproc") is None

    def test_get_active_chooses_active_over_stale_when_same_instance(
        self, repository, sample_job_data
    ):
        """Stale terminal job + fresh active job → active wins.

        This is the exact pattern the fix protects against: a CANCELLED job
        from a prior terminate (older created_at) plus a fresh PROCESSING job
        from a revive (newer created_at). ``get_active_by_instance`` must
        return the active one.
        """
        # Stale CANCELLED job (older).
        stale = repository.create(**sample_job_data, instance_id="inst-revive")
        repository.start_job(stale.job_id, "inst-revive")
        repository.cancel_job(stale.job_id)

        # Fresh PROCESSING job from revive (newer).
        fresh = repository.create(**sample_job_data, instance_id="inst-revive")
        repository.start_job(fresh.job_id, "inst-revive")

        result = repository.get_active_by_instance("inst-revive")

        assert result is not None
        assert result.job_id == fresh.job_id
        assert result.admission_state == AdmissionState.ACTIVE.value


class TestRepositoryHardDeleteByProject:
    """Tests for hard_delete_by_project method."""

    def test_hard_delete_by_project_removes_jobs(self, repository, sample_job_data):
        """Test hard_delete_by_project removes all jobs for specified project."""
        # Create jobs for multiple projects
        job1 = repository.create(**sample_job_data)  # test-project
        job2 = repository.create(**sample_job_data)  # test-project
        other_job = repository.create(
            **{**sample_job_data, "project_id": "other-project"}
        )
        
        # Hard delete all jobs for test-project
        deleted_count = repository.hard_delete_by_project("test-project")
        
        assert deleted_count == 2
        assert repository.get(job1.job_id) is None
        assert repository.get(job2.job_id) is None
        # Other project's job should remain
        assert repository.get(other_job.job_id) is not None

    def test_hard_delete_by_project_returns_count(self, repository, sample_job_data):
        """Test hard_delete_by_project returns the number of deleted jobs."""
        # Create multiple jobs for same project
        repository.create(**sample_job_data)  # job 1
        repository.create(**sample_job_data)  # job 2
        repository.create(**sample_job_data)  # job 3
        
        deleted_count = repository.hard_delete_by_project("test-project")
        
        assert deleted_count == 3

    def test_hard_delete_by_project_other_projects_unaffected(self, repository, sample_job_data):
        """Test hard_delete_by_project does not affect jobs from other projects."""
        # Create jobs for different projects
        project_a_job = repository.create(
            **{**sample_job_data, "project_id": "project-a"}
        )
        project_b_job = repository.create(
            **{**sample_job_data, "project_id": "project-b"}
        )
        project_c_job = repository.create(
            **{**sample_job_data, "project_id": "project-c"}
        )
        
        # Hard delete only project-a's jobs
        deleted_count = repository.hard_delete_by_project("project-a")
        
        assert deleted_count == 1
        assert repository.get(project_a_job.job_id) is None
        # Other projects' jobs should be unaffected
        assert repository.get(project_b_job.job_id) is not None
        assert repository.get(project_c_job.job_id) is not None
