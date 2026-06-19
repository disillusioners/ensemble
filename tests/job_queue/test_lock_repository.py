"""Comprehensive tests for LockRepository.

This module tests the persistence layer for job locks.
"""

import pytest

from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.job_queue.models import JobLock


def _acquire_with_slot(repo, project_id, queue_id, job_id, slot,
                       instance_id=None):
    """Test helper: create and acquire a JobLock with an explicit ``lock_slot``.

    After migration ``20260619_000001_add_lock_slot_to_job_locks.sql`` the
    ``uq_job_locks_slot`` UNIQUE constraint on
    ``(project_id, queue_id, lock_slot)`` makes the old pattern of
    inserting many locks with default ``lock_slot=0`` invalid — every
    lock in the same (project, queue) pair must carry a distinct slot.
    This helper picks the slot explicitly so tests can build realistic
    lock sets without bouncing off the new constraint.

    Tests that need to exercise the *acquire* method itself (not the
    surrounding release/get logic) should call ``repo.acquire(...)``
    directly with a fully-specified ``JobLock``.
    """
    lock = JobLock(
        project_id=project_id,
        queue_id=queue_id,
        job_id=job_id,
        instance_id=instance_id,
        lock_slot=slot,
    )
    return repo.acquire(lock)


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

        # Create multiple locks in the same queue. Distinct lock_slot
        # values are required (uq_job_locks_slot UNIQUE constraint
        # added by migration 20260619_000001).
        _acquire_with_slot(repo, "test-project", "test-queue", "job-1", slot=0)
        _acquire_with_slot(repo, "test-project", "test-queue", "job-2", slot=1)

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

        # Create locks for different queues. Distinct lock_slot
        # values for the two locks that share (p1, q1).
        _acquire_with_slot(repo, "p1", "q1", "j1", slot=0)
        _acquire_with_slot(repo, "p1", "q1", "j2", slot=1)
        _acquire_with_slot(repo, "p1", "q2", "j3", slot=0)

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

        # Create 3 locks for the queue with distinct lock_slot values
        # (uq_job_locks_slot UNIQUE constraint).
        for i in range(3):
            _acquire_with_slot(repo, "p1", "q1", f"job-{i}", slot=i)

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


# ============================================================================
# C5 — try_acquire_slot (cross-process atomic acquire primitive)
# ============================================================================

class TestTryAcquireSlot:
    """Tests for the dialect-aware atomic slot acquire primitive.

    These verify the building block of the cross-process-safe
    ``acquire_queue_lock``. Two processes that both pass the
    in-process acquire path cannot both end up holding the same
    slot — the DB-enforced UNIQUE constraint on
    ``(project_id, queue_id, lock_slot)`` ensures at most one
    wins each ``try_acquire_slot`` call.
    """

    def test_first_call_for_slot_returns_true(self, engine):
        """First call to try_acquire_slot for a free slot succeeds."""
        repo = LockRepository(engine)

        ok = repo.try_acquire_slot(
            lock_id="lock-A",
            project_id="p1",
            queue_id="q1",
            job_id="job-1",
            instance_id="inst-1",
            slot=0,
        )
        assert ok is True

        locks = repo.get_all_locks()
        assert len(locks) == 1
        assert locks[0].lock_id == "lock-A"
        assert locks[0].lock_slot == 0

    def test_second_call_same_slot_returns_false(self, engine):
        """Second call for the same slot is rejected by the UNIQUE constraint."""
        repo = LockRepository(engine)

        ok1 = repo.try_acquire_slot(
            lock_id="lock-A",
            project_id="p1",
            queue_id="q1",
            job_id="job-1",
            instance_id="inst-1",
            slot=0,
        )
        ok2 = repo.try_acquire_slot(
            lock_id="lock-B",
            project_id="p1",
            queue_id="q1",
            job_id="job-2",
            instance_id="inst-2",
            slot=0,
        )
        assert ok1 is True
        assert ok2 is False

        # Only the first lock survives.
        locks = repo.get_all_locks()
        assert len(locks) == 1
        assert locks[0].lock_id == "lock-A"

    def test_different_slots_coexist(self, engine):
        """Different slots in the same queue can both be claimed."""
        repo = LockRepository(engine)

        ok0 = repo.try_acquire_slot(
            lock_id="lock-A", project_id="p1", queue_id="q1",
            job_id="job-1", instance_id="inst-1", slot=0,
        )
        ok1 = repo.try_acquire_slot(
            lock_id="lock-B", project_id="p1", queue_id="q1",
            job_id="job-2", instance_id="inst-2", slot=1,
        )
        assert ok0 is True
        assert ok1 is True

        locks = sorted(repo.get_all_locks(), key=lambda l: l.lock_slot)
        assert [l.lock_slot for l in locks] == [0, 1]

    def test_different_queues_independent_slots(self, engine):
        """Slot 0 in queue A and slot 0 in queue B are independent."""
        repo = LockRepository(engine)

        ok_a = repo.try_acquire_slot(
            lock_id="lock-A", project_id="p1", queue_id="qA",
            job_id="job-1", instance_id="inst-1", slot=0,
        )
        ok_b = repo.try_acquire_slot(
            lock_id="lock-B", project_id="p1", queue_id="qB",
            job_id="job-2", instance_id="inst-2", slot=0,
        )
        assert ok_a is True
        assert ok_b is True
        assert len(repo.get_all_locks()) == 2

    def test_at_most_concurrency_limit_locks_per_queue(self, engine):
        """Bounded slots: with limit=3, only 3 lock rows can exist per queue."""
        repo = LockRepository(engine)
        limit = 3

        acquired = []
        for i in range(limit):
            ok = repo.try_acquire_slot(
                lock_id=f"lock-{i}",
                project_id="p1",
                queue_id="q1",
                job_id=f"job-{i}",
                instance_id=f"inst-{i}",
                slot=i,
            )
            acquired.append(ok)
        assert acquired == [True, True, True]

        # A 4th attempt at slot 0..limit-1 must all fail.
        for i in range(limit):
            ok = repo.try_acquire_slot(
                lock_id=f"overflow-{i}",
                project_id="p1",
                queue_id="q1",
                job_id=f"overflow-job-{i}",
                instance_id=f"overflow-inst-{i}",
                slot=i,
            )
            assert ok is False, f"slot {i} should be occupied"

        assert repo.get_lock_count("p1", "q1") == limit

    def test_instance_id_optional(self, engine):
        """Slot acquire with instance_id=None succeeds."""
        repo = LockRepository(engine)

        ok = repo.try_acquire_slot(
            lock_id="lock-A",
            project_id="p1",
            queue_id="q1",
            job_id="job-1",
            instance_id=None,
            slot=0,
        )
        assert ok is True
        assert repo.get_all_locks()[0].instance_id is None


# ============================================================================
# C12 — clear_stale_job_locks / clear_terminal_job_locks
# ============================================================================


class TestClearStaleJobLocks:
    """Tests for the C12 startup sweep / periodic cleanup primitives.

    ``clear_stale_job_locks`` (and its alias ``clear_terminal_job_locks``)
    DELETEs every ``job_locks`` row whose ``job_id`` no longer maps to an
    active (pending/processing, non-deleted) ``job_queue_items`` row.

    Setup pattern: create the lock, then create (or skip creating) the
    backing job with the appropriate status, then call the sweep and
    assert which locks survived.
    """

    def _create_job(self, repository, project_id, queue_id, status):
        """Create a job with the given status. Returns the job."""
        return repository.create(
            agent_id="test-agent",
            agent_dir="./agents/test-agent",
            message="sweep test job",
            source="api",
            project_id=project_id,
            queue_id=queue_id,
            priority=5,
            job_metadata=None,
        )

    def _force_status(self, repository, job_id, status):
        """Direct UPDATE to set job status (bypass normal flow for tests)."""
        from sqlalchemy import text
        with repository.engine.begin() as conn:
            conn.execute(
                text("UPDATE job_queue_items SET status = :s WHERE job_id = :id"),
                {"s": status, "id": job_id},
            )

    def test_orphan_lock_with_no_job_is_cleared(self, engine, lock_repo):
        """A lock whose job_id has no row in job_queue_items is cleared."""
        lock = JobLock(
            project_id="p1", queue_id="q1", job_id="ghost-job",
            instance_id=None,
        )
        lock_repo.acquire(lock)
        assert len(lock_repo.get_all_locks()) == 1

        cleared = lock_repo.clear_stale_job_locks()
        assert cleared == 1
        assert lock_repo.get_all_locks() == []

    def test_active_pending_job_lock_survives(self, engine, lock_repo, repository):
        """A lock for a PENDING job is NOT cleared (job is still active)."""
        job = self._create_job(repository, "p1", "q1", "pending")
        # create() defaults status to PENDING, but be explicit:
        self._force_status(repository, job.job_id, "pending")

        lock = JobLock(
            project_id="p1", queue_id="q1", job_id=job.job_id,
            instance_id=None,
        )
        lock_repo.acquire(lock)

        cleared = lock_repo.clear_stale_job_locks()
        assert cleared == 0
        assert len(lock_repo.get_all_locks()) == 1

    def test_active_processing_job_lock_survives(self, engine, lock_repo, repository):
        """A lock for a PROCESSING job is NOT cleared."""
        job = self._create_job(repository, "p1", "q1", "processing")

        lock = JobLock(
            project_id="p1", queue_id="q1", job_id=job.job_id,
            instance_id=None,
        )
        lock_repo.acquire(lock)

        cleared = lock_repo.clear_stale_job_locks()
        assert cleared == 0
        assert len(lock_repo.get_all_locks()) == 1

    def test_terminal_status_locks_are_cleared(self, engine, lock_repo, repository):
        """Locks for jobs in terminal statuses (completed/failed/cancelled/dead_letter) are cleared."""
        for idx, status in enumerate(("completed", "failed", "cancelled", "dead_letter")):
            job = self._create_job(repository, "p1", "q1", "pending")
            self._force_status(repository, job.job_id, status)
            # Distinct lock_slot values for each lock in (p1, q1).
            _acquire_with_slot(lock_repo, "p1", "q1", job.job_id, slot=idx)

        # All 4 locks are present.
        assert len(lock_repo.get_all_locks()) == 4

        cleared = lock_repo.clear_stale_job_locks()
        assert cleared == 4
        assert lock_repo.get_all_locks() == []


    def test_soft_deleted_job_lock_cleared(self, engine, lock_repo, repository):
        """A lock for a soft-deleted job is cleared (deleted_at IS NOT NULL)."""
        from datetime import datetime, timezone
        job = self._create_job(repository, "p1", "q1", "processing")

        lock = JobLock(
            project_id="p1", queue_id="q1", job_id=job.job_id,
            instance_id=None,
        )
        lock_repo.acquire(lock)

        # Soft-delete the job.
        from sqlalchemy import text
        with repository.engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE job_queue_items SET deleted_at = :now "
                    "WHERE job_id = :id"
                ),
                {
                    "now": datetime.now(timezone.utc).isoformat(),
                    "id": job.job_id,
                },
            )

        cleared = lock_repo.clear_stale_job_locks()
        assert cleared == 1
        assert lock_repo.get_all_locks() == []

    def test_clear_terminal_alias_matches_clear_stale(self, engine, lock_repo, repository):
        """clear_terminal_job_locks is an alias with identical semantics."""
        job = self._create_job(repository, "p1", "q1", "completed")
        # _create_job ignores the status arg (it just calls
        # repository.create with no status). Force-set it explicitly.
        self._force_status(repository, job.job_id, "completed")
        lock = JobLock(
            project_id="p1", queue_id="q1", job_id=job.job_id,
            instance_id=None,
        )
        lock_repo.acquire(lock)

        cleared = lock_repo.clear_terminal_job_locks()
        assert cleared == 1
        assert lock_repo.get_all_locks() == []

    def test_sweep_is_idempotent(self, engine, lock_repo):
        """Running the sweep twice with no new data returns 0 the second time."""
        # No jobs at all → all locks (if any) are orphaned.
        lock = JobLock(
            project_id="p1", queue_id="q1", job_id="ghost",
            instance_id=None,
        )
        lock_repo.acquire(lock)

        first = lock_repo.clear_stale_job_locks()
        second = lock_repo.clear_stale_job_locks()
        assert first == 1
        assert second == 0

    def test_mixed_active_and_terminal(self, engine, lock_repo, repository):
        """Sweep clears terminal locks while leaving active ones intact."""
        # One active (processing) job at slot 0.
        active_job = self._create_job(repository, "p1", "q1", "processing")
        _acquire_with_slot(
            lock_repo, "p1", "q1", active_job.job_id, slot=0,
        )

        # One completed job at slot 1.
        completed_job = self._create_job(repository, "p1", "q1", "pending")
        self._force_status(repository, completed_job.job_id, "completed")
        _acquire_with_slot(
            lock_repo, "p1", "q1", completed_job.job_id, slot=1,
        )

        # One orphan (no job row) at slot 2.
        _acquire_with_slot(
            lock_repo, "p1", "q1", "never-existed", slot=2,
        )

        assert len(lock_repo.get_all_locks()) == 3

        cleared = lock_repo.clear_stale_job_locks()
        assert cleared == 2  # completed + orphan

        survivors = lock_repo.get_all_locks()
        assert len(survivors) == 1
        assert survivors[0].job_id == active_job.job_id
