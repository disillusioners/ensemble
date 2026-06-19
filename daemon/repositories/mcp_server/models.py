"""MCP Server-related database models (tables)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column
from sqlmodel import SQLModel, Field

from daemon.repositories.infra.types import JSONBType


class McpServer(SQLModel, table=True):
    """SQLModel McpServer table for MCP server configuration storage."""
    __tablename__ = "mcp_servers"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    name: str = Field(unique=True, index=True)
    description: str | None = Field(default=None)
    config: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONBType)
    )
    is_active: bool = Field(default=True)
    is_builtin: bool = Field(default=False)
    config_schema: list[dict[str, Any]] | None = Field(
        default=None,
        sa_column=Column(JSONBType)
    )
    config_schema_version: str = Field(default="0")
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str | None = Field(default=None)
