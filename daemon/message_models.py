"""Unified message models for API + internal use."""

from __future__ import annotations

import enum
from typing import Any

from pydantic import BaseModel


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
