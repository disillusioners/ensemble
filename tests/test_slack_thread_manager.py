"""Tests for Slack ThreadManager with TTL and LRU eviction."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon.sources.adapters.slack.thread_manager import ThreadManager, ThreadInstance


@pytest.fixture
def mock_manager():
    """Create a mock InstanceManager with async spawn and terminate."""
    manager = MagicMock()
    manager.spawn_instance = AsyncMock(return_value="test-instance-123")
    manager.terminate_instance = AsyncMock(return_value=None)
    return manager


@pytest.fixture
def thread_manager(mock_manager):
    """Create a ThreadManager with short TTL for testing."""
    return ThreadManager(
        manager=mock_manager,
        ttl_seconds=0.1,  # 100ms TTL for fast expiry tests
        max_threads_per_workspace=3,  # Small capacity for LRU eviction tests
    )


class TestRegisterThread:
    """Tests for _register_thread_unlocked method."""

    @pytest.mark.asyncio
    async def test_register_thread_creates_thread_instance(self, thread_manager, mock_manager):
        """Test that _register_thread_unlocked creates a new ThreadInstance."""
        # Act
        thread = await thread_manager._register_thread_unlocked(
            workspace_id="WS001",
            channel_id="C001",
            thread_ts="1234567890.123456",
            instance_id="instance-1",
        )

        # Assert
        assert isinstance(thread, ThreadInstance)
        assert thread.workspace_id == "WS001"
        assert thread.channel_id == "C001"
        assert thread.thread_ts == "1234567890.123456"
        assert thread.instance_id == "instance-1"
        assert thread.created_at > 0
        assert thread.last_accessed > 0
        assert thread.created_at == thread.last_accessed

    @pytest.mark.asyncio
    async def test_register_thread_updates_existing_thread(self, thread_manager):
        """Test that registering same thread updates existing instead of creating new."""
        # Arrange - register first thread
        thread1 = await thread_manager._register_thread_unlocked(
            workspace_id="WS001",
            channel_id="C001",
            thread_ts="1234567890.123456",
            instance_id="instance-1",
        )
        original_created_at = thread1.created_at
        await asyncio.sleep(0.01)  # Small delay to ensure different timestamps

        # Act - register same thread with different instance
        thread2 = await thread_manager._register_thread_unlocked(
            workspace_id="WS001",
            channel_id="C001",
            thread_ts="1234567890.123456",
            instance_id="instance-2",
        )

        # Assert - should be same object, updated
        assert thread2 is thread1  # Same object reference
        assert thread2.instance_id == "instance-2"
        assert thread2.created_at == original_created_at  # Created at unchanged
        assert thread2.last_accessed >= thread2.created_at  # Last accessed updated
        assert thread2.last_accessed > original_created_at  # Actually updated

    @pytest.mark.asyncio
    async def test_register_thread_adds_to_workspace(self, thread_manager):
        """Test that threads are properly added to workspace storage."""
        # Register multiple threads
        await thread_manager._register_thread_unlocked(
            workspace_id="WS001",
            channel_id="C001",
            thread_ts="thread-1",
            instance_id="instance-1",
        )
        await thread_manager._register_thread_unlocked(
            workspace_id="WS001",
            channel_id="C002",
            thread_ts="thread-2",
            instance_id="instance-2",
        )
        await thread_manager._register_thread_unlocked(
            workspace_id="WS002",
            channel_id="C003",
            thread_ts="thread-3",
            instance_id="instance-3",
        )

        # Assert - check stats
        stats = await thread_manager.get_stats()
        assert stats["total_threads"] == 3
        assert stats["workspaces"] == 2
        assert stats["threads_per_workspace"]["WS001"] == 2
        assert stats["threads_per_workspace"]["WS002"] == 1

    @pytest.mark.asyncio
    async def test_register_thread_tracks_instance_mapping(self, thread_manager):
        """Test that instance_id to thread mapping is tracked."""
        # Register a thread
        await thread_manager._register_thread_unlocked(
            workspace_id="WS001",
            channel_id="C001",
            thread_ts="thread-1",
            instance_id="instance-1",
        )

        # Verify instance mapping exists
        assert "instance-1" in thread_manager._instance_to_thread
        assert thread_manager._instance_to_thread["instance-1"] == ("WS001", "thread-1")


class TestGetThread:
    """Tests for _get_thread_unlocked method."""

    @pytest.mark.asyncio
    async def test_get_thread_returns_none_for_nonexistent(self, thread_manager):
        """Test that _get_thread_unlocked returns None for non-existent thread."""
        async with thread_manager._threads_guard:
            result = await thread_manager._get_thread_unlocked(
                workspace_id="WS999",
                thread_ts="nonexistent",
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_get_thread_returns_registered_thread(self, thread_manager):
        """Test that _get_thread_unlocked returns the registered thread."""
        # Register a thread
        await thread_manager._register_thread_unlocked(
            workspace_id="WS001",
            channel_id="C001",
            thread_ts="thread-1",
            instance_id="instance-1",
        )

        # Act
        async with thread_manager._threads_guard:
            result = await thread_manager._get_thread_unlocked(
                workspace_id="WS001",
                thread_ts="thread-1",
            )

        # Assert
        assert result is not None
        assert isinstance(result, ThreadInstance)
        assert result.instance_id == "instance-1"

    @pytest.mark.asyncio
    async def test_get_thread_updates_last_accessed(self, thread_manager):
        """Test that _get_thread_unlocked updates last_accessed timestamp."""
        # Register a thread
        thread = await thread_manager._register_thread_unlocked(
            workspace_id="WS001",
            channel_id="C001",
            thread_ts="thread-1",
            instance_id="instance-1",
        )
        original_last_accessed = thread.last_accessed

        await asyncio.sleep(0.01)  # Ensure time passes

        # Get thread (should update last_accessed)
        async with thread_manager._threads_guard:
            result = await thread_manager._get_thread_unlocked(
                workspace_id="WS001",
                thread_ts="thread-1",
            )

        # Assert
        assert result.last_accessed > original_last_accessed

    @pytest.mark.asyncio
    async def test_get_thread_moves_to_end_lru(self, thread_manager):
        """Test that _get_thread_unlocked moves thread to end (most recently used)."""
        # Register multiple threads
        await thread_manager._register_thread_unlocked(
            workspace_id="WS001",
            channel_id="C001",
            thread_ts="thread-1",
            instance_id="instance-1",
        )
        await asyncio.sleep(0.01)
        await thread_manager._register_thread_unlocked(
            workspace_id="WS001",
            channel_id="C002",
            thread_ts="thread-2",
            instance_id="instance-2",
        )
        await asyncio.sleep(0.01)
        await thread_manager._register_thread_unlocked(
            workspace_id="WS001",
            channel_id="C003",
            thread_ts="thread-3",
            instance_id="instance-3",
        )

        # Access first thread (should move to end)
        async with thread_manager._threads_guard:
            await thread_manager._get_thread_unlocked(workspace_id="WS001", thread_ts="thread-1")

        # Verify ordering - thread-1 should be last (most recently used)
        workspace_threads = thread_manager._threads["WS001"]
        ordered_keys = list(workspace_threads.keys())
        assert ordered_keys == ["thread-2", "thread-3", "thread-1"]

    @pytest.mark.asyncio
    async def test_get_thread_returns_none_for_expired(self, thread_manager):
        """Test TTL expiry - _get_thread_unlocked returns None for expired thread."""
        # Register a thread
        await thread_manager._register_thread_unlocked(
            workspace_id="WS001",
            channel_id="C001",
            thread_ts="thread-1",
            instance_id="instance-1",
        )

        # Wait for TTL to expire (TTL is 0.1 seconds)
        await asyncio.sleep(0.15)

        # Act - should return None and clean up expired thread
        async with thread_manager._threads_guard:
            result = await thread_manager._get_thread_unlocked(
                workspace_id="WS001",
                thread_ts="thread-1",
            )

        # Assert
        assert result is None

        # Verify thread was cleaned up
        stats = await thread_manager.get_stats()
        assert stats["total_threads"] == 0

    @pytest.mark.asyncio
    async def test_get_thread_removes_instance_mapping_on_expired(self, thread_manager):
        """Test that instance mapping is removed when thread expires."""
        # Register a thread
        await thread_manager._register_thread_unlocked(
            workspace_id="WS001",
            channel_id="C001",
            thread_ts="thread-1",
            instance_id="instance-1",
        )

        # Verify mapping exists
        assert "instance-1" in thread_manager._instance_to_thread

        # Wait for TTL to expire
        await asyncio.sleep(0.15)

        # Trigger expiry check via get
        async with thread_manager._threads_guard:
            await thread_manager._get_thread_unlocked(workspace_id="WS001", thread_ts="thread-1")

        # Verify mapping was cleaned up
        assert "instance-1" not in thread_manager._instance_to_thread


class TestLRUEviction:
    """Tests for LRU eviction at capacity."""

    @pytest.mark.asyncio
    async def test_lru_eviction_at_capacity(self, thread_manager, mock_manager):
        """Test that oldest thread is evicted when max capacity reached."""
        # Register max_threads threads
        for i in range(3):
            await thread_manager._register_thread_unlocked(
                workspace_id="WS001",
                channel_id=f"C00{i}",
                thread_ts=f"thread-{i}",
                instance_id=f"instance-{i}",
            )

        # Verify capacity is full
        stats = await thread_manager.get_stats()
        assert stats["total_threads"] == 3

        # Register new thread (should trigger eviction)
        await thread_manager._register_thread_unlocked(
            workspace_id="WS001",
            channel_id="C999",
            thread_ts="thread-new",
            instance_id="instance-new",
        )

        # Assert - total should still be 3, thread-0 evicted
        stats = await thread_manager.get_stats()
        assert stats["total_threads"] == 3

        # Verify evicted thread is gone
        async with thread_manager._threads_guard:
            result = await thread_manager._get_thread_unlocked(
                workspace_id="WS001",
                thread_ts="thread-0",
            )
        assert result is None

        # Verify new thread exists
        async with thread_manager._threads_guard:
            result = await thread_manager._get_thread_unlocked(
                workspace_id="WS001",
                thread_ts="thread-new",
            )
        assert result is not None
        assert result.instance_id == "instance-new"

    @pytest.mark.asyncio
    async def test_lru_eviction_respects_access_order(self, thread_manager, mock_manager):
        """Test that recently accessed threads are not evicted."""
        # Register threads
        for i in range(3):
            await thread_manager._register_thread_unlocked(
                workspace_id="WS001",
                channel_id=f"C00{i}",
                thread_ts=f"thread-{i}",
                instance_id=f"instance-{i}",
            )

        # Access thread-0 to make it recently used
        async with thread_manager._threads_guard:
            await thread_manager._get_thread_unlocked(workspace_id="WS001", thread_ts="thread-0")

        # Add new thread (should evict thread-1, not thread-0)
        await thread_manager._register_thread_unlocked(
            workspace_id="WS001",
            channel_id="C999",
            thread_ts="thread-new",
            instance_id="instance-new",
        )

        # Verify thread-1 was evicted (oldest after access)
        async with thread_manager._threads_guard:
            result1 = await thread_manager._get_thread_unlocked(workspace_id="WS001", thread_ts="thread-1")
        assert result1 is None

        # Verify thread-0 still exists
        async with thread_manager._threads_guard:
            result0 = await thread_manager._get_thread_unlocked(workspace_id="WS001", thread_ts="thread-0")
        assert result0 is not None

        # Verify new thread exists
        async with thread_manager._threads_guard:
            result_new = await thread_manager._get_thread_unlocked(workspace_id="WS001", thread_ts="thread-new")
        assert result_new is not None

    @pytest.mark.asyncio
    async def test_evict_oldest_terminates_instance(self, thread_manager, mock_manager):
        """Test that LRU eviction calls terminate_instance on the manager."""
        # Register a thread
        await thread_manager._register_thread_unlocked(
            workspace_id="WS001",
            channel_id="C001",
            thread_ts="thread-1",
            instance_id="instance-1",
        )

        # Verify no terminations yet
        mock_manager.terminate_instance.assert_not_called()

        # Fill up capacity and trigger eviction
        await thread_manager._register_thread_unlocked(
            workspace_id="WS001",
            channel_id="C002",
            thread_ts="thread-2",
            instance_id="instance-2",
        )
        await thread_manager._register_thread_unlocked(
            workspace_id="WS001",
            channel_id="C003",
            thread_ts="thread-3",
            instance_id="instance-3",
        )

        # This should evict thread-1
        await thread_manager._register_thread_unlocked(
            workspace_id="WS001",
            channel_id="C999",
            thread_ts="thread-new",
            instance_id="instance-new",
        )

        # Assert terminate was called for evicted instance
        mock_manager.terminate_instance.assert_called_once_with("instance-1")

    @pytest.mark.asyncio
    async def test_evict_oldest_does_not_terminate_none_instance(self, thread_manager, mock_manager):
        """Test that eviction doesn't call terminate for thread without instance."""
        # Create a thread without a real instance_id (using empty string)
        await thread_manager._register_thread_unlocked(
            workspace_id="WS001",
            channel_id="C001",
            thread_ts="thread-1",
            instance_id="instance-1",
        )
        # Fill up capacity
        await thread_manager._register_thread_unlocked(
            workspace_id="WS001",
            channel_id="C002",
            thread_ts="thread-2",
            instance_id="instance-2",
        )
        await thread_manager._register_thread_unlocked(
            workspace_id="WS001",
            channel_id="C003",
            thread_ts="thread-3",
            instance_id="instance-3",
        )

        # Clear mock call count
        mock_manager.terminate_instance.reset_mock()

        # Add new thread (evicts thread-1)
        await thread_manager._register_thread_unlocked(
            workspace_id="WS001",
            channel_id="C999",
            thread_ts="thread-new",
            instance_id="instance-new",
        )

        # Verify terminate was called (thread-1 had instance-1)
        mock_manager.terminate_instance.assert_called_once()


class TestGetStats:
    """Tests for get_stats method."""

    @pytest.mark.asyncio
    async def test_get_stats_returns_correct_counts(self, thread_manager):
        """Test that get_stats returns accurate counts."""
        # Register threads across workspaces
        await thread_manager._register_thread_unlocked(
            workspace_id="WS001",
            channel_id="C001",
            thread_ts="thread-1",
            instance_id="instance-1",
        )
        await thread_manager._register_thread_unlocked(
            workspace_id="WS001",
            channel_id="C002",
            thread_ts="thread-2",
            instance_id="instance-2",
        )
        await thread_manager._register_thread_unlocked(
            workspace_id="WS002",
            channel_id="C003",
            thread_ts="thread-3",
            instance_id="instance-3",
        )

        # Act
        stats = await thread_manager.get_stats()

        # Assert
        assert stats["total_threads"] == 3
        assert stats["workspaces"] == 2
        assert stats["threads_per_workspace"]["WS001"] == 2
        assert stats["threads_per_workspace"]["WS002"] == 1
        assert stats["ttl_seconds"] == 0.1
        assert stats["max_threads_per_workspace"] == 3

    @pytest.mark.asyncio
    async def test_get_stats_empty_manager(self, thread_manager):
        """Test get_stats for empty manager."""
        stats = await thread_manager.get_stats()

        assert stats["total_threads"] == 0
        assert stats["workspaces"] == 0
        assert stats["threads_per_workspace"] == {}


class TestCleanupInstance:
    """Tests for cleanup_instance method."""

    @pytest.mark.asyncio
    async def test_cleanup_instance_removes_mapping(self, thread_manager):
        """Test that cleanup_instance removes thread and mapping."""
        # Register a thread
        await thread_manager._register_thread_unlocked(
            workspace_id="WS001",
            channel_id="C001",
            thread_ts="thread-1",
            instance_id="instance-1",
        )

        # Verify thread exists
        assert "instance-1" in thread_manager._instance_to_thread
        async with thread_manager._threads_guard:
            thread = await thread_manager._get_thread_unlocked(workspace_id="WS001", thread_ts="thread-1")
        assert thread is not None

        # Act - cleanup
        await thread_manager.cleanup_instance("instance-1")

        # Assert - mapping removed
        assert "instance-1" not in thread_manager._instance_to_thread

        # Thread should be gone
        async with thread_manager._threads_guard:
            thread = await thread_manager._get_thread_unlocked(workspace_id="WS001", thread_ts="thread-1")
        assert thread is None

    @pytest.mark.asyncio
    async def test_cleanup_instance_nonexistent(self, thread_manager):
        """Test cleanup_instance with non-existent instance (should not raise)."""
        # Should not raise
        await thread_manager.cleanup_instance("nonexistent-instance")

        # Stats should still be empty
        stats = await thread_manager.get_stats()
        assert stats["total_threads"] == 0


class TestEvictExpired:
    """Tests for evict_expired method."""

    @pytest.mark.asyncio
    async def test_evict_expired_removes_old_threads(self, thread_manager, mock_manager):
        """Test that evict_expired removes threads past TTL."""
        # Register threads
        await thread_manager._register_thread_unlocked(
            workspace_id="WS001",
            channel_id="C001",
            thread_ts="thread-1",
            instance_id="instance-1",
        )
        await thread_manager._register_thread_unlocked(
            workspace_id="WS001",
            channel_id="C002",
            thread_ts="thread-2",
            instance_id="instance-2",
        )

        # Wait for TTL expiry
        await asyncio.sleep(0.15)

        # Act - evict expired
        evicted = await thread_manager.evict_expired()

        # Assert
        assert len(evicted) == 2
        assert all(isinstance(e, ThreadInstance) for e in evicted)

        # Stats should be empty
        stats = await thread_manager.get_stats()
        assert stats["total_threads"] == 0

    @pytest.mark.asyncio
    async def test_evict_expired_mixed_ttl(self, thread_manager):
        """Test evict_expired with mix of expired and non-expired threads."""
        # Register first thread
        await thread_manager._register_thread_unlocked(
            workspace_id="WS001",
            channel_id="C001",
            thread_ts="thread-1",
            instance_id="instance-1",
        )

        # Wait for first thread to expire
        await asyncio.sleep(0.15)

        # Register second thread - this triggers eviction of expired thread-1
        await thread_manager._register_thread_unlocked(
            workspace_id="WS001",
            channel_id="C002",
            thread_ts="thread-2",
            instance_id="instance-2",
        )

        # Thread-1 should have been evicted during thread-2 registration
        async with thread_manager._threads_guard:
            thread1 = await thread_manager._get_thread_unlocked(workspace_id="WS001", thread_ts="thread-1")
        assert thread1 is None

        # thread-2 should still exist (not expired yet)
        async with thread_manager._threads_guard:
            thread2 = await thread_manager._get_thread_unlocked(workspace_id="WS001", thread_ts="thread-2")
        assert thread2 is not None
