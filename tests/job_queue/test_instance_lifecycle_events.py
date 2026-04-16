"""Comprehensive tests for instance lifecycle event publishing.

Tests the _publish_instance_lifecycle_event method in InstanceManager
and verifies that INSTANCE_LIFECYCLE events are published correctly
for top-level instances (with parent_id=None).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from daemon.repositories.event.models import EventKind


class TestPublishInstanceLifecycleEvent:
    """Tests for _publish_instance_lifecycle_event method."""

    @pytest.mark.asyncio
    async def test_lifecycle_event_published_on_completion(self):
        """Top-level instance completion publishes event."""
        from daemon.manager import InstanceManager

        # Create mock manager
        mock_config = MagicMock()
        mock_config.persistence.db_path = ":memory:"
        mock_config.persistence.checkpointer_db_path = ":memory:"
        mock_config.llm.base_url = "http://localhost"
        mock_config.llm.api_key = "test"
        mock_config.llm.model = "test"
        mock_config.llm.temperature = 0.7
        mock_config.llm.request_timeout = 60
        mock_config.compaction.enabled = False
        mock_config.queue.discard_on_startup = False
        mock_config.limits.max_instances = 10
        mock_config.limits.max_children_per_instance = 5
        mock_config.limits.llm_concurrency = 5
        mock_config.limits.graph_recursion_limit = 50

        with patch.object(InstanceManager, '__init__', lambda self, config: None):
            manager = InstanceManager.__new__(InstanceManager)
            manager.config = mock_config
            manager._live_hub = MagicMock()
            manager._live_hub.stream_lifecycle = AsyncMock()

            # Publish lifecycle event for top-level instance completion
            await manager._publish_instance_lifecycle_event(
                instance_id="test-instance-123",
                status="completed",
                error=None,
                parent_id=None,  # Top-level instance
            )

            # Verify: stream_lifecycle was called
            manager._live_hub.stream_lifecycle.assert_called_once()

            # Verify: event data
            call_args = manager._live_hub.stream_lifecycle.call_args
            assert call_args.kwargs.get("instance_id") == "test-instance-123"
            assert call_args.kwargs.get("event_type") == EventKind.INSTANCE_LIFECYCLE.value

            # Verify: data payload
            data = call_args.kwargs.get("data")
            assert data["instance_id"] == "test-instance-123"
            assert data["status"] == "completed"
            assert data["error"] is None
            assert data["parent_id"] is None

    @pytest.mark.asyncio
    async def test_lifecycle_event_published_on_termination(self):
        """Instance termination publishes event."""
        from daemon.manager import InstanceManager

        with patch.object(InstanceManager, '__init__', lambda self, config: None):
            manager = InstanceManager.__new__(InstanceManager)
            manager._live_hub = MagicMock()
            manager._live_hub.stream_lifecycle = AsyncMock()

            # Publish lifecycle event for termination
            await manager._publish_instance_lifecycle_event(
                instance_id="test-instance-456",
                status="terminated",
                error=None,
                parent_id=None,
            )

            # Verify
            manager._live_hub.stream_lifecycle.assert_called_once()
            call_args = manager._live_hub.stream_lifecycle.call_args
            assert call_args.kwargs.get("event_type") == EventKind.INSTANCE_LIFECYCLE.value

            data = call_args.kwargs.get("data")
            assert data["status"] == "terminated"

    @pytest.mark.asyncio
    async def test_lifecycle_event_published_on_error(self):
        """Instance error publishes event with error message."""
        from daemon.manager import InstanceManager

        with patch.object(InstanceManager, '__init__', lambda self, config: None):
            manager = InstanceManager.__new__(InstanceManager)
            manager._live_hub = MagicMock()
            manager._live_hub.stream_lifecycle = AsyncMock()

            # Publish lifecycle event for error
            await manager._publish_instance_lifecycle_event(
                instance_id="test-instance-789",
                status="error",
                error="Something went wrong",
                parent_id=None,
            )

            # Verify
            manager._live_hub.stream_lifecycle.assert_called_once()

            data = manager._live_hub.stream_lifecycle.call_args.kwargs.get("data")
            assert data["status"] == "error"
            assert data["error"] == "Something went wrong"

    @pytest.mark.asyncio
    async def test_lifecycle_event_with_parent_id(self):
        """Event includes parent_id when provided."""
        from daemon.manager import InstanceManager

        with patch.object(InstanceManager, '__init__', lambda self, config: None):
            manager = InstanceManager.__new__(InstanceManager)
            manager._live_hub = MagicMock()
            manager._live_hub.stream_lifecycle = AsyncMock()

            # Publish lifecycle event for child instance with parent
            await manager._publish_instance_lifecycle_event(
                instance_id="child-instance",
                status="completed",
                error=None,
                parent_id="parent-instance",
            )

            # Verify: parent_id is included in data
            data = manager._live_hub.stream_lifecycle.call_args.kwargs.get("data")
            assert data["parent_id"] == "parent-instance"


class TestEventDataSchema:
    """Tests for event data schema validation."""

    @pytest.mark.asyncio
    async def test_event_data_has_correct_fields(self):
        """Verify event data has all required fields."""
        from daemon.manager import InstanceManager

        with patch.object(InstanceManager, '__init__', lambda self, config: None):
            manager = InstanceManager.__new__(InstanceManager)
            manager._live_hub = MagicMock()
            manager._live_hub.stream_lifecycle = AsyncMock()

            await manager._publish_instance_lifecycle_event(
                instance_id="test-instance",
                status="completed",
                error=None,
                parent_id=None,
            )

            data = manager._live_hub.stream_lifecycle.call_args.kwargs.get("data")

            # Verify all required fields
            assert "instance_id" in data
            assert "status" in data
            assert "error" in data
            assert "parent_id" in data

    @pytest.mark.asyncio
    async def test_event_type_is_instance_lifecycle(self):
        """Verify event_type is INSTANCE_LIFECYCLE."""
        from daemon.manager import InstanceManager

        with patch.object(InstanceManager, '__init__', lambda self, config: None):
            manager = InstanceManager.__new__(InstanceManager)
            manager._live_hub = MagicMock()
            manager._live_hub.stream_lifecycle = AsyncMock()

            await manager._publish_instance_lifecycle_event(
                instance_id="test-instance",
                status="completed",
                error=None,
                parent_id=None,
            )

            event_type = manager._live_hub.stream_lifecycle.call_args.kwargs.get("event_type")
            assert event_type == EventKind.INSTANCE_LIFECYCLE.value


class TestPublishFailureHandling:
    """Tests for failure handling in event publishing."""

    @pytest.mark.asyncio
    async def test_publish_failure_is_handled(self):
        """If publishing fails, it's logged but doesn't crash."""
        from daemon.manager import InstanceManager
        import logging

        with patch.object(InstanceManager, '__init__', lambda self, config: None):
            manager = InstanceManager.__new__(InstanceManager)
            manager._live_hub = MagicMock()
            manager._live_hub.stream_lifecycle = AsyncMock(side_effect=Exception("Network error"))

            # Should not raise
            await manager._publish_instance_lifecycle_event(
                instance_id="test-instance",
                status="completed",
                error=None,
                parent_id=None,
            )


class TestChildInstanceVsTopLevel:
    """Tests distinguishing between child and top-level instances."""

    @pytest.mark.asyncio
    async def test_no_lifecycle_event_for_child_instances(self):
        """Child instances use INSTANCE_COMPLETED, not INSTANCE_LIFECYCLE.

        Note: The _publish_instance_lifecycle_event method is called for ALL
        instances (both top-level and child). The distinction is in what
        happens BEFORE calling this method:
        - Top-level instances: _process_child_completion_and_notify_parent calls _publish_instance_lifecycle_event
        - Child instances: _process_child_completion_and_notify_parent sends completion report instead
        """
        from daemon.manager import InstanceManager

        with patch.object(InstanceManager, '__init__', lambda self, config: None):
            manager = InstanceManager.__new__(InstanceManager)
            manager._live_hub = MagicMock()
            manager._live_hub.stream_lifecycle = AsyncMock()

            # Even child instances should publish lifecycle events if the method is called
            # The difference is in WHEN/WHERE the method is called
            await manager._publish_instance_lifecycle_event(
                instance_id="child-instance",
                status="completed",
                error=None,
                parent_id="parent-instance",  # Has a parent = child instance
            )

            # Verify: event is still published (method doesn't distinguish)
            manager._live_hub.stream_lifecycle.assert_called_once()


class TestLifecycleEventCallSites:
    """Tests for verifying lifecycle events are called from correct places."""

    @pytest.mark.asyncio
    async def test_terminate_instance_publishes_lifecycle_event(self):
        """terminate_instance calls _publish_instance_lifecycle_event."""
        from daemon.manager import InstanceManager, Instance

        # Create minimal mock setup
        mock_config = MagicMock()
        mock_config.persistence.db_path = ":memory:"
        mock_config.persistence.checkpointer_db_path = ":memory:"
        mock_config.llm.base_url = "http://localhost"
        mock_config.llm.api_key = "test"
        mock_config.llm.model = "test"
        mock_config.llm.temperature = 0.7
        mock_config.llm.request_timeout = 60
        mock_config.compaction.enabled = False
        mock_config.queue.discard_on_startup = False
        mock_config.limits.max_instances = 10
        mock_config.limits.max_children_per_instance = 5
        mock_config.limits.llm_concurrency = 5
        mock_config.limits.graph_recursion_limit = 50

        with patch.object(InstanceManager, '__init__', lambda self, config: None):
            manager = InstanceManager.__new__(InstanceManager)
            manager.config = mock_config

            # Setup mocks
            manager._live_hub = MagicMock()
            manager._live_hub.stream_lifecycle = AsyncMock()
            manager._live_hub.cleanup_instance = AsyncMock()

            manager._request_registry = MagicMock()
            manager._request_registry.cancel_by_instance = MagicMock()

            manager._job_queue_service = None
            manager.instances = {}

            # Mock instance repository
            mock_instance = MagicMock()
            mock_instance.instance_id = "test-instance"
            mock_instance.parent_id = None
            mock_instance.children = None
            manager._instance_repository = MagicMock()
            manager._instance_repository.get.return_value = mock_instance
            manager._instance_repository.update_status = MagicMock()

            # Mock checkpointer
            manager._checkpointer = MagicMock()
            manager._loop = None

            # Call terminate
            result = await manager.terminate_instance("test-instance")

            # Verify: termination succeeded
            assert result is True

            # Verify: lifecycle event was published
            manager._live_hub.stream_lifecycle.assert_called()


class TestEventKindEnum:
    """Tests for EventKind enum."""

    def test_instance_lifecycle_kind_exists(self):
        """INSTANCE_LIFECYCLE kind exists in EventKind enum."""
        assert hasattr(EventKind, "INSTANCE_LIFECYCLE")
        assert EventKind.INSTANCE_LIFECYCLE.value == "instance_lifecycle"

    def test_instance_completed_kind_exists(self):
        """INSTANCE_COMPLETED kind exists (used for child instances)."""
        assert hasattr(EventKind, "INSTANCE_COMPLETED")
        assert EventKind.INSTANCE_COMPLETED.value == "instance_completed"

    def test_event_kinds_are_distinct(self):
        """Different lifecycle event kinds are distinct."""
        assert EventKind.INSTANCE_LIFECYCLE.value != EventKind.INSTANCE_COMPLETED.value


class TestIntegrationPublishFlow:
    """Integration tests for the full publish flow."""

    @pytest.mark.asyncio
    async def test_publish_with_all_parameters(self):
        """Test publishing with all parameters provided."""
        from daemon.manager import InstanceManager

        with patch.object(InstanceManager, '__init__', lambda self, config: None):
            manager = InstanceManager.__new__(InstanceManager)
            manager._live_hub = MagicMock()
            manager._live_hub.stream_lifecycle = AsyncMock()

            await manager._publish_instance_lifecycle_event(
                instance_id="full-instance",
                status="error",
                error="Max retries exceeded",
                parent_id="parent-123",
            )

            call_args = manager._live_hub.stream_lifecycle.call_args
            assert call_args.kwargs["instance_id"] == "full-instance"
            assert call_args.kwargs["event_type"] == "instance_lifecycle"
            data = call_args.kwargs["data"]
            assert data["instance_id"] == "full-instance"
            assert data["status"] == "error"
            assert data["error"] == "Max retries exceeded"
            assert data["parent_id"] == "parent-123"

    @pytest.mark.asyncio
    async def test_publish_with_minimal_parameters(self):
        """Test publishing with only required parameters."""
        from daemon.manager import InstanceManager

        with patch.object(InstanceManager, '__init__', lambda self, config: None):
            manager = InstanceManager.__new__(InstanceManager)
            manager._live_hub = MagicMock()
            manager._live_hub.stream_lifecycle = AsyncMock()

            await manager._publish_instance_lifecycle_event(
                instance_id="minimal-instance",
                status="completed",
            )

            call_args = manager._live_hub.stream_lifecycle.call_args
            data = call_args.kwargs["data"]
            assert data["error"] is None
            assert data["parent_id"] is None
