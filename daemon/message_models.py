"""Unified message models for consistent API + SSE formats."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

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

    def to_sse_data(self) -> dict[str, Any]:
        """Format for SSE event data payload — omits None fields."""
        payload = {
            "message_id": self.message_id,
            "instance_id": self.instance_id,
            "role": self.role,
            "content": self.content,
            "created_at": self.created_at.isoformat(),
        }
        if self.thinking is not None:
            payload["thinking"] = self.thinking
        if self.thinking_extracted is not None:
            payload["thinking_extracted"] = self.thinking_extracted
        if self.tool_calls:
            payload["tool_calls"] = [tc.model_dump() for tc in self.tool_calls]
        if self.source:
            payload["source"] = self.source
        return payload

    def to_api_response(self) -> dict[str, Any]:
        """Format for GET /messages API response — keeps None as null."""
        return {
            "message_id": self.message_id,
            "role": self.role,
            "content": self.content,
            "thinking": self.thinking,
            "thinking_extracted": self.thinking_extracted,
            "tool_calls": [tc.model_dump() for tc in self.tool_calls] if self.tool_calls else None,
            "created_at": self.created_at.isoformat(),
        }
