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
    # KMS-Lite (P3-WP6) — JSONB column carrying handle→env-key
    # bindings. Day-1 shape: ``{"bound_handles": [{"handle":
    # "KMS_HANDLE_<uuid>", "env_key": "OPENDESIGN_API_KEY",
    # "fingerprint": "<sha256[:16]>"}]}``. Plaintext NEVER rides this
    # column. Schema added by
    # ``daemon/migrations/versions/20260926_120000_add_mcp_server_instance_metadata.sql``
    # (SQLite) + ``daemon/manager.py::_ensure_postgres_columns``
    # (PostgreSQL — the migration runner is SQLite-only by design).
    instance_metadata: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONBType),
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