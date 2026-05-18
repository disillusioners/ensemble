from __future__ import annotations

from datetime import datetime
from typing import Any
from typing import Literal

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
    is_builtin: bool = Field(default=False, description="Whether this is a built-in server")
    config_schema: list[ConfigSchemaField] | None = Field(default=None, description="Configuration schema")
    config_schema_version: str = Field(default="0", description="Schema version")
    initial_values: dict[str, Any] | None = Field(default=None, description="Initial values for form pre-fill")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime | None = Field(default=None, description="Last update timestamp")


class McpServerListResponse(BaseModel):
    """Response for listing MCP servers."""

    mcp_servers: list[McpServerInfo] = Field(..., description="List of MCP servers")


class McpServerDeleteResponse(BaseModel):
    """Response for deleting an MCP server."""

    deleted: bool = Field(..., description="Whether deletion succeeded")
    id: str = Field(..., description="Server identifier")


class ConfigSchemaField(BaseModel):
    """Schema definition for a single configuration field."""

    key: str = Field(..., description="Configuration key")
    label: str = Field(..., description="Human-readable field label")
    type: Literal["text", "number", "boolean", "select"] = Field(..., description="Field type")
    description: str | None = Field(default=None, description="Field description")
    default: Any | None = Field(default=None, description="Default value")
    required: bool = Field(default=True, description="Whether field is required")
    options: list[str] | None = Field(default=None, description="Options for select type")
    min: float | None = Field(default=None, description="Minimum value for number type")
    max: float | None = Field(default=None, description="Maximum value for number type")
    section: Literal["args", "env"] = Field(..., description="Configuration section")
    arg_format: Literal["key_value", "flag"] = Field(default="key_value", description="Argument format")


class BuiltinServerTemplate(BaseModel):
    """Template for a built-in MCP server."""

    name: str = Field(..., description="Template name")
    display_name: str = Field(..., description="Display name")
    description: str = Field(..., description="Template description")
    config_schema: list[ConfigSchemaField] = Field(..., description="Configuration schema")


class BuiltinTemplateListResponse(BaseModel):
    """Response for listing built-in server templates."""

    templates: list[BuiltinServerTemplate] = Field(..., description="List of templates")


class BuiltinServerConfigure(BaseModel):
    """Request for configuring a built-in MCP server."""

    template_name: str = Field(..., description="Template name to use")
    values: dict[str, Any] = Field(..., description="Configuration values")


__all__ = [
    "McpServerCreate",
    "McpServerUpdate",
    "McpServerInfo",
    "McpServerListResponse",
    "McpServerDeleteResponse",
    "ConfigSchemaField",
    "BuiltinServerTemplate",
    "BuiltinTemplateListResponse",
    "BuiltinServerConfigure",
]
