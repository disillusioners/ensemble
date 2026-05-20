"""Notification broadcaster service for global SSE notifications.

This service broadcasts notification events (e.g., root instance completion)
to all connected SSE clients globally, not per-instance like LiveEventHub.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from daemon.constants import SSE_PING_INTERVAL, SSE_QUEUE_MAXSIZE, SSE_TIMEOUT_S

logger = logging.getLogger(__name__)


class NotificationBroadcaster:
    """Singleton service for broadcasting global notifications to all SSE clients.

    Unlike LiveEventHub which is per-instance, this broadcaster sends notifications
    to ALL connected clients (e.g., root instance completion events).

    Uses per-connection asyncio.Queues for non-blocking event delivery.
    """

    def __init__(self, max_queue_size: int = 50) -> None:
        """Initialize NotificationBroadcaster.

        Args:
            max_queue_size: Max size per connection queue (backpressure).
        """
        self._max_queue_size = max_queue_size

        # Per-connection queues: connection_id -> asyncio.Queue
        self._connections: dict[str, asyncio.Queue] = {}

        # Lock for thread-safe connection management
        self._lock = asyncio.Lock()

        # Counter for unique connection IDs
        self._connection_counter = 0

    # -------------------------------------------------------------------------
    # Connection Management
    # -------------------------------------------------------------------------

    async def add_connection(self, queue: asyncio.Queue) -> str:
        """Register a new SSE connection.

        Args:
            queue: The connection's asyncio.Queue for receiving notifications.

        Returns:
            The unique connection ID for this connection.
        """
        async with self._lock:
            self._connection_counter += 1
            connection_id = f"conn_{self._connection_counter}"
            self._connections[connection_id] = queue
            logger.debug(f"Connection added: {connection_id}, total: {len(self._connections)}")
            return connection_id

    async def remove_connection(self, connection_id: str) -> None:
        """Unregister an SSE connection.

        Args:
            connection_id: The connection ID to remove.
        """
        async with self._lock:
            self._connections.pop(connection_id, None)
            logger.debug(f"Connection removed: {connection_id}")

    async def get_connection_count(self) -> int:
        """Get number of active connections.

        Returns:
            Number of active SSE connections.
        """
        async with self._lock:
            return len(self._connections)

    # -------------------------------------------------------------------------
    # Notification Broadcasting
    # -------------------------------------------------------------------------

    async def emit(self, notification_data: dict[str, Any]) -> int:
        """Broadcast a notification to all connected clients.

        Args:
            notification_data: The notification data to broadcast.
                Expected keys: instance_id, agent_id, name, status, timestamp

        Returns:
            Number of clients that received the notification.
        """
        async with self._lock:
            connections = list(self._connections.items())
            dead_connections = []

            delivered_count = 0

            for connection_id, queue in connections:
                try:
                    queue.put_nowait(notification_data)
                    delivered_count += 1
                except asyncio.QueueFull:
                    # Queue full = slow consumer, mark as dead
                    dead_connections.append(connection_id)

            # Clean up dead connections
            for connection_id in dead_connections:
                self._connections.pop(connection_id, None)
                logger.debug(f"Removed dead connection: {connection_id}")

            if delivered_count > 0:
                logger.debug(f"Broadcast notification to {delivered_count} clients")
            elif dead_connections:
                logger.debug(f"Dropped notification (all connections dead)")

            return delivered_count

    async def emit_root_completion(
        self,
        instance_id: str,
        agent_id: str,
        agent_name: str | None,
        status: str,
    ) -> int:
        """Emit a notification for root instance completion.

        Args:
            instance_id: The completed instance ID.
            agent_id: The agent ID (e.g., "coder").
            agent_name: Optional agent display name.
            status: The terminal status (COMPLETED, ERROR, TERMINATED, FAILED).

        Returns:
            Number of clients that received the notification.
        """
        notification = {
            "instance_id": instance_id,
            "agent_id": agent_id,
            "name": agent_name or agent_id.title(),
            "status": status.upper(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        return await self.emit(notification)

    # -------------------------------------------------------------------------
    # Cleanup
    # -------------------------------------------------------------------------

    async def shutdown(self) -> None:
        """Shutdown the broadcaster, clearing all connections."""
        async with self._lock:
            self._connections.clear()
        logger.info("NotificationBroadcaster shutdown complete")


# Module-level singleton
_notification_broadcaster: NotificationBroadcaster | None = None


def get_notification_broadcaster() -> NotificationBroadcaster:
    """Get the global NotificationBroadcaster singleton instance.

    Returns:
        The shared NotificationBroadcaster instance.
    """
    global _notification_broadcaster
    if _notification_broadcaster is None:
        _notification_broadcaster = NotificationBroadcaster()
    return _notification_broadcaster
