"""Cancellation service for managing request cancellation."""

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from ..cancellation import CancellationReason
from ..request_registry import ActiveRequestRegistry

logger = logging.getLogger(__name__)


class CancellationService:
    """Service for managing request cancellation and shutdown coordination.
    
    Handles cancellation of individual requests, instance-level cancellation,
    and graceful shutdown with inflight request tracking.
    """

    def __init__(
        self,
        manager: "InstanceManager",
    ):
        """Initialize the cancellation service.
        
        Args:
            manager: The InstanceManager facade.
        """
        self._manager = manager

    @property
    def _request_registry(self) -> ActiveRequestRegistry:
        """Access request registry through manager for test mockability."""
        return self._manager._request_registry

    @property
    def _shutting_down(self) -> bool:
        """Access shutting_down through manager for test mockability."""
        return self._manager._shutting_down

    def cancel(self, message_id: str, reason: CancellationReason) -> bool:
        """Request cancellation of a specific message.
        
        Args:
            message_id: The message ID to cancel.
            reason: The cancellation reason.
        
        Returns:
            True if cancellation was signalled, False if not found.
        """
        return self._request_registry.cancel(message_id, reason)

    def cancel_instance_requests(self, instance_id: str, reason: CancellationReason) -> int:
        """Cancel all active requests for an instance. Returns count of cancelled."""
        message_ids = self._request_registry.get_active_for_instance(instance_id)
        count = 0
        for msg_id in message_ids:
            if self.cancel(msg_id, reason):
                count += 1
        return count

    def get_active_requests(self, instance_id: str) -> list[str]:
        """Get list of active request message IDs for an instance.
        
        Args:
            instance_id: The instance ID to check.
        
        Returns:
            List of message IDs that are currently being processed.
        """
        return self._request_registry.get_active_for_instance(instance_id)

    @property
    def is_shutting_down(self) -> bool:
        """Check if shutdown is in progress."""
        return self._shutting_down

    async def _cancel_all_active_requests(self) -> None:
        """Cancel all active requests in the registry with SHUTDOWN reason."""
        # Use asyncio.to_thread to avoid blocking the event loop with the thread lock
        message_ids = await asyncio.to_thread(self._request_registry.get_all_message_ids)
        
        if message_ids:
            logger.info(f"Cancelling {len(message_ids)} active request(s)...")
            for message_id in message_ids:
                self._request_registry.cancel(message_id, CancellationReason.SHUTDOWN)

    async def _wait_for_inflight(self, grace_period: float) -> None:
        """Wait for in-flight processing to finish.
        
        Args:
            grace_period: Maximum seconds to wait.
        """
        start_time = time.monotonic()
        while time.monotonic() - start_time < grace_period:
            # Check if any requests are still active
            with self._request_registry._lock:
                active_requests = len(self._request_registry._requests)
            
            if active_requests == 0:
                logger.debug("All requests completed, proceeding with shutdown")
                break
            
            logger.debug(
                f"Waiting for shutdown: {active_requests} active requests"
            )
            await asyncio.sleep(0.5)
