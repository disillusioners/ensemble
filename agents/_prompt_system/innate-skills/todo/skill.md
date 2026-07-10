# Todo Skill

Track multi-step workflows with a todo graph (DAG). Plan work, track progress, and mark items complete. Two creation modes: a **flat list** for simple linear work, or an **explicit graph** for branching, parallel, and aggregating plans.

## Tool Inventory

| Tool | Purpose | Usage |
|------|---------|-------|
| `todo_create(items)` | Create/replace a flat todo list (auto-chains `A → B → C`) | Simple task list — sequential only, no parallel, no aggregation |
| `todo_create(nodes, edges)` | Create/replace a task graph with branches and dependencies | Complex splits — child tasks, parallel branches, fan-in/aggregation |
| `todo_update(index, status)` | Update one item's status by position | Flat-list workflows |
| `todo_update(node_id, status)` | Update one node's status by stable ID | Graph workflows (preferred; takes precedence over `index`) |
| `todo_add_edge(from_id, to_id)` | Add a dependency edge between two nodes | Graph workflows |
| `todo_remove_edge(from_id, to_id)` | Remove a dependency edge | Graph workflows |
| `todo_list()` | View the current todo graph | Read-only, any time |
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

## Behavioral Hint

When you complete a task, mark it `done` via `todo_update`. The system returns a graph-aware reminder pointing to the **next ready items** — pending nodes whose predecessors are all done — and reports blocked items still waiting on a predecessor, or confirms completion. Keep your todo graph current throughout multi-step work; it tracks progress and prevents skipped steps.
