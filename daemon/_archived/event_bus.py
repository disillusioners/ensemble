"""DEPRECATED: Archived EventBus - preserved for reference only.

This module has been replaced by LiveEventHub for live-only SSE streaming.
See: daemon/services/live_event_hub.py
"""

"""EventBus: Checkpoint-based event delivery service for SSE.

Checkpoint events contain the full message state from LangGraph:
  checkpoint (instance_id, messages, checkpoint_id, tool_outputs)
  → asyncio.Queue → SSE picks up immediately

Error events are persisted to DB:
  error (instance_id, kind=ERROR, data=error)
  → INSERT INTO event table → notify SSE listeners via asyncio.Event
"""
import asyncio
import logging
import threading
from typing import Any, Optional

from daemon.repositories.event.models import EventKind

logger = logging.getLogger(__name__)


class EventBus:
    """Checkpoint-based event delivery service combining DB persistence with in-memory streaming.

    Checkpoint events are delivered via asyncio.Queue without DB persistence.
    Error events are persisted to the database via EventRepository, with an
    asyncio.Event notification to wake SSE listeners.

    The streaming queues now carry checkpoint payloads instead of individual
    streaming deltas.
    """

    def __init__(
        self,
        event_repo: Any,
        streaming_queue_size: int = 100,
        global_queue_size: int = 1000,
    ) -> None:
        """Initialize EventBus.

        Args:
            event_repo: EventRepository instance for DB operations (used for error events)
            streaming_queue_size: Max queue size per streaming channel
            global_queue_size: Max queue size for global subscribers
        """
        self._event_repo = event_repo

        # Per-instance streaming channels (queues) - now carry checkpoint events
        self._streaming_channels: dict[str, asyncio.Queue] = {}

        # Per-instance notification events (for error/lifecycle events)
        self._notifications: dict[str, asyncio.Event] = {}

        # Global subscribers (receive ALL events)
        self._global_subscribers: dict[str, asyncio.Queue] = {}

        # Queue size limits
        self._streaming_queue_size = streaming_queue_size
        self._global_queue_size = global_queue_size

        # Thread safety
        self._sync_lock = threading.Lock()

        # Event loop reference for sync operations
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # -------------------------------------------------------------------------
    # Checkpoint Event Method (replaces all streaming events)
    # -------------------------------------------------------------------------

    async def broadcast_checkpoint_event(
        self,
        instance_id: str,
        messages: list[dict],
        checkpoint_id: str,
        tool_outputs: dict | None = None,
    ) -> None:
        """Broadcast a checkpoint event containing full message state.

        This replaces all individual streaming events (content_chunk, thinking,
        tool_call, tool_complete) and lifecycle events.

        Args:
            instance_id: The instance this checkpoint belongs to.
            messages: Pre-serialized list of message dicts.
            checkpoint_id: Checkpoint ID from LangGraph state.
            tool_outputs: Map of tool_call_id -> output content.
        """
        # Messages arrive PRE-SERIALIZED from manager (list[dict], not BaseMessage objects).
        # Skip empty checkpoints — LangGraph nodes may complete without new messages.
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

        queue = self.get_streaming_queue(instance_id)
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning(f"Queue full for instance {instance_id}, dropping checkpoint")

        self.notify(instance_id)
        await self._broadcast_to_global(instance_id, "checkpoint", data=event)

    # -------------------------------------------------------------------------
    # Individual Message Event Method
    # -------------------------------------------------------------------------

    async def broadcast_message_event(
        self,
        instance_id: str,
        message: dict,
        event_type: str = "message",
        checkpoint_id: str | None = None,
    ) -> None:
        """Broadcast a single message event.

        Args:
            instance_id: The instance this message belongs to.
            message: Pre-serialized message dict (MUST include tool_outputs baked in).
            event_type: Type of message event for frontend routing.
            checkpoint_id: Optional checkpoint ID for ordering.
        """
        event: dict[str, Any] = {
            "instance_id": instance_id,
            "event_type": event_type,
            "event_id": message.get("message_id", ""),
            "message": message,  # Single message dict
            "checkpoint_id": checkpoint_id,
        }

        queue = self.get_streaming_queue(instance_id)
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.error(f"Queue full for {instance_id}, dropping message")

        self.notify(instance_id)

        # CRITICAL: Also broadcast to global subscribers (ResponseDispatcher, etc.)
        await self._broadcast_to_global(instance_id, event_type, data=event)

    # -------------------------------------------------------------------------
    # Error Event Method (persists to DB + notifies)
    # -------------------------------------------------------------------------

    async def create_event(
        self,
        instance_id: str,
        kind: EventKind | str,
        data: Optional[dict[str, Any]] = None,
        message_id: Optional[str] = None,
    ) -> None:
        """Create an error event: persist to DB and notify listeners.

        Args:
            instance_id: The instance this event belongs to
            kind: EventKind enum or string
            data: Optional event data to serialize as JSON
            message_id: Optional associated message ID
        """
        # Convert string to EventKind if needed
        if isinstance(kind, str):
            kind = EventKind(kind)

        # Persist to database (EventRepository handles JSON serialization)
        await asyncio.to_thread(
            self._event_repo.create_event,
            instance_id=instance_id,
            kind=kind.value,
            data=data,  # Pass dict, EventRepository will serialize
            message_id=message_id,
        )

        # Notify SSE listeners
        self.notify(instance_id)

        # Broadcast to global subscribers
        await self._broadcast_to_global(
            instance_id=instance_id,
            event_type=kind.value,
            data=data,
        )

    async def create_error_event(
        self,
        instance_id: str,
        error: Optional[dict[str, Any]] = None,
    ) -> None:
        """Create a generic error lifecycle event."""
        await self.create_event(
            instance_id=instance_id,
            kind=EventKind.ERROR,
            data=error,
        )

    # -------------------------------------------------------------------------
    # Notification Mechanism (asyncio.Event per instance)
    # -------------------------------------------------------------------------

    def get_notification(self, instance_id: str) -> asyncio.Event:
        """Get or create an asyncio.Event for the given instance.

        This Event is used to wake SSE listeners when new events occur.

        Args:
            instance_id: The instance ID

        Returns:
            asyncio.Event for this instance
        """
        if instance_id not in self._notifications:
            self._notifications[instance_id] = asyncio.Event()
        return self._notifications[instance_id]

    def notify(self, instance_id: str) -> None:
        """Signal that new events are available for the given instance.

        This wakes up any SSE listeners waiting on get_notification().

        Args:
            instance_id: The instance ID to notify
        """
        event = self.get_notification(instance_id)
        if not event.is_set():
            event.set()

    # -------------------------------------------------------------------------
    # Streaming Queue Management
    # -------------------------------------------------------------------------

    def get_streaming_queue(self, instance_id: str) -> asyncio.Queue:
        """Get or create the streaming queue for the given instance.

        Args:
            instance_id: The instance ID

        Returns:
            asyncio.Queue for checkpoint events
        """
        if instance_id not in self._streaming_channels:
            self._streaming_channels[instance_id] = asyncio.Queue(
                maxsize=self._streaming_queue_size
            )
        return self._streaming_channels[instance_id]

    async def get_streaming_events(
        self,
        instance_id: str,
        max_events: int = 50,
    ) -> list[dict[str, Any]]:
        """Drain available events for the given instance.

        Args:
            instance_id: The instance ID
            max_events: Maximum number of events to return

        Returns:
            List of checkpoint events
        """
        queue = self.get_streaming_queue(instance_id)
        events = []

        # Drain up to max_events
        for _ in range(max_events):
            try:
                event = queue.get_nowait()
                events.append(event)
            except asyncio.QueueEmpty:
                break

        return events

    # -------------------------------------------------------------------------
    # Global Subscriber Support (for ResponseDispatcher)
    # -------------------------------------------------------------------------

    def subscribe_all(
        self,
        subscriber_id: str,
        maxsize: Optional[int] = None,
    ) -> asyncio.Queue:
        """Subscribe to ALL events.

        This is used by ResponseDispatcher to receive completed events.

        Args:
            subscriber_id: Unique identifier for this subscriber
            maxsize: Optional queue max size (defaults to global_queue_size)

        Returns:
            asyncio.Queue that receives all events
        """
        if maxsize is None:
            maxsize = self._global_queue_size

        queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._global_subscribers[subscriber_id] = queue
        return queue

    def unsubscribe_all(self, subscriber_id: str) -> None:
        """Unsubscribe a global subscriber.

        Args:
            subscriber_id: The subscriber ID to remove
        """
        self._global_subscribers.pop(subscriber_id, None)

    async def _broadcast_to_global(
        self,
        instance_id: str,
        event_type: str,
        event_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Broadcast an event to all global subscribers.

        Args:
            instance_id: The instance ID
            event_type: The event type
            event_id: Optional event ID
            data: Optional data payload
        """
        if not self._global_subscribers:
            return

        event: dict[str, Any] = {
            "instance_id": instance_id,
            "event_type": event_type,
        }

        if event_id:
            event["event_id"] = event_id
        if data:
            event["data"] = data

        for subscriber_id, queue in list(self._global_subscribers.items()):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning(
                    f"Global subscriber {subscriber_id} queue full, dropping event"
                )

    # -------------------------------------------------------------------------
    # Cleanup Methods
    # -------------------------------------------------------------------------

    def cleanup_instance(self, instance_id: str) -> None:
        """Remove in-memory state for an instance.

        Args:
            instance_id: The instance ID to clean up
        """
        # Remove notification event
        notification = self._notifications.pop(instance_id, None)
        if notification:
            notification.set()  # Ensure any waiters are woken

        # Clear streaming queue
        self._streaming_channels.pop(instance_id, None)

        logger.debug(f"Cleaned up in-memory state for instance {instance_id}")

    async def shutdown(self) -> None:
        """Graceful shutdown of EventBus.

        Clears all in-memory state and wakes any waiting listeners.
        """
        logger.info("Shutting down EventBus")

        # Wake all notification waiters
        for instance_id, event in list(self._notifications.items()):
            event.set()

        # Clear all channels
        self._streaming_channels.clear()
        self._notifications.clear()
        self._global_subscribers.clear()

        logger.info("EventBus shutdown complete")

    # -------------------------------------------------------------------------
    # Sync Broadcast (for thread-safe usage)
    # -------------------------------------------------------------------------

    def broadcast_sync(self, event: dict) -> None:
        """Route completed events to dispatcher (for external sources like Telegram/Discord).

        Args:
            event: Event dict with event_type and data
        """
        # Legacy stub - ResponseDispatcher will be connected in Phase 4
        pass
