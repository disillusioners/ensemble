# Phase 2: Agent Tools + Skill Docs

## Objective
Add 3 new agent tools (`todo_add_subtask`, `todo_update_subtask`, `todo_remove_subtask`) to the todo tool surface, update `_format_graph()` to render sub-tasks as an indented checklist, update the tool category docstring, and update the skill documentation.

## Coupling
- **Depends on**: Phase 1 (manager methods must exist)
- **Coupling type**: tight — tools directly call `manager._todo_manager.add_subtask()`, `update_subtask()`, `remove_subtask()`
- **Shared files with other phases**: None (tools layer is independent of API layer)
- **Shared APIs/interfaces**: Manager method signatures from Phase 1
- **Why this coupling**: Tools are thin wrappers around manager methods; they cannot be implemented until the methods exist with correct signatures

## Context
- Current tool factory: `create_todo_tools(manager, current_instance_id, live_event_hub)` returns 6 tools
- All tools follow the same pattern: call manager method → emit SSE via `_emit_update()` → return formatted string
- `_format_graph()` renders nodes as linear list or branching tree — needs sub-task rendering
- `_STATUS_ICONS` dict maps statuses to Unicode symbols
- Skill docs at `agents/_prompt_system/innate-skills/todo/skill.md` document all tools

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Implement `todo_add_subtask(node_id, text)` tool | Calls `manager._todo_manager.add_subtask(current_instance_id, node_id, text)`. Emits SSE. Returns formatted graph with confirmation. Error handling: node not found, max sub-tasks exceeded. | `daemon/tools/todo_tools.py` |
| 2 | Implement `todo_update_subtask(node_id, subtask_id, status, auto_complete=False)` tool | Calls `manager._todo_manager.update_subtask(...)`. Emits SSE. Returns formatted graph + reminder (matches `todo_update` return shape). Error handling: invalid status, sub-task not found. | `daemon/tools/todo_tools.py` |
| 3 | Implement `todo_remove_subtask(node_id, subtask_id)` tool | Calls `manager._todo_manager.remove_subtask(...)`. Emits SSE. Returns formatted graph. Error handling: sub-task not found. | `daemon/tools/todo_tools.py` |
| 4 | Update `create_todo_tools()` return list | Add 3 new tools to the returned list (now 9 tools total). | `daemon/tools/todo_tools.py` |
| 5 | Update `_format_graph()` to render sub-tasks | After each node line, if node has sub-tasks, render them as indented checklist: `    ☐ sub-task text` (pending) or `    ☑ sub-task text` (done). Apply in both linear and branching modes. **Verbosity control:** truncate to 5 sub-tasks per node by default with `+{N} more` suffix; `verbose` param shows all. | `daemon/tools/todo_tools.py` |
| 6 | Add sub-task status icons | Add `_SUBTASK_ICONS = {"pending": "☐", "done": "☑"}` constant. Distinct from node icons (○◐●). | `daemon/tools/todo_tools.py` |
| 7 | Update `CATEGORY_DOC` docstring | Document 9 tools (was 6). Add sub-task tools to the inventory table. Add example showing sub-task usage. | `daemon/tools/todo_tools.py` |
| 8 | Update `todo_create` tool docstring | Document that `nodes` specs can include optional `subtasks` key. | `daemon/tools/todo_tools.py` |
| 9 | Update skill docs | **Full revision** of `skill.md`: update tool inventory table to 9 tools, add "Sub-Tasks" section with examples, mention `auto_complete` parameter and its feedback behavior, note that reverse-propagation (un-checking sub-task → parent back to `in_progress`) is NOT supported, document `verbose` param on `todo_list`. | `agents/_prompt_system/innate-skills/todo/skill.md` |
| 10 | Add `verbose` parameter to `todo_list` | `todo_list(verbose: bool = False)` — controls sub-task rendering depth in `_format_graph()`. Default `False` truncates to 5 per node. | `daemon/tools/todo_tools.py` |

## Key Files
- `daemon/tools/todo_tools.py` — 575 lines currently, estimated +200-250 lines
- `agents/_prompt_system/innate-skills/todo/skill.md` — 53 lines currently, estimated +30-40 lines

## Tool Specifications

### `todo_add_subtask(node_id: str, text: str) -> str`

```python
@register_tool_category("todo")
@tool
async def todo_add_subtask(node_id: str, text: str) -> str:
    """Add a sub-task (checklist item) to a todo node.

    Sub-tasks are binary (pending/done) checklist items nested within
    a graph node. They don't participate in the graph structure — they
    are detail items for breaking down a node's work.

    Args:
        node_id: The parent node's stable ID (e.g. "n-a1b2c3d4").
        text: The sub-task description.

    Returns:
        Formatted graph with the new sub-task visible, or ERROR: string.
    """
```

### `todo_update_subtask(node_id: str, subtask_id: str, status: str, auto_complete: bool = False) -> str`

```python
@register_tool_category("todo")
@tool
async def todo_update_subtask(
    node_id: str,
    subtask_id: str,
    status: str,
    auto_complete: bool = False,
) -> str:
    """Update a sub-task's status (pending or done).

    When auto_complete=True and all sub-tasks on the node are done,
    the parent node's status is automatically set to "done".

    Args:
        node_id: The parent node's stable ID.
        subtask_id: The sub-task's ID (e.g. "s-a1b2c3d4").
        status: "pending" or "done" (aliases: "completed" → "done").
        auto_complete: If True, auto-mark parent done when all sub-tasks done.

    Returns:
        Formatted graph + reminder, or ERROR: string. When auto_complete=True
        but not all sub-tasks are done, the return includes a note:
        "auto_complete requested but N sub-task(s) remain pending."
    """
```

### `todo_remove_subtask(node_id: str, subtask_id: str) -> str`

```python
@register_tool_category("todo")
@tool
async def todo_remove_subtask(node_id: str, subtask_id: str) -> str:
    """Remove a sub-task from a todo node.

    Args:
        node_id: The parent node's stable ID.
        subtask_id: The sub-task's ID to remove.

    Returns:
        Formatted graph, or ERROR: string.
    """
```

## `_format_graph()` Sub-Task Rendering

### Linear mode (current):
```
[0] ○ Setup DB
[1] ◐ Build API
[2] ● Write tests
```

### Linear mode (with sub-tasks):
```
[0] ○ Setup DB
    ☐ Create schema
    ☑ Run migration
[1] ◐ Build API
[2] ● Write tests
```

### Branching mode (with sub-tasks):
```
[0] ○ Setup DB
    ☐ Create schema
    ☑ Run migration
  └→ [1] ◐ Build API
       └→ [2] ● Write tests
  └→ [3] ○ Deploy
```

**Implementation:** After appending each node line, check if `node.get("subtasks")` is non-empty. If so, append each sub-task as an indented line using the node's current indent + 4 spaces.

**Verbosity control (Warning 11):** A graph with 200 nodes × 20 sub-tasks = 4,200 lines would consume excessive agent context. Add a `verbose: bool = False` parameter to `todo_list`:
- `verbose=False` (default): Show at most 5 sub-tasks per node. If more, append `    +{N} more` after the 5th.
- `verbose=True`: Show all sub-tasks.
- `todo_update_subtask` and other mutation tools always use `verbose=False` in their formatted output (the agent already knows what it just changed).

## Skill Docs Update

Add to the tool inventory table:

| Tool | Purpose | Usage |
|------|---------|-------|
| `todo_add_subtask(node_id, text)` | Add a checklist item to a node | Break down a node into smaller steps |
| `todo_update_subtask(node_id, subtask_id, status)` | Check/uncheck a sub-task | Track sub-task progress |
| `todo_remove_subtask(node_id, subtask_id)` | Remove a sub-task | Clean up or correct mistakes |

Add "Sub-Tasks" section:

```markdown
## Sub-Tasks

Sub-tasks are lightweight checklist items nested within a todo node. They
don't participate in the graph structure — they're for breaking a node's
work into smaller checkable steps.

- Sub-task status is binary: `pending` (☐) or `done` (☑) — no `in_progress`.
- Set `auto_complete=True` on `todo_update_subtask` to auto-mark the parent
  node as done when all its sub-tasks are completed.
- Sub-tasks are rendered as an indented checklist under their parent node.

```python
# Add sub-tasks to a node
todo_add_subtask(node_id="n-a1b2c3d4", text="Create schema")
todo_add_subtask(node_id="n-a1b2c3d4", text="Run migration")

# Check off a sub-task
todo_update_subtask(node_id="n-a1b2c3d4", subtask_id="s-e5f6g7h8", status="done")

# Auto-complete parent when all sub-tasks done
todo_update_subtask(node_id="n-a1b2c3d4", subtask_id="s-a1b2c3d4", status="done", auto_complete=True)
```
```

## Constraints
- All 3 new tools must follow the existing pattern: `@register_tool_category("todo")` + `@tool` decorator
- SSE emission via `_emit_update()` helper on all mutations
- Error handling returns `ERROR:` strings (never raises to the agent)
- `_format_graph()` must handle nodes without `subtasks` key gracefully (defensive: `node.get("subtasks", [])`)
- Tool docstrings must include `_full_doc_` extended documentation (matching existing pattern)

## Deliverables
- [ ] 3 new tools implemented and registered
- [ ] `_format_graph()` renders sub-tasks in both linear and branching modes
- [ ] `_SUBTASK_ICONS` constant added
- [ ] `CATEGORY_DOC` updated (9 tools)
- [ ] `todo_create` docstring updated (subtasks in node specs)
- [ ] Skill docs updated with sub-task section
- [ ] `create_todo_tools()` returns 9 tools
- [ ] All existing tool tests pass after updating 2 tool-count assertions (`len(tools) == 6` → `== 9`; exact name list → add 3 names)
- [ ] New tool tests (~10-12 tests)
