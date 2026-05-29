# 2025-05-29-job-cancellation-bug-on-instance-terminate.md

## Bug: Jobs not properly cancelled when main instance is terminated

### Root Cause
When `DELETE /api/instances/{id}` is called:
1. `graph_task.cancel()` is called (fire-and-forget) — raises `CancelledError` in the graph execution
2. `CancelledError` propagates through `_process_message_with_tracking()` → `handle()` → `_process_next_job()` → `_process_loop()`
3. In `handle()` (message_job_handler.py:189-216), the handler checks instance status
4. **Race condition**: Instance status is still "running" at this point (not yet "terminated"), so handler re-raises CancelledError
5. CancelledError propagates out of `_process_loop()` — kills the job processor task
6. **Job stays in PROCESSING status** — no code updates it to CANCELLED

The instance status is only set to "terminated" AFTER `graph_task.cancel()` returns, but by then the CancelledError has already been processed and re-raised.

### Key Insight: graph_task IS the _process_loop task
The `_graph_tasks[instance_id]` stores the same asyncio task as `JobProcessor._job`. So `graph_task.cancel()` cancels the entire processing loop, not just the graph.

### Sweep at step 7.6 may partially work but is unreliable
The sweep at step 7.6 in terminate_instance() calls `complete_job(CANCELLED)` for processing jobs. But:
- It runs AFTER `graph_task.cancel()` which is fire-and-forget
- There's no ordering guarantee — the sweep may run before or after the handler processes the CancelledError
- If the handler's finally block runs first, it pops the token from `_active_tokens`, and then the sweep might fail

### Fix Strategy
The fix should be in `message_job_handler.py` handle() method's CancelledError handler. When instance status is NOT paused, instead of re-raising, it should:
1. Complete the job as CANCELLED
2. Then return (not re-raise)

This way the job status is always updated regardless of the race condition.

### Files involved:
- `daemon/services/instance_lifecycle.py` — terminate_instance() 
- `daemon/services/message_job_handler.py` — handle() CancelledError handler (THE BUG LOCATION)
- `daemon/services/job_processor.py` — _process_loop() (unprotected CancelledError)
- `daemon/services/instance_messaging.py` — _process_message_with_tracking() registers task in _graph_tasks

### Pause/Resume must NOT be broken:
- When paused, CancelledError handler should still leave job as PROCESSING (for resume)
- Only change behavior for non-pause cancellation cases
