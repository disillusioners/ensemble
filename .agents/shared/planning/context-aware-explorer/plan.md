# Plan: Context-Aware Explorer — Auto-Save Explorer Results

## Objective
After the explorer agent completes, automatically save its result to a shared temp directory keyed by the session's context-key. This allows external agent systems (like opencode) to read accumulated exploration context from a predictable disk path.

## Scope Assessment
**SMALL** — Focused changes to 2 files + 1 file edit. No architectural shifts. All code is pure Python, no LLM involved in save logic.

## Context
- **Project**: agents-ensemble
- **Working Directory**: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- **Key insight**: CONTEXT_KEY (root parent instance ID) is already appended to all system prompts via `append_context_key()` in `daemon/services/instance_lifecycle.py`

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add auto-save function to knowledge_tools.py | Create `_save_explorer_result()` that writes the markdown file with metadata header to `{tempdir}/ensemble/context/{context-key}/` | `daemon/tools/knowledge_tools.py` |
| 2 | Call save function in explore() tool | After explorer returns result (line ~290), call `_save_explorer_result()` — fire-and-forget with try/except, never fail the explore response | `daemon/tools/knowledge_tools.py` |
| 3 | Add shared context template to knowledge.md | Add instruction that agents controlling external systems must include a context template with `{shared_context_dir}` and `{context-key}` variables | `agents/_prompt_system/knowledge.md` |
| 4 | Resolve `{shared_context_dir}` in prompt assembly | In `append_context_key()` (or adjacent), resolve `{shared_context_dir}` placeholder in the composed prompt to the actual temp directory path | `daemon/services/instance_lifecycle.py` |

## Implementation Details

### Task 1: `_save_explorer_result()` function

**Location**: `daemon/tools/knowledge_tools.py`, new function before `create_knowledge_tools()`

**Signature**:
```python
def _save_explorer_result(
    query: str,
    result: str,
    context_key: str,
    project_name: str | None = None,
    mode: str = "hybrid",
) -> None:
```

**Logic**:
1. Compute paths:
   - `tempdir = tempfile.gettempdir()`
   - `slug = re.sub(r'[^a-z0-9]+', '-', query.lower())[:80]`
   - `timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")`
   - `dir_path = Path(tempdir) / "ensemble" / "context" / context_key`
   - `file_path = dir_path / f"{slug}_{timestamp}.md"`
2. Create directory: `dir_path.mkdir(parents=True, exist_ok=True)`
3. Format content with metadata header (query, ISO timestamp, project name, mode)
4. Write file atomically (or just `file_path.write_text(...)`)
5. Wrap entire function in try/except — log warning on failure, never raise

**New imports needed**: `import tempfile`, `from datetime import datetime`, `from pathlib import Path` (verify existing imports)

### Task 2: Call in `explore()` tool

**Location**: `daemon/tools/knowledge_tools.py`, inside `explore()` function, after line 290 (after stripping the heading, before `return result`)

**Key: getting the context_key**:
- The `explore()` closure has access to `manager` and `current_instance_id`
- Need to get the root parent ID (context_key). Two approaches:
  - **Option A (preferred)**: Use `manager._instance_repository.get_tree_root_id(current_instance_id)` — same pattern used in `append_context_key()`
  - **Option B**: Parse CONTEXT_KEY from system prompt — fragile, don't do this

**Also need project_name**: 
- Use `manager._project_repository.get(pid)` to get Project object, then `.name`

**Code insertion** (after line 290, before `return result`):
```python
# Auto-save explorer result to shared context directory (fire-and-forget)
try:
    root_id = manager._instance_repository.get_tree_root_id(current_instance_id)
    context_key = root_id or current_instance_id
    project_name = None
    if pid and hasattr(manager, '_project_repository'):
        try:
            proj = manager._project_repository.get(pid)
            project_name = proj.name if proj else None
        except Exception:
            pass
    _save_explorer_result(
        query=query,
        result=result,
        context_key=context_key,
        project_name=project_name,
        mode=mode,
    )
except Exception as e:
    logger.debug("Failed to save explorer result to shared context: %s", e)
```

### Task 3: Update `knowledge.md`

**Location**: `agents/_prompt_system/knowledge.md`, add new section before "## Important Notes"

**Content to add**:
```markdown
---

## Shared Context for External Agent Systems

When controlling external agent systems (opencode, etc.), you MUST include the following context template in your prompt. This ensures the external system has access to shared exploration context accumulated during this session.

**Context template to include**:

> Current context-key is: {context-key}
>
> We are working in a multi-agent system environment named ensemble. Current shared-explored context and knowledge base files are under this directory:
>
> {shared_context_dir}
>
> Important: Read and understand all shared context files in that directory first before proceeding.

The `{context-key}` and `{shared_context_dir}` variables are automatically resolved when your system prompt is assembled.
```

### Task 4: Resolve `{shared_context_dir}` in prompt assembly

**Location**: `daemon/services/instance_lifecycle.py`, in `append_context_key()` function

**Current behavior**: Appends `CONTEXT_KEY: {root_id}` to the system prompt.

**New behavior**: After computing `root_id`, also resolve `{shared_context_dir}` and `{context-key}` placeholders throughout the system prompt.

**Changes to `append_context_key()`**:
```python
def append_context_key(
    system_prompt: str,
    instance_id: str,
    instance_repository: "SQLModelInstanceRepository",
    parent_id: Optional[str] = None,
) -> str:
    # ... existing root_id resolution logic ...
    
    # Resolve shared_context_dir (cross-platform)
    import tempfile
    from pathlib import Path
    shared_context_dir = str(Path(tempfile.gettempdir()) / "ensemble" / "context" / root_id)
    
    # Resolve placeholders in prompt body
    system_prompt = system_prompt.replace("{context-key}", root_id)
    system_prompt = system_prompt.replace("{shared_context_dir}", shared_context_dir)
    
    # Append CONTEXT_KEY section (existing behavior)
    context_section = f"\n---\n\n## Context Key\n\nCONTEXT_KEY: {root_id}\n"
    return system_prompt + context_section
```

**Note**: The `import tempfile` and `from pathlib import Path` should be moved to the top of the file for cleanliness.

## Key Files
- `daemon/tools/knowledge_tools.py` — Explorer tool + save function
- `daemon/services/instance_lifecycle.py` — `append_context_key()` for variable resolution
- `agents/_prompt_system/knowledge.md` — Context template instructions

## Constraints
- Explorer-only (no planner, experience, or context saves)
- No LLM in save logic — pure Python
- Cross-platform (Linux, macOS, Windows via `tempfile.gettempdir()`)
- Fire-and-forget — save failures never break the explore() tool
- No cleanup needed — OS temp cleanup

## Success Criteria
- [ ] `explore()` saves results to `{tempdir}/ensemble/context/{context-key}/{slug}_{timestamp}.md`
- [ ] Saved files include metadata header (query, time, project, mode)
- [ ] `knowledge.md` includes shared context template with `{context-key}` and `{shared_context_dir}` placeholders
- [ ] `append_context_key()` resolves both placeholders in system prompt
- [ ] Save failures are logged but never break the explore tool
- [ ] Directory auto-creation works cross-platform

## Tracking
- Created: 2026-05-31
- Status: draft
