"""Markdown loader for agent prompts."""

import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

from .rag.config import is_rag_enabled
from .registry import ToolFilter

logger = logging.getLogger(__name__)


# Path to shared files (injected into all agents)
# Handle PyInstaller frozen state - use executable parent for prod
if getattr(sys, 'frozen', False):
    _base_dir = Path(sys.executable).parent
else:
    _base_dir = Path(__file__).parent.parent
PROJECT_EXPERIENCE_FILE = _base_dir / "agents" / "_prompt_system" / "project-experience.md"
KNOWLEDGE_FILE = _base_dir / "agents" / "_prompt_system" / "knowledge.md"
KNOWLEDGE_NO_FORCE_EXPLORE_FILE = _base_dir / "agents" / "_prompt_system" / "knowledge_no_force_explore.md"


def _ensure_tool_metadata_populated() -> None:
    """Ensure _tool_metadata is populated by importing and scanning all tool modules.
    
    This enables category expansion in resolve_tool_filter() and ensures
    CATEGORY_DOC is available for load_tools_doc_for_agent().
    
    Safe to call multiple times - subsequent calls are no-ops if already populated.
    """
    from .tools._tool_registry import _tool_metadata, scan_tools_for_full_docs
    
    if _tool_metadata:
        return  # Already populated
    
    # Import all tool modules to trigger @register_tool_category decorators
    # and collect @tool decorated functions
    from .tools.bash import bash
    from .tools.filesystem import (
        list_directory, read_file, glob_files, write_file, grep_files, edit_file
    )
    from .tools.time import time
    from .tools.inner_soul import create_inner_soul_tool
    from .tools.access_memory import create_access_memory_tool
    from .tools.help import create_help_tool
    from .tools.project import create_project_tools
    
    # Create dummy instances to get the tools (these create closures with None manager)
    # We just need the tool objects themselves for metadata scanning
    inner_soul = create_inner_soul_tool(None, "", "")
    access_memory = create_access_memory_tool("")
    project_tools = create_project_tools(None, "", "")
    
    # Create help tool with empty tool list first
    help_tool = create_help_tool([], "")
    
    # Scan all discovered tools
    all_tools = [
        bash,
        list_directory, read_file, glob_files, write_file, grep_files, edit_file,
        time,
        inner_soul, access_memory, help_tool,
    ]
    
    # Add project tools if any
    if project_tools:
        all_tools.extend(project_tools)
    
    scan_tools_for_full_docs(all_tools)


def load_tools_doc_for_agent(agent_id: str, mcp_tool_names: list[str] | None = None) -> str:
    """Build tool documentation for an agent based on their allowed tools.
    
    Dynamically generates tool documentation by:
    1. Getting the agent's tool filter from the registry
    2. Resolving allowed tool names using resolve_tool_filter()
    3. For each allowed category, building a section with CATEGORY_NAME and tool list
    
    Args:
        agent_id: The agent identifier to get tool documentation for.
        mcp_tool_names: Optional list of MCP tool names for "mcp" category expansion.
        
    Returns:
        Formatted string with tool documentation sections.
    """
    from .registry import get_registry
    from .tools.instance import resolve_tool_filter, expand_allow_for_innate_skills
    from .tools._tool_registry import get_tool_categories, get_category_doc, _tool_metadata

    # Ensure _tool_metadata is populated by scanning tool modules
    # This is needed because load_tools_doc_for_agent may be called before
    # create_instance_tools (which also scans tools)
    if not _tool_metadata:
        _ensure_tool_metadata_populated()

    # Get agent's tool filter from registry
    # Resolve alias (backward compat for renamed agents like 'coder'→'developer')
    # so tool filtering uses the correct agent's filter instead of skipping.
    tool_filter: ToolFilter | None = None
    agent_innate_skills: list[str] | None = None
    try:
        registry = get_registry()
        agent_meta = registry.get_resolved(agent_id)
        if agent_meta is not None:
            tool_filter = agent_meta.tools
            agent_innate_skills = agent_meta.innate_skills
            # Use resolved id for downstream tool filtering context
            resolved_agent_id = registry.resolve_pure_id(agent_id) or agent_id
            agent_id = resolved_agent_id
    except (KeyError, ValueError, RuntimeError) as e:
        logger.debug(f"Registry lookup failed for {agent_id}: {e}")
        return ""

    # Build all_tool_names set for MCP category expansion
    all_tool_names: set[str] | None = None
    if mcp_tool_names:
        all_tool_names = set(mcp_tool_names)
        logger.debug(f"Including {len(mcp_tool_names)} MCP tools in tool filter resolution for {agent_id}")

    # Resolve filter to set of allowed tool names.
    # Innate skills (e.g. "opencode") implicitly grant their tool categories
    # so the system prompt lists them even when `tools.allow` omits them.
    if tool_filter is None:
        # No filter → all tools allowed, pass None to get all categories
        allowed_tools: set[str] | None = None
    else:
        effective_allow = expand_allow_for_innate_skills(
            tool_filter.allow,
            agent_innate_skills,
        )
        allowed_tools = resolve_tool_filter(
            allow=effective_allow,
            deny=tool_filter.deny,
            all_tool_names=all_tool_names,
        )
        # If None returned, all tools are allowed
        if allowed_tools is None:
            allowed_tools = None
    
    # Get categories with their tools
    categories = get_tool_categories(allowed_tools)
    
    if not categories:
        return ""
    
    # Build sections for each category
    sections: list[str] = []
    for category_key, tool_names in sorted(categories.items()):
        try:
            cat_result = get_category_doc(category_key)
            if cat_result is not None:
                category_name, category_doc = cat_result
            else:
                category_name = category_key
                category_doc = ""
        except KeyError:
            # Category not in CATEGORY_MODULES - use key as name
            category_name = category_key
            category_doc = ""
        
        # Sort tools for deterministic output
        sorted_tools = sorted(tool_names)
        tools_list = ", ".join(sorted_tools)
        
        section = f"## {category_name}\n"
        if category_doc:
            section += f"{category_doc}\n\n"
        section += f"**Available tools:** {tools_list}\n"
        section += "Use tool_help(\"tool_name\") for detailed documentation."
        
        sections.append(section)
    
    return "\n\n".join(sections)


def load_project_experience() -> str:
    """Load project experience instructions shared by all agents.
    
    Returns:
        Content of project-experience.md or empty string if not found.
    """
    if PROJECT_EXPERIENCE_FILE.exists():
        return PROJECT_EXPERIENCE_FILE.read_text(encoding="utf-8")
    return ""


def load_shared_knowledge(no_force_explore: bool = False) -> str:
    """Load shared knowledge base instructions. Only loaded when RAG is enabled.
    
    Args:
        no_force_explore: If True, load knowledge_no_force_explore.md instead
                         (for leader/orchestrator agents that delegate exploration).
    
    Returns:
        Content of knowledge file or empty string if not found/disabled.
    """
    if not is_rag_enabled():
        return ""
    knowledge_file = KNOWLEDGE_NO_FORCE_EXPLORE_FILE if no_force_explore else KNOWLEDGE_FILE
    if knowledge_file.exists():
        return knowledge_file.read_text(encoding="utf-8")
    return ""


def load_recent_memories(agent_dir: Path, limit: int = 5, include_archived: bool = False, archive_limit: int = 5) -> str:
    """Load list of recent memory filenames from memories/ directory.
    
    Returns filenames only (not content) to minimize token usage.
    When include_archived=True, also lists files from memories/archive/YYYY/MM/.
    """
    memories_dir = agent_dir / "memories"
    if not memories_dir.exists() or not memories_dir.is_dir():
        return ""
    
    # Active memory files
    memory_files = sorted(
        [f for f in memories_dir.iterdir() if f.suffix == ".md" and not f.is_symlink() and f.is_file()],
        key=lambda p: p.name,
        reverse=True
    )[:limit]
    
    lines = [f"- {f.name}" for f in memory_files]
    
    # Archived memory files
    if include_archived:
        archive_dir = memories_dir / "archive"
        if archive_dir.exists() and archive_dir.is_dir():
            archive_files = []
            for year_dir in sorted(archive_dir.iterdir(), reverse=True):
                if not year_dir.is_dir() or not year_dir.name.isdigit():
                    continue
                for month_dir in sorted(year_dir.iterdir(), reverse=True):
                    if not month_dir.is_dir() or not month_dir.name.isdigit():
                        continue
                    for f in month_dir.iterdir():
                        if f.suffix == ".md" and not f.is_symlink() and f.is_file():
                            archive_files.append(f)
            
            # Sort by full relative path (YYYY/MM/filename.md) for correct chronological order
            archive_files.sort(key=lambda p: str(p.relative_to(archive_dir)), reverse=True)
            archive_files = archive_files[:archive_limit]
            
            for f in archive_files:
                # Format as archive/YYYY/MM/filename.md
                relative = f.relative_to(archive_dir)
                lines.append(f"- archive/{relative}")
    
    if not lines:
        return ""
    
    return "\n".join(lines)


def _resolve_innate_skill_paths(agent_dir: Path, meta: dict) -> list[tuple[str, Path]]:
    """Resolve innate skill file paths from meta config."""
    innate_skills_dir = agent_dir.parent / "_prompt_system" / "innate-skills"
    return [
        (name, innate_skills_dir / name / "skill.md")
        for name in sorted(set(meta.get("innate_skills", [])))
    ]


def load_agent_skills(agent_dir: Path, meta: dict | None = None) -> dict[str, str]:
    """Load agent skills from centralized innate-skills or local skills/ directory.

    When meta is provided with a non-empty innate_skills list, loads from the
    centralized agents/_prompt_system/innate-skills/ directory. Otherwise falls back to scanning
    the agent's own skills/ directory for backward compatibility.

    An empty innate_skills array ([]) is treated as absent, triggering legacy mode.
    """
    skills: dict[str, str] = {}

    # New path: load from centralized innate-skills registry
    # NOTE: truthy check (not just "in") ensures empty array [] falls through to legacy
    if meta and meta.get("innate_skills"):
        for skill_name, skill_file in _resolve_innate_skill_paths(agent_dir, meta):
            if skill_file.exists():
                skills[skill_name] = skill_file.read_text(encoding="utf-8")
            else:
                logger.warning(f"Innate skill '{skill_name}' declared in meta.json but not found at {skill_file}")
        return skills

    # Legacy fallback: load from agent's own skills/ directory
    skills_dir = agent_dir / "skills"
    if not skills_dir.exists() or not skills_dir.is_dir():
        return skills
    for skill_dir in sorted(skills_dir.iterdir(), key=lambda p: p.name):
        if skill_dir.is_dir():
            skill_file = skill_dir / "skill.md"
            if skill_file.exists():
                skills[skill_dir.name] = skill_file.read_text(encoding="utf-8")
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
    prompt_files = ["soul.md", "skill.md", "workflow.md", "rule.md", "memory.md"]
    prompts: dict[str, str] = {}
    
    for filename in prompt_files:
        filepath = agent_dir / filename
        if filepath.exists():
            prompts[filename.replace(".md", "")] = filepath.read_text(encoding="utf-8")
    
    # Load tools_note.md with fallback to tools.md (for backward compatibility)
    tools_note_path = agent_dir / "tools_note.md"
    tools_fallback_path = agent_dir / "tools.md"
    
    if tools_note_path.exists():
        prompts["tools"] = tools_note_path.read_text(encoding="utf-8")
    elif tools_fallback_path.exists():
        prompts["tools"] = tools_fallback_path.read_text(encoding="utf-8")
    
    return prompts


def compose_system_prompt(
    prompts: dict[str, str], 
    skills: dict[str, str] | None = None,
    dynamic_tools: str = "",
    project_experience: str = "",
    recent_memories: str = "",
    shared_knowledge: str = ""
) -> str:
    """Compose system prompt from prompts dict and optional skills.
    
    Args:
        prompts: Dict with filename (without .md) as key, content as value.
                 Expected keys: soul, rule, skill, tools, workflow, memory
        skills: Optional dict with skill name as key, skill.md content as value.
                Loaded from agent's skills/ directory.
        dynamic_tools: Dynamic tools content from load_tools_doc_for_agent() (agent's available tools).
        project_experience: Project experience content from project-experience.md (shared by all agents).
        recent_memories: List of recent memory filenames.
        shared_knowledge: Shared knowledge base content from knowledge.md.
        
    Returns:
        Composed system prompt with sections in order:
        1. soul.md (identity/personality - who I am)
        2. rule.md (constraints - highest priority)
        3. skill.md (base skill, if exists - backward compat)
        4. All skills from skills/ directory (each as separate section)
        5. dynamic_tools (from load_tools_doc_for_agent - available tools based on agent config)
        6. tools.md (agent-specific tools note)
        7. workflow.md (methodology)
        8. memory.md (knowledge)
        9. Recent memories (filenames only)
        10. shared_knowledge (from _prompt_system/knowledge.md)
        11. project-experience.md (how to use .agents directory for project knowledge)
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
    
    # 5. Add dynamic tools section (from load_tools_doc_for_agent)
    if dynamic_tools.strip():
        sections.append(dynamic_tools.strip())
    
    # 6. Add agent-specific tools note (tools_note.md with fallback to tools.md)
    if "tools" in prompts:
        agent_tools = prompts["tools"].strip()
        if agent_tools:
            sections.append(agent_tools)
    
    # 7-8. Add workflow and memory sections
    for key in ["workflow", "memory"]:
        if key in prompts:
            content = prompts[key].strip()
            if content:
                sections.append(content)
    
    # 9. Add recent memories section (filenames only, max 5)
    if recent_memories:
        sections.append(f"## Recent Memories\n\n{recent_memories}")
    
    # 10. Add shared knowledge section (from _prompt_system/knowledge.md)
    if shared_knowledge.strip():
        # Strip leading H1 heading from knowledge content (file has its own, we add section heading)
        content = shared_knowledge.strip()
        content = re.sub(r'^#\s+.*\n*', '', content, count=1)
        sections.append(f"## Knowledge Base\n\n{content}")
    
    # 11. Add project experience section (shared .agents directory usage)
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
    
    Uses agent_id and MCP tool names as cache key for logical identification
    rather than filesystem path. MCP tool names are included because different
    instances may have different MCP tools, which affects the tools section.
    """
    
    def __init__(self) -> None:
        self._cache: dict[str, tuple[str, int, dict[str, float]]] = {}
    
    def _make_key(self, agent_id: str, mcp_tool_names: list[str] | None) -> str:
        """Create a cache key from agent_id and MCP tool names.
        
        Args:
            agent_id: The agent identifier.
            mcp_tool_names: Optional list of MCP tool names.
        
        Returns:
            Cache key string.
        """
        # Normalize MCP tool names: sort and join, use empty string for None/empty
        if mcp_tool_names:
            normalized_mcp = ",".join(sorted(mcp_tool_names))
        else:
            normalized_mcp = ""
        return f"{agent_id}::{normalized_mcp}"
    
    def get(self, agent_id: str, mcp_tool_names: list[str] | None = None) -> tuple[str, int] | None:
        """Get cached prompt for agent.
        
        Args:
            agent_id: The agent identifier (e.g., "developer").
            mcp_tool_names: Optional list of MCP tool names.
            
        Returns:
            Tuple of (compiled_prompt, token_count) or None if not cached.
        """
        key = self._make_key(agent_id, mcp_tool_names)
        if key not in self._cache:
            return None
        
        return (self._cache[key][0], self._cache[key][1])
    
    def set(self, agent_id: str, prompt: str, tokens: int, mtimes: dict[str, float], mcp_tool_names: list[str] | None = None) -> None:
        """Store prompt in cache.
        
        Args:
            agent_id: The agent identifier (e.g., "developer").
            prompt: Compiled system prompt.
            tokens: Token count.
            mtimes: Dict of filename to modification time.
            mcp_tool_names: Optional list of MCP tool names.
        """
        key = self._make_key(agent_id, mcp_tool_names)
        self._cache[key] = (prompt, tokens, mtimes)
    
    def invalidate(self, agent_id: str, mcp_tool_names: list[str] | None = None) -> None:
        """Remove agent from cache.
        
        Args:
            agent_id: The agent identifier (e.g., "developer").
            mcp_tool_names: Optional list of MCP tool names.
        """
        key = self._make_key(agent_id, mcp_tool_names)
        self._cache.pop(key, None)


def load_and_cache_prompt(agent_id: str, agent_dir: Path, cache: PromptCache, mcp_tool_names: list[str] | None = None) -> tuple[str, int]:
    """Load and cache agent prompts including multiple skills.
    
    Args:
        agent_id: The agent identifier (e.g., "developer").
        agent_dir: Path to the agent directory.
        cache: PromptCache instance.
        mcp_tool_names: Optional list of MCP tool names for "mcp" category expansion.
        
    Returns:
        Tuple of (system_prompt, token_count).
    """
    # Calculate current mtimes for all prompt files
    prompt_files = ["soul.md", "skill.md", "workflow.md", "rule.md", "memory.md"]
    current_mtimes: dict[str, float] = {}
    
    # Include project experience file mtime for cache invalidation
    if PROJECT_EXPERIENCE_FILE.exists():
        current_mtimes["project-experience.md"] = PROJECT_EXPERIENCE_FILE.stat().st_mtime
    
    # Load meta.json ONCE with mtime tracking and error handling
    meta_path = agent_dir / "meta.json"
    meta = None
    if meta_path.exists():
        current_mtimes["meta.json"] = meta_path.stat().st_mtime
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning(f"Failed to parse {meta_path}")
            meta = None

    # Track knowledge file mtime for cache invalidation (variant depends on agent meta)
    no_force_explore = bool(meta and meta.get("no_force_explore"))
    knowledge_file = KNOWLEDGE_NO_FORCE_EXPLORE_FILE if no_force_explore else KNOWLEDGE_FILE
    if knowledge_file.exists():
        current_mtimes[knowledge_file.name] = knowledge_file.stat().st_mtime

    for filename in prompt_files:
        filepath = agent_dir / filename
        if filepath.exists():
            current_mtimes[filename] = filepath.stat().st_mtime

    # Include mtime for tools_note.md or tools.md for cache invalidation
    tools_note_path = agent_dir / "tools_note.md"
    tools_fallback_path = agent_dir / "tools.md"
    if tools_note_path.exists():
        current_mtimes["tools_note.md"] = tools_note_path.stat().st_mtime
    elif tools_fallback_path.exists():
        current_mtimes["tools.md"] = tools_fallback_path.stat().st_mtime

    # Include mtimes for all skill files (mode-aware: innate-skills or legacy)
    if meta and meta.get("innate_skills"):
        # Innate-skills mode: track centralized skill files
        for skill_name, skill_file in _resolve_innate_skill_paths(agent_dir, meta):
            if skill_file.exists():
                current_mtimes[f"_prompt_system/innate-skills/{skill_name}/skill.md"] = skill_file.stat().st_mtime
    else:
        # Legacy mode: scan agent's own skills/ directory
        skills_dir = agent_dir / "skills"
        if skills_dir.exists() and skills_dir.is_dir():
            for skill_dir in sorted(skills_dir.iterdir(), key=lambda p: p.name):
                if skill_dir.is_dir():
                    skill_file = skill_dir / "skill.md"
                    if skill_file.exists():
                        current_mtimes[f"skills/{skill_dir.name}/skill.md"] = skill_file.stat().st_mtime
    
    # Track memories/ directory mtimes for cache invalidation
    memories_dir = agent_dir / "memories"
    if memories_dir.exists() and memories_dir.is_dir():
        for memory_file in memories_dir.iterdir():
            if memory_file.is_file() and memory_file.suffix == ".md":
                try:
                    current_mtimes[f"memories/{memory_file.name}"] = memory_file.stat().st_mtime
                except (PermissionError, OSError):
                    pass  # Skip broken symlinks and permission issues
    
    # Check cache (include mcp_tool_names in cache key)
    cached = cache.get(agent_id, mcp_tool_names)
    if cached is not None:
        # Get stored mtimes from cache using the same key
        cache_key = cache._make_key(agent_id, mcp_tool_names)
        stored_mtimes = cache._cache.get(cache_key, (None, None, {}))[2]
        
        # Compare mtimes
        if stored_mtimes == current_mtimes:
            logger.debug(f"Prompt cache hit for {agent_id} (mcp_tools={len(mcp_tool_names) if mcp_tool_names else 0})")
            return cached
    
    # Cache miss or files changed - reload
    prompts = load_agent_prompts(agent_dir)
    skills = load_agent_skills(agent_dir, meta)
    dynamic_tools = load_tools_doc_for_agent(agent_id, mcp_tool_names)
    project_experience = load_project_experience()
    recent_memories = load_recent_memories(agent_dir)
    shared_knowledge = load_shared_knowledge(no_force_explore=no_force_explore)
    system_prompt = compose_system_prompt(prompts, skills, dynamic_tools, project_experience, recent_memories, shared_knowledge)
    tokens = estimate_tokens(system_prompt)
    
    # Update cache (include mcp_tool_names in cache key)
    cache.set(agent_id, system_prompt, tokens, current_mtimes, mcp_tool_names)
    
    return (system_prompt, tokens)
