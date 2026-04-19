"""Centralized agent registry for discovering and managing agent metadata."""

import json
import logging
import sys
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger(__name__)

# Directories to skip during agent discovery
SKIP_DIRS: frozenset[str] = frozenset({"_trash", "_baby_template"})


class ToolFilter(BaseModel):
    """Tool filtering configuration for an agent.
    
    Controls which tools an agent can access.
    - allow: If present, ONLY these tools/categories are included
    - deny: Tools/categories to exclude (deny wins over allow)
    - Both empty/missing: All tools allowed (backward compatible)
    """
    
    allow: Annotated[list[str] | None, Field(
        default=None,
        description="List of tool categories or individual tool names to allow. "
                    "If present, only these tools are included."
    )] = None
    
    deny: Annotated[list[str] | None, Field(
        default=None,
        description="List of tool categories or individual tool names to deny. "
                    "These are excluded even if in allow."
    )] = None
    
    model_config = ConfigDict(
        extra="ignore",
        json_schema_extra={
            "example": {
                "allow": ["bash", "filesystem", "instance", "help"],
                "deny": ["write_file", "edit_file"]
            }
        }
    )


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
    tools: ToolFilter | None = Field(
        default=None,
        description="Tool filtering configuration. None means all tools allowed."
    )

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
                "tags": ["coding", "development"],
                "tools": {
                    "allow": ["bash", "filesystem", "instance", "help"],
                    "deny": ["write_file", "edit_file"]
                }
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

            # Skip symlinks for security
            if agent_path.is_symlink():
                logger.warning(f"Skipping symlink directory: {agent_path}")
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
            tools_config = meta.get("tools")
            tools_filter = None
            if tools_config is not None:
                try:
                    tools_filter = ToolFilter.model_validate(tools_config)
                except Exception as e:
                    logger.warning(f"Failed to parse tools config for {agent_path.name}: {e}")
            
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
                    tools=tools_filter,
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
        if not agent_dir_or_id:
            return None

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
                # Reject symlinks for security
                if abs_path.is_symlink():
                    return None
                # CRITICAL: Verify path is within agents directory
                try:
                    abs_path.relative_to(self._agents_dir)
                except ValueError:
                    return None  # Path traversal attempt - path outside agents dir
                if abs_path.parent == self._agents_dir and abs_path.name in self._agents:
                    return abs_path.name
            except (OSError, ValueError):
                pass

        return None

    def list_all(self) -> list[AgentMetadata]:
        """List all registered agents.

        Returns:
            List of all AgentMetadata objects, sorted by ID
        """
        return sorted(self._agents.values(), key=lambda a: a.id)

    def find_skill(self, skill_name: str) -> list[str]:
        """Find all agents that have a specific skill.

        Skills are stored at agents/<agent_id>/skills/<skill_name>/skill.md.
        This method scans the filesystem to find agents with the given skill.

        Args:
            skill_name: The skill name to search for.

        Returns:
            List of agent_ids that have this skill.
        """
        if '/' in skill_name or '\\' in skill_name or '..' in skill_name:
            return []
        agents_with_skill = []
        for agent_id, metadata in self._agents.items():
            skill_path = metadata.path / "skills" / skill_name / "skill.md"
            if skill_path.exists():
                agents_with_skill.append(agent_id)
        return agents_with_skill

    def validate_tool_configs(self) -> list[str]:
        """Validate all agents' tool configs. Returns list of warning messages.
        
        Checks for:
        - Unknown categories/tools in allow/deny lists
        - Configurations that result in zero available tools
        """
        warnings: list[str] = []
        
        # Import here to avoid circular imports
        from daemon.tools._tool_registry import (
            list_tools_by_category,
            scan_tools_for_full_docs,
            _tool_metadata,
            CATEGORY_MODULES,
        )
        from daemon.tools.instance import resolve_tool_filter
        
        # Populate metadata if not already done
        if not _tool_metadata:
            from daemon.tools import (
                bash,
                list_directory, read_file, glob_files, write_file, grep_files, edit_file,
                time,
            )
            scan_tools_for_full_docs([
                bash,
                list_directory, read_file, glob_files, write_file, grep_files, edit_file,
                time,
            ])
        
        # Get available categories and tools
        available_categories = list_tools_by_category()  # {category_name: [tool_names]}
        all_tool_names: set[str] = set()
        for tools in available_categories.values():
            all_tool_names.update(tools)
        
        # Known categories come from CATEGORY_MODULES (includes categories that may not have
        # tools registered yet, e.g., dynamically created tools)
        known_categories: set[str] = set(CATEGORY_MODULES.keys())
        
        for agent_id, agent_meta in self._agents.items():
            if agent_meta.tools is None:
                continue
            
            tools_filter = agent_meta.tools
            
            # Check allow entries (entry is valid if it's a known category or tool name)
            if tools_filter.allow:
                for entry in tools_filter.allow:
                    if entry not in known_categories and entry not in all_tool_names:
                        warnings.append(
                            f"Agent '{agent_id}': allow entry '{entry}' is neither a known category nor a known tool"
                        )
            
            # Check deny entries
            if tools_filter.deny:
                for entry in tools_filter.deny:
                    if entry not in known_categories and entry not in all_tool_names:
                        warnings.append(
                            f"Agent '{agent_id}': deny entry '{entry}' is neither a known category nor a known tool"
                        )
            
            # Check that agent ends up with at least 1 tool
            allowed = resolve_tool_filter(
                tools_filter.allow,
                tools_filter.deny,
                tool_categories=available_categories,
            )
            if allowed is not None and len(allowed) == 0:
                warnings.append(
                    f"Agent '{agent_id}': tool config results in ZERO available tools"
                )
        
        return warnings

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
        # Handle PyInstaller frozen state - use executable parent for prod
        if getattr(sys, 'frozen', False):
            base_dir = Path(sys.executable).parent
        else:
            base_dir = Path(__file__).parent.parent
        _registry = AgentRegistry(base_dir / "agents")
        _registry.discover()
        # Validate tool configs and log warnings
        warnings = _registry.validate_tool_configs()
        for w in warnings:
            logger.warning(f"Tool config validation: {w}")
    return _registry
