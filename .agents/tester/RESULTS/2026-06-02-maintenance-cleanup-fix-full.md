# Test Report: Maintenance Cleanup Bug Fix

**Branch:** `feature/fix-maintenance-cleanup` (commits `34279f0`, `a32ca71`)

---

## Summary

| Suite | Tests | Passed | Failed | Skipped | Status |
|-------|-------|--------|--------|---------|--------|
| test_maintenance.py | 46 | 46 | 0 | 0 | ✅ PASS |
| test_instance_cascade.py | 5 | 5 | 0 | 0 | ✅ PASS |
| core_unit_test (regression) | 662 | 662 | 0 | 0 | ✅ PASS |
| api_unit_test (regression) | 217 | 209 | 0 | 8 | ✅ PASS |
| ensure.md (dev.sh stability) | — | — | — | — | ✅ PASS |
| **Total** | **930** | **922** | **0** | **8** | ✅ **ALL PASS** |

---

## Key Scenario Verification

| # | Scenario | Tests | Status |
|---|----------|-------|--------|
| 1 | **Instance with JobWatcher rows gets fully deleted (cascade)** | `test_cascade_deletes_jobwatcher_task_event_messagequeue_hierarchy`, `test_cleanup_instance_full_sequence` | ✅ PASS |
| 2 | **Instance that resumes between query and delete is skipped (TOCTOU guard)** | `test_cleanup_instance_toctou_skips_when_no_longer_terminal`, `test_cleanup_instance_toctou_continues_when_instance_already_gone` | ✅ PASS |
| 3 | **One failure doesn't abort the batch (error isolation)** | `test_cleanup_instance_batch_continues_on_failure`, `test_error_isolation`, `test_operation_a_error_does_not_prevent_b` | ✅ PASS (3 tests) |
| 4 | **Instance record deleted before checkpoint data (step ordering)** | `test_cleanup_instance_execution_order` | ✅ PASS |

---

## Targeted Test Details

### test_maintenance.py (46/46 PASS)
Covers the full maintenance service including the new `_cleanup_instance` helper:
- `TestCleanupInstanceHelper` (8 tests) — directly tests the 3 bug fixes
- `TestCheckpointCleanupJobOrphans/Expired/HistoryCap` — existing cleanup operations
- `TestCheckpointCleanupJobErrorIsolation` — error isolation at operation level
- `TestMaintenanceServiceRegistration/Lifecycle/IsDue/IsIdle` — service lifecycle

### test_instance_cascade.py (5/5 PASS)
Integration tests with real SQLite (`PRAGMA foreign_keys=ON`):
- `test_cascade_deletes_jobwatcher_task_event_messagequeue_hierarchy` — all 5 dependent tables cleaned
- `test_cascade_does_not_touch_unrelated_rows` — scoped deletion
- `test_delete_nonexistent_instance_returns_not_found` — graceful handling
- `test_naive_instance_delete_violates_jobwatcher_fk` — proves FK constraint is real
- `test_repository_delete_succeeds_where_naive_delete_fails` — proves cascade fix works

---

## Regression Results

| Pack | Baseline | This Run | Delta |
|------|----------|----------|-------|
| core_unit_test | 662/662 | 662/662 | 0 regressions |
| api_unit_test | 209/217 (8 skip) | 209/217 (8 skip) | 0 regressions |

---

## ensure.md Validation
- **dev.sh**: Ran stable for 30 seconds on `feature/fix-maintenance-cleanup`
- Clean startup (v0.4.4), all subsystems initialized (RAG, MCP, workers, maintenance)
- Graceful shutdown via SIGTERM
- Port 8079 freed after test
- **Result**: ✅ PASS

---

## Quick Fixes Applied
None — all tests passed on first run.

---

## Overall Status: ✅ READY

All 3 claimed bugs are verified as fixed:
1. **Instance records now deleted** — `_cleanup_instance` calls `instance_repo.delete()` 
2. **JobWatcher FK cascade** — `_cascade_instance_deps` handles JobWatcher before other dependents
3. **Function names/logs corrected** — `_get_terminal_instances_ordered_by_age`, accurate log messages

No regressions. Safe to merge.