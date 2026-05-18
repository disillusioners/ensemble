"""WebFetch built-in MCP server definition.

Provides the mcp-server-fetch package via uvx for fetching web page content.
"""

from __future__ import annotations

from typing import Any

from daemon.mcp.builtin_servers.base import BuiltinServerDefinition


class WebFetchServerDefinition(BuiltinServerDefinition):
    """Built-in MCP server definition for WebFetch (mcp-server-fetch)."""

    @property
    def name(self) -> str:
        return "webfetch"

    @property
    def display_name(self) -> str:
        return "WebFetch"

    @property
    def description(self) -> str:
        return "Fetch and read web page content. Allows agents to access web URLs and retrieve readable page content."

    @property
    def schema_version(self) -> str:
        return "1"

    def get_base_config(self) -> dict[str, Any]:
        """Return base configuration for mcp-server-fetch."""
        return {
            "transport": "stdio",
            "command": "uvx",
            "args": ["mcp-server-fetch"],
        }

    def build_config(self, user_values: dict[str, Any]) -> dict[str, Any]:
        """
        Build config with proxy_url validation.

        Validates that proxy_url (if provided) uses http:// or https:// scheme.
        """
        # Validate proxy_url if present
        proxy_url = user_values.get("proxy_url")
        if proxy_url:
            if not isinstance(proxy_url, str):
                raise ValueError("proxy_url must be a string")
            if not proxy_url.startswith(("http://", "https://")):
                raise ValueError(
                    "proxy_url must start with http:// or https:// "
                    "(e.g., 'http://proxy:8080' or 'https://proxy:8080')"
                )
        return super().build_config(user_values)

    def get_config_schema(self) -> list[dict[str, Any]]:
        """Return the configuration schema for WebFetch server."""
        return [
            {
                "key": "user_agent",
                "label": "User Agent",
                "type": "text",
                "section": "args",
                "arg_format": "key_value",
                "description": "Custom User-Agent string for HTTP requests",
                "default": "Mozilla/5.0 (compatible; MCP-WebFetch/1.0)",
                "required": False,
            },
            {
                "key": "ignore_robots_txt",
                "label": "Ignore robots.txt",
                "type": "boolean",
                "section": "args",
                "arg_format": "flag",
                "description": "Bypass robots.txt restrictions when fetching pages",
                "default": False,
                "required": False,
            },
            {
                "key": "proxy_url",
                "label": "Proxy URL",
                "type": "text",
                "section": "args",
                "arg_format": "key_value",
                "description": "HTTP proxy URL (must start with http:// or https://)",
                "default": None,
                "required": False,
            },
        ]
