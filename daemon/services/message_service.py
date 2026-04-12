"""Unified message storage + SSE emission service.

Single entry point:
    store_message_and_emit(message) → stores + emits SSE automatically
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from daemon.message_models import ToolCallInfo, UnifiedMessage
from daemon.repositories.event.models import EventKind

if TYPE_CHECKING:
    from daemon.services.event_bus import EventBus

logger = logging.getLogger(__name__)


class MessageService:
    """Coordinates message storage confirmation + SSE emission."""
    
    def __init__(self, event_bus: EventBus):
        self._event_bus = event_bus
    
    async def on_user_message_stored(
        self,
        instance_id: str,
        message_id: str,
        content: str,
        source: str = "api",
        **extra: Any,
    ) -> UnifiedMessage:
        """Emit message_received after user message is queued."""
        message = UnifiedMessage(
            message_id=message_id,
            instance_id=instance_id,
            role="user",
            content=content,
            source=source,
            created_at=datetime.now(timezone.utc),
        )
        
        await self._event_bus.create_message_received_event(
            instance_id=instance_id,
            message_id=message_id,
            content=message.to_dict(),
        )
        
        return message
    
    async def on_assistant_message_completed(
        self,
        instance_id: str,
        original_message_id: str,
        content: str = "",
        thinking: str | None = None,
        thinking_extracted: str | None = None,
        tool_calls: list[ToolCallInfo] | None = None,
    ) -> UnifiedMessage:
        """Emit message_completed with full assistant response.
        
        This is THE key addition. Call this after LangGraph has stored
        the assistant message. It broadcasts the full message via SSE.
        """
        assistant_message_id = str(uuid.uuid4())
        
        message = UnifiedMessage(
            message_id=assistant_message_id,
            instance_id=instance_id,
            role="assistant",
            content=content,
            thinking=thinking,
            thinking_extracted=thinking_extracted,
            tool_calls=tool_calls,
            created_at=datetime.now(timezone.utc),
        )
        
        # Emit message_completed event with full payload
        try:
            await self._event_bus.create_event(
                instance_id=instance_id,
                kind=EventKind.MESSAGE_COMPLETED,
                message_id=original_message_id,
                data=message.to_dict(),
            )
        except Exception as e:
            logger.error(f"Failed to emit message_completed: {e}")

        # processing_completed: lightweight status only (content comes from message_completed)
        try:
            await self._event_bus.create_processing_completed_event(
                instance_id=instance_id,
                message_id=original_message_id,
                result={
                    "success": True,
                    "assistant_message_id": assistant_message_id,
                },
            )
        except Exception as e:
            logger.error(f"Failed to emit processing_completed: {e}")
        
        logger.info(
            f"Assistant message completed: {assistant_message_id} "
            f"for instance {instance_id[:8]}..."
        )
        return message
    
    async def on_child_completion_report(
        self,
        parent_instance_id: str,
        child_instance_id: str,
        report_content: str,
        message_id: str,
    ) -> UnifiedMessage:
        """Emit message for child agent's completion report to parent."""
        message = UnifiedMessage(
            message_id=message_id,
            instance_id=parent_instance_id,
            role="user",
            content=report_content,
            source=f"child:{child_instance_id}",
            created_at=datetime.now(timezone.utc),
        )
        
        await self._event_bus.create_message_received_event(
            instance_id=parent_instance_id,
            message_id=message_id,
            content=message.to_dict(),
        )
        
        return message

    async def on_child_error_report(
        self,
        parent_instance_id: str,
        child_instance_id: str,
        error_report: str,
        error_type: str,
        severity: str,
        message_id: str,
    ) -> UnifiedMessage:
        """Emit message for child agent's error report to parent."""
        message = UnifiedMessage(
            message_id=message_id,
            instance_id=parent_instance_id,
            role="user",
            content=error_report,
            source=f"error_report:{child_instance_id}",
            created_at=datetime.now(timezone.utc),
        )
        
        await self._event_bus.create_message_received_event(
            instance_id=parent_instance_id,
            message_id=message_id,
            content=message.to_dict(),
        )
        
        return message
