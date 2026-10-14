# Phase 4 Implementation — Event-Driven Dispatch & Idempotent Enqueue

## Summary
Implemented Phase 4 of the job system improvements plan. Commit: `f1addd3` (21 files, 2122 insertions, 838 tests pass).

## Key Learnings

### 1. DispatchEventBus is separate from EventBus
- `DispatchEventBus` (`daemon/services/dispatch_event_bus.py`) operates at the **JOB level** — wakes JobProcessor when new jobs are enqueued
- `EventBus` (`daemon/services/event_bus.py`) operates at the **TASK/SSE level** — publishes instance lifecycle events
- They serve different purposes and must not be confused

### 2. api.py Wiring Order is Critical
- `DispatchEventBus` must be created **BEFORE** services that depend on it
- The review caught a critical bug: `dispatch_event_bus` was `None` when assigned to `job_queue_mgmt_service._dispatch_bus` because creation happened after service setup
- **Pattern:** Create infrastructure → Wire to services → Start services

### 3. asyncio.Event Auto-Clear Pattern
- `DispatchEventBus.wait_for_job()` auto-clears the event after returning
- Without auto-clear, the processor would keep waking on stale events
- `call_soon_threadsafe` is needed for `notify_new_job()` since it can be called from sync context (e.g., scheduler)

### 4. Idempotency Key TTL Implementation
- `idempotency_key_ttl_hours` config was defined but never used initially
- Had to add `set_config()` to `JobQueueService` and implement TTL check comparing `created_at` against cutoff
- Jobs older than TTL with same key are treated as new (allows re-submission)

### 5. HTTP Status Code Change (202→201/200)
- Original create_job endpoint returned 202 for all cases
- Phase 4 spec requires 201 (new job) and 200 (existing job via idempotency)
- This is a **breaking change** for existing clients — needs documentation

### 6. Metrics Counters for Observability
- `jobs_dispatched_immediately` — event-driven wakeup (fast path)
- `jobs_dispatched_polling` — timeout fallback (slow path)
- Both tracked in `JobProcessor` for monitoring dispatch efficiency

## Architecture
```
enqueue() → notify_new_job(project_id) → DispatchEventBus → JobProcessor wakes
                                                              ↓
                                              process_next_job() picks up job
                                                              
RetryScheduler → finds retryable jobs → notify_new_job() → same wakeup path
Queue resume → start_queue() → notify_new_job() → same wakeup path
```
