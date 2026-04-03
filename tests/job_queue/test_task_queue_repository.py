"""Tests for JobRepository.

This module tests the SQLModel-based repository for job queue CRUD operations.
"""

import pytest
import time

from daemon.repositories.job_queue import JobRepository
from daemon.repositories.job_queue.models import JobStatus, JobItem


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
        assert job.status == JobStatus.PENDING.value
        assert job.job_metadata == sample_job_data["job_metadata"]

    def test_create_job_without_project(self, repository, sample_job_data_no_project):
        """Test creating a job without project_id."""
        job = repository.create(**sample_job_data_no_project)
        
        assert job.job_id is not None
        assert job.project_id is None
        assert job.status == JobStatus.PENDING.value

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
        assert job.status == JobStatus.PENDING.value
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
        
        pending_jobs, total = repository.list(status=JobStatus.PENDING.value)
        processing_jobs, _ = repository.list(status=JobStatus.PROCESSING.value)
        
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
        assert all(j.status == JobStatus.PENDING.value for j in pending)

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

    def test_update_invalid_status(self, repository, sample_job_data):
        """Test updating with invalid status raises ValueError."""
        job = repository.create(**sample_job_data)
        
        with pytest.raises(ValueError) as exc_info:
            repository.update(job.job_id, status="invalid-status")
        
        assert "Invalid status" in str(exc_info.value)


class TestRepositoryJobLifecycle:
    """Tests for job lifecycle transitions."""

    def test_start_pending_job(self, repository, sample_job_data):
        """Test starting a pending job."""
        job = repository.create(**sample_job_data)
        
        started = repository.start_job(job.job_id, "instance-1")
        
        assert started is not None
        assert started.status == JobStatus.PROCESSING.value
        assert started.instance_id == "instance-1"
        assert started.started_at is not None

    def test_start_already_started_job_raises(self, repository, sample_job_data):
        """Test starting an already started job raises ValueError."""
        job = repository.create(**sample_job_data)
        repository.start_job(job.job_id, "instance-1")
        
        with pytest.raises(ValueError) as exc_info:
            repository.start_job(job.job_id, "instance-2")
        
        assert "Cannot start job" in str(exc_info.value)
        assert "processing" in str(exc_info.value)

    def test_start_completed_job_raises(self, repository, sample_job_data):
        """Test starting a completed job raises ValueError."""
        job = repository.create(**sample_job_data)
        started = repository.start_job(job.job_id, "instance-1")
        repository.complete_job(started.job_id)
        
        with pytest.raises(ValueError) as exc_info:
            repository.start_job(job.job_id, "instance-2")
        
        assert "Cannot start job" in str(exc_info.value)
        assert "completed" in str(exc_info.value)

    def test_complete_processing_job(self, repository, sample_job_data):
        """Test completing a processing job."""
        job = repository.create(**sample_job_data)
        started = repository.start_job(job.job_id, "instance-1")
        
        completed = repository.complete_job(
            started.job_id,
            result_summary="Job completed successfully"
        )
        
        assert completed is not None
        assert completed.status == JobStatus.COMPLETED.value
        assert completed.completed_at is not None
        assert completed.result_summary == "Job completed successfully"

    def test_complete_pending_job_raises(self, repository, sample_job_data):
        """Test completing a pending job raises ValueError."""
        job = repository.create(**sample_job_data)
        
        with pytest.raises(ValueError) as exc_info:
            repository.complete_job(job.job_id)
        
        assert "Cannot complete job" in str(exc_info.value)
        assert "pending" in str(exc_info.value)

    def test_fail_processing_job(self, repository, sample_job_data):
        """Test failing a processing job."""
        job = repository.create(**sample_job_data)
        started = repository.start_job(job.job_id, "instance-1")
        
        failed = repository.fail_job(
            started.job_id,
            error_message="Something went wrong"
        )
        
        assert failed is not None
        assert failed.status == JobStatus.FAILED.value
        assert failed.completed_at is not None
        assert failed.error_message == "Something went wrong"

    def test_fail_pending_job_raises(self, repository, sample_job_data):
        """Test failing a pending job raises ValueError."""
        job = repository.create(**sample_job_data)
        
        with pytest.raises(ValueError) as exc_info:
            repository.fail_job(job.job_id, "Error")
        
        assert "Cannot fail job" in str(exc_info.value)
        assert "pending" in str(exc_info.value)

    def test_cancel_pending_job(self, repository, sample_job_data):
        """Test cancelling a pending job."""
        job = repository.create(**sample_job_data)
        
        cancelled = repository.cancel_job(job.job_id)
        
        assert cancelled is not None
        assert cancelled.status == JobStatus.CANCELLED.value
        assert cancelled.cancelled_at is not None

    def test_cancel_processing_job_raises(self, repository, sample_job_data):
        """Test cancelling a processing job raises ValueError."""
        job = repository.create(**sample_job_data)
        repository.start_job(job.job_id, "instance-1")
        
        with pytest.raises(ValueError) as exc_info:
            repository.cancel_job(job.job_id)
        
        assert "Cannot cancel job" in str(exc_info.value)
        assert "processing" in str(exc_info.value)


class TestRepositoryDelete:
    """Tests for job deletion."""

    def test_delete_existing_job(self, repository, sample_job_data):
        """Test deleting an existing job."""
        job = repository.create(**sample_job_data)
        
        result = repository.delete(job.job_id)
        
        assert result["deleted"] is True
        assert result["job_id"] == job.job_id
        
        # Verify job is gone
        assert repository.get(job.job_id) is None

    def test_delete_nonexistent_job(self, repository):
        """Test deleting non-existent job returns error."""
        result = repository.delete("nonexistent-id")
        
        assert result["deleted"] is False
        assert "error" in result

    def test_delete_completed_jobs(self, repository, sample_job_data):
        """Test deleting all completed jobs."""
        # Create and complete some jobs
        job1 = repository.create(**sample_job_data)
        job2 = repository.create(**sample_job_data)
        job3 = repository.create(**sample_job_data)
        
        repository.start_job(job1.job_id, "i1")
        repository.start_job(job2.job_id, "i2")
        repository.complete_job(job1.job_id)
        repository.complete_job(job2.job_id)
        # job3 remains pending
        
        deleted_count = repository.delete_completed()
        
        assert deleted_count == 2
        assert repository.get(job1.job_id) is None
        assert repository.get(job2.job_id) is None
        assert repository.get(job3.job_id) is not None

    def test_delete_by_project(self, repository, sample_job_data):
        """Test deleting all jobs for a project."""
        # Create jobs for multiple projects
        job1 = repository.create(**sample_job_data)  # test-project
        job2 = repository.create(**sample_job_data)  # test-project
        job3 = repository.create(
            **{**sample_job_data, "project_id": "other"}
        )
        
        deleted_count = repository.delete_by_project("test-project")
        
        assert deleted_count == 2
        assert repository.get(job1.job_id) is None
        assert repository.get(job2.job_id) is None
        assert repository.get(job3.job_id) is not None

    def test_delete_completed_when_none(self, repository):
        """Test delete_completed when no completed jobs exist."""
        count = repository.delete_completed()
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
            status=JobStatus.PENDING.value,
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
        assert job.status == JobStatus.PENDING.value
        
        # Start job
        started = repository.start_job(job.job_id, "instance-1")
        assert started.status == JobStatus.PROCESSING.value
        
        # Complete job
        completed = repository.complete_job(job.job_id, "Done")
        assert completed.status == JobStatus.COMPLETED.value
        
        # Verify final state persists
        final = repository.get(job.job_id)
        assert final.status == JobStatus.COMPLETED.value
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
