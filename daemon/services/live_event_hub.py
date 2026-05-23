"""Live-only SSE event hub - no buffering.

Events are only streamed to active SSE connections. If no client is listening,
events are dropped silently (fire-and-forget).
"""
import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class LiveEventHub:
    """Hub for live-only SSE streaming without buffering.
    
    Each SSE connection registers its own asyncio.Queue. When events are
    broadcasted, they're sent directly to all registered queues (if any).
    If no queues are registered, events are dropped.
    
    This replaces the old EventBus which buffered events regardless of
    client connection state.
    """
    
    def __init__(self, max_queue_size: int = 50) -> None:
        """Initialize LiveEventHub.
        
        Args:
            max_queue_size: Max size per connection queue (backpressure).
        """
        self._max_queue_size = max_queue_size
        
        # Per-instance connection registry: instance_id -> set of Queues
        self._connections: dict[str, set[asyncio.Queue]] = {}
        
        # Lock for thread-safe connection management
        self._lock = asyncio.Lock()
    
    # -------------------------------------------------------------------------
    # Connection Management
    # -------------------------------------------------------------------------
    
    async def add_connection(self, instance_id: str, queue: asyncio.Queue) -> None:
        """Register an SSE connection for an instance.
        
        Args:
            instance_id: The instance to stream events for.
            queue: The connection's asyncio.Queue for receiving events.
        """
        async with self._lock:
            if instance_id not in self._connections:
                self._connections[instance_id] = set()
            self._connections[instance_id].add(queue)
            logger.debug(f"Connection added for {instance_id}, total: {len(self._connections[instance_id])}")
    
    async def remove_connection(self, instance_id: str, queue: asyncio.Queue) -> None:
        """Unregister an SSE connection.
        
        Args:
            instance_id: The instance ID.
            queue: The connection's queue to remove.
        """
        async with self._lock:
            if instance_id in self._connections:
                self._connections[instance_id].discard(queue)
                if not self._connections[instance_id]:
                    del self._connections[instance_id]
                logger.debug(f"Connection removed for {instance_id}")
    
    async def get_connection_count(self, instance_id: str) -> int:
        """Get number of active connections for an instance.
        
        Args:
            instance_id: The instance ID.
            
        Returns:
            Number of active SSE connections.
        """
        async with self._lock:
            return len(self._connections.get(instance_id, set()))
    
    # -------------------------------------------------------------------------
    # Event Streaming
    # -------------------------------------------------------------------------
    
    async def stream_checkpoint(
        self,
        instance_id: str,
        messages: list[dict],
        checkpoint_id: str,
        tool_outputs: dict | None = None,
    ) -> None:
        """Stream checkpoint event to all active connections.
        
        Args:
            instance_id: The instance this checkpoint belongs to.
            messages: Pre-serialized list of message dicts.
            checkpoint_id: Checkpoint ID from LangGraph state.
            tool_outputs: Optional tool outputs map.
        """
        if not messages:
            return
        
        event: dict[str, Any] = {
            "instance_id": instance_id,
            "event_type": "checkpoint",
            "event_id": checkpoint_id,
            "messages": messages,
            "checkpoint_id": checkpoint_id,
        }
        if tool_outputs:
            event["tool_outputs"] = tool_outputs
        
        await self._stream_to_connections(instance_id, event)
    
    async def stream_message(
        self,
        instance_id: str,
        message: dict,
        event_type: str = "message",
        checkpoint_id: str | None = None,
    ) -> None:
        """Stream message event to all active connections.
        
        Args:
            instance_id: The instance this message belongs to.
            message: Pre-serialized message dict.
            event_type: Type of message event.
            checkpoint_id: Optional checkpoint ID.
        """
        event: dict[str, Any] = {
            "instance_id": instance_id,
            "event_type": event_type,
            "event_id": message.get("message_id", ""),
            "message": message,
            "checkpoint_id": checkpoint_id,
        }
        
        await self._stream_to_connections(instance_id, event)
    
    async def _stream_to_connections(self, instance_id: str, event: dict[str, Any]) -> None:
        """Stream event to all active connections for an instance.
        
        If no connections exist, the event is silently dropped.
        
        Args:
            instance_id: The instance ID.
            event: The event dict to stream.
        """
        async with self._lock:
            connections = list(self._connections.get(instance_id, set()))
            dead_queues = []
            
            for queue in connections:
                try:
                    queue.put_nowait(event)
                except (asyncio.QueueFull, asyncio.QueueShutDown):
                    dead_queues.append(queue)
            
            # Clean up dead connections (queues that are full = slow consumer)
            for q in dead_queues:
                self._connections.get(instance_id, set()).discard(q)
    
    # -------------------------------------------------------------------------
    # Lifecycle Events (still need DB persistence + notification)
    # -------------------------------------------------------------------------
    
    async def stream_error(
        self,
        instance_id: str,
        error: dict[str, Any] | None = None,
    ) -> None:
        """Stream error event to active connections.
        
        Args:
            instance_id: The instance ID.
            error: Error data dict.
        """
        event: dict[str, Any] = {
            "instance_id": instance_id,
            "event_type": "error",
            "error": error,
        }
        await self._stream_to_connections(instance_id, event)
    
    async def stream_status_change(self, instance_id: str, status: str, agent_id: str | None = None) -> None:
        """Stream status change event to all active connections.

        Args:
            instance_id: The instance ID.
            status: The new status value.
            agent_id: The agent ID (optional, for filtering KB instances on frontend).
        """
        event: dict[str, Any] = {
            "instance_id": instance_id,
            "event_type": "status_change",
            "status": status,
        }
        if agent_id is not None:
            event["agent_id"] = agent_id
        await self._stream_to_connections(instance_id, event)

    async def stream_lifecycle(
        self,
        instance_id: str,
        event_type: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Stream lifecycle event to active connections.

        Args:
            instance_id: The instance ID.
            event_type: Lifecycle event type (completed, failed, etc.).
            data: Optional event data.
        """
        event: dict[str, Any] = {
            "instance_id": instance_id,
            "event_type": event_type,
        }
        if data:
            event["data"] = data
        await self._stream_to_connections(instance_id, event)
    
    # -------------------------------------------------------------------------
    # Cleanup
    # -------------------------------------------------------------------------
    
    async def cleanup_instance(self, instance_id: str) -> None:
        """Remove all connections for an instance.
        
        Args:
            instance_id: The instance ID to clean up.
        """
        async with self._lock:
            self._connections.pop(instance_id, None)
            logger.debug(f"Cleaned up connections for {instance_id}")
    
    async def shutdown(self) -> None:
        """Shutdown the hub, clearing all connections."""
        async with self._lock:
            self._connections.clear()
        logger.info("LiveEventHub shutdown complete")
