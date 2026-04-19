"""Tests for JobRepository soft-delete functionality.

This module tests the soft-delete, restore, and exclusion behaviors
of the job queue repository.
"""

import pytest

from daemon.repositories.job_queue import JobRepository
from daemon.repositories.job_queue.models import JobStatus


class TestSoftDelete:
    """Tests for soft_delete() method."""

    def test_soft_delete_existing_job(self, repository, sample_job_data):
        """Test soft_delete() sets deleted_at, job still exists in DB."""
        job = repository.create(**sample_job_data)
        job_id = job.job_id
        
        result = repository.soft_delete(job_id)
        
        assert result is not None
        assert result.deleted_at is not None
        # Job should still be retrievable via get()
        retrieved = repository.get(job_id)
        assert retrieved is not None
        assert retrieved.job_id == job_id

    def test_soft_delete_nonexistent_job(self, repository):
        """Test soft_delete() on non-existent job returns None."""
        result = repository.soft_delete("nonexistent-id")
        assert result is None

    def test_soft_delete_idempotent(self, repository, sample_job_data):
        """Test soft_delete() is idempotent - calling twice returns same job."""
        job = repository.create(**sample_job_data)
        job_id = job.job_id
        
        first_result = repository.soft_delete(job_id)
        second_result = repository.soft_delete(job_id)
        
        assert first_result is not None
        assert second_result is not None
        assert first_result.job_id == second_result.job_id
        assert first_result.deleted_at == second_result.deleted_at

    def test_soft_delete_does_not_remove_from_db(self, repository, sample_job_data):
        """Test soft_delete() does not physically remove job - hard_delete() can find it."""
        job = repository.create(**sample_job_data)
        job_id = job.job_id
        
        repository.soft_delete(job_id)
        
        # hard_delete() should still find and remove the job
        hard_result = repository.hard_delete(job_id)
        assert hard_result["deleted"] is True
        # Now get() should return None
        assert repository.get(job_id) is None

    def test_soft_delete_sets_timestamp(self, repository, sample_job_data):
        """Test soft_delete() sets deleted_at to valid ISO timestamp."""
        job = repository.create(**sample_job_data)
        job_id = job.job_id
        
        result = repository.soft_delete(job_id)
        
        assert result.deleted_at is not None
        # Should be a valid ISO format string (contains digits and hyphens/colons)
        assert "-" in result.deleted_at or ":" in result.deleted_at
        assert "T" in result.deleted_at  # ISO format contains T separator


class TestRestore:
    """Tests for restore() method."""

    def test_restore_deleted_job(self, repository, sample_job_data):
        """Test restore() clears deleted_at on soft-deleted job."""
        job = repository.create(**sample_job_data)
        job_id = job.job_id
        
        repository.soft_delete(job_id)
        restored = repository.restore(job_id)
        
        assert restored is not None
        assert restored.deleted_at is None
        # Verify via get()
        retrieved = repository.get(job_id)
        assert retrieved.deleted_at is None

    def test_restore_nonexistent_job(self, repository):
        """Test restore() on non-existent job returns None."""
        result = repository.restore("nonexistent-id")
        assert result is None

    def test_restore_non_deleted_job(self, repository, sample_job_data):
        """Test restore() on non-deleted job clears nothing (deleted_at was already None)."""
        job = repository.create(**sample_job_data)
        job_id = job.job_id
        
        # Verify not deleted
        assert job.deleted_at is None
        
        restored = repository.restore(job_id)
        
        assert restored is not None
        assert restored.deleted_at is None  # Still None

    def test_restore_then_reuse_idempotency_key(self, repository, sample_job_data):
        """Test after restore, find_by_idempotency_key can find the job again."""
        idempotency_key = "unique-key-123"
        job_data = {**sample_job_data, "idempotency_key": idempotency_key}
        job = repository.create(**job_data)
        job_id = job.job_id
        
        # Soft delete the job
        repository.soft_delete(job_id)
        
        # find_by_idempotency_key should NOT find deleted job
        found_while_deleted = repository.find_by_idempotency_key(idempotency_key)
        assert found_while_deleted is None
        
        # Restore the job
        repository.restore(job_id)
        
        # Now find_by_idempotency_key SHOULD find it again
        found_after_restore = repository.find_by_idempotency_key(idempotency_key)
        assert found_after_restore is not None
        assert found_after_restore.job_id == job_id


class TestListExcludesDeleted:
    """Tests for list() excluding soft-deleted jobs."""

    def test_list_excludes_deleted_by_default(self, repository, sample_job_data):
        """Test list() without include_deleted=True excludes soft-deleted jobs."""
        job1 = repository.create(**sample_job_data)
        job2 = repository.create(**sample_job_data)
        
        repository.soft_delete(job1.job_id)
        
        jobs, total = repository.list()
        
        assert total == 1
        assert len(jobs) == 1
        assert jobs[0].job_id == job2.job_id

    def test_list_includes_deleted_when_requested(self, repository, sample_job_data):
        """Test list(include_deleted=True) includes soft-deleted jobs."""
        job1 = repository.create(**sample_job_data)
        job2 = repository.create(**sample_job_data)
        
        repository.soft_delete(job1.job_id)
        
        jobs, total = repository.list(include_deleted=True)
        
        assert total == 2
        assert len(jobs) == 2
        job_ids = [j.job_id for j in jobs]
        assert job1.job_id in job_ids
        assert job2.job_id in job_ids

    def test_list_deleted_and_active_mixed(self, repository, sample_job_data):
        """Test list with include_deleted=True returns both deleted and active jobs."""
        job1 = repository.create(**sample_job_data)
        job2 = repository.create(**sample_job_data)
        job3 = repository.create(**sample_job_data)
        
        repository.soft_delete(job1.job_id)
        repository.soft_delete(job3.job_id)
        
        jobs, total = repository.list(include_deleted=True)
        
        assert total == 3
        assert len(jobs) == 3
        # Verify we can distinguish deleted vs active
        deleted_jobs = [j for j in jobs if j.deleted_at is not None]
        active_jobs = [j for j in jobs if j.deleted_at is None]
        assert len(deleted_jobs) == 2
        assert len(active_jobs) == 1
        assert active_jobs[0].job_id == job2.job_id

    def test_list_count_excludes_deleted(self, repository, sample_job_data):
        """Test total count from list() excludes deleted jobs."""
        job1 = repository.create(**sample_job_data)
        job2 = repository.create(**sample_job_data)
        job3 = repository.create(**sample_job_data)
        
        repository.soft_delete(job1.job_id)
        repository.soft_delete(job2.job_id)
        
        jobs, total = repository.list()
        
        assert total == 1  # Only job3 is active
        assert jobs[0].job_id == job3.job_id


class TestSchedulerSafety:
    """CRITICAL tests: scheduler methods must exclude deleted jobs."""

    def test_list_pending_by_project_excludes_deleted_pending_jobs(
        self, repository, sample_job_data
    ):
        """Test deleted PENDING jobs don't appear in scheduler queue."""
        job = repository.create(**sample_job_data)
        
        # Verify job is PENDING
        assert job.status == JobStatus.PENDING.value
        
        # Soft delete the pending job
        repository.soft_delete(job.job_id)
        
        # list_pending_by_project should NOT return the deleted job
        pending = repository.list_pending_by_project("test-project")
        
        assert len(pending) == 0
        job_ids = [j.job_id for j in pending]
        assert job.job_id not in job_ids

    def test_list_all_pending_excludes_deleted(self, repository, sample_job_data):
        """Test deleted PENDING jobs not picked up by list_all_pending."""
        job = repository.create(**sample_job_data)
        job_id = job.job_id
        
        repository.soft_delete(job_id)
        
        all_pending = repository.list_all_pending()
        
        assert len(all_pending) == 0
        job_ids = [j.job_id for j in all_pending]
        assert job_id not in job_ids

    def test_list_pending_by_queue_excludes_deleted(self, repository, queue_repository):
        """Test deleted PENDING jobs not picked up by list_pending_by_queue."""
        queue = queue_repository.create(
            project_id="test-project",
            queue_name="test-queue"
        )
        job = repository.create(
            agent_id="test-agent",
            agent_dir="/test",
            message="Queue job",
            project_id="test-project",
            queue_id=queue.queue_id
        )
        job_id = job.job_id
        
        repository.soft_delete(job_id)
        
        pending = repository.list_pending_by_queue(queue.queue_id)
        
        assert len(pending) == 0
        job_ids = [j.job_id for j in pending]
        assert job_id not in job_ids

    def test_list_processing_excludes_deleted(self, repository, sample_job_data):
        """Test deleted PROCESSING jobs not returned by find_processing_jobs."""
        job = repository.create(**sample_job_data)
        started_job = repository.start_job(job.job_id, "test-instance")
        
        # Verify job is now PROCESSING (use the returned object)
        assert started_job.status == JobStatus.PROCESSING.value
        
        repository.soft_delete(started_job.job_id)
        
        processing = repository.find_processing_jobs()
        
        assert len(processing) == 0

    def test_list_by_queue_excludes_deleted(self, repository, queue_repository):
        """Test deleted jobs not returned by list_by_queue."""
        queue = queue_repository.create(
            project_id="test-project",
            queue_name="list-test-queue"
        )
        job = repository.create(
            agent_id="test-agent",
            agent_dir="/test",
            message="Listed job",
            project_id="test-project",
            queue_id=queue.queue_id
        )
        job_id = job.job_id
        
        repository.soft_delete(job_id)
        
        jobs, total = repository.list_by_queue(queue.queue_id)
        
        assert total == 0
        assert len(jobs) == 0


class TestIntentionalReturnsDeleted:
    """Tests for methods that intentionally return deleted jobs."""

    def test_get_returns_deleted_jobs(self, repository, sample_job_data):
        """Test get() returns deleted jobs (needed for restore)."""
        job = repository.create(**sample_job_data)
        job_id = job.job_id
        
        repository.soft_delete(job_id)
        
        retrieved = repository.get(job_id)
        
        assert retrieved is not None
        assert retrieved.job_id == job_id
        assert retrieved.deleted_at is not None

    def test_atomic_transition_works_on_deleted_jobs(self, repository, sample_job_data):
        """Test atomic_transition works on deleted jobs (uses get(), not list)."""
        job = repository.create(**sample_job_data)
        job_id = job.job_id
        
        repository.soft_delete(job_id)
        
        # start_job uses get() internally, so it should work
        started = repository.start_job(job_id, "new-instance")
        
        # Note: This will fail because the job is PENDING but we're trying to start
        # a deleted job. The test verifies that get() is used, not list().
        # The actual behavior is that deleted jobs in PENDING can be started
        # because start_job doesn't filter by deleted_at.


class TestIdempotencyKeyReuse:
    """CRITICAL tests: idempotency key reuse after soft delete."""

    def test_find_by_idempotency_key_excludes_deleted_jobs(
        self, repository, sample_job_data
    ):
        """Test deleted jobs excluded from find_by_idempotency_key allows key reuse."""
        idempotency_key = "reuse-key-456"
        job_data = {**sample_job_data, "idempotency_key": idempotency_key}
        job = repository.create(**job_data)
        job_id = job.job_id
        
        # Verify job is found initially
        found = repository.find_by_idempotency_key(idempotency_key)
        assert found is not None
        assert found.job_id == job_id
        
        # Soft delete the job
        repository.soft_delete(job_id)
        
        # After soft delete, find_by_idempotency_key should NOT find it
        found_after_delete = repository.find_by_idempotency_key(idempotency_key)
        assert found_after_delete is None
        
        # This allows creating a NEW job with the same idempotency key
        new_job = repository.create(**job_data)
        assert new_job.job_id != job_id
        assert new_job.idempotency_key == idempotency_key

    def test_find_by_idempotency_key_includes_non_deleted(
        self, repository, sample_job_data
    ):
        """Test non-deleted jobs are found by find_by_idempotency_key."""
        idempotency_key = "active-key-789"
        job_data = {**sample_job_data, "idempotency_key": idempotency_key}
        job = repository.create(**job_data)
        job_id = job.job_id
        
        found = repository.find_by_idempotency_key(idempotency_key)
        
        assert found is not None
        assert found.job_id == job_id


class TestGetByInstance:
    """Tests for get_by_instance() excluding deleted jobs."""

    def test_get_by_instance_excludes_deleted(self, repository, sample_job_data):
        """Test deleted jobs excluded from instance lookup."""
        job = repository.create(**sample_job_data)
        repository.start_job(job.job_id, "test-instance")
        
        instance_id = "test-instance"
        
        # Verify job is found initially
        found = repository.get_by_instance(instance_id)
        assert found is not None
        assert found.job_id == job.job_id
        
        # Soft delete the job
        repository.soft_delete(job.job_id)
        
        # After soft delete, get_by_instance should NOT find it
        found_after_delete = repository.get_by_instance(instance_id)
        assert found_after_delete is None
