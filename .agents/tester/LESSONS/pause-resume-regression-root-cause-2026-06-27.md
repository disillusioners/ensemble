# Pause/Resume Regression Root Cause — E2E Caught What Unit Tests Missed

**Date**: 2026-06-27
**Branch**: `feature/migration-followups`
**Commits**: `c35c46b0`, `053140a9`, `677599d2`

## The Bug
After architecture migration (D11+D13), parent instance stuck at `waiting_children` after pause→resume→child-completion. Parent's pending message phantom-completed without LLM call.

## Root Cause (3 layers, fixed bottom-up)
1. **SYMPTOM (Part A, `c35c46b0`)**: Resume cleanup marked freshly-claimed PROCESS_REPORT messages as COMPLETED
2. **SYMPTOM (Part B, `053140a9`)**: Missing deferred finalize safety net in `_process_resume_finalize`
3. **ROOT CAUSE (`677599d2`)**: `find_paused_or_running_by_instance` only looked for `PAUSED/RUNNING` status. But `_resume_cascade_db_sync` transitions tasks **PAUSED → CANCELLED**. So `resume_processing_job` found no task → misrouted root instance to WorkerPool child path → stale messages stayed in queue → parent wedged at `waiting_children`.

## Why Unit Tests Missed It
The unit test `test_pause_resume_root.py` had **codified the broken routing as expected behaviour**: `assert routed_after_resume is None`. This assertion documented the bug as "working as designed." The E2E test ran the real workflow and caught the actual failure.

## Lesson
- **Never codify buggy behaviour as expected in tests** — if routing returns None when it should return something, that's a bug, not a feature.
- **E2E tests are essential** — they validate the full chain of real interactions that unit tests with mocked components cannot.
- **Follow the data through every state transition** — PAUSED → CANCELLED → (must be found by queries looking for paused/resumed tasks)

## The Fix
Added `TaskStatus.CANCELLED.value` to the IN clause in `find_paused_or_running_by_instance`. CANCELLED is the marker that an instance was paused-and-resumed and needs the resume cleanup path.

## Pattern to Remember
When implementing state transitions (like PAUSED → CANCELLED), ALL queries that look up tasks by status must include the new status. If `_resume_cascade_db_sync` marks tasks as CANCELLED, then any query that's supposed to find "tasks that need resume handling" must include CANCELLED.
