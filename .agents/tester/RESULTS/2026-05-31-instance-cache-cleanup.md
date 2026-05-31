## Test Report: Instance Cache Cleanup (feature/instance-cache-cleanup)
Date: 2026-05-31T18:46+07:00
Sessions: cache-cleanup-tests (ses_18232c981ffezFXLuysBIB7N3f), cache-cleanup-regression (ses_18232c96dffeV7GenlI2WHEapn), cache-cleanup-ensure (ses_182255b56ffeomSZDefZ1vEiji)

### Summary
- **Targeted Tests**: 26/26 PASS ✅
- **Regression Suite**: 5114 passed, 20 pre-existing failures, 0 new regressions ✅
- **ensure.md (dev.sh)**: PASS ✅
- **Quick Fixes Applied**: 3 (2 test fixes + 1 conftest fix)

### Changes Under Test
1. Instance cache cleanup covers ALL terminal states (COMPLETED, ERROR, TERMINATED, FAILED, PAUSED)
2. TTL changed from 30 minutes → 4 hours
3. Method renames: `_cleanup_paused_instances()` → `_cleanup_cached_instances()`, `release_paused_instance()` → `_release_cached_instance()`

---

### Targeted Test Results (26/26 PASS)

| # | Test | Class | Result |
|---|------|-------|--------|
| 1 | `test_release_cached_instance_removes_from_memory` | TestReleaseCachedInstance | ✅ PASS |
| 2 | `test_release_cached_instance_cancels_graph_task` | TestReleaseCachedInstance | ✅ PASS |
| 3 | `test_release_cached_instance_idempotent` | TestReleaseCachedInstance | ✅ PASS |
| 4 | `test_release_cached_instance_cancels_requests` | TestReleaseCachedInstance | ✅ PASS |
| 5 | `test_release_cached_instance_skips_done_task` | TestReleaseCachedInstance | ✅ PASS |
| 6 | `test_cleanup_calculates_expired_instances_correctly` | TestCleanupCachedInstances | ✅ PASS |
| 7 | `test_cleanup_releases_expired_paused_instances` | TestCleanupCachedInstances | ✅ PASS |
| 8 | `test_cleanup_skips_recent_paused_instances` | TestCleanupCachedInstances | ✅ PASS |
| 9 | `test_cleanup_skips_recent_paused_instances_with_explicit_time` | TestCleanupCachedInstances | ✅ PASS |
| 10 | `test_cleanup_skips_instances_not_in_memory` | TestCleanupCachedInstances | ✅ PASS |
| 11 | `test_cleanup_handles_invalid_updated_at` | TestCleanupCachedInstances | ✅ PASS |
| 12 | `test_cleanup_handles_multiple_instances` | TestCleanupCachedInstances | ✅ PASS |
| 13 | `test_cleanup_releases_expired_by_status[completed]` | TestCleanupCachedInstances | ✅ PASS |
| 14 | `test_cleanup_releases_expired_by_status[error]` | TestCleanupCachedInstances | ✅ PASS |
| 15 | `test_cleanup_releases_expired_by_status[terminated]` | TestCleanupCachedInstances | ✅ PASS |
| 16 | `test_cleanup_releases_expired_by_status[failed]` | TestCleanupCachedInstances | ✅ PASS |
| 17 | `test_cleanup_releases_expired_by_status[paused]` | TestCleanupCachedInstances | ✅ PASS |
| 18 | `test_cleanup_handles_empty_list` | TestCleanupCachedInstances | ✅ PASS |
| 19 | `test_hot_resume_within_ttl` | TestHotColdResume | ✅ PASS |
| 20 | `test_cold_resume_after_ttl_concept` | TestHotColdResume | ✅ PASS |
| 21 | `test_instance_cache_ttl_hours` | TestTTLConstants | ✅ PASS |
| 22 | `test_cold_resume_flow_end_to_end` | TestColdResume | ✅ PASS |
| 23 | `test_get_instance_hot_path_skips_restore` | TestColdResume | ✅ PASS |
| 24 | `test_get_instance_cold_resume_triggers_restore` | TestColdResume | ✅ PASS |
| 25 | `test_pause_single_sets_paused_at_field` | TestPausedAtField | ✅ PASS |
| 26 | `test_paused_at_cleared_on_resume` | TestPausedAtField | ✅ PASS |

### Regression Results
- **Core tests (manager + cache cleanup)**: 100 passed ✅
- **Broader suite**: 5114 passed, 20 failed (pre-existing), 34 skipped
- **New regressions**: 0 ✅

Pre-existing failures (not caused by this branch):
- test_live_event_hub.py (9), test_progressive_dispatch.py (4), test_sources_registry.py (2), test_invoked_as_tool.py (2), test_title_generation_trigger.py (1), test_worker_timeout.py (1), test_config.py (1)

### ensure.md Validation
- dev.sh ran for 30 seconds without crash → **PASS** ✅

### Quick Fixes Applied

1. **test_cleanup_releases_expired_paused_instances** (commit `568d80e`)
   - Root cause: Test asserted time math but never called `_cleanup_cached_instances()`
   - Fix: Added full cleanup invocation with proper timezone-aware datetime format

2. **test_cleanup_handles_multiple_instances** (commit `568d80e`)
   - Root cause: Same — only asserted arithmetic, never invoked cleanup method
   - Fix: Added full cleanup invocation with 3 instances (2 expired, 1 recent)

3. **conftest.py mock injection** (commit `757a2ee`)
   - Root cause: `if key not in sys.modules` guard prevented mocks from overriding pre-imported modules
   - Fix: Always inject mocks regardless of prior sys.modules presence

### Test Quality Assessment ✅
- **Per-status parametrize**: Actually runs 5 separate tests (completed, error, terminated, failed, paused)
- **TTL = 4 hours**: Asserted in `test_instance_cache_ttl_hours`
- **Cold resume**: Verified via `_restore_instance` call
- **Cleanup loop execution**: Now genuinely exercised (was vacuously passing before fixes)
- **Multi-instance selective cleanup**: 2 expired released, 1 recent kept

### Overall Status: ✅ READY
