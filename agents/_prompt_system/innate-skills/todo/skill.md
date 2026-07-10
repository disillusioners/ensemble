# Todo Skill

Track multi-step workflows with a todo list or task graph. Use these tools to plan, track progress, and mark items complete.

## Tool Inventory

| Tool | Purpose |
|------|---------|
| `todo_create(items)` | Create/replace the full todo list (flat list, backward compatible) |
| `todo_create(nodes, edges)` | Create a task graph with branches and dependencies (new) |
| `todo_update(index, status)` | Update item status by index (backward compatible) |
| `todo_update(node_id, status)` | Update item status by node ID (new) |
| `todo_list()` | View current todo graph |
| `todo_clear()` | Clear all items |
| `todo_add_edge(from_id, to_id)` | Add a dependency edge between nodes (new) |
| `todo_remove_edge(from_id, to_id)` | Remove a dependency edge (new) |

## Behavioral Hint

When you complete a task, mark it `done` via `todo_update`. The system will remind you of the next ready item(s) — nodes whose predecessors are all done. Keep your todo list current throughout multi-step work — it helps you track progress and avoid skipping steps.