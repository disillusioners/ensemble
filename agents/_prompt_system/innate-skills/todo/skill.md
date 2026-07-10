# Todo Skill

Track multi-step work with a todo graph. Two tool sets share one store — pick by plan shape:

- **todo_list_*** — flat sequential list (auto-chains `A -> B -> C`), addressed by `index`.
- **todo_graph_*** — branching/parallel DAG, addressed by stable `node_id`; holds per-node sub-task checklists too.
- **todo_view** / **todo_clear** — shared read/reset for either set.

Sub-tasks are binary (`pending`/`done`); `auto_complete=True` on the final sub-task marks the parent `done`. Statuses: `pending`/`in_progress`/`done` (aliases accepted).

Keep the graph current: after marking a node `done`, the response points to the next ready nodes (predecessors all done) and any still blocked.
