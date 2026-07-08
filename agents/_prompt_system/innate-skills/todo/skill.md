# Todo Skill

Track multi-step workflows with a todo list. Use these tools to plan, track progress, and mark items complete.

## Tool Inventory

| Tool | Purpose |
|------|---------|
| `todo_create(items)` | Create/replace the full todo list |
| `todo_update(index, status)` | Update item status (pending/in_progress/done) |
| `todo_list()` | View current todo list |
| `todo_clear()` | Clear all items |

## Behavioral Hint

When you complete a task, mark it `done` via `todo_update`. The system will remind you of the next pending item. Keep your todo list current throughout multi-step work — it helps you track progress and avoid skipping steps.
