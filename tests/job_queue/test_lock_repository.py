"""Comprehensive tests for LockRepository.

This module tests the persistence layer for job locks.
"""

import pytest

from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.job_queue.models import JobLock


class TestLockRepositoryAcquire:
    """Tests for acquire() method."""

    def test_acquire_persists_lock(self, engine):
        """Test acquire() persists a lock."""
        repo = LockRepository(engine)
        
        lock = JobLock(
            project_id="test-project",
            queue_id="test-queue",
            job_id="job-123",
            instance_id="instance-456",
        )
        
        result = repo.acquire(lock)
        
        assert result is not None
        assert result.lock_id is not None
        assert result.project_id == "test-project"
        assert result.queue_id == "test-queue"
        assert result.job_id == "job-123"
        assert result.instance_id == "instance-456"

    def test_acquire_generates_lock_id(self, engine):
        """Test acquire() generates a unique lock_id."""
        repo = LockRepository(engine)
        
        lock1 = JobLock(project_id="p1", queue_id="q1", job_id="j1")
        lock2 = JobLock(project_id="p2", queue_id="q2", job_id="j2")
        
        result1 = repo.acquire(lock1)
        result2 = repo.acquire(lock2)
        
        assert result1.lock_id != result2.lock_id

    def test_acquire_sets_acquired_at(self, engine):
        """Test acquire() sets acquired_at timestamp."""
        repo = LockRepository(engine)
        
        lock = JobLock(
            project_id="test-project",
            queue_id="test-queue",
            job_id="job-123",
        )
        
        result = repo.acquire(lock)
        
        assert result.acquired_at is not None


class TestLockRepositoryRelease:
    """Tests for release() method."""

    def test_release_removes_lock(self, engine):
        """Test release() removes a lock."""
        repo = LockRepository(engine)
        
        lock = JobLock(
            project_id="test-project",
            queue_id="test-queue",
            job_id="job-123",
        )
        acquired = repo.acquire(lock)
        
        result = repo.release(acquired.lock_id)
        
        assert result is True
        
        # Verify lock is gone
        locks = repo.get_all_locks()
        assert not any(l.lock_id == acquired.lock_id for l in locks)

    def test_release_returns_false_for_nonexistent(self, engine):
        """Test release() returns False for non-existent lock."""
        repo = LockRepository(engine)
        
        result = repo.release("nonexistent-lock-id")
        
        assert result is False


class TestLockRepositoryReleaseByJob:
    """Tests for release_by_job() method."""

    def test_release_by_job_removes_correct_lock(self, engine):
        """Test release_by_job() removes the correct lock."""
        repo = LockRepository(engine)
        
        # Create multiple locks
        lock1 = JobLock(
            project_id="test-project",
            queue_id="test-queue",
            job_id="job-1",
        )
        lock2 = JobLock(
            project_id="test-project",
            queue_id="test-queue",
            job_id="job-2",
        )
        repo.acquire(lock1)
        repo.acquire(lock2)
        
        # Release job-1
        result = repo.release_by_job("test-project", "test-queue", "job-1")
        
        assert result is True
        
        # Verify job-1 is gone, job-2 remains
        active = repo.get_active_locks("test-project", "test-queue")
        job_ids = {lock.job_id for lock in active}
        assert "job-1" not in job_ids
        assert "job-2" in job_ids

    def test_release_by_job_returns_false_if_not_found(self, engine):
        """Test release_by_job() returns False if lock not found."""
        repo = LockRepository(engine)
        
        result = repo.release_by_job("nonexistent", "queue", "job")
        
        assert result is False


class TestLockRepositoryReleaseByInstance:
    """Tests for release_by_instance() method."""

    def test_release_by_instance_removes_all_instance_locks(self, engine):
        """Test release_by_instance() removes all locks for an instance."""
        repo = LockRepository(engine)
        
        # Create locks for instance-1
        lock1 = JobLock(
            project_id="project-1",
            queue_id="queue-1",
            job_id="job-1",
            instance_id="instance-1",
        )
        lock2 = JobLock(
            project_id="project-2",
            queue_id="queue-2",
            job_id="job-2",
            instance_id="instance-1",
        )
        # Create lock for different instance
        lock3 = JobLock(
            project_id="project-3",
            queue_id="queue-3",
            job_id="job-3",
            instance_id="instance-2",
        )
        repo.acquire(lock1)
        repo.acquire(lock2)
        repo.acquire(lock3)
        
        # Release all locks for instance-1
        count = repo.release_by_instance("instance-1")
        
        assert count == 2
        
        # Verify instance-1 locks are gone
        instance1_locks = repo.get_locks_by_instance("instance-1")
        assert len(instance1_locks) == 0
        
        # Verify instance-2 lock remains
        instance2_locks = repo.get_locks_by_instance("instance-2")
        assert len(instance2_locks) == 1

    def test_release_by_instance_returns_zero_for_nonexistent(self, engine):
        """Test release_by_instance() returns 0 for nonexistent instance."""
        repo = LockRepository(engine)
        
        count = repo.release_by_instance("nonexistent-instance")
        
        assert count == 0


class TestLockRepositoryGetActiveLocks:
    """Tests for get_active_locks() method."""

    def test_get_active_locks_returns_locks_for_queue(self, engine):
        """Test get_active_locks() returns only locks for specified queue."""
        repo = LockRepository(engine)
        
        # Create locks for different queues
        lock1 = JobLock(project_id="p1", queue_id="q1", job_id="j1")
        lock2 = JobLock(project_id="p1", queue_id="q1", job_id="j2")
        lock3 = JobLock(project_id="p1", queue_id="q2", job_id="j3")
        repo.acquire(lock1)
        repo.acquire(lock2)
        repo.acquire(lock3)
        
        active = repo.get_active_locks("p1", "q1")
        
        assert len(active) == 2
        job_ids = {lock.job_id for lock in active}
        assert "j1" in job_ids
        assert "j2" in job_ids
        assert "j3" not in job_ids

    def test_get_active_locks_returns_empty_for_nonexistent_queue(self, engine):
        """Test get_active_locks() returns empty list for nonexistent queue."""
        repo = LockRepository(engine)
        
        active = repo.get_active_locks("nonexistent", "queue")
        
        assert active == []


class TestLockRepositoryGetLockCount:
    """Tests for get_lock_count() method."""

    def test_get_lock_count_returns_correct_count(self, engine):
        """Test get_lock_count() returns correct count."""
        repo = LockRepository(engine)
        
        # Create 3 locks for the queue
        for i in range(3):
            lock = JobLock(project_id="p1", queue_id="q1", job_id=f"job-{i}")
            repo.acquire(lock)
        
        count = repo.get_lock_count("p1", "q1")
        
        assert count == 3

    def test_get_lock_count_returns_zero_for_empty_queue(self, engine):
        """Test get_lock_count() returns 0 for empty queue."""
        repo = LockRepository(engine)
        
        count = repo.get_lock_count("nonexistent", "queue")
        
        assert count == 0

    def test_get_lock_count_excludes_other_queues(self, engine):
        """Test get_lock_count() excludes locks from other queues."""
        repo = LockRepository(engine)
        
        lock1 = JobLock(project_id="p1", queue_id="q1", job_id="j1")
        lock2 = JobLock(project_id="p1", queue_id="q2", job_id="j2")
        repo.acquire(lock1)
        repo.acquire(lock2)
        
        count_q1 = repo.get_lock_count("p1", "q1")
        count_q2 = repo.get_lock_count("p1", "q2")
        
        assert count_q1 == 1
        assert count_q2 == 1


class TestLockRepositoryGetAllLocks:
    """Tests for get_all_locks() method."""

    def test_get_all_locks_returns_all_locks(self, engine):
        """Test get_all_locks() returns all locks across all queues."""
        repo = LockRepository(engine)
        
        lock1 = JobLock(project_id="p1", queue_id="q1", job_id="j1")
        lock2 = JobLock(project_id="p1", queue_id="q2", job_id="j2")
        lock3 = JobLock(project_id="p2", queue_id="q1", job_id="j3")
        repo.acquire(lock1)
        repo.acquire(lock2)
        repo.acquire(lock3)
        
        all_locks = repo.get_all_locks()
        
        assert len(all_locks) == 3

    def test_get_all_locks_returns_empty_for_empty_db(self, engine):
        """Test get_all_locks() returns empty list for empty database."""
        repo = LockRepository(engine)
        
        all_locks = repo.get_all_locks()
        
        assert all_locks == []


class TestLockRepositoryGetLocksByInstance:
    """Tests for get_locks_by_instance() method."""

    def test_get_locks_by_instance_returns_instance_locks(self, engine):
        """Test get_locks_by_instance() returns only locks for the instance."""
        repo = LockRepository(engine)
        
        lock1 = JobLock(
            project_id="p1",
            queue_id="q1",
            job_id="j1",
            instance_id="instance-1"
        )
        lock2 = JobLock(
            project_id="p2",
            queue_id="q2",
            job_id="j2",
            instance_id="instance-1"
        )
        lock3 = JobLock(
            project_id="p3",
            queue_id="q3",
            job_id="j3",
            instance_id="instance-2"
        )
        repo.acquire(lock1)
        repo.acquire(lock2)
        repo.acquire(lock3)
        
        instance_locks = repo.get_locks_by_instance("instance-1")
        
        assert len(instance_locks) == 2
        instance_ids = {lock.instance_id for lock in instance_locks}
        assert instance_ids == {"instance-1"}

    def test_get_locks_by_instance_returns_empty_for_nonexistent(self, engine):
        """Test get_locks_by_instance() returns empty list for nonexistent instance."""
        repo = LockRepository(engine)
        
        instance_locks = repo.get_locks_by_instance("nonexistent")
        
        assert instance_locks == []


class TestLockRepositoryEdgeCases:
    """Tests for edge cases and error conditions."""

    def test_release_lock_idempotent(self, engine):
        """Test that releasing the same lock twice returns False the second time."""
        repo = LockRepository(engine)
        
        lock = JobLock(
            project_id="p1",
            queue_id="q1",
            job_id="j1",
        )
        acquired = repo.acquire(lock)
        
        result1 = repo.release(acquired.lock_id)
        result2 = repo.release(acquired.lock_id)
        
        assert result1 is True
        assert result2 is False

    def test_release_by_job_idempotent(self, engine):
        """Test that releasing by job twice returns False the second time."""
        repo = LockRepository(engine)
        
        lock = JobLock(
            project_id="p1",
            queue_id="q1",
            job_id="j1",
        )
        repo.acquire(lock)
        
        result1 = repo.release_by_job("p1", "q1", "j1")
        result2 = repo.release_by_job("p1", "q1", "j1")
        
        assert result1 is True
        assert result2 is False

    def test_acquire_multiple_locks_same_job_different_projects(self, engine):
        """Test that same job_id can have locks in different projects."""
        repo = LockRepository(engine)
        
        lock1 = JobLock(project_id="p1", queue_id="q1", job_id="same-job")
        lock2 = JobLock(project_id="p2", queue_id="q2", job_id="same-job")
        
        repo.acquire(lock1)
        repo.acquire(lock2)
        
        all_locks = repo.get_all_locks()
        assert len(all_locks) == 2

    def test_lock_without_instance_id(self, engine):
        """Test that locks can be created without instance_id."""
        repo = LockRepository(engine)
        
        lock = JobLock(
            project_id="p1",
            queue_id="q1",
            job_id="j1",
            instance_id=None,
        )
        result = repo.acquire(lock)
        
        assert result.lock_id is not None
        assert result.instance_id is None
