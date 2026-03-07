"""Event broadcasting system for real-time SSE updates."""

import asyncio
import json
import logging
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class Event:
    """Structured event for SSE broadcast."""
    type: str  # "message_queued" | "status_changed" | "content_chunk" | "tool_call" | "completed" | "error"
    session_id: str
    message_id: Optional[str] = None
    data: dict[str, Any] = field(default_factory=dict)
    event_id: int = 0  # Monotonic counter for reconnection


class EventBroadcaster:
    """Manages per-session asyncio.Queues for real-time event delivery.
    
    This provides:
    - Per-session event queues for SSE streaming
    - Event history for reconnection support
    - Non-blocking broadcast with queue overflow handling
    - Thread-safe operations for cross-thread broadcasting
    """
    
    def __init__(self, max_queue_size: int = 100, history_size: int = 50):
        """Initialize the broadcaster.
        
        Args:
            max_queue_size: Maximum events per session queue before dropping.
            history_size: Number of recent events to keep for reconnection.
        """
        self._queues: dict[str, asyncio.Queue] = {}
        self._event_counters: dict[str, int] = defaultdict(int)
        self._event_history: dict[str, list[Event]] = defaultdict(list)
        self._max_queue_size = max_queue_size
        self._history_size = history_size
        self._lock = threading.Lock()  # Thread-safe for sync operations
        self._async_lock: Optional[asyncio.Lock] = None  # Created lazily in async context
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None  # Set from main event loop
        
        # Global subscribers for ResponseDispatcher and other listeners
        self._global_subscribers: list[asyncio.Queue] = []
        self._subscriber_refs: dict[str, asyncio.Queue] = {}  # Track by ID for cleanup
    
    def set_main_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Set the main event loop for thread-safe coroutine scheduling.
        
        Should be called once from the main event loop during initialization.
        
        Args:
            loop: The main asyncio event loop.
        """
        self._main_loop = loop
    
    def _get_async_lock(self) -> asyncio.Lock:
        """Get or create the async lock (must be called from async context)."""
        if self._async_lock is None:
            self._async_lock = asyncio.Lock()
        return self._async_lock
    
    async def get_queue(self, session_id: str) -> asyncio.Queue:
        """Get (or create) the event queue for a session.
        
        Thread-safe queue creation to prevent duplicate queues.
        
        Note: The fast-path dict read without lock is safe because:
        - Python dict reads are thread-safe (no concurrent writes during read)
        - The double-check pattern after acquiring lock prevents duplicates
        
        Args:
            session_id: The session ID.
            
        Returns:
            asyncio.Queue for the session.
        """
        # Fast path - queue already exists (safe: dict read is atomic in Python)
        if session_id in self._queues:
            return self._queues[session_id]
        
        # Slow path - need to create queue with lock
        async with self._get_async_lock():
            # Double-check after acquiring lock
            if session_id not in self._queues:
                self._queues[session_id] = asyncio.Queue(maxsize=self._max_queue_size)
            return self._queues[session_id]
    
    async def subscribe_all(self, subscriber_id: str, maxsize: int = 1000) -> asyncio.Queue:
        """Subscribe to ALL events across all sessions.
        
        Used by ResponseDispatcher to route agent responses back to external sources.
        
        Args:
            subscriber_id: Unique identifier for cleanup (e.g., "response_dispatcher").
            maxsize: Maximum queue size for subscriber.
            
        Returns:
            asyncio.Queue that will receive all broadcast events.
        """
        q = asyncio.Queue(maxsize=maxsize)
        with self._lock:
            # Remove old subscription if exists (prevents duplicates)
            if subscriber_id in self._subscriber_refs:
                old_q = self._subscriber_refs[subscriber_id]
                if old_q in self._global_subscribers:
                    self._global_subscribers.remove(old_q)
            
            self._global_subscribers.append(q)
            self._subscriber_refs[subscriber_id] = q
        logger.debug(f"Global subscriber '{subscriber_id}' registered")
        return q
    
    def unsubscribe_all(self, subscriber_id: str) -> None:
        """Unsubscribe from all events.
        
        Call during shutdown to prevent memory leaks.
        
        Args:
            subscriber_id: The subscriber ID used in subscribe_all().
        """
        with self._lock:
            q = self._subscriber_refs.pop(subscriber_id, None)
            if q and q in self._global_subscribers:
                self._global_subscribers.remove(q)
        logger.debug(f"Global subscriber '{subscriber_id}' unregistered")
    
    async def broadcast(self, event: Event) -> None:
        session_id = event.session_id
        
        # Thread-safe counter increment and history update
        with self._lock:
            self._event_counters[session_id] += 1
            event.event_id = self._event_counters[session_id]
            
            # Store in history for reconnection
            history = self._event_history[session_id]
            history.append(event)
            if len(history) > self._history_size:
                history.pop(0)
        
        # Only broadcast to session queue if there's an active consumer (queue exists and has items waiting)
        queue = self._queues.get(session_id)
        if queue and queue.qsize() > 0:
            try:
                queue.put_nowait(event)
                logger.debug(f"Broadcast event {event.type} to session {session_id}")
            except asyncio.QueueFull:
                logger.warning(f"Event queue full for session {session_id}, dropping event")
    
    def broadcast_sync(self, event: Event) -> None:
        """Synchronous version of broadcast for use in threads.
        
        Uses run_coroutine_threadsafe for proper thread-safe coroutine scheduling.
        Requires set_main_loop() to have been called from the main event loop.
        
        Args:
            event: The event to broadcast.
        """
        if self._main_loop is None:
            logger.error(
                "broadcast_sync called but main loop not set. "
                "Call set_main_loop() during initialization. Event dropped."
            )
            return
        
        if self._main_loop.is_closed():
            logger.error("Main event loop is closed. Event dropped.")
            return
        
        # Schedule the coroutine on the main event loop from this thread
        future = asyncio.run_coroutine_threadsafe(self.broadcast(event), self._main_loop)
        # Don't wait for result - fire and forget with error logging
        def _log_exception(f):
            try:
                f.result()
            except Exception as e:
                logger.error(f"Error in broadcast_sync: {e}")
        future.add_done_callback(_log_exception)
    
    def get_events_since(self, session_id: str, last_event_id: int) -> list[Event]:
        """Get missed events for reconnection.
        
        Args:
            session_id: The session ID.
            last_event_id: The last event ID the client received.
            
        Returns:
            List of events after the given ID.
        """
        history = self._event_history.get(session_id, [])
        return [e for e in history if e.event_id > last_event_id]
    
    def cleanup_session(self, session_id: str) -> None:
        """Remove all state for a session.
        
        Should be called when a session is terminated to prevent memory leaks.
        
        Args:
            session_id: The session ID to clean up.
        """
        with self._lock:
            self._queues.pop(session_id, None)
            self._event_history.pop(session_id, None)
            self._event_counters.pop(session_id, None)
        logger.debug(f"Cleaned up event state for session {session_id}")
    
    def clear_queue(self, session_id: str) -> None:
        """Clear the event queue for a session.
        
        Should be called when a new SSE connection is established to prevent
        old events from accumulating when there was no active consumer.
        
        Args:
            session_id: The session ID to clear the queue for.
        """
        queue = self._queues.get(session_id)
        if queue:
            cleared = 0
            # Drain the queue
            while not queue.empty():
                try:
                    queue.get_nowait()
                    cleared += 1
                except asyncio.QueueEmpty:
                    break
            if cleared > 0:
                logger.debug(f"Cleared {cleared} stale events from queue for session {session_id}")
    
    def get_stats(self, session_id: str) -> dict:
        """Get statistics for a session's event queue.
        
        Args:
            session_id: The session ID.
            
        Returns:
            Dict with queue stats.
        """
        with self._lock:
            queue = self._queues.get(session_id)
            history = self._event_history.get(session_id, [])
            
            return {
                "queue_size": queue.qsize() if queue else 0,
                "history_size": len(history),
                "last_event_id": self._event_counters.get(session_id, 0),
                "has_consumer": queue is not None and queue.qsize() > 0,
            }


def event_to_sse(event: Event) -> dict:
    """Convert an Event to SSE response format.
    
    Args:
        event: The event to convert.
        
    Returns:
        Dict with 'id', 'event', and 'data' keys for SSE.
    """
    return {
        "id": str(event.event_id),
        "event": event.type,
        "data": json.dumps({
            "message_id": event.message_id,
            "session_id": event.session_id,
            **event.data
        })
    }
