# Test Report: Route HTTP API Messages Through JobQueue

**Date**: 2026-05-25
**Feature**: HTTP POST `/instances/{id}/messages` routed through JobQueue parallel queue
**Commits Tested**: `914adaa` → `ee3bdca` → `20b61f0` (feature) + `215629c` (tests) + `daf846e` (quick fixes)

## Summary

| Category | Result | Details |
|----------|--------|---------|
| New Tests | ✅ 29/29 PASS | All 10 test scenarios covered |
| Job Queue Suite | ✅ 1073/1073 PASS | +29 new tests, 19 skipped, 0 regressions |
| ensure.md | ✅ PASS | dev.sh stable 30s+ |
| Quick Fixes | 2 | api.py init order + job_type migration |

## Test Coverage by Scenario

### 1. HTTP Message → JobQueue Path (4 tests)
- ✅ MESSAGE job created with `job_type="message"`, correct `instance_id`
- ✅ Job routed to `system_parallel_queue`
- ✅ Message processed correctly
- ✅ Instance status transitions (IDLE → RUNNING → IDLE)

### 2. Concurrency Gate (3 tests)
- ✅ Only 1 MESSAGE processes per instance at a time
- ✅ 2nd message requeued to PENDING
- ✅ 2nd message processes after 1st completes

### 3. Orphan Recovery Guard (3 tests)
- ✅ MESSAGE job stuck in PROCESSING with instance gone → FAILED
- ✅ TASK jobs still re-spawned as before
- ✅ Both orphan recovery branches guarded

### 4. Cancellation (3 tests)
- ✅ PENDING MESSAGE job → CANCELLED state transition
- ✅ PROCESSING MESSAGE job → CancellationToken signaled, graceful stop
- ✅ Instance NOT terminated (only message cancelled)

### 5. Instance Termination (2 tests)
- ✅ All PENDING MESSAGE jobs cancelled on instance termination
- ✅ PROCESSING MESSAGE jobs also cancelled

### 6. Backward Compatibility (2 tests)
- ✅ Internal messages still go through WorkerPool
- ✅ `_process_message_with_tracking()` not modified

### 7. Side Effects Parity (5 tests)
- ✅ Instance status IDLE/PAUSED → RUNNING
- ✅ MESSAGE_RECEIVED event created
- ✅ `last_activity_at` + `version` updated
- ✅ SSE status change streamed
- ✅ Title generation triggered (first message)

### 8. Status Endpoint (2 tests)
- ✅ GET `/instances/{id}/messages/{id}` returns correct status
- ✅ Status reflects JobQueue state accurately

### 9. Error Handling (3 tests)
- ✅ MESSAGE job fails → transitions to FAILED
- ✅ Error event persisted to DB
- ✅ Retry mechanism works (FAILED → PENDING)

### 10. No Project Context (2 tests)
- ✅ Message without project routes to `system_parallel_queue`
- ✅ Does NOT route to FIFO queue

## Quick Fixes Applied

### Fix 1: API initialization order (`daemon/api.py`)
- **Root Cause**: `job_processor.setup_message_job_handler()` called before `job_processor` was defined
- **Fix**: Moved the call to after `JobProcessor` initialization
- **Commit**: `daf846e`

### Fix 2: Missing migration (`daemon/migrations/versions/20260524_000002_add_job_type_to_job_queue_items.sql`)
- **Root Cause**: `job_queue_items.job_type` column was missing from DB schema
- **Fix**: Created migration to ALTER TABLE and add column
- **Commit**: `daf846e`

## Test File
- **Location**: `tests/job_queue/test_message_job_queue.py`
- **Lines**: 659
- **Tests**: 29
- **Test Classes**: 10 (one per scenario)

## Regressions
- **0 regressions** — All existing tests continue to pass
- Job queue suite: 1073 passed (up from 1043, +29 new +1 phase1 repo test)

## ensure.md Validation
- ✅ dev.sh runs stable for 30+ seconds after quick fixes applied
