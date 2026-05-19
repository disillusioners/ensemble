"""Context7 built-in MCP server definition.

Provides the @upstash/context7-mcp package via npx for fetching
up-to-date library documentation.
"""

from __future__ import annotations

from typing import Any

from daemon.mcp.builtin_servers.base import BuiltinServerDefinition


class Context7ServerDefinition(BuiltinServerDefinition):
    """Built-in MCP server definition for Context7."""

    @property
    def name(self) -> str:
        return "context7"

    @property
    def display_name(self) -> str:
        return "Context7"

    @property
    def description(self) -> str:
        return "Provides up-to-date library documentation for AI coding assistants. Fetches official docs, API references, and examples for libraries."

    @property
    def schema_version(self) -> str:
        return "1"

    def get_base_config(self) -> dict[str, Any]:
        """Return base configuration for @upstash/context7-mcp."""
        return {
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@upstash/context7-mcp"],
        }

    def get_config_schema(self) -> list[dict[str, Any]]:
        """Return the configuration schema for Context7 server.

        Context7 requires no configuration.
        """
        return []
