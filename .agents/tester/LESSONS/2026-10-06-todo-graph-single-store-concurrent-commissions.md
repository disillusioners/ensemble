# Todo-graph single-store replacement under concurrent commissions (2026-10-06, plugin slices ③+④-RV)

**Context**: Overnight build ran two tester commissions in overlapping waves (slice ③ pass in worktree-03 + slice ④ council re-verify in worktree-04), dispatched in the same turn.

**What happened**: I created a todo graph for slice ③ (nodes n3-*), then later created a second graph for the ④-RV wave — `todo_graph_create` **REPLACES the entire store** ("Create/replace an explicit todo DAG"), silently wiping the ③ graph. First `todo_graph_update(n3-c1, done)` after ④-RV's creation errored ("node does not exist"), which is the only visible symptom — no warning at creation time.

**Rule for future multi-commission passes**:
1. The todo store holds exactly ONE graph. With ≥2 concurrent commission waves, build ONE combined graph with per-slice namespaced node ids (e.g. `s3-c4-hashes`, `s4rv-2-epoch`) and per-slice aggregate nodes, created in a SINGLE `todo_graph_create` call.
2. If a second commission arrives after a graph already exists, RECREATE the combined graph (all nodes, statuses re-applied via updates) rather than calling `todo_graph_create` for just the new slice — the replace semantics will eat the first graph.
3. Symptom to watch for: `todo_graph_update` "node does not exist" on a node you know you created = your graph was replaced.

**Related (same session)**: per-slice RESULTS files + per-slice aggregate nodes worked fine once the combined graph was in place; PACKS.md sections accumulate independently of the graph.
