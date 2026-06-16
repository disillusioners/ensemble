# Phase 0: Fix Critical Bugs

## Objective
Fix the two active bugs that exist independently of the CorrelationManager migration:
1. **Race #5 (HIGH)**: `resume_processing_job` bypasses ExecutionGate, risking dual-driver checkpoint corruption
2. **JobQueue Missing Error Reporting**: `message_job_handler.py` error path omits 3 critical side-effects that WorkerPool has

These are existing production bugs that should be fixed regardless of the architectural change.

## Coupling
- **Depends on**: None
- **Coupling type**: independent
- **Shared files with other phases**: `manager.py` (Phase 4 removes `waiting_for` from here), `message_job_handler.py` (Phase 3 touches cascade logic)
- **Shared APIs/interfaces**: ExecutionGate API, EventBus event publishing
- **Why this coupling**: Phase 0 fixes the existing buggy code that later phases will refactor. Fixing bugs first ensures a stable baseline.

## Context
- These bugs are independent of the CorrelationManager architecture
- Fixing them first ensures the migration starts from a correct baseline
- Can run in parallel with Phase 1 (different files, no conflicts)

## Tasks

### Part A: Fix Race #5 — `resume_processing_job` Gate Bypass

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Wrap `_resume_processing_background` in ExecutionGate | Change `manager.py:2701-2794` so `_resume_processing_background` acquires an ExecutionGate lease before calling `_process_message_with_tracking` | `daemon/manager.py` |
| 2 | Add `LeaseContention` handling to resume path | If gate is held by another dispatcher (WorkerPool task or MESSAGE job), log and return `{"status": "already_processing"}` instead of racing | `daemon/manager.py` |
| 3 | Add `LeaseLostError` handling to resume path | On lease lost mid-resume, transition instance to ERROR and notify | `daemon/manager.py` |
| 4 | Add test: concurrent resume + MESSAGE job | Test that starting a MESSAGE job while resume is in-flight properly contends on the gate (one wins, other defers) | `tests/test_resume_gate.py` (new) |
| 5 | Add test: concurrent resume + WorkerPool task | Test that a WorkerPool task mid-flight blocks resume via gate contention | `tests/test_resume_gate.py` |
| 5b | Add test: retry limit prevents infinite loop (Fix C6) | Test that after 3 lease contentions, resume falls back to enqueue instead of retrying forever | `tests/test_resume_gate.py` |

#### Detailed Implementation for Race #5

**Current code** (`manager.py:2684-2692`):
```python
task = asyncio.create_task(self._resume_processing_background(...))
self._graph_tasks[instance_id] = task
```

**Current `_resume_processing_background`** (`manager.py:2701-2794`):
```python
async def _resume_processing_background(self, instance_id, message, message_id, ...):
    # ... line 2729: NO GATE
    result = await self._process_message_with_tracking(instance_id, message, message_id, ...)
    # ... line 2743:
    await self._process_child_completion_and_notify_parent(instance_id, message_id)
    # ...
```

**Required change**: Wrap the `_process_message_with_tracking` call in `execution_gate.run()`:

```python
async def _resume_processing_background(self, instance_id, message, message_id, ...,
                                        _retry_attempt: int = 0):
    async def _do_process():
        return await self._process_message_with_tracking(
            instance_id=instance_id, message=message, message_id=message_id,
            is_retry=True, retry_count=0, message_source="cascade_resume",
            silent=silent, images=images,
        )

    result = await self._execution_gate.run(
        instance_id=instance_id,
        holder_id=f"resume:{message_id}",
        holder_kind=LeaseHolderKind.MESSAGE_JOB.value,  # reuse existing kind
        work_fn=_do_process,
    )

    if isinstance(result, LeaseContention):
        MAX_RESUME_RETRIES = 3
        BACKOFF_DELAYS = [0.5, 1.0, 2.0]  # seconds (Fix C6)

        if _retry_attempt < MAX_RESUME_RETRIES:
            delay = BACKOFF_DELAYS[min(_retry_attempt, len(BACKOFF_DELAYS) - 1)]
            logger.warning(
                f"resume_processing_job: instance {instance_id[:8]}... lease held by "
                f"{result.holder_kind}:{result.holder_id[:8]}... — "
                f"retry {_retry_attempt + 1}/{MAX_RESUME_RETRIES} after {delay}s"
            )
            await asyncio.sleep(delay)
            # Recursive retry with incremented attempt counter
            await self._resume_processing_background(
                instance_id, message, message_id, ..., _retry_attempt=_retry_attempt + 1
            )
        else:
            logger.error(
                f"resume_processing_job: instance {instance_id[:8]}... lease contention "
                f"after {MAX_RESUME_RETRIES} retries — falling back to enqueue"
            )
            # Final fallback: enqueue for WorkerPool to pick up later
            await self.enqueue_message(instance_id, message, source="resume_exhausted")
        return

    # ... continue with _process_child_completion_and_notify_parent ...
```

**Key design decisions:**
- **Reuse `LeaseHolderKind.MESSAGE_JOB`**: No new enum value needed; resume is semantically a message job
- **Bounded retry with exponential backoff (Fix C6)**: 3 retries with 0.5s, 1s, 2s delays. If still contended after 3 retries, fall back to enqueue (WorkerPool path will eventually acquire the gate). This prevents infinite re-enqueue loops.
- **Keep `_graph_tasks[instance_id]` tracking**: This still prevents concurrent resume calls from stacking up

### Part B: Fix JobQueue Missing Error Reporting

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 6 | Extract error handling to shared helper | Create `_handle_message_processing_error()` method that encapsulates: error event creation, lifecycle event publish, error report to parent, job completion | `daemon/services/message_job_handler.py` |
| 7 | Wire shared helper into WorkerPool path | Refactor `task_processor.py:412-465` to use the shared helper (or call the same service method) | `daemon/services/task_processor.py` |
| 8 | Wire shared helper into JobQueue path | Replace `message_job_handler.py:408-416` bare `complete_job(FAILED)` with the shared helper call | `daemon/services/message_job_handler.py` |
| 9 | Fix `retry_count` hardcoded to 0 | Read `retry_count` from job metadata instead of hardcoding `0` at `message_job_handler.py:190` | `daemon/services/message_job_handler.py` |
| 10 | Add test: JobQueue path triggers error cascade | Test that a child failing through JobQueue path sends error report to parent, emits lifecycle event, writes error event to DB | `tests/test_jq_error_reporting.py` (new) |

#### Detailed Implementation for Error Reporting Fix

**Create shared error handler** — extract the WorkerPool error block pattern into a reusable method.

**Current divergence:**

| Behavior | WorkerPool (`task_processor.py`) | JobQueue (`message_job_handler.py`) |
|----------|----------------------------------|--------------------------------------|
| Error event in DB | ✅ `create_error_event` (line 417-436) | ❌ Missing |
| Lifecycle event publish | ✅ `_publish_instance_lifecycle_event(status="error")` (line 440-451) | ❌ Missing |
| Error report to parent | ✅ `_send_error_report(...)` (line 456-465) | ❌ Missing |
| Error type classification | ✅ `_classify_error_type(e)` (line 461) | ❌ Missing |
| Job completion | N/A (task completion) | ✅ `complete_job(FAILED)` only (line 408-416) |

**Recommended approach:**

Create a method on `InstanceManager` (or a shared service) that both paths call:

```python
# daemon/services/message_processing_errors.py (new file)
async def handle_message_processing_error(
    instance_manager,
    instance_id: str,
    error: Exception,
    message_id: str | None = None,
    job_id: str | None = None,       # JobQueue path
    task_id: str | None = None,      # WorkerPool path
) -> None:
    """
    Unified error handling for message processing failures.
    Both WorkerPool and JobQueue paths call this to ensure identical
    error side-effects: error event, lifecycle event, parent report.
    """
    error_type = _classify_error_type(error)

    # 1. Error event in DB
    if instance_manager._event_bus:
        await instance_manager._event_bus.create_error_event(instance_id, error=str(error))

    # 2. Lifecycle event publish (triggers JobFeedbackObserver cascade)
    if instance_manager._events_service:
        await instance_manager._events_service._publish_instance_lifecycle_event(
            instance_id=instance_id,
            status="error",
            error=str(error),
            parent_id=...,  # from instance repo
        )

    # 3. Error report to parent (triggers parent cascade)
    await instance_manager._send_error_report(
        instance_id=instance_id,
        error=str(error),
        error_type=error_type,
        message_id=message_id,
    )

    # 4. Job completion (JobQueue path only)
    if job_id and instance_manager._job_queue_service:
        await instance_manager._job_queue_service.complete_job(
            job_id, demand_state=DemandState.FAILED, error=str(error),
        )
```

**Key design decisions:**
- **Shared function, not inheritance**: Both `ProcessMessageProcessor` and `MessageJobHandler` are different classes; a shared function avoids fragile inheritance.
- **`_classify_error_type` moves to shared module**: Currently at `task_processor.py:37-108`; should be importable by both paths.
- **Job completion stays in caller**: The function doesn't force `complete_job` — WorkerPool completes the task differently. Only JobQueue needs `complete_job`.

## Key Files

| File | Purpose |
|------|---------|
| `daemon/manager.py:2553-2794` | `resume_processing_job` + `_resume_processing_background` — Race #5 fix |
| `daemon/services/message_job_handler.py:408-416` | Error handling block — missing 3 side-effects |
| `daemon/services/task_processor.py:37-108, 412-465` | Error handling reference implementation + `_classify_error_type` |
| `daemon/services/execution_gate.py:336-446` | `ExecutionGateService.run()` — the gate API to use |
| `daemon/services/error_reporting.py:79-361` | `_send_error_report` — the cascade logic called by error handler |
| `daemon/services/event_bus.py:155-191` | `create_event` / `create_error_event` — event publishing |

## Constraints
- Must not change ExecutionGate API (it's the cleanest pattern in the system)
- Must support both SQLite and PostgreSQL (gate uses `INSERT ... ON CONFLICT DO NOTHING` — already portable)
- `_graph_tasks` tracking must be preserved for cancellation support
- `_classify_error_type` must remain pure (no side-effects) so it's safely shareable

## Verification Strategy

### Part A (Race #5)
1. **Unit test**: Create a mock ExecutionGate that returns `LeaseContention`; verify `_resume_processing_background` defers correctly
2. **Integration test**: Start a WorkerPool task for instance X, then call `resume_processing_job(X)` — verify resume defers and re-enqueues
3. **Negative test**: Remove the gate wrapping, verify test fails (proves test catches the bug)
4. **Log verification**: Check logs show `lease held by task:... — deferring resume`

### Part B (Error Reporting)
1. **Unit test**: Mock an exception in `MessageJobHandler.handle()`, verify `create_error_event`, `_publish_instance_lifecycle_event`, and `_send_error_report` are all called
2. **Integration test**: Spawn a child via JobQueue, inject failure, verify parent receives `internal_error_report:` message
3. **Log verification**: Check `error_reporting.py` cascade log (`Parent X completed after child error`) fires for JobQueue-spawned children

## Rollback Plan

### Part A
Revert `_resume_processing_background` to direct `_process_message_with_tracking` call. The gate wrapping is additive — removing it restores original behavior (including the race, but no crashes).

### Part B
Revert `message_job_handler.py` error block to bare `complete_job(FAILED)`. The shared helper is additive — the JobQueue path will simply lack error reporting again (same as current state).

**Both parts are independently revertible** since they touch different files and different concerns.

## Deliverables
- [ ] `_resume_processing_background` wrapped in `execution_gate.run()`
- [ ] `LeaseContention` handled with bounded retry (3 retries, exponential backoff) (Fix C6)
- [ ] `LeaseLostError` handled in resume path
- [ ] Shared error handling helper created
- [ ] WorkerPool error block refactored to use shared helper
- [ ] JobQueue error block updated to use shared helper
- [ ] `retry_count` read from job metadata (not hardcoded)
- [ ] Tests for gate contention on resume (including retry limit test)
- [ ] Tests for JobQueue error reporting completeness
