"""Response dispatcher for routing agent responses back to external sources.

The dispatcher receives completed message events directly from the manager
and routes responses to external sources (Telegram, Discord, etc.) using
per-user ordering locks for guaranteed delivery ordering.
"""

import asyncio
import logging
import re
from collections import OrderedDict
from typing import TYPE_CHECKING

from .base import OutgoingMessage

if TYPE_CHECKING:
    from .registry import SourceRegistry

logger = logging.getLogger(__name__)


class ResponseDispatcher:
    """Dispatches completed agent responses to appropriate message sources.
    
    Receives completed message events directly from the manager via
    `dispatch_completed()` and routes responses to external sources.
    Uses per-user ordering locks to guarantee delivery ordering.
    """
    
    MAX_SEND_LOCKS = 10000  # Class constant for LRU eviction
    
    def __init__(
        self,
        registry: "SourceRegistry" | None = None,
        subscriber_id: str = "response_dispatcher"
    ) -> None:
        """Initialize the response dispatcher.
        
        Args:
            registry: SourceRegistry to get adapters from.
            subscriber_id: Unique identifier for this dispatcher instance.
        """
        self._registry: "SourceRegistry" | None = registry
        self._subscriber_id = subscriber_id
        
        self._running: bool = False
        
        self._send_locks: OrderedDict[str, asyncio.Lock] = OrderedDict()
        self._locks_guard = asyncio.Lock()
        
        # Track sources that received progressive messages to avoid duplicate delivery
        self._progressive_sent_sources: set[str] = set()
        
        logger.info(f"ResponseDispatcher initialized with subscriber_id={subscriber_id}")
    
    async def start(self) -> None:
        """Start the dispatcher asynchronously."""
        if self._running:
            logger.warning("ResponseDispatcher already running")
            return
        
        logger.info("Starting ResponseDispatcher")
        self._running = True
    
    async def stop(self, timeout: float = 30.0) -> None:
        """Stop the dispatcher gracefully.
        
        Args:
            timeout: Maximum seconds to wait for graceful shutdown.
        """
        if not self._running:
            return
        
        logger.info("Stopping ResponseDispatcher")
        self._running = False
        
        # Clear send locks
        async with self._locks_guard:
            self._send_locks.clear()
            self._progressive_sent_sources.clear()
    
    async def dispatch_completed(
        self,
        instance_id: str,
        message_id: str,
        source: str,
        content: str,
        message_type: str = "text",
        metadata: dict | None = None,
        reply_to_id: str | None = None,
    ) -> None:
        """Dispatch a completed message to the appropriate external source.
        
        This is the main entry point called by the manager when a message
        completes processing. It routes the response to the correct adapter.
        
        Args:
            instance_id: The instance that processed the message.
            message_id: The message ID that completed.
            source: The source identifier (format: "source_id:external_user_id").
            content: The response content to send.
            message_type: Type of message (text, image, etc.).
            metadata: Optional metadata to include.
            reply_to_id: Optional message ID to reply to.
        """
        logger.info(f"[DISPATCH] dispatch_completed called: source={source}, content_length={len(content) if content else 0}")
        
        if not self._running:
            logger.debug("ResponseDispatcher not running, skipping dispatch")
            return
        
        if self._registry is None:
            logger.debug("No source registry configured, skipping dispatch")
            return
        
        # Skip empty content to avoid duplicate/empty sends (progressive may have sent)
        if not content or not content.strip():
            logger.debug(f"Skipping empty content for source={source}")
            return
        
        # Skip if source already received progressive messages (last message was already sent)
        logger.info(f"[DISPATCH] progressive_sent_sources check: source={source}, in_set={source in self._progressive_sent_sources}")
        if source in self._progressive_sent_sources:
            logger.debug(f"Skipping dispatch_completed for source={source} (progressive delivery already sent)")
            self._progressive_sent_sources.discard(source)
            return
        
        # Parse source as "source_id:external_user_id"
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
        
        logger.debug(f"Dispatching completed message for source={source_id}, user={external_user_id}")
        
        # Skip adapter lookup for internal sources (not external adapters)
        # C1 fix: Only skip internal_report and internal_error_report, NOT internal_agent
        if source_id in ("internal_report", "internal_error_report"):
            logger.info("[DISPATCH] SKIPPED: no adapter or source is internal")
            logger.debug(f"Skipping internal report source (no adapter needed): {source_id}")
            return
        
        # Get adapter from registry
        adapter = self._registry.get(source_id)
        if adapter is None:
            logger.info("[DISPATCH] SKIPPED: no adapter or source is internal")
            if source_id.startswith("internal_"):
                logger.debug(f"No adapter needed for internal source: {source_id}")
            else:
                logger.debug(f"No adapter found for source_id={source_id}")
            return

        logger.info(f"[DISPATCH] sending to adapter: source={source}, adapter_type={type(adapter).__name__}")
        
        # Create OutgoingMessage
        outgoing = OutgoingMessage(
            external_user_id=external_user_id,
            content=content,
            source_id=source_id,
            metadata=metadata or {},
            message_type=message_type,
            reply_to_id=reply_to_id
        )
        
        # Send with per-user lock for ordering
        send_lock = await self._get_send_lock(external_user_id)
        
        async with send_lock:
            success = await adapter.send(outgoing)
            if success:
                logger.debug(f"Sent response to user {external_user_id} via {source_id}")
            else:
                logger.warning(f"Failed to send response to user {external_user_id} via {source_id}")
    
    async def dispatch_message(self, source: str, content: str) -> None:
        """Send an intermediate message during execution (progressive delivery).
        
        Routes messages to external sources during agent execution, as opposed
        to dispatch_completed which sends the final response.
        
        Args:
            source: The source identifier (format: "source_id:external_user_id").
            content: The message content to send.
        """
        if not self._running:
            logger.debug("ResponseDispatcher not running, skipping dispatch")
            return
        
        if self._registry is None:
            logger.debug("No source registry configured, skipping dispatch")
            return
        
        # Parse source as "source_id:external_user_id"
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
        
        logger.debug(f"Dispatching progressive message for source={source_id}, user={external_user_id}")
        
        # Skip adapter lookup for internal sources (not external adapters)
        # C1 fix: Only skip internal_report and internal_error_report, NOT internal_agent
        if source_id in ("internal_report", "internal_error_report"):
            logger.debug(f"Skipping internal report source (no adapter needed): {source_id}")
            return
        
        # Get adapter from registry
        adapter = self._registry.get(source_id)
        if adapter is None:
            if source_id.startswith("internal_"):
                logger.debug(f"No adapter needed for internal source: {source_id}")
            else:
                logger.debug(f"No adapter found for source_id={source_id}")
            return
        
        # Create OutgoingMessage
        outgoing = OutgoingMessage(
            external_user_id=external_user_id,
            content=content,
            source_id=source_id,
            metadata={},
            message_type="text",
            reply_to_id=None
        )
        
        # Send with per-user lock for ordering
        send_lock = await self._get_send_lock(external_user_id)
        
        async with send_lock:
            try:
                success = await adapter.send(outgoing)
            except Exception as e:
                logger.warning(f"Progressive dispatch failed for source {source}: {e}")
                return
            if success:
                logger.debug(f"Sent progressive message to user {external_user_id} via {source_id}")
                # Track this source so dispatch_completed won't send again
                self._progressive_sent_sources.add(source)
            else:
                logger.warning(f"Failed to send progressive message to user {external_user_id} via {source_id}")
    
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
    
    async def _handle_event(self, event: dict) -> None:
        """Process a completed event by sending response to source.
        
        NOTE: Disabled pending redesign. This was previously called by the event
        loop but is now a no-op.
        
        Args:
            event: The event dict (not used)
        """
        # No-op - dispatcher is disabled pending redesign
        pass
