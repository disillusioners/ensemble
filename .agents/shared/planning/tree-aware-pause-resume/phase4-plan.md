# Phase 4: Tests

## Objective

Write comprehensive tests for the tree-aware pause/resume functionality covering tree traversal, cascade behavior, `waiting_for` semantics, and edge cases.

## Coupling

- **Depends on**: Phases 1-3 (all implementation complete)
- **Coupling type**: loose — tests exercise the public API
- **Shared files with other phases**: None (test files only)
- **Shared APIs/interfaces**: Tests call cascade functions and repository methods directly

## Context

The existing test suite should be examined first to understand patterns (fixtures, database setup, mocking strategy). Tests must cover the three layers:

1. **Repository** (Phase 1): Pure unit tests for tree helpers
2. **Lifecycle** (Phase 2): Integration-ish tests for cascade functions
3. **Router** (Phase 3): API endpoint tests

## Test Scenarios

### 4.1 Repository Tree Helpers

| # | Scenario | Method | Input | Expected |
|---|----------|--------|-------|----------|
| 1 | Single node (no parent, no children) | `get_tree_root_id` | instance_id of lone node | Returns itself |
| 2 | Chain: Root → A → B | `get_tree_root_id` | B | Returns Root |
| 3 | Chain: Root → A → B | `get_tree_root_id` | Root | Returns Root |
| 4 | Missing instance | `get_tree_root_id` | "nonexistent" | Raises ValueError |
| 5 | Single node | `get_tree_ids` | root_id of lone node | `{root_id}` |
| 6 | Tree: Root → [A, B], A → [C, D] | `get_tree_ids` | Root | `{Root, A, B, C, D}` |
| 7 | Subtree query | `get_tree_ids` | A | `{A, C, D}` (not Root, B) |
| 8 | Single node | `get_ancestor_ids` | lone node | `[]` |
| 9 | Chain: Root → A → B | `get_ancestor_ids` | B | `[A, Root]` (nearest first) |
| 10 | Chain: Root → A → B | `get_ancestor_ids` | Root | `[]` |
| 11 | Chain: Root → A → B | `get_ancestor_ids` | A | `[Root]` |

### 4.2 Pause Cascade

| # | Scenario | Input | Expected Paused | waiting_for |
|---|----------|-------|-----------------|-------------|
| 1 | Pause root of tree: Root → [A, B] | Root | {Root, A, B} | All 0 |
| 2 | Pause child of tree: Root → [A, B] | A | {Root, A, B} | All 0 |
| 3 | Pause leaf of deep tree: Root → A → B → C | C | {Root, A, B, C} | All 0 |
| 4 | Pause already-paused node | A (already paused) | {} (skipped) | N/A |
| 5 | Pause single-node tree | Lone | {Lone} | 0 |
| 6 | Multi-branch: Root → [A, B], A → [C], B → [D, E] | C | {Root, A, B, C, D, E} | All 0 |

**Key invariant**: Pause ALWAYS pauses the ENTIRE tree regardless of which node is clicked. `waiting_for = 0` for all nodes.

### 4.3 Resume Cascade + waiting_for

| # | Scenario | Input | Expected Resumed | waiting_for for ancestors | waiting_for for others |
|---|----------|-------|------------------|--------------------------|----------------------|
| 1 | Resume root of tree: Root → [A, B] | Root | {Root, A, B} | N/A (no ancestors) | All 0 |
| 2 | Resume child of tree: Root → [A, B] | A (whole tree was paused) | {Root, A, B} | Root: 1 | A: 0, B: 0 |
| 3 | Resume leaf of deep tree: Root → A → B → C | C | {Root, A, B, C} | Root: 1, A: 1, B: 1 | C: 0 |
| 4 | Resume middle of tree: Root → A → B → C | B | {Root, A, B, C} | Root: 1, A: 1 | B: 0, C: 0 |
| 5 | Resume single-node tree | Lone | {Lone} | N/A | 0 |
| 6 | Resume non-paused node | A (running) | {} (skipped) | N/A | N/A |

**Key invariant**: Resume from child → ancestors get `waiting_for = 1`. Resume from root → all get `waiting_for = 0`.

### 4.4 Router + resume_processing_job Integration

| # | Scenario | Expected |
|---|----------|
| 1 | Resume tree from root | `resume_processing_job()` called for all nodes. Target gets `silent=False`. Others get `silent=True`. |
| 2 | Resume tree from child | Same as above — `target_id` from cascade result used to determine silent flag. |
| 3 | Partial resume_processing_job failure | Other nodes still resume. Error reported in `resume_results`. |
| 4 | No PROCESSING job for a node | `resume_processing_job()` returns None gracefully. Node still marked as resumed. |

### 4.5 End-to-End Scenarios

| # | Scenario | Steps | Expected Final State |
|---|----------|-------|---------------------|
| 1 | Pause child → resume root | Pause C (in Root→A→B→C), then resume Root | After pause: all PAUSED, wf=0. After resume: all RUNNING, wf=0. |
| 2 | Pause child → resume same child | Pause C, then resume C | After pause: all PAUSED, wf=0. After resume: all RUNNING, ancestors (Root,A,B) wf=1. |
| 3 | Pause → resume → pause → resume | Full cycle twice | Idempotent — same results each time. |
| 4 | Tree with mixed statuses | Pause tree, manually complete one child, resume | Completed child is skipped (not PAUSED), others resume. |

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Check existing test patterns | Read existing test files for instances/lifecycle to understand fixtures, DB setup, mocking | tests/ |
| 2 | Write repository tree helper tests | Cover all 11 scenarios in section 4.1 | tests/repositories/test_instance_tree.py (new) |
| 3 | Write cascade function tests | Cover all scenarios in sections 4.2 and 4.3. May need to mock `_request_registry`, `_graph_tasks`, `_live_hub`, `_instance_repository` | tests/services/test_lifecycle_cascade.py (new or extend existing) |
| 4 | Write router integration tests | Cover scenarios in section 4.4. Test that `resume_processing_job` is called with correct silent flags. | tests/routers/test_instances_resume.py (new or extend existing) |
| 5 | Write end-to-end scenario tests | Cover scenarios in section 4.5 | tests/integration/ (if pattern exists) |
| 6 | Verify all existing tests still pass | Run full test suite to catch regressions | — |

## Constraints

- Follow existing test patterns (check conftest.py, fixtures)
- Mock external dependencies (LLM calls, graph execution)
- Use real database (SQLite in-memory) for repository tests
- Keep tests independent — no shared mutable state between tests
- Each test should set up its own tree structure

## Deliverables

- [ ] All repository tree helper tests passing (11 scenarios)
- [ ] All cascade function tests passing (12 scenarios)
- [ ] Router integration tests passing (4 scenarios)
- [ ] End-to-end scenario tests passing (4 scenarios)
- [ ] Existing test suite still passing (no regressions)
