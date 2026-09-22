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
from daemon.repositories.instance.repository import KB_AGENT_IDS

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
                except Exception as e:
                    logger.warning(f"Failed to enqueue for connection {connection_id}: {e}")
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
        project_id: str | None = None,
        instance_name: str | None = None,
        result_summary: str | None = None,
    ) -> int:
        """Emit a notification for root instance completion.

        KB agent completions (experiencer, kb-importer) are filtered out
        to avoid unnecessary SSE traffic for background processes.

        Args:
            instance_id: The completed instance ID.
            agent_id: The agent ID (e.g., "developer").
            agent_name: Optional agent display name.
            status: The terminal status (COMPLETED, ERROR, TERMINATED, FAILED).
            project_id: Optional project ID the instance belongs to.
            instance_name: Optional instance display name/title.
            result_summary: Optional pre-fetched agent's last assistant
                message content (the production extraction seam
                ``manager._get_last_assistant_message_raw``). When
                not ``None`` it's added to the broadcast data dict
                so ``/api/notifications/stream`` SSE subscribers
                receive it on the notification frame. v0.13.9 fix
                (fix/job-completed-result-arm, 2026-09-22): the
                keyword is backward-compatible — older callers omit
                it and the field is silently absent from the
                payload, matching the pre-fix shape exactly.

        Returns:
            Number of clients that received the notification.
        """
        # Filter out KB agent completions
        if agent_id in KB_AGENT_IDS:
            logger.debug(f"Skipping root_completion broadcast for KB agent: {agent_id}")
            return 0

        notification = {
            "instance_id": instance_id,
            "agent_id": agent_id,
            "name": agent_name or agent_id.title(),
            "status": status.upper(),
            "project_id": project_id,
            "instance_name": instance_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        # v0.13.9 fix (fix/job-completed-result-arm, 2026-09-22): add
        # ``result_summary`` to the broadcast data dict when the caller
        # supplied one. Older callers (no kwarg → ``None``) keep the
        # pre-fix shape exactly — the field is absent, not ``"None"`` —
        # so SSE consumers that gate on key presence still work.
        if result_summary is not None:
            notification["result_summary"] = result_summary
        return await self.emit(notification)

    async def emit_question_escalation(
        self,
        instance_id: str,
        agent_id: str | None,
        question_pack_id: str | None,
        emission_index: int = 3,
    ) -> int:
        """Emit a wedge-guard question escalation to ALL connected clients.

        Mid-flight QA channel (design §4.4 / §8.7, 2026-09-21): fired
        UNCONDITIONALLY at ``emission_index=3`` (the wedge-guard
        escalation) so the operator sees the stuck question even when
        every ``watch_job`` row has been garbage-collected. Without
        this fan-out the escalation is logs-only. Parallel to
        ``emit_root_completion`` — never fired for the normal
        question/report paths.

        Args:
            instance_id: The wedged asker instance.
            agent_id: The asker's agent id (e.g. "leader").
            question_pack_id: The pending pack's durable id.
            emission_index: The wedge-guard emission index at escalation
                (3 by contract; parameterized for tests).

        Returns:
            Number of clients that received the notification.
        """
        notification = {
            "event_type": "question_escalation",
            "instance_id": instance_id,
            "agent_id": agent_id,
            "name": (agent_id or "unknown").title(),
            "status": "STUCK_AWAITING_ANSWER",
            "question_pack_id": question_pack_id,
            "emission_index": emission_index,
            "message": (
                f"Instance {(agent_id or 'unknown').title()} has been paused "
                f"awaiting an answer for ~60 minutes "
                f"({emission_index} heartbeat emissions). The wedge guard "
                f"terminated the asker — answer or re-dispatch the work."
            ),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        return await self.emit(notification)

    async def emit_instance_created(self, instance_data: dict[str, Any]) -> int:
        """Emit a notification for newly created root instance (no parent).

        This broadcasts to ALL connected clients so they can add the new
        root instance to their instance trees.

        KB agent instances (experiencer, kb-importer) are filtered out
        to reduce unnecessary SSE traffic.

        Args:
            instance_data: Full instance info dict with fields:
                instance_id, agent_id, parent_id, status, project_id,
                created_at, children, title.

        Returns:
            Number of clients that received the notification.
        """
        # Filter out KB agent instances
        agent_id = instance_data.get("agent_id", "")
        if agent_id in KB_AGENT_IDS:
            logger.debug(f"Skipping instance_created broadcast for KB agent: {agent_id}")
            return 0

        # Derive instance_name from explicit fields, then spawn metadata.
        instance_name = (
            instance_data.get("title")
            or instance_data.get("name")
            or (instance_data.get("instance_metadata") or {}).get("instance_name")
        )

        data_payload = {**instance_data, "instance_name": instance_name}

        notification = {
            "event_type": "instance_created",
            "data": data_payload,
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
