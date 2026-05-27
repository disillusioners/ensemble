# Plan Overview: Tree-Aware Pause/Resume (v3 — Definitive)

## Objective

Rewrite pause/resume cascade to operate on the **entire instance tree** (root + all descendants), not just target + children. Add ancestor `waiting_for` propagation on resume, and ensure `resume_processing_job()` is called for every node in the tree so orphaned graph tasks are re-spawned.

## Scope Assessment

**LARGE** — Changes span 3 backend files (repository, lifecycle, router) plus tests. The cascade logic rewrite is the core risk, but the domain is well-understood and all edge cases are enumerated.

## Context

- Project: agents-ensemble
- Working Directory: /Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble
- Key insight: When an instance is paused, the asyncio graph task is **killed** (cancel() → CancelledError). There is no background watcher. The job is orphaned in PROCESSING status. Just changing PAUSED→RUNNING is insufficient — `resume_processing_job()` MUST be called for every resumed node.

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Repository tree helpers | Add `get_tree_root_id()`, `get_tree_ids()`, `get_ancestor_ids()` to InstanceRepository | None | — | 1h |
| 2 | Rewrite cascade functions | Rewrite both `pause_instance_cascade()` and `resume_instance_cascade()` for full-tree operation + `waiting_for` semantics | Phase 1 | tight | 2h |
| 3 | Router integration | Ensure router calls `resume_processing_job()` for ALL nodes, correct `silent` flag, fix resume endpoint to pass `target_id` through cascade result | Phase 2 | tight | 1h |
| 4 | Tests | Tree scenarios, `waiting_for` semantics, edge cases (single node, deep tree, mid-tree pause/resume) | Phases 1-3 | loose | 2h |
| 5 | Frontend verification | Verify UI handles tree-level status changes correctly — no code changes expected | Phase 3 | loose | 0.5h |

### Coupling Assessment

| Phase Pair | Coupling | Reason |
|------------|----------|--------|
| 1 → 2 | **tight** | Phase 2 imports and calls the new repo methods from Phase 1 |
| 2 → 3 | **tight** | Router calls the rewritten cascade functions from Phase 2 |
| 3 → 4 | **loose** | Tests exercise the API but don't share implementation details |
| 3 → 5 | **loose** | Frontend just shows what the backend reports |

## Current Behavior vs Required Behavior

### Current (BROKEN)
- **Pause**: Cascades to **children only** (DFS down). If child B is paused, parent A keeps running.
- **Resume**: Cascades to **target + children** only. Ancestors, siblings, and their subtrees are unaffected.
- **waiting_for**: Always set to 0 on resume for all nodes. No upward propagation.

### Required (FIXED)
- **Pause ANY node** → find root → pause **ENTIRE tree** (root + all descendants).
- **Resume ANY node** → find root → resume **ENTIRE tree** (root + all descendants).
- **waiting_for on resume from child**: Ancestors (parent, grandparent, ..., root) get `waiting_for = 1`. Propagates upward from selected child.

## Architecture Decisions

### A1: Non-recursive cascade functions
The current recursive DFS with `_visited`/`_depth` guards is replaced with iterative set-based operations. This is simpler, easier to reason about, and avoids recursion limits.

### A2: Tree discovery in repository
Tree helpers are pure repository methods (sync, SQL-based). The lifecycle service calls them once, then operates on the resulting set.

### A3: `resume_processing_job()` for ALL resumed nodes
Every node that transitions PAUSED→RUNNING needs `resume_processing_job()`. The target gets `silent=False` (message injected), all others get `silent=True` (pure checkpoint resume).

### A4: `waiting_for` propagation only on resume from non-root
When the target IS the root → no `waiting_for` changes (stays 0).
When the target is a child → ancestors of target get `waiting_for = 1`.

## Key Files

| File | Role |
|------|------|
| `daemon/repositories/instance/repository.py` | Tree helper methods (Phase 1) |
| `daemon/services/instance_lifecycle.py` | Cascade rewrite (Phase 2) |
| `daemon/routers/instances.py` | Resume endpoint fix (Phase 3) |
| `daemon/manager.py` | `resume_processing_job()` — no changes needed |
| `daemon/repositories/instance/models.py` | Instance model — no changes needed |

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Tree traversal performance on deep trees | Low | Trees are typically <20 nodes; set-based approach is O(n) |
| `waiting_for=1` causes ancestors to wait indefinitely | High | Phase 2 must set `waiting_for=1` ONLY on ancestors, not on target or its descendants. Test thoroughly. |
| `resume_processing_job()` fails for nodes without PROCESSING job | Low | It already returns `None` gracefully when no job found |
| Race condition: pause mid-resume | Medium | Both operations are async but not concurrent per tree — consider adding tree-level lock in future |
| `_enrich_instance` replaces children field | None | We use `parent_id` traversal for ancestors, `instance_hierarchy` for descendants — both are direct DB queries |

## Success Criteria

- [ ] Pausing ANY node in a tree pauses ALL nodes in the tree (root + all descendants)
- [ ] Resuming ANY node in a tree resumes ALL nodes in the tree (root + all descendants)
- [ ] `resume_processing_job()` is called for every resumed node (target: silent=False, others: silent=True)
- [ ] Resume from child: ancestors get `waiting_for = 1`, all other nodes stay at `waiting_for = 0`
- [ ] Resume from root: `waiting_for` stays 0 for all nodes
- [ ] Pause sets `waiting_for = 0` for all nodes (existing behavior preserved)
- [ ] Single-node trees (no parent, no children) work correctly
- [ ] All existing tests continue to pass

## Tracking

- Created: 2025-05-27
- Last Updated: 2025-05-27
- Status: draft
