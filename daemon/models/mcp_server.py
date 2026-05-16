from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class McpServerCreate(BaseModel):
    """Request for creating a new MCP server."""

    name: str = Field(..., description="Unique server name", min_length=1, max_length=128)
    description: str | None = Field(default=None, description="Optional server description")
    config: dict[str, Any] = Field(default_factory=dict, description="Server configuration")
    is_active: bool = Field(default=True, description="Whether the server is active")


class McpServerUpdate(BaseModel):
    """Request for updating an MCP server."""

    name: str | None = Field(default=None, description="Server name", min_length=1, max_length=128)
    description: str | None = Field(default=None, description="Server description")
    config: dict[str, Any] | None = Field(default=None, description="Server configuration")
    is_active: bool | None = Field(default=None, description="Whether the server is active")


class McpServerInfo(BaseModel):
    """Response for MCP server information."""

    id: str = Field(..., description="Server identifier")
    name: str = Field(..., description="Server name")
    description: str | None = Field(default=None, description="Server description")
    config: dict[str, Any] = Field(..., description="Server configuration")
    is_active: bool = Field(..., description="Whether the server is active")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime | None = Field(default=None, description="Last update timestamp")


class McpServerListResponse(BaseModel):
    """Response for listing MCP servers."""

    mcp_servers: list[McpServerInfo] = Field(..., description="List of MCP servers")


class McpServerDeleteResponse(BaseModel):
    """Response for deleting an MCP server."""

    deleted: bool = Field(..., description="Whether deletion succeeded")
    id: str = Field(..., description="Server identifier")


__all__ = [
    "McpServerCreate",
    "McpServerUpdate",
    "McpServerInfo",
    "McpServerListResponse",
    "McpServerDeleteResponse",
]
