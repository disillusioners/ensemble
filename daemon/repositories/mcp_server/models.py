"""MCP Server-related database models (tables)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column
from sqlalchemy.types import JSON
from sqlmodel import SQLModel, Field


class McpServer(SQLModel, table=True):
    """SQLModel McpServer table for MCP server configuration storage."""
    __tablename__ = "mcp_servers"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    name: str = Field(unique=True, index=True)
    description: str | None = Field(default=None)
    config: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON)
    )
    is_active: bool = Field(default=True)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str | None = Field(default=None)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "config": dict(self.config),
            "is_active": self.is_active,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
