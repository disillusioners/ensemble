# Phase 2: Tool Module

## Objective
Create `daemon/tools/critical_experience.py` with three tools (`project_ce_add`, `project_ce_list`, `project_ce_remove`) implementing upsert with semantic merge logic, eviction of lowest-priority entries when full (30 max), and proper access control registration.

## Coupling
- **Depends on**: Phase 1 (schema)
- **Coupling type**: **tight** — tools directly operate on the `critical_experience` JSON column and `CriticalExperience` model defined in Phase 1
- **Shared files with other phases**: None (new file)
- **Shared APIs/interfaces**: None (self-contained tool file)
- **Why this coupling**: Tools are the only code that writes to and manages the `critical_experience` field. Phase 3 (Experiencer) only reads/calls these tools.

## Context
- Previous phase completed: Schema defined, migration run, bug fixed
- This phase creates the tool file and registers it in the tool system

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create `daemon/tools/critical_experience.py` — skeleton | Create new file. Imports: `@tool` from langchain_core, `@register_tool_category` from `._tool_registry`, `SQLModelProjectRepository`, `CriticalExperience`, `CriticalExperienceCategory`, `CriticalExperiencePriority` from `..repositories.project.models`. Define `CATEGORY_NAME = "critical_experience"` and `CATEGORY_DOC`. Define `_MAX_ENTRIES = 30` and `_MAX_SUMMARY_LEN = 200`. | `daemon/tools/critical_experience.py` (new) |
| 2 | Implement merge helper `_find_similar_entry()` | Given category + summary text, search existing entries for same category with similar theme. Use keyword overlap: extract keywords from summary (words > 3 chars), check overlap with existing entry summaries. Threshold: ≥2 shared keywords = match. Return the matching entry or None. This is used by `project_ce_add` for upsert logic. | `daemon/tools/critical_experience.py` |
| 3 | Implement merge logic `_merge_entries()` | Given existing entry + new entry: keep the more concise/accurate summary (shorter wins if both are accurate), keep original `created_at`, update `updated_at` to now, update `source_agent` to new agent, keep the `reference` from whichever has one. Return merged `CriticalExperience`. | `daemon/tools/critical_experience.py` |
| 4 | Implement eviction helper `_evict_if_needed()` | If `len(entries) >= _MAX_ENTRIES`, sort entries by: 1) priority (critical > high > medium), 2) created_at ascending (oldest first). Remove the oldest lowest-priority entry. Return modified list. | `daemon/tools/critical_experience.py` |
| 5 | Implement `project_ce_add` tool | **Inputs**: `project_id: str`, `category: str`, `priority: str`, `summary: str` (max 200), `reference: str \| None = None`. **Calling sequence** (MUST follow this exact order): ① Validate category/priority/summary. ② Get project + current entries list. ③ Check for merge via `_find_similar_entry()`. ④ **If merge found** → merge in-place, no eviction needed. ⑤ **If no merge** → call `_evict_if_needed()` on current list first (triggers at ≥30, drops to 29), THEN append new entry (29→30). ⑥ Save + return. | `daemon/tools/critical_experience.py` |
| 6 | Implement `project_ce_list` tool | **Inputs**: `project_id: str`. **Logic**: 1) Get project from store. 2) Return the `critical_experience` list. Return format: `list[dict]` (each dict from `entry.to_dict()`). | `daemon/tools/critical_experience.py` |
| 7 | Implement `project_ce_remove` tool | **Inputs**: `project_id: str`, `entry_id: str`. **Logic**: 1) Get project from store. 2) Find entry by `id`. 3) If not found → return error. 4) Remove from list, save. 5) Return confirmation with removed entry summary. | `daemon/tools/critical_experience.py` |
| 8 | Create factory function `create_critical_experience_tools()` | Following the pattern of `create_project_tools()` in `project.py:324`. Signature: `(store: SQLModelProjectRepository, current_instance_id: str = "", agent_id: str = "") -> list`. Returns list of the 3 tools with `@register_tool_category("critical_experience")` decorators applied. | `daemon/tools/critical_experience.py` |
| 9 | Wire into `create_instance_tools()` | In `daemon/tools/instance.py`, add import + call to `create_critical_experience_tools()` and extend tools list. **Placement**: After `project_tools.extend()` (line 608) — critical experience tools depend on project store and are project-scoped. | `daemon/tools/instance.py` |

## Key Files
- `daemon/tools/critical_experience.py` — New tool module (self-contained)
- `daemon/tools/instance.py` — Wire up new tools in `create_instance_tools()`

## Detailed Implementation Notes

### Tool Registration Pattern

Each tool function is defined inside `create_critical_experience_tools()` with `@register_tool_category("critical_experience")` decorator, following the pattern from `project.py`:

```python
def create_critical_experience_tools(store, current_instance_id, agent_id):
    @register_tool_category("critical_experience")
    @tool
    def project_ce_add(...):
        """Add or update a critical experience entry for a project. Use tool_help() for details."""
        ...

    project_ce_add._full_doc_ = """Full documentation for project_ce_add..."""
    
    # Similar for project_ce_list and project_ce_remove
    return [project_ce_add, project_ce_list, project_ce_remove]
```

### Merge Algorithm (Task 2)

```python
def _find_similar_entry(entries: list[CriticalExperience], category: str, summary: str) -> CriticalExperience | None:
    """Find an entry with same category and similar theme."""
    # Extract keywords from new summary
    new_keywords = {w.lower() for w in summary.split() if len(w) > 3}
    
    for entry in entries:
        if entry.category != category:
            continue
        # Extract keywords from existing summary
        existing_keywords = {w.lower() for w in entry.summary.split() if len(w) > 3}
        # Check overlap
        overlap = new_keywords & existing_keywords
        if len(overlap) >= 2:  # At least 2 shared keywords
            return entry
    return None
```

### Eviction Logic (Task 4)

```python
def _evict_if_needed(entries: list[CriticalExperience]) -> list[CriticalExperience]:
    if len(entries) < _MAX_ENTRIES:
        return entries
    
    # Priority order: critical > high > medium
    priority_order = {"critical": 0, "high": 1, "medium": 2}
    
    # Sort by (priority_order, created_at)
    sorted_entries = sorted(
        entries,
        key=lambda e: (priority_order.get(e.priority, 2), e.created_at)
    )
    
    # Remove the oldest lowest-priority (first in sorted list)
    return sorted_entries[1:]  # Remove first (lowest priority, oldest)
```

### project_ce_add: Exact Calling Sequence (Task 5)

**The order of operations is critical. Follow this sequence exactly:**

```python
@register_tool_category("critical_experience")
@tool
def project_ce_add(
    project_id: str,
    category: str,
    priority: str,
    summary: str,
    reference: str | None = None,
) -> dict:
    """Add or update a critical experience entry for a project. Use tool_help() for details."""
    
    # ── Step ①: Validate inputs ──
    if not CriticalExperienceCategory.is_valid(category):
        return {"error": f"Invalid category '{category}'..."}
    if not CriticalExperiencePriority.is_valid(priority):
        return {"error": f"Invalid priority '{priority}'..."}
    if len(summary) > _MAX_SUMMARY_LEN:
        return {"error": f"Summary must be ≤{_MAX_SUMMARY_LEN} chars, got {len(summary)}"}
    
    # ── Step ②: Load project + current entries ──
    project = store.get(project_id)
    if not project:
        return {"error": f"Project '{project_id}' not found"}
    
    entries = [
        CriticalExperience(**e) if isinstance(e, dict) else e
        for e in (project.critical_experience or [])
    ]
    
    # ── Step ③: Check for merge ──
    similar = _find_similar_entry(entries, category, summary)
    
    if similar is not None:
        # ── Step ④: MERGE PATH — merge in-place, no eviction ──
        merged = _merge_entries(similar, CriticalExperience(
            category=category, priority=priority, summary=summary,
            reference=reference, source_agent=agent_id,
        ))
        entries = [merged if e.id == similar.id else e for e in entries]
        # No eviction needed — we replaced an existing entry, count unchanged
    else:
        # ── Step ⑤: NEW ENTRY PATH — evict FIRST, then append ──
        entries = _evict_if_needed(entries)   # if len >= 30 → drops to 29
        new_entry = CriticalExperience(
            category=category, priority=priority, summary=summary,
            reference=reference, source_agent=agent_id,
        )
        entries.append(new_entry)             # 29 → 30 (or N → N+1 if under limit)
    
    # ── Step ⑥: Save + return ──
    project.critical_experience = [e.to_dict() for e in entries]
    store.update_critical_experience(project_id, project.critical_experience)
    return (merged if similar else new_entry).to_dict()
```

**Why this order matters:**
- Merge path: No eviction needed — we're replacing an existing entry, not adding a new one
- New entry path: Eviction runs on the current list (size 30) → drops to 29 → then we append → back to 30
- This ensures we never exceed `_MAX_ENTRIES` (30) at any point

### Error Handling Pattern

Follow existing tool patterns (e.g., `project_create` in `project.py`):
- Validation errors → return `{"error": "message"}`
- Not found → return `{"error": "Entry not found"}`
- Success → return dict with `entry.to_dict()` or confirmation message

### Wire-in Location

In `daemon/tools/instance.py`, after line 608 (`tools.extend(project_tools)`):

```python
# Create critical experience tools (project-scoped management)
ce_tools = create_critical_experience_tools(store, current_instance_id, agent_id)
tools.extend(ce_tools)
```

**Wait**: Need to check how `store` is accessible at this point. Looking at line 573:
```python
project_tools = create_project_tools(manager.project_store, current_instance_id, agent_id)
```

So `manager.project_store` is available. Add import at top of `instance.py`:
```python
from .critical_experience import create_critical_experience_tools
```

## Constraints
- **Summary max 200 chars enforced at tool level** (Phase 1 model validator is first line of defense; tool layer re-validates)
- **Max 30 entries enforced at tool level** (eviction on add)
- Merge must be conservative — require keyword overlap ≥2, not just same category
- Each tool returns structured dicts (not raw Python objects)
- Follow existing tool naming conventions: `project_ce_*` prefix
- No new imports beyond what's needed

## Deliverables
- [ ] `daemon/tools/critical_experience.py` created with 3 tools
- [ ] `project_ce_add` with upsert + merge + eviction logic
- [ ] `project_ce_list` returning all entries
- [ ] `project_ce_remove` with ID-based deletion
- [ ] `create_critical_experience_tools()` factory function
- [ ] Wired into `create_instance_tools()` in `instance.py`
- [ ] Access controlled by `meta.json` tool config (see Phase 3)
