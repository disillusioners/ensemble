"""EventBus: Hybrid event delivery service for SSE.

Lifecycle events (persisted to DB):
  message_received, processing_started, processing_completed, processing_failed,
  child_completed, child_failed, instance_completed, error
  → INSERT INTO event table → notify SSE listeners via asyncio.Event

Streaming events (in-memory only):
  content_chunk, thinking, tool_call, tool_complete
  → asyncio.Queue → SSE picks up immediately
"""
import asyncio
import logging
import threading
from typing import Any, Optional

from daemon.repositories.event.models import EventKind

logger = logging.getLogger(__name__)

# Streaming event types are NOT persisted to DB
STREAMING_EVENT_TYPES = {"content_chunk", "thinking", "tool_call", "tool_complete"}

# Backward compatibility: map old event types to new kinds
LEGACY_EVENT_MAP = {
    "message_queued": EventKind.MESSAGE_RECEIVED,
    "completed": EventKind.PROCESSING_COMPLETED,
}


class EventBus:
    """Hybrid event delivery service combining DB persistence with in-memory streaming.

    Lifecycle events are persisted to the database via EventRepository, with an
    asyncio.Event notification to wake SSE listeners.

    Streaming events (content_chunk, thinking, tool_call, tool_complete) are
    delivered via asyncio.Queue without DB persistence.
    """

    def __init__(
        self,
        event_repo: Any,
        streaming_queue_size: int = 100,
        global_queue_size: int = 1000,
    ) -> None:
        """Initialize EventBus.

        Args:
            event_repo: EventRepository instance for DB operations
            streaming_queue_size: Max queue size per streaming channel
            global_queue_size: Max queue size for global subscribers
        """
        self._event_repo = event_repo

        # Per-instance streaming channels (queues)
        self._streaming_channels: dict[str, asyncio.Queue] = {}

        # Per-instance notification events (lifecycle events)
        self._notifications: dict[str, asyncio.Event] = {}

        # Global subscribers (receive ALL events)
        self._global_subscribers: dict[str, asyncio.Queue] = {}

        # Per-instance streaming counters for event IDs (delta_type -> counter)
        self._streaming_counters: dict[str, dict[str, int]] = {}

        # Queue size limits
        self._streaming_queue_size = streaming_queue_size
        self._global_queue_size = global_queue_size

        # Thread safety
        self._sync_lock = threading.Lock()

        # Event loop reference for sync operations
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # -------------------------------------------------------------------------
    # Lifecycle Event Methods (persist to DB + notify)
    # -------------------------------------------------------------------------

    async def create_message_received_event(
        self,
        instance_id: str,
        message_id: str,
        content: Optional[dict[str, Any]] = None,
    ) -> None:
        """Create a message_received lifecycle event."""
        await self.create_event(
            instance_id=instance_id,
            kind=EventKind.MESSAGE_RECEIVED,
            data=content,
            message_id=message_id,
        )

    async def create_processing_started_event(
        self,
        instance_id: str,
        message_id: str,
    ) -> None:
        """Create a processing_started lifecycle event."""
        await self.create_event(
            instance_id=instance_id,
            kind=EventKind.PROCESSING_STARTED,
            message_id=message_id,
        )

    async def create_processing_completed_event(
        self,
        instance_id: str,
        message_id: str,
        result: Optional[dict[str, Any]] = None,
    ) -> None:
        """Create a processing_completed lifecycle event."""
        await self.create_event(
            instance_id=instance_id,
            kind=EventKind.PROCESSING_COMPLETED,
            data=result,
            message_id=message_id,
        )

    async def create_processing_failed_event(
        self,
        instance_id: str,
        message_id: str,
        error: Optional[dict[str, Any]] = None,
    ) -> None:
        """Create a processing_failed lifecycle event."""
        await self.create_event(
            instance_id=instance_id,
            kind=EventKind.PROCESSING_FAILED,
            data=error,
            message_id=message_id,
        )

    async def create_child_completed_event(
        self,
        instance_id: str,
        child_id: str,
    ) -> None:
        """Create a child_completed lifecycle event."""
        await self.create_event(
            instance_id=instance_id,
            kind=EventKind.CHILD_COMPLETED,
            data={"child_id": child_id},
        )

    async def create_child_failed_event(
        self,
        instance_id: str,
        child_id: str,
        error: Optional[dict[str, Any]] = None,
    ) -> None:
        """Create a child_failed lifecycle event."""
        await self.create_event(
            instance_id=instance_id,
            kind=EventKind.CHILD_FAILED,
            data={"child_id": child_id, "error": error},
        )

    async def create_instance_completed_event(
        self,
        instance_id: str,
    ) -> None:
        """Create an instance_completed lifecycle event."""
        await self.create_event(
            instance_id=instance_id,
            kind=EventKind.INSTANCE_COMPLETED,
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

    async def create_event(
        self,
        instance_id: str,
        kind: EventKind | str,
        data: Optional[dict[str, Any]] = None,
        message_id: Optional[str] = None,
    ) -> None:
        """Create a lifecycle event: persist to DB and notify listeners.

        Args:
            instance_id: The instance this event belongs to
            kind: EventKind enum or legacy string
            data: Optional event data to serialize as JSON
            message_id: Optional associated message ID
        """
        # Handle legacy event types
        if isinstance(kind, str):
            kind = LEGACY_EVENT_MAP.get(kind) or EventKind(kind)

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

    # -------------------------------------------------------------------------
    # Streaming Event Methods (in-memory only)
    # -------------------------------------------------------------------------

    async def broadcast_streaming_event(
        self,
        instance_id: str,
        event_type: str,
        message_id: str,
        delta: dict[str, Any],
    ) -> None:
        """Broadcast a streaming event (in-memory only).

        Streaming events are NOT persisted to the database. They are delivered
        immediately via asyncio.Queue to SSE listeners.

        Args:
            instance_id: The instance this event belongs to
            event_type: One of content_chunk, thinking, tool_call, tool_complete
            message_id: The queue message ID this event belongs to
            delta: Event payload with type, content/tool_call, and index
        """
        if event_type not in STREAMING_EVENT_TYPES:
            logger.warning(
                f"Unknown streaming event type: {event_type}. "
                f"Expected one of: {STREAMING_EVENT_TYPES}"
            )

        # Extract delta type and generate prefixed ID
        delta_type = delta.get("type", event_type)
        event_id = self._next_streaming_id(instance_id, delta_type)
        
        # Put in per-instance streaming queue
        queue = self.get_streaming_queue(instance_id)
        event = {
            "instance_id": instance_id,
            "event_type": event_type,
            "event_id": event_id,
            "message_id": message_id,
            "delta": delta,
        }

        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning(
                f"Streaming queue full for instance {instance_id}, dropping event"
            )

        # Notify SSE listeners to drain the streaming queue
        self.notify(instance_id)

        # Broadcast to global subscribers
        await self._broadcast_to_global(
            instance_id=instance_id,
            event_type=event_type,
            event_id=event_id,
            message_id=message_id,
            delta=delta,
        )

    # -------------------------------------------------------------------------
    # Notification Mechanism (asyncio.Event per instance)
    # -------------------------------------------------------------------------

    def get_notification(self, instance_id: str) -> asyncio.Event:
        """Get or create an asyncio.Event for the given instance.

        This Event is used to wake SSE listeners when new lifecycle events occur.

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
            asyncio.Queue for streaming events
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
        """Drain available streaming events for the given instance.

        Args:
            instance_id: The instance ID
            max_events: Maximum number of events to return

        Returns:
            List of streaming events
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
        """Subscribe to ALL events (both lifecycle and streaming).

        This is used by ResponseDispatcher to receive all events.

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
        message_id: str | None = None,
        delta: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Broadcast an event to all global subscribers.

        Args:
            instance_id: The instance ID
            event_type: The event type
            event_id: Optional event ID (for streaming events with prefixed IDs)
            message_id: Optional message ID
            delta: Optional delta payload (for streaming events)
            data: Optional legacy data payload
        """
        if not self._global_subscribers:
            return

        event: dict[str, Any] = {
            "instance_id": instance_id,
            "event_type": event_type,
        }
        
        if event_id:
            event["event_id"] = event_id
        if message_id:
            event["message_id"] = message_id
        if delta:
            event["delta"] = delta
        elif data:
            event["data"] = data

        for subscriber_id, queue in list(self._global_subscribers.items()):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning(
                    f"Global subscriber {subscriber_id} queue full, dropping event"
                )

    # -------------------------------------------------------------------------
    # Streaming ID Generation
    # -------------------------------------------------------------------------

    def _next_streaming_id(self, instance_id: str, delta_type: str) -> str:
        """Generate monotonic streaming event ID for a delta type with 's' prefix.
        
        Uses 's' prefix to avoid collision with DB auto-increment IDs.
        SSE client can distinguish: 's5' = streaming, '42' = DB event.
        
        Args:
            instance_id: The instance ID
            delta_type: The delta type (chunk, thinking, tool_call, tool_complete)
        
        Returns:
            String ID like 's1', 's2' with 's' prefix
        """
        if instance_id not in self._streaming_counters:
            self._streaming_counters[instance_id] = {}
        
        counters = self._streaming_counters[instance_id]
        if delta_type not in counters:
            counters[delta_type] = 0
        
        counters[delta_type] += 1
        return f"s{counters[delta_type]}"

    # -------------------------------------------------------------------------
    # Cleanup Methods
    # -------------------------------------------------------------------------

    async def cleanup_old(self, hours: int = 24) -> int:
        """Clean up old events from the database.

        Args:
            hours: Delete events older than this many hours

        Returns:
            Number of events deleted
        """
        return await asyncio.to_thread(self._event_repo.cleanup_old, hours)

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
        
        # Clear streaming counters (prevent memory leak)
        self._streaming_counters.pop(instance_id, None)

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
        self._streaming_counters.clear()

        logger.info("EventBus shutdown complete")

    # -------------------------------------------------------------------------
    # Sync Broadcast (for thread-safe usage)
    # -------------------------------------------------------------------------

    def broadcast_sync(
        self,
        instance_id: str,
        event_type: str,
        message_id: str | None = None,
        delta: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Synchronous broadcast for use from non-async context.

        Uses run_coroutine_threadsafe to schedule the async operation.

        Args:
            instance_id: The instance ID
            event_type: The event type
            message_id: The message ID (for streaming events)
            delta: Delta payload (for streaming events)
            data: Legacy data payload
        """
        if self._loop is None:
            self._loop = asyncio.get_running_loop()

        if event_type in STREAMING_EVENT_TYPES:
            coro = self.broadcast_streaming_event(
                instance_id, event_type, message_id or "", delta or {}
            )
        else:
            # Map to EventKind for lifecycle events
            try:
                kind = LEGACY_EVENT_MAP.get(event_type, EventKind(event_type))
                coro = self.create_event(instance_id, kind, data)
            except ValueError:
                logger.error(f"Unknown event type: {event_type}")
                return

        with self._sync_lock:
            asyncio.run_coroutine_threadsafe(coro, self._loop)
