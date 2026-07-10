# Tracking: Todo Graph Transformation

Current Plan: Todo Graph Transformation
Tracking File: todo-graph-transformation-tracking.md
Iteration: 001
Status: REJECTED
Last Updated: 2026-07-10 07:08

---

## Iteration 001 — REJECTED

### Verdict: REJECTED
### Date: 2026-07-10

### Blocking Issues

#### B1: Comment-fence prompt-injection protection lost in `_compute_reminder()`

**Location**: Phase 1, `phase1-plan.md:170-180` (Reminder Logic section)

**Description**: The current `TodoManager.update()` at `daemon/services/todo_manager.py:183-186` includes a documented prompt-injection mitigation: when a todo item is marked "done" and has a non-empty comment, the reminder is prefixed with `User commented:\n---\n{comment}\n---\n` — the `---` fences visually separate untrusted user-supplied content from system-formatted text.

The plan's new `_compute_reminder()` method (phase1-plan.md:170-180) defines only 4 reminder states:
1. Ready nodes → "⏭️ Ready: {text1}, {text2}, ..."
2. Blocked nodes → "⏳ {N} items blocked"
3. All done → "All items completed! ✅"
4. (implied) No ready but pending → "⏳ Waiting: {count} blocked items"

It **completely omits** the comment-fence pattern. No mention of:
- `completed_comment` variable
- `"User commented:"` prefix
- `---` fence delimiters
- The prompt-injection protection rationale

**Expected**: `_compute_reminder()` must preserve the comment-fence pattern from the current `update()` method. When the updated node is marked "done" and has a non-empty comment, the reminder must be prefixed with `"User commented:\n---\n{comment}\n---\n"` before the graph-aware reminder text.

**Found**: `_compute_reminder()` at phase1-plan.md:170-180 has no comment handling whatsoever. The docstring lists only 4 states, none involving comments.

**Impact**: 
- Safety regression — active prompt-injection mitigation removed
- 5 existing tests will break (test_todo_manager.py:192-247 — tests for `test_update_to_done_with_comment_prefixes_reminder`, `test_update_to_done_with_comment_and_no_remaining_pending`, `test_update_to_non_done_status_ignores_comment`, `test_update_with_empty_comment_skips_user_commented_prefix`)
- Phase 5 test plan (phase5-plan.md) does not enumerate these tests in its update list — only 4 `set(item.keys())` assertions are listed

**Fix**: Add a 5th rule to `_compute_reminder()`:
```python
def _compute_reminder(self, nodes: dict[str, TodoNode], updated_node_id: str) -> str:
    """Compute reminder for graph structure.

    Logic:
    1. Find all "ready" pending nodes — pending nodes whose ALL predecessors are done.
    2. If ready nodes exist: "⏭️ Ready: {text1}, {text2}, ..." (list all ready)
    3. If no ready nodes but pending nodes exist: "⏳ {N} items blocked"
    4. If no pending nodes: "All items completed! ✅"
    5. If updated node is "done" and has non-empty comment: prefix with
       "User commented:\\n---\\n{comment}\\n---\\n" (prompt-injection fence)
    """
```
Also add the 5 affected tests to Phase 5's test update enumeration.

### Non-Blocking Observations

1. **Orphan nodes not detected** — `_has_cycle()` uses Kahn's algorithm which counts all nodes in `visited`, including orphans (no path from any root). Orphan nodes are valid in a DAG but may confuse the reminder logic (an orphan pending node would never be "ready" since it has no predecessors). Non-blocking — orphans are an edge case, not a correctness issue.

2. **`add_node`/`remove_node` not exposed as tools** — Only `todo_add_edge` and `todo_remove_edge` are new tools. `add_node` and `remove_node` methods exist on the manager but are only accessible via API. This is reasonable for initial scope — agents create graphs via `todo_create(nodes=..., edges=...)` and modify edges dynamically. Adding/removing individual nodes can be a follow-up.

3. **`GET /todos/graph` marked optional** — Phase 3 task #2 includes it but the plan marks it "optional". No other phase depends on it. Acceptable — it's a convenience endpoint.

4. **Frontend `assignLayer` recursion** — `computeLayout()` uses recursive DFS. For 200 nodes in a deep chain, this could hit JS stack limits (~10K frames typical). However, MAX_NODES=200 and typical graphs are 5-20 nodes. Non-blocking but could add iterative fallback as a note.

5. **`create_graph` with user-provided IDs** — If user provides all-numeric IDs (e.g., "123"), the API's `node_id.isdigit()` check would incorrectly route to `set_comment_by_index()`. However, the plan's `create_graph` accepts user-provided IDs — these are NOT `n-` prefixed. The plan should validate/normalize user-provided IDs or document that user IDs must not be all-numeric. Non-blocking since this is an edge case, but worth a note.

6. **Test count: 49 new vs ~45 claimed** — Phase 5 enumerates tasks 15-63 = 49 new tests but claims ~45. Minor discrepancy, non-blocking.

7. **`_format_graph` merge rendering** — In a diamond A→B, A→C, B→D, C→D, the DFS visits D from B's subtree, renders it, then encounters D again from C's subtree and annotates `[2] (merged)`. The edge C→D is implicitly represented by the `(merged)` annotation. This is acceptable for text rendering — the agent understands D has multiple predecessors.

### Verified Correct

- Kahn's algorithm implementation (C1 fix) — correct, O(V+E), handles all edge cases
- Backward compatibility for `todo_update(0, "done")` (C5 fix) — positional mapping correct
- Node ID collision probability (C3 fix) — negligible for <200 nodes
- SSE schema freeze (W10 fix) — 6 keys complete, no downstream gaps
- Thread safety — snapshot-based `_has_cycle()` called within lock scope
- All codebase claims (line numbers, file paths, test counts) — verified accurate
- Internal consistency across 5 phases + 14 ADRs — no contradictions
- Phase coupling matrix — accurate, enables parallelization
