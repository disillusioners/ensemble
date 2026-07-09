# Phase 1: Backend — TodoManager + Tools

## Objective
Create a TodoManager service for per-instance in-memory todo state, implement 4 todo tools (`todo_create`, `todo_update`, `todo_list`, `todo_clear`) following the existing factory pattern, and register the "todo" tool category in `INNATE_SKILL_TOOL_CATEGORIES`.

## Coupling
- **Depends on**: None (root phase)
- **Coupling type**: —
- **Shared files with other phases**: `daemon/tools/instance.py` (import + assembly point, also modified in Phase 2)
- **Shared APIs/interfaces**: TodoManager class interface (consumed by Phase 2 SSE integration)
- **Why this coupling**: Phase 2 wires SSE emission into the tools created here. The TodoManager holds the state that SSE broadcasts.

## Context
- All tool factories follow: `create_x_tools(manager, current_instance_id) -> list`
- Tools use `@register_tool_category("category")` + `@tool` decorators
- Tools return strings, never raise exceptions
- Reference implementation: `daemon/tools/chart_tools.py` (cleanest example)
- Assembly point: `create_instance_tools()` at `daemon/tools/instance.py:536-1036`

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create TodoManager service | Per-instance in-memory todo state. Dict keyed by `instance_id`. Thread-safe with `asyncio.Lock`. Methods: `create_list(instance_id, items)`, `update_item(instance_id, index, status)`, `get_list(instance_id)`, `clear_list(instance_id)`. Returns structured `TodoItem` dataclass/dict with `{index, text, status}`. | `daemon/services/todo_manager.py` (NEW) |
| 2 | Register TodoManager on InstanceManager | Add `self._todo_manager = TodoManager()` in `InstanceManager.__init__` so tools can access it via `manager._todo_manager`. | `daemon/manager.py` |
| 3 | Create todo tools module | `create_todo_tools(manager, current_instance_id) -> list`. 4 tools: `todo_create(items: list[str])`, `todo_update(index: int, status: str)`, `todo_list()`, `todo_clear()`. All use `@register_tool_category("todo")` + `@tool`. `todo_update` returns reminder of next pending item. Tools delegate to `manager._todo_manager`. | `daemon/tools/todo_tools.py` (NEW) |
| 4 | Add "todo" to INNATE_SKILL_TOOL_CATEGORIES | Add `"todo": ["todo"]` to the mapping dict at line ~52. | `daemon/tools/instance.py` |
| 5 | Import + wire into create_instance_tools | Add `from .todo_tools import create_todo_tools` to imports. Add `todo_tool_list = create_todo_tools(manager, current_instance_id)` + `tools.extend(todo_tool_list)` in the assembly section (after chart tools, before DB tools — around line ~974). | `daemon/tools/instance.py` |
| 6 | Write unit tests | Test TodoManager CRUD operations, edge cases (out-of-bounds index, empty list, invalid status), reminder logic for `todo_update`. | `tests/test_todo_manager.py` (NEW) |

## Key Files
- `daemon/services/todo_manager.py` (NEW) — TodoManager service, per-instance todo state
- `daemon/tools/todo_tools.py` (NEW) — 4 todo tool functions + factory
- `daemon/tools/instance.py` — Import, `INNATE_SKILL_TOOL_CATEGORIES` entry, assembly wiring
- `daemon/manager.py` — TodoManager singleton registration
- `tests/test_todo_manager.py` (NEW) — Unit tests

## Detailed Design

### TodoManager (`daemon/services/todo_manager.py`)

```python
class TodoItem:
    """Single todo item."""
    index: int
    text: str
    status: str  # "pending" | "in_progress" | "done"

class TodoManager:
    """Per-instance in-memory todo list manager."""
    
    def __init__(self):
        self._todos: dict[str, list[TodoItem]] = {}  # instance_id -> items
        self._lock = asyncio.Lock()
    
    async def create_list(self, instance_id: str, items: list[str]) -> list[dict]:
        """Replace/create full todo list. All items start as 'pending'."""
    
    async def update_item(self, instance_id: str, index: int, status: str) -> list[dict] | None:
        """Update item status. Returns full list or None if invalid index."""
    
    async def get_list(self, instance_id: str) -> list[dict]:
        """Get current todo list (empty list if none)."""
    
    async def clear_list(self, instance_id: str) -> None:
        """Clear entire todo list for instance."""
```

### Tool Return Format (strings for LLM)

```
todo_create → "✅ Todo list created with {n} items:\n[0] ○ item text\n[1] ○ item text\n..."
todo_update → "📋 Updated item {index} → {status}.\n\n" + full list + "\n\n👉 Next: [{next_index}] {next_text}" (if any pending)
todo_list   → "📋 Current todo list:\n[0] ● done item\n[1] ◐ in progress\n[2] ○ pending" (or "No todo items.")
todo_clear  → "🗑️ Todo list cleared."
```

### Status Indicators (consistent across tool output + frontend)
- `pending` → ○
- `in_progress` → ◐
- `done` → ●

## Constraints
- Todos are ephemeral — in-memory only, no DB persistence
- Tools return strings (never raise exceptions — return error message strings)
- Valid statuses: `pending`, `in_progress`, `done` (validate input, accept common aliases like `completed`)
- TodoManager must be async (uses `asyncio.Lock` for concurrent tool access)
- Instance-scoped: each instance_id has independent todo state

## Deliverables
- [ ] `daemon/services/todo_manager.py` — TodoManager with full CRUD
- [ ] `daemon/tools/todo_tools.py` — 4 tools with `@register_tool_category("todo")`
- [ ] `daemon/tools/instance.py` — `"todo"` in `INNATE_SKILL_TOOL_CATEGORIES`, import + assembly wiring
- [ ] `daemon/manager.py` — TodoManager singleton
- [ ] `tests/test_todo_manager.py` — Unit tests passing
