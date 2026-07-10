# Phase 1: Backend Data Model + Service Layer

## Objective
Add the `SubTask` dataclass, extend `TodoNode` with a `subtasks` field, implement 3 new manager methods (`add_subtask`, `update_subtask`, `remove_subtask`), implement optional status propagation, and evolve the frozen `_to_dict` schema from 6 keys to 7 keys.

## Coupling
- **Depends on**: None (root phase)
- **Coupling type**: — (root)
- **Shared files with other phases**: `daemon/services/todo_manager.py` (consumed by Phase 2 tools and Phase 3 API)
- **Shared APIs/interfaces**: Manager method signatures (`add_subtask`, `update_subtask`, `remove_subtask`), `_to_dict` output schema
- **Why this coupling**: All downstream phases depend on the manager's data model and method contracts

## Context
- The current `TodoNode` dataclass has 6 fields: `id`, `text`, `status`, `comment`, `next_ids`, `index`
- `_to_dict()` returns a frozen 6-key schema documented as a contract across phases
- `MAX_NODES = 200`, `MAX_COMMENT_LENGTH = 1000` are existing limits
- Thread-safe via `threading.Lock`; all state mutations guarded
- `_compute_reminder()` computes graph-aware ready nodes — must NOT be affected by sub-tasks (sub-tasks are within-node detail)

## Design Decisions (Resolved)

### D1: Data Model — `SubTask` dataclass + `subtasks` field on `TodoNode`

```python
@dataclass
class SubTask:
    """A checklist item nested within a TodoNode."""
    id: str          # Format: "s-{uuid.uuid4().hex[:8]}" — prefixed "s-" to distinguish from node IDs
    text: str
    status: str = "pending"  # pending | done  (no "in_progress" — sub-tasks are binary checklists)
```

Add to `TodoNode`:
```python
subtasks: list[SubTask] = field(default_factory=list)
```

**Rationale:** Sub-tasks are simpler than nodes — they have no graph edges, no comments, no index. A binary pending/done status is sufficient (checklist semantics). The `s-` prefix prevents ID collision with node IDs (`n-` prefix).

### D2: Status Propagation — Opt-in, default OFF

When a sub-task is marked `done`:
1. Check if ALL sub-tasks on that node are now `done` — **guard against vacuous truth**: `if auto_complete and node.subtasks and all(st.status == "done" for st in node.subtasks)` (an empty sub-task list must NOT trigger auto-completion)
2. If yes AND the node's status is currently `pending` or `in_progress` (not already `done`):
   - **If `auto_complete=True`**: Set node status to `done`, set `auto_completed=True` in return
   - **If `auto_complete=False`** (default): Return the updated sub-task list but do NOT change node status. `auto_completed=False`.
3. If `auto_complete=True` but NOT all sub-tasks done: no-op, `auto_completed=False`. The tool surfaces feedback: "auto_complete requested but N sub-task(s) remain pending."

The `auto_complete` parameter is passed to `add_subtask` and stored on the node? **No** — store it as a per-call parameter on `update_subtask`:

```python
def update_subtask(self, instance_id, node_id, subtask_id, status, auto_complete=False):
```

**Rationale:** Keeping `auto_complete` as a per-call parameter is simpler than storing a flag on the node. The agent/frontend decides at update-time whether to propagate. This avoids schema complexity and gives maximum flexibility.

### D3: Sub-task status — Binary (pending/done only)

Sub-tasks use only `pending` and `done`. No `in_progress`. They are checklist items, not tracked work items. This simplifies the UI (checkbox, not tri-state) and the agent model.

Normalization: `_normalize_status()` already handles `completed`→`done` etc. For sub-tasks, we reuse it but reject `in_progress` as invalid for sub-task context.

### D4: MAX_SUBTASKS_PER_NODE = 20

Separate limit. Sub-tasks don't count toward `MAX_NODES = 200`. Rationale: 20 sub-tasks per node × 200 nodes = 4000 items max — manageable for in-memory + SSE.

**Enforced uniformly at all 3 entry points:** `add_subtask()`, `create_graph()` (per-node), and `add_node()`.

### D5: `_to_dict()` schema evolution — 6→7 keys

```python
@staticmethod
def _to_dict(node: TodoNode) -> dict[str, Any]:
    return {
        "id": node.id,
        "index": node.index,
        "text": node.text,
        "status": node.status,
        "comment": node.comment,
        "next_ids": list(node.next_ids),
        "subtasks": [
            {"id": st.id, "text": st.text, "status": st.status}
            for st in node.subtasks
        ],
    }
```

The `subtasks` key is always present (empty list `[]` when no sub-tasks). This is backward-compatible because:
- JSON consumers that don't know about `subtasks` simply ignore the extra key
- The 6 existing keys remain unchanged in name, type, and position
- **8 existing tests use `set(item.keys()) == {6-key-set}` assertions and must be updated** to expect the 7-key set (see decisions.md D4 for the full list of affected test files and line numbers)
- **2 existing tests check tool count** (`len(tools) == 6` and exact name list) and must be updated to `== 9`
- These 10 test updates are tracked as a Phase 5 task

### D6: `_compute_reminder()` — No change

Sub-task state does NOT affect graph readiness. The reminder logic operates on node-level status only. A node with all sub-tasks done but node status still `pending` is still "ready" if its predecessors are done. This is the correct behavior — the agent sees the node as pending and the sub-task checklist as detail.

### D7: Sub-task creation in `create` and `create_graph`

- `create(items)`: flat-list path — no sub-tasks (backward compat, all `subtasks=[]`)
- `create_graph(nodes, edges)`: accept optional `subtasks` key in each node spec:
  ```python
  {"id": "setup", "text": "Setup DB", "subtasks": [
      {"text": "Create schema"}, {"text": "Run migration"}
  ]}
  ```
  Sub-task IDs auto-generated if not provided. Sub-task `status` defaults to `pending`.

  **Sub-task spec validation contract:**
  - `subtasks` must be a `list` if present (non-list → `ValueError`)
  - Each spec must be a `dict` with non-empty `text` (missing/empty `text` → `ValueError`)
  - Explicit `id` must be `s-` prefixed (non-`s-` prefix → auto-generate instead; all-numeric → reject)
  - `status` normalizes to `pending`/`done` via `_normalize_subtask_status()` (invalid → `ValueError`)
  - Unknown fields in the spec are silently ignored (forward-compatible)
  - `MAX_SUBTASKS_PER_NODE` enforced per-node (exceeds → `ValueError`)

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add `SubTask` dataclass | New dataclass with `id`, `text`, `status` fields. `id` format `s-{uuid.uuid4().hex[:8]}`. Status limited to `pending`/`done`. | `daemon/services/todo_manager.py` |
| 2 | Add `subtasks` field to `TodoNode` | `subtasks: list[SubTask] = field(default_factory=list)`. Default empty list. | `daemon/services/todo_manager.py` |
| 3 | Add `MAX_SUBTASKS_PER_NODE = 20` constant | Class-level constant on `TodoGraphManager`. Documented. | `daemon/services/todo_manager.py` |
| 4 | Add `_generate_subtask_id()` static method | Returns `f"s-{uuid.uuid4().hex[:8]}"`. Mirrors `_generate_id()` pattern. | `daemon/services/todo_manager.py` |
| 5 | Implement `add_subtask(instance_id, node_id, text)` | Creates a new `SubTask` with `status="pending"`, appends to node's `subtasks` list. Enforces `MAX_SUBTASKS_PER_NODE`. Returns `{"todos": [...], "reminder": str}` (uniform shape — see D12). Thread-safe. | `daemon/services/todo_manager.py` |
| 6 | Implement `update_subtask(instance_id, node_id, subtask_id, status, auto_complete=False)` | Normalizes status (must be `pending` or `done` — reject `in_progress`). Updates sub-task status. If `auto_complete=True` AND node has sub-tasks AND all sub-tasks are `done` AND node status is not `done`, sets node status to `done` and sets `auto_completed=True` (vacuous-truth guard: empty sub-task list does NOT trigger). Returns `{"todos": [...], "reminder": str, "auto_completed": bool}`. | `daemon/services/todo_manager.py` |
| 7 | Implement `remove_subtask(instance_id, node_id, subtask_id)` | Removes sub-task from node's list by ID. Returns `{"todos": [...], "reminder": str}` (uniform shape). Thread-safe. | `daemon/services/todo_manager.py` |
| 8 | Evolve `_to_dict()` to 7-key schema | Add `subtasks` key with list of `{"id", "text", "status"}` dicts. Update docstring. | `daemon/services/todo_manager.py` |
| 9 | Update `create_graph()` to accept subtasks in node specs | Parse optional `subtasks` key in each node dict. Validate per D7 contract (list type, non-empty text, `s-` prefix or auto-gen IDs, status normalization, unknown fields ignored). Enforce `MAX_SUBTASKS_PER_NODE` per-node. | `daemon/services/todo_manager.py` |
| 10 | Update `create()` to initialize empty subtasks | Flat-list path: all nodes get `subtasks=[]` (already the default, but be explicit). | `daemon/services/todo_manager.py` |
| 11 | Add `_normalize_subtask_status()` helper | Like `_normalize_status()` but rejects `in_progress` aliases. Returns `pending` or `done` only, or `None` if invalid. | `daemon/services/todo_manager.py` |
| 12 | Update `add_node()` to accept optional subtasks | `subtasks: list[dict] | None = None` — same validation contract as `create_graph` (D7). Enforce `MAX_SUBTASKS_PER_NODE`. Auto-generate `s-` IDs. Default status `pending`. | `daemon/services/todo_manager.py` |
| 13 | Update module + class docstrings | Document the 7-key schema, sub-task semantics, `MAX_SUBTASKS_PER_NODE`, status propagation behavior. | `daemon/services/todo_manager.py` |
| 14 | Audit and update frozen-contract docstrings | Update all 6 locations that assert "exactly six keys" / "frozen" / "Do NOT change": `todo_manager.py:22-28`, `:160-162`, `:476-479`, `:938-958`; `live_event_hub.py:351-370`; `instances.py:428-436`. Evolve wording to "frozen seven keys (subtasks added)." | `daemon/services/todo_manager.py`, `daemon/services/live_event_hub.py`, `daemon/routers/instances.py` |

## Key Files
- `daemon/services/todo_manager.py` — All changes in this phase. ~974 lines currently, estimated +250-300 lines.

## Constraints
- Thread safety: all new methods must acquire `self._lock` before reading/writing node state
- `_compute_reminder()` must NOT be modified — graph readiness is node-level only
- Sub-task status is binary (`pending`/`done`) — `in_progress` is rejected
- `_to_dict()` must always include `subtasks` key (even if empty list) — no conditional omission
- Existing method signatures must not change (add optional params only, at the end)
- `MAX_SUBTASKS_PER_NODE` enforced at ALL THREE entry points: `add_subtask`, `create_graph` (per-node), and `add_node`
- Status propagation must guard against vacuous truth: `if auto_complete and node.subtasks and all(...)` — empty sub-task list does NOT trigger auto-completion
- All 3 manager methods (`add_subtask`, `update_subtask`, `remove_subtask`) return `{"todos": [...], "reminder": str}` (update_subtask also includes `"auto_completed": bool`) — uniform shape per D12

## Deliverables
- [ ] `SubTask` dataclass defined
- [ ] `TodoNode.subtasks` field added
- [ ] `MAX_SUBTASKS_PER_NODE = 20` constant
- [ ] `add_subtask()`, `update_subtask()`, `remove_subtask()` methods implemented
- [ ] `_to_dict()` returns 7-key schema with `subtasks` list
- [ ] `create_graph()` accepts optional `subtasks` in node specs
- [ ] Status propagation logic (opt-in via `auto_complete` parameter)
- [ ] All existing tests pass after updating 10 assertion tests (8 schema-key-set + 2 tool-count) — see Phase 5
- [ ] Frozen-contract docstrings audited and updated (6 locations)
- [ ] New unit tests for sub-task CRUD, propagation, limits, edge cases (~25-30 tests)

## Test Strategy (Phase 1)

New test class `TestTodoSubtasks` in `tests/test_todo_manager.py`:

| Test | Description |
|------|-------------|
| `test_add_subtask_creates_pending_subtask` | Add sub-task, verify it appears in node dict with `status="pending"` |
| `test_add_subtask_generates_s_prefixed_id` | Sub-task ID starts with `s-` |
| `test_add_subtask_max_limit` | Adding 21st sub-task raises `ValueError` |
| `test_add_subtask_to_nonexistent_node` | Returns `None` or raises `ValueError` |
| `test_add_subtask_to_nonexistent_instance` | Returns `None` or raises `ValueError` |
| `test_update_subtask_to_done` | Mark sub-task done, verify status in node dict |
| `test_update_subtask_auto_complete_propagates` | All sub-tasks done + `auto_complete=True` → node status becomes `done`, `auto_completed=True` in return |
| `test_update_subtask_auto_complete_off_no_propagation` | All sub-tasks done + `auto_complete=False` → node status unchanged, `auto_completed=False` |
| `test_update_subtask_auto_complete_skips_if_already_done` | Node already `done` → no change, no error, `auto_completed=False` |
| `test_update_subtask_auto_complete_not_all_done` | `auto_complete=True` but 1 sub-task still pending → no propagation, `auto_completed=False` |
| `test_update_subtask_auto_complete_zero_subtasks` | `auto_complete=True` + node has 0 sub-tasks → no propagation (vacuous-truth guard), `auto_completed=False` |
| `test_update_subtask_rejects_in_progress` | `in_progress` status → `None` return (invalid) |
| `test_update_subtask_nonexistent_subtask` | Invalid `subtask_id` → `None` |
| `test_update_subtask_returns_todos_and_reminder` | Return shape matches `update()` contract |
| `test_remove_subtask_removes_by_id` | Remove sub-task, verify it's gone from list |
| `test_remove_subtask_nonexistent` | Invalid `subtask_id` → `None` |
| `test_remove_subtask_preserves_others` | Remove one, verify others remain |
| `test_to_dict_includes_subtasks_key` | `_to_dict()` output has `subtasks` key (7 keys total) |
| `test_to_dict_subtasks_empty_list_default` | Node with no sub-tasks → `subtasks: []` |
| `test_to_dict_subtasks_serialized_correctly` | Sub-tasks serialized as `{"id", "text", "status"}` dicts |
| `test_create_graph_with_subtasks` | `create_graph` accepts `subtasks` in node specs |
| `test_create_graph_subtasks_auto_id` | Sub-tasks without explicit ID get auto-generated `s-` IDs |
| `test_create_flat_list_no_subtasks` | `create(items)` → all nodes have `subtasks: []` |
| `test_add_node_with_subtasks` | `add_node` accepts optional `subtasks` parameter |
| `test_subtask_ids_unique_within_node` | Two sub-tasks on same node get different IDs |
| `test_subtask_status_accepts_aliases` | `completed` → `done`, `cancelled` → `pending` |
| `test_create_graph_malformed_subtasks_not_list` | `subtasks: "not a list"` → `ValueError` |
| `test_create_graph_malformed_subtask_missing_text` | `subtasks: [{"id": "s-1"}]` (no text) → `ValueError` |
| `test_create_graph_max_subtasks_per_node` | Node spec with 21 sub-tasks → `ValueError` |
| `test_add_node_max_subtasks_exceeded` | `add_node` with 21 sub-tasks → `ValueError` |
| `test_subtask_id_collision_within_node` | Two sub-tasks with same explicit ID → `ValueError` or auto-generate |
| `test_concurrent_update_subtask_same_id` | Thread safety: two threads update same sub-task simultaneously |
| `test_backward_compat_existing_tests_pass` | Run full existing test suite after updating 10 assertions — 0 regressions |
