"""Tests for JobRepository list() sort order fix.

This test module verifies the fix for the job list sort order:
- list(): ORDER BY created_at DESC, priority DESC (newest-first with priority as tiebreaker)
- list_by_queue(): ORDER BY priority DESC, created_at DESC (UNCHANGED - priority-first)
- list_pending_by_queue(): ORDER BY priority DESC, created_at ASC (UNCHANGED - priority-first)

The fix changed the primary sort order of list() from priority-first to newest-first,
making it suitable for the "All Jobs" view where recency is more important than priority.
"""

import pytest
from datetime import datetime, timedelta, timezone

from daemon.repositories.job_queue import JobRepository
from daemon.repositories.job_queue.models import AdmissionState, JobStatus


# Base job data without priority (priority varies per test)
BASE_JOB_DATA = {
    "agent_id": "test-agent",
    "agent_dir": "./agents/test-agent",
    "message": "Test job message",
    "source": "api",
    "project_id": "test-project",
    "job_metadata": {"test": True},
}


def create_job_with_timestamp(repository, data: dict, timestamp: str, **kwargs):
    """Helper to create a job and set its created_at timestamp persistently.

    The timestamp is updated via repository.update() to ensure it's persisted
    in the database, not just in the detached ORM object.
    """
    job = repository.create(**data, **kwargs)
    # Update the created_at to persist the timestamp change
    repository.update(job.job_id, created_at=timestamp)
    return job


class TestListSortOrder:
    """Tests for JobRepository.list() method sort order.

    The list() method should sort by created_at DESC, priority DESC.
    This means: newest jobs first, with priority as tiebreaker.
    """

    def test_list_returns_newest_first_when_same_priority(self, repository):
        """Test that list() returns newest jobs first when priority is the same.

        Scenario: Create jobs with different timestamps but same priority.
        Expected: Jobs should be returned in newest-first order.
        """
        # Create three jobs with different timestamps (same priority=5)
        timestamps = [
            "2024-01-01T10:00:00Z",
            "2024-01-01T12:00:00Z",  # newest
            "2024-01-01T08:00:00Z",  # oldest
        ]

        job_ids = []
        for ts in timestamps:
            job = create_job_with_timestamp(
                repository,
                BASE_JOB_DATA,
                ts,
                priority=5,
            )
            job_ids.append(job.job_id)

        # List all jobs
        jobs, total = repository.list()

        # Verify order: newest first (12:00, 10:00, 08:00)
        job_ids_ordered = [j.job_id for j in jobs]
        assert job_ids_ordered.index(job_ids[1]) < job_ids_ordered.index(job_ids[0])
        assert job_ids_ordered.index(job_ids[0]) < job_ids_ordered.index(job_ids[2])

    def test_list_uses_priority_as_tiebreaker_when_same_timestamp(self, repository):
        """Test that list() uses priority as tiebreaker when timestamps are equal.

        Scenario: Create jobs with same timestamp but different priorities.
        Expected: Higher priority jobs should come first within the same timestamp.
        """
        same_timestamp = "2024-01-01T12:00:00Z"

        # Create jobs with same timestamp but different priorities
        job_high = create_job_with_timestamp(
            repository,
            BASE_JOB_DATA,
            same_timestamp,
            priority=10,  # highest
        )

        job_low = create_job_with_timestamp(
            repository,
            BASE_JOB_DATA,
            same_timestamp,
            priority=1,  # lowest
        )

        job_mid = create_job_with_timestamp(
            repository,
            BASE_JOB_DATA,
            same_timestamp,
            priority=5,  # medium
        )

        # List all jobs
        jobs, total = repository.list()

        # Verify order: highest priority first when timestamps are equal
        job_ids_ordered = [j.job_id for j in jobs]
        assert job_ids_ordered.index(job_high.job_id) < job_ids_ordered.index(job_mid.job_id)
        assert job_ids_ordered.index(job_mid.job_id) < job_ids_ordered.index(job_low.job_id)

    def test_list_newest_takes_precedence_over_priority(self, repository):
        """Test that newest-first takes precedence over priority.

        Scenario: A newer low-priority job should come before an older high-priority job.
        Expected: created_at DESC is the primary sort, priority DESC is secondary.

        This is the KEY test for the sort order fix.
        """
        # Newer job with LOW priority
        job_new_low = create_job_with_timestamp(
            repository,
            BASE_JOB_DATA,
            "2024-01-01T14:00:00Z",  # newer
            priority=1,  # low
        )

        # Older job with HIGH priority
        job_old_high = create_job_with_timestamp(
            repository,
            BASE_JOB_DATA,
            "2024-01-01T10:00:00Z",  # older
            priority=10,  # high
        )

        # List all jobs
        jobs, total = repository.list()

        # Verify: NEWER job comes first despite lower priority
        job_ids_ordered = [j.job_id for j in jobs]
        assert job_ids_ordered.index(job_new_low.job_id) < job_ids_ordered.index(job_old_high.job_id), \
            "Newer job should come first even with lower priority (newest-first primary sort)"

    def test_list_without_queue_filter_all_jobs_view(self, repository):
        """Test list() without queue filter (All Jobs view) maintains sort order.

        Scenario: Jobs across multiple queues without queue filter.
        Expected: Sort order should be applied across all jobs regardless of queue.
        """
        # Create jobs in different queues with varying timestamps
        job1 = create_job_with_timestamp(
            repository,
            BASE_JOB_DATA,
            "2024-01-01T10:00:00Z",
            priority=5,
            queue_id="queue-a",
        )

        job2 = create_job_with_timestamp(
            repository,
            BASE_JOB_DATA,
            "2024-01-01T12:00:00Z",  # newest
            priority=10,
            queue_id="queue-b",
        )

        job3 = create_job_with_timestamp(
            repository,
            BASE_JOB_DATA,
            "2024-01-01T08:00:00Z",  # oldest
            priority=8,
            queue_id="queue-c",
        )

        # List without queue filter (All Jobs)
        jobs, total = repository.list()

        # Verify: newest first (job2 > job1 > job3)
        job_ids_ordered = [j.job_id for j in jobs]
        assert job_ids_ordered.index(job2.job_id) < job_ids_ordered.index(job1.job_id)
        assert job_ids_ordered.index(job1.job_id) < job_ids_ordered.index(job3.job_id)

    def test_list_same_timestamp_same_priority_stable_ordering(self, repository):
        """Test that list() handles identical timestamp and priority without crashing.

        Scenario: Jobs with exactly same timestamp and priority.
        Expected: Should not crash, stable ordering (any order is acceptable).
        """
        same_ts = "2024-01-01T12:00:00Z"
        same_priority = 5

        job1 = create_job_with_timestamp(repository, BASE_JOB_DATA, same_ts, priority=same_priority)
        job2 = create_job_with_timestamp(repository, BASE_JOB_DATA, same_ts, priority=same_priority)
        job3 = create_job_with_timestamp(repository, BASE_JOB_DATA, same_ts, priority=same_priority)

        # Should not crash
        jobs, total = repository.list()

        # All jobs should be returned
        assert len(jobs) >= 3
        job_ids = {j.job_id for j in jobs}
        assert job_ids >= {job1.job_id, job2.job_id, job3.job_id}

    def test_list_single_job(self, repository):
        """Test list() with a single job returns it correctly."""
        job = repository.create(**BASE_JOB_DATA, priority=5)
        jobs, total = repository.list()

        assert len(jobs) == 1
        assert jobs[0].job_id == job.job_id
        assert total == 1

    def test_list_empty_result(self, repository):
        """Test list() with no jobs returns empty list."""
        jobs, total = repository.list()
        assert jobs == []
        assert total == 0


class TestListByQueueSortOrder:
    """Tests for JobRepository.list_by_queue() method sort order.

    This method should UNCHANGED: ORDER BY priority DESC, created_at DESC.
    Priority takes precedence, with newest first as tiebreaker.
    """

    def test_list_by_queue_priority_takes_precedence(self, repository):
        """Test that list_by_queue() sorts by priority first, then created_at.

        This method should NOT have been changed - it maintains priority-first sorting.
        """
        queue_id = "test-queue-1"

        # Newer job with LOW priority
        job_new_low = create_job_with_timestamp(
            repository,
            BASE_JOB_DATA,
            "2024-01-01T14:00:00Z",  # newer
            priority=1,  # low
            queue_id=queue_id,
        )

        # Older job with HIGH priority
        job_old_high = create_job_with_timestamp(
            repository,
            BASE_JOB_DATA,
            "2024-01-01T10:00:00Z",  # older
            priority=10,  # high
            queue_id=queue_id,
        )

        # List by queue
        jobs, total = repository.list_by_queue(queue_id)

        # Verify: HIGH priority job comes first despite being older
        job_ids_ordered = [j.job_id for j in jobs]
        assert job_ids_ordered.index(job_old_high.job_id) < job_ids_ordered.index(job_new_low.job_id), \
            "Higher priority job should come first in list_by_queue (priority-first sort)"

    def test_list_by_queue_same_priority_newest_first(self, repository):
        """Test that list_by_queue() returns newest first when priorities are equal."""
        queue_id = "test-queue-2"

        job_old = create_job_with_timestamp(
            repository,
            BASE_JOB_DATA,
            "2024-01-01T10:00:00Z",
            priority=5,
            queue_id=queue_id,
        )

        job_new = create_job_with_timestamp(
            repository,
            BASE_JOB_DATA,
            "2024-01-01T14:00:00Z",  # newer
            priority=5,
            queue_id=queue_id,
        )

        jobs, total = repository.list_by_queue(queue_id)

        job_ids_ordered = [j.job_id for j in jobs]
        assert job_ids_ordered.index(job_new.job_id) < job_ids_ordered.index(job_old.job_id)


class TestListPendingByQueueSortOrder:
    """Tests for JobRepository.list_pending_by_queue() method sort order.

    This method should UNCHANGED: ORDER BY priority DESC, created_at ASC.
    Priority takes precedence, with OLDEST first (FIFO) as tiebreaker within same priority.
    """

    def test_list_pending_by_queue_priority_takes_precedence(self, repository):
        """Test that list_pending_by_queue() sorts by priority first.

        This method should NOT have been changed - it maintains priority-first sorting.
        """
        queue_id = "test-queue-pending-1"

        # Newer job with LOW priority
        job_new_low = create_job_with_timestamp(
            repository,
            BASE_JOB_DATA,
            "2024-01-01T14:00:00Z",  # newer
            priority=1,  # low
            queue_id=queue_id,
        )

        # Older job with HIGH priority
        job_old_high = create_job_with_timestamp(
            repository,
            BASE_JOB_DATA,
            "2024-01-01T10:00:00Z",  # older
            priority=10,  # high
            queue_id=queue_id,
        )

        # List pending by queue
        jobs = repository.list_pending_by_queue(queue_id)

        # Verify: HIGH priority job comes first despite being older
        job_ids_ordered = [j.job_id for j in jobs]
        assert job_ids_ordered.index(job_old_high.job_id) < job_ids_ordered.index(job_new_low.job_id), \
            "Higher priority job should come first in list_pending_by_queue (priority-first sort)"

    def test_list_pending_by_queue_same_priority_oldest_first(self, repository):
        """Test that list_pending_by_queue() returns OLDEST first within same priority (FIFO).

        This is different from list() which returns newest first.
        """
        queue_id = "test-queue-pending-2"

        job_new = create_job_with_timestamp(
            repository,
            BASE_JOB_DATA,
            "2024-01-01T14:00:00Z",  # newer
            priority=5,
            queue_id=queue_id,
        )

        job_old = create_job_with_timestamp(
            repository,
            BASE_JOB_DATA,
            "2024-01-01T10:00:00Z",  # older
            priority=5,
            queue_id=queue_id,
        )

        jobs = repository.list_pending_by_queue(queue_id)

        # Verify: OLDEST job comes first when priorities are equal (FIFO within same priority)
        job_ids_ordered = [j.job_id for j in jobs]
        assert job_ids_ordered.index(job_old.job_id) < job_ids_ordered.index(job_new.job_id), \
            "Oldest job should come first within same priority (FIFO ordering)"


class TestSortOrderRegressionTests:
    """Regression tests to ensure sort order changes don't break existing behavior."""

    def test_list_excludes_soft_deleted_jobs(self, repository):
        """Verify list() still excludes soft-deleted jobs."""
        active_job = repository.create(**BASE_JOB_DATA, priority=5)

        deleted_job = repository.create(**BASE_JOB_DATA, priority=5)
        repository.soft_delete(deleted_job.job_id)

        jobs, total = repository.list()

        job_ids = [j.job_id for j in jobs]
        assert active_job.job_id in job_ids
        assert deleted_job.job_id not in job_ids

    def test_list_filters_by_status(self, repository):
        """Verify list() still correctly filters by status."""
        pending_job = repository.create(**BASE_JOB_DATA, priority=5)

        processing_job = repository.create(**BASE_JOB_DATA, priority=5)
        repository.start_job(processing_job.job_id, "test-instance")

        # Filter for pending only — Phase 5: pass the legacy
        # ``JobStatus.PENDING.value`` so ``_statuses_to_admission``
        # maps to ``AdmissionState.QUEUED.value`` and the SQL filter
        # matches the right admission bucket.
        jobs, total = repository.list(statuses=[JobStatus.PENDING.value])

        job_ids = [j.job_id for j in jobs]
        assert pending_job.job_id in job_ids
        assert processing_job.job_id not in job_ids

    def test_list_filters_by_queue_id(self, repository):
        """Verify list() still correctly filters by queue_id."""
        queue_a_job = repository.create(**BASE_JOB_DATA, priority=5, queue_id="queue-a")
        queue_b_job = repository.create(**BASE_JOB_DATA, priority=5, queue_id="queue-b")

        # Filter for queue-a only
        jobs, total = repository.list(queue_id="queue-a")

        job_ids = [j.job_id for j in jobs]
        assert queue_a_job.job_id in job_ids
        assert queue_b_job.job_id not in job_ids

    def test_list_by_queue_excludes_deleted_jobs(self, repository):
        """Verify list_by_queue() still excludes soft-deleted jobs."""
        queue_id = "test-queue-deleted"

        active_job = repository.create(**BASE_JOB_DATA, priority=5, queue_id=queue_id)

        deleted_job = repository.create(**BASE_JOB_DATA, priority=5, queue_id=queue_id)
        repository.soft_delete(deleted_job.job_id)

        jobs, total = repository.list_by_queue(queue_id)

        job_ids = [j.job_id for j in jobs]
        assert active_job.job_id in job_ids
        assert deleted_job.job_id not in job_ids

    def test_list_pending_by_queue_only_returns_pending(self, repository):
        """Verify list_pending_by_queue() only returns PENDING jobs."""
        queue_id = "test-queue-pending-filter"

        pending_job = repository.create(**BASE_JOB_DATA, priority=5, queue_id=queue_id)

        processing_job = repository.create(**BASE_JOB_DATA, priority=5, queue_id=queue_id)
        repository.start_job(processing_job.job_id, "test-instance")

        jobs = repository.list_pending_by_queue(queue_id)

        job_ids = [j.job_id for j in jobs]
        assert pending_job.job_id in job_ids
        assert processing_job.job_id not in job_ids
