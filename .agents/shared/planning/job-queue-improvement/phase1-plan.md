# Phase 1: Backend — Job Completion Callback

## Objective
Wire the instance completion lifecycle into the job queue so that jobs transition from PROCESSING → COMPLETED/FAILED when instances finish executing, error out, or are terminated.

## Coupling
- **Depends on**: None
- **Coupling type**: independent
- **Shared files with other phases**: None
- **Shared APIs/interfaces**: `JobQueueService.complete_job()` — existing method, signature extended with `result_summary` param
- **Why this coupling**: This phase calls existing public APIs on JobQueueService and adds one new public method. No interface changes affect other phases.

## Context

### The Problem (Confirmed by Code Analysis)

When a job is submitted and processed:
1. `JobProcessor._process_next_job()` spawns an instance, enqueues a message, sets status to `PROCESSING`
2. `InstanceManager._process_queue()` processes the message through LangGraph
3. On success: broadcasts `"completed"` event to frontend SSE, sends completion report to parent — **but NEVER calls `complete_job()`**
4. On failure (max retries): marks message as failed, sends error report — **but NEVER calls `complete_job()`**
5. On terminate: releases locks via `release_locks_by_instance_sync()` — **but NEVER calls `complete_job()`**
6. **Result**: Jobs stay in PROCESSING state forever

### The Existing Infrastructure (Good News)
- `self._job_queue_service` is already wired into `InstanceManager` (set via `set_job_queue_service()`)
- `JobQueueService.complete_job(job_id, success, error)` already exists and works — handles lock release + status update
- `JobRepository.get_by_instance(instance_id)` already exists — finds job by instance_id
- `JobQueueService.trigger_next_job(project_id)` already exists — dequeues next pending job
- The only missing piece is **calling these methods from the right places**

### What Already Works Correctly
- **`_fail_job()` DOES release locks** — confirmed at `job_queue_service.py` `_fail_job()` method: `self._lock_manager.release_sync(job.project_id, job.job_id)` is called before marking failed
- **`_complete_job()` DOES release locks** — same pattern, confirmed working
- **`complete_job()` DOES release locks** — via `self._lock_manager.release()` at line 429

### Issue with Premature `trigger_next_job()`
In `job_processor.py` `_process_next_job()`, `trigger_next_job()` is called immediately after enqueuing the message — BEFORE the current job completes. For same-project jobs, this silently fails because the lock is already held. This call should be removed; triggering the next job should happen only from the completion callback (Phase 1 Task 5).

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add public `get_job_by_instance()` to `JobQueueService` | New public method wrapping `_repository.get_by_instance()` — avoids private field access from manager | `daemon/services/job_queue_service.py` |
| 2 | Add `result_summary` parameter to `complete_job()` | Extend `complete_job()` signature to accept optional `result_summary` instead of hardcoding `"Job queued successfully"` | `daemon/services/job_queue_service.py` `complete_job()` |
| 3 | Add `_complete_job_for_instance()` helper to InstanceManager | New method that uses public `get_job_by_instance()` to find and complete the job associated with an instance | `daemon/manager.py` |
| 4 | Wire helper into `_process_queue()` — success and failure paths | Call helper after successful message processing AND after max retries exceeded | `daemon/manager.py` `_process_queue()` |
| 5 | Wire helper into `terminate_instance()` | Mark associated job as FAILED when instance is terminated | `daemon/manager.py` `terminate_instance()` |
| 6 | Remove premature `trigger_next_job()` from `job_processor.py` | Remove the no-op `trigger_next_job()` call that fires before job completes | `daemon/services/job_processor.py` `_process_next_job()` |

## Detailed Implementation

### Task 1: Add `get_job_by_instance()` to JobQueueService

**File**: `daemon/services/job_queue_service.py`

**Add new public method**:
```python
async def get_job_by_instance(self, instance_id: str) -> Optional[JobItem]:
    """Look up a job by its associated instance ID.
    
    Used by InstanceManager to find jobs when instances complete.
    
    Args:
        instance_id: The instance ID to search for.
        
    Returns:
        JobItem if found, None otherwise.
    """
    return await asyncio.to_thread(self._repository.get_by_instance, instance_id)
```

This provides a clean public API — no private `_repository` access from outside the service.

### Task 2: Add `result_summary` Parameter to `complete_job()`

**File**: `daemon/services/job_queue_service.py` — `complete_job()` method

**Current signature**:
```python
async def complete_job(
    self,
    job_id: str,
    success: bool = True,
    error: Optional[str] = None,
) -> Optional[JobItem]:
```

**Updated signature**:
```python
async def complete_job(
    self,
    job_id: str,
    success: bool = True,
    error: Optional[str] = None,
    result_summary: Optional[str] = None,
) -> Optional[JobItem]:
```

**Updated success path** (inside `complete_job()`):
```python
if success:
    summary = result_summary or "Job completed successfully"
    return await asyncio.to_thread(self._repository.complete_job, job_id, result_summary=summary)
```

This replaces the hardcoded `"Job queued successfully"` with a caller-provided summary or a sensible default.

### Task 3: Add `_complete_job_for_instance()` Helper

**File**: `daemon/manager.py`

**New helper method** on InstanceManager:
```python
async def _complete_job_for_instance(
    self,
    instance_id: str,
    success: bool,
    error: str | None = None,
    result_summary: str | None = None,
) -> None:
    """Update job status when instance completes.
    
    Looks up the job associated with this instance using the public
    get_job_by_instance() API and marks it as completed or failed.
    Also triggers the next pending job for the same project.
    
    Args:
        instance_id: The instance that has completed.
        success: True if instance completed successfully.
        error: Error message if success=False.
        result_summary: Optional summary of the result.
    """
    if self._job_queue_service is None:
        return
    
    try:
        job = await self._job_queue_service.get_job_by_instance(instance_id)
        if job is None:
            return  # No job associated with this instance
        
        if job.status not in ("processing", "pending"):
            return  # Already in terminal state — idempotent guard
        
        # Complete or fail the job (handles lock release internally)
        updated = await self._job_queue_service.complete_job(
            job.job_id,
            success=success,
            error=error,
            result_summary=result_summary,
        )
        
        # Trigger next pending job for this project
        if updated and job.project_id:
            await self._job_queue_service.trigger_next_job(job.project_id)
            
    except Exception as e:
        logger.warning(
            f"Failed to update job status for instance {instance_id[:8]}...: {e}"
        )
```

**Key design decisions**:
- Uses **public** `get_job_by_instance()` — no private `_repository` access
- Passes `result_summary` directly to `complete_job()` — no two-step workaround
- Idempotent guard: checks job status before updating
- Handles "no job" case silently (not all instances come from the job queue)
- Triggers next job only on successful completion (not on failure, since `complete_job()` already handles lock release)

### Task 4: Wire Helper into `_process_queue()` Success and Failure

**File**: `daemon/manager.py` — `_process_queue()` method

**Integration point A — after successful message processing** (in the block where the "completed" event is broadcast):
```python
# After broadcasting the "completed" event for the message
await self._complete_job_for_instance(
    instance_id=instance_id,
    success=True,
    result_summary=result.content[:500] if result.content else None,
)
```

**Integration point B — after max retries exceeded** (in the except block where max retries are exceeded):
```python
# After marking message as failed and broadcasting error event
await self._complete_job_for_instance(
    instance_id=instance_id,
    success=False,
    error=f"Max retries ({msg.retry_count}) exceeded: {e}",
)
```

### Task 5: Wire Helper into `terminate_instance()`

**File**: `daemon/manager.py` — `terminate_instance()` method

`terminate_instance()` is synchronous. The helper is async. We need to schedule it properly.

**Option A** (preferred — fire-and-forget):
```python
# After releasing locks (existing code at terminate_instance)
if self._job_queue_service is not None:
    try:
        job = self._job_queue_service.get_job_by_instance_sync(instance_id)
        if job and job.status == "processing":
            # Schedule async completion
            loop = self.broadcaster._main_loop  # Already stored during set_main_loop()
            asyncio.run_coroutine_threadsafe(
                self._complete_job_for_instance(
                    instance_id, success=False, error="Instance terminated"
                ),
                loop,
            )
    except Exception as e:
        logger.warning(f"Failed to mark job as failed on terminate: {e}")
```

This requires also adding `get_job_by_instance_sync()` to JobQueueService — a thin sync wrapper:
```python
def get_job_by_instance_sync(self, instance_id: str) -> Optional[JobItem]:
    """Sync version of get_job_by_instance()."""
    return self._repository.get_by_instance(instance_id)
```

### Task 6: Remove Premature `trigger_next_job()` from JobProcessor

**File**: `daemon/services/job_processor.py` — `_process_next_job()` method

**Remove these lines** (after the `update_job()` call):
```python
            # Trigger next job for this project (if any)
            if job.project_id:
                await self._queue_service.trigger_next_job(job.project_id)
```

**Why remove it**: This call fires immediately after enqueuing a message — the job hasn't completed yet. For same-project jobs, `trigger_next_job()` will try to acquire the lock, fail silently (lock already held by the current job), and do nothing. The correct place to trigger the next job is in the completion callback (Task 5 above), which fires when the current job actually finishes and releases the lock.

## Key Files
- `daemon/manager.py` — New `_complete_job_for_instance()` helper + integration points in `_process_queue()` and `terminate_instance()`
- `daemon/services/job_queue_service.py` — New `get_job_by_instance()` + `get_job_by_instance_sync()`, extended `complete_job()` signature
- `daemon/services/job_processor.py` — Remove premature `trigger_next_job()` call
- `daemon/repositories/job_queue/repository.py` — No changes (uses existing `get_by_instance()`)

## Constraints
- `terminate_instance()` is synchronous — need sync-compatible path for job lookup, async for completion
- Must handle case where instance has NO associated job (not all instances come from job queue)
- Must be idempotent — `complete_job()` already handles `ValueError` for already-completed jobs
- Must not block the message processing loop — all job operations should be fast DB updates
- Must handle race between `_process_queue` completion and `terminate_instance` — both might try to complete the same job. The idempotent guard (checking job status first) handles this.
- Must NOT access `self._job_queue_service._repository` — use public methods only

## Deliverables
- [ ] `get_job_by_instance()` public method added to JobQueueService
- [ ] `get_job_by_instance_sync()` sync wrapper added to JobQueueService
- [ ] `complete_job()` accepts optional `result_summary` parameter
- [ ] `_complete_job_for_instance()` helper added to InstanceManager (uses public API only)
- [ ] Helper called from success and failure paths in `_process_queue()`
- [ ] Helper called from `terminate_instance()` (via event loop scheduling)
- [ ] Premature `trigger_next_job()` removed from `job_processor.py`
- [ ] No regressions in existing message processing
