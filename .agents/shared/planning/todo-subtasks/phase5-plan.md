# Phase 5: Tests + Integration Verification

## Objective
Write comprehensive tests for all sub-task functionality across manager, tools, and API layers. Verify backward compatibility (all existing tests pass after updating 10 assertions). Run integration checks to ensure end-to-end flow works.

## Coupling
- **Depends on**: Phases 1-4 (all layers must be implemented)
- **Coupling type**: loose — tests verify contracts and behavior, not implementation details
- **Shared files with other phases**: Tests are new files; they import from all layers
- **Shared APIs/interfaces**: Manager methods, tool functions, API endpoints
- **Why this coupling**: Tests are the verification layer; they can only be written after the code under test exists

## Context
- Existing test suite: 168 tests across 5 files
- Test framework: `pytest` with `unittest.mock` for async/API tests
- API tests use `FastAPI TestClient` with mock manager
- Manager tests are pure sync unit tests (no DB, no asyncio)
- PostgreSQL is the primary dev/test DB (but todo system is in-memory, no DB)

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 0 | **Update 10 existing test assertions** | **8 schema-key-set tests** — update `set(item.keys()) == {6-key-set}` to include `"subtasks"`:<br>1. `test_todo_manager.py:399`<br>2. `test_todo_manager.py:1069` (`test_to_dict_has_six_keys` → rename to `test_to_dict_has_seven_keys`)<br>3-6. `test_todo_sse.py:324-331, 387-394, 434-441, 481-488`<br>7. `test_todo_api.py:158-160`<br>8. `test_comment_edge_cases.py:187`<br>**2 tool-count tests** — update in `test_todo_tools.py`:<br>9. Line 75: `assert len(tools) == 6` → `== 9`<br>10. Lines 84-91: exact tool name list → add 3 new names | 5 test files |
| 1 | Write manager sub-task tests | ~25-30 tests in `TestTodoSubtasks` class covering CRUD, propagation, limits, edge cases, schema. See Phase 1 test strategy for full list. | `tests/test_todo_manager.py` |
| 2 | Write tool sub-task tests | ~10-12 tests covering `todo_add_subtask`, `todo_update_subtask`, `todo_remove_subtask` tools — success paths, error paths, SSE emission, `_format_graph` rendering. | `tests/test_todo_tools.py` |
| 3 | Write API sub-task tests | ~12-15 tests covering POST/PATCH/DELETE endpoints — success, 404 (instance/node/sub-task not found), 400 (invalid status, max sub-tasks), SSE emission, route ordering. | `tests/unit/routers/test_todo_api.py` |
| 4 | Write SSE sub-task tests | ~4-5 tests verifying SSE payload includes `subtasks` key, correct serialization, emission on all mutations. | `tests/test_todo_sse.py` |
| 5 | Write edge case tests | ~5-6 tests: sub-task on node with comment, sub-task + graph edges interaction, auto_complete + reminder, remove sub-task during iteration, concurrent mutations. | `tests/test_todo_comment_edge_cases.py` or new file |
| 6 | Run full existing test suite | `pytest tests/test_todo_manager.py tests/test_todo_tools.py tests/test_todo_sse.py tests/test_todo_comment_edge_cases.py tests/unit/routers/test_todo_api.py -v` — all 168 existing tests pass **after** Task 0 updates (10 assertion changes). | — |
| 7 | Run new test suite | All new tests pass. Target: ~55-70 new tests, 0 failures. | — |
| 8 | Manual integration check | Start dev server, create a graph with sub-tasks via API, verify SSE updates, toggle sub-tasks, check frontend rendering in both linear and graph modes. | — |
| 9 | Backward compatibility audit | Verify: (a) existing nodes without sub-tasks serialize correctly, (b) old API responses still work, (c) frontend handles `subtasks: []` gracefully, (d) `_format_graph` handles nodes without sub-tasks. | — |

## Key Files
- `tests/test_todo_manager.py` — 71 tests currently, +25-30 new
- `tests/test_todo_tools.py` — 35 tests currently, +10-12 new
- `tests/unit/routers/test_todo_api.py` — 31 tests currently, +12-15 new
- `tests/test_todo_sse.py` — 14 tests currently, +4-5 new
- `tests/test_todo_comment_edge_cases.py` — 17 tests currently, +5-6 new

## Test Categories

### Manager Tests (Phase 1 + Phase 5)

| Category | Tests | Description |
|----------|-------|-------------|
| Sub-task CRUD | 8 | add, update, remove — success and not-found paths |
| Status propagation | 6 | auto_complete on/off, already-done, not-all-done, zero-subtasks (vacuous-truth), partial completion |
| Status normalization | 2 | aliases for sub-task status, in_progress rejection |
| Limits | 4 | MAX_SUBTASKS_PER_NODE at add_subtask, create_graph, add_node; malformed subtask spec |
| Schema | 4 | _to_dict 7-key, subtasks serialization, empty default |
| create_graph with subtasks | 3 | subtasks in node specs, auto-ID, flat-list no subtasks |
| add_node with subtasks | 2 | optional subtasks parameter |
| Backward compat | 2 | existing tests pass, nodes without subtasks work |

### Tool Tests (Phase 2 + Phase 5)

| Category | Tests | Description |
|----------|-------|-------------|
| todo_add_subtask | 3 | success, node not found, max exceeded |
| todo_update_subtask | 3 | success, auto_complete, invalid status |
| todo_remove_subtask | 2 | success, not found |
| _format_graph rendering | 3 | linear with subtasks, branching with subtasks, no subtasks |

### API Tests (Phase 3 + Phase 5)

| Category | Tests | Description |
|----------|-------|-------------|
| POST subtask | 3 | success, 404 instance/node, 400 max/empty |
| PATCH subtask | 3 | success, 404, 400 invalid status |
| DELETE subtask | 2 | success, 404 |
| SSE emission | 2 | todo_update emitted with subtasks, payload correct |
| Route ordering | 2 | /subtasks not captured by /{node_id}/comment |

### SSE Tests (Phase 5)

| Category | Tests | Description |
|----------|-------|-------------|
| Payload schema | 2 | 7-key schema, subtasks serialization |
| Emission on mutation | 3 | add/update/remove all emit todo_update |

### Edge Case Tests (Phase 5)

| Category | Tests | Description |
|----------|-------|-------------|
| Sub-task + comment | 1 | Node with both sub-tasks and comment |
| Sub-task + edges | 1 | Sub-tasks don't affect edge operations |
| Auto-complete + reminder | 1 | Reminder reflects propagated status |
| Auto-complete + not-all-done | 1 | `auto_complete=True` but sub-tasks remain pending → no propagation, `auto_completed=False` |
| Auto-complete + zero sub-tasks | 1 | `auto_complete=True` + 0 sub-tasks → no propagation (vacuous-truth guard) |
| create_graph malformed subtasks | 2 | Non-list subtasks → `ValueError`; spec missing text → `ValueError` |
| MAX_SUBTASKS at create_graph | 1 | Node spec with 21 sub-tasks → `ValueError` |
| Concurrent update_subtask | 1 | Two threads update same sub-task — lock correctness |
| Sub-task ID collision | 1 | Two sub-tasks with same explicit ID in create_graph → `ValueError` |
| Remove during iteration | 1 | Thread safety |
| Empty sub-task text | 1 | Validation rejects empty text |

## Constraints
- All existing tests pass **after updating 10 assertion tests** (Task 0): 8 schema-key-set assertions updated to 7-key set, 2 tool-count assertions updated to 9 tools. No other existing test modifications needed.
- New tests must not depend on test execution order
- API tests use `TestClient` with mock manager (same pattern as existing)
- Manager tests are pure sync (no asyncio)
- No external dependencies (no real DB, no real SSE server)

## Deliverables
- [ ] 10 existing test assertions updated (8 schema-key-set + 2 tool-count) — Task 0
- [ ] ~25-30 new manager tests
- [ ] ~10-12 new tool tests
- [ ] ~12-15 new API tests
- [ ] ~4-5 new SSE tests
- [ ] ~10-11 new edge case tests (expanded from 6)
- [ ] All 168 existing tests pass after Task 0 updates (0 regressions)
- [ ] All new tests pass (0 failures)
- [ ] Manual integration check completed
- [ ] Backward compatibility audit completed
- [ ] Total: ~65-80 new tests, 233-248 total tests
- [ ] **Atomic merge:** Phases 1-4 merged together (do not merge to main without Phase 4)
