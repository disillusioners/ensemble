# Phase 3: Integration Layer — Registry, Loading, Injection

## Objective
Wire the project history tools into the tool registry, instance tool loading, and project context injection. This is the "glue" phase that makes everything work together.

## Coupling
- **Depends on**: Phase 1 (repository methods for injection), Phase 2 (tool factory for loading)
- **Coupling type**: loose
- **Shared files with other phases**:
  - `daemon/tools/_tool_registry.py` — add category mapping (Phase 2 module reference)
  - `daemon/tools/instance.py` — add tool loading call (imports Phase 2 factory)
  - `daemon/manager.py` — update `format_project_context()` (calls Phase 1 repository)
- **Shared APIs/interfaces**:
  - `create_project_history_tools()` factory from Phase 2
  - `get_recent_history()` repository method from Phase 1
- **Why this coupling**: Integration connects the data and tool layers to the rest of the system without modifying their internals.

## Context
- `CATEGORY_MODULES` in `_tool_registry.py` maps category names to module paths
- Instance tools loaded in `daemon/tools/instance.py:573-615` — tools are created and appended to the tools list
- `format_project_context()` in `daemon/manager.py:156-197` — builds markdown context block from project data
- Current injection format: JSON block + Critical Experience section with emoji icons

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Register `project_history` category in registry | Add `"project_history": "daemon.tools.project_history"` to `CATEGORY_MODULES` dict. | `daemon/tools/_tool_registry.py` |
| 2 | Load tools in instance creation | Import `create_project_history_tools` and call it after critical experience tools, extending the tools list. | `daemon/tools/instance.py` |
| 3 | Update `format_project_context()` | After the Critical Experience section, add a "### 📜 Recent History" section that renders the last 10 history entries as formatted markdown with timestamps and type icons. | `daemon/manager.py` |
| 4 | Wire repository access into injection | The `format_project_context()` function currently only receives a `project` object. It needs access to the store to call `get_recent_history()`. Pass store as parameter or use an alternative approach. | `daemon/manager.py`, `daemon/services/instance_messaging.py` |

## Key Files
- `daemon/tools/_tool_registry.py` — Add 1 line to CATEGORY_MODULES dict
- `daemon/tools/instance.py` — Add ~4 lines for tool loading
- `daemon/manager.py` — Update `format_project_context()` to include history section
- `daemon/services/instance_messaging.py` — Pass store to `format_project_context()` if needed

## Detailed Implementation Notes

### 1. Registry Update (`_tool_registry.py`)
Add to `CATEGORY_MODULES` dict (around line 184-198):
```python
"project_history": "daemon.tools.project_history",
```

### 2. Tool Loading (`instance.py`)
After the critical experience tools section (~line 607):
```python
# Project history tools (chronological project event recording)
from daemon.tools.project_history import create_project_history_tools
history_tools = create_project_history_tools(manager.project_store, current_instance_id, agent_id)
tools.extend(history_tools)
```

### 3. Context Formatting (`manager.py`)

**Challenge:** `format_project_context(project)` currently takes only a project object. To get history, it needs the store.

**Approach:** Add optional `store` parameter. When provided, fetch recent history and format it.

**Note:** Ensure `logger` is available in `manager.py` (verify existing import: `import logging` / `logger = logging.getLogger(...)`). If not present, add it.

```python
def format_project_context(project, store=None) -> str:
    """Format project info as context block for prepending to message."""
    import json
    
    project_dict = project.to_dict() if hasattr(project, 'to_dict') else vars(project)
    
    # ... existing critical experience section ...
    
    # Build history section
    history_section = ""
    if store:
        try:
            recent = store.get_recent_history(project.project_id, limit=10)
            if recent:
                history_section = "\n### 📜 Recent History\n"
                type_icons = {
                    "milestone": "🏁", "commit": "📦", "phase": "🔄",
                    "bugfix": "🐛", "deployment": "🚀", "note": "📝",
                    "config_change": "⚙️", "other": "📋",
                }
                for entry in recent:
                    icon = type_icons.get(entry.entry_type, "📋")
                    ts = entry.created_at.strftime("%Y-%m-%d %H:%M") if entry.created_at else "?"
                    history_section += f"- {icon} **[{entry.entry_type}]** {entry.summary} _({ts})_\n"
        except Exception:
            logger.warning("History injection failed", exc_info=True)
            pass  # Non-critical; don't break injection if history fails
    
    # Remove critical_experience from data dict (already formatted above)
    data = {k: v for k, v in project_dict.items() if k != "critical_experience"}
    
    return f"""## Related Project

```json
{json.dumps(data, indent=2)}
```
{ce_section}{history_section}
"""
```

### 4. Injection Wiring (`instance_messaging.py`)
Where `format_project_context()` is called, pass the store:
```python
# Before:
project_context = format_project_context(project)
# After:
project_context = format_project_context(project, store=manager.project_store)
```

**Important:** Verify all call sites of `format_project_context()` in `instance_messaging.py` and update them.

## Constraints
- Must not break existing project context format (history is additive)
- History section is non-critical — if it fails, injection should still work
- Keep the history section concise (10 entries max, one line each)
- Use same emoji-based formatting as Critical Experience for visual consistency
- The `store` parameter must be optional to maintain backward compatibility

## Deliverables
- [ ] Category registered in `_tool_registry.py`
- [ ] Tools loaded in `instance.py`
- [ ] History section rendered in `format_project_context()`
- [ ] Store passed through from `instance_messaging.py`
- [ ] Existing injection tests still pass
