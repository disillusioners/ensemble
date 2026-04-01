# Phase 2: Recent Memories in System Prompt

## Objective
When composing the system prompt, list the 5 most recent `memories/` filenames so agents are aware of what they've stored. This is the key fix that turns write-only memories into actually useful context.

## Context
- Previous phase: New memory files use human-readable `{datetime}-{description}.md` naming
- Currently: `memories/` files are created but never loaded — agents don't know they exist
- Bug: `_update_memories()` does **NOT** call `prompt_cache.invalidate()` — new memories won't appear until cache expires naturally
- `compose_system_prompt()` builds sections in order: soul → rules → skills → tools → workflow → memory → project-experience

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add `load_recent_memories()` function | New function in `loader.py` — reads `memories/` dir, returns 5 most recent filenames | `daemon/loader.py` (new function) |
| 2 | Add cache invalidation for `memories/` | Track `memories/*.md` mtimes in `load_and_cache_prompt()` | `daemon/loader.py` L263-282 |
| 3 | Add `memories` param to `compose_system_prompt()` | New parameter, rendered as "## Recent Memories" section | `daemon/loader.py` L84-89, L163-172 |
| 4 | Wire `load_recent_memories()` into cache flow | Call new function, pass result to `compose_system_prompt()` | `daemon/loader.py` L285-289 |
| 5 | Fix cache invalidation in `inner_soul` | Add `manager.prompt_cache.invalidate(agent_id)` in `_update_memories()` | `daemon/tools/inner_soul.py` ~L374 |

## Implementation Details

### Task 1: `load_recent_memories()` function

**Add to `daemon/loader.py`** (after `load_project_experience()`):

```python
def load_recent_memories(agent_dir: Path, limit: int = 5) -> str:
    """Load list of recent memory filenames from memories/ directory.
    
    Returns filenames only (not content) to minimize token usage.
    """
    memories_dir = agent_dir / "memories"
    if not memories_dir.exists() or not memories_dir.is_dir():
        return ""
    
    memory_files = sorted(
        [f for f in memories_dir.iterdir() if f.suffix == ".md"],
        key=lambda p: p.name,  # Sort by name (timestamp-prefix sorts chronologically)
        reverse=True           # Most recent first
    )[:limit]
    
    if not memory_files:
        return ""
    
    lines = [f"- {f.name}" for f in memory_files]
    return "\n".join(lines)
```

**Key design choice:** Sort by filename (not mtime) because the filename starts with a timestamp. This is faster and consistent even if files are moved/copied.

### Task 2: Cache mtime tracking for `memories/`

**In `load_and_cache_prompt()` (after line ~272):**

```python
# Track memories/ directory mtimes for cache invalidation
memories_dir = agent_dir / "memories"
if memories_dir.exists() and memories_dir.is_dir():
    for memory_file in memories_dir.iterdir():
        if memory_file.suffix == ".md":
            current_mtimes[f"memories/{memory_file.name}"] = memory_file.stat().st_mtime
```

### Task 3: Update `compose_system_prompt()` signature and body

**Signature (line 84):**
```python
def compose_system_prompt(
    prompts: dict[str, str],
    skills: dict[str, str] | None = None,
    common_tools: str = "",
    project_experience: str = "",
    recent_memories: str = "",   # NEW
) -> str:
```

**Body — add section after memory, before project-experience (after line ~168):**
```python
# Add recent memories section
if recent_memories:
    sections.append(f"## Recent Memories\n\n{recent_memories}")
```

### Task 4: Wire into cache flow

**In `load_and_cache_prompt()` (lines 285-289):**
```python
prompts = load_agent_prompts(agent_dir)
skills = load_agent_skills(agent_dir)
common_tools = load_common_tools()
project_experience = load_project_experience()
recent_memories = load_recent_memories(agent_dir)  # NEW

system_prompt = compose_system_prompt(
    prompts, skills, common_tools, project_experience, recent_memories  # ADD param
)
```

### Task 5: Fix `_update_memories()` cache invalidation bug

**In `daemon/tools/inner_soul.py`, at end of `_update_memories()` (before return, ~L374):**
```python
# Invalidate prompt cache so new memory appears in next prompt
if manager:
    manager.prompt_cache.invalidate(agent_id)
```

**Note:** The `manager` parameter is already available in the closure from `create_inner_soul_tool()`. Check that it's accessible from `_update_memories()` — it may need to be passed explicitly if it's not in scope.

## Output Format (what agent sees)

When memories exist, this section appears in the system prompt:

```
## Recent Memories

- 20260401_1430-remember-user-prefers-terse-replies.md
- 20260401_0915-async-api-patterns-for-retry-logic.md
- 20260331_1730-deployment-to-prod-completed.md
```

When no memories exist, the section is omitted entirely (no empty header).

## Key Files
- `daemon/loader.py` — Main changes: new function, cache tracking, compose_system_prompt update
- `daemon/tools/inner_soul.py` — Bug fix: add cache invalidation in `_update_memories()`

## Constraints
- Max 5 filenames — keep it minimal, not a wall of text
- Filenames only, NOT content — content is accessed via `access_memory` tool (Phase 3)
- Section omitted entirely when no memories exist
- Don't break existing prompt ordering

## Deliverables
- [ ] `load_recent_memories()` function added to `loader.py`
- [ ] Cache tracks `memories/*.md` mtimes
- [ ] `compose_system_prompt()` has `recent_memories` parameter
- [ ] "## Recent Memories" section appears in prompts (filenames only, max 5)
- [ ] Cache invalidation bug fixed in `_update_memories()`
