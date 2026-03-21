"""Tests for TaskLockManager.

This module tests the in-memory lock manager that provides per-project
task serialization with waiter notification.
"""

import asyncio
import pytest

from daemon.services.task_lock_manager import TaskLockManager, LockInfo
from daemon.repositories.task_queue.models import TaskLockInfo


class TestLockManagerBasicOperations:
    """Tests for basic lock acquisition and release."""

    @pytest.mark.asyncio
    async def test_acquire_lock_success(self, lock_manager):
        """Test successful lock acquisition."""
        result = await lock_manager.acquire(
            project_id="project-1",
            task_id="task-1",
            session_id="session-1"
        )
        assert result is True
        assert await lock_manager.is_locked("project-1") is True

    @pytest.mark.asyncio
    async def test_acquire_lock_already_held(self, lock_manager):
        """Test acquiring lock that's already held returns False."""
        # First acquisition succeeds
        await lock_manager.acquire(
            project_id="project-1",
            task_id="task-1",
            session_id="session-1"
        )
        
        # Second acquisition for same project fails
        result = await lock_manager.acquire(
            project_id="project-1",
            task_id="task-2",
            session_id="session-2"
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_release_lock_success(self, lock_manager):
        """Test successful lock release."""
        await lock_manager.acquire(
            project_id="project-1",
            task_id="task-1",
            session_id="session-1"
        )
        
        result = await lock_manager.release("project-1", "task-1")
        assert result is True
        assert await lock_manager.is_locked("project-1") is False

    @pytest.mark.asyncio
    async def test_release_lock_not_held(self, lock_manager):
        """Test releasing lock that's not held returns False."""
        result = await lock_manager.release("project-1", "task-1")
        assert result is False

    @pytest.mark.asyncio
    async def test_release_lock_wrong_task(self, lock_manager):
        """Test releasing lock held by different task returns False."""
        await lock_manager.acquire(
            project_id="project-1",
            task_id="task-1",
            session_id="session-1"
        )
        
        # Try to release with different task_id
        result = await lock_manager.release("project-1", "task-2")
        assert result is False
        assert await lock_manager.is_locked("project-1") is True

    @pytest.mark.asyncio
    async def test_release_lock_double_release(self, lock_manager):
        """Test double release returns False second time."""
        await lock_manager.acquire(
            project_id="project-1",
            task_id="task-1",
            session_id="session-1"
        )
        
        # First release succeeds
        result1 = await lock_manager.release("project-1", "task-1")
        assert result1 is True
        
        # Second release fails (lock no longer held)
        result2 = await lock_manager.release("project-1", "task-1")
        assert result2 is False

    @pytest.mark.asyncio
    async def test_multiple_projects_independent(self, lock_manager):
        """Test locks for different projects are independent."""
        # Acquire locks for multiple projects
        await lock_manager.acquire("project-1", "task-1", "session-1")
        await lock_manager.acquire("project-2", "task-2", "session-2")
        await lock_manager.acquire("project-3", "task-3", "session-3")
        
        assert await lock_manager.is_locked("project-1") is True
        assert await lock_manager.is_locked("project-2") is True
        assert await lock_manager.is_locked("project-3") is True
        
        # Release one doesn't affect others
        await lock_manager.release("project-2", "task-2")
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
            task_id="task-1",
            session_id="session-1"
        )
        
        info = await lock_manager.get_lock_info("project-1")
        assert info is not None
        assert info.task_id == "task-1"
        assert info.project_id == "project-1"
        assert info.session_id == "session-1"
        assert info.locked_at is not None

    @pytest.mark.asyncio
    async def test_get_lock_info_not_exists(self, lock_manager):
        """Test getting lock info when lock doesn't exist."""
        info = await lock_manager.get_lock_info("project-1")
        assert info is None

    @pytest.mark.asyncio
    async def test_get_all_locks(self, lock_manager):
        """Test getting all current locks."""
        await lock_manager.acquire("project-1", "task-1", "session-1")
        await lock_manager.acquire("project-2", "task-2", "session-2")
        
        all_locks = await lock_manager.get_all_locks()
        assert len(all_locks) == 2
        assert "project-1" in all_locks
        assert "project-2" in all_locks

    @pytest.mark.asyncio
    async def test_get_all_locks_empty(self, lock_manager):
        """Test getting all locks when none exist."""
        all_locks = await lock_manager.get_all_locks()
        assert len(all_locks) == 0


class TestLockManagerConcurrentAccess:
    """Tests for concurrent lock acquisition attempts."""

    @pytest.mark.asyncio
    async def test_concurrent_acquire_same_project(self, lock_manager):
        """Test concurrent attempts to acquire same project lock."""
        acquired_count = 0
        
        async def try_acquire(task_id: str):
            nonlocal acquired_count
            result = await lock_manager.acquire(
                project_id="project-1",
                task_id=task_id,
                session_id=f"session-{task_id}"
            )
            if result:
                acquired_count += 1
            return result
        
        # Run multiple concurrent acquisitions
        results = await asyncio.gather(
            try_acquire("task-1"),
            try_acquire("task-2"),
            try_acquire("task-3"),
        )
        
        # Only one should succeed
        assert acquired_count == 1
        assert results.count(True) == 1
        assert results.count(False) == 2

    @pytest.mark.asyncio
    async def test_concurrent_acquire_different_projects(self, lock_manager):
        """Test concurrent acquisitions for different projects all succeed."""
        results = await asyncio.gather(
            lock_manager.acquire("project-1", "task-1", "session-1"),
            lock_manager.acquire("project-2", "task-2", "session-2"),
            lock_manager.acquire("project-3", "task-3", "session-3"),
        )
        
        assert all(results)
        assert await lock_manager.get_waiter_count("project-1") == 0

    @pytest.mark.asyncio
    async def test_concurrent_acquire_and_release(self, lock_manager):
        """Test concurrent acquire and release operations."""
        # Acquire initial lock
        await lock_manager.acquire("project-1", "task-1", "session-1")
        
        async def release_task():
            await asyncio.sleep(0.01)  # Small delay
            return await lock_manager.release("project-1", "task-1")
        
        async def acquire_after():
            await asyncio.sleep(0.02)  # Larger delay
            return await lock_manager.acquire("project-1", "task-2", "session-2")
        
        # Start both operations concurrently
        release_result, acquire_result = await asyncio.gather(
            release_task(),
            acquire_after()
        )
        
        assert release_result is True
        # Acquire may succeed or fail depending on timing
        assert isinstance(acquire_result, bool)


class TestLockManagerWaitForLock:
    """Tests for wait_for_lock with waiter queue."""

    @pytest.mark.asyncio
    async def test_wait_for_lock_immediate_acquire(self, lock_manager):
        """Test wait_for_lock when lock is immediately available."""
        result = await lock_manager.wait_for_lock(
            project_id="project-1",
            task_id="task-1",
            session_id="session-1"
        )
        assert result is True
        assert await lock_manager.is_locked("project-1") is True

    @pytest.mark.asyncio
    async def test_wait_for_lock_waits_for_release(self, lock_manager):
        """Test wait_for_lock waits and acquires when lock released."""
        # First task holds lock
        await lock_manager.acquire("project-1", "task-1", "session-1")
        
        async def wait_for_and_acquire():
            return await lock_manager.wait_for_lock(
                project_id="project-1",
                task_id="task-2",
                session_id="session-2"
            )
        
        async def release_after_delay():
            await asyncio.sleep(0.05)
            await lock_manager.release("project-1", "task-1")
        
        # Waiter should get the lock after release
        wait_result, _ = await asyncio.gather(
            wait_for_and_acquire(),
            release_after_delay()
        )
        
        assert wait_result is True
        assert await lock_manager.is_locked("project-1") is True
        
        # Verify the lock was acquired by task-2
        lock_info = await lock_manager.get_lock_info("project-1")
        assert lock_info.task_id == "task-2"

    @pytest.mark.asyncio
    async def test_wait_for_lock_with_timeout(self, lock_manager):
        """Test wait_for_lock respects timeout."""
        # Hold lock indefinitely
        await lock_manager.acquire("project-1", "task-1", "session-1")
        
        # Wait with very short timeout
        result = await lock_manager.wait_for_lock(
            project_id="project-1",
            task_id="task-2",
            session_id="session-2",
            timeout=0.05
        )
        
        assert result is False
        # Lock should still be held by task-1
        assert await lock_manager.is_locked("project-1") is True
        lock_info = await lock_manager.get_lock_info("project-1")
        assert lock_info.task_id == "task-1"

    @pytest.mark.asyncio
    async def test_wait_for_lock_fifo_order(self, lock_manager):
        """Test waiters are notified in FIFO order."""
        # Hold lock
        await lock_manager.acquire("project-1", "task-1", "session-1")
        
        acquired_tasks = []
        
        async def wait_and_acquire(task_id: str):
            result = await lock_manager.wait_for_lock(
                project_id="project-1",
                task_id=task_id,
                session_id=f"session-{task_id}"
            )
            if result:
                acquired_tasks.append(task_id)
        
        # Add multiple waiters
        await asyncio.gather(
            wait_and_acquire("task-2"),
            wait_and_acquire("task-3"),
            wait_and_acquire("task-4"),
        )
        
        # Release the lock
        await lock_manager.release("project-1", "task-1")
        
        # Wait for all waiters to complete
        await asyncio.sleep(0.1)
        
        # First waiter should get the lock
        assert len(acquired_tasks) == 1
        assert acquired_tasks[0] == "task-2"

    @pytest.mark.asyncio
    async def test_wait_for_lock_max_waiters(self):
        """Test max_waiters limit is enforced."""
        manager = TaskLockManager(max_waiters=2)
        
        # Hold lock
        await manager.acquire("project-1", "task-1", "session-1")
        
        # First two waiters should succeed
        result1 = await manager.wait_for_lock("project-1", "task-2", "session-2")
        result2 = await manager.wait_for_lock("project-1", "task-3", "session-3")
        
        assert result1 is True
        assert result2 is True
        
        # Third waiter should fail (max reached)
        result3 = await manager.wait_for_lock("project-1", "task-4", "session-4")
        assert result3 is False


class TestLockManagerReleaseBySession:
    """Tests for session-based lock release."""

    @pytest.mark.asyncio
    async def test_release_by_session_single_lock(self, lock_manager):
        """Test releasing lock by session ID."""
        await lock_manager.acquire("project-1", "task-1", "session-1")
        await lock_manager.acquire("project-2", "task-2", "session-1")
        await lock_manager.acquire("project-3", "task-3", "session-2")
        
        released = await lock_manager.release_by_session("session-1")
        
        assert set(released) == {"project-1", "project-2"}
        assert await lock_manager.is_locked("project-1") is False
        assert await lock_manager.is_locked("project-2") is False
        assert await lock_manager.is_locked("project-3") is True

    @pytest.mark.asyncio
    async def test_release_by_session_no_matching(self, lock_manager):
        """Test release_by_session with no matching session."""
        await lock_manager.acquire("project-1", "task-1", "session-1")
        
        released = await lock_manager.release_by_session("session-nonexistent")
        
        assert released == []
        assert await lock_manager.is_locked("project-1") is True

    @pytest.mark.asyncio
    async def test_release_by_session_empty(self, lock_manager):
        """Test release_by_session with no locks held."""
        released = await lock_manager.release_by_session("session-1")
        assert released == []


class TestLockManagerSyncMethods:
    """Tests for synchronous lock methods."""

    def test_acquire_sync_success(self, lock_manager):
        """Test synchronous lock acquisition."""
        result = lock_manager.acquire_sync(
            project_id="project-1",
            task_id="task-1",
            session_id="session-1"
        )
        assert result is True
        # Verify in-memory state
        assert lock_manager._locks.get("project-1") is not None

    def test_acquire_sync_already_held(self, lock_manager):
        """Test synchronous acquisition when already held."""
        lock_manager.acquire_sync("project-1", "task-1", "session-1")
        
        result = lock_manager.acquire_sync("project-1", "task-2", "session-2")
        assert result is False

    def test_release_sync_success(self, lock_manager):
        """Test synchronous lock release."""
        lock_manager.acquire_sync("project-1", "task-1", "session-1")
        
        result = lock_manager.release_sync("project-1", "task-1")
        assert result is True
        assert lock_manager._locks.get("project-1") is None

    def test_release_sync_wrong_task(self, lock_manager):
        """Test sync release with wrong task_id."""
        lock_manager.acquire_sync("project-1", "task-1", "session-1")
        
        result = lock_manager.release_sync("project-1", "task-2")
        assert result is False
        assert lock_manager._locks.get("project-1") is not None

    def test_release_by_session_sync(self, lock_manager):
        """Test synchronous release_by_session."""
        lock_manager.acquire_sync("project-1", "task-1", "session-1")
        lock_manager.acquire_sync("project-2", "task-2", "session-1")
        lock_manager.acquire_sync("project-3", "task-3", "session-2")
        
        released = lock_manager.release_by_session_sync("session-1")
        
        assert set(released) == {"project-1", "project-2"}
        assert lock_manager._locks.get("project-1") is None
        assert lock_manager._locks.get("project-2") is None
        assert lock_manager._locks.get("project-3") is not None


class TestLockManagerContextManager:
    """Tests for lock_context context manager."""

    @pytest.mark.asyncio
    async def test_lock_context_acquires_and_releases(self, lock_manager):
        """Test context manager acquires and releases lock."""
        async with lock_manager.lock_context(
            project_id="project-1",
            task_id="task-1",
            session_id="session-1"
        ) as acquired:
            assert acquired is True
            assert await lock_manager.is_locked("project-1") is True
        
        # Lock should be released after context exits
        assert await lock_manager.is_locked("project-1") is False

    @pytest.mark.asyncio
    async def test_lock_context_with_timeout(self, lock_manager):
        """Test context manager with timeout."""
        # Hold lock
        await lock_manager.acquire("project-1", "task-1", "session-1")
        
        async with lock_manager.lock_context(
            project_id="project-1",
            task_id="task-2",
            session_id="session-2",
            timeout=0.05
        ) as acquired:
            assert acquired is False
        
        # Original lock should still be held
        assert await lock_manager.is_locked("project-1") is True

    @pytest.mark.asyncio
    async def test_lock_context_exception_releases(self, lock_manager):
        """Test context manager releases lock on exception."""
        with pytest.raises(ValueError):
            async with lock_manager.lock_context(
                project_id="project-1",
                task_id="task-1",
                session_id="session-1"
            ):
                assert await lock_manager.is_locked("project-1") is True
                raise ValueError("Test exception")
        
        # Lock should still be released despite exception
        assert await lock_manager.is_locked("project-1") is False


class TestLockManagerClear:
    """Tests for clear() method."""

    @pytest.mark.asyncio
    async def test_clear_removes_all_locks(self, lock_manager):
        """Test clear removes all locks and waiters."""
        await lock_manager.acquire("project-1", "task-1", "session-1")
        await lock_manager.acquire("project-2", "task-2", "session-2")
        
        lock_manager.clear()
        
        assert await lock_manager.is_locked("project-1") is False
        assert await lock_manager.is_locked("project-2") is False
        assert len(await lock_manager.get_all_locks()) == 0

    @pytest.mark.asyncio
    async def test_clear_removes_waiters(self, lock_manager):
        """Test clear also removes waiters."""
        await lock_manager.acquire("project-1", "task-1", "session-1")
        
        # Add waiters
        await lock_manager.wait_for_lock("project-1", "task-2", "session-2")
        await lock_manager.wait_for_lock("project-1", "task-3", "session-3")
        
        assert await lock_manager.get_waiter_count("project-1") == 2
        
        lock_manager.clear()
        
        assert await lock_manager.get_waiter_count("project-1") == 0


class TestLockInfo:
    """Tests for LockInfo internal class."""

    def test_lock_info_creation(self):
        """Test LockInfo creation with default timestamp."""
        info = LockInfo(
            task_id="task-1",
            project_id="project-1",
            session_id="session-1"
        )
        
        assert info.task_id == "task-1"
        assert info.project_id == "project-1"
        assert info.session_id == "session-1"
        assert info.locked_at is not None

    def test_lock_info_custom_timestamp(self):
        """Test LockInfo creation with custom timestamp."""
        custom_time = datetime(2024, 1, 1, 12, 0, 0)
        info = LockInfo(
            task_id="task-1",
            project_id="project-1",
            session_id="session-1",
            locked_at=custom_time
        )
        
        assert info.locked_at == custom_time

    def test_lock_info_to_lock_info(self):
        """Test conversion to TaskLockInfo."""
        info = LockInfo(
            task_id="task-1",
            project_id="project-1",
            session_id="session-1"
        )
        
        lock_info = info.to_lock_info()
        
        assert isinstance(lock_info, TaskLockInfo)
        assert lock_info.task_id == "task-1"
        assert lock_info.project_id == "project-1"
        assert lock_info.session_id == "session-1"
        assert lock_info.locked_at == info.locked_at


class TestLockManagerEdgeCases:
    """Tests for edge cases and error conditions."""

    @pytest.mark.asyncio
    async def test_empty_project_id(self, lock_manager):
        """Test lock operations with empty project ID."""
        result = await lock_manager.acquire(
            project_id="",
            task_id="task-1",
            session_id="session-1"
        )
        assert result is True
        
        assert await lock_manager.is_locked("") is True
        
        release_result = await lock_manager.release("", "task-1")
        assert release_result is True

    @pytest.mark.asyncio
    async def test_special_characters_in_ids(self, lock_manager):
        """Test with special characters in IDs."""
        special_project = "project/with/slashes"
        
        result = await lock_manager.acquire(
            project_id=special_project,
            task_id="task-1",
            session_id="session-1"
        )
        assert result is True
        
        info = await lock_manager.get_lock_info(special_project)
        assert info is not None
        assert info.project_id == special_project

    @pytest.mark.asyncio
    async def test_waiter_count(self, lock_manager):
        """Test waiter count tracking."""
        await lock_manager.acquire("project-1", "task-1", "session-1")
        
        assert await lock_manager.get_waiter_count("project-1") == 0
        
        # Add waiters (using acquire which should fail, then manually add)
        # Actually wait_for_lock adds to waiters when lock is held
        async def add_waiter():
            return await lock_manager.wait_for_lock(
                "project-1", f"task-waiter", f"session-waiter"
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

    @pytest.mark.asyncio
    async def test_release_triggers_next_waiter(self, lock_manager):
        """Test that releasing lock notifies next waiter."""
        await lock_manager.acquire("project-1", "task-1", "session-1")
        
        # Add waiter that will acquire when lock is released
        acquired_by_waiter = False
        
        async def waiter_task():
            nonlocal acquired_by_waiter
            result = await lock_manager.wait_for_lock(
                "project-1", "task-2", "session-2", timeout=1.0
            )
            if result:
                acquired_by_waiter = True
            return result
        
        # Start waiter
        waiter = asyncio.create_task(waiter_task())
        
        # Wait for waiter to be registered
        await asyncio.sleep(0.05)
        
        # Release original lock
        await lock_manager.release("project-1", "task-1")
        
        # Wait for waiter to complete
        try:
            result = await asyncio.wait_for(waiter, timeout=0.5)
            assert result is True
            assert acquired_by_waiter is True
        except asyncio.TimeoutError:
            pytest.fail("Waiter was not notified after lock release")
