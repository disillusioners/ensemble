# Todo Skill

Track multi-step workflows with a todo graph (DAG). Plan work, track progress, and mark items complete. Tools are split into **two sets** by planning shape — pick the prefix that matches your intent instead of one overloaded call:

- **`todo_list_*`** — a **flat list** for simple, strictly sequential work (`A → B → C` auto-chained), addressed by insertion-order `index`.
- **`todo_graph_*`** — an **explicit graph** for branching, parallel, and aggregating plans, addressed by stable `node_id`. Sub-task checklists live here too.
- **shared (unprefixed)** — `todo_view` (read) and `todo_clear` (reset) work on whichever shape you built.

Attach **sub-tasks** to any graph node to break a single step into a small, checkable checklist.

## Tool Inventory

### Flat list set (`todo_list_*`) — sequential, index-based

| Tool | Purpose | Usage |
|------|---------|-------|
| `todo_list_create(items)` | Create/replace a flat todo list (auto-chains `A → B → C`) | Simple task list — sequential only, no parallel, no aggregation |
| `todo_list_update(index, status)` | Update one item's status by position | Flat-list workflows |

### Graph set (`todo_graph_*`) — DAG, node_id-based

| Tool | Purpose | Usage |
|------|---------|-------|
| `todo_graph_create(nodes, edges)` | Create/replace a task graph with branches and dependencies | Complex splits — child tasks, parallel branches, fan-in/aggregation |
| `todo_graph_update(node_id, status)` | Update one node's status by stable ID | Graph workflows |
| `todo_graph_add_edge(from_id, to_id)` | Add a dependency edge between two nodes | Graph workflows |
| `todo_graph_remove_edge(from_id, to_id)` | Remove a dependency edge | Graph workflows |
| `todo_graph_add_subtask(node_id, text)` | Add one or more checklist items to a node (`text` is a single string or `list[str]` for batched, atomic insertion) | Break down a node into smaller steps |
| `todo_graph_update_subtask(node_id, subtask_id, status, auto_complete=False)` | Check/uncheck a sub-task (binary: `pending` / `done`) | Track sub-task progress; optionally auto-complete the parent when all sub-tasks are done |
| `todo_graph_remove_subtask(node_id, subtask_id)` | Remove a sub-task | Clean up or correct mistakes |

### Shared (unprefixed)

| Tool | Purpose | Usage |
|------|---------|-------|
| `todo_view(verbose=False)` | View the current todo graph (truncates sub-tasks to 5 per node) | Read-only, any time, works on either shape |
| `todo_view(verbose=True)` | View the current todo graph with all sub-tasks expanded | When you need the full checklist of a node |
| `todo_clear()` | Clear all items | Reset between unrelated tasks |

## Choosing a Set

Use **`todo_graph_*`** whenever a task split carries complexity — child tasks, parallel tracks, or aggregation/fan-in points. Reserve **`todo_list_*`** for a strictly sequential checklist with no branching. The two sets operate on the same per-instance store, so `todo_view` and `todo_clear` work regardless of which set built the graph.

```python
# Flat list — linear plan
todo_list_create(items=["Setup DB", "Build API", "Run tests"])

# Graph — branching + fan-in
todo_graph_create(
    nodes=[
        {"id": "setup", "text": "Setup DB"},
        {"id": "api", "text": "Build API"},
        {"id": "ui", "text": "Build UI"},
        {"id": "test", "text": "Run tests"},
    ],
    edges=[
        {"from": "setup", "to": "api"},
        {"from": "setup", "to": "ui"},
        {"from": "api", "to": "test"},
        {"from": "ui", "to": "test"},
    ],
)
```

Rules that follow from the tool contract:

- Pick one set per graph and stay with it: `todo_list_*` for sequential / `index`-based work, `todo_graph_*` for branching / `node_id`-based work.
- Node `id` must be a non-empty, **non-numeric** string; `text` is required, `next_ids` is optional.
- Edges are layered on top of any per-node `next_ids`; cycles and self-loops are rejected.
- Statuses: `pending`, `in_progress`, `done` (plus case-insensitive aliases like `completed`, `wip`, `started`).

## Sub-Tasks

Sub-tasks are lightweight checklist items nested inside a single graph node. They let you break a node's work into smaller, checkable steps without growing the graph itself.

- Sub-tasks **do not participate in the graph structure** — they are local to their parent node, with no edges, no predecessors, no successors.
- They exist purely to break a node's work into smaller checkable steps. Use them when a node is a meaningful unit of work but the agent (or user) wants finer-grained progress signals.
- **Sub-task status is binary**: `pending` (☐) or `done` (☑). There is no `in_progress` state for sub-tasks — flip them once the step is done.
- Sub-tasks are rendered as an indented checklist under their parent node when viewed via `todo_view`.
- **Limits**: max **20 sub-tasks per node**; max **500 characters** per sub-task text. The manager raises `ValueError` for both caps, and the tool wraps them as `ERROR: Failed to add sub-task: {e}`:
  - `ValueError("Cannot add N sub-task(s): node '...' already has M sub-task(s); M+N would exceed the maximum of 20.")` — per-node sub-task cap reached (the combined existing + new count is checked up-front for batch calls, so a too-large batch is rejected in full).
  - `ValueError("texts[i] exceeds maximum length of 500 characters (got N)")` — a sub-task text entry is too long.
  - For empty text: `ValueError("texts[i] must be a non-empty string")` (single-string calls reuse the same path).
- A missing parent `node_id` (or missing instance) returns `ERROR: Node '<id>' not found in instance '<instance-id>'.`
- Sub-tasks survive `todo_graph_update` on the parent node — changing the parent's status does not alter its sub-tasks.
- `todo_clear` removes the entire graph including all sub-tasks.

```python
# Add a single sub-task to a node
todo_graph_add_subtask(node_id="n-a1b2c3d4", text="Create schema")

# Add several sub-tasks at once (atomic batch — all appended or none)
todo_graph_add_subtask(
    node_id="n-a1b2c3d4",
    text=["Create schema", "Run migration", "Seed data"],
)

# Check off a sub-task
todo_graph_update_subtask(node_id="n-a1b2c3d4", subtask_id="s-e5f6g7h8", status="done")

# Auto-complete parent when all sub-tasks done
todo_graph_update_subtask(node_id="n-a1b2c3d4", subtask_id="s-a1b2c3d4", status="done", auto_complete=True)
```

### `auto_complete` Behavior

`auto_complete` is an **opt-in** flag on `todo_graph_update_subtask` (default `False`). It only matters when you are marking a sub-task `done`.

- When `auto_complete=True` and **all** sub-tasks on the parent node are `done` → the parent node's status is automatically set to `done`. The tool emits a human-readable confirmation line such as ``Parent node 'alpha' auto-completed (all sub-tasks done).``.
- When `auto_complete=True` but **some** sub-tasks are still `pending` → no parent mutation occurs. The tool emits a note such as ``auto_complete requested but N sub-task(s) remain pending`` along with a count of remaining pending sub-tasks so you can finish them explicitly.
- When `auto_complete=False` (the default) → the sub-task is updated normally and the parent node's status is **never** touched, regardless of how many sub-tasks are done.

```python
# After all sub-tasks are done, this final call auto-completes the parent
todo_graph_update_subtask(
    node_id="n-a1b2c3d4",
    subtask_id="s-finalstep",
    status="done",
    auto_complete=True,
)
# → Tool emits a confirmation line, e.g.:
#    "Updated sub-task 's-finalstep' status to 'done'.
#     Parent node 'n-a1b2c3d4' auto-completed (all sub-tasks done)."
#    (the parent node n-a1b2c3d4 is now "done")
```

> **Reverse propagation does not happen.** Un-checking a sub-task (setting it back to `pending`) does **not** revert the parent node's status. Once a parent node is auto-completed, it stays `done` until you explicitly change it via `todo_graph_update(node_id=..., status=...)`. If you accidentally complete a parent early, use `todo_graph_update` to revert it.

### `verbose` Parameter on `todo_view`

`todo_view` accepts a single `verbose` flag that controls sub-task rendering:

- `todo_view(verbose=False)` — the **default**. Sub-tasks are truncated to **5 per node** with a `+N more` suffix when a node has more than 5. Use this for routine progress checks; the output stays compact.
- `todo_view(verbose=True)` — shows **all** sub-tasks for every node. Use this when you need to see the full checklist of a node with many sub-tasks, e.g. before deciding which `subtask_id` to update or to verify nothing was lost.

```python
# Default — compact view
todo_view()
# [0] ○ Setup DB
#   ☐ Create schema
#   ☐ Run migration
#   +3 more

# Verbose — full checklist
todo_view(verbose=True)
# [0] ○ Setup DB
#   ☐ Create schema
#   ☐ Run migration
#   ☐ Seed dev data
#   ☐ Verify connection
#   ☐ Update env file
#   ☐ Document setup steps
```

## Behavioral Hint

When you complete a task, mark it `done` via `todo_graph_update` (or `todo_list_update` for a flat list). The system returns a graph-aware reminder pointing to the **next ready items** — pending nodes whose predecessors are all done — and reports blocked items still waiting on a predecessor, or confirms completion. Keep your todo graph current throughout multi-step work; it tracks progress and prevents skipped steps.

When a node represents work with several internal steps, attach **sub-tasks** with `todo_graph_add_subtask` and check them off as you go. Pass a `list[str]` as `text` to attach several items in one atomic batch rather than issuing a call per item. Pass `auto_complete=True` on the final `todo_graph_update_subtask` to automatically promote the parent node to `done` once every sub-task is checked — this is the cleanest way to close out a multi-step node without a separate `todo_graph_update` call. Reach for `todo_view(verbose=True)` when you need the full checklist of a node with many sub-tasks (e.g. before choosing which `subtask_id` to update, or to confirm nothing was lost). Remember: sub-task un-checking does **not** revert a parent node's status, so use `todo_graph_update` explicitly if you need to re-open a finished parent.
