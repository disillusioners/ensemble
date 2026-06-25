"""Unit tests for SSE notification endpoint at GET /api/notifications/stream."""

import pytest
import asyncio
import json
from unittest.mock import Mock, AsyncMock, MagicMock, patch

from daemon.services.notification_broadcaster import NotificationBroadcaster, get_notification_broadcaster


class TestNotificationBroadcasterSSEIntegration:
    """Tests for NotificationBroadcaster integration with SSE patterns."""

    @pytest.mark.asyncio
    async def test_broadcaster_queue_for_sse(self):
        """Broadcaster add_connection returns ID that maps to the queue."""
        broadcaster = NotificationBroadcaster()
        queue = asyncio.Queue()

        conn_id = await broadcaster.add_connection(queue)
        assert conn_id.startswith("conn_")

        # Verify queue is stored
        assert await broadcaster.get_connection_count() == 1

        # Cleanup
        await broadcaster.shutdown()

    @pytest.mark.asyncio
    async def test_broadcaster_emits_to_sse_queue(self):
        """Broadcaster emit delivers notifications to registered queue."""
        broadcaster = NotificationBroadcaster()
        queue = asyncio.Queue()

        await broadcaster.add_connection(queue)

        notification = {
            "instance_id": "test-123",
            "agent_id": "developer",
            "name": "Developer Agent",
            "status": "COMPLETED",
            "timestamp": "2024-01-01T00:00:00Z",
        }

        delivered = await broadcaster.emit(notification)
        assert delivered == 1

        # Verify queue received the notification
        received = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert received == notification

        # Cleanup
        await broadcaster.shutdown()

    @pytest.mark.asyncio
    async def test_broadcaster_multiple_sse_clients(self):
        """Multiple SSE clients each get their own queue and receive broadcasts."""
        broadcaster = NotificationBroadcaster()
        queue1 = asyncio.Queue()
        queue2 = asyncio.Queue()

        await broadcaster.add_connection(queue1)
        await broadcaster.add_connection(queue2)

        notification = {"instance_id": "multi-cast", "status": "COMPLETED"}
        delivered = await broadcaster.emit(notification)
        assert delivered == 2

        # Both queues receive
        received1 = await asyncio.wait_for(queue1.get(), timeout=1.0)
        received2 = await asyncio.wait_for(queue2.get(), timeout=1.0)

        assert received1 == notification
        assert received2 == notification

        # Cleanup
        await broadcaster.shutdown()

    @pytest.mark.asyncio
    async def test_broadcaster_removes_connection_on_queue_full(self):
        """Full queue causes connection removal (simulates client disconnect)."""
        broadcaster = NotificationBroadcaster(max_queue_size=1)
        queue = asyncio.Queue(maxsize=1)

        conn_id = await broadcaster.add_connection(queue)
        assert await broadcaster.get_connection_count() == 1

        # Fill the queue
        await queue.put("dummy")

        # Emit - should fail to enqueue, remove connection
        await broadcaster.emit({"test": "data"})

        # Connection should be removed
        assert await broadcaster.get_connection_count() == 0

        # Cleanup
        await broadcaster.shutdown()

    @pytest.mark.asyncio
    async def test_broadcaster_singleton_pattern(self):
        """get_notification_broadcaster returns singleton for app-wide use."""
        broadcaster1 = get_notification_broadcaster()
        broadcaster2 = get_notification_broadcaster()

        assert broadcaster1 is broadcaster2

        # Cleanup
        await broadcaster1.shutdown()


class TestSSERouterIntegration:
    """Tests for the SSE router integration with NotificationBroadcaster."""

    @pytest.mark.asyncio
    async def test_broadcaster_direct_notification_flow(self):
        """Test the complete flow: emit_root_completion -> broadcast -> queue."""
        broadcaster = NotificationBroadcaster()
        queue = asyncio.Queue()

        await broadcaster.add_connection(queue)

        # Simulate root completion notification
        delivered = await broadcaster.emit_root_completion(
            instance_id="root-instance-123",
            agent_id="developer",
            agent_name="Developer Agent",
            status="COMPLETED",
        )

        assert delivered == 1

        # Verify queue received proper notification structure
        received = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert received["instance_id"] == "root-instance-123"
        assert received["agent_id"] == "developer"
        assert received["name"] == "Developer Agent"
        assert received["status"] == "COMPLETED"
        assert "timestamp" in received

        # Cleanup
        await broadcaster.shutdown()

    @pytest.mark.asyncio
    async def test_broadcaster_creates_sse_event_structure(self):
        """Test that broadcaster creates notification ready for SSE event format."""
        broadcaster = NotificationBroadcaster()
        queue = asyncio.Queue()

        await broadcaster.add_connection(queue)

        # Emit creates dict that can be JSON serialized for SSE
        notification = {
            "instance_id": "sse-event-test",
            "agent_id": "leader",
            "name": "Leader Agent",
            "status": "COMPLETED",
            "timestamp": "2024-06-15T10:30:00Z",
        }

        await broadcaster.emit(notification)

        # Receive and verify JSON serializable
        received = await asyncio.wait_for(queue.get(), timeout=1.0)
        json_str = json.dumps(received)  # Should not raise
        assert "instance_id" in json_str
        assert "sse-event-test" in json_str

        # Cleanup
        await broadcaster.shutdown()

    @pytest.mark.asyncio
    async def test_broadcaster_root_completion_json_format(self):
        """emit_root_completion creates JSON-serializable notification."""
        broadcaster = NotificationBroadcaster()
        queue = asyncio.Queue()

        await broadcaster.add_connection(queue)

        await broadcaster.emit_root_completion(
            instance_id="json-format-test",
            agent_id="developer",
            agent_name=None,  # Will use title()
            status="completed",  # Will be uppercased
        )

        received = await asyncio.wait_for(queue.get(), timeout=1.0)

        # Verify JSON serialization works
        json_str = json.dumps(received)
        parsed = json.loads(json_str)

        assert parsed["instance_id"] == "json-format-test"
        assert parsed["agent_id"] == "developer"
        assert parsed["name"] == "Developer"  # agent_id.title()
        assert parsed["status"] == "COMPLETED"  # uppercased
        assert "timestamp" in parsed

        # Cleanup
        await broadcaster.shutdown()

    @pytest.mark.asyncio
    async def test_broadcaster_heartbeat_needed_for_long_idle(self):
        """Test that broadcaster doesn't interfere with ping/heartbeat (handled by SSE lib)."""
        broadcaster = NotificationBroadcaster()
        queue = asyncio.Queue()

        await broadcaster.add_connection(queue)

        # No notifications sent - broadcaster just holds the connection
        # The ping interval is handled by sse_starlette's EventSourceResponse
        assert await broadcaster.get_connection_count() == 1

        # Emit one notification after some time
        await broadcaster.emit({"type": "heartbeat_check"})
        received = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert received["type"] == "heartbeat_check"

        # Cleanup
        await broadcaster.shutdown()


class TestSSEEventFormat:
    """Tests for SSE event format compliance."""

    @pytest.mark.asyncio
    async def test_notification_data_is_json_serializable(self):
        """Notifications are JSON-serializable for SSE data field."""
        broadcaster = NotificationBroadcaster()
        queue = asyncio.Queue()

        await broadcaster.add_connection(queue)

        notifications = [
            {
                "instance_id": "simple-test",
                "status": "COMPLETED",
            },
            {
                "instance_id": "full-test",
                "agent_id": "developer",
                "name": "Developer Agent",
                "status": "COMPLETED",
                "timestamp": "2024-01-01T00:00:00Z",
            },
        ]

        for notif in notifications:
            await broadcaster.emit(notif)
            received = await asyncio.wait_for(queue.get(), timeout=1.0)
            # Should be JSON serializable
            json_str = json.dumps(received)
            assert json_str is not None

        # Cleanup
        await broadcaster.shutdown()

    @pytest.mark.asyncio
    async def test_notification_event_type_is_notification(self):
        """SSE event type for notifications is 'notification' (router sends this)."""
        # The SSE router yields: {"event": "notification", "data": json.dumps(notification)}
        # We verify the data payload is ready for this format
        broadcaster = NotificationBroadcaster()
        queue = asyncio.Queue()

        await broadcaster.add_connection(queue)

        notification = {"instance_id": "event-type-test", "status": "COMPLETED"}
        await broadcaster.emit(notification)

        received = await asyncio.wait_for(queue.get(), timeout=1.0)

        # Data should be a dict that can be put in SSE format
        assert isinstance(received, dict)
        assert received["instance_id"] == "event-type-test"

        # Cleanup
        await broadcaster.shutdown()
