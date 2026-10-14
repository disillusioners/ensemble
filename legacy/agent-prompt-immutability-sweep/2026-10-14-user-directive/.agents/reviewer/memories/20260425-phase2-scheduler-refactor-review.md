# Phase 2 Scheduler Core Refactor Review

## Date: 2026-04-25
## Commit: 1cddc4e

## Key Findings

### 🔴 CRITICAL: Semaphore Leak on Cancellation
Both `_emit_scheduled_message()` and `_execute_trigger()` acquire semaphore in `_acquire_execution_slot()` but release manually on the early-return path (instance active check). If `asyncio.CancelledError` fires between acquisition and manual `release()`, the semaphore leaks permanently. With `max_concurrent=1`, this deadlocks the scheduler.

**Fix**: Use try-finally to guarantee release.

### 🟡 WARNING: No CancelledError Handler
`_execute_run()` catches `Exception` but not `asyncio.CancelledError` (BaseException). Currently accidentally correct (finally still runs), but should be explicit.

### 🟡 NOTE: Double-Callback Bugs
"Two double-callback bugs found and fixed during implementation" refers to bugs the author found during the NEW code structure creation, not pre-existing bugs. The `_route_via_job_queue()` re-raise pattern prevents double "failed" callbacks. Second bug is implicit in the restructuring itself — separating the methods removed the nested try-except pattern that was the root cause.

### ✅ Verified Clean
- Method decomposition is well-done
- Constants are well-named
- store_responses fully removed
- Shared helper correct for both paths
- Queue routing correctly gated by trigger_type
