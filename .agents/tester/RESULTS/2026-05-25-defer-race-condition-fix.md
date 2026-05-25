# Test Report: Defer Job Race Condition Fix
**Date:** 2026-05-25
**Commit:** c4f6e17 (fix) + fead301 (tests)
**File changed:** `daemon/services/job_queue_service.py`

## Summary

| Metric | Count |
|--------|-------|
| **New tests** | 16 |
| **Passed** | 16 |
| **Failed** | 0 |
| **Existing suite** | 1089 passed, 19 skipped |
| **Regressions** | 0 |
| **Quick Fixes** | 0 |

## ensure.md Validation
- ✅ dev.sh runs stably for 30+ seconds (exit code 124 = timeout killed it = was still running)

## Bug Description

**Root Cause:** `JobFeedbackObserver._get_next_job()` called `list_pending_by_project()` and returned the first pending job directly, without checking queue type. This meant defer jobs could start while non-defer (MESSAGE) jobs were still active.

**Fix (c4f6e17):** Added `_select_next_eligible_job()` method to `JobQueueService` that:
1. Batch-fetches queue types to avoid N+1 queries
2. Checks once if non-defer work is active (`count_active_jobs_in_non_defer_queues`)
3. Returns non-defer jobs immediately; returns defer jobs only when `non_defer_active == 0`

## Test Coverage

### New File: `tests/job_queue/test_select_next_eligible_job.py` (575 lines)

#### Class 1: TestSelectNextEligibleJobBasic (4 tests)
| Test | Result | What it validates |
|------|--------|-------------------|
| `test_non_defer_job_returned_immediately` | ✅ PASS | FIFO job returned without any active check |
| `test_non_defer_job_returned_even_with_active_jobs` | ✅ PASS | Non-defer jobs not blocked by active work |
| `test_defer_job_skipped_when_non_defer_active` | ✅ PASS | **CORE RACE FIX**: defer returns None when non-defer active |
| `test_defer_job_returned_when_project_idle` | ✅ PASS | Defer job returned when count_active == 0 |

#### Class 2: TestSelectNextEligibleJobPriority (2 tests)
| Test | Result | What it validates |
|------|--------|-------------------|
| `test_high_priority_defer_skipped_when_non_defer_active` | ✅ PASS | Priority doesn't override idle check |
| `test_non_defer_preferred_over_defer_regardless_of_position` | ✅ PASS | Iteration finds non-defer behind defer |

#### Class 3: TestSelectNextEligibleJobMultipleQueues (2 tests)
| Test | Result | What it validates |
|------|--------|-------------------|
| `test_multiple_defer_queues_all_respect_idle_check` | ✅ PASS | All defer queues respect same idle check |
| `test_mixed_queues_defer_first_when_idle` | ✅ PASS | FIFO+PARALLEL+DEFER: non-defer picked first |

#### Class 4: TestSelectNextEligibleJobEdgeCases (4 tests)
| Test | Result | What it validates |
|------|--------|-------------------|
| `test_empty_pending_returns_none` | ✅ PASS | Empty list → None |
| `test_all_pending_are_defer_and_idle` | ✅ PASS | Only defer jobs, idle → first defer returned |
| `test_all_pending_are_defer_and_busy` | ✅ PASS | Only defer jobs, busy → None |
| `test_defer_job_with_no_queue_id` | ✅ PASS | queue_id=None treated as non-defer (safe) |

#### Class 5: TestGetNextJobIntegration (4 tests)
| Test | Result | What it validates |
|------|--------|-------------------|
| `test_get_next_job_with_project_id_uses_select_next_eligible` | ✅ PASS | _get_next_job(project_id=X) delegates correctly |
| `test_get_next_job_without_project_id_returns_first_pending` | ✅ PASS | _get_next_job() old path still works |
| `test_get_next_job_with_queue_id_returns_first_pending` | ✅ PASS | queue_id precedence path works |
| `test_get_next_job_integration_mixed_queues` | ✅ PASS | Full integration with mixed queue types |

## Regression Results

```
Job Queue Suite: 1089 passed, 19 skipped, 0 failures
Previous baseline: 1073 passed, 19 skipped (from test_message_job_queue run)
Delta: +16 new tests, 0 regressions
```

## Test Gap Analysis

### Covered by existing tests (processor path):
- `test_defer_queue.py::TestDeferQueueIdleCheck` — 7 tests for per-queue idle check via processor
- `test_defer_deadlock.py` — 4 tests for deadlock scenarios via processor

### Covered by NEW tests (observer path):
- `_select_next_eligible_job()` — 12 direct tests
- `_get_next_job(project_id=...)` integration — 4 tests

### Remaining gap (pre-existing, not introduced by this fix):
- `trigger_next_job_sync()` in `instance_lifecycle.py:429` has no defer check — documented as needing async migration

## Overall Status

| Check | Status |
|-------|--------|
| New Tests | ✅ 16/16 PASS |
| Regression Suite | ✅ 1089/1089 PASS (19 skipped) |
| ensure.md | ✅ dev.sh stable 30s+ |
| **Testing Complete** | ✅ **READY** |
