# Phase 4: Cascade & Hierarchy

## Objective

Ensure pause/resume operations correctly cascade through the parent-child instance hierarchy, propagating job and task state changes to all descendants. Pausing a parent pauses all children's jobs/tasks; resuming a parent resumes all children's jobs/tasks.

## Coupling

- **Depends on**: Phase 2 (Pause Flow), Phase 3 (Resume Flow)
- **Coupling type**: loose
- **Shared files with other phases**: `daemon/services/instance_lifecycle.py`
- **Shared APIs/interfaces**: `pause_instance_cascade()`, `resume_instance_cascade()`, `get_tree_ids()`
- **Why this coupling**: Extends the pause/resume logic from Phases 2-3 to the hierarchy traversal. Depends on interfaces, not core implementation details.

## Context

- Phases 2 and 3 implemented the core pause/resume job/task transitions
- The current cascade uses `get_tree_ids(root_id)` for downward DFS traversal
- Current cascade already handles instance status correctly
- We need to extend it to ensure job/task transitions happen for ALL nodes in the tree
- The current `_pause_cascade_db_sync` and `_resume_cascade_db_sync` do batched UPDATEs — they need to handle job/task transitions for all tree nodes

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Extend `_pause_cascade_db_sync` for job/task transitions | The current batched UPDATE sets instance status for all tree_ids. Extend to also transition jobs (PROCESSING → PAUSED) and tasks (RUNNING → PAUSED) for ALL instances in tree_ids. Do this in the same WriteGuardSession. | `daemon/services/instance_lifecycle.py` (`_pause_cascade_db_sync`) |
| 2 | Extend `_resume_cascade_db_sync` for job/task transitions | Similarly, extend the resume batched UPDATE to transition jobs (PAUSED → PROCESSING) and tasks (PAUSED → PENDING) for all instances in tree_ids. | `daemon/services/instance_lifecycle.py` (`_resume_cascade_db_sync`) |
| 3 | Verify resume "silent" vs "non-silent" for children | Current resume: target instance gets `silent=False` (processes user message), children get `silent=True` (resume from checkpoint). Verify this still works with PAUSED job transitions. | `daemon/services/instance_lifecycle.py:1056-1180` |
| 4 | Verify `resume_processing_job` called for all tree nodes | The resume endpoint loops over `resumed_ids` calling `resume_processing_job()`. Verify it correctly handles each node's PAUSED → PROCESSING job transition. | `daemon/routers/instances.py:255-261`, `daemon/manager.py:2589` |
| 5 | Test pause from child pauses only that subtree | When pausing a child (not root), only the child's subtree should be paused — not the parent. Verify the cascade correctly limits to the subtree rooted at the child. | Tests in `tests/unit/test_tree_aware_pause_resume.py` |
| 6 | Test resume from child does NOT resume parent | Resuming a child should NOT resume the parent. The parent stays paused. Verify the resume cascade only touches descendants of the resumed instance. | Tests in `tests/unit/test_tree_aware_pause_resume.py` |
| 7 | Handle mixed states in cascade | What if some children are already PAUSED and some are RUNNING when pausing the parent? The cascade should pause all of them (idempotent for already-paused). Verify the batched UPDATE handles this. | `daemon/services/instance_lifecycle.py` |
| 8 | Handle partial-tree pause with running children (C3 scenario) | When parent is paused but some children are still running (partial tree), children continue completing and registering FIRED watchers on the paused parent. Verify the compaction hook (Phase 2 Task 4) cleans these on resume. Verify children's PROCESS_REPORT tasks accumulate in PENDING and are processed after parent resumes. | `daemon/services/instance_lifecycle.py`, `daemon/services/child_reports.py` |
| 9 | Update tree-aware tests | Update `tests/unit/test_tree_aware_pause_resume.py` (10 tests) to assert job/task PAUSED states in addition to instance states. | `tests/unit/test_tree_aware_pause_resume.py` |
| 10 | Test deep hierarchy pause/resume | Test with 3+ levels of hierarchy (grandparent → parent → child). Pause grandparent → all descendants pause. Resume grandparent → all descendants resume. | `tests/unit/test_pause_instance_cascade.py` |

## Key Files

- `daemon/services/instance_lifecycle.py` — `pause_instance_cascade()` (line 924), `resume_instance_cascade()` (line 1056), `_pause_cascade_db_sync`, `_resume_cascade_db_sync`, `get_tree_ids()`
- `daemon/repositories/instance/repository.py` — `get_tree_root_id()`, `get_tree_ids()`, `get_ancestor_ids()`
- `daemon/routers/instances.py` — Pause endpoint (line 247), Resume endpoint (line 255-261)
- `daemon/manager.py` — `resume_processing_job()` (line 2589)

## Constraints

- Cascade is downward only (parent → children). No upward or sideways propagation.
- Pausing a child only pauses that child's subtree.
- Resuming a child only resumes that child's subtree. Parent remains paused.
- All transitions in a single transaction (atomic batched UPDATE).
- The `get_tree_ids()` DFS traversal is the canonical source of the tree.

## Deliverables

- [ ] Pausing parent cascades job/task PAUSED transitions to all descendants
- [ ] Pausing child only pauses that child's subtree (parent NOT paused)
- [ ] Resuming parent cascades job/task PROCESSING/PENDING transitions to all descendants
- [ ] Resuming child only resumes that child's subtree (parent stays paused)
- [ ] Deep hierarchy (3+ levels) pause/resume works correctly
- [ ] Mixed states handled (idempotent for already-paused)
- [ ] Tree-aware tests updated with job/task assertions

## Notes

- The existing `_pause_cascade_db_sync` already does a single batched UPDATE for instances. We're extending it to include jobs and tasks in the same transaction.
- The SQL pattern would be something like:
  ```sql
  -- Instance status (existing)
  UPDATE instances SET status = 'paused', paused_at = :now
  WHERE instance_id IN (:tree_ids) AND status = 'running';

  -- Job status (new)
  UPDATE job_queue_items SET status = 'paused'
  WHERE instance_id IN (:tree_ids) AND status = 'processing';

  -- Task status (new)
  UPDATE task SET status = 'paused'
  WHERE instance_id IN (:tree_ids) AND status = 'running';
  ```
- The `get_tree_ids()` function returns all node IDs in the subtree as a flat list.
