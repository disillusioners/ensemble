"""Response dispatcher for routing agent responses back to external sources."""

import asyncio
import logging
import re
from collections import OrderedDict
from typing import Optional, TYPE_CHECKING

from ..events import Event, EventBroadcaster
from .base import OutgoingMessage

if TYPE_CHECKING:
    from .registry import SourceRegistry

logger = logging.getLogger(__name__)


class ResponseDispatcher:
    """Dispatches completed agent responses to appropriate message sources.
    
    Listens for "completed" events from the event broadcaster and routes
    responses to external sources using per-user ordering locks.
    """
    
    MAX_SEND_LOCKS = 10000  # Class constant for LRU eviction
    
    def __init__(
        self,
        broadcaster: EventBroadcaster,
        registry: "SourceRegistry",
        subscriber_id: str = "response_dispatcher"
    ) -> None:
        """Initialize the response dispatcher.
        
        Args:
            broadcaster: EventBroadcaster to subscribe to.
            registry: SourceRegistry to get adapters from.
            subscriber_id: Unique identifier for this subscriber.
        """
        self._broadcaster = broadcaster
        self._registry: "SourceRegistry" = registry
        self._subscriber_id = subscriber_id
        
        self._running: bool = False
        self._task: Optional[asyncio.Task] = None
        self._event_queue: Optional[asyncio.Queue] = None
        
        self._send_locks: OrderedDict[str, asyncio.Lock] = OrderedDict()
        self._locks_guard = asyncio.Lock()
        
        logger.info(f"ResponseDispatcher initialized with subscriber_id={subscriber_id}")
    
    async def start(self) -> None:
        """Start the dispatcher asynchronously."""
        if self._running:
            logger.warning("ResponseDispatcher already running")
            return
        
        logger.info("Starting ResponseDispatcher")
        
        # Subscribe to all events from broadcaster (now async)
        self._event_queue = await self._broadcaster.subscribe_all(self._subscriber_id)
        
        self._running = True
        self._task = asyncio.create_task(self._event_loop())
        logger.info("ResponseDispatcher event loop started")
    
    async def stop(self, timeout: float = 30.0) -> None:
        """Stop the dispatcher gracefully.
        
        Args:
            timeout: Maximum seconds to wait for graceful shutdown.
        """
        if not self._running:
            logger.warning("ResponseDispatcher not running")
            return
        
        logger.info(f"Stopping ResponseDispatcher (timeout={timeout}s)")
        self._running = False
        
        # Unsubscribe from broadcaster
        self._broadcaster.unsubscribe_all(self._subscriber_id)
        
        # Wait for task to complete with timeout
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=timeout)
                logger.info("ResponseDispatcher stopped gracefully")
            except asyncio.CancelledError:
                logger.warning("ResponseDispatcher stop cancelled")
            except asyncio.TimeoutError:
                logger.warning(f"ResponseDispatcher stop timed out after {timeout}s")
                if self._task is not None and not self._task.done():
                    self._task.cancel()
                    try:
                        await self._task
                    except asyncio.CancelledError:
                        pass
        else:
            logger.warning("ResponseDispatcher had no task to stop")
        
        # Cleanup
        self._event_queue = None
        self._task = None
        
        # Clear send locks
        async with self._locks_guard:
            self._send_locks.clear()
    
    async def _get_send_lock(self, external_user_id: str) -> asyncio.Lock:
        """Get or create a send lock for a specific user.
        
        Uses double-check locking pattern for thread-safe lock creation.
        Implements LRU eviction to prevent memory leaks.
        
        Args:
            external_user_id: The external user ID to get lock for.
            
        Returns:
            asyncio.Lock for this user's send operations.
        """
        async with self._locks_guard:
            if external_user_id in self._send_locks:
                # Move to end (most recently used)
                self._send_locks.move_to_end(external_user_id)
                return self._send_locks[external_user_id]
            
            # Evict oldest if at capacity
            if len(self._send_locks) >= self.MAX_SEND_LOCKS:
                oldest_id, _ = self._send_locks.popitem(last=False)
                logger.debug(f"Evicted send lock for inactive user: {oldest_id}")
            
            lock = asyncio.Lock()
            self._send_locks[external_user_id] = lock
            return lock
    
    async def _event_loop(self) -> None:
        """Main event loop: process events from the queue."""
        logger.info("ResponseDispatcher event loop started")
        
        # Queue should be initialized by start() before this runs
        assert self._event_queue is not None, "Event queue not initialized - call start() first"
        
        while self._running:
            try:
                # Use wait_for to check _running flag periodically
                event = await asyncio.wait_for(
                    self._event_queue.get(),
                    timeout=1.0
                )
                
                await self._handle_event(event)
                
            except asyncio.TimeoutError:
                # No event available within timeout, continue loop
                continue
            except asyncio.CancelledError:
                logger.info("ResponseDispatcher event loop cancelled")
                break
            except Exception as e:
                logger.error(f"Error in ResponseDispatcher event loop: {e}", exc_info=True)
                # Continue processing despite errors
        
        logger.info("ResponseDispatcher event loop exited")
    
    async def _handle_event(self, event: Event) -> None:
        """Process a completed event by sending response to source.
        
        Args:
            event: The event to process.
        """
        try:
            # Step 1: Check event type is "completed"
            if event.type != "completed":
                logger.debug(f"Ignoring non-completed event: {event.type}")
                return
            
            # Step 2: Get source from event data
            source = event.data.get("source")
            if not source:
                logger.warning(f"Completed event missing source: {event}")
                return
            
            # Step 3: Parse source as "source_id:external_user_id"
            # Sources without ":" are internal (e.g., "api") and don't need routing
            if ":" not in source:
                logger.debug(f"Skipping internal source (no routing needed): {source}")
                return
            
            source_id, external_user_id = source.split(":", 1)
            
            # Validate source_id format
            if not re.match(r'^[a-zA-Z0-9_-]{1,64}$', source_id):
                logger.warning(f"Invalid source_id format: {source_id}")
                return
            
            # Validate external_user_id length
            if len(external_user_id) > 256:
                logger.warning(f"external_user_id too long: {len(external_user_id)}")
                return
            
            logger.debug(f"Processing completed event for source={source_id}, user={external_user_id}")
            
            # Step 4: Get adapter from registry
            adapter = self._registry.get(source_id)
            if adapter is None:
                logger.error(f"No adapter found for source_id={source_id}")
                return
            
            # Step 5: Create OutgoingMessage
            outgoing = OutgoingMessage(
                external_user_id=external_user_id,
                content=event.data.get("content", ""),
                source_id=source_id,
                metadata=event.data.get("metadata", {}),
                message_type=event.data.get("message_type", "text"),
                reply_to_id=event.data.get("reply_to_id")
            )
            
            # Step 6: Send with per-user lock for ordering
            send_lock = await self._get_send_lock(external_user_id)
            
            async with send_lock:
                success = await adapter.send(outgoing)
                if success:
                    logger.debug(f"Sent response to user {external_user_id} via {source_id}")
                else:
                    logger.warning(f"Failed to send response to user {external_user_id} via {source_id}")
            
        except Exception as e:
            logger.error(f"Error handling completed event: {e}", exc_info=True)
