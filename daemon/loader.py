"""Markdown loader for agent prompts."""

import logging
from pathlib import Path
from typing import Any

from .registry import ToolFilter

logger = logging.getLogger(__name__)


# Path to shared files (injected into all agents)
COMMON_TOOLS_FILE = Path(__file__).parent.parent / "agents" / "tools_common.md"
PROJECT_EXPERIENCE_FILE = Path(__file__).parent.parent / "agents" / "project-experience.md"


# Section-to-tool-names mapping for filtering tools_common.md
# NOTE: All tool names MUST exist in TOOL_CATEGORIES in daemon/tools/instance.py
SECTION_TOOLS: dict[str, set[str]] = {
    "File Operations": {"list_directory", "read_file", "write_file", "glob_files", "grep_files", "edit_file"},
    "Shell": {"bash", "time"},
    "Instance Management": {
        "spawn_instance", "send_message", "terminate_instance",
        "list_instances", "get_instance_info"
    },
    "Project Management": {
        "project_create", "project_get", "project_list", "project_search",
        "project_get_by_instance", "project_get_by_directory", "project_update",
        "project_set_status", "project_add_directory", "project_remove_directory",
        "project_set_tags", "project_add_tag", "project_remove_tag",
        "project_set_shortnames", "project_add_shortname", "project_remove_shortname",
        "project_set_metadata", "project_delete_metadata",
        "project_link", "project_unlink", "project_delete",
    },
    "Self-Modification": {"inner_soul", "access_memory"},
    "Help": {"tool_help"},
}


def load_common_tools_filtered(tool_filter: ToolFilter | None) -> str:
    """Load common tools documentation, filtered by agent's allowed tools.
    
    Args:
        tool_filter: Agent's tool filter configuration. If None, returns full content.
        
    Returns:
        Filtered content of tools_common.md, or full content if no filter.
    """
    if not COMMON_TOOLS_FILE.exists():
        return ""
    
    # Read file content once at the start
    content = COMMON_TOOLS_FILE.read_text(encoding="utf-8")
    
    if tool_filter is None:
        # No filter → return full content (backward compatible)
        return content
    
    # Import here to avoid circular imports
    from .tools.instance import resolve_tool_filter
    
    # Resolve filter to set of allowed tool names
    allowed_tools = resolve_tool_filter(
        allow=tool_filter.allow,
        deny=tool_filter.deny,
    )
    
    # If None returned, all tools are allowed
    if allowed_tools is None:
        return content
    
    # Parse the file content and filter sections
    lines = content.split("\n")
    
    # Extract header (lines before first ## section)
    header_lines: list[str] = []
    section_lines: list[tuple[str, list[str]]] = []  # (section_name, lines)
    current_section: str | None = None
    current_lines: list[str] = []
    
    for line in lines:
        if line.startswith("## "):
            # Save previous section
            if current_section is not None:
                section_lines.append((current_section, current_lines))
            # Start new section
            current_section = line[3:].strip()
            current_lines = [line]
        else:
            if current_section is not None:
                current_lines.append(line)
            else:
                header_lines.append(line)
    
    # Save last section
    if current_section is not None:
        section_lines.append((current_section, current_lines))
    
    # Filter sections: include if ANY tool in section is in allowed set
    filtered_sections: list[list[str]] = []
    for section_name, section_content in section_lines:
        section_tool_names = SECTION_TOOLS.get(section_name, set())
        if section_tool_names and section_tool_names & allowed_tools:
            # At least one tool from this section is allowed
            filtered_sections.append(section_content)
        elif section_name not in SECTION_TOOLS:
            # Section not in mapping - log for debugging
            logger.debug(f"Skipping unmapped tools_common section: {section_name}")
    
    # Reconstruct the filtered content
    result_lines = header_lines + ["\n"]
    result_lines.extend(["\n".join(s) for s in filtered_sections])
    
    return "\n".join(result_lines)


def load_project_experience() -> str:
    """Load project experience instructions shared by all agents.
    
    Returns:
        Content of project-experience.md or empty string if not found.
    """
    if PROJECT_EXPERIENCE_FILE.exists():
        return PROJECT_EXPERIENCE_FILE.read_text(encoding="utf-8")
    return ""


def load_recent_memories(agent_dir: Path, limit: int = 5) -> str:
    """Load list of recent memory filenames from memories/ directory.
    
    Returns filenames only (not content) to minimize token usage.
    """
    memories_dir = agent_dir / "memories"
    if not memories_dir.exists() or not memories_dir.is_dir():
        return ""
    
    memory_files = sorted(
        [f for f in memories_dir.iterdir() if f.suffix == ".md" and not f.is_symlink()],
        key=lambda p: p.name,  # Sort by name (timestamp-prefix sorts chronologically)
        reverse=True           # Most recent first
    )[:limit]
    
    if not memory_files:
        return ""
    
    lines = [f"- {f.name}" for f in memory_files]
    return "\n".join(lines)


def load_agent_skills(agent_dir: Path) -> dict[str, str]:
    """Load all skill.md files from agent's skills/ directory.
    
    Args:
        agent_dir: Path to the agent directory containing skills/ subdirectory.
        
    Returns:
        Dict with skill name (directory name) as key, skill.md content as value.
        Returns empty dict if skills/ directory doesn't exist.
    """
    skills_dir = agent_dir / "skills"
    skills: dict[str, str] = {}
    
    if not skills_dir.exists() or not skills_dir.is_dir():
        return skills
    
    for skill_dir in sorted(skills_dir.iterdir(), key=lambda p: p.name):
        if skill_dir.is_dir():
            skill_file = skill_dir / "skill.md"
            if skill_file.exists():
                skill_name = skill_dir.name
                skills[skill_name] = skill_file.read_text(encoding="utf-8")
    
    return skills


def load_agent_prompts(agent_dir: Path) -> dict[str, str]:
    """Load all markdown files from agent directory.
    
    Loads base prompts (soul.md, tools.md, workflow.md, rule.md, memory.md) and optional skill.md.
    For multiple skills, use load_agent_skills() which loads from skills/ directory.
    
    Args:
        agent_dir: Path to the agent directory containing prompt files.
        
    Returns:
        Dict with filename (without .md) as key, content as value.
        Skips missing files.
    """
    prompt_files = ["soul.md", "skill.md", "tools.md", "workflow.md", "rule.md", "memory.md"]
    prompts: dict[str, str] = {}
    
    for filename in prompt_files:
        filepath = agent_dir / filename
        if filepath.exists():
            prompts[filename.replace(".md", "")] = filepath.read_text(encoding="utf-8")
    
    return prompts


def compose_system_prompt(
    prompts: dict[str, str], 
    skills: dict[str, str] | None = None,
    common_tools: str = "",
    project_experience: str = "",
    recent_memories: str = ""
) -> str:
    """Compose system prompt from prompts dict and optional skills.
    
    Args:
        prompts: Dict with filename (without .md) as key, content as value.
                 Expected keys: soul, rule, skill, tools, workflow, memory
        skills: Optional dict with skill name as key, skill.md content as value.
                Loaded from agent's skills/ directory.
        common_tools: Common tools content from tools_common.md (shared by all agents).
        project_experience: Project experience content from project-experience.md (shared by all agents).
        
    Returns:
        Composed system prompt with sections in order:
        1. soul.md (identity/personality - who I am)
        2. rule.md (constraints - highest priority)
        3. skill.md (base skill, if exists - backward compat)
        4. All skills from skills/ directory (each as separate section)
        5. tools_common.md + tools.md (available tools - only if content exists)
        6. workflow.md (methodology)
        7. memory.md (knowledge)
        8. project-experience.md (how to use .agents directory for project knowledge)
        Separated by "\n\n---\n\n". Headers come from the file content itself.
    """
    sections: list[str] = []
    
    # 1. Add soul section first (identity - who I am)
    if "soul" in prompts:
        content = prompts["soul"].strip()
        if content:
            sections.append(content)
    
    # 2. Add rule section (constraints - highest priority)
    if "rule" in prompts:
        content = prompts["rule"].strip()
        if content:
            sections.append(content)
    
    # 3. Add base skill if exists (backward compatibility)
    if "skill" in prompts:
        content = prompts["skill"].strip()
        if content:
            sections.append(content)
    
    # 4. Add all skills from skills/ directory (sorted for deterministic order)
    if skills:
        for skill_name in sorted(skills.keys()):
            skill_content = skills[skill_name]
            content = skill_content.strip()
            if content:
                sections.append(content)
    
    # 5. Add tools section (combine common + agent-specific, only if non-empty)
    tools_parts = []
    if common_tools.strip():
        tools_parts.append(common_tools.strip())
    if "tools" in prompts:
        agent_tools = prompts["tools"].strip()
        if agent_tools:
            tools_parts.append(agent_tools)
    
    if tools_parts:
        combined_tools = "\n\n---\n\n".join(tools_parts)
        sections.append(combined_tools)
    
    # 6-7. Add workflow and memory sections
    for key in ["workflow", "memory"]:
        if key in prompts:
            content = prompts[key].strip()
            if content:
                sections.append(content)
    
    # Add recent memories section (filenames only, max 5)
    if recent_memories:
        sections.append(f"## Recent Memories\n\n{recent_memories}")
    
    # 8. Add project experience section (shared .agents directory usage)
    if project_experience.strip():
        sections.append(f"## Project Experience\n\n{project_experience.strip()}")
    
    return "\n\n---\n\n".join(sections)


def estimate_tokens(text: str) -> int:
    """Estimate token count using tiktoken with cl100k_base encoding.
    
    Args:
        text: Text to count tokens for.
        
    Returns:
        Estimated token count.
    """
    import tiktoken
    
    encoder = tiktoken.get_encoding("cl100k_base")
    return len(encoder.encode(text))


def estimate_messages_tokens(messages: list) -> int:
    """Estimate total token count for a list of LangChain messages.
    
    Accounts for per-message overhead (role tokens, formatting) that LLMs add.
    Uses rough overhead estimates based on OpenAI token accounting:
    - Each message: +4 tokens (role markers, separators)
    - Tool calls: additional tokens for function call formatting
    
    Args:
        messages: List of LangChain BaseMessage objects.
        
    Returns:
        Estimated total token count including overhead.
    """
    if not messages:
        return 0
        
    total = 0
    for msg in messages:
        # Content tokens
        content = getattr(msg, "content", "") or ""
        if isinstance(content, list):
            # Some models return content as list of blocks
            for block in content:
                if isinstance(block, dict):
                    total += estimate_tokens(block.get("text", ""))
                else:
                    total += estimate_tokens(str(block))
        else:
            total += estimate_tokens(str(content))
        
        # Per-message overhead (~4 tokens for role markers, separators)
        total += 4
        
        # Tool calls overhead
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                if isinstance(tc, dict):
                    total += estimate_tokens(str(tc.get("args", {})))
                    total += estimate_tokens(tc.get("name", ""))
                else:
                    total += estimate_tokens(str(getattr(tc, "args", {})))
                    total += estimate_tokens(getattr(tc, "name", ""))
                total += 3  # function call formatting overhead
        
        # Tool response metadata
        if hasattr(msg, "name") and msg.name:
            total += estimate_tokens(msg.name) + 2
        
        # Additional kwargs (thinking, reasoning)
        if hasattr(msg, "additional_kwargs") and msg.additional_kwargs:
            for key, val in msg.additional_kwargs.items():
                if key in ("reasoning_content", "thinking"):
                    total += estimate_tokens(str(val))
    
    return total


class PromptCache:
    """In-memory cache for compiled prompts.
    
    Uses agent_id as cache key for logical identification rather than filesystem path.
    """
    
    def __init__(self) -> None:
        self._cache: dict[str, tuple[str, int, dict[str, float]]] = {}
    
    def get(self, agent_id: str) -> tuple[str, int] | None:
        """Get cached prompt for agent.
        
        Args:
            agent_id: The agent identifier (e.g., "coder").
            
        Returns:
            Tuple of (compiled_prompt, token_count) or None if not cached.
        """
        if agent_id not in self._cache:
            return None
        
        return (self._cache[agent_id][0], self._cache[agent_id][1])
    
    def set(self, agent_id: str, prompt: str, tokens: int, mtimes: dict[str, float]) -> None:
        """Store prompt in cache.
        
        Args:
            agent_id: The agent identifier (e.g., "coder").
            prompt: Compiled system prompt.
            tokens: Token count.
            mtimes: Dict of filename to modification time.
        """
        self._cache[agent_id] = (prompt, tokens, mtimes)
    
    def invalidate(self, agent_id: str) -> None:
        """Remove agent from cache.
        
        Args:
            agent_id: The agent identifier (e.g., "coder").
        """
        self._cache.pop(agent_id, None)


def load_and_cache_prompt(agent_id: str, agent_dir: Path, cache: PromptCache) -> tuple[str, int]:
    """Load and cache agent prompts including multiple skills.
    
    Args:
        agent_id: The agent identifier (e.g., "coder").
        agent_dir: Path to the agent directory.
        cache: PromptCache instance.
        
    Returns:
        Tuple of (system_prompt, token_count).
    """
    # Calculate current mtimes for all prompt files
    prompt_files = ["soul.md", "skill.md", "tools.md", "workflow.md", "rule.md", "memory.md"]
    current_mtimes: dict[str, float] = {}
    
    # Include common tools file mtime for cache invalidation
    if COMMON_TOOLS_FILE.exists():
        current_mtimes["tools_common.md"] = COMMON_TOOLS_FILE.stat().st_mtime
    
    # Include project experience file mtime for cache invalidation
    if PROJECT_EXPERIENCE_FILE.exists():
        current_mtimes["project-experience.md"] = PROJECT_EXPERIENCE_FILE.stat().st_mtime
    
    # Include meta.json mtime for cache invalidation (tool filter config)
    meta_path = agent_dir / "meta.json"
    if meta_path.exists():
        current_mtimes["meta.json"] = meta_path.stat().st_mtime
    
    for filename in prompt_files:
        filepath = agent_dir / filename
        if filepath.exists():
            current_mtimes[filename] = filepath.stat().st_mtime
    
    # Include mtimes for all skill files in skills/ directory
    skills_dir = agent_dir / "skills"
    if skills_dir.exists() and skills_dir.is_dir():
        for skill_dir in sorted(skills_dir.iterdir(), key=lambda p: p.name):
            if skill_dir.is_dir():
                skill_file = skill_dir / "skill.md"
                if skill_file.exists():
                    # Use relative path like "skills/coding/skill.md" as key
                    relative_path = f"skills/{skill_dir.name}/skill.md"
                    current_mtimes[relative_path] = skill_file.stat().st_mtime
    
    # Track memories/ directory mtimes for cache invalidation
    memories_dir = agent_dir / "memories"
    if memories_dir.exists() and memories_dir.is_dir():
        for memory_file in memories_dir.iterdir():
            if memory_file.is_file() and memory_file.suffix == ".md":
                try:
                    current_mtimes[f"memories/{memory_file.name}"] = memory_file.stat().st_mtime
                except (PermissionError, OSError):
                    pass  # Skip broken symlinks and permission issues
    
    # Check cache
    cached = cache.get(agent_id)
    if cached is not None:
        # Get stored mtimes from cache
        stored_mtimes = cache._cache[agent_id][2] if agent_id in cache._cache else {}
        
        # Compare mtimes
        if stored_mtimes == current_mtimes:
            return cached
    
    # Get agent's tool filter for filtered common tools loading
    tool_filter: ToolFilter | None = None
    from .registry import get_registry
    try:
        registry = get_registry()
        agent_meta = registry.get(agent_id)
        if agent_meta is not None:
            tool_filter = agent_meta.tools
    except (KeyError, ValueError, RuntimeError) as e:
        logger.debug(f"Registry lookup failed for {agent_id}: {e}")
    
    # Cache miss or files changed - reload
    prompts = load_agent_prompts(agent_dir)
    skills = load_agent_skills(agent_dir)
    common_tools = load_common_tools_filtered(tool_filter)
    project_experience = load_project_experience()
    recent_memories = load_recent_memories(agent_dir)
    system_prompt = compose_system_prompt(prompts, skills, common_tools, project_experience, recent_memories)
    tokens = estimate_tokens(system_prompt)
    
    # Update cache
    cache.set(agent_id, system_prompt, tokens, current_mtimes)
    
    return (system_prompt, tokens)
