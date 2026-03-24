"""Centralized agent registry for discovering and managing agent metadata."""

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger(__name__)

# Directories to skip during agent discovery
SKIP_DIRS: frozenset[str] = frozenset({"_trash", "_baby_template"})


class AgentMetadata(BaseModel):
    """Complete agent metadata."""

    id: str = Field(..., description="Unique agent identifier (e.g., 'coder')")
    name: str = Field(..., description="Display name")
    description: str = Field(default="", description="Agent description")
    icon: str = Field(default="🤖", description="Emoji icon")
    color: str = Field(default="accent-blue", description="Color theme")
    version: str | None = Field(default=None, description="Agent version")
    path: Path = Field(..., description="Resolved absolute path to agent directory")
    system: bool = Field(default=False, description="Whether this is a system agent")
    capabilities: list[str] = Field(default_factory=list, description="Agent capabilities")
    tags: list[str] = Field(default_factory=list, description="Agent tags")

    model_config = ConfigDict(
        extra="ignore",
        json_schema_extra={
            "example": {
                "id": "coder",
                "name": "Coder",
                "description": "Specializes in code generation and debugging",
                "icon": "💻",
                "color": "accent-cyan",
                "version": "1.0.0",
                "path": "/path/to/agents/coder",
                "system": False,
                "capabilities": ["code_generation", "debugging"],
                "tags": ["coding", "development"]
            }
        }
    )

    @field_validator("path", mode="before")
    @classmethod
    def resolve_path(cls, v: Any) -> Path:
        """Ensure path is a Path object."""
        if isinstance(v, Path):
            return v
        return Path(v)


class AgentRegistry:
    """Centralized registry mapping agent_id to AgentMetadata."""

    _agents: dict[str, AgentMetadata]
    _agents_dir: Path

    def __init__(self, agents_dir: Path) -> None:
        """Initialize the registry with an agents directory.

        Args:
            agents_dir: Path to the agents directory to scan
        """
        self._agents_dir = agents_dir
        self._agents = {}

    def discover(self) -> None:
        """Scan agents directory and populate registry."""
        if not self._agents_dir.exists():
            logger.warning(f"Agents directory does not exist: {self._agents_dir}")
            return

        for agent_path in sorted(self._agents_dir.iterdir()):
            # Skip non-directories
            if not agent_path.is_dir():
                continue

            # Skip hidden directories (starting with .)
            if agent_path.name.startswith("."):
                continue

            # Skip special internal directories
            if agent_path.name in SKIP_DIRS:
                continue

            # Load and parse meta.json
            meta_path = agent_path / "meta.json"
            if not meta_path.exists():
                logger.warning(f"No meta.json found for agent directory: {agent_path.name}")
                continue

            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse meta.json for {agent_path.name}: {e}")
                continue

            # Extract agent ID (use directory name as fallback)
            agent_id = meta.get("id", agent_path.name)

            # Build AgentMetadata with defaults for missing fields
            try:
                agent_meta = AgentMetadata(
                    id=agent_id,
                    name=meta.get("name", agent_id.title()),
                    description=meta.get("description", ""),
                    icon=meta.get("icon", "🤖"),
                    color=meta.get("color", "accent-blue"),
                    version=meta.get("version"),
                    path=agent_path,
                    system=meta.get("system", False),
                    capabilities=meta.get("capabilities", []),
                    tags=meta.get("tags", []),
                )
                self._agents[agent_id] = agent_meta
            except Exception as e:
                logger.warning(f"Failed to create AgentMetadata for {agent_path.name}: {e}")

    def get(self, agent_id: str) -> AgentMetadata | None:
        """Get agent metadata by ID.

        Args:
            agent_id: The agent identifier

        Returns:
            AgentMetadata if found, None otherwise
        """
        return self._agents.get(agent_id)

    def resolve_to_id(self, agent_dir_or_id: str) -> str | None:
        """Resolve agent_dir or agent_id to canonical agent_id.

        Handles various path formats:
          - "coder" → "coder"
          - "./agents/coder" → "coder"
          - "agents/coder" → "coder"
          - "/absolute/path/to/agents/coder" → "coder"

        Args:
            agent_dir_or_id: Agent ID or path to agent directory

        Returns:
            Canonical agent_id if found, None otherwise
        """
        # Already just an ID - check if it exists
        if agent_id := self.resolve_pure_id(agent_dir_or_id):
            return agent_id

        # Try resolving as path
        return self.resolve_path_to_id(agent_dir_or_id)

    def resolve_pure_id(self, agent_id: str) -> str | None:
        """Check if a string is a valid agent ID.

        Args:
            agent_id: The string to check

        Returns:
            The agent_id if valid, None otherwise
        """
        if agent_id in self._agents:
            return agent_id
        return None

    def resolve_path_to_id(self, path_str: str) -> str | None:
        """Resolve a path string to an agent ID.

        Args:
            path_str: Path string (relative or absolute)

        Returns:
            Canonical agent_id if the path points to a valid agent, None otherwise
        """
        # Normalize path - remove leading ./ or ./
        normalized = path_str
        if normalized.startswith("./"):
            normalized = normalized[2:]
        elif normalized.startswith(".\\"):
            normalized = normalized[2:]

        # Try to extract agent_id from various path formats
        parts = normalized.replace("\\", "/").split("/")

        # Handle: agents/coder, ./agents/coder, agents/coder/
        # We need to find 'agents' segment and take the next one
        agent_parts_idx = -1
        for i, part in enumerate(parts):
            if part == "agents" and i + 1 < len(parts):
                agent_parts_idx = i + 1
                break

        if agent_parts_idx >= 0:
            potential_id = parts[agent_parts_idx]
            if potential_id in self._agents:
                return potential_id

        # Try treating the last part as an agent_id
        if parts[-1] and parts[-1] in self._agents:
            return parts[-1]

        # Try treating full path as absolute path
        if Path(path_str).is_absolute():
            try:
                abs_path = Path(path_str).resolve()
                if abs_path.parent.name == "agents":
                    agent_id = abs_path.name
                    if agent_id in self._agents:
                        return agent_id
            except (OSError, ValueError):
                pass

        return None

    def list_all(self) -> list[AgentMetadata]:
        """List all registered agents.

        Returns:
            List of all AgentMetadata objects, sorted by ID
        """
        return sorted(self._agents.values(), key=lambda a: a.id)

    def exists(self, agent_id: str) -> bool:
        """Check if agent exists.

        Args:
            agent_id: The agent identifier

        Returns:
            True if agent exists, False otherwise
        """
        return agent_id in self._agents


# Global registry instance
_registry: AgentRegistry | None = None


def get_registry() -> AgentRegistry:
    """Get or create the global registry instance.

    Returns:
        The global AgentRegistry instance
    """
    global _registry
    if _registry is None:
        from daemon.config import BASE_DIR

        _registry = AgentRegistry(BASE_DIR / "agents")
        _registry.discover()
    return _registry
