# Phase 1: Backend — Job Completion Callback

## Objective
Wire the instance completion lifecycle into the job queue so that jobs transition from PROCESSING → COMPLETED/FAILED when instances finish executing, error out, or are terminated.

## Coupling
- **Depends on**: None
- **Coupling type**: independent
- **Shared files with other phases**: None
- **Shared APIs/interfaces**: `JobQueueService.complete_job()` — existing method, no signature changes needed
- **Why this coupling**: This phase adds calls to an existing API. No interface changes.

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
- `self._job_queue_service` is already wired into `InstanceManager` (line 472, set via `set_job_queue_service()`)
- `JobQueueService.complete_job(job_id, success, error)` already exists and works (lines 406-439)
- It releases locks, updates status, and returns the updated job
- `JobRepository.get_by_instance(instance_id)` already exists (line 89-101) — finds job by instance_id
- `JobQueueService.trigger_next_job(project_id)` already exists (line 441) — dequeues next pending job
- The only missing piece is **calling these methods from the right places**

### Lock Not Released on Failure (Additional Bug)
In `_fail_job()` (job_queue_service.py:286-298), the job is marked FAILED but the lock is NOT released. Compare with `_complete_job()` which releases the lock. This means failed jobs block the project queue.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add job completion on successful message processing | After the message is processed successfully in `_process_queue()`, look up any job associated with the instance and call `complete_job(success=True)` | `daemon/manager.py:~1025` |
| 2 | Add job failure on max retries exceeded | When max retries are exceeded in `_process_queue()`, look up job and call `complete_job(success=False, error=...)` | `daemon/manager.py:~1065` |
| 3 | Add job failure on instance termination | In `terminate_instance()`, after releasing locks, also mark the associated job as FAILED | `daemon/manager.py:~2150` |
| 4 | Fix lock release on job failure | In `JobQueueService._fail_job()`, release the lock before marking failed (matching `_complete_job()` pattern) | `daemon/services/job_queue_service.py:286-298` |
| 5 | Trigger next job after completion | After `complete_job()` in manager.py, call `trigger_next_job(project_id)` to dequeue the next pending job for that project | `daemon/manager.py:~1025, ~1065` |
| 6 | Add result_summary from instance output | When completing a job successfully, capture the last assistant message as `result_summary` instead of the generic "Job queued successfully" | `daemon/manager.py`, `daemon/services/job_queue_service.py` |

## Detailed Implementation

### Task 1: Job Completion on Successful Processing

**Location**: `daemon/manager.py` — `_process_queue()` method, around line 1025 (after successful message broadcast)

**Approach**: Add a helper method `_complete_job_for_instance()` to InstanceManager that:
1. Checks if `_job_queue_service` is available
2. Calls `repository.get_by_instance(instance_id)` to find the job
3. If found and in PROCESSING state, calls `complete_job(job_id, success=True, result_summary=...)`
4. Calls `trigger_next_job(project_id)` if project_id exists

**New helper method** (add to InstanceManager):
```python
async def _complete_job_for_instance(
    self,
    instance_id: str,
    success: bool,
    error: str | None = None,
    result_summary: str | None = None,
) -> None:
    """Update job status when instance completes.
    
    Looks up the job associated with this instance and marks it
    as completed or failed based on success parameter.
    Also triggers the next pending job for the same project.
    """
    if self._job_queue_service is None:
        return
    
    try:
        job = await asyncio.to_thread(
            self._job_queue_service._repository.get_by_instance, instance_id
        )
        if job is None:
            return  # No job associated with this instance
        
        if success:
            summary = result_summary or "Job completed successfully"
            await self._job_queue_service.complete_job(
                job.job_id, success=True, error=None
            )
            # Update result_summary separately if provided
            if result_summary:
                await asyncio.to_thread(
                    self._job_queue_service._repository.update,
                    job.job_id,
                    {"result_summary": result_summary}
                )
        else:
            await self._job_queue_service.complete_job(
                job.job_id, success=False, error=error or "Instance failed"
            )
        
        # Trigger next pending job for this project
        if job.project_id:
            await self._job_queue_service.trigger_next_job(job.project_id)
            
    except Exception as e:
        logger.warning(f"Failed to update job status for instance {instance_id}: {e}")
```

**Integration point 1** — after successful message processing (after line ~1024):
```python
# After broadcasting the "completed" event for the message
await self._complete_job_for_instance(
    instance_id=instance_id,
    success=True,
    result_summary=result.content[:500] if result.content else None,  # Truncate
)
```

### Task 2: Job Failure on Max Retries

**Integration point 2** — in the max retries exceeded block (after line ~1078):
```python
# After marking message as failed and broadcasting error
await self._complete_job_for_instance(
    instance_id=instance_id,
    success=False,
    error=f"Max retries ({msg.retry_count}) exceeded: {e}",
)
```

### Task 3: Job Failure on Termination

**Integration point 3** — in `terminate_instance()` (after line ~2152, after releasing locks):
```python
# After releasing locks for the instance
# Mark any associated job as failed
if self._job_queue_service is not None:
    try:
        # Must be sync since terminate_instance is sync
        job = self._job_queue_service._repository.get_by_instance(instance_id)
        if job and job.status == "processing":
            self._job_queue_service.complete_job_sync(
                job.job_id, success=False, error="Instance terminated"
            )
    except Exception as e:
        logger.warning(f"Failed to mark job as failed on terminate: {e}")
```

**Note**: `terminate_instance()` is a sync method. We need either:
- A sync version of `complete_job()` (add `complete_job_sync()` to JobQueueService)
- Or run the async call via `asyncio.run_coroutine_threadsafe()`

### Task 4: Fix Lock Release on Failure

**File**: `daemon/services/job_queue_service.py` — `_fail_job()` method (lines 286-298)

**Current code** (broken):
```python
async def _fail_job(self, job: JobItem, error_message: str) -> None:
    """Mark a job as failed and release its lock."""
    # Mark job as failed
    await asyncio.to_thread(self._repository.fail_job, job.job_id, error_message)
    # NOTE: Lock is NOT released here — BUG!
```

**Fixed code**:
```python
async def _fail_job(self, job: JobItem, error_message: str) -> None:
    """Mark a job as failed and release its lock."""
    # Release the lock first (matching _complete_job pattern)
    if job.project_id:
        await self._lock_manager.release(job.project_id, job.job_id)
    # Mark job as failed
    await asyncio.to_thread(self._repository.fail_job, job.job_id, error_message)
```

### Task 5: Trigger Next Job

Already covered in the helper method above. The `_complete_job_for_instance()` helper calls `trigger_next_job(project_id)` after completing the current job.

### Task 6: Result Summary

The `complete_job()` method currently uses a hardcoded `result_summary="Job queued successfully"`. This is misleading for completed jobs. 

**Fix**: Pass the actual result content as `result_summary`. The helper method extracts the last assistant message content from the instance execution result.

## Key Files
- `daemon/manager.py` — Main changes: new helper method + 3 integration points (~lines 1025, 1078, 2152)
- `daemon/services/job_queue_service.py` — Fix `_fail_job()` lock release, add `complete_job_sync()` for sync callers
- `daemon/repositories/job_queue/repository.py` — `get_by_instance()` already exists (line 89-101)

## Constraints
- `terminate_instance()` is synchronous — need sync-compatible path for job completion
- Must handle case where instance has NO associated job (not all instances come from job queue)
- Must be idempotent — `complete_job()` already handles `ValueError` for already-completed jobs
- Must not block the message processing loop — all job operations should be fast DB updates
- Must handle race between `_process_queue` completion and `terminate_instance` — both might try to complete the same job

## Deliverables
- [ ] `_complete_job_for_instance()` helper method added to InstanceManager
- [ ] Helper called from 3 integration points (success, failure, termination)
- [ ] Lock released on job failure in `_fail_job()`
- [ ] `complete_job_sync()` added for sync callers
- [ ] Result summary captured from instance output
- [ ] Next pending job triggered after completion
- [ ] No regressions in existing message processing
