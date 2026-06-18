# Test Report: Revived Instance Stale Job Lookup Fix

Date: 2026-06-18T13:28:27Z
Branch: `fix/revive-stale-job-lookup` (commits b1218739, 9376ab4d, 82182b26)
Sessions: `targeted-fix-verify`, `full-regression`, `failure-analysis`, `ensure-validation`

## Summary
- **Overall Status**: ✅ **READY FOR MERGE**
- All 6 requirement categories VERIFIED with passing tests
- Zero regressions from the bug fix in affected areas
- ensure.md: 4/4 Critical PASS, 2/2 Important PASS
- 1 new test added (Category 6 atomicity), 1 test fix applied
- 42 pre-existing failures (unrelated — projects table fixture, config drift, port collision)

## Requirement Categories (6/6 VERIFIED)

### Category 1 — Root cause fix (repository): ✅ COVERED
**File:** `tests/job_queue/test_task_queue_repository.py`
- `test_get_by_instance_returns_most_recent_when_multiple_exist` — newest wins
- `test_get_by_instance_orders_by_created_at_desc_across_statuses` — created_at DESC
- `test_get_by_instance_excludes_soft_deleted` — deleted_at IS NULL filter
- `test_get_by_instance_breaks_ties_with_job_id_when_created_at_identical` — job_id tie-breaker
- `test_get_active_returns_pending_job` / `_returns_processing_job` — only active statuses
- `test_get_active_returns_only_active_across_mixed_statuses` — excludes COMPLETED/FAILED/CANCELLED
- `test_get_active_returns_none_when_no_active_exists` — graceful None
- `test_get_active_returns_none_for_unknown_instance` — unknown instance
- `test_get_active_excludes_soft_deleted` — soft-delete filtered
- `test_get_active_chooses_active_over_stale_when_same_instance` — active over stale

### Category 2 — Defense-in-depth (feedback observer): ✅ COVERED
**File:** `tests/test_observer_correlation.py`
- `test_callback_finalizes_when_get_by_instance_returns_processing` — happy path
- `test_callback_re_queries_and_finalizes_active_when_stale_job_returned` — defensive re-query
- `test_callback_skips_when_no_active_job_exists` — None graceful
- `test_callback_skips_when_active_job_is_pending_not_processing` — PENDING safe
- `test_process_event_re_queries_and_finalizes_active_when_stale_job_returned` — Gap 1 fix
- `test_process_event_bails_out_when_no_active_job_exists` — _process_event defense
- `test_register_during_llm_fetch_aborts_terminal_transition` — C1 TOCTOU re-check

### Category 3 — CM cleanup on terminate: ✅ COVERED
**Files:** `tests/services/test_instance_lifecycle_terminate.py`, `tests/test_correlation_manager.py`
- `test_terminate_clears_correlation_manager_state_for_instance` — _pending + _locks cleared
- `test_terminate_succeeds_when_correlation_manager_is_none` — graceful when CM absent
- `test_terminate_handles_correlation_manager_failure_gracefully` — failure isolation
- `test_terminate_resets_waiting_for_to_zero_on_instance_repo` — waiting_for=0 reset
- `TestClearForInstance` (6 tests) — direct clear_for_instance unit tests

### Category 4 — send_message guard: ✅ COVERED
**File:** `tests/tools/test_send_message_status_guard.py`
- `test_send_message_rejects_terminated_instance`
- `test_send_message_rejects_errored_instance`
- `test_send_message_accepts_idle_instance`
- `test_send_message_accepts_running_instance`
- `test_send_message_rejects_when_terminated_check_runs_first`
- `test_send_message_does_not_use_deprecated_terminated_key`

### Category 5 — Edge cases: ✅ COVERED
- Multiple jobs (cancelled + processing) → `test_get_active_chooses_active_over_stale_when_same_instance`
- Same-microsecond tie → `test_get_by_instance_breaks_ties_with_job_id_when_created_at_identical`
- No active job → `test_get_active_returns_none_when_no_active_exists`, `test_callback_skips_when_no_active_job_exists`
- CM disabled (None) → `test_terminate_succeeds_when_correlation_manager_is_none`, `test_lifecycle_event_with_no_cm_pending_falls_through_to_terminal`

### Category 6 — Atomicity: ✅ COVERED (GAP CLOSED)
**File:** `tests/services/test_instance_lifecycle_terminate.py`
- `test_terminate_writes_status_and_waiting_for_in_single_atomic_update` (NEW — commit 3eca1484)
  - Asserts exactly ONE `update()` call carries BOTH `status="terminated"` AND `waiting_for=0`

## ensure.md Validation Results

### Critical (4/4 PASS)
1. ✅ Non-integration tests — all fix-related files pass; 42 pre-existing failures unrelated
2. ✅ Deadlock fix tests — 11/11 passed
3. ✅ No sync DB calls on event loop — 10/10 thread-identity tests passed
4. ✅ dev.sh includes `--timeout-graceful-shutdown 10` — confirmed at line 74

### Important (2/2 PASS)
5. ✅ All async callers use await — verified 7 call sites across 4 modules
6. ✅ Parent→child→complete flow — 12/12 cascade tests passed

## Test Run Statistics

### Targeted Fix Tests (1327 tests)
- **1327 passed / 1 failed / 19 skipped / 0 errors**
- The 1 failure: `test_ensure_dev_sh_still_works` — pre-existing port collision (8079 in use), NOT fix-related

### Full Regression Suite (7650 tests)
- **7608 passed / 42 failed / 41 skipped / 5 deselected / 6 xfailed** (362.69s)
- All 42 failures pre-existing: projects table fixture issue (~10), config-default drift (~12), port collision (~1), other unrelated (~19)

## Quick Fixes Applied

| Commit | File | Fix | Root Cause |
|--------|------|-----|------------|
| d26bf795 | tests/services/test_manager.py | Updated `test_terminate_instance_success` assertion | Atomic update() changed call pattern — test expected separate update_status + update calls |
| 3eca1484 | tests/services/test_instance_lifecycle_terminate.py | NEW test: atomicity verification | Coverage gap — no test verified single update() call for status + waiting_for |

## Pre-existing Failures (NOT caused by bug fix)
- `tests/test_spawn_limit_edge_cases.py` (10) — `sqlite3.OperationalError: no such table: projects`
- `tests/test_progressive_dispatch.py` (10) — fixture/dispatch issues
- `tests/test_manager.py` ProgressiveMessageDelivery/ToolResultStreaming/ListContentStreaming (12) — fixture issues
- `tests/test_config.py` — `assert 500 == 300` (config-default drift)
- `tests/job_queue/test_jober_watch_integration.py::test_ensure_dev_sh_still_works` (1) — port 8079 in use
- `tests/test_innate_skills_refactoring.py` (3), `test_memory_integration.py`, rag config, constants, startup_integration — misc pre-existing
