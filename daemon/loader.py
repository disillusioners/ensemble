"""Markdown loader for agent prompts."""

from pathlib import Path
from typing import Any


# Path to shared files (injected into all agents)
COMMON_TOOLS_FILE = Path(__file__).parent.parent / "agents" / "tools_common.md"
PROJECT_EXPERIENCE_FILE = Path(__file__).parent.parent / "agents" / "project-experience.md"


def load_common_tools() -> str:
    """Load common tools documentation shared by all agents.
    
    Returns:
        Content of tools_common.md or empty string if not found.
    """
    if COMMON_TOOLS_FILE.exists():
        return COMMON_TOOLS_FILE.read_text(encoding="utf-8")
    return ""


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
        Separated by "\n\n---\n\n" with headers.
    """
    section_titles = {
        "soul": "Identity",
        "rule": "Rules",
        "tools": "Tools",
        "workflow": "Workflow",
        "memory": "Memory"
    }
    
    sections: list[str] = []
    
    # 1. Add soul section first (identity - who I am)
    if "soul" in prompts:
        content = prompts["soul"].strip()
        if content:
            sections.append(f"## {section_titles['soul']}\n\n{content}")
    
    # 2. Add rule section (constraints - highest priority)
    if "rule" in prompts:
        content = prompts["rule"].strip()
        if content:
            sections.append(f"## {section_titles['rule']}\n\n{content}")
    
    # 3. Add base skill if exists (backward compatibility)
    if "skill" in prompts:
        content = prompts["skill"].strip()
        if content:
            sections.append(f"## Skills\n\n{content}")
    
    # 4. Add all skills from skills/ directory (sorted for deterministic order)
    if skills:
        for skill_name in sorted(skills.keys()):
            skill_content = skills[skill_name]
            content = skill_content.strip()
            if content:
                # Format skill name as title (e.g., "code-review" -> "Code Review")
                formatted_name = skill_name.replace("-", " ").replace("_", " ").title()
                sections.append(f"## Skill: {formatted_name}\n\n{content}")
    
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
        sections.append(f"## {section_titles['tools']}\n\n{combined_tools}")
    
    # 6-7. Add workflow and memory sections
    for key in ["workflow", "memory"]:
        if key in prompts:
            content = prompts[key].strip()
            if content:
                sections.append(f"## {section_titles[key]}\n\n{content}")
    
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
    
    # Cache miss or files changed - reload
    prompts = load_agent_prompts(agent_dir)
    skills = load_agent_skills(agent_dir)
    common_tools = load_common_tools()
    project_experience = load_project_experience()
    recent_memories = load_recent_memories(agent_dir)
    system_prompt = compose_system_prompt(prompts, skills, common_tools, project_experience, recent_memories)
    tokens = estimate_tokens(system_prompt)
    
    # Update cache
    cache.set(agent_id, system_prompt, tokens, current_mtimes)
    
    return (system_prompt, tokens)
