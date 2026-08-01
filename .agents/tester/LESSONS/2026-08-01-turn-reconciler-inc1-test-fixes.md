# Quick Fix: Turn Reconciler Inc 1 Test Code Fixes
Date: 2026-08-01
Branch: `feature/turn-reconciler-named-transitions`

## Fix 1: is_background in MockRow fixtures (commit `1b2a857f`)

**File**: `tests/message_queue_redesign/test_task_retry_models.py`
**Root cause**: The `task_is_background` migration added a new required field `is_background` to the Task model. `daemon/repositories/task/repository.py:1687` reads `row.is_background` with no `hasattr` fallback. The backward-compat `MockRowOld` and `MockRowPartial` fixtures in `TestRowToTaskBackwardCompat` were never updated when the migration landed. This is the same "required field" pattern as the C2 commit that added `work_id`/`is_deferred`.
**Fix**: Added `self.is_background = False` to both MockRowOld (line ~395) and MockRowPartial (line ~438) `__init__` methods. 4 net added lines, test-only.
**Impact**: `test_row_to_task_defaults_for_missing_fields` and `test_row_to_task_partial_new_fields` now pass.
**Unrelated to turn-reconciler**: This is a pre-existing test fixture bug found during regression.

## Fix 2: PG test fixture + stale UPDATE 4 assertions (commit `5877c366`)

**File**: `tests/postgres/test_pause_report_orphan_reconciliation_pg.py`
**Root cause (Bug 1)**: The PG test's `_make_service` used a bare `MagicMock()` for the manager. The cascade's call `self._task_repo.reconcile_turn_mirror(work_id)` hit a MagicMock no-op instead of the real `TaskRepository`. The matching SQLite `lifecycle_service` fixture does `manager._task_repo = TaskRepository(engine=engine)` — the PG fixture did not.
**Root cause (Bug 2)**: After Turn-Reconciler Increment 1, `reconcile_turn_mirror` no longer carries the `state.work_id <> ct.work_id` exclusion. The SQLite tests were updated to reflect this; the PG tests were not.
**Fix**: (1) Added `manager._task_repo = TaskRepository(engine=engine)` to `_make_service` (mirrors SQLite fixture). (2) Updated `test_pg_update4_preserves_mixed_terminal_live` and `test_pg_two_connection_race_with_concurrent_live_insert` assertions to match new reconciler behavior (orphan IS reconciled). +48/-14 lines, test-only.
**Impact**: All 4 initially-failing PG tests now pass. 153 passed, 0 failed.
**Related to turn-reconciler**: Bug 1 was masking the reconciler behind a mock. Bug 2 was stale test expectations not updated during the Inc 1 implementation.
