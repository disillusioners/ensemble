# Phase 5: Test Suite Update + Graph Tests

## Objective

Update all 101 existing tests (across 5 files) to work with the new graph data model (`TodoNode` with `id`, `index`, `next_ids` fields). Add ~45 new tests covering DAG-specific functionality: cycle rejection, branching, merging, orphan nodes, edge management, merge-node text rendering, and backward-compatibility shims. Ensure zero test regressions.

## Coupling

- **Depends on**: Phases 1-4 (all layers must be implemented before tests can run green)
- **Coupling type**: **loose** — tests validate all layers but can be written against the interface contracts
- **Shared files with other phases**: All test files listed below
- **Why this coupling**: Tests are the validation layer. They can be written incrementally alongside each phase (test-first for new graph features, test-update for existing tests), but full green requires all phases complete.

## Context

> **C7 fix**: The plan originally claimed 77 tests. The actual count (verified via `pytest --collect-only`) is **101 tests** across 5 files. Phase 5 effort is re-estimated to 4-5h (up from 3-4h).

### Actual Test Counts (Verified)

| File | Tests | Classes | Lines |
|------|-------|---------|-------|
| `tests/test_todo_manager.py` | 35 | 6 | 496 |
| `tests/test_todo_tools.py` | 19 | 6 | 370 |
| `tests/test_todo_sse.py` | 11 | 6 | 328 |
| `tests/test_todo_comment_edge_cases.py` | 18 | 3 | 634 |
| `tests/unit/routers/test_todo_api.py` | 18 | 3 | 474 |
| **Total** | **101** | **24** | **2302** |

## C8 Fix: Complete Enumeration of `index` / Key-Set Assertion Sites

> **C8 fix**: Every assertion that checks `set(item.keys()) == {"index", "text", "status", "comment"}` or accesses `item["index"]` / `["index"]` must be updated. After the graph transformation, the key set becomes `{"id", "index", "text", "status", "comment", "next_ids"}` (6 keys — `index` preserved per C4 fix).

### All Affected Assertion Sites

| File | Line | Current Assertion | Updated Assertion |
|------|------|-------------------|-------------------|
| `tests/test_todo_manager.py` | 50 | `assert [item["index"] for item in result] == [0, 1, 2]` | Keep — `index` is preserved (C4 fix). Still valid. |
| `tests/test_todo_manager.py` | 67 | `assert result[0]["index"] == 0` | Keep — still valid. |
| `tests/test_todo_manager.py` | 266 | `assert updated["index"] == 1` | Keep — still valid. |
| `tests/test_todo_manager.py` | 396 | `assert set(item.keys()) == {"index", "text", "status", "comment"}` | **UPDATE** → `{"id", "index", "text", "status", "comment", "next_ids"}` |
| `tests/test_todo_manager.py` | 397 | `assert item["index"] == 0` | Keep — still valid. |
| `tests/test_todo_sse.py` | 304 | `assert set(item.keys()) == {"index", "text", "status", "comment"}` | **UPDATE** → `{"id", "index", "text", "status", "comment", "next_ids"}` |
| `tests/test_todo_sse.py` | 305 | `assert item["index"] == 0` | Keep — still valid. |
| `tests/test_todo_comment_edge_cases.py` | 187 | `assert set(item.keys()) == {"index", "text", "status", "comment"}` | **UPDATE** → `{"id", "index", "text", "status", "comment", "next_ids"}` |
| `tests/unit/routers/test_todo_api.py` | 135 | `assert set(item.keys()) == {"index", "text", "status", "comment"}` | **UPDATE** → `{"id", "index", "text", "status", "comment", "next_ids"}` |
| `tests/unit/routers/test_todo_api.py` | 203 | `assert body["index"] == 1` | Keep — still valid. |

**Summary**: 4 `set(item.keys())` assertions need updating (lines 396, 304, 187, 135). All `item["index"]` assertions remain valid because `index` is preserved in the frozen schema (C4 fix).

### Additional Index-Related References (Verify, Don't Necessarily Change)

| File | Lines | What | Action |
|------|-------|------|--------|
| `tests/test_todo_tools.py` | 213, 231, 244, 261, 274 | `update_tool.coroutine(index=0, status="done")` | Keep — backward-compatible `index` param still works (C5 fix) |
| `tests/test_todo_tools.py` | 162-163, 302-303 | `assert "[0]" in result`, `assert "[1]" in result` | Verify — `_format_graph()` still shows `[index]` in output |
| `tests/unit/routers/test_todo_api.py` | 247-291 | `test_index_too_large_returns_404`, `test_negative_index_returns_404` | Keep — numeric index backward-compat path must still return 404 |
| `tests/test_todo_comment_edge_cases.py` | 558-573 | `test_no_sse_on_bad_index`, `test_no_sse_on_negative_index` | Keep — backward-compat index validation still works |

### Comment-Fence Prompt-Injection Tests (MUST PRESERVE)

> **BLOCKING fix**: The current `TodoManager.update()` (lines 183-186) includes a comment-fence prompt-injection mitigation. When a todo item is marked "done" with a non-empty comment, the reminder is prefixed with `"User commented:\n---\n{comment}\n---\n"`. The `---` fences visually separate untrusted user text from system-formatted reminder. These 5 existing tests verify this behavior and MUST continue to pass after the graph refactor. The `_compute_reminder()` method in Phase 1 preserves this pattern (see phase1-plan.md).

| File | Lines | Test Name | What It Verifies | Action |
|------|-------|-----------|------------------|--------|
| `tests/test_todo_manager.py` | 177-190 | `test_update_to_done_with_no_comment_returns_default_reminder` | Done + no comment → no `"User commented:"` prefix, just `"Next:"` | Keep — verify still passes. `_compute_reminder()` only prefixes when comment is non-empty. |
| `tests/test_todo_manager.py` | 192-206 | `test_update_to_done_with_comment_prefixes_reminder` | Done + non-empty comment → `"User commented:\n---\n{comment}\n---\n"` prefix + `"Next:"` | Keep — verify still passes. `_compute_reminder()` step 2 applies the fence. |
| `tests/test_todo_manager.py` | 208-221 | `test_update_to_done_with_comment_and_no_remaining_pending` | Done + comment + no pending → fence prefix + `"All items completed!"` | Keep — verify still passes. Tests fence + all-done base reminder combination. |
| `tests/test_todo_manager.py` | 223-237 | `test_update_to_non_done_status_ignores_comment` | `in_progress` + comment → NO fence prefix, just `"Next:"` | Keep — verify still passes. `_compute_reminder()` only fences on `"done"` status. |
| `tests/test_todo_manager.py` | 239-248 | `test_update_with_empty_comment_skips_user_commented_prefix` | Done + empty comment → NO fence prefix, just `"Next:"` | Keep — verify still passes. Empty string → no prefix (falsy check). |

**Key**: All 5 tests use `create("inst-1", [...])` (flat list) + `set_comment("inst-1", index, ...)` + `update("inst-1", index, "done")`. After the graph refactor, these call paths become `create()` → `set_comment_by_index()` → `update_by_index()`. The tests must be updated to use the `_by_index` shims OR the new `node_id`-based methods. Either way, the reminder output (including the comment fence) must be identical.

**Recommended test update approach**: Update these 5 tests to use `node_id`-based calls for the primary test, and keep the `_by_index` calls as backward-compat verification tests. This doubles coverage (5 → 10 tests) and ensures both code paths preserve the fence.

## Tasks

### Part A: Update Existing Tests (101 tests)

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Update 4 `set(item.keys())` assertions | Change from `{"index", "text", "status", "comment"}` to `{"id", "index", "text", "status", "comment", "next_ids"}`. Lines: `test_todo_manager.py:396`, `test_todo_sse.py:304`, `test_todo_comment_edge_cases.py:187`, `test_todo_api.py:135`. | 4 test files |
| 2 | Update `test_create_assigns_sequential_indices` | `test_todo_manager.py:45-53` — still asserts `[0, 1, 2]` for `index` field. Keep as-is (index preserved). Add new assertion: each dict also has `id` (non-empty string) and `next_ids` (list). | `tests/test_todo_manager.py` |
| 3 | Update `test_create_returned_dicts_are_independent_of_state` | `test_todo_manager.py:80-92` — verify `next_ids` list copy is independent (mutating returned `next_ids` doesn't corrupt state). | `tests/test_todo_manager.py` |
| 4 | Update `test_get_all_returns_list_of_dicts` | `test_todo_manager.py:386-400` — the key set assertion changes (task 1). Also add assertions for `id` and `next_ids` field types. | `tests/test_todo_manager.py` |
| 5 | Update factory test: `test_factory_returns_list_of_four_tools` | `test_todo_tools.py:68-73` — rename to `test_factory_returns_list_of_six_tools`, assert `len(tools) == 6`. | `tests/test_todo_tools.py` |
| 6 | Update factory test: `test_factory_returns_documented_tool_names` | `test_todo_tools.py:75-81` — add `"todo_add_edge"`, `"todo_remove_edge"` to expected names list. | `tests/test_todo_tools.py` |
| 7 | Update SSE payload structure tests | `test_todo_sse.py:296-308` — key set assertion changes (task 1). Add assertions for `id` and `next_ids` in SSE payload. | `tests/test_todo_sse.py` |
| 8 | Update SSE payload after partial progress test | `test_todo_sse.py:310-328` — verify `next_ids` is present and correct in payload after status updates. | `tests/test_todo_sse.py` |
| 9 | Update concurrent test key-set assertion | `test_todo_comment_edge_cases.py:187` — key set changes (task 1). | `tests/test_todo_comment_edge_cases.py` |
| 10 | Update API key-set assertion | `test_todo_api.py:135` — key set changes (task 1). | `tests/unit/routers/test_todo_api.py` |
| 11 | Verify all `index`-param tests still pass | `test_todo_tools.py:213,231,244,261,274` — `todo_update(index=0, status="done")` must still work (C5 backward compat). No changes needed — just verify. | `tests/test_todo_tools.py` |
| 12 | Verify all `set_comment_by_index` path tests | `test_todo_api.py:247-291` — numeric index in URL path must still work. No changes needed — just verify. | `tests/unit/routers/test_todo_api.py` |
| 13 | Verify `[N]` format assertions in tool output | `test_todo_tools.py:162-163, 302-303` — `_format_graph()` still shows `[index]` in output. Verify, adjust if format changes. | `tests/test_todo_tools.py` |
| 14 | Update `_build_tools()` helper | All test files that use `_build_tools()` — verify it returns 6 tools. Tests accessing `tools[4]` and `tools[5]` for new tool tests. | `tests/test_todo_tools.py`, `tests/test_todo_sse.py` |

### Part B: New Graph-Specific Tests (~45 tests)

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 15 | Test `create_graph()` with valid DAG | Create nodes + edges, verify all nodes present with correct `next_ids`. | `tests/test_todo_manager.py` |
| 16 | Test `create_graph()` rejects cycle | Create A→B→C→A, verify `ValueError` or rejection. | `tests/test_todo_manager.py` |
| 17 | Test `create_graph()` rejects dangling edge refs | Edge pointing to non-existent node ID → rejection. | `tests/test_todo_manager.py` |
| 18 | Test `create_graph()` with branching | A→B, A→C — verify both successors in `next_ids`. | `tests/test_todo_manager.py` |
| 19 | Test `create_graph()` with merge (diamond) | A→B, A→C, B→D, C→D — verify D has 2 predecessors. | `tests/test_todo_manager.py` |
| 20 | Test `create_graph()` enforces MAX_NODES | Create 201 nodes → `ValueError`. | `tests/test_todo_manager.py` |
| 21 | Test `create()` (flat list) enforces MAX_NODES | Create 201 items → `ValueError`. (W4 fix.) | `tests/test_todo_manager.py` |
| 22 | Test `add_node()` to existing graph | Add node with `next_ids` pointing to existing nodes. Verify added. | `tests/test_todo_manager.py` |
| 23 | Test `add_node()` enforces MAX_NODES | Graph at 200 nodes, add 1 more → `ValueError`. | `tests/test_todo_manager.py` |
| 24 | Test `add_edge()` creates directed edge | Add A→B, verify `A.next_ids` includes `B`. | `tests/test_todo_manager.py` |
| 25 | Test `add_edge()` rejects cycle | A→B exists, add B→A → rejected (returns `None`). | `tests/test_todo_manager.py` |
| 26 | Test `add_edge()` rejects non-existent nodes | Add edge with unknown `from_id` or `to_id` → `None`. | `tests/test_todo_manager.py` |
| 27 | Test `remove_edge()` removes directed edge | Remove A→B, verify `A.next_ids` no longer includes `B`. | `tests/test_todo_manager.py` |
| 28 | Test `remove_edge()` on non-existent edge | Remove edge that doesn't exist → `None`. | `tests/test_todo_manager.py` |
| 29 | Test `remove_node()` removes node + edges | Remove node B (A→B→C), verify A.next_ids and C's predecessors updated. | `tests/test_todo_manager.py` |
| 30 | Test `remove_node()` on non-existent node | Remove unknown node → `None`. | `tests/test_todo_manager.py` |
| 31 | Test `_has_cycle()` on valid DAG | Linear chain, branching, diamond — all return `False`. | `tests/test_todo_manager.py` |
| 32 | Test `_has_cycle()` on cyclic graph | A→B→A, A→B→C→A — all return `True`. | `tests/test_todo_manager.py` |
| 33 | Test `_generate_id()` prefix | Verify all IDs start with `n-` (C3 fix). | `tests/test_todo_manager.py` |
| 34 | Test `_generate_id()` uniqueness | Generate 200 IDs, verify no collisions. | `tests/test_todo_manager.py` |
| 35 | Test `_compute_reminder()` with single ready node | One pending node, all predecessors done → "Next: {text}". | `tests/test_todo_manager.py` |
| 36 | Test `_compute_reminder()` with multiple ready nodes | Two pending nodes, both unblocked → "Ready: {text1}, {text2}". | `tests/test_todo_manager.py` |
| 37 | Test `_compute_reminder()` with blocked nodes | Pending nodes but all have non-done predecessors → "Waiting: N blocked". | `tests/test_todo_manager.py` |
| 38 | Test `_compute_reminder()` all done | All nodes done → "All items completed!". | `tests/test_todo_manager.py` |
| 39 | Test `_compute_reminder()` comment-fence on done | Done + non-empty comment → `"User commented:\n---\n{comment}\n---\n"` prefix before base reminder. **Prompt-injection protection — must pass.** | `tests/test_todo_manager.py` |
| 40 | Test `_compute_reminder()` comment-fence with all done | Done + comment + no pending nodes → fence prefix + `"All items completed!"`. | `tests/test_todo_manager.py` |
| 41 | Test `_compute_reminder()` no fence on non-done | `in_progress` + comment → NO fence prefix. Only `"done"` triggers the fence. | `tests/test_todo_manager.py` |
| 42 | Test `_compute_reminder()` no fence on empty comment | Done + empty comment → NO fence prefix. Falsy comment → no prefix. | `tests/test_todo_manager.py` |
| 43 | Test comment-fence with branching graph | Done node with comment in a branching graph → fence prefix + "Ready: {text1}, {text2}". | `tests/test_todo_manager.py` |
| 44 | Test `update()` by node_id | Update status using `node_id` parameter. Verify status change + reminder. | `tests/test_todo_manager.py` |
| 45 | Test `update_by_index()` shim | Update using `index` — verify it resolves to correct node. | `tests/test_todo_manager.py` |
| 46 | Test `set_comment()` by node_id | Set comment using `node_id`. Verify comment persisted. | `tests/test_todo_manager.py` |
| 47 | Test `set_comment_by_index()` shim | Set comment using `index` — verify it resolves to correct node. | `tests/test_todo_manager.py` |
| 48 | Test `get_graph()` returns correct structure | Verify `{"nodes": [...], "edges": [...]}` shape with correct edge derivation. | `tests/test_todo_manager.py` |
| 49 | Test `create_graph()` rejects all-numeric user IDs | User-supplied node ID `"123"` → `ValueError` (prevents API `isdigit()` misfire). | `tests/test_todo_manager.py` |
| 50 | Test `todo_create` with graph structure | Call `todo_create(nodes=[...], edges=[...])` via tool. Verify formatted output. | `tests/test_todo_tools.py` |
| 51 | Test `todo_create` with flat list (backward compat) | Call `todo_create(items=["A","B"])` — verify still works, output includes `[0]`, `[1]`. | `tests/test_todo_tools.py` |
| 52 | Test `todo_update` with node_id | Call `todo_update(node_id="n-xxx", status="done")` via tool. | `tests/test_todo_tools.py` |
| 53 | Test `todo_update` positional backward compat | Call `todo_update(0, "done")` — verify `index=0, status="done"` (C5 fix). | `tests/test_todo_tools.py` |
| 54 | Test `todo_add_edge` tool | Call tool, verify edge added, SSE emitted, formatted output. | `tests/test_todo_tools.py` |
| 55 | Test `todo_remove_edge` tool | Call tool, verify edge removed, SSE emitted, formatted output. | `tests/test_todo_tools.py` |
| 56 | Test `_format_graph()` linear chain | Verify linear chain renders as flat list `[0] ○ text`. | `tests/test_todo_tools.py` |
| 57 | Test `_format_graph()` branching | Verify branching renders with `└→` indentation. | `tests/test_todo_tools.py` |
| 58 | Test `_format_graph()` merge node (W6 fix) | Verify diamond renders with `(merged)` annotation. | `tests/test_todo_tools.py` |
| 59 | Test `todo_add_edge` SSE emission | Verify `stream_todo_update` called once after edge add. | `tests/test_todo_sse.py` |
| 60 | Test `todo_remove_edge` SSE emission | Verify `stream_todo_update` called once after edge remove. | `tests/test_todo_sse.py` |
| 61 | Test SSE payload includes `id` and `next_ids` | After `todo_create` with graph, verify SSE payload has 6-key dicts. | `tests/test_todo_sse.py` |
| 62 | Test SSE payload after `add_edge` | Verify `next_ids` updated in SSE payload after edge addition. | `tests/test_todo_sse.py` |
| 63 | Test `POST /todos/edges` endpoint | Add edge via API, verify 200 + graph structure returned. | `tests/unit/routers/test_todo_api.py` |
| 64 | Test `DELETE /todos/edges` endpoint | Remove edge via API, verify 200 + graph structure returned. | `tests/unit/routers/test_todo_api.py` |
| 65 | Test `POST /todos/edges` cycle rejection | Add edge that creates cycle → 400. | `tests/unit/routers/test_todo_api.py` |
| 66 | Test `POST /todos/edges` non-existent node | Add edge with bad node ID → 400 or 404. | `tests/unit/routers/test_todo_api.py` |
| 67 | Test `POST /todos/{node_id}/comment` with string ID | Use `n-xxxx` node ID in URL → 200. | `tests/unit/routers/test_todo_api.py` |
| 68 | Test `POST /todos/{node_id}/comment` with numeric index | Use `"0"` in URL → 200 (backward compat). | `tests/unit/routers/test_todo_api.py` |
| 69 | Test `GET /todos` returns 6-key dicts | Verify response items have `id`, `index`, `text`, `status`, `comment`, `next_ids`. | `tests/unit/routers/test_todo_api.py` |

## Key Files

- `tests/test_todo_manager.py` — 35 existing tests (update ~5, add ~30 new, including 5 comment-fence + 1 all-numeric ID rejection)
- `tests/test_todo_tools.py` — 19 existing tests (update ~3, add ~9 new)
- `tests/test_todo_sse.py` — 11 existing tests (update ~2, add ~4 new)
- `tests/test_todo_comment_edge_cases.py` — 18 existing tests (update ~1, add ~0 new)
- `tests/unit/routers/test_todo_api.py` — 18 existing tests (update ~2, add ~7 new)

## Constraints

- **Zero regressions**: All 101 existing tests must pass after updates
- **Backward compatibility tests preserved**: Tests using `index=` params and numeric URL paths must still pass
- **New tests cover DAG validation**: Cycle rejection, branching, merging, orphan nodes, edge management
- **Key-set assertions updated**: 4 `set(item.keys())` assertions updated to 6-key set (C8 fix)
- **Test count**: 101 existing + ~50 new = ~151 total tests (includes 5 comment-fence preservation + 1 all-numeric ID rejection)
- **No new test files**: All new tests added to existing 5 files

## Deliverables

- [ ] All 4 `set(item.keys())` assertions updated to 6-key set (C8 fix)
- [ ] Factory test updated: 6 tools, 6 names
- [ ] All 101 existing tests pass (updated for graph model)
- [ ] ~50 new graph-specific tests added across 5 files
- [ ] **5 comment-fence prompt-injection tests preserved and passing** (done + non-empty comment → `"User commented:\n---\n{comment}\n---\n"` prefix)
- [ ] Cycle rejection tests (create_graph, add_edge)
- [ ] Branching + merge (diamond) tests
- [ ] Edge management tests (add_edge, remove_edge, remove_node)
- [ ] Backward-compat shim tests (update_by_index, set_comment_by_index)
- [ ] `_format_graph()` merge-node rendering test (W6 fix)
- [ ] `todo_update` positional backward-compat test (C5 fix)
- [ ] SSE payload 6-key verification tests
- [ ] API edge endpoint tests (POST/DELETE)
- [ ] API node_id vs numeric index backward-compat test
- [ ] `MAX_NODES` guard tests on create() + create_graph() + add_node() (W4 fix)
- [ ] `_generate_id()` prefix test (`n-` prefix — C3 fix)
- [ ] `create_graph()` rejects all-numeric user-supplied node IDs (prevents API `isdigit()` misfire)
- [ ] Total test count: ~151 (101 existing + ~50 new)
