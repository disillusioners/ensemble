"""Markdown loader for agent prompts."""

from pathlib import Path
from typing import Any


def load_agent_prompts(agent_dir: Path) -> dict[str, str]:
    """Load all 4 markdown files from agent directory.
    
    Args:
        agent_dir: Path to the agent directory containing prompt files.
        
    Returns:
        Dict with filename (without .md) as key, content as value.
        Skips missing files.
    """
    prompt_files = ["skill.md", "workflow.md", "rule.md", "memory.md"]
    prompts: dict[str, str] = {}
    
    for filename in prompt_files:
        filepath = agent_dir / filename
        if filepath.exists():
            prompts[filename.replace(".md", "")] = filepath.read_text(encoding="utf-8")
    
    return prompts


def compose_system_prompt(prompts: dict[str, str]) -> str:
    """Compose system prompt from prompts dict.
    
    Args:
        prompts: Dict with filename (without .md) as key, content as value.
        
    Returns:
        Composed system prompt with sections in order:
        1. rule.md (constraints - highest priority)
        2. skill.md (capabilities)
        3. workflow.md (methodology)
        4. memory.md (knowledge)
        Separated by "\n\n---\n\n" with headers.
    """
    section_order = ["rule", "skill", "workflow", "memory"]
    section_titles = {
        "rule": "Rules",
        "skill": "Skills",
        "workflow": "Workflow",
        "memory": "Memory"
    }
    
    sections: list[str] = []
    for key in section_order:
        if key in prompts:
            content = prompts[key].strip()
            if content:
                sections.append(f"## {section_titles[key]}\n\n{content}")
    
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
    """In-memory cache for compiled prompts."""
    
    def __init__(self) -> None:
        self._cache: dict[str, tuple[str, int, dict[str, float]]] = {}
    
    def get(self, agent_dir: Path) -> tuple[str, int] | None:
        """Get cached prompt for agent directory.
        
        Args:
            agent_dir: Path to the agent directory.
            
        Returns:
            Tuple of (compiled_prompt, token_count) or None if not cached.
        """
        key = str(agent_dir)
        if key not in self._cache:
            return None
        
        return (self._cache[key][0], self._cache[key][1])
    
    def set(self, agent_dir: Path, prompt: str, tokens: int, mtimes: dict[str, float]) -> None:
        """Store prompt in cache.
        
        Args:
            agent_dir: Path to the agent directory.
            prompt: Compiled system prompt.
            tokens: Token count.
            mtimes: Dict of filename to modification time.
        """
        key = str(agent_dir)
        self._cache[key] = (prompt, tokens, mtimes)
    
    def invalidate(self, agent_dir: Path) -> None:
        """Remove agent from cache.
        
        Args:
            agent_dir: Path to the agent directory.
        """
        key = str(agent_dir)
        self._cache.pop(key, None)


def load_and_cache_prompt(agent_dir: Path, cache: PromptCache) -> tuple[str, int]:
    """Load and cache agent prompts.
    
    Args:
        agent_dir: Path to the agent directory.
        cache: PromptCache instance.
        
    Returns:
        Tuple of (system_prompt, token_count).
    """
    # Calculate current mtimes for all prompt files
    prompt_files = ["skill.md", "workflow.md", "rule.md", "memory.md"]
    current_mtimes: dict[str, float] = {}
    
    for filename in prompt_files:
        filepath = agent_dir / filename
        if filepath.exists():
            current_mtimes[filename] = filepath.stat().st_mtime
    
    # Check cache
    cached = cache.get(agent_dir)
    if cached is not None:
        # Get stored mtimes from cache
        key = str(agent_dir)
        stored_mtimes = cache._cache[key][2] if key in cache._cache else {}
        
        # Compare mtimes
        if stored_mtimes == current_mtimes:
            return cached
    
    # Cache miss or files changed - reload
    prompts = load_agent_prompts(agent_dir)
    system_prompt = compose_system_prompt(prompts)
    tokens = estimate_tokens(system_prompt)
    
    # Update cache
    cache.set(agent_dir, system_prompt, tokens, current_mtimes)
    
    return (system_prompt, tokens)
