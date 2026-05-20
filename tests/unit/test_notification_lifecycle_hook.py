"""Unit tests for notification lifecycle hook (root instance completion)."""

import pytest
from unittest.mock import Mock, AsyncMock, MagicMock, patch
from datetime import datetime

from daemon.services.event_publisher import EventPublisherService
from daemon.services.notification_broadcaster import NotificationBroadcaster


@pytest.fixture
def mock_instance_metadata():
    """Create mock instance metadata."""
    meta = Mock()
    meta.instance_id = "test-root-123"
    meta.agent_id = "coder"
    meta.agent_name = "Coder Agent"
    meta.status = "COMPLETED"
    return meta


@pytest.fixture
def mock_manager(mock_instance_metadata):
    """Create a mock InstanceManager with broadcaster and instance repository."""
    manager = Mock()

    # Mock broadcaster
    broadcaster = Mock(spec=NotificationBroadcaster)
    broadcaster.emit_root_completion = AsyncMock(return_value=1)
    manager._notification_broadcaster = broadcaster

    # Mock instance repository
    instance_repo = Mock()
    instance_repo.get = Mock(return_value=mock_instance_metadata)
    manager._instance_repository = instance_repo

    # Mock event bus
    event_bus = Mock()
    event_bus.create_event = AsyncMock()
    manager._event_bus = event_bus

    return manager


@pytest.fixture
def event_publisher(mock_manager):
    """Create EventPublisherService with mocked manager."""
    return EventPublisherService(manager=mock_manager)


class TestRootInstanceNotification:
    """Tests for root instance notification triggering."""

    @pytest.mark.asyncio
    async def test_root_completion_triggers_notification(self, event_publisher, mock_manager):
        """Root instance reaching terminal state triggers broadcast_notification call."""
        # Call the lifecycle event with parent_id=None (root instance)
        await event_publisher._publish_instance_lifecycle_event(
            instance_id="test-root-456",
            status="COMPLETED",
            parent_id=None,  # Root instance
        )

        # Verify emit_root_completion was called
        mock_manager._notification_broadcaster.emit_root_completion.assert_called_once()

        # Verify correct arguments
        call_args = mock_manager._notification_broadcaster.emit_root_completion.call_args
        assert call_args.kwargs["instance_id"] == "test-root-456"
        assert call_args.kwargs["status"] == "COMPLETED"

    @pytest.mark.asyncio
    async def test_root_completion_notification_includes_agent_info(
        self, event_publisher, mock_manager, mock_instance_metadata
    ):
        """Notification includes correct agent_id and agent_name from instance metadata."""
        await event_publisher._publish_instance_lifecycle_event(
            instance_id="test-root-123",
            status="COMPLETED",
            parent_id=None,
        )

        call_args = mock_manager._notification_broadcaster.emit_root_completion.call_args
        assert call_args.kwargs["agent_id"] == mock_instance_metadata.agent_id
        assert call_args.kwargs["agent_name"] == mock_instance_metadata.agent_name

    @pytest.mark.asyncio
    async def test_root_error_status_triggers_notification(self, event_publisher, mock_manager):
        """Root instance reaching ERROR status triggers notification."""
        await event_publisher._publish_instance_lifecycle_event(
            instance_id="test-root-error",
            status="ERROR",
            parent_id=None,
            error="Something went wrong",
        )

        mock_manager._notification_broadcaster.emit_root_completion.assert_called_once()
        call_args = mock_manager._notification_broadcaster.emit_root_completion.call_args
        assert call_args.kwargs["status"] == "ERROR"

    @pytest.mark.asyncio
    async def test_root_terminated_status_triggers_notification(self, event_publisher, mock_manager):
        """Root instance reaching TERMINATED status triggers notification."""
        await event_publisher._publish_instance_lifecycle_event(
            instance_id="test-root-terminated",
            status="TERMINATED",
            parent_id=None,
        )

        mock_manager._notification_broadcaster.emit_root_completion.assert_called_once()


class TestChildInstanceNoNotification:
    """Tests for child instance (non-root) NOT triggering notifications."""

    @pytest.mark.asyncio
    async def test_child_instance_does_not_trigger_notification(
        self, event_publisher, mock_manager
    ):
        """Child instances with parent_id do NOT emit root completion notifications."""
        # Call the lifecycle event with parent_id set (child instance)
        await event_publisher._publish_instance_lifecycle_event(
            instance_id="test-child-789",
            status="COMPLETED",
            parent_id="parent-instance-123",  # Has parent = child instance
        )

        # Verify emit_root_completion was NOT called
        mock_manager._notification_broadcaster.emit_root_completion.assert_not_called()

    @pytest.mark.asyncio
    async def test_child_instance_still_publishes_event_bus(
        self, event_publisher, mock_manager
    ):
        """Child instances still publish lifecycle events to EventBus (just no SSE notification)."""
        await event_publisher._publish_instance_lifecycle_event(
            instance_id="test-child-event",
            status="COMPLETED",
            parent_id="parent-id",
        )

        # EventBus should still be called
        mock_manager._event_bus.create_event.assert_called_once()


class TestNotificationPayload:
    """Tests for notification payload structure and content."""

    @pytest.mark.asyncio
    async def test_notification_contains_instance_id(self, event_publisher, mock_manager):
        """Notification payload includes instance_id."""
        await event_publisher._publish_instance_lifecycle_event(
            instance_id="instance-abc-123",
            status="COMPLETED",
            parent_id=None,
        )

        call_args = mock_manager._notification_broadcaster.emit_root_completion.call_args
        assert "instance_id" in call_args.kwargs
        assert call_args.kwargs["instance_id"] == "instance-abc-123"

    @pytest.mark.asyncio
    async def test_notification_contains_agent_id(self, event_publisher, mock_manager, mock_instance_metadata):
        """Notification payload includes agent_id."""
        await event_publisher._publish_instance_lifecycle_event(
            instance_id="instance-agent-test",
            status="COMPLETED",
            parent_id=None,
        )

        call_args = mock_manager._notification_broadcaster.emit_root_completion.call_args
        assert "agent_id" in call_args.kwargs
        assert call_args.kwargs["agent_id"] == mock_instance_metadata.agent_id

    @pytest.mark.asyncio
    async def test_notification_contains_status(self, event_publisher, mock_manager):
        """Notification payload includes status."""
        await event_publisher._publish_instance_lifecycle_event(
            instance_id="instance-status-test",
            status="COMPLETED",
            parent_id=None,
        )

        call_args = mock_manager._notification_broadcaster.emit_root_completion.call_args
        assert "status" in call_args.kwargs

    @pytest.mark.asyncio
    async def test_notification_contains_timestamp_in_broadcast(
        self, event_publisher, mock_manager, mock_instance_metadata
    ):
        """Notification is broadcast (timestamp added by broadcaster.emit_root_completion)."""
        # The timestamp is added by NotificationBroadcaster.emit_root_completion()
        # We verify the broadcast was called, which means timestamp will be generated
        await event_publisher._publish_instance_lifecycle_event(
            instance_id="instance-time-test",
            status="COMPLETED",
            parent_id=None,
        )

        mock_manager._notification_broadcaster.emit_root_completion.assert_called_once()


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    @pytest.mark.asyncio
    async def test_notification_skipped_when_broadcaster_none(self, mock_instance_metadata):
        """No error when broadcaster is not initialized (returns early)."""
        manager = Mock()
        manager._notification_broadcaster = None  # Not set
        manager._instance_repository = Mock()
        manager._instance_repository.get.return_value = mock_instance_metadata
        manager._event_bus = Mock()

        publisher = EventPublisherService(manager=manager)

        # Should not raise
        await publisher._publish_instance_lifecycle_event(
            instance_id="test-no-broadcaster",
            status="COMPLETED",
            parent_id=None,
        )

    @pytest.mark.asyncio
    async def test_notification_skipped_when_instance_not_found(self, mock_manager):
        """No error when instance not found in repository."""
        mock_manager._instance_repository.get.return_value = None

        publisher = EventPublisherService(manager=mock_manager)

        # Should not raise (just logs warning)
        await publisher._publish_instance_lifecycle_event(
            instance_id="nonexistent-instance",
            status="COMPLETED",
            parent_id=None,
        )

    @pytest.mark.asyncio
    async def test_notification_uses_agent_id_as_fallback_name(
        self, event_publisher, mock_manager, mock_instance_metadata
    ):
        """When agent_name is None, uses agent_id.title() as name."""
        mock_instance_metadata.agent_name = None
        mock_manager._instance_repository.get.return_value = mock_instance_metadata

        await event_publisher._publish_instance_lifecycle_event(
            instance_id="test-no-name",
            status="COMPLETED",
            parent_id=None,
        )

        # The broadcaster.emit_root_completion will handle None agent_name
        # and use agent_id.title() as fallback
        call_args = mock_manager._notification_broadcaster.emit_root_completion.call_args
        assert call_args.kwargs["agent_name"] is None  # Passed as None, broadcaster handles it


class TestEventBusIntegration:
    """Tests for EventBus integration alongside notifications."""

    @pytest.mark.asyncio
    async def test_lifecycle_event_published_to_event_bus(self, event_publisher, mock_manager):
        """Lifecycle event is published to EventBus for all instances (root and child)."""
        from daemon.repositories.event.models import EventKind

        await event_publisher._publish_instance_lifecycle_event(
            instance_id="test-instance-bus",
            status="COMPLETED",
            parent_id=None,
        )

        mock_manager._event_bus.create_event.assert_called_once()
        call_args = mock_manager._event_bus.create_event.call_args
        assert call_args.kwargs["instance_id"] == "test-instance-bus"
        assert call_args.kwargs["kind"] == EventKind.INSTANCE_LIFECYCLE

    @pytest.mark.asyncio
    async def test_event_bus_receives_error_info(self, event_publisher, mock_manager):
        """Error information is included in EventBus event data."""
        await event_publisher._publish_instance_lifecycle_event(
            instance_id="test-error-instance",
            status="ERROR",
            parent_id=None,
            error="Database connection failed",
        )

        call_args = mock_manager._event_bus.create_event.call_args
        assert call_args.kwargs["data"]["error"] == "Database connection failed"
