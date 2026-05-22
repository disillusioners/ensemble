"""Built-in MCP Server Registry."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from daemon.mcp.builtin_servers.base import BuiltinServerDefinition

logger = logging.getLogger(__name__)


def is_builtin_disabled(server_name: str) -> bool:
    """Check if a built-in server is disabled via environment variable.

    Looks for MCP_DISABLE_BUILT_IN_{SERVER_NAME} env var (uppercase).
    If set to "true" (case-insensitive), the server is considered disabled.

    Args:
        server_name: Name of the built-in server (e.g., "context7", "webfetch").

    Returns:
        True if the server is disabled, False otherwise.
    """
    env_var = f"MCP_DISABLE_BUILT_IN_{server_name.upper()}"
    return os.environ.get(env_var, "").lower() == "true"


class BuiltinServerRegistry:
    """Singleton registry for built-in MCP server definitions."""

    _instance: "BuiltinServerRegistry | None" = None
    _definitions: dict[str, "BuiltinServerDefinition"]

    def __new__(cls) -> "BuiltinServerRegistry":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._definitions = {}
        return cls._instance

    def register(self, definition: "BuiltinServerDefinition") -> None:
        """Register a built-in server definition."""
        self._definitions[definition.name] = definition

    def get_all(self) -> list["BuiltinServerDefinition"]:
        """Get all registered definitions."""
        return list(self._definitions.values())

    def get_by_name(self, name: str) -> "BuiltinServerDefinition | None":
        """Get a definition by name."""
        return self._definitions.get(name)

    @property
    def definitions(self) -> dict[str, "BuiltinServerDefinition"]:
        """Access all definitions (returns a copy)."""
        return dict(self._definitions)

    def unregister(self, name: str) -> None:
        """Remove a registered definition by name."""
        self._definitions.pop(name, None)


# Module-level convenience
_registry = BuiltinServerRegistry()


def get_registry() -> BuiltinServerRegistry:
    """Get the global registry instance."""
    return _registry


# Register built-in server definitions
from daemon.mcp.builtin_servers.webfetch import WebFetchServerDefinition
from daemon.mcp.builtin_servers.context7 import Context7ServerDefinition

_registry.register(WebFetchServerDefinition())
_registry.register(Context7ServerDefinition())
