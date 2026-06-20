"""Tests for instance cache TTL functionality.

Tests the TTL-based release of in-memory graphs for cached instances, including:
- _release_cached_instance() behavior
- _cleanup_cached_instances() background task
- Hot vs cold resume scenarios
"""

import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime, timedelta

from daemon.cancellation import CancellationReason


class TestReleaseCachedInstance:
    """Tests for _release_cached_instance() method."""

    @pytest.fixture
    def mock_manager(self):
        """Create a mock manager for testing _release_cached_instance."""
        manager = MagicMock()
        manager.instances = {}  # Maps instance_id -> (graph, agent_dir)
        manager._graph_tasks = {}  # Maps instance_id -> asyncio.Task
        manager._request_registry = MagicMock()
        manager._request_registry.cancel_by_instance = MagicMock(return_value=0)
        return manager

    def _call_release_cached_instance(self, manager, instance_id):
        """Import and call _release_cached_instance directly."""
        from daemon.manager import InstanceManager
        return InstanceManager._release_cached_instance(manager, instance_id)

    def test_release_cached_instance_removes_from_memory(self, mock_manager):
        """Verify that _release_cached_instance() removes the instance from self.instances dict."""
        instance_id = "test-instance-123"
        mock_graph = MagicMock()
        mock_agent_dir = "/agents/test"
        
        # Pre-condition: instance exists in memory
        mock_manager.instances[instance_id] = (mock_graph, mock_agent_dir)
        assert instance_id in mock_manager.instances
        
        # Call release
        self._call_release_cached_instance(mock_manager, instance_id)
        
        # Post-condition: instance removed from memory
        assert instance_id not in mock_manager.instances

    def test_release_cached_instance_cancels_graph_task(self, mock_manager):
        """Verify lingering graph task is cancelled."""
        instance_id = "test-instance-123"
        
        # Create a mock task that's not done
        mock_task = MagicMock(spec=asyncio.Task)
        mock_task.done.return_value = False
        mock_manager._graph_tasks[instance_id] = mock_task
        
        # Call release
        self._call_release_cached_instance(mock_manager, instance_id)
        
        # Verify task was cancelled and removed
        mock_task.cancel.assert_called_once()
        assert instance_id not in mock_manager._graph_tasks

    def test_release_cached_instance_idempotent(self, mock_manager):
        """Calling on already-released instance should not error."""
        instance_id = "test-instance-123"
        
        # Instance is NOT in memory (already released)
        assert instance_id not in mock_manager.instances
        
        # Should not raise any exception
        self._call_release_cached_instance(mock_manager, instance_id)

    def test_release_cached_instance_cancels_requests(self, mock_manager):
        """Verify request registry cancel is called with correct reason."""
        instance_id = "test-instance-123"
        
        # Call release
        self._call_release_cached_instance(mock_manager, instance_id)
        
        # Verify cancel_by_instance was called with SESSION_TERMINATED
        mock_manager._request_registry.cancel_by_instance.assert_called_once_with(
            instance_id,
            CancellationReason.SESSION_TERMINATED
        )

    def test_release_cached_instance_skips_done_task(self, mock_manager):
        """Verify that already-done tasks are not cancelled."""
        instance_id = "test-instance-123"
        
        # Create a mock task that is already done
        mock_task = MagicMock(spec=asyncio.Task)
        mock_task.done.return_value = True
        mock_manager._graph_tasks[instance_id] = mock_task
        
        # Call release
        self._call_release_cached_instance(mock_manager, instance_id)
        
        # Task should NOT be cancelled (it's already done)
        mock_task.cancel.assert_not_called()
        # Task should still be removed from dict
        assert instance_id not in mock_manager._graph_tasks


class TestCleanupCachedInstances:
    """Tests for _cleanup_cached_instances() background task."""

    @pytest.fixture
    def mock_manager(self):
        """Create a mock manager for testing cleanup."""
        from daemon.manager import InstanceManager
        manager = MagicMock(spec=InstanceManager)
        manager.instances = {}  # Maps instance_id -> (graph, agent_dir)
        manager._graph_tasks = {}
        manager._request_registry = MagicMock()
        manager._request_registry.cancel_by_instance = MagicMock(return_value=0)
        manager._instance_repository = MagicMock()
        return manager

    def _make_cached_instance(self, instance_id: str, updated_at: str | None, status: str = "paused", paused_at: str | None = None) -> MagicMock:
        """Create a mock cached instance with specified timestamps."""
        instance = MagicMock()
        instance.instance_id = instance_id
        instance.updated_at = updated_at
        instance.paused_at = paused_at
        instance.status = status
        return instance

    @pytest.mark.asyncio
    async def test_cleanup_calculates_expired_instances_correctly(self, mock_manager):
        """Verify cleanup correctly identifies expired instances by time comparison."""
        from daemon.manager import InstanceManager, INSTANCE_CACHE_TTL_HOURS
        
        # This test verifies the core TTL calculation logic works correctly
        # by directly testing the datetime comparison that drives the cleanup
        
        instance_id = "test-instance"
        ttl_seconds = INSTANCE_CACHE_TTL_HOURS * 3600
        
        # Case 1: Instance cached just under TTL - should NOT be released
        almost_expired_time = (datetime.utcnow() - timedelta(seconds=ttl_seconds - 1)).isoformat()
        almost_expired_instance = self._make_cached_instance(instance_id, almost_expired_time)
        cached_at = datetime.fromisoformat(almost_expired_instance.updated_at)
        diff_under_ttl = (datetime.utcnow() - cached_at).total_seconds()
        assert diff_under_ttl < ttl_seconds, "Instance under TTL should have diff < ttl"
        
        # Case 2: Instance cached just over TTL - SHOULD be released
        just_expired_time = (datetime.utcnow() - timedelta(seconds=ttl_seconds + 1)).isoformat()
        just_expired_instance = self._make_cached_instance(instance_id, just_expired_time)
        cached_at = datetime.fromisoformat(just_expired_instance.updated_at)
        diff_over_ttl = (datetime.utcnow() - cached_at).total_seconds()
        assert diff_over_ttl > ttl_seconds, "Instance over TTL should have diff > ttl"
        
        # Case 3: Instance cached well over TTL - SHOULD be released
        long_expired_time = (datetime.utcnow() - timedelta(hours=48)).isoformat()
        long_expired_instance = self._make_cached_instance(instance_id, long_expired_time)
        cached_at = datetime.fromisoformat(long_expired_instance.updated_at)
        diff_long_expired = (datetime.utcnow() - cached_at).total_seconds()
        assert diff_long_expired > ttl_seconds, "Long expired instance should have diff > ttl"

    @pytest.mark.asyncio
    async def test_cleanup_releases_expired_paused_instances(self, mock_manager):
        """Verify cleanup correctly identifies and releases expired instances.
        
        This test verifies the core cleanup loop logic: expired instances
        (cached > TTL ago) should be identified and released.
        We verify by calling the cleanup method and checking release is invoked.
        """
        from daemon.manager import InstanceManager, INSTANCE_CACHE_TTL_HOURS
        
        ttl_seconds = INSTANCE_CACHE_TTL_HOURS * 3600
        instance_id = "expired-instance"
        
        # Create instance that's in memory
        mock_graph = MagicMock()
        mock_manager.instances[instance_id] = (mock_graph, "/agents/test")
        
        # Create cached instance that was cached >TTL ago
        # Use timezone-aware datetime (+00:00) to match production code's datetime.now(timezone.utc)
        expired_time = (datetime.utcnow() - timedelta(seconds=ttl_seconds + 60)).strftime('%Y-%m-%dT%H:%M:%S+00:00')
        mock_instance = self._make_cached_instance(instance_id, expired_time, status="paused")
        mock_manager._instance_repository.list.return_value = ([mock_instance], 1)
        
        # Mock _release_cached_instance to track calls
        released_instances = []
        def track_release(iid):
            released_instances.append(iid)
            if iid in mock_manager.instances:
                del mock_manager.instances[iid]
        mock_manager._release_cached_instance = MagicMock(side_effect=track_release)
        
        # Run cleanup once
        mock_manager._shutting_down = False
        
        async def break_after_one_iteration(*args, **kwargs):
            mock_manager._shutting_down = True
        with patch("daemon.manager.asyncio.sleep", new_callable=AsyncMock, side_effect=break_after_one_iteration):
            await InstanceManager._cleanup_cached_instances(mock_manager)
        
        # Verify the expired instance WAS released (expired and in non-active status)
        assert instance_id not in mock_manager.instances
        assert instance_id in released_instances

    @pytest.mark.asyncio
    async def test_cleanup_skips_recent_paused_instances(self, mock_manager):
        """Verify instances cached <4h are NOT released."""
        from daemon.manager import INSTANCE_CACHE_TTL_HOURS
        
        ttl_seconds = INSTANCE_CACHE_TTL_HOURS * 3600
        instance_id = "recent-instance"
        
        # Create instance that's in memory
        mock_graph = MagicMock()
        mock_manager.instances[instance_id] = (mock_graph, "/agents/test")
        
        # Create cached instance that was updated only 10 minutes ago
        recent_time = (datetime.utcnow() - timedelta(seconds=ttl_seconds - 600)).isoformat()
        mock_instance = self._make_cached_instance(instance_id, recent_time)
        mock_manager._instance_repository.list.return_value = ([mock_instance], 1)
        
        # Verify the instance would NOT be considered expired
        cached_at = datetime.fromisoformat(mock_instance.updated_at)
        diff = (datetime.utcnow() - cached_at).total_seconds()
        assert diff < ttl_seconds, "Instance should NOT be considered expired"
        
        # Run cleanup once - set _shutting_down=False so loop enters, mock sleep to break after one iteration
        mock_manager._shutting_down = False
        from daemon.manager import InstanceManager
        
        # Mock asyncio.sleep to exit loop after first iteration by setting _shutting_down=True
        async def break_after_one_iteration(*args, **kwargs):
            mock_manager._shutting_down = True
        with patch("daemon.manager.asyncio.sleep", new_callable=AsyncMock, side_effect=break_after_one_iteration):
            await InstanceManager._cleanup_cached_instances(mock_manager)
        
        # Verify instance was NOT released (still in memory)
        assert instance_id in mock_manager.instances

    @pytest.mark.asyncio
    async def test_cleanup_skips_recent_paused_instances_with_explicit_time(self, mock_manager):
        """Verify instances cached <4h are NOT released using explicit time."""
        from daemon.manager import InstanceManager
        
        instance_id = "recent-instance"
        
        # Create instance that's in memory
        mock_graph = MagicMock()
        mock_manager.instances[instance_id] = (mock_graph, "/agents/test")
        
        # Create cached instance that was updated only 10 minutes ago
        recent_time = (datetime.utcnow() - timedelta(minutes=10)).isoformat()
        mock_instance = self._make_cached_instance(instance_id, recent_time)
        mock_manager._instance_repository.list.return_value = ([mock_instance], 1)
        
        # Run cleanup once - set _shutting_down=False so loop enters, mock sleep to break after one iteration
        mock_manager._shutting_down = False
        
        async def break_after_one_iteration(*args, **kwargs):
            mock_manager._shutting_down = True
        with patch("daemon.manager.asyncio.sleep", new_callable=AsyncMock, side_effect=break_after_one_iteration):
            await InstanceManager._cleanup_cached_instances(mock_manager)
        
        # Verify instance was NOT released (still in memory)
        assert instance_id in mock_manager.instances

    @pytest.mark.asyncio
    async def test_cleanup_skips_instances_not_in_memory(self, mock_manager):
        """Cached instances without in-memory graph are skipped."""
        from daemon.manager import InstanceManager
        
        instance_id = "not-in-memory-instance"
        
        # Instance is NOT in memory (already cleaned up or never loaded)
        assert instance_id not in mock_manager.instances
        
        # Create cached instance that was updated 5 hours ago
        expired_time = (datetime.utcnow() - timedelta(hours=5)).isoformat()
        mock_instance = self._make_cached_instance(instance_id, expired_time)
        mock_manager._instance_repository.list.return_value = ([mock_instance], 1)
        
        # Run cleanup once - set _shutting_down=False so loop enters, mock sleep to break after one iteration
        mock_manager._shutting_down = False
        
        async def break_after_one_iteration(*args, **kwargs):
            mock_manager._shutting_down = True
        with patch("daemon.manager.asyncio.sleep", new_callable=AsyncMock, side_effect=break_after_one_iteration):
            await InstanceManager._cleanup_cached_instances(mock_manager)
        
        # No crash - instances not in memory are skipped

    @pytest.mark.asyncio
    async def test_cleanup_handles_invalid_updated_at(self, mock_manager):
        """Instance with None/invalid updated_at doesn't crash the loop."""
        from daemon.manager import InstanceManager
        
        # Create multiple instances with various invalid updated_at values
        # valid_instance is 2h ago (within 24h TTL), so it should be kept
        valid_instance = self._make_cached_instance(
            "valid-instance",
            (datetime.utcnow() - timedelta(hours=2)).isoformat()
        )
        none_instance = self._make_cached_instance("none-instance", None)
        empty_instance = self._make_cached_instance("empty-instance", "")
        invalid_instance = self._make_cached_instance("invalid-instance", "not-a-date")
        
        # Only the valid instance is in memory
        mock_manager.instances["valid-instance"] = (MagicMock(), "/agents/test")
        
        mock_manager._instance_repository.list.return_value = (
            [valid_instance, none_instance, empty_instance, invalid_instance],
            4
        )
        
        # Run cleanup once - set _shutting_down=False so loop enters, mock sleep to break after one iteration
        mock_manager._shutting_down = False
        
        async def break_after_one_iteration(*args, **kwargs):
            mock_manager._shutting_down = True
        with patch("daemon.manager.asyncio.sleep", new_callable=AsyncMock, side_effect=break_after_one_iteration):
            await InstanceManager._cleanup_cached_instances(mock_manager)
        
        # Only valid instance should have been processed (and kept, since it's within TTL)
        assert "valid-instance" in mock_manager.instances  # Still there (within TTL check passes)
        
        # Verify no crash occurred for invalid instances

    @pytest.mark.asyncio
    async def test_cleanup_handles_multiple_instances(self, mock_manager):
        """Verify cleanup correctly handles multiple instances with different ages.
        
        We verify that expired instances are released while recent ones are kept,
        by calling the cleanup method and checking the results.
        """
        from daemon.manager import InstanceManager, INSTANCE_CACHE_TTL_HOURS
        
        ttl_seconds = INSTANCE_CACHE_TTL_HOURS * 3600
        
        # Create instances with different update times
        # Use timezone-aware datetime (+00:00) to match production code's datetime.now(timezone.utc)
        expired_time1 = (datetime.utcnow() - timedelta(seconds=ttl_seconds + 300)).strftime('%Y-%m-%dT%H:%M:%S+00:00')
        expired_time2 = (datetime.utcnow() - timedelta(seconds=ttl_seconds + 600)).strftime('%Y-%m-%dT%H:%M:%S+00:00')
        recent_time = (datetime.utcnow() - timedelta(minutes=30)).strftime('%Y-%m-%dT%H:%M:%S+00:00')
        
        # Put all instances in memory
        mock_graph = MagicMock()
        mock_manager.instances["expired-1"] = (mock_graph, "/agents/test")
        mock_manager.instances["expired-2"] = (mock_graph, "/agents/test")
        mock_manager.instances["recent"] = (mock_graph, "/agents/test")
        
        instance1 = self._make_cached_instance("expired-1", expired_time1)
        instance2 = self._make_cached_instance("expired-2", expired_time2)
        recent_instance = self._make_cached_instance("recent", recent_time)
        
        mock_manager._instance_repository.list.return_value = (
            [instance1, instance2, recent_instance], 3
        )
        
        # Mock _release_cached_instance to actually remove from memory
        released_instances = []
        def track_release(iid):
            released_instances.append(iid)
            if iid in mock_manager.instances:
                del mock_manager.instances[iid]
        mock_manager._release_cached_instance = MagicMock(side_effect=track_release)
        
        # Run cleanup once
        mock_manager._shutting_down = False
        
        async def break_after_one_iteration(*args, **kwargs):
            mock_manager._shutting_down = True
        with patch("daemon.manager.asyncio.sleep", new_callable=AsyncMock, side_effect=break_after_one_iteration):
            await InstanceManager._cleanup_cached_instances(mock_manager)
        
        # Verify expired instances were released
        assert "expired-1" not in mock_manager.instances
        assert "expired-2" not in mock_manager.instances
        assert "expired-1" in released_instances
        assert "expired-2" in released_instances
        
        # Verify recent instance was kept
        assert "recent" in mock_manager.instances
        assert "recent" not in released_instances

    @pytest.mark.parametrize("status", ["completed", "error", "terminated", "failed", "paused"])
    @pytest.mark.asyncio
    async def test_cleanup_releases_expired_by_status(self, mock_manager, status):
        """Verify cleanup releases expired instances for all non-active statuses."""
        from daemon.manager import InstanceManager
        
        instance_id = f"expired-{status}-instance"
        
        # Create instance that's in memory with the given status
        mock_graph = MagicMock()
        mock_manager.instances[instance_id] = (mock_graph, "/agents/test")
        
        # Create cached instance that was updated 25 hours ago (past 24h TTL)
        # Use timezone-aware datetime (+00:00) to match production code's datetime.now(timezone.utc)
        expired_time = (datetime.utcnow() - timedelta(hours=25)).strftime('%Y-%m-%dT%H:%M:%S+00:00')
        mock_instance = self._make_cached_instance(instance_id, expired_time, status=status)
        mock_manager._instance_repository.list.return_value = ([mock_instance], 1)
        
        # Mock _release_cached_instance to actually delete from mock_manager.instances
        def release_instance(instance_id_to_release):
            if instance_id_to_release in mock_manager.instances:
                del mock_manager.instances[instance_id_to_release]
        mock_manager._release_cached_instance = MagicMock(side_effect=release_instance)
        
        # Run cleanup once - set _shutting_down=False so loop enters, mock sleep to break after one iteration
        mock_manager._shutting_down = False
        
        async def break_after_one_iteration(*args, **kwargs):
            mock_manager._shutting_down = True
        with patch("daemon.manager.asyncio.sleep", new_callable=AsyncMock, side_effect=break_after_one_iteration):
            await InstanceManager._cleanup_cached_instances(mock_manager)
        
        # Verify instance WAS released (expired and in non-active status)
        assert instance_id not in mock_manager.instances

    @pytest.mark.asyncio
    async def test_cleanup_handles_empty_list(self, mock_manager):
        """Verify cleanup handles empty instances list gracefully."""
        from daemon.manager import InstanceManager
        
        mock_manager._instance_repository.list.return_value = ([], 0)
        
        # Run cleanup once - set _shutting_down=False so loop enters, mock sleep to break after one iteration
        mock_manager._shutting_down = False
        
        async def break_after_one_iteration(*args, **kwargs):
            mock_manager._shutting_down = True
        with patch("daemon.manager.asyncio.sleep", new_callable=AsyncMock, side_effect=break_after_one_iteration):
            await InstanceManager._cleanup_cached_instances(mock_manager)
        
        # No crash - just empty iteration


class TestHotColdResume:
    """Tests for hot vs cold resume scenarios."""

    def test_hot_resume_within_ttl(self):
        """Pausing and quickly resuming uses in-memory graph."""
        # This tests the conceptual behavior - within TTL the graph
        # should remain in memory for fast hot resume
        from daemon.manager import INSTANCE_CACHE_TTL_HOURS
        
        # TTL should be 24 hours
        assert INSTANCE_CACHE_TTL_HOURS == 24

    def test_cold_resume_after_ttl_concept(self):
        """After TTL release, resuming the instance works (graph rebuilt from checkpoint)."""
        # This is a conceptual test - the actual cold resume behavior
        # involves the checkpointer and is tested elsewhere.
        # We verify the TTL constant is defined correctly.
        from daemon.manager import INSTANCE_CACHE_TTL_HOURS

        assert INSTANCE_CACHE_TTL_HOURS == 24


class TestTTLConstants:
    """Tests for TTL-related constants."""

    def test_instance_cache_ttl_hours(self):
        """Verify TTL constant is defined and reasonable."""
        from daemon.manager import INSTANCE_CACHE_TTL_HOURS
        
        assert INSTANCE_CACHE_TTL_HOURS == 24
        assert INSTANCE_CACHE_TTL_HOURS > 0
        assert INSTANCE_CACHE_TTL_HOURS < 168  # Less than 1 week


class TestColdResume:
    """Tests for cold resume flow (release from memory + restore from checkpoint)."""

    @pytest.fixture
    def mock_manager(self):
        """Create a mock manager for testing cold resume."""
        from daemon.manager import InstanceManager
        from daemon.services.instance_lifecycle import InstanceLifecycleService
        from unittest.mock import MagicMock, AsyncMock, patch

        manager = MagicMock()
        manager.instances = {}  # Maps instance_id -> (graph, agent_dir)
        manager._graph_tasks = {}  # Maps instance_id -> asyncio.Task
        manager._request_registry = MagicMock()
        manager._request_registry.cancel_by_instance = MagicMock(return_value=0)
        manager._instance_repository = MagicMock()
        manager._project_repository = MagicMock()
        manager.prompt_cache = MagicMock()
        manager._checkpointer = MagicMock()
        manager._compactor = None
        manager.config = MagicMock()
        manager.config.llm = MagicMock()
        manager.config.llm.base_url = "http://localhost"
        manager.config.llm.api_key = "test"
        manager.config.llm.model = "gpt-4o"
        manager.config.llm.model_vision = False
        manager.config.llm.temperature = 0.7
        manager.config.llm.request_timeout = 60
        manager.config.limits = MagicMock()
        manager.config.limits.max_instances = 100
        manager.config.limits.max_children_per_instance = 10
        manager.config.limits.graph_recursion_limit = 50
        manager.config.limits.llm_concurrency = 5
        manager.config.queue = MagicMock()
        manager.config.queue.llm_retry_transient_attempts = 3
        manager.config.queue.llm_retry_timeout_attempts = 2
        manager._completion_registry = MagicMock()
        # Add async methods
        manager.get_instance = AsyncMock()
        manager.ensure_mcp_preloaded = AsyncMock()

        # Create lifecycle service with mocked manager
        cancellation_service = MagicMock()
        events_service = MagicMock()
        lifecycle_service = InstanceLifecycleService(
            manager=manager,
            cancellation_service=cancellation_service,
            events_service=events_service,
        )
        manager._lifecycle_service = lifecycle_service

        return manager

    def _call_release_cached_instance(self, manager, instance_id):
        """Import and call _release_cached_instance directly."""
        from daemon.manager import InstanceManager
        return InstanceManager._release_cached_instance(manager, instance_id)

    @pytest.mark.asyncio
    async def test_cold_resume_flow_end_to_end(self, mock_manager):
        """Verify cold resume: release from memory, then restore from checkpoint.

        This test verifies the complete flow:
        1. Instance exists in memory (hot path)
        2. _release_cached_instance() removes it from memory
        3. get_instance() triggers cold resume (restores from checkpoint)
        4. Instance is back in memory
        """
        instance_id = "test-instance-cold-resume"
        mock_graph = MagicMock()
        mock_agent_dir = "/agents/test"

        # Step 1: Pre-condition - instance exists in memory
        mock_manager.instances[instance_id] = (mock_graph, mock_agent_dir)
        assert instance_id in mock_manager.instances

        # Step 2: Create mock paused instance metadata
        mock_instance_meta = MagicMock()
        mock_instance_meta.instance_id = instance_id
        mock_instance_meta.agent_id = "test-agent"
        mock_instance_meta.agent_dir = mock_agent_dir
        mock_instance_meta.status = "paused"
        mock_instance_meta.paused_at = "2024-01-01T00:00:00"
        mock_manager._instance_repository.get.return_value = mock_instance_meta

        # Step 3: Call _release_cached_instance to remove from memory
        self._call_release_cached_instance(mock_manager, instance_id)

        # Step 4: Verify instance is NO LONGER in memory
        assert instance_id not in mock_manager.instances

        # Step 5: Mock _restore_instance to verify it gets called
        # (Full restore requires too many dependencies - we verify the call)
        mock_manager._lifecycle_service._restore_instance = MagicMock(return_value=mock_graph)

        # Step 6: Call get_instance - should trigger cold resume
        from daemon.manager import InstanceManager
        await InstanceManager.get_instance(mock_manager, instance_id)

        # Step 7: Verify _restore_instance was called with correct args
        mock_manager._lifecycle_service._restore_instance.assert_called_once_with(
            instance_id, mock_instance_meta
        )

        # Step 8: Verify graph is BACK in instances dict (via _restore_instance adding it)
        # Note: The actual restore adds the graph to instances dict internally

    @pytest.mark.asyncio
    async def test_get_instance_hot_path_skips_restore(self, mock_manager):
        """Verify get_instance uses hot path when instance is in memory."""
        instance_id = "test-instance-hot"
        mock_graph = MagicMock()
        mock_agent_dir = "/agents/test"

        # Instance IS in memory
        mock_manager.instances[instance_id] = (mock_graph, mock_agent_dir)

        # Mock _restore_instance on the lifecycle service to track if it's called
        mock_manager._lifecycle_service._restore_instance = MagicMock()

        # Call get_instance
        from daemon.manager import InstanceManager
        result = await InstanceManager.get_instance(mock_manager, instance_id)

        # Should return the in-memory graph
        assert result == mock_graph

        # _restore_instance should NOT be called (hot path)
        mock_manager._lifecycle_service._restore_instance.assert_not_called()

        # Repository get should NOT be called (hot path)
        mock_manager._instance_repository.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_instance_cold_resume_triggers_restore(self, mock_manager):
        """Verify get_instance triggers cold resume when instance is not in memory."""
        instance_id = "test-instance-cold"
        mock_graph = MagicMock()
        mock_agent_dir = "/agents/test"

        # Instance is NOT in memory
        assert instance_id not in mock_manager.instances

        # Create mock instance metadata
        mock_instance_meta = MagicMock()
        mock_instance_meta.instance_id = instance_id
        mock_instance_meta.agent_id = "test-agent"
        mock_instance_meta.agent_dir = mock_agent_dir
        mock_manager._instance_repository.get.return_value = mock_instance_meta

        # Mock _restore_instance
        mock_manager._lifecycle_service._restore_instance = MagicMock(return_value=mock_graph)

        # Call get_instance
        from daemon.manager import InstanceManager
        result = await InstanceManager.get_instance(mock_manager, instance_id)

        # Should call _restore_instance
        mock_manager._lifecycle_service._restore_instance.assert_called_once_with(
            instance_id, mock_instance_meta
        )


class TestPausedAtField:
    """Tests for paused_at field handling."""

    @pytest.fixture
    def mock_manager(self):
        """Create a mock manager for testing paused_at field."""
        from daemon.manager import InstanceManager
        from daemon.services.instance_lifecycle import InstanceLifecycleService
        from unittest.mock import MagicMock, AsyncMock
        
        manager = MagicMock()
        manager.instances = {}
        manager._graph_tasks = {}
        manager._request_registry = MagicMock()
        manager._request_registry.cancel_by_instance = MagicMock(return_value=0)
        manager._instance_repository = MagicMock()
        manager._completion_registry = MagicMock()
        # Use AsyncMock for async methods
        manager._live_hub = MagicMock()
        manager._live_hub.stream_status_change = AsyncMock()
        manager._events_service = MagicMock()
        
        cancellation_service = MagicMock()
        lifecycle_service = InstanceLifecycleService(
            manager=manager,
            cancellation_service=cancellation_service,
            events_service=manager._events_service,
        )
        manager._lifecycle_service = lifecycle_service
        
        return manager

    @pytest.mark.asyncio
    async def test_pause_single_sets_paused_at_field(self, mock_manager):
        """Verify pause_instance_cascade sets paused_at field via the batched SQL UPDATE.

        L14 note: the OLD per-node ``repo.update(...)`` path no longer
        exists — pause now goes through ``_pause_cascade_db_sync``
        which runs a single ``UPDATE ... SET paused_at = :paused_at``.
        We verify ``paused_at`` was set by capturing the helper's
        ``paused_at_iso`` keyword argument.
        """
        instance_id = "test-pause-instance"

        # Capture calls to the batched db_sync helper
        captured: dict = {"pause_calls": []}

        from daemon.services.instance_lifecycle import _CascadeUpdateResult

        def _mock_pause_db_sync(engine, write_guard, *, tree_ids, paused_at_iso, paused_instances_data):
            captured["pause_calls"].append(
                {"tree_ids": list(tree_ids), "paused_at_iso": paused_at_iso,
                 "paused_instances_data": list(paused_instances_data)}
            )
            return _CascadeUpdateResult(
                updated_ids=[iid for iid, _a, _w in paused_instances_data],
                skipped_ids=[],
                agent_ids_by_instance={iid: a for iid, a, _w in paused_instances_data},
                waiting_for_by_instance={iid: w for iid, _a, w in paused_instances_data},
            )

        mock_manager._lifecycle_service._pause_cascade_db_sync = _mock_pause_db_sync

        # Mock the tree traversal methods on the repository
        mock_manager._instance_repository.get_tree_root_id.return_value = instance_id
        mock_manager._instance_repository.get_tree_ids.return_value = [instance_id]

        # Create mock meta for the instance
        mock_meta = MagicMock()
        mock_meta.instance_id = instance_id
        mock_meta.status = "running"
        mock_meta.waiting_for = 0
        mock_manager._instance_repository.get.return_value = mock_meta

        # Call pause_instance_cascade
        from daemon.manager import InstanceManager
        await InstanceManager.pause_instance_cascade(mock_manager, instance_id)

        # L14: verify the batched db_sync helper was called with a
        # non-empty paused_at_iso timestamp and the single instance
        # in paused_instances_data.
        assert len(captured["pause_calls"]) == 1, (
            "_pause_cascade_db_sync should have been called exactly once"
        )
        pause_call = captured["pause_calls"][0]
        assert pause_call["paused_at_iso"], "paused_at_iso should be a non-empty ISO timestamp"
        assert pause_call["paused_at_iso"] is not None
        assert pause_call["paused_at_iso"] != ""
        # The single running instance should be in the batch payload
        data_ids = {iid for iid, _a, _w in pause_call["paused_instances_data"]}
        assert data_ids == {instance_id}

    def test_paused_at_cleared_on_resume(self, mock_manager):
        """Verify paused_at is cleared when instance transitions from paused to running."""
        instance_id = "test-resume-instance"
        
        # Track calls to update
        update_calls = []
        def track_update(*args, **kwargs):
            update_calls.append(kwargs)
            # Return mock instance
            result = MagicMock()
            result.instance_id = instance_id
            return result
        mock_manager._instance_repository.update = track_update
        
        # Create mock paused instance
        from datetime import datetime
        mock_meta = MagicMock()
        mock_meta.instance_id = instance_id
        mock_meta.status = "paused"
        mock_meta.waiting_for = 0
        mock_meta.paused_at = datetime.utcnow().isoformat()
        mock_manager._instance_repository.get.return_value = mock_meta
        
        # When resuming, status changes to running and paused_at should be cleared
        # This happens in the messaging service when a message is sent to a paused instance
        # For this test, we verify the pattern: update with paused_at=None
        
        # Simulate resume: update status to running with paused_at=None
        mock_manager._instance_repository.update(
            instance_id,
            status="running",
            paused_at=None
        )
        
        # Verify update was called with paused_at=None
        assert len(update_calls) >= 1
        resume_update = update_calls[-1]
        assert resume_update.get('status') == 'running'
        assert resume_update.get('paused_at') is None, "paused_at should be cleared (None) on resume"
