"""Tests for Phase 1 JobRepository instance-based methods.

Tests for:
- find_processing_message_jobs_by_instance()
- find_jobs_by_instance()
- create() with job_type and instance_id parameters
"""

import pytest

from daemon.repositories.job_queue import JobRepository
from daemon.repositories.job_queue.models import JobStatus


class TestCreateWithJobTypeAndInstanceId:
    """Tests for JobRepository.create() with job_type and instance_id parameters."""

    def test_create_job_with_job_type_message(self, repository, sample_job_data):
        """Test creating a job with job_type='message' stores it correctly."""
        job = repository.create(**sample_job_data, job_type="message")

        assert job.job_type == "message"

    def test_create_job_with_job_type_task_default(self, repository, sample_job_data):
        """Test that job_type defaults to 'task' when not specified."""
        job = repository.create(**sample_job_data)

        assert job.job_type == "task"

    def test_create_job_with_instance_id(self, repository, sample_job_data):
        """Test creating a job with instance_id stores it in the instance_id column."""
        instance_uuid = "550e8400-e29b-41d4-a716-446655440000"
        job = repository.create(**sample_job_data, instance_id=instance_uuid)

        assert job.instance_id == instance_uuid

    def test_create_job_with_instance_id_none_by_default(self, repository, sample_job_data):
        """Test that instance_id is None when not specified."""
        job = repository.create(**sample_job_data)

        assert job.instance_id is None

    def test_create_job_with_both_job_type_and_instance_id(self, repository, sample_job_data):
        """Test creating a job with both job_type and instance_id."""
        instance_uuid = "550e8400-e29b-41d4-a716-446655440001"
        job = repository.create(
            **sample_job_data,
            job_type="message",
            instance_id=instance_uuid,
        )

        assert job.job_type == "message"
        assert job.instance_id == instance_uuid


class TestFindProcessingMessageJobsByInstance:
    """Tests for find_processing_message_jobs_by_instance() method."""

    def test_returns_processing_message_jobs_for_instance(
        self, repository, sample_job_data
    ):
        """Test it returns jobs with status='processing', job_type='message', matching instance_id."""
        instance_id = "test-instance-123"

        # Create and start a message job for this instance
        job = repository.create(**sample_job_data, job_type="message")
        repository.start_job(job.job_id, instance_id)

        # Create and start a message job for another instance
        other_job = repository.create(**sample_job_data, job_type="message")
        repository.start_job(other_job.job_id, "other-instance")

        results = repository.find_processing_message_jobs_by_instance(instance_id)

        assert len(results) == 1
        assert results[0].job_id == job.job_id
        assert results[0].status == JobStatus.PROCESSING.value
        assert results[0].job_type == "message"
        assert results[0].instance_id == instance_id

    def test_excludes_pending_jobs(self, repository, sample_job_data):
        """Test it excludes jobs that are still PENDING."""
        instance_id = "pending-test-instance"

        # Create a job and start it (will be PROCESSING)
        processing_job = repository.create(**sample_job_data, job_type="message")
        repository.start_job(processing_job.job_id, instance_id)

        # Create another job but DON'T start it (stays PENDING)
        pending_job = repository.create(**sample_job_data, job_type="message")

        results = repository.find_processing_message_jobs_by_instance(instance_id)

        # Should only find the PROCESSING one, not the PENDING one
        assert len(results) == 1
        assert results[0].job_id == processing_job.job_id
        assert results[0].status == JobStatus.PROCESSING.value

    def test_excludes_task_jobs(self, repository, sample_job_data):
        """Test it excludes jobs with job_type='task'."""
        instance_id = "task-test-instance"

        # Create a message job (should be found)
        message_job = repository.create(**sample_job_data, job_type="message")
        repository.start_job(message_job.job_id, instance_id)

        # Create a task job (should be excluded)
        task_job = repository.create(**sample_job_data, job_type="task")
        repository.start_job(task_job.job_id, instance_id)

        results = repository.find_processing_message_jobs_by_instance(instance_id)

        assert len(results) == 1
        assert results[0].job_id == message_job.job_id
        assert results[0].job_type == "message"

    def test_excludes_wrong_instance_id(self, repository, sample_job_data):
        """Test it excludes jobs for different instance_id."""
        target_instance = "target-instance"
        other_instance = "other-instance"

        # Create job for target instance (should be found)
        target_job = repository.create(**sample_job_data, job_type="message")
        repository.start_job(target_job.job_id, target_instance)

        # Create job for other instance (should be excluded)
        other_job = repository.create(**sample_job_data, job_type="message")
        repository.start_job(other_job.job_id, other_instance)

        results = repository.find_processing_message_jobs_by_instance(target_instance)

        assert len(results) == 1
        assert results[0].job_id == target_job.job_id
        assert results[0].instance_id == target_instance

    def test_excludes_deleted_jobs(self, repository, sample_job_data):
        """Test it excludes soft-deleted jobs."""
        instance_id = "deleted-test-instance"

        # Create and start a job
        job = repository.create(**sample_job_data, job_type="message")
        repository.start_job(job.job_id, instance_id)

        # Soft delete it
        repository.soft_delete(job.job_id)

        results = repository.find_processing_message_jobs_by_instance(instance_id)

        assert len(results) == 0

    def test_returns_multiple_processing_jobs(self, repository, sample_job_data):
        """Test it returns all matching processing message jobs for an instance."""
        instance_id = "multi-job-instance"

        # Create multiple message jobs for this instance
        job1 = repository.create(**sample_job_data, job_type="message")
        job2 = repository.create(**sample_job_data, job_type="message")
        job3 = repository.create(**sample_job_data, job_type="message")

        repository.start_job(job1.job_id, instance_id)
        repository.start_job(job2.job_id, instance_id)
        repository.start_job(job3.job_id, instance_id)

        results = repository.find_processing_message_jobs_by_instance(instance_id)

        assert len(results) == 3
        job_ids = {r.job_id for r in results}
        assert job_ids == {job1.job_id, job2.job_id, job3.job_id}

    def test_returns_empty_list_when_no_match(self, repository, sample_job_data):
        """Test it returns empty list when no matching jobs exist."""
        results = repository.find_processing_message_jobs_by_instance("nonexistent-instance")
        assert results == []


class TestFindJobsByInstance:
    """Tests for find_jobs_by_instance() method."""

    def test_returns_pending_and_processing_jobs(self, repository, sample_job_data):
        """Test it returns jobs with status PENDING or PROCESSING for the instance."""
        instance_id = "mixed-status-instance"

        # Create a pending job
        pending_job = repository.create(**sample_job_data)
        # Create a processing job
        processing_job = repository.create(**sample_job_data)
        repository.start_job(processing_job.job_id, instance_id)

        results = repository.find_jobs_by_instance(instance_id)

        # Should find the processing job (instance_id matches)
        assert len(results) >= 1
        job_ids = {r.job_id for r in results}
        assert processing_job.job_id in job_ids

    def test_filters_by_job_type_when_provided(self, repository, sample_job_data):
        """Test it filters by job_type when the parameter is provided."""
        instance_id = "typed-instance"

        # Create message job
        message_job = repository.create(**sample_job_data, job_type="message")
        repository.start_job(message_job.job_id, instance_id)

        # Create task job
        task_job = repository.create(**sample_job_data, job_type="task")
        repository.start_job(task_job.job_id, instance_id)

        # Filter by message type
        results = repository.find_jobs_by_instance(instance_id, job_type="message")

        assert len(results) == 1
        assert results[0].job_id == message_job.job_id
        assert results[0].job_type == "message"

    def test_filters_by_task_job_type(self, repository, sample_job_data):
        """Test filtering by job_type='task'."""
        instance_id = "task-filter-instance"

        message_job = repository.create(**sample_job_data, job_type="message")
        task_job = repository.create(**sample_job_data, job_type="task")

        repository.start_job(message_job.job_id, instance_id)
        repository.start_job(task_job.job_id, instance_id)

        results = repository.find_jobs_by_instance(instance_id, job_type="task")

        assert len(results) == 1
        assert results[0].job_id == task_job.job_id
        assert results[0].job_type == "task"

    def test_excludes_deleted_jobs(self, repository, sample_job_data):
        """Test it excludes soft-deleted jobs."""
        instance_id = "deleted-instance"

        # Create and start a job
        job = repository.create(**sample_job_data)
        repository.start_job(job.job_id, instance_id)

        # Soft delete it
        repository.soft_delete(job.job_id)

        results = repository.find_jobs_by_instance(instance_id)

        assert len(results) == 0

    def test_excludes_completed_jobs(self, repository, sample_job_data):
        """Test it excludes completed jobs."""
        instance_id = "completed-instance"

        # Create and complete a job
        job = repository.create(**sample_job_data)
        started_job = repository.start_job(job.job_id, instance_id)
        repository.complete_job(started_job.job_id)

        results = repository.find_jobs_by_instance(instance_id)

        assert len(results) == 0

    def test_excludes_failed_jobs(self, repository, sample_job_data):
        """Test it excludes failed jobs."""
        instance_id = "failed-instance"

        job = repository.create(**sample_job_data)
        started_job = repository.start_job(job.job_id, instance_id)
        repository.fail_job(started_job.job_id, "test error")

        results = repository.find_jobs_by_instance(instance_id)

        assert len(results) == 0

    def test_excludes_cancelled_jobs(self, repository, sample_job_data):
        """Test it excludes cancelled jobs."""
        instance_id = "cancelled-instance"

        job = repository.create(**sample_job_data)
        started_job = repository.start_job(job.job_id, instance_id)
        repository.cancel_job(started_job.job_id)

        results = repository.find_jobs_by_instance(instance_id)

        assert len(results) == 0

    def test_excludes_wrong_instance_id(self, repository, sample_job_data):
        """Test it excludes jobs for different instance_id."""
        target_instance = "target"
        other_instance = "other"

        target_job = repository.create(**sample_job_data)
        repository.start_job(target_job.job_id, target_instance)

        other_job = repository.create(**sample_job_data)
        repository.start_job(other_job.job_id, other_instance)

        results = repository.find_jobs_by_instance(target_instance)

        assert len(results) == 1
        assert results[0].job_id == target_job.job_id

    def test_returns_empty_list_when_no_match(self, repository, sample_job_data):
        """Test it returns empty list when no matching jobs exist."""
        results = repository.find_jobs_by_instance("nonexistent-instance")
        assert results == []

    def test_returns_all_active_jobs_without_job_type_filter(
        self, repository, sample_job_data
    ):
        """Test it returns both message and task jobs when no job_type filter."""
        instance_id = "no-filter-instance"

        message_job = repository.create(**sample_job_data, job_type="message")
        task_job = repository.create(**sample_job_data, job_type="task")

        repository.start_job(message_job.job_id, instance_id)
        repository.start_job(task_job.job_id, instance_id)

        results = repository.find_jobs_by_instance(instance_id)

        assert len(results) == 2
        job_ids = {r.job_id for r in results}
        assert job_ids == {message_job.job_id, task_job.job_id}
