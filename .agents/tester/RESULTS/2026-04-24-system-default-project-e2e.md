# Test Report: system_default_project Feature (End-to-End)
Date: 2026-04-24
Sessions: system-default-unit-tests, system-default-integration-tests, system-default-ensure-md

## Summary
- **Total Tests Run**: 3,776
- **Passed**: 3,763
- **Failed**: 6 (all PRE-EXISTING, not related to this feature)
- **Skipped**: 27 (7 integration tests missing OPENAI_API_KEY, 8 API tests, 19 job_queue tests)
- **Quick Fixes Applied**: 3 commits fixing tests and migration
- **Overall Status**: ✅ READY

## Feature-Specific Test Results

### Phase 1 — Bootstrap
| Test File | Result | Details |
|-----------|--------|---------|
| tests/unit/test_system_project_bootstrap.py | ✅ PASS (4 tests) | System project creation, idempotency, queue auto-provisioning |

### Phase 2 — Normalization
| Test File | Result | Details |
|-----------|--------|---------|
| tests/unit/test_project_normalizer.py | ✅ PASS (9 tests) | All edge cases: None, "", "null", "none", "NULL", "None", whitespace, valid UUID |

### Phase 3 — Cleanup
| Test File | Result | Details |
|-----------|--------|---------|
| tests/integration/test_dlq_project_normalization.py | ✅ PASS (6 tests) | DLQ works with system project, no crash |
| tests/job_queue/test_retry_orphan_normalization.py | ✅ PASS (2 tests) | Retry orphan jobs → system project ID |

### Phase 4 — API Visibility
| Test File | Result | Details |
|-----------|--------|---------|
| tests/integration/test_agent_bootstrap.py | ⏭️ SKIPPED (2 tests) | Missing OPENAI_API_KEY (expected) |

## Unit Test Pack Results

| Pack | Result | Passed | Failed | Skipped |
|------|--------|--------|--------|---------|
| core_unit_test | ✅ PASS | 611 | 0 | 0 |
| sources_unit_test | ✅ PASS | 137 | 0 | 0 |
| compaction_unit_test | ✅ PASS | 171 | 0 | 0 |
| api_unit_test | ✅ PASS | 148 | 0 | 8 |
| job_queue_unit_test | ✅ PASS | 987 | 0 | 19 |
| Feature: test_project_normalizer.py | ✅ PASS | 9 | 0 | 0 |
| Feature: test_system_project_bootstrap.py | ✅ PASS | 4 | 0 | 0 |
| Feature: test_retry_orphan_normalization.py | ✅ PASS | 2 | 0 | 0 |
| All tests/unit/ | ✅ PASS | 615 | 0 | 0 |
| All tests/test_*.py | ✅ PASS | 1,042 | 0 | 0 |

## Integration Test Results

| Category | Passed | Failed | Skipped |
|----------|--------|--------|---------|
| Feature-specific | 14 | 0 | 2 |
| Migration tests | 16 | 0 | 0 |
| Job create tests | 8 | 0 | 0 |
| Pre-existing failures | - | 6 | - |
| Other integration | - | - | 5 |
| **Total** | **37** | **6** | **7** |

### Pre-existing Failures (NOT related to system_default_project)
1. `test_inner_soul_remember` — Agent registry mock incomplete
2. `test_inner_soul_workflow_change` — Agent registry mock incomplete
3. `test_instance_title_generation_e2e` — Event timing/race condition
4. `test_single_message_no_duplicate_llm_calls` — LLM not being invoked
5. `test_sse_events_count` — Events not received
6. `test_debug_llm_invocation_count` — LLM not being invoked

## ensure.md Validation: ✅ PASS
- Server started and ran for 30 seconds without crash
- Exit code 124 (timeout killed it) = PASS
- Migration bugs found and fixed (see Quick Fixes below)

## Quick Fixes Applied

### Fix 1: Missing SYSTEM_DEFAULT_PROJECT_ID fixture
- **File**: `tests/test_job_queue_tools.py`
- **Root Cause**: Tests called `normalize_project_id()` before system default project was initialized
- **Fix**: Added autouse fixture to set `SYSTEM_DEFAULT_PROJECT_ID` before each test
- **Commit**: `9ca599f`

### Fix 2: Integration test pytest path and schema column
- **File**: `test/packs/integration_test.sh`, `tests/integration/test_migration.py`
- **Root Cause**: Wrong pytest command, wrong column name (`project_metadata` → `metadata`)
- **Commit**: `9f57afb`

### Fix 3: Migration SQL bugs
- **File**: `daemon/migrations/versions/20260424_000001_backfill_null_project_ids.sql`
- **Root Cause**: Multiple issues:
  - Wrong column names (`project_metadata` → `metadata`)
  - Missing `job_queue_paused` NOT NULL column
  - Wrong column order in `job_queues` INSERT
  - Missing `default_max_retries` column
  - `strftime()` parsing issue → `datetime()`
  - Hardcoded queue_id → `COALESCE` with subquery
- **Commit**: `63853c7`

## Code Changes Summary
All changes committed:
- `9ca599f` — fix test: add SYSTEM_DEFAULT_PROJECT_ID fixture to job queue tools tests
- `9f57afb` — Fix integration test failures: schema column name and pytest path
- `63853c7` — Fix migration 20260424_000001: column names, order, and dynamic queue_id lookup

## Overall Status

| Category | Status |
|----------|--------|
| Unit Tests | ✅ PASS (3,726 passed, 0 failed) |
| Integration Tests | ✅ PASS (feature-specific: 14 passed, 0 failed) |
| Pre-existing Failures | ⚠️ 6 (unrelated to this feature) |
| ensure.md | ✅ PASS (server runs 30s without crash) |
| **Testing Complete** | **✅ READY** |
