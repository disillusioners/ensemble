"""Event publisher service for broadcasting instance lifecycle events."""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..services.event_bus import EventBus
    from ..repositories.event.models import EventKind


logger = logging.getLogger(__name__)


class EventPublisherService:
    """Service for publishing instance lifecycle events via EventBus.
    
    Lifecycle events signal important state transitions: completed, terminated, error.
    This service publishes to EventBus so JobFeedbackObserver (which subscribes via
    subscribe_all) receives the events for job completion feedback.
    """

    def __init__(
        self,
        manager: "InstanceManager",
    ):
        """Initialize the event publisher service.
        
        Args:
            manager: The InstanceManager facade.
        """
        self._manager = manager

    @property
    def _event_bus(self) -> "EventBus":
        """Access event bus through manager for test mockability."""
        return self._manager._event_bus

    async def _publish_instance_lifecycle_event(
        self,
        instance_id: str,
        status: str,
        error: str | None = None,
        parent_id: str | None = None,
    ) -> None:
        """Publish an instance lifecycle event via the EventBus.
        
        Args:
            instance_id: The instance ID.
            status: Lifecycle status ("completed", "terminated", "error").
            error: Optional error message for error status.
            parent_id: Optional parent instance ID.
        """
        # Import here to avoid circular imports
        from ..repositories.event.models import EventKind
        
        event_data = {
            "instance_id": instance_id,
            "status": status,
            "error": error,
            "parent_id": parent_id,
        }
        
        try:
            # Publish via EventBus - this broadcasts to global subscribers including
            # JobFeedbackObserver which listens for job completion feedback
            await self._event_bus.create_event(
                instance_id=instance_id,
                kind=EventKind.INSTANCE_LIFECYCLE,
                data=event_data,
            )
            logger.debug(f"Published INSTANCE_LIFECYCLE event for {instance_id[:8]}...: status={status}")
        except Exception as e:
            logger.warning(f"Failed to publish INSTANCE_LIFECYCLE event for {instance_id[:8]}...: {e}")
