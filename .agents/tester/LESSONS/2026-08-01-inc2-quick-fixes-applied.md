# Quick Fixes Applied — Turn Reconciler Increment 2 Regression

**Date:** 2026-08-01
**Branch:** `feature/turn-reconciler-named-transitions`
**Commit:** `c5192f6f`

## Fix 1: Bounded Dev Server Teardown (commit `3cff7198`)
- **File:** `tests/job_queue/test_jober_watch_integration.py`
- **Root cause:** `test_ensure_dev_sh_still_works` blocked on unbounded `proc.communicate()` — reload children retaining stdout/stderr pipes caused the pack to hang
- **Fix:** Added bounded output collection and pipe cleanup after terminating the dev.sh process group
- **Worker:** job-queue-full (d1cb047d)

## Fix 2: PG Cross-System Guard Test Alignment (commit `a9806419`)
- **File:** `tests/postgres/test_report_lane_phase2_pg.py`
- **Root cause:** Increment 2 deleted the message_id-matching carve-out, replacing with work_id-keyed `_active_jobitem_with_inflight_task_sql`. The PG test still expected the old message_id-keyed behavior. The Inc 2 commit (`c5192f6f`) updated 4 of 5 affected test files but missed this PG test.
- **Fix:** Captured JobItem's `job_id`, aligned Task's `work_id` to it via Session.get UPDATE. Updated docstring to reflect new correlation axis.
- **Worker:** pg-fix-rerun (2758062f)

## Fix 3: SQLite Cross-System Guard Test Alignment (commit `e97f91bb`)
- **File:** `tests/test_report_lane_phase2.py`
- **Root cause:** Same as Fix 2 — SQLite mirror of the same stale test. The `test_process_message_blocked_by_cross_system_guard` test at line 512 failed, and `test_process_message_unblocked_when_message_id_matches` at line 523 passed for the wrong reason.
- **Fix:** Applied same pattern as PG fix (Fix 2). Also audited and updated the companion test docstring/assertion.
- **Worker:** fix-sqlite-stale-test (05596519)

## Fix 4: FakeInstance Status Default (commit `66235c31`)
- **File:** `tests/services/test_skill_metrics_service.py`
- **Root cause:** `FakeInstance` lacked `status` attribute — 10 tests in `test_process_message_metrics.py` failed with `AttributeError` when pipeline reached `_is_instance_paused` guard
- **Fix:** Added `self.status = "active"` default to `FakeInstance.__init__`
- **Worker:** sqlite-broad (ed947c80)

## Pattern: Stale Tests After Carve-Out Deletion
The most significant finding: when a large code deletion (546 lines) is committed, ALL test files that exercise the deleted behavior must be updated. The Inc 2 commit updated 4 of 5 files, missing both the SQLite and PG versions of `test_report_lane_phase2.py`. **Recommendation for Increment 3+:** when deleting guard code, use `grep -rn "<deleted_function_or_pattern>" tests/` to find ALL affected tests before committing.
