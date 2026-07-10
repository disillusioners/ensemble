# Todo Skill

Track multi-step workflows with a todo graph (DAG). Plan work, track progress, and mark items complete. Two creation modes: a **flat list** for simple linear work, or an **explicit graph** for branching, parallel, and aggregating plans. Attach **sub-tasks** to any node to break a single step into a small, checkable checklist.

## Tool Inventory

| Tool | Purpose | Usage |
|------|---------|-------|
| `todo_create(items)` | Create/replace a flat todo list (auto-chains `A → B → C`) | Simple task list — sequential only, no parallel, no aggregation |
| `todo_create(nodes, edges)` | Create/replace a task graph with branches and dependencies | Complex splits — child tasks, parallel branches, fan-in/aggregation |
| `todo_update(index, status)` | Update one item's status by position | Flat-list workflows |
| `todo_update(node_id, status)` | Update one node's status by stable ID | Graph workflows (preferred; takes precedence over `index`) |
| `todo_add_edge(from_id, to_id)` | Add a dependency edge between two nodes | Graph workflows |
| `todo_remove_edge(from_id, to_id)` | Remove a dependency edge | Graph workflows |
| `todo_add_subtask(node_id, text)` | Add one or more checklist items to a node (`text` is a single string or `list[str]` for batched, atomic insertion) | Break down a node into smaller steps |
| `todo_update_subtask(node_id, subtask_id, status, auto_complete=False)` | Check/uncheck a sub-task (binary: `pending` / `done`) | Track sub-task progress; optionally auto-complete the parent when all sub-tasks are done |
| `todo_remove_subtask(node_id, subtask_id)` | Remove a sub-task | Clean up or correct mistakes |
| `todo_list(verbose=False)` | View the current todo graph (truncates sub-tasks to 5 per node) | Read-only, any time |
| `todo_list(verbose=True)` | View the current todo graph with all sub-tasks expanded | When you need the full checklist of a node |
| `todo_clear()` | Clear all items | Reset between unrelated tasks |

## Choosing a Mode

Prefer the **graph mode** (`nodes` + `edges`) whenever a task split carries complexity — child tasks, parallel tracks, or aggregation/fan-in points. Reserve `items` for a strictly sequential checklist with no branching.

```python
# Flat list — linear plan
todo_create(items=["Setup DB", "Build API", "Run tests"])

# Graph — branching + fan-in
todo_create(
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

- `items` takes precedence over `nodes`/`edges` — never pass both.
- Node `id` must be a non-empty, **non-numeric** string; `text` is required, `next_ids` is optional.
- Edges are layered on top of any per-node `next_ids`; cycles and self-loops are rejected.
- `node_id` takes precedence over `index` in `todo_update`; provide one or the other.
- Statuses: `pending`, `in_progress`, `done` (plus case-insensitive aliases like `completed`, `wip`, `started`).

## Sub-Tasks

Sub-tasks are lightweight checklist items nested inside a single todo node. They let you break a node's work into smaller, checkable steps without growing the graph itself.

- Sub-tasks **do not participate in the graph structure** — they are local to their parent node, with no edges, no predecessors, no successors.
- They exist purely to break a node's work into smaller checkable steps. Use them when a node is a meaningful unit of work but the agent (or user) wants finer-grained progress signals.
- **Sub-task status is binary**: `pending` (☐) or `done` (☑). There is no `in_progress` state for sub-tasks — flip them once the step is done.
- Sub-tasks are rendered as an indented checklist under their parent node when listed via `todo_list`.
- **Limits**: max **20 sub-tasks per node**; max **500 characters** per sub-task text. The manager raises `ValueError` for both caps, and the tool wraps them as `ERROR: Failed to add sub-task: {e}`:
  - `ValueError("Cannot add N sub-task(s): node '...' already has M sub-task(s); M+N would exceed the maximum of 20.")` — per-node sub-task cap reached (the combined existing + new count is checked up-front for batch calls, so a too-large batch is rejected in full).
  - `ValueError("texts[i] exceeds maximum length of 500 characters (got N)")` — a sub-task text entry is too long.
  - For empty text: `ValueError("texts[i] must be a non-empty string")` (single-string calls reuse the same path).
- A missing parent `node_id` (or missing instance) returns `ERROR: Node '<id>' not found in instance '<instance-id>'.`
- Sub-tasks survive `todo_update` on the parent node — changing the parent's status does not alter its sub-tasks.
- `todo_clear` removes the entire graph including all sub-tasks.

```python
# Add a single sub-task to a node
todo_add_subtask(node_id="n-a1b2c3d4", text="Create schema")

# Add several sub-tasks at once (atomic batch — all appended or none)
todo_add_subtask(
    node_id="n-a1b2c3d4",
    text=["Create schema", "Run migration", "Seed data"],
)

# Check off a sub-task
todo_update_subtask(node_id="n-a1b2c3d4", subtask_id="s-e5f6g7h8", status="done")

# Auto-complete parent when all sub-tasks done
todo_update_subtask(node_id="n-a1b2c3d4", subtask_id="s-a1b2c3d4", status="done", auto_complete=True)
```

### `auto_complete` Behavior

`auto_complete` is an **opt-in** flag on `todo_update_subtask` (default `False`). It only matters when you are marking a sub-task `done`.

- When `auto_complete=True` and **all** sub-tasks on the parent node are `done` → the parent node's status is automatically set to `done`. The tool emits a human-readable confirmation line such as ``Parent node 'alpha' auto-completed (all sub-tasks done).``.
- When `auto_complete=True` but **some** sub-tasks are still `pending` → no parent mutation occurs. The tool emits a note such as ``auto_complete requested but N sub-task(s) remain pending`` along with a count of remaining pending sub-tasks so you can finish them explicitly.
- When `auto_complete=False` (the default) → the sub-task is updated normally and the parent node's status is **never** touched, regardless of how many sub-tasks are done.

```python
# After all sub-tasks are done, this final call auto-completes the parent
todo_update_subtask(
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

> **Reverse propagation does not happen.** Un-checking a sub-task (setting it back to `pending`) does **not** revert the parent node's status. Once a parent node is auto-completed, it stays `done` until you explicitly change it via `todo_update(node_id=..., status=...)`. If you accidentally complete a parent early, use `todo_update` to revert it.

### `verbose` Parameter on `todo_list`

`todo_list` accepts a single `verbose` flag that controls sub-task rendering:

- `todo_list(verbose=False)` — the **default**. Sub-tasks are truncated to **5 per node** with a `+N more` suffix when a node has more than 5. Use this for routine progress checks; the output stays compact.
- `todo_list(verbose=True)` — shows **all** sub-tasks for every node. Use this when you need to see the full checklist of a node with many sub-tasks, e.g. before deciding which `subtask_id` to update or to verify nothing was lost.

```python
# Default — compact view
todo_list()
# [0] ○ Setup DB
#   ☐ Create schema
#   ☐ Run migration
#   +3 more

# Verbose — full checklist
todo_list(verbose=True)
# [0] ○ Setup DB
#   ☐ Create schema
#   ☐ Run migration
#   ☐ Seed dev data
#   ☐ Verify connection
#   ☐ Update env file
#   ☐ Document setup steps
```

## Behavioral Hint

When you complete a task, mark it `done` via `todo_update`. The system returns a graph-aware reminder pointing to the **next ready items** — pending nodes whose predecessors are all done — and reports blocked items still waiting on a predecessor, or confirms completion. Keep your todo graph current throughout multi-step work; it tracks progress and prevents skipped steps.

When a node represents work with several internal steps, attach **sub-tasks** with `todo_add_subtask` and check them off as you go. Pass a `list[str]` as `text` to attach several items in one atomic batch rather than issuing a call per item. Pass `auto_complete=True` on the final `todo_update_subtask` to automatically promote the parent node to `done` once every sub-task is checked — this is the cleanest way to close out a multi-step node without a separate `todo_update` call. Reach for `todo_list(verbose=True)` when you need the full checklist of a node with many sub-tasks (e.g. before choosing which `subtask_id` to update, or to confirm nothing was lost). Remember: sub-task un-checking does **not** revert a parent node's status, so use `todo_update` explicitly if you need to re-open a finished parent.
