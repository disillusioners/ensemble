"""Unified message models for consistent API + SSE formats."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class MessageRole(str, enum.Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class ToolCallInfo(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = {}
    output: str | None = None


class SSEEventPayload(BaseModel):
    """Canonical SSE event envelope."""
    event_type: str
    instance_id: str
    message_id: str | None = None
    message: dict[str, Any] | None = None
    delta: dict[str, Any] | None = None
    status: dict[str, Any] | None = None


class SSEEventDelta(BaseModel):
    """Streaming delta metadata."""
    type: str  # 'chunk' | 'thinking' | 'tool_call' | 'tool_complete'
    content: str | None = None
    tool_call: dict[str, Any] | None = None
    index: int = 0


class SSEEventStatus(BaseModel):
    """Lifecycle event status."""
    success: bool | None = None
    error: str | None = None
    stage: str | None = None
    message_id: str | None = None
    metadata: dict[str, Any] | None = None


class UnifiedMessage(BaseModel):
    """Canonical message format for both GET /messages and SSE events."""
    
    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    instance_id: str
    role: str  # user | assistant | system | tool
    content: str = ""
    thinking: str | None = None
    thinking_extracted: str | None = None
    tool_calls: list[ToolCallInfo] | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: str | None = None  # api, telegram:xxx, child:instance_id

    def to_dict(self, include_nulls: bool = False) -> dict[str, Any]:
        """Single serialization for both API and SSE.
        
        Args:
            include_nulls: If True, include fields with None values.
                           If False (default), omit None fields.
        """
        result: dict[str, Any] = {
            "message_id": self.message_id,
            "role": self.role,
            "content": self.content,
            "created_at": self.created_at.isoformat(),
        }
        
        # Optional fields
        if include_nulls or self.thinking is not None:
            result["thinking"] = self.thinking
        if include_nulls or self.thinking_extracted is not None:
            result["thinking_extracted"] = self.thinking_extracted
        if include_nulls or (self.tool_calls is not None and self.tool_calls):
            result["tool_calls"] = [tc.model_dump() for tc in self.tool_calls] if self.tool_calls else None
        if include_nulls or self.source is not None:
            result["source"] = self.source
            
        return result
