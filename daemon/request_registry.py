"""Registry for tracking active processing requests."""

import threading
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .cancellation import CancellationTokenSource, CancellationReason

logger = logging.getLogger(__name__)


@dataclass
class ActiveRequest:
    """Tracks an actively processing message."""
    message_id: str
    instance_id: str
    cancellation_source: CancellationTokenSource
    started_at: datetime
    task: Optional[asyncio.Task] = None
    thread_id: Optional[int] = None


class ActiveRequestRegistry:
    """Thread-safe registry for tracking and cancelling active requests.
    
    This is the bridge between the Watchdog (sync thread) and the
    InstanceManager (async event loop).
    """
    
    def __init__(self):
        self._requests: dict[str, ActiveRequest] = {}  # message_id -> ActiveRequest
        self._by_instance: dict[str, set[str]] = {}  # instance_id -> set of message_ids
        self._lock = threading.RLock()
    
    def register(
        self,
        message_id: str,
        instance_id: str,
        task: Optional[asyncio.Task] = None
    ) -> CancellationTokenSource:
        """Register a new active request.
        
        Returns:
            CancellationTokenSource that can be used to cancel this request.
        """
        source = CancellationTokenSource()
        request = ActiveRequest(
            message_id=message_id,
            instance_id=instance_id,
            cancellation_source=source,
            started_at=datetime.now(timezone.utc),
            task=task,
            thread_id=threading.current_thread().ident
        )
        
        with self._lock:
            self._requests[message_id] = request
            if instance_id not in self._by_instance:
                self._by_instance[instance_id] = set()
            self._by_instance[instance_id].add(message_id)
        
        logger.debug(f"Registered active request {message_id[:8]}... for instance {instance_id[:8]}...")
        return source
    
    def unregister(self, message_id: str) -> None:
        """Unregister a completed request."""
        with self._lock:
            request = self._requests.pop(message_id, None)
            if request:
                instance_id = request.instance_id
                if instance_id in self._by_instance:
                    self._by_instance[instance_id].discard(message_id)
                    if not self._by_instance[instance_id]:
                        del self._by_instance[instance_id]
                logger.debug(f"Unregistered request {message_id[:8]}...")
    
    def cancel(self, message_id: str, reason: CancellationReason) -> bool:
        """Request cancellation of a specific message.
        
        Returns:
            True if cancellation was signalled, False if not found.
        """
        with self._lock:
            request = self._requests.get(message_id)
            if request is None:
                return False
            
            # Cancel the asyncio task if available
            if request.task and not request.task.done():
                # Schedule cancellation on the task's event loop
                try:
                    loop = request.task.get_loop()
                    loop.call_soon_threadsafe(request.task.cancel)
                except RuntimeError:
                    pass  # No event loop available
            
            # Signal via cancellation token
            request.cancellation_source.cancel(reason)
            logger.info(
                f"Signalled cancellation for {message_id[:8]}... "
                f"(reason: {reason.value})"
            )
            return True
    
    def get_active_for_instance(self, instance_id: str) -> list[str]:
        """Get list of active message IDs for an instance."""
        with self._lock:
            return list(self._by_instance.get(instance_id, set()))
    
    def get_request(self, message_id: str) -> Optional[ActiveRequest]:
        """Get request info by message ID."""
        with self._lock:
            return self._requests.get(message_id)
    
    def cancel_by_instance(self, instance_id: str) -> None:
        """Cancel all active requests for an instance.
        
        Args:
            instance_id: The instance whose requests should be cancelled.
        """
        with self._lock:
            message_ids = self._by_instance.get(instance_id, set()).copy()
        
        for message_id in message_ids:
            self.cancel(message_id, CancellationReason.INSTANCE_TERMINATED)
        
        if message_ids:
            logger.info(f"Cancelled {len(message_ids)} request(s) for instance {instance_id[:8]}...")
