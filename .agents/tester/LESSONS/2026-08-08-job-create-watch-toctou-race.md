# TOCTOU Race in job_create watch Registration

**Date:** 2026-08-08
**Branch:** `feature/job-orchestrator-fix`
**Severity:** 🔴 Critical
**Found by:** E2E worker `3e2d4356`

## Root Cause

`daemon/tools/job_queue.py` lines 329→361 has a TOCTOU race between `enqueue()` and `add_watch()`:

```python
job_item = await job_service.enqueue(...)  # dispatches job to worker pool
# worker pool may pick up and complete job HERE (fast jobs: ~4ms pickup)
watcher_repo.add_watch(job_item.job_id, current_instance_id)  # too late
```

The comment at line 344 ("job is PENDING here, no race with observer") is FALSE. The `enqueue()` call signals the worker pool immediately. For fast jobs (leader that completes in ~7s), the job can be fully processed and completed before `add_watch()` registers the watcher. When no watcher exists at completion time, `notify_watchers()` fires zero notifications — the watch is silently lost.

## Evidence

- Job `20576c7e`: task created 21:49:41.926, started 4ms later (21:49:41.930), completed 21:49:48.285
- `job_watchers` table: 0 rows (watcher never registered in time)
- `message_queue`: 0 job_event messages (notification never enqueued)
- Test `test_mock_source_job_create_and_watch` FAILS consistently because of this

## Why test_mock_source_job_continue PASSES

That test uses both `job_create(watch=true)` AND a separate explicit `watch_job()` call. The explicit `watch_job()` registers the watcher reliably, so even though `job_create`'s internal watch registration loses the race, the explicit call saves it.

## Fix

Move `watcher_repo.add_watch()` to BEFORE `job_service.enqueue()`, or use a single atomic operation that creates the job + watcher together (e.g., pass watch parameters into `enqueue()` itself).

## Resolution (2026-08-08)

**FIXED.** Developer applied the fix: pre-generate `job_id` via `uuid.uuid4()`, register watch with the pre-generated id BEFORE `enqueue()`, thread the pre-generated UUID into `enqueue(job_id=...)`, and handle idempotency dedup (re-register against actual job_id if enqueue returns a different one, clean up stale pre-gen watch).

**Verified by:**
- 3 new unit tests in `tests/test_job_queue_tools.py` (call-order assertion, watch-limit, idempotency re-register)
- E2E test: `[JOB_EVENT] completed` event now reliably reaches ari (previously never fired)
- Verification worker confirmed ordering: `add_watch` (line 345) → `enqueue` (line 347)

**Remaining note:** A separate result_summary regression (resolver returns `None`, omitting `Result:` block from event body) still blocks full e2e pass. This is a distinct bug, NOT caused by the TOCTOU fix.
