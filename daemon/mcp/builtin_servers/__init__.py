"""Built-in MCP Server Registry."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from daemon.mcp.builtin_servers.base import BuiltinServerDefinition

logger = logging.getLogger(__name__)


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

_registry.register(WebFetchServerDefinition())
