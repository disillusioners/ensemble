"""Tests for JobLockManager.

This module tests the in-memory lock manager that provides per-queue
job serialization with queue-based locking.
"""

import asyncio
from datetime import datetime
import pytest

from daemon.services.job_lock_manager import JobLockManager
from daemon.repositories.job_queue.models import JobLockInfo


class TestLockManagerBasicOperations:
    """Tests for basic lock acquisition and release."""

    @pytest.mark.asyncio
    async def test_acquire_lock_success(self, lock_manager):
        """Test successful lock acquisition."""
        result = await lock_manager.acquire(
            project_id="project-1",
            job_id="job-1",
            instance_id="instance-1"
        )
        assert result is True
        assert await lock_manager.is_locked("project-1") is True

    @pytest.mark.asyncio
    async def test_acquire_lock_already_held(self, lock_manager):
        """Test acquiring lock that's already held returns False."""
        # First acquisition succeeds
        await lock_manager.acquire(
            project_id="project-1",
            job_id="job-1",
            instance_id="instance-1"
        )
        
        # Second acquisition for same project fails
        result = await lock_manager.acquire(
            project_id="project-1",
            job_id="job-2",
            instance_id="instance-2"
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_release_lock_success(self, lock_manager):
        """Test successful lock release."""
        await lock_manager.acquire(
            project_id="project-1",
            job_id="job-1",
            instance_id="instance-1"
        )
        
        result = await lock_manager.release("project-1", "job-1")
        assert result is True
        assert await lock_manager.is_locked("project-1") is False

    @pytest.mark.asyncio
    async def test_release_lock_not_held(self, lock_manager):
        """Test releasing lock that's not held returns False."""
        result = await lock_manager.release("project-1", "job-1")
        assert result is False

    @pytest.mark.asyncio
    async def test_release_lock_wrong_job(self, lock_manager):
        """Test releasing lock held by different job returns False."""
        await lock_manager.acquire(
            project_id="project-1",
            job_id="job-1",
            instance_id="instance-1"
        )
        
        # Try to release with different job_id
        result = await lock_manager.release("project-1", "job-2")
        assert result is False
        assert await lock_manager.is_locked("project-1") is True

    @pytest.mark.asyncio
    async def test_release_lock_double_release(self, lock_manager):
        """Test double release returns False second time."""
        await lock_manager.acquire(
            project_id="project-1",
            job_id="job-1",
            instance_id="instance-1"
        )
        
        # First release succeeds
        result1 = await lock_manager.release("project-1", "job-1")
        assert result1 is True
        
        # Second release fails (lock no longer held)
        result2 = await lock_manager.release("project-1", "job-1")
        assert result2 is False

    @pytest.mark.asyncio
    async def test_multiple_projects_independent(self, lock_manager):
        """Test locks for different projects are independent."""
        # Acquire locks for multiple projects
        await lock_manager.acquire("project-1", "job-1", "instance-1")
        await lock_manager.acquire("project-2", "job-2", "instance-2")
        await lock_manager.acquire("project-3", "job-3", "instance-3")
        
        assert await lock_manager.is_locked("project-1") is True
        assert await lock_manager.is_locked("project-2") is True
        assert await lock_manager.is_locked("project-3") is True
        
        # Release one doesn't affect others
        await lock_manager.release("project-2", "job-2")
        assert await lock_manager.is_locked("project-1") is True
        assert await lock_manager.is_locked("project-2") is False
        assert await lock_manager.is_locked("project-3") is True


class TestLockManagerGetLockInfo:
    """Tests for lock information retrieval."""

    @pytest.mark.asyncio
    async def test_get_lock_info_exists(self, lock_manager):
        """Test getting lock info when lock exists."""
        await lock_manager.acquire(
            project_id="project-1",
            job_id="job-1",
            instance_id="instance-1"
        )
        
        info = await lock_manager.get_lock_info("project-1")
        assert info is not None
        assert info.job_id == "job-1"
        assert info.project_id == "project-1"
        assert info.instance_id == "instance-1"
        assert info.locked_at is not None

    @pytest.mark.asyncio
    async def test_get_lock_info_not_exists(self, lock_manager):
        """Test getting lock info when lock doesn't exist."""
        info = await lock_manager.get_lock_info("project-1")
        assert info is None

    @pytest.mark.asyncio
    async def test_get_all_locks(self, lock_manager):
        """Test getting all current locks."""
        await lock_manager.acquire("project-1", "job-1", "instance-1")
        await lock_manager.acquire("project-2", "job-2", "instance-2")
        
        all_locks = await lock_manager.get_all_locks()
        assert len(all_locks) == 2
        # Keys are in format "project_id:queue_id"
        # Default queue_id is "project:{project_id}"
        assert "project-1:project:project-1" in all_locks
        assert "project-2:project:project-2" in all_locks

    @pytest.mark.asyncio
    async def test_get_all_locks_empty(self, lock_manager):
        """Test getting all locks when none exist."""
        all_locks = await lock_manager.get_all_locks()
        assert len(all_locks) == 0


class TestLockManagerConcurrentAccess:
    """Tests for concurrent lock acquisition attempts."""

    @pytest.mark.asyncio
    async def test_concurrent_acquire_same_project(
        self, concurrent_lock_manager
    ):
        """Test the NEW slot-claim contract under contention.

        F10: rewrote from the old single-slot ``acquire()`` semantics
        (3 callers → 1 wins, 2 lose) to the new
        ``acquire_queue_lock`` contract with ``concurrency_limit=2``.

        Five concurrent callers race for the same (project_id,
        queue_id). The DB-level ``uq_job_locks_slot`` UNIQUE
        constraint enforces that at most ``concurrency_limit`` rows
        can exist per (project_id, queue_id). Exactly 2 acquire must
        succeed; the remaining 3 must fail. The DB lock count must
        equal 2 — proves the cap is held under fan-out, not just in
        the happy path.

        Uses ``concurrent_lock_manager`` (file-backed SQLite with
        default QueuePool) instead of the regular ``lock_manager``
        (in-memory + StaticPool) so each task gets its own SQLite
        connection. StaticPool shares one connection across tasks,
        which serialises cursor access and masks the race we want
        to exercise.
        """
        manager = concurrent_lock_manager

        async def try_acquire(job_id: str):
            return await manager.acquire_queue_lock(
                project_id="project-1",
                queue_id="queue-1",
                job_id=job_id,
                instance_id=f"instance-{job_id}",
                concurrency_limit=2,
            )

        results = await asyncio.gather(
            try_acquire("job-1"),
            try_acquire("job-2"),
            try_acquire("job-3"),
            try_acquire("job-4"),
            try_acquire("job-5"),
        )

        # Exactly 2 successes, 3 failures (concurrency_limit=2).
        assert results.count(True) == 2
        assert results.count(False) == 3
        # DB-level invariant: at most 2 lock rows exist for the
        # (project_id, queue_id) pair.
        assert manager._lock_repo.get_lock_count("project-1", "queue-1") == 2

    @pytest.mark.asyncio
    async def test_concurrent_acquire_different_projects(self, lock_manager):
        """Test concurrent acquisitions for different projects all succeed."""
        results = await asyncio.gather(
            lock_manager.acquire("project-1", "job-1", "instance-1"),
            lock_manager.acquire("project-2", "job-2", "instance-2"),
            lock_manager.acquire("project-3", "job-3", "instance-3"),
        )
        
        assert all(results)
        assert await lock_manager.get_waiter_count("project:project-1") == 1

    @pytest.mark.asyncio
    async def test_concurrent_acquire_and_release(self, lock_manager):
        """Test concurrent acquire and release operations."""
        # Acquire initial lock
        await lock_manager.acquire("project-1", "job-1", "instance-1")
        
        async def release_job():
            await asyncio.sleep(0.01)  # Small delay
            return await lock_manager.release("project-1", "job-1")
        
        async def acquire_after():
            await asyncio.sleep(0.02)  # Larger delay
            return await lock_manager.acquire("project-1", "job-2", "instance-2")
        
        # Start both operations concurrently
        release_result, acquire_result = await asyncio.gather(
            release_job(),
            acquire_after()
        )
        
        assert release_result is True
        # Acquire may succeed or fail depending on timing
        assert isinstance(acquire_result, bool)


class TestLockManagerWaitForLock:
    """Tests for wait_for_lock with waiter queue.
    
    NOTE: These tests are skipped because wait_for_lock() was removed
    in the queue-based redesign. Waiter functionality is no longer
    supported in the new API.
    """

    @pytest.mark.skip(reason="wait_for_lock() removed in queue-based redesign")
    @pytest.mark.asyncio
    async def test_wait_for_lock_immediate_acquire(self, lock_manager):
        """Test wait_for_lock when lock is immediately available."""
        result = await lock_manager.wait_for_lock(
            project_id="project-1",
            job_id="job-1",
            instance_id="instance-1"
        )
        assert result is True
        assert await lock_manager.is_locked("project-1") is True

    @pytest.mark.skip(reason="wait_for_lock() removed in queue-based redesign")
    @pytest.mark.asyncio
    async def test_wait_for_lock_waits_for_release(self, lock_manager):
        """Test wait_for_lock waits and acquires when lock released."""
        # First job holds lock
        await lock_manager.acquire("project-1", "job-1", "instance-1")
        
        async def wait_for_and_acquire():
            return await lock_manager.wait_for_lock(
                project_id="project-1",
                job_id="job-2",
                instance_id="instance-2"
            )
        
        async def release_after_delay():
            await asyncio.sleep(0.05)
            await lock_manager.release("project-1", "job-1")
        
        # Waiter should get the lock after release
        wait_result, _ = await asyncio.gather(
            wait_for_and_acquire(),
            release_after_delay()
        )
        
        assert wait_result is True
        assert await lock_manager.is_locked("project-1") is True
        
        # Verify the lock was acquired by job-2
        lock_info = await lock_manager.get_lock_info("project-1")
        assert lock_info.job_id == "job-2"

    @pytest.mark.skip(reason="wait_for_lock() removed in queue-based redesign")
    @pytest.mark.asyncio
    async def test_wait_for_lock_with_timeout(self, lock_manager):
        """Test wait_for_lock respects timeout."""
        # Hold lock indefinitely
        await lock_manager.acquire("project-1", "job-1", "instance-1")
        
        # Wait with very short timeout
        result = await lock_manager.wait_for_lock(
            project_id="project-1",
            job_id="job-2",
            instance_id="instance-2",
            timeout=0.05
        )
        
        assert result is False
        # Lock should still be held by job-1
        assert await lock_manager.is_locked("project-1") is True
        lock_info = await lock_manager.get_lock_info("project-1")
        assert lock_info.job_id == "job-1"

    @pytest.mark.skip(reason="wait_for_lock() removed in queue-based redesign")
    @pytest.mark.asyncio
    async def test_wait_for_lock_fifo_order(self, lock_manager):
        """Test waiters are notified in FIFO order."""
        # Hold lock
        await lock_manager.acquire("project-1", "job-1", "instance-1")
        
        acquired_jobs = []
        
        async def wait_and_acquire(job_id: str):
            result = await lock_manager.wait_for_lock(
                project_id="project-1",
                job_id=job_id,
                instance_id=f"instance-{job_id}"
            )
            if result:
                acquired_jobs.append(job_id)
        
        # Add waiters as tasks (they will block until lock is released)
        waiter_tasks = [
            asyncio.create_task(wait_and_acquire("job-2")),
            asyncio.create_task(wait_and_acquire("job-3")),
            asyncio.create_task(wait_and_acquire("job-4")),
        ]
        
        # Small delay to ensure all waiters are registered
        await asyncio.sleep(0.05)
        
        # Release the lock - this should unblock the first waiter (job-2)
        await lock_manager.release("project-1", "job-1")
        
        # Wait for waiters to complete (job-2 should get lock, others still waiting)
        await asyncio.sleep(0.1)
        
        # Only the first waiter (job-2) should have acquired the lock
        assert len(acquired_jobs) == 1
        assert acquired_jobs[0] == "job-2"
        
        # Cancel remaining waiters
        for task in waiter_tasks:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    @pytest.mark.skip(reason="wait_for_lock() removed in queue-based redesign")
    @pytest.mark.asyncio
    async def test_wait_for_lock_max_waiters(self):
        """Test max_waiters limit is enforced."""
        manager = JobLockManager(max_waiters=2)
        
        # Hold lock
        await manager.acquire("project-1", "job-1", "instance-1")
        
        # Add waiters as tasks (they will block until lock is released)
        waiter_tasks = [
            asyncio.create_task(manager.wait_for_lock("project-1", "job-2", "instance-2", timeout=2.0)),
            asyncio.create_task(manager.wait_for_lock("project-1", "job-3", "instance-3", timeout=2.0)),
        ]
        
        # Small delay to ensure waiters are registered
        await asyncio.sleep(0.1)
        
        # Release the lock - first waiter should acquire
        await manager.release("project-1", "job-1")
        
        # Wait for first waiter to complete (should succeed)
        result1 = await waiter_tasks[0]
        assert result1 is True
        
        # Release job-2's lock so job-3 can acquire
        await manager.release("project-1", "job-2")
        
        # Wait for second waiter
        result2 = await waiter_tasks[1]
        assert result2 is True
        
        # Now add a third waiter - should fail because max_waiters is enforced
        # when the lock is held (and we already have 2 in queue)
        await manager.acquire("project-1", "job-4", "instance-4")
        
        result3 = await manager.wait_for_lock("project-1", "job-5", "instance-5", timeout=0.1)
        assert result3 is False
        
        # Cleanup
        await manager.release("project-1", "job-4")


class TestLockManagerReleaseByInstance:
    """Tests for instance-based lock release."""

    @pytest.mark.asyncio
    async def test_release_by_instance_single_lock(self, lock_manager):
        """Test releasing lock by instance ID."""
        await lock_manager.acquire("project-1", "job-1", "instance-1")
        await lock_manager.acquire("project-2", "job-2", "instance-1")
        await lock_manager.acquire("project-3", "job-3", "instance-2")
        
        released = await lock_manager.release_by_instance("instance-1")
        
        # release_by_instance now returns list[tuple[str, str]]
        assert len(released) == 2
        released_keys = set(released)
        assert ("project-1", "project:project-1") in released_keys
        assert ("project-2", "project:project-2") in released_keys
        assert await lock_manager.is_locked("project-1") is False
        assert await lock_manager.is_locked("project-2") is False
        assert await lock_manager.is_locked("project-3") is True

    @pytest.mark.asyncio
    async def test_release_by_instance_no_matching(self, lock_manager):
        """Test release_by_instance with no matching instance."""
        await lock_manager.acquire("project-1", "job-1", "instance-1")
        
        released = await lock_manager.release_by_instance("instance-nonexistent")
        
        assert released == []
        assert await lock_manager.is_locked("project-1") is True

    @pytest.mark.asyncio
    async def test_release_by_instance_empty(self, lock_manager):
        """Test release_by_instance with no locks held."""
        released = await lock_manager.release_by_instance("instance-1")
        assert released == []


class TestLockManagerSyncMethods:
    """Tests for synchronous lock methods.
    
    NOTE: These tests are skipped because acquire_sync() and release_sync()
    were removed in the DB-only redesign. The manager now uses async-only operations.
    """

    @pytest.mark.skip(reason="acquire_sync() removed in DB-only redesign")
    def test_acquire_sync_success(self, lock_manager):
        """Test synchronous lock acquisition."""
        result = lock_manager.acquire_sync(
            project_id="project-1",
            job_id="job-1",
            instance_id="instance-1"
        )
        assert result is True
        # Verify in-memory state - keys are tuples (project_id, queue_id)
        queue_id = lock_manager._get_default_queue_id("project-1")
        assert lock_manager._queue_locks.get(("project-1", queue_id)) is not None

    @pytest.mark.skip(reason="acquire_sync() removed in DB-only redesign")
    def test_acquire_sync_already_held(self, lock_manager):
        """Test synchronous acquisition when already held."""
        lock_manager.acquire_sync("project-1", "job-1", "instance-1")
        
        result = lock_manager.acquire_sync("project-1", "job-2", "instance-2")
        assert result is False

    @pytest.mark.skip(reason="release_sync() removed in DB-only redesign")
    def test_release_sync_success(self, lock_manager):
        """Test synchronous lock release."""
        lock_manager.acquire_sync("project-1", "job-1", "instance-1")
        
        result = lock_manager.release_sync("project-1", "job-1")
        assert result is True
        queue_id = lock_manager._get_default_queue_id("project-1")
        assert lock_manager._queue_locks.get(("project-1", queue_id)) is None

    @pytest.mark.skip(reason="release_sync() removed in DB-only redesign")
    def test_release_sync_wrong_job(self, lock_manager):
        """Test sync release with wrong job_id."""
        lock_manager.acquire_sync("project-1", "job-1", "instance-1")
        
        result = lock_manager.release_sync("project-1", "job-2")
        assert result is False
        queue_id = lock_manager._get_default_queue_id("project-1")
        assert lock_manager._queue_locks.get(("project-1", queue_id)) is not None

    @pytest.mark.skip(reason="release_by_instance_sync() removed in queue-based redesign")
    def test_release_by_instance_sync(self, lock_manager):
        """Test synchronous release_by_instance."""
        lock_manager.acquire_sync("project-1", "job-1", "instance-1")
        lock_manager.acquire_sync("project-2", "job-2", "instance-1")
        lock_manager.acquire_sync("project-3", "job-3", "instance-2")
        
        released = lock_manager.release_by_instance_sync("instance-1")
        
        assert set(released) == {"project-1", "project-2"}
        queue_id_1 = lock_manager._get_default_queue_id("project-1")
        queue_id_2 = lock_manager._get_default_queue_id("project-2")
        queue_id_3 = lock_manager._get_default_queue_id("project-3")
        assert lock_manager._queue_locks.get(("project-1", queue_id_1)) is None
        assert lock_manager._queue_locks.get(("project-2", queue_id_2)) is None
        assert lock_manager._queue_locks.get(("project-3", queue_id_3)) is not None


class TestLockManagerContextManager:
    """Tests for lock_context context manager.
    
    NOTE: These tests are skipped because lock_context() was removed
    in the queue-based redesign.
    """

    @pytest.mark.skip(reason="lock_context() removed in queue-based redesign")
    @pytest.mark.asyncio
    async def test_lock_context_acquires_and_releases(self, lock_manager):
        """Test context manager acquires and releases lock."""
        async with lock_manager.lock_context(
            project_id="project-1",
            job_id="job-1",
            instance_id="instance-1"
        ) as acquired:
            assert acquired is True
            assert await lock_manager.is_locked("project-1") is True
        
        # Lock should be released after context exits
        assert await lock_manager.is_locked("project-1") is False

    @pytest.mark.skip(reason="lock_context() removed in queue-based redesign")
    @pytest.mark.asyncio
    async def test_lock_context_with_timeout(self, lock_manager):
        """Test context manager with timeout."""
        # Hold lock
        await lock_manager.acquire("project-1", "job-1", "instance-1")
        
        async with lock_manager.lock_context(
            project_id="project-1",
            job_id="job-2",
            instance_id="instance-2",
            timeout=0.05
        ) as acquired:
            assert acquired is False
        
        # Original lock should still be held
        assert await lock_manager.is_locked("project-1") is True

    @pytest.mark.skip(reason="lock_context() removed in queue-based redesign")
    @pytest.mark.asyncio
    async def test_lock_context_exception_releases(self, lock_manager):
        """Test context manager releases lock on exception."""
        with pytest.raises(ValueError):
            async with lock_manager.lock_context(
                project_id="project-1",
                job_id="job-1",
                instance_id="instance-1"
            ):
                assert await lock_manager.is_locked("project-1") is True
                raise ValueError("Test exception")
        
        # Lock should still be released despite exception
        assert await lock_manager.is_locked("project-1") is False


class TestLockManagerClear:
    """Tests for clear() method.
    
    NOTE: All tests are skipped because clear() was removed
    and now raises NotImplementedError. Use release_by_instance()
    for specific cleanup.
    """

    @pytest.mark.skip(reason="clear() removed - raises NotImplementedError in DB-only redesign")
    @pytest.mark.asyncio
    async def test_clear_removes_all_locks(self, lock_manager):
        """Test clear removes all locks and waiters."""
        await lock_manager.acquire("project-1", "job-1", "instance-1")
        await lock_manager.acquire("project-2", "job-2", "instance-2")
        
        lock_manager.clear()
        
        assert await lock_manager.is_locked("project-1") is False
        assert await lock_manager.is_locked("project-2") is False
        assert len(await lock_manager.get_all_locks()) == 0

    @pytest.mark.skip(reason="_waiters dict removed in queue-based redesign")
    @pytest.mark.asyncio
    async def test_clear_removes_waiters(self, lock_manager):
        """Test clear also removes waiters."""
        await lock_manager.acquire("project-1", "job-1", "instance-1")
        
        # Manually add waiters to the internal queue (bypassing wait_for_lock)
        waiter1 = asyncio.Event()
        waiter2 = asyncio.Event()
        lock_manager._waiters["project-1"] = [
            ("job-2", waiter1),
            ("job-3", waiter2),
        ]
        
        assert await lock_manager.get_waiter_count("project-1") == 2
        
        lock_manager.clear()
        
        assert await lock_manager.get_waiter_count("project-1") == 0


class TestLockManagerPerQueueLocking:
    """Tests for per-queue locking with configurable concurrency limits."""

    @pytest.mark.asyncio
    async def test_acquire_queue_lock_success(self, lock_manager):
        """Test successful acquisition of a queue lock."""
        result = await lock_manager.acquire_queue_lock(
            project_id="project-1",
            queue_id="queue-1",
            job_id="job-1",
            instance_id="instance-1",
            concurrency_limit=1,
        )
        assert result is True
        assert await lock_manager.is_queue_locked("project-1", "queue-1") is True
        assert await lock_manager.get_queue_lock_count("project-1", "queue-1") == 1

    @pytest.mark.asyncio
    async def test_acquire_queue_lock_respects_concurrency_limit(self, lock_manager):
        """Test that queue lock respects concurrency_limit (e.g., limit=2 allows 2 concurrent locks)."""
        # First job acquires lock
        result1 = await lock_manager.acquire_queue_lock(
            project_id="project-1",
            queue_id="queue-1",
            job_id="job-1",
            instance_id="instance-1",
            concurrency_limit=2,
        )
        assert result1 is True

        # Second job also acquires (limit=2)
        result2 = await lock_manager.acquire_queue_lock(
            project_id="project-1",
            queue_id="queue-1",
            job_id="job-2",
            instance_id="instance-2",
            concurrency_limit=2,
        )
        assert result2 is True
        assert await lock_manager.get_queue_lock_count("project-1", "queue-1") == 2

    @pytest.mark.asyncio
    async def test_acquire_queue_lock_exceeds_concurrency(self, lock_manager):
        """Test that acquiring lock fails when concurrency_limit is reached."""
        # Acquire first two slots (limit=2)
        await lock_manager.acquire_queue_lock(
            project_id="project-1",
            queue_id="queue-1",
            job_id="job-1",
            instance_id="instance-1",
            concurrency_limit=2,
        )
        await lock_manager.acquire_queue_lock(
            project_id="project-1",
            queue_id="queue-1",
            job_id="job-2",
            instance_id="instance-2",
            concurrency_limit=2,
        )

        # Third job should fail (limit exceeded)
        result = await lock_manager.acquire_queue_lock(
            project_id="project-1",
            queue_id="queue-1",
            job_id="job-3",
            instance_id="instance-3",
            concurrency_limit=2,
        )
        assert result is False
        assert await lock_manager.get_queue_lock_count("project-1", "queue-1") == 2

    @pytest.mark.asyncio
    async def test_release_queue_lock_frees_slot(self, lock_manager):
        """Test that releasing a queue lock frees the slot for next job."""
        # Acquire two slots (limit=2)
        await lock_manager.acquire_queue_lock(
            project_id="project-1",
            queue_id="queue-1",
            job_id="job-1",
            instance_id="instance-1",
            concurrency_limit=2,
        )
        await lock_manager.acquire_queue_lock(
            project_id="project-1",
            queue_id="queue-1",
            job_id="job-2",
            instance_id="instance-2",
            concurrency_limit=2,
        )
        assert await lock_manager.get_queue_lock_count("project-1", "queue-1") == 2

        # Release first job
        released = await lock_manager.release_queue_lock("project-1", "queue-1", "job-1")
        assert released is True
        assert await lock_manager.get_queue_lock_count("project-1", "queue-1") == 1

        # Now third job can acquire
        result = await lock_manager.acquire_queue_lock(
            project_id="project-1",
            queue_id="queue-1",
            job_id="job-3",
            instance_id="instance-3",
            concurrency_limit=2,
        )
        assert result is True
        assert await lock_manager.get_queue_lock_count("project-1", "queue-1") == 2

    @pytest.mark.asyncio
    async def test_different_queues_independent(self, lock_manager):
        """Test that locks on different queues don't interfere."""
        # Acquire full capacity on queue-1 (limit=1)
        await lock_manager.acquire_queue_lock(
            project_id="project-1",
            queue_id="queue-1",
            job_id="job-1",
            instance_id="instance-1",
            concurrency_limit=1,
        )

        # Acquire lock on different queue - should succeed
        result = await lock_manager.acquire_queue_lock(
            project_id="project-1",
            queue_id="queue-2",
            job_id="job-2",
            instance_id="instance-2",
            concurrency_limit=1,
        )
        assert result is True

        # Both queues should have 1 lock each
        assert await lock_manager.get_queue_lock_count("project-1", "queue-1") == 1
        assert await lock_manager.get_queue_lock_count("project-1", "queue-2") == 1

        # Release from queue-1 doesn't affect queue-2
        await lock_manager.release_queue_lock("project-1", "queue-1", "job-1")
        assert await lock_manager.get_queue_lock_count("project-1", "queue-1") == 0
        assert await lock_manager.get_queue_lock_count("project-1", "queue-2") == 1

    @pytest.mark.asyncio
    async def test_acquire_queue_lock_concurrent_safety(self, concurrent_lock_manager):
        """Test that multiple concurrent acquires don't exceed the concurrency limit."""
        acquired_count = 0
        lock_manager = concurrent_lock_manager

        async def try_acquire(job_id: str):
            nonlocal acquired_count
            result = await lock_manager.acquire_queue_lock(
                project_id="project-1",
                queue_id="queue-1",
                job_id=job_id,
                instance_id=f"instance-{job_id}",
                concurrency_limit=2,
            )
            if result:
                acquired_count += 1
            return result

        # Run many concurrent acquisitions with limit=2
        results = await asyncio.gather(
            try_acquire("job-1"),
            try_acquire("job-2"),
            try_acquire("job-3"),
            try_acquire("job-4"),
            try_acquire("job-5"),
        )

        # Only 2 should succeed due to concurrency limit
        assert acquired_count == 2
        assert results.count(True) == 2
        assert results.count(False) == 3
        assert await lock_manager.get_queue_lock_count("project-1", "queue-1") == 2


class TestLockManagerQueueHelpers:
    """Tests for queue helper methods."""

    @pytest.mark.asyncio
    async def test_is_queue_locked_true(self, lock_manager):
        """Test is_queue_locked returns True when queue has locks."""
        await lock_manager.acquire_queue_lock(
            project_id="project-1",
            queue_id="queue-1",
            job_id="job-1",
            instance_id="instance-1",
            concurrency_limit=1,
        )
        assert await lock_manager.is_queue_locked("project-1", "queue-1") is True

    @pytest.mark.asyncio
    async def test_is_queue_locked_false(self, lock_manager):
        """Test is_queue_locked returns False when no locks exist."""
        assert await lock_manager.is_queue_locked("project-1", "queue-1") is False

    @pytest.mark.asyncio
    async def test_get_queue_lock_count(self, lock_manager):
        """Test get_queue_lock_count returns correct count."""
        # Initially 0
        assert await lock_manager.get_queue_lock_count("project-1", "queue-1") == 0

        # Add locks
        await lock_manager.acquire_queue_lock(
            project_id="project-1",
            queue_id="queue-1",
            job_id="job-1",
            instance_id="instance-1",
            concurrency_limit=3,
        )
        await lock_manager.acquire_queue_lock(
            project_id="project-1",
            queue_id="queue-1",
            job_id="job-2",
            instance_id="instance-2",
            concurrency_limit=3,
        )
        assert await lock_manager.get_queue_lock_count("project-1", "queue-1") == 2

        # Release one
        await lock_manager.release_queue_lock("project-1", "queue-1", "job-1")
        assert await lock_manager.get_queue_lock_count("project-1", "queue-1") == 1

    def test_get_default_queue_id(self, lock_manager):
        """Test _get_default_queue_id returns consistent ID format."""
        result = lock_manager._get_default_queue_id("my-project")
        assert result == "project:my-project"

        result2 = lock_manager._get_default_queue_id("another-project")
        assert result2 == "project:another-project"


class TestLockManagerReleaseByInstanceQueueAware:
    """Tests for instance-based lock release with queue-aware returns."""

    @pytest.mark.asyncio
    async def test_release_by_instance_returns_queue_ids(self, lock_manager):
        """Test release_by_instance returns list of (project_id, queue_id) tuples."""
        await lock_manager.acquire_queue_lock(
            project_id="project-1",
            queue_id="queue-1",
            job_id="job-1",
            instance_id="instance-x",
            concurrency_limit=2,
        )
        await lock_manager.acquire_queue_lock(
            project_id="project-2",
            queue_id="queue-2",
            job_id="job-2",
            instance_id="instance-x",
            concurrency_limit=2,
        )

        released = await lock_manager.release_by_instance("instance-x")

        assert len(released) == 2
        released_keys = set(released)
        assert ("project-1", "queue-1") in released_keys
        assert ("project-2", "queue-2") in released_keys

    @pytest.mark.asyncio
    async def test_release_by_instance_frees_queue_slots(self, lock_manager):
        """Test that after release_by_instance, queue slots are available."""
        # Acquire full capacity
        await lock_manager.acquire_queue_lock(
            project_id="project-1",
            queue_id="queue-1",
            job_id="job-1",
            instance_id="instance-x",
            concurrency_limit=1,
        )
        assert await lock_manager.get_queue_lock_count("project-1", "queue-1") == 1

        # Release by instance
        released = await lock_manager.release_by_instance("instance-x")
        assert len(released) == 1

        # Slot should be freed - new job can acquire
        result = await lock_manager.acquire_queue_lock(
            project_id="project-1",
            queue_id="queue-1",
            job_id="job-2",
            instance_id="instance-y",
            concurrency_limit=1,
        )
        assert result is True
        assert await lock_manager.get_queue_lock_count("project-1", "queue-1") == 1


class TestLockManagerEdgeCases:
    """Tests for edge cases and error conditions."""

    @pytest.mark.asyncio
    async def test_empty_project_id(self, lock_manager):
        """Test lock operations with empty project ID."""
        result = await lock_manager.acquire(
            project_id="",
            job_id="job-1",
            instance_id="instance-1"
        )
        assert result is True
        
        assert await lock_manager.is_locked("") is True
        
        release_result = await lock_manager.release("", "job-1")
        assert release_result is True

    @pytest.mark.asyncio
    async def test_special_characters_in_ids(self, lock_manager):
        """Test with special characters in IDs."""
        special_project = "project/with/slashes"
        
        result = await lock_manager.acquire(
            project_id=special_project,
            job_id="job-1",
            instance_id="instance-1"
        )
        assert result is True
        
        info = await lock_manager.get_lock_info(special_project)
        assert info is not None
        assert info.project_id == special_project

    @pytest.mark.skip(reason="wait_for_lock() removed in queue-based redesign")
    @pytest.mark.asyncio
    async def test_waiter_count(self, lock_manager):
        """Test waiter count tracking."""
        await lock_manager.acquire("project-1", "job-1", "instance-1")
        
        assert await lock_manager.get_waiter_count("project-1") == 0
        
        # Add waiters (using acquire which should fail, then manually add)
        # Actually wait_for_lock adds to waiters when lock is held
        async def add_waiter():
            return await lock_manager.wait_for_lock(
                "project-1", f"job-waiter", f"instance-waiter"
            )
        
        # Note: wait_for_lock will return False but still add waiter for FIFO
        # Let's verify this behavior
        waiter_task = asyncio.create_task(add_waiter())
        await asyncio.sleep(0.01)  # Let it register
        
        # The waiter should be added even if it times out
        waiter_task.cancel()
        try:
            await waiter_task
        except asyncio.CancelledError:
            pass
        
        # Verify waiter count (may or may not have waiter depending on implementation)
        count = await lock_manager.get_waiter_count("project-1")
        assert count >= 0  # Just verify it returns a valid count

    @pytest.mark.skip(reason="wait_for_lock() removed in queue-based redesign")
    @pytest.mark.asyncio
    async def test_release_triggers_next_waiter(self, lock_manager):
        """Test that releasing lock notifies next waiter."""
        await lock_manager.acquire("project-1", "job-1", "instance-1")
        
        # Add waiter that will acquire when lock is released
        acquired_by_waiter = False
        
        async def waiter_job():
            nonlocal acquired_by_waiter
            result = await lock_manager.wait_for_lock(
                "project-1", "job-2", "instance-2", timeout=1.0
            )
            if result:
                acquired_by_waiter = True
            return result
        
        # Start waiter
        waiter = asyncio.create_task(waiter_job())
        
        # Wait for waiter to be registered
        await asyncio.sleep(0.05)
        
        # Release original lock
        await lock_manager.release("project-1", "job-1")
        
        # Wait for waiter to complete
        try:
            result = await asyncio.wait_for(waiter, timeout=0.5)
            assert result is True
            assert acquired_by_waiter is True
        except asyncio.TimeoutError:
            pytest.fail("Waiter was not notified after lock release")


# ============================================================================
# C5 — cross-process-safe acquire_queue_lock
# ============================================================================
#
# These tests verify the new atomic slot-claim loop:
# 1. Happy path: first acquire claims slot 0 and returns True.
# 2. Limit enforcement: after `limit` acquires, further acquires return False.
# 3. After release, the freed slot is reclaimable.
# 4. Slot column on the lock row reflects the claimed slot (0..limit-1).
# 5. Different queues don't compete for slots.
# 6. Same job_id can acquire multiple slots in different invocations
#    (this is preserved pre-existing non-idempotency, not a regression).

class TestAcquireQueueLockCrossProcessSafety:
    """Tests that acquire_queue_lock respects concurrency_limit
    even under contention."""

    @pytest.mark.asyncio
    async def test_first_acquire_claims_slot_zero(self, lock_manager):
        """First acquire for an empty queue claims slot 0."""
        ok = await lock_manager.acquire_queue_lock(
            project_id="p1", queue_id="q1", job_id="j1",
            instance_id="i1", concurrency_limit=2,
        )
        assert ok is True
        assert lock_manager._lock_repo.get_lock_count("p1", "q1") == 1
        locks = lock_manager._lock_repo.get_all_locks()
        assert locks[0].lock_slot == 0

    @pytest.mark.asyncio
    async def test_second_acquire_claims_slot_one(self, lock_manager):
        """Second acquire fills slot 1."""
        await lock_manager.acquire_queue_lock(
            project_id="p1", queue_id="q1", job_id="j1",
            instance_id="i1", concurrency_limit=2,
        )
        ok = await lock_manager.acquire_queue_lock(
            project_id="p1", queue_id="q1", job_id="j2",
            instance_id="i2", concurrency_limit=2,
        )
        assert ok is True
        assert lock_manager._lock_repo.get_lock_count("p1", "q1") == 2
        slots = sorted(l.lock_slot for l in lock_manager._lock_repo.get_all_locks())
        assert slots == [0, 1]

    @pytest.mark.asyncio
    async def test_third_acquire_at_limit_returns_false(self, lock_manager):
        """Once limit=2 slots are filled, a third acquire returns False."""
        await lock_manager.acquire_queue_lock(
            project_id="p1", queue_id="q1", job_id="j1",
            instance_id="i1", concurrency_limit=2,
        )
        await lock_manager.acquire_queue_lock(
            project_id="p1", queue_id="q1", job_id="j2",
            instance_id="i2", concurrency_limit=2,
        )
        ok = await lock_manager.acquire_queue_lock(
            project_id="p1", queue_id="q1", job_id="j3",
            instance_id="i3", concurrency_limit=2,
        )
        assert ok is False
        # Lock count remains exactly limit, not limit+1 (the bug).
        assert lock_manager._lock_repo.get_lock_count("p1", "q1") == 2

    @pytest.mark.asyncio
    async def test_release_reopens_slot_for_new_acquire(self, lock_manager):
        """After release, the freed slot is immediately reclaimable."""
        await lock_manager.acquire_queue_lock(
            project_id="p1", queue_id="q1", job_id="j1",
            instance_id="i1", concurrency_limit=2,
        )
        await lock_manager.acquire_queue_lock(
            project_id="p1", queue_id="q1", job_id="j2",
            instance_id="i2", concurrency_limit=2,
        )
        # j3 is rejected.
        ok_before = await lock_manager.acquire_queue_lock(
            project_id="p1", queue_id="q1", job_id="j3",
            instance_id="i3", concurrency_limit=2,
        )
        assert ok_before is False

        # Release j1's slot 0. j3 can now take slot 0.
        released = await lock_manager.release_queue_lock("p1", "q1", "j1")
        assert released is True

        ok_after = await lock_manager.acquire_queue_lock(
            project_id="p1", queue_id="q1", job_id="j3",
            instance_id="i3", concurrency_limit=2,
        )
        assert ok_after is True
        assert lock_manager._lock_repo.get_lock_count("p1", "q1") == 2

    @pytest.mark.asyncio
    async def test_different_queues_have_independent_slots(self, lock_manager):
        """Slot 0 in queue-A does not block slot 0 in queue-B."""
        await lock_manager.acquire_queue_lock(
            project_id="p1", queue_id="qA", job_id="j1",
            instance_id="i1", concurrency_limit=1,
        )
        ok = await lock_manager.acquire_queue_lock(
            project_id="p1", queue_id="qB", job_id="j2",
            instance_id="i2", concurrency_limit=1,
        )
        assert ok is True
        assert lock_manager._lock_repo.get_lock_count("p1", "qA") == 1
        assert lock_manager._lock_repo.get_lock_count("p1", "qB") == 1

    @pytest.mark.asyncio
    async def test_concurrent_acquires_never_exceed_limit(self, concurrent_lock_manager):
        """Several concurrent acquires for a queue with limit=2 yield
        exactly 2 successes.

        This is the direct regression test for C5: pre-fix, the
        in-process ``asyncio.Lock`` masked races within one process
        but two processes could both pass the count check. The new
        atomic slot-claim loop makes the cap a DB-enforced
        invariant. Even within one process the bound is now
        explicit (and visible in this test).

        NOTE: SQLite's StaticPool serialises cursor access across
        threads, so we keep concurrency low (3 acquires for limit=2)
        to avoid the InterfaceError that surfaces with very high
        fan-out against the in-memory test engine. The repo-level
        ``TestTryAcquireSlot`` tests cover the high-concurrency
        atomicity property directly.
        """
        lock_manager = concurrent_lock_manager

        async def try_acquire(job_id: str):
            return await lock_manager.acquire_queue_lock(
                project_id="p1", queue_id="q1", job_id=job_id,
                instance_id=f"inst-{job_id}", concurrency_limit=2,
            )

        results = await asyncio.gather(
            try_acquire("j1"),
            try_acquire("j2"),
            try_acquire("j3"),
        )
        assert results.count(True) == 2
        assert results.count(False) == 1
        assert lock_manager._lock_repo.get_lock_count("p1", "q1") == 2

    @pytest.mark.asyncio
    async def test_same_job_can_acquire_different_slot_after_release(self, lock_manager):
        """If a job holds slot 0 and releases it, it can acquire slot 0 again."""
        await lock_manager.acquire_queue_lock(
            project_id="p1", queue_id="q1", job_id="j1",
            instance_id="i1", concurrency_limit=2,
        )
        await lock_manager.acquire_queue_lock(
            project_id="p1", queue_id="q1", job_id="j2",
            instance_id="i2", concurrency_limit=2,
        )
        # j1 releases its slot 0.
        await lock_manager.release_queue_lock("p1", "q1", "j1")
        # j1 can claim slot 0 again.
        ok = await lock_manager.acquire_queue_lock(
            project_id="p1", queue_id="q1", job_id="j1",
            instance_id="i1", concurrency_limit=2,
        )
        assert ok is True
        assert lock_manager._lock_repo.get_lock_count("p1", "q1") == 2


# ============================================================================
# C12 — startup sweep / periodic cleanup at the manager level
# ============================================================================


class TestRecoverStaleJobLocks:
    """Tests for ``JobLockManager.recover_stale_job_locks``.

    Wraps ``LockRepository.clear_stale_job_locks``; verifies the
    async wrapper behaves correctly (returns the row count, can be
    called multiple times idempotently).
    """

    @pytest.mark.asyncio
    async def test_returns_zero_when_nothing_to_clear(self, lock_manager, repository):
        """Empty lock table → 0."""
        # No locks at all.
        cleared = await lock_manager.recover_stale_job_locks()
        assert cleared == 0

    @pytest.mark.asyncio
    async def test_returns_count_of_orphans_cleared(self, lock_manager):
        """Counts locks whose job is no longer active and returns the count."""
        # Two orphan locks (no backing job row at all). Distinct
        # lock_slot values required (uq_job_locks_slot UNIQUE).
        from daemon.repositories.job_queue.models import JobLock
        for idx, jid in enumerate(("orphan-1", "orphan-2")):
            lock = JobLock(
                project_id="p1", queue_id="q1", job_id=jid,
                instance_id=None, lock_slot=idx,
            )
            lock_manager._lock_repo.acquire(lock)

        cleared = await lock_manager.recover_stale_job_locks()
        assert cleared == 2
        assert lock_manager._lock_repo.get_all_locks() == []

    @pytest.mark.asyncio
    async def test_active_lock_survives_recover(self, lock_manager, repository):
        """A lock for a processing job is NOT cleared."""
        job = repository.create(
            agent_id="test-agent", agent_dir="./agents/test-agent",
            message="m", source="api", project_id="p1", queue_id="q1",
            priority=5, job_metadata=None,
        )
        from daemon.repositories.job_queue.models import JobLock
        lock = JobLock(
            project_id="p1", queue_id="q1", job_id=job.job_id,
            instance_id=None,
        )
        lock_manager._lock_repo.acquire(lock)

        cleared = await lock_manager.recover_stale_job_locks()
        assert cleared == 0
        assert len(lock_manager._lock_repo.get_all_locks()) == 1


class TestCleanupTerminalJobLocks:
    """Tests for ``JobLockManager.cleanup_terminal_job_locks`` (periodic variant)."""

    @pytest.mark.asyncio
    async def test_clears_terminal_status_locks(self, lock_manager, repository):
        """A lock for a job in 'completed' status is cleared."""
        job = repository.create(
            agent_id="test-agent", agent_dir="./agents/test-agent",
            message="m", source="api", project_id="p1", queue_id="q1",
            priority=5, job_metadata=None,
        )
        from sqlalchemy import text
        with repository.engine.begin() as conn:
            conn.execute(
                text("UPDATE job_queue_items SET status='completed' WHERE job_id=:id"),
                {"id": job.job_id},
            )
        from daemon.repositories.job_queue.models import JobLock
        lock = JobLock(
            project_id="p1", queue_id="q1", job_id=job.job_id,
            instance_id=None,
        )
        lock_manager._lock_repo.acquire(lock)

        cleared = await lock_manager.cleanup_terminal_job_locks()
        assert cleared == 1
        assert lock_manager._lock_repo.get_all_locks() == []

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_terminal_locks(self, lock_manager, repository):
        """Active jobs are not cleared by the periodic cleanup."""
        job = repository.create(
            agent_id="test-agent", agent_dir="./agents/test-agent",
            message="m", source="api", project_id="p1", queue_id="q1",
            priority=5, job_metadata=None,
        )
        from daemon.repositories.job_queue.models import JobLock
        lock = JobLock(
            project_id="p1", queue_id="q1", job_id=job.job_id,
            instance_id=None,
        )
        lock_manager._lock_repo.acquire(lock)

        cleared = await lock_manager.cleanup_terminal_job_locks()
        assert cleared == 0
        assert len(lock_manager._lock_repo.get_all_locks()) == 1
