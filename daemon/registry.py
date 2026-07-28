"""Centralized agent registry for discovering and managing agent metadata."""

import json
import logging
import re
import sys
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger(__name__)

# Directories to skip during agent discovery.
# Underscore-prefixed directories are shared scaffolding / templates and are
# not standalone agents (no meta.json). Adding them here silences the
# "No meta.json found for agent directory" warning at startup and avoids
# pointless work in ``discover()``.
SKIP_DIRS: frozenset[str] = frozenset({
    "_trash",
    "_baby_template",
    "_prompt_system",
    "_inner_soul",
})

# Directory-name suffix parser for tagged agent versions.
# A directory named "developer[v2]" is parsed as base "developer" + tag "v2".
# The tag must contain only letters, digits, underscores, or hyphens (no
# path chars, no nested brackets). Names like "dev[[v2]]" or "dev[../etc]"
# do NOT match and are treated as plain agent ids.
_TAG_PATTERN = re.compile(r'^([^\[\]]+)\[([A-Za-z0-9_-]+)\]$')

# Module-level dedup set for the "context_injection: true" deprecation
# warning. ``discover()`` is called on every daemon poll / reload, so an
# unconditional warning would fire repeatedly for the same legacy agent.
# Keyed by agent_id — once we've warned for an agent we never warn again.
_deprecation_warned: set[str] = set()


def _parse_agent_dir_name(dir_name: str) -> tuple[str, str | None]:
    """Parse a directory name, extracting optional [tag] suffix.

    Examples:
        "developer" → ("developer", None)
        "developer[v2]" → ("developer", "v2")
        "developer[test_version]" → ("developer", "test_version")
        "dev[[v2]]" → ("dev[[v2]]", None)  # no match, inner brackets
        "dev[../etc]" → ("dev[../etc]", None)  # no match, path chars

    Returns:
        Tuple of (base_agent_id, version_tag or None)
    """
    match = _TAG_PATTERN.match(dir_name)
    if match:
        return match.group(1), match.group(2)
    return dir_name, None

# Backward-compatibility aliases for renamed agent IDs.
# Maps old agent_id -> new canonical agent_id. Used by ``resolve_pure_id``
# and (transitively) by ``resolve_path_to_id`` and ``exists`` so that
# persisted references to the old ID continue to resolve after a rename.
#
# NOTE: 'coder' was removed from this map because agents/coder/ now exists
# as a separate, canonical agent (direct hands-on implementer, distinct
# from developer). Stale DB rows with agent_id='coder' will load
# agents/coder/'s metadata directly.
AGENT_ID_ALIASES: dict[str, str] = {}


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

    id: str = Field(..., description="Unique agent identifier (e.g., 'developer')")
    name: str = Field(..., description="Display name")
    description: str = Field(default="", description="Agent description")
    icon: str = Field(default="🤖", description="Emoji icon")
    color: str = Field(default="accent-blue", description="Color theme")
    version: str | None = Field(default=None, description="Agent version")
    path: Path = Field(..., description="Resolved absolute path to agent directory")
    system: bool = Field(default=False, description="Whether this is a system agent")
    capabilities: list[str] = Field(default_factory=list, description="Agent capabilities")
    tags: list[str] = Field(default_factory=list, description="Agent tags")
    innate_skills: list[str] = Field(default_factory=list, description="Innate skills from shared registry")
    tools: ToolFilter | None = Field(
        default=None,
        description="Tool filtering configuration. None means all tools allowed."
    )
    llm_model: str | None = Field(default=None, description="Override the global LLM model for this agent")
    team_members: list[str] = Field(
        default_factory=list,
        description=(
            "Canonical agent_ids that THIS agent is allowed to spawn via "
            "spawn_instance. Empty/missing means deny-by-default — the agent "
            "cannot spawn any other agents. Enforced by the spawn_instance "
            "tool layer before any DB transaction. Aliases are resolved to "
            "canonical ids via the registry."
        ),
    )
    skill_injection: bool = Field(
        default=False,
        description="Whether this agent should have dynamic skills injected into conversations.",
    )
    context_injection: bool = Field(
        default=False,
        description="When true, inject shared project context into this agent's system prompt at spawn time.",
    )
    # ADR-8: per-agent context-injection mode flag. Two values only —
    # "human_messages" (default — context as [SYSTEM CONTEXT: ...]
    # HumanMessages) or "legacy" (opt-in — original system-prompt
    # injection behavior, used to reproduce the pre-restructure byte
    # layout). The legacy ``context_injection: true`` flag does NOT
    # influence this mode; agents that previously relied on it now
    # default to ``human_messages`` unless they explicitly set
    # ``context_injection_mode: "legacy"`` in meta.json. Validation
    # lives in
    # :func:`daemon.services.instance_lifecycle._resolve_injection_mode`
    # — unknown values are silently coerced to the default
    # (``"human_messages"``) rather than rejected, so a typo in
    # meta.json cannot break instance execution.
    context_injection_mode: str = Field(
        default="human_messages",
        description=(
            "Context injection mode — one of 'human_messages' (default, "
            "context as [SYSTEM CONTEXT: ...] HumanMessages) or 'legacy' "
            "(original system-prompt injection). See ADR-8."
        ),
    )
    inject_allowed_models: bool = Field(
        default=False,
        description="When true, inject the allowed-models list into this agent's system prompt at spawn time.",
    )
    version_tag: str | None = Field(
        default=None,
        description="Directory-name derived version tag (e.g., 'v2' for 'developer[v2]'). None = base.",
    )

    model_config = ConfigDict(
        extra="ignore",
        json_schema_extra={
            "example": {
                "id": "developer",
                "name": "Developer",
                "description": "Specializes in code generation and debugging",
                "icon": "💻",
                "color": "accent-cyan",
                "version": "1.0.0",
                "path": "/path/to/agents/developer",
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
        # Tagged agent versions, keyed by composite "base[tag]" string.
        # Plain (untagged) agents live in ``_agents`` only; tagged agents
        # live in ``_versioned_agents`` only. The two dicts never overlap.
        self._versioned_agents: dict[str, AgentMetadata] = {}
        # Per-base-agent view of available versions. Each list contains
        # ``None`` (for the base) and/or one or more tag strings, preserving
        # discovery order without duplicates.
        self._versions: dict[str, list[str | None]] = {}

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
            # NOTE: Skip rules above already validated agent_path.name.
            # Parse the directory name for an optional [tag] suffix BEFORE
            # falling back to it as the agent id, so that a directory named
            # "developer[v2]" registers as base="developer" with tag="v2"
            # rather than id="developer[v2]".
            base_agent_id, version_tag = _parse_agent_dir_name(agent_path.name)
            meta_id = meta.get("id")
            if meta_id and meta_id != base_agent_id:
                logger.warning(
                    f"Agent '{base_agent_id}' meta.json has id='{meta_id}' which differs "
                    "from directory name. Directory name takes precedence for versioning."
                )
            agent_id = meta.get("id", base_agent_id)

            # Deprecation warning: ``context_injection: true`` is the legacy
            # boolean opt-in. It no longer controls the per-agent injection
            # mode (the newer ``context_injection_mode`` field does — see
            # ADR-8). Emit a one-shot warning so agents still relying on the
            # legacy flag can migrate to ``context_injection_mode``.
            if meta.get("context_injection") and agent_id not in _deprecation_warned:
                _deprecation_warned.add(agent_id)
                logger.warning(
                    "Agent '%s' uses deprecated 'context_injection: true' flag. "
                    "This flag no longer controls context injection mode. "
                    "The agent now defaults to 'context_injection_mode: \"human_messages\"'. "
                    "To preserve the legacy system-prompt injection behavior, set 'context_injection_mode': 'legacy' in meta.json. "
                    "The 'context_injection' flag will be removed in a future version.",
                    agent_id,
                )

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
                    innate_skills=meta.get("innate_skills", []),
                    llm_model=meta.get("llm_model"),
                    team_members=meta.get("team_members", []) or [],
                    skill_injection=meta.get("skill_injection", False),
                    context_injection=meta.get("context_injection", False),
                    context_injection_mode=meta.get(
                        "context_injection_mode", "human_messages"
                    ),
                    inject_allowed_models=meta.get("inject_allowed_models", False),
                    version_tag=version_tag,
                )
                # Split storage: untagged → _agents, tagged → _versioned_agents.
                # _agents keys are NEVER composite keys.
                if version_tag is None:
                    self._agents[base_agent_id] = agent_meta
                else:
                    composite_key = f"{base_agent_id}[{version_tag}]"
                    self._versioned_agents[composite_key] = agent_meta
                # Record this version under its base id (dedup, preserve order)
                self._versions.setdefault(base_agent_id, [])
                if version_tag not in self._versions[base_agent_id]:
                    self._versions[base_agent_id].append(version_tag)
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

    def get_resolved(self, agent_id: str) -> AgentMetadata | None:
        """Get agent metadata, resolving aliases first.

        Use this when ``agent_id`` may come from an external source (DB row,
        API param, persisted metadata) that could contain a legacy alias.
        Returns ``None`` if the ID is unknown even after alias resolution.

        With ``AGENT_ID_ALIASES`` empty (current state), this method is
        functionally equivalent to :meth:`get` — it is retained as the
        canonical entry point so future renames can be supported by
        re-populating the alias map without touching call sites.

        Args:
            agent_id: The agent identifier (may be an alias).

        Returns:
            AgentMetadata for the canonical agent if found, else ``None``.
        """
        resolved = self.resolve_pure_id(agent_id)
        if resolved is None:
            return None
        return self._agents.get(resolved)

    def resolve_to_id(self, agent_dir_or_id: str) -> str | None:
        """Resolve agent_dir or agent_id to canonical agent_id.

        Handles various path formats:
          - "developer" → "developer"
          - "./agents/developer" → "developer"
          - "agents/developer" → "developer"
          - "/absolute/path/to/agents/developer" → "developer"

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
        """Check if a string is a valid agent ID (with alias support).

        Args:
            agent_id: The string to check

        Returns:
            The canonical agent_id if valid (resolving aliases), None otherwise
        """
        # Check for alias first (backward compat for renamed agents)
        canonical = AGENT_ID_ALIASES.get(agent_id, agent_id)
        if canonical in self._agents:
            return canonical
        # Also check the original in case alias maps to something not yet loaded
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

        # Handle: agents/developer, ./agents/developer, agents/developer/
        # We need to find 'agents' segment and take the next one
        agent_parts_idx = -1
        for i, part in enumerate(parts):
            if part == "agents" and i + 1 < len(parts):
                agent_parts_idx = i + 1
                break

        if agent_parts_idx >= 0:
            potential_id = parts[agent_parts_idx]
            resolved = self.resolve_pure_id(potential_id)
            if resolved is not None:
                return resolved

        # Try treating the last part as an agent_id
        if parts[-1]:
            resolved = self.resolve_pure_id(parts[-1])
            if resolved is not None:
                return resolved

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
                if abs_path.parent == self._agents_dir:
                    resolved = self.resolve_pure_id(abs_path.name)
                    if resolved is not None:
                        return resolved
            except (OSError, ValueError):
                pass

        return None

    def list_all(self) -> list[AgentMetadata]:
        """List all registered agents.

        Returns:
            List of all AgentMetadata objects, sorted by ID
        """
        return sorted(self._agents.values(), key=lambda a: a.id)

    def get_version(self, agent_id: str, version_tag: str | None = None) -> AgentMetadata | None:
        """Resolve an agent by id and optional version tag.

        Resolution order:
          1. If ``version_tag`` is provided, look up the composite key
             ``"{agent_id}[{version_tag}]"`` in ``_versioned_agents``.
          2. Otherwise, return the base (untagged) version if present.
          3. If only tagged versions exist (no base), return the
             lexicographically smallest tagged version as a fallback.

        Args:
            agent_id: Base agent identifier (never a composite key).
            version_tag: Optional directory-name tag (e.g., ``"v2"``).

        Returns:
            The matching ``AgentMetadata``, or ``None`` if unknown.
        """
        if version_tag is not None:
            composite_key = f"{agent_id}[{version_tag}]"
            return self._versioned_agents.get(composite_key)
        base_meta = self._agents.get(agent_id)
        if base_meta is not None:
            return base_meta
        versions = self._versions.get(agent_id, [])
        tagged_versions = sorted([v for v in versions if v is not None])
        if tagged_versions:
            composite_key = f"{agent_id}[{tagged_versions[0]}]"
            return self._versioned_agents.get(composite_key)
        return None

    def list_versions(self, agent_id: str) -> list[str | None]:
        """Return the known versions for a base agent id.

        The first element is typically ``None`` (the untagged base) when
        both base and tagged variants exist; ordering matches discovery.
        Returns an empty list when the agent_id is unknown.

        Args:
            agent_id: Base agent identifier (never a composite key).

        Returns:
            List of version tags (``None`` for the base) preserving
            discovery order with no duplicates.
        """
        return self._versions.get(agent_id, [])

    def list_all_grouped(self) -> dict[str, list[AgentMetadata]]:
        """List all agents grouped by base id (base + tagged versions).

        Returns:
            Dict mapping base agent_id to a list of ``AgentMetadata``
            objects containing the base (if present) and all tagged
            variants. Order within each group is unspecified.
        """
        grouped: dict[str, list[AgentMetadata]] = {}
        for agent_id, meta in self._agents.items():
            grouped.setdefault(agent_id, []).append(meta)
        for composite_key, meta in self._versioned_agents.items():
            base_id, _ = _parse_agent_dir_name(composite_key)
            grouped.setdefault(base_id, []).append(meta)
        return grouped

    def _agent_has_skill(
        self,
        metadata: AgentMetadata,
        skill_name: str,
        innate_exists: bool,
    ) -> bool:
        """Return True when the given agent has ``skill_name``.

        Two sources are checked:
          - The centralized innate-skills registry, when present at
            ``<agents_dir>/_prompt_system/innate-skills/<skill>/skill.md``
            AND the agent's ``innate_skills`` list includes the name.
          - The legacy per-agent ``skills/<skill>/skill.md`` file.

        Args:
            metadata: The agent metadata to inspect.
            skill_name: Skill name to look up (already validated).
            innate_exists: Whether the innate skill file exists on disk.

        Returns:
            ``True`` if the agent has the skill by either criterion.
        """
        if innate_exists and metadata.innate_skills and skill_name in metadata.innate_skills:
            return True
        skill_path = metadata.path / "skills" / skill_name / "skill.md"
        return skill_path.exists()

    def find_skill(self, skill_name: str) -> list[str]:
        """Find all agents that have a specific skill.

        Checks both the centralized innate-skills registry (via AgentMetadata.innate_skills)
        and legacy per-agent skills/ directories (agents/<agent_id>/skills/<skill_name>/skill.md).

        Tagged agent versions report their BASE id (no composite keys) and
        are deduplicated against any base agent with the same id.

        Args:
            skill_name: The skill name to search for.

        Returns:
            List of agent_ids that have this skill.
        """
        if '/' in skill_name or '\\' in skill_name or '..' in skill_name:
            return []
        agents_with_skill = []
        innate_skill_path = self._agents_dir / "_prompt_system" / "innate-skills" / skill_name / "skill.md"
        innate_exists = innate_skill_path.exists()
        # Check base agents (untagged): report full agent_id.
        for agent_id, metadata in self._agents.items():
            if self._agent_has_skill(metadata, skill_name, innate_exists):
                agents_with_skill.append(agent_id)
        # Check tagged versions: report BASE id only, dedup against base.
        for composite_key, metadata in self._versioned_agents.items():
            if self._agent_has_skill(metadata, skill_name, innate_exists):
                base_id = _parse_agent_dir_name(composite_key)[0]
                if base_id not in agents_with_skill:
                    agents_with_skill.append(base_id)
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
            DYNAMIC_TOOL_NAMES,
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
        all_tool_names.update(DYNAMIC_TOOL_NAMES)
        
        # Known categories come from CATEGORY_MODULES (includes categories that may not have
        # tools registered yet, e.g., dynamically created tools)
        known_categories: set[str] = set(CATEGORY_MODULES.keys())

        # Iterate both base agents and tagged versions. Every entry is
        # validated independently — a tagged version is a distinct
        # configuration even when it shares the same meta.id as the
        # base (it may carry a different tools block in its own meta.json).
        for _iter_dict in (self._agents, self._versioned_agents):
            for _iter_key, agent_meta in _iter_dict.items():
                if agent_meta.tools is None:
                    continue

                tools_filter = agent_meta.tools
                # Display uses meta.id for both base and tagged variants
                # so logs consistently reflect the canonical identifier.
                display_id = agent_meta.id

                # Check allow entries (entry is valid if it's a known category or tool name)
                if tools_filter.allow:
                    for entry in tools_filter.allow:
                        if entry not in known_categories and entry not in all_tool_names:
                            warnings.append(
                                f"Agent '{display_id}': allow entry '{entry}' is neither a known category nor a known tool"
                            )

                # Check deny entries
                if tools_filter.deny:
                    for entry in tools_filter.deny:
                        if entry not in known_categories and entry not in all_tool_names:
                            warnings.append(
                                f"Agent '{display_id}': deny entry '{entry}' is neither a known category nor a known tool"
                            )

                # Check that agent ends up with at least 1 tool
                allowed = resolve_tool_filter(
                    tools_filter.allow,
                    tools_filter.deny,
                    tool_categories=available_categories,
                )
                if allowed is not None and len(allowed) == 0:
                    warnings.append(
                        f"Agent '{display_id}': tool config results in ZERO available tools"
                    )

        return warnings

    def exists(self, agent_id: str) -> bool:
        """Check if agent exists (with alias support).

        Args:
            agent_id: The agent identifier

        Returns:
            True if agent exists (directly or via alias), False otherwise
        """
        return self.resolve_pure_id(agent_id) is not None


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
