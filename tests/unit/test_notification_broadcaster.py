"""Unit tests for NotificationBroadcaster service."""

import pytest
import asyncio

from daemon.services.notification_broadcaster import (
    NotificationBroadcaster,
    get_notification_broadcaster,
)


class TestConnectionManagement:
    """Tests for connection management operations."""

    @pytest.mark.asyncio
    async def test_add_connection(self):
        """Adding a connection returns a connection ID and registers it."""
        broadcaster = NotificationBroadcaster()
        queue = asyncio.Queue()

        conn_id = await broadcaster.add_connection(queue)

        assert conn_id.startswith("conn_")
        count = await broadcaster.get_connection_count()
        assert count == 1

    @pytest.mark.asyncio
    async def test_remove_connection(self):
        """Removing a connection unregisters it."""
        broadcaster = NotificationBroadcaster()
        queue = asyncio.Queue()

        conn_id = await broadcaster.add_connection(queue)
        assert await broadcaster.get_connection_count() == 1

        await broadcaster.remove_connection(conn_id)
        assert await broadcaster.get_connection_count() == 0

    @pytest.mark.asyncio
    async def test_remove_nonexistent_connection(self):
        """Removing a non-existent connection does not raise."""
        broadcaster = NotificationBroadcaster()

        # Should not raise
        await broadcaster.remove_connection("nonexistent_conn_id")
        assert await broadcaster.get_connection_count() == 0

    @pytest.mark.asyncio
    async def test_multiple_connections(self):
        """Multiple connections can be registered."""
        broadcaster = NotificationBroadcaster()
        queue1 = asyncio.Queue()
        queue2 = asyncio.Queue()
        queue3 = asyncio.Queue()

        await broadcaster.add_connection(queue1)
        await broadcaster.add_connection(queue2)
        await broadcaster.add_connection(queue3)

        assert await broadcaster.get_connection_count() == 3

    @pytest.mark.asyncio
    async def test_unique_connection_ids(self):
        """Each connection gets a unique ID."""
        broadcaster = NotificationBroadcaster()
        queue1 = asyncio.Queue()
        queue2 = asyncio.Queue()

        id1 = await broadcaster.add_connection(queue1)
        id2 = await broadcaster.add_connection(queue2)

        assert id1 != id2


class TestNotificationBroadcasting:
    """Tests for notification broadcasting."""

    @pytest.mark.asyncio
    async def test_emit_delivered(self):
        """Emitted notifications are delivered to registered connections."""
        broadcaster = NotificationBroadcaster()
        queue = asyncio.Queue()
        notification = {
            "instance_id": "test-123",
            "agent_id": "coder",
            "name": "Coder",
            "status": "COMPLETED",
            "timestamp": "2024-01-01T00:00:00Z",
        }

        await broadcaster.add_connection(queue)
        delivered = await broadcaster.emit(notification)

        assert delivered == 1
        received = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert received == notification

    @pytest.mark.asyncio
    async def test_emit_multiple_connections(self):
        """Emitted notifications reach all connections."""
        broadcaster = NotificationBroadcaster()
        queue1 = asyncio.Queue()
        queue2 = asyncio.Queue()
        notification = {"instance_id": "test-123", "status": "COMPLETED"}

        await broadcaster.add_connection(queue1)
        await broadcaster.add_connection(queue2)
        delivered = await broadcaster.emit(notification)

        assert delivered == 2

        received1 = await asyncio.wait_for(queue1.get(), timeout=1.0)
        received2 = await asyncio.wait_for(queue2.get(), timeout=1.0)

        assert received1 == notification
        assert received2 == notification

    @pytest.mark.asyncio
    async def test_emit_no_connections(self):
        """Emitting with no connections returns 0 and doesn't raise."""
        broadcaster = NotificationBroadcaster()
        notification = {"instance_id": "test-123"}

        # Should not raise
        delivered = await broadcaster.emit(notification)
        assert delivered == 0

    @pytest.mark.asyncio
    async def test_emit_root_completion(self):
        """emit_root_completion creates proper notification structure."""
        broadcaster = NotificationBroadcaster()
        queue = asyncio.Queue()

        await broadcaster.add_connection(queue)
        delivered = await broadcaster.emit_root_completion(
            instance_id="test-123",
            agent_id="coder",
            agent_name="Coder Agent",
            status="COMPLETED",
        )

        assert delivered == 1
        received = await asyncio.wait_for(queue.get(), timeout=1.0)

        assert received["instance_id"] == "test-123"
        assert received["agent_id"] == "coder"
        assert received["name"] == "Coder Agent"
        assert received["status"] == "COMPLETED"
        assert "timestamp" in received

    @pytest.mark.asyncio
    async def test_emit_root_completion_default_name(self):
        """emit_root_completion uses agent_id.title() when agent_name is None."""
        broadcaster = NotificationBroadcaster()
        queue = asyncio.Queue()

        await broadcaster.add_connection(queue)
        delivered = await broadcaster.emit_root_completion(
            instance_id="test-123",
            agent_id="coder",
            agent_name=None,
            status="COMPLETED",
        )

        assert delivered == 1
        received = await asyncio.wait_for(queue.get(), timeout=1.0)

        assert received["name"] == "Coder"

    @pytest.mark.asyncio
    async def test_emit_status_uppercase(self):
        """emit_root_completion uppercases the status."""
        broadcaster = NotificationBroadcaster()
        queue = asyncio.Queue()

        await broadcaster.add_connection(queue)
        await broadcaster.emit_root_completion(
            instance_id="test-123",
            agent_id="coder",
            agent_name="Coder",
            status="completed",  # lowercase input
        )

        received = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert received["status"] == "COMPLETED"


class TestQueueFullHandling:
    """Tests for handling of full queues (dead connections)."""

    @pytest.mark.asyncio
    async def test_queue_full_removes_dead_connection(self):
        """Full queue triggers connection cleanup."""
        broadcaster = NotificationBroadcaster(max_queue_size=1)
        queue = asyncio.Queue(maxsize=1)

        conn_id = await broadcaster.add_connection(queue)
        assert await broadcaster.get_connection_count() == 1

        # Fill the queue
        await queue.put("dummy")

        # Emit should mark queue as dead and remove it
        await broadcaster.emit({"test": "data"})

        # Connection should be removed
        assert await broadcaster.get_connection_count() == 0

    @pytest.mark.asyncio
    async def test_other_connections_survive_queue_full(self):
        """Other connections are not affected when one queue is full."""
        broadcaster = NotificationBroadcaster(max_queue_size=1)
        queue1 = asyncio.Queue(maxsize=1)
        queue2 = asyncio.Queue(maxsize=10)

        conn_id1 = await broadcaster.add_connection(queue1)
        conn_id2 = await broadcaster.add_connection(queue2)

        # Fill queue1
        await queue1.put("dummy")

        # Emit should remove queue1, keep queue2
        await broadcaster.emit({"test": "data"})

        assert await broadcaster.get_connection_count() == 1

        # Queue2 should still receive the event
        received = await asyncio.wait_for(queue2.get(), timeout=1.0)
        assert received["test"] == "data"


class TestCleanup:
    """Tests for cleanup operations."""

    @pytest.mark.asyncio
    async def test_shutdown_clears_all_connections(self):
        """shutdown removes all connections."""
        broadcaster = NotificationBroadcaster()
        queue1 = asyncio.Queue()
        queue2 = asyncio.Queue()

        await broadcaster.add_connection(queue1)
        await broadcaster.add_connection(queue2)

        await broadcaster.shutdown()

        assert await broadcaster.get_connection_count() == 0

    @pytest.mark.asyncio
    async def test_shutdown_on_empty_broadcaster(self):
        """Shutdown on empty broadcaster does not raise."""
        broadcaster = NotificationBroadcaster()

        # Should not raise
        await broadcaster.shutdown()


class TestSingleton:
    """Tests for singleton behavior."""

    def test_get_notification_broadcaster_returns_instance(self):
        """get_notification_broadcaster returns a NotificationBroadcaster instance."""
        broadcaster = get_notification_broadcaster()
        assert isinstance(broadcaster, NotificationBroadcaster)

    def test_get_notification_broadcaster_returns_same_instance(self):
        """get_notification_broadcaster returns the same instance."""
        broadcaster1 = get_notification_broadcaster()
        broadcaster2 = get_notification_broadcaster()
        assert broadcaster1 is broadcaster2
