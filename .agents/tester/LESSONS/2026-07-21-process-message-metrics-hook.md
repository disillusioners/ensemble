# Lesson: Process Message Metrics Hook — Bug Pattern & Test Architecture

**Date:** 2026-07-21
**Bug:** `total_completions` always 0 for `process_message` child tasks
**Fix:** `_record_metrics_for_task(task, succeeded)` hook in `ProcessMessageProcessor`
**Commit:** `02794c1f` (fix), `128ad317` (gap-coverage tests)

## Root Cause

`SkillMetricsService.record_task_completion` was originally only called from the job-queue `_finalize_terminal` path. However, child instances (workers, spawned agents) execute via `process_message` tasks handled by `ProcessMessageProcessor`, which bypasses the job queue entirely. As a result:
- `skill_feedback` correctly bumped `total_selections` (via the feedback path)
- But `total_completions` stayed at 0 forever (the completion hook never fired for these tasks)

This created a misleading UI metric: skills appeared to be selected but never completed.

## Fix Architecture

A single hook `_record_metrics_for_task(task, succeeded)` was added to `ProcessMessageProcessor`, firing on **3 mutually exclusive paths**:

1. **on_success callback** (line 747-748): `succeeded=True`
2. **work_fn except Exception** (line 382): `succeeded=False`
3. **post-processing error** (line 396): `succeeded=False`

**Intentionally NOT fired on:**
- `OperationCancelledError` (line 324) — pause/shutdown
- `asyncio.CancelledError` (line 339) — task cancellation
- `should_defer` requeue (line 401) — not terminal

## Key Design Decisions

### 1. Real iterations/duration (not hardcoded)
`_compute_iterations_and_duration()` computes real values:
- `duration_seconds = max(0, int((terminal_at or now) - task.created_at))`
- `iterations = count of instance queue rows with type=="agent" timestamped >= task.created_at`

This matters because the CAPTURED eligibility gate (Gate 5 in skill_metrics_service) skips capture when `iterations <= min_iter AND duration <= min_dur`. Hardcoded 0/0 would silently disable capture.

### 2. No double-counting with job-queue path
Structurally impossible for a single task to go through both paths:
- WorkerPool and JobQueue are mutually exclusive dispatchers
- Message JobItems filtered from JobQueue dispatch (`job_type != 'message'`)
- WorkerPool completes/fails tasks directly, never calls `_finalize_terminal`

### 3. Hook is async, wraps DB in asyncio.to_thread
`_record_metrics_for_task` is `async def`; sync DB helpers are wrapped in `asyncio.to_thread` (lines 478, 615). The hook swallows its own exceptions (metrics failure must not crash the task processor).

## Test Architecture

### Coverage by path (14 tests total)
- **Service-layer** (6 tests): success/failure/idempotency/metadata-clearing via direct `SkillMetricsService` calls
- **Wiring** (3 developer tests + 5 gap tests): real `ProcessMessageProcessor` with spied `_record_metrics_for_task`

### Gap tests added (5)
The developer's tests covered the 3 firing paths but missed the 3 NON-firing paths + real-value verification:
1. `OperationCancelledError` → hook NOT fired
2. `asyncio.CancelledError` → hook NOT fired
3. `should_defer` requeue → hook NOT fired
4. Real iterations >= 1, duration > 0 (guards against 0/0 regression)
5. Zero iterations complement (no agent messages → iterations=0)

### PACKS.md glob gap
The new test file `tests/services/test_process_message_metrics.py` does NOT match the `skill_services_unit_test` glob (`test_skill_*.py`). A dedicated `process_message_metrics_unit_test` pack row was added to PACKS.md. Alternative: rename file to `test_skill_process_message_metrics.py`.

## Reusable Pattern

**Bug class:** Metrics/recording hooks that only fire on one of multiple task-processing paths.

**Detection approach:** When a hook is added to one task path, enumerate ALL task-processing paths (job-queue, process_message, WorkerPool, etc.) and verify the hook fires (or correctly doesn't fire) on each. Write wiring tests for both firing AND non-firing paths.

**Test template:** Use a spy/mock on the hook method itself, exercise each path via the real processor, and assert called/not-called + succeeded flag.
