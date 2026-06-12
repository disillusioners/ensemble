"""Shared MCP data models.

Holds dataclasses that need to be importable from both
``daemon.services.mcp_service`` and ``daemon.mcp.warmup_pool`` without
introducing circular imports between the two layers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class McpToolSchema:
    """MCP tool schema without session binding.

    Carries just enough information to build a ``StructuredTool`` later
    on, without keeping a reference to the underlying MCP session. This
    lets the schema cache survive across instances and across cold-starts.

    Attributes:
        name: Original (un-prefixed) MCP tool name, e.g. ``"read_file"``.
        description: Human-readable description (may be empty).
        input_schema: JSON Schema dict for the tool's arguments.
        server_name: Name of the MCP server that owns this tool.
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    server_name: str


__all__ = ["McpToolSchema"]
