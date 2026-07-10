# Architecture Decisions: Todo Node Sub-Tasks

## Decision Log

### D1: SubTask Dataclass — Flat Structure (Not Nested Graph)

**Decision:** Sub-tasks are a flat `list[SubTask]` on `TodoNode`, not graph nodes themselves.

**Rationale:** Sub-tasks are checklist items, not workflow steps. They don't have dependencies, comments, or graph edges. Making them graph nodes would:
- Complicate cycle detection (sub-task "edges" within a node?)
- Blur the distinction between "task breakdown" and "task dependency"
- Inflate the node count toward `MAX_NODES`
- Require frontend graph layout changes for nested structures

A flat list is simpler, matches user mental models (checklist within a task), and keeps the graph structure clean.

**Alternatives considered:**
- ❌ Sub-tasks as graph nodes with auto-edges — rejected (over-engineered, confusing)
- ❌ Nested graph (sub-graph within node) — rejected (layout nightmare, no user value)

---

### D2: Sub-Task Status — Binary (pending/done), No in_progress

**Decision:** Sub-tasks have only `pending` and `done` statuses. `in_progress` is rejected.

**Rationale:** Sub-tasks are checklists — you either haven't done them or you have. Adding `in_progress` would:
- Require tri-state checkboxes in the UI (confusing for simple checklists)
- Overlap with the parent node's `in_progress` status (redundant)
- Complicate status propagation logic

The parent node tracks overall progress (`in_progress`); sub-tasks track completion of sub-items (`done`/`pending`).

---

### D3: Status Propagation — Opt-in via `auto_complete` Parameter

**Decision:** When all sub-tasks on a node are `done`, the parent node's status is NOT automatically changed unless `auto_complete=True` is passed to `update_subtask()`.

**Rationale:**
- **Default OFF** prevents surprising behavior — an agent might want all sub-tasks done but still keep the node `in_progress` while doing final verification
- **Opt-in** gives the agent explicit control: "I'm done with all sub-items, mark the parent done too"
- The `auto_complete` flag is per-call, not stored on the node — simpler schema, no migration needed

**Propagation rules:**
1. `auto_complete=True` + node has sub-tasks + all sub-tasks `done` + node status != `done` → set node to `done`, set `auto_completed=True` in return
2. `auto_complete=True` + node has sub-tasks + all sub-tasks `done` + node status == `done` → no-op (already done), `auto_completed=False`
3. `auto_complete=False` (default) → never change node status, `auto_completed=False`
4. `auto_complete=True` + NOT all sub-tasks `done` → no-op (condition not met), `auto_completed=False`. **The return includes `auto_completed: False` so the caller can distinguish "propagation skipped" from "propagation applied."** The tool surfaces this: "Sub-task marked done. auto_complete requested but N sub-task(s) remain pending."
5. `auto_complete=True` + node has **zero** sub-tasks → no-op (vacuous-truth guard). `all([])` returns `True` in Python, so the guard must check `if auto_complete and node.subtasks and all(st.status == "done" for st in node.subtasks)`. An empty sub-task list must NOT trigger auto-completion — the parent has no checklist to complete.

**Frontend behavior:** The frontend always sends `auto_complete: false`. This is an agent-only feature — agents can pass it via tools when they want propagation.

**Return shape:** `update_subtask()` returns `{"todos": [...], "reminder": str, "auto_completed": bool}`. The `auto_completed` flag is `True` only when rule #1 fired (parent was auto-marked done). This lets the tool layer give the agent clear feedback: "Parent node auto-completed." or "auto_complete requested but N sub-task(s) remain pending."

**Alternatives considered:**
- ❌ Always propagate — rejected (surprising, removes agent control)
- ❌ Store `auto_complete` flag on node — rejected (schema complexity, migration)
- ❌ Propagate in reverse (un-checking a sub-task sets parent back to `in_progress`) — rejected (too aggressive, parent might be done for other reasons)

---

### D4: Frozen Schema Evolution — 6→7 Keys (Additive)

**Decision:** `_to_dict()` evolves from 6 keys to 7 keys by adding `subtasks`. The existing 6 keys remain unchanged.

**Rationale:** This is an additive change — existing consumers that don't know about `subtasks` simply ignore the extra key. The frozen schema contract is preserved for the 6 existing keys; `subtasks` is a new addition documented as part of the evolution.

**Backward compatibility verification:**
- Existing tests check specific keys (`item["status"]`, `item["text"]`), not key count — **EXCEPT** 8 tests that use `set(item.keys()) == {six-key-set}` assertions. These 8 tests must be updated to expect the 7-key set:
  1. `tests/test_todo_manager.py:399`
  2. `tests/test_todo_manager.py:1069` — `test_to_dict_has_six_keys` (rename to `test_to_dict_has_seven_keys`)
  3. `tests/test_todo_sse.py:324-331`
  4. `tests/test_todo_sse.py:387-394`
  5. `tests/test_todo_sse.py:434-441`
  6. `tests/test_todo_sse.py:481-488`
  7. `tests/unit/routers/test_todo_api.py:158-160`
  8. `tests/test_todo_comment_edge_cases.py:187`
- Additionally, 2 tests check tool count and must be updated:
  9. `tests/test_todo_tools.py:75` — `assert len(tools) == 6` → `== 9`
  10. `tests/test_todo_tools.py:84-91` — exact tool name list equality → add 3 new names
- SSE handler in frontend replaces the entire `todos` signal — extra keys are harmless
- API responses are JSON arrays — extra keys are ignored by clients that don't use them

**Frozen contract docstring audit (6 locations must be updated):**
The following docstrings explicitly assert "exactly six keys" / "frozen" / "Do NOT change." All must be updated to reflect the 7-key schema:
1. `daemon/services/todo_manager.py:22-28` — module docstring "exactly six keys"
2. `daemon/services/todo_manager.py:160-162` — `create()` docstring "six keys each"
3. `daemon/services/todo_manager.py:476-479` — `get_all()` docstring "six keys"
4. `daemon/services/todo_manager.py:938-958` — `_to_dict()` docstring "six keys, all required"
5. `daemon/services/live_event_hub.py:351-370` — `stream_todo_update()` docstring "exactly six keys"
6. `daemon/routers/instances.py:428-436` — `get_instance_todos()` docstring "exactly six keys"

Each should evolve from "frozen six keys" to "frozen seven keys (subtasks added in sub-task phase)." The schema is still frozen — it's just frozen at 7 keys now.

**Defensive measures:**
- Frontend SSE handler defaults `subtasks` to `[]` if missing (handles old payloads during rollout)
- `_format_graph()` uses `node.get("subtasks", [])` (defensive)
- New manager methods check for `subtasks` attribute existence (for any code that constructs `TodoNode` manually)

---

### D5: Sub-Task ID Format — `s-` Prefix

**Decision:** Sub-task IDs use `s-{uuid.uuid4().hex[:8]}` format.

**Rationale:**
- `s-` prefix distinguishes sub-tasks from nodes (`n-` prefix) in logs, debugging, and API paths
- Non-numeric prefix prevents collision with the API's `isdigit()` backward-compat path (same rationale as node IDs)
- 8-hex-char suffix gives ~4 billion possible IDs — collision risk for <20 sub-tasks per node is negligible

---

### D6: MAX_SUBTASKS_PER_NODE = 20 (Separate from MAX_NODES)

**Decision:** Sub-tasks have their own limit (`MAX_SUBTASKS_PER_NODE = 20`), separate from `MAX_NODES = 200`.

**Rationale:**
- Sub-tasks are lightweight (3 fields vs 6 fields) — less memory per item
- 20 sub-tasks × 200 nodes = 4000 items max — manageable for in-memory + SSE
- Counting sub-tasks toward `MAX_NODES` would artificially limit graph size (a node with 20 sub-tasks would "consume" 20 of the 200 slots)
- Separate limits allow independent tuning

**Enforcement points (all three entry points):**
The limit is enforced uniformly at every code path that creates sub-tasks:
1. `add_subtask()` — raises `ValueError` if `len(node.subtasks) >= MAX_SUBTASKS_PER_NODE`
2. `create_graph()` — raises `ValueError` if any node spec's `subtasks` list exceeds `MAX_SUBTASKS_PER_NODE`
3. `add_node()` — raises `ValueError` if the `subtasks` parameter exceeds `MAX_SUBTASKS_PER_NODE`

---

### D7: `_compute_reminder()` — No Change (Sub-Tasks Don't Affect Graph Readiness)

**Decision:** The reminder logic operates on node-level status only. Sub-task state does NOT affect graph readiness calculations.

**Rationale:**
- Graph readiness answers: "Which nodes can I start working on?" — this is about dependencies between nodes, not within them
- A node with all sub-tasks done but node status still `pending` is still "ready" (its predecessors are done)
- The agent sees the node as pending and the sub-task checklist as detail — correct behavior
- Mixing sub-task state into readiness would create confusing reminders ("Node X is blocked because its sub-tasks aren't done" — but the node itself is ready to start)

**What DOES change:** When `auto_complete=True` propagates and sets a node to `done`, the next `todo_update` call's reminder will naturally reflect the new node status (since `_compute_reminder` runs after every status change). This is correct and requires no modification to `_compute_reminder`.

---

### D8: Frontend Graph Mode — Popup, Not Inline Expansion

**Decision:** In graph mode, sub-tasks are shown in a popup (like the comment popup), not as inline expansion within the `foreignObject`. A count badge (`2/3`) on the node card indicates sub-task presence.

**Rationale:**
- `foreignObject` has a fixed `NODE_HEIGHT = 48px` — expanding it would break `computeLayout()` positioning and edge rendering
- Dynamic height would require recalculating all node positions on expand/collapse — expensive and visually jarring
- The popup pattern is already established (comment popup) and works well
- Linear mode can use inline expansion (no SVG constraints)

**Alternatives considered:**
- ❌ Dynamic `foreignObject` height — rejected (breaks layout, edge misalignment)
- ❌ Sub-tasks only in linear mode — rejected (graph users need sub-tasks too)
- ❌ Separate sub-task panel below the graph — rejected (context loss, poor UX)

---

### D9: HTTP Method — PATCH for Sub-Task Status Update

**Decision:** Use `PATCH /{node_id}/subtasks/{subtask_id}` for status updates, not `PUT` or `POST`.

**Rationale:**
- `PATCH` is semantically correct for partial updates (updating only `status`, not the whole resource)
- `PUT` implies full replacement of the resource
- `POST` implies creation
- RESTful convention: `PATCH` for partial field updates

---

### D10: Route Ordering — Sub-Task Routes Before Comment Catch-All

**Decision:** Declare all `/subtasks` routes before the `POST /{node_id}/comment` catch-all route.

**Rationale:** FastAPI matches routes in declaration order. The `/{node_id}` path parameter in the comment route would capture the literal string `subtasks` if declared first. By declaring `/subtasks` routes before the comment route, FastAPI matches the literal segment first.

This is the same pattern already used for `/edges` routes (declared before `/{node_id}/comment`).

---

### D11: `create_graph()` Accepts Optional Sub-Tasks in Node Specs

**Decision:** The `create_graph()` method accepts an optional `subtasks` key in each node spec dict:
```python
{"id": "setup", "text": "Setup DB", "subtasks": [
    {"text": "Create schema"},
    {"text": "Run migration"}
]}
```

Sub-task IDs are auto-generated if not provided. Sub-task `status` defaults to `pending`.

**Sub-task spec validation contract:**
- `subtasks` must be a `list` if present (non-list → `ValueError`)
- Each spec must be a `dict` with non-empty `text` (missing/empty `text` → `ValueError`)
- Explicit `id` must be `s-` prefixed (non-`s-` prefix → auto-generate instead; all-numeric → reject)
- `status` normalizes to `pending`/`done` via `_normalize_subtask_status()` (invalid → `ValueError`)
- Unknown fields in the spec are silently ignored (forward-compatible)
- `MAX_SUBTASKS_PER_NODE` enforced per-node (exceeds → `ValueError`)

**Rationale:**
- Allows agents to create a graph with sub-tasks in a single `todo_create` call (no need for follow-up `todo_add_subtask` calls)
- The `subtasks` key is optional — existing node specs without it work unchanged
- Auto-generated IDs simplify the agent's task (no need to invent `s-` prefixed IDs)

**`add_node()` sub-task parameter:** Same contract applies. `add_node(text, next_ids=None, subtasks=None)`:
- `subtasks: list[dict] | None = None` — same validation as `create_graph`
- Enforces `MAX_SUBTASKS_PER_NODE`
- Auto-generates `s-` IDs for specs without explicit `id`
- Default status `pending`

**What about `create(items)` (flat list)?** No sub-task support — the flat-list path is for simple sequential checklists. Agents who want sub-tasks should use the graph mode.

---

### D12: Uniform API Response Shape — All Sub-Task Endpoints Return `{"todos": [...], "reminder": str}`

**Decision:** All 3 new API endpoints (`POST`, `PATCH`, `DELETE /subtasks`) return the same shape: `{"todos": [...], "reminder": str}`.

**Rationale:**
- Matches the `update()` / `update_subtask()` manager method return shape
- The frontend replaces the entire `todos` signal on every SSE update anyway — a consistent response shape simplifies the API client code
- `reminder` is useful context for API consumers (not just agents) — e.g., "All items completed! ✅" after deleting the last sub-task on the last pending node
- Inconsistent shapes (bare node dict vs. `{"todos", "reminder"}`) would require frontend branching logic per endpoint

**Implementation:** `add_subtask()` and `remove_subtask()` manager methods return `{"todos": [...], "reminder": str}` (not bare node dicts). The `reminder` is computed via `_compute_reminder()` after the mutation, same as `update()`.

**Alternatives considered:**
- ❌ POST returns bare node dict, PATCH returns `{todos, reminder}`, DELETE returns bare node dict — rejected (inconsistent, frontend must branch)
- ❌ All return bare node dicts — rejected (loses reminder context, inconsistent with `update()`)
