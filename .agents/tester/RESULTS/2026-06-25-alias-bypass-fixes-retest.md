# Alias-Bypass Fixes Retest — 2026-06-25

**Branch**: `feature/rename-coder-to-developer`
**Commit**: `daba08ac`
**Sessions**: 3 parallel opencode sessions
**Date**: 2026-06-25T20:32 UTC

---

## Summary

| Fix | Status | Tests Run | Result |
|-----|--------|-----------|--------|
| C1: `_restore_instance()` alias resolution | ✅ PASS | 2 tests in TestRestoreInstanceWithAlias | All pass |
| C2: `job_queue_service.enqueue()` alias (2 call sites) | ❌ FAIL | 3 tests in TestJobQueueEnqueueWithAlias pass, BUT 4 existing message_job_queue tests broken by fix | 4 regressions |
| W3a: `child_reports.py` display name alias | ✅ PASS | 10 tests | All pass |
| W3b: `loader.py` tool doc loading alias | ✅ PASS | 67 tests | All pass |
| W1: 5 new crash-recovery integration tests | ✅ PASS | 5/5 tests | All pass |
| Registry alias tests | ✅ PASS | 45 tests | All pass |
| Job queue regression (enqueue paths) | ❌ 4 FAILURES | 1334 pass, 4 fail | Regression caused by C2 fix |
| Service lifecycle tests | ✅ PASS | 21 tests | All pass |

**Overall**: 4 failures caused by the C2 alias fix. The new tests pass, but the fix introduced a regression in existing message_job_queue tests.

---

## Detailed Results

### Session 1: Migration + Registry Tests

#### Migration Test Suite (`tests/unit/test_coder_developer_migration.py`)
- **Total**: 11 tests
- **Passed**: 10
- **Skipped**: 1 (PostgreSQL probe — PG unavailable, graceful skip)
- **Failed**: 0

**5 NEW crash-recovery tests — ALL PASSED:**
1. `TestRestoreInstanceWithAlias::test_restore_instance_with_coder_agent_id_does_not_raise` ✅
2. `TestRestoreInstanceWithAlias::test_restore_instance_with_developer_agent_id_still_works` ✅
3. `TestJobQueueEnqueueWithAlias::test_enqueue_with_coder_agent_id_succeeds` ✅
4. `TestJobQueueEnqueueWithAlias::test_enqueue_with_coder_and_idempotency_key_succeeds` ✅
5. `TestJobQueueEnqueueWithAlias::test_enqueue_with_developer_agent_id_still_works` ✅

**Original 6 migration tests**: All passed (5 SQLite + 1 PG probe-skipped).

#### Registry Alias Tests (`tests/test_registry.py`)
- **Total**: 45 tests
- **Passed**: 45
- **Failed**: 0

**4 alias backward-compatibility tests — ALL PASSED:**
1. `TestAgentIdAliasBackwardCompatibility::test_resolve_pure_id_alias` ✅
2. `TestAgentIdAliasBackwardCompatibility::test_resolve_path_to_id_alias` ✅
3. `TestAgentIdAliasBackwardCompatibility::test_exists_alias` ✅
4. `TestAgentIdAliasBackwardCompatibility::test_instance_create_normalizes_alias` ✅

---

### Session 2: Job Queue Regression Tests

#### Main Job Queue Tests (`tests/job_queue/`)
- **Total**: 1376 tests
- **Passed**: 1334
- **Skipped**: 38
- **Failed**: 4

**4 FAILURES — ALL caused by the C2 alias fix:**

| # | Test | Error |
|---|------|-------|
| 1 | `TestHttpMessageJobQueuePath::test_http_message_routes_to_parallel_queue` | `MagicMock` not supported by SQLite |
| 2 | `TestHttpMessageJobQueuePath::test_http_message_full_flow` | Same |
| 3 | `TestNoProjectContext::test_message_job_no_project_routes_to_system_parallel` | Same |
| 4 | `TestNoProjectContext::test_message_job_default_project_queue_type` | Same |

**Root Cause**: The C2 fix adds `registry.resolve_pure_id()` call in `job_queue_service.enqueue()`. Existing tests in `test_message_job_queue.py` mock `registry.get()` but do NOT mock `resolve_pure_id()`. The returned `MagicMock` object is passed as `agent_id` into SQL INSERT, which SQLite rejects with `ProgrammingError: Error binding parameter 2: type 'MagicMock' is not supported`.

**Fix needed**: Update test fixtures in `tests/job_queue/test_message_job_queue.py` to also mock `registry.resolve_pure_id()` to return a string (e.g., `"developer"` or the test's expected agent_id).

#### Phase 6 Dispatch Tests (`tests/test_enqueue_shared.py` + `test_message_job_queue.py`)
- **Passed**: 55 (test_enqueue_shared.py all pass)
- **Failed**: 4 (same message_job_queue tests as above)

#### Service Lifecycle Tests (`tests/services/`)
- **Total**: 35 tests
- **Passed**: 21
- **Skipped**: 14
- **Failed**: 0

---

### Session 3: Child Reports + Loader Tests

#### Child Reports Tests (W3a — display name fix)
**File**: `tests/unit/services/test_child_reports.py`
- **Total**: 10 tests
- **Passed**: 10
- **Failed**: 0

#### Loader Tests (W3b — tool doc loading fix)
**File**: `tests/test_loader.py`
- **Total**: 67 tests
- **Passed**: 67
- **Failed**: 0

#### Full `tests/unit/services/` directory
- **Total**: 391 tests
- **Passed**: 391
- **Failed**: 0

---

## Combined Totals

| Category | Passed | Failed | Skipped |
|----------|--------|--------|---------|
| Migration suite | 10 | 0 | 1 |
| Registry suite | 45 | 0 | 0 |
| Job queue regression | 1334 | **4** | 38 |
| Service lifecycle | 21 | 0 | 14 |
| Child reports | 10 | 0 | 0 |
| Loader | 67 | 0 | 0 |
| **Total** | **1487** | **4** | **53** |

---

## Conclusion

4 out of 5 fixes (C1, W3a, W3b, W1) are clean — all their tests pass.

**C2 fix (job_queue_service.enqueue alias resolution)** introduces 4 regressions in `tests/job_queue/test_message_job_queue.py`. The new crash-recovery tests for C2 pass, but existing message_job_queue tests have incomplete mock setup — they mock `registry.get()` but not `registry.resolve_pure_id()`.

**Quick fix eligible**: The fix is to update 4 test fixtures to mock `resolve_pure_id()`. This is < 20 lines, single file, obvious root cause.
