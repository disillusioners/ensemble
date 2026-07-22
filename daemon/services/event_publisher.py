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

        # Emit global notification for root instances (parent_id is None) reaching terminal state
        if parent_id is None:
            await self._emit_root_completion_notification(
                instance_id=instance_id,
                status=status,
            )

    async def _emit_root_completion_notification(
        self,
        instance_id: str,
        status: str,
    ) -> None:
        """Emit a notification for root instance terminal state.

        Args:
            instance_id: The root instance ID.
            status: The terminal status.
        """
        broadcaster = self._manager._notification_broadcaster
        if broadcaster is None:
            return

        # Get instance metadata for agent info
        meta = self._manager._instance_repository.get(instance_id)
        if meta is None:
            logger.warning(f"Cannot emit notification: instance {instance_id[:8]}... not found")
            return

        # Derive instance_name from Instance model. The Instance model has no
        # ``name`` attribute — title is exposed as a property that reads
        # ``instance_metadata['title']``. The metadata dict is the canonical
        # source of the optional ``instance_name`` field as well. Precedence:
        #   title (from instance_metadata['title']) > instance_metadata['instance_name']
        # ``meta.title`` already returns None when instance_metadata is empty,
        # so the fallback chain is correct without an extra guard.
        instance_name = (
            meta.title
            or (meta.instance_metadata or {}).get("instance_name")
        )

        try:
            await broadcaster.emit_root_completion(
                instance_id=instance_id,
                agent_id=meta.agent_id,
                agent_name=meta.agent_name,
                status=status,
                project_id=meta.project_id,
                instance_name=instance_name,
            )
            logger.debug(
                f"Emitted notification for root instance {instance_id[:8]}...: status={status}"
            )
        except Exception as e:
            logger.warning(f"Failed to emit notification for root instance {instance_id[:8]}...: {e}")
