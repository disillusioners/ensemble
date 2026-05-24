# Phase 2: Integration & Wiring — HTTP API → JobQueue → MessageJobHandler

## Objective

Wire the HTTP API message path through JobQueue: POST endpoint enqueues via `JobQueueService.enqueue(job_type="message", instance_id=...)`, a new `MessageJobHandler` processes MESSAGE jobs via `_process_message_with_tracking()`, and cancellation/orphan/status/termination all handle MESSAGE jobs correctly.

## Coupling

- **Depends on**: Phase 1 (all tasks)
- **Coupling type**: tight
- **Shared files with other phases**: `job_processor.py`, `job_queue_service.py`
- **Shared APIs/interfaces**: `job_type="message"`, `find_processing_message_jobs_by_instance()`, `find_jobs_by_instance()`, `enqueue(job_type=..., instance_id=...)`, `atomic_transition(requeue)`, `start_job()` MESSAGE override
- **Why this coupling**: Phase 2 uses all types and methods from Phase 1, including the `requeue` state transition and `start_job()` override. Extends same files.

## Context

- Phase 1 completed: `job_type` on JobItem, indexed `instance_id` column queries, `enqueue()` accepts `job_type` + `instance_id`, `start_job()` uses `job.instance_id` for MESSAGE jobs, `requeue` state transition
- Key constraint: `_process_message_with_tracking()` is NOT modified — called as-is
- `instance_id` stored in `JobItem.instance_id` column (NOT `job_metadata`)

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create `MessageJobHandler` | New class. Accepts MESSAGE `JobItem` (already past pre-check), reads `job.instance_id` from column, calls `manager._process_message_with_tracking()`, marks job done. Stores active CancellationTokenSource for cancellation. Includes safety-net concurrency check. | New: `daemon/services/message_job_handler.py` |
| 2 | Wire handler into `JobProcessor._process_next_job()` | **Before `start_job()`**: DB-level concurrency check using `job.instance_id` column — if MESSAGE job's instance already has a PROCESSING MESSAGE, skip (continue). **After `start_job()` succeeds**: if `job.job_type == "message"` → delegate to `MessageJobHandler`. | `daemon/services/job_processor.py` |
| 3 | Add `enqueue_message_via_jq()` to `InstanceMessaging` | New method: creates `MessageQueue` entry + all side effects, enqueues via `JobQueueService.enqueue(job_type="message", instance_id=instance_id, ...)`. Does NOT create `Task`, does NOT call `worker_pool.notify_work()`. | `daemon/services/instance_messaging.py` |
| 4 | Update HTTP POST router | `POST /{instance_id}/messages` calls `manager.enqueue_message_via_jq()` instead of `manager.enqueue_message()`. Same request/response format. | `daemon/routers/messages.py` |
| 5 | Fix orphan recovery — MESSAGE guard FIRST in loop | In `job_processor.py`: `for proc_job in (processing or []):` — FIRST statement inside loop must be `if proc_job.job_type == "message":` guard → FAIL + continue. BEFORE `if proc_job.instance_id:` check. | `daemon/services/job_processor.py` |
| 6 | Add `cancel_message_job()` to `JobQueueService` | Handles PENDING and PROCESSING differently. PENDING: repository `cancel_job()` (PENDING→CANCELLED). PROCESSING: signal CancellationToken via handler's `_active_tokens`. | `daemon/services/job_queue_service.py` |
| 7 | Update GET status endpoint for JobQueue messages | Query `JobItem` by `instance_id` column + filter `job_metadata["message_id"]`. If found, return job status. Otherwise, fall back to existing `get_queue_stats()`. | `daemon/routers/messages.py` |
| 8 | Cancel ALL MESSAGE jobs on instance termination | In `instance_lifecycle.py` `terminate_instance()`: query `find_jobs_by_instance(instance_id, job_type="message")`, iterate and call `self._job_queue_service.cancel_message_job()`. | `daemon/services/instance_lifecycle.py` |

## Detailed Design: MessageJobHandler

```python
# daemon/services/message_job_handler.py (NEW FILE)

import asyncio
import logging
from daemon.cancellation import (
    CancellationTokenSource,
    CancellationReason,
    OperationCancelledError,
)
from daemon.services.job_queue_service import DemandState

logger = logging.getLogger(__name__)


class MessageJobHandler:
    """Handles MESSAGE-type jobs by routing to existing instance's _process_message_with_tracking().
    
    Key design:
    - Primary concurrency gate is in JobProcessor._process_next_job() BEFORE start_job()
    - This handler has a safety-net check for race conditions
    - Stores active CancellationTokenSource for PROCESSING jobs (enables cancel_message_job)
    - Does NOT spawn instances — MESSAGE jobs target existing running instances
    - Reads instance_id from JobItem.instance_id column (set at enqueue time)
    - Calls _process_message_with_tracking() as-is (no modifications)
    """

    def __init__(self, manager, job_queue_service, job_repository):
        self._manager = manager
        self._job_service = job_queue_service
        self._job_repo = job_repository
        self._active_tokens: dict[str, CancellationTokenSource] = {}  # job_id → CTS

    async def handle(self, job) -> None:
        """Process a MESSAGE job. Called from JobProcessor after start_job() succeeds.
        
        Args:
            job: JobItem with job_type="message" and instance_id set in column.
        """
        instance_id = job.instance_id
        if not instance_id:
            await self._job_service.complete_job(
                job.job_id,
                demand_state=DemandState.FAILED,
                error="MESSAGE job missing instance_id",
            )
            return

        # DB-level concurrency gate: check if another MESSAGE is processing for this instance
        active = await asyncio.to_thread(
            self._job_repo.find_processing_message_jobs_by_instance, instance_id
        )
        # Exclude self (we just transitioned to PROCESSING)
        active_other = [j for j in active if j.job_id != job.job_id]
        if active_other:
            # Another MESSAGE job is processing for this instance.
            # Back-transition this job: PROCESSING → PENDING so it's picked up next poll cycle.
            # Do NOT fail it — this is a temporary condition.
            # Use atomic_transition (not update()) for race-safety under concurrency.
            logger.info(
                f"MessageJobHandler: instance {instance_id[:8]}... already has "
                f"MESSAGE job processing, re-queuing {job.job_id[:8]}..."
            )
            result = await asyncio.to_thread(
                self._job_repo.atomic_transition, job.job_id,
                from_status="processing", to_status="pending",
            )
            if result is None:
                # Job was already transitioned by another process — nothing to do
                logger.debug(
                    f"MessageJobHandler: job {job.job_id[:8]}... already transitioned, skipping"
                )
                return
            # Release the per-queue lock acquired by start_job()
            # release_queue_lock takes (project_id, queue_id, job_id)
            if job.project_id and job.queue_id:
                await self._job_service._lock_manager.release_queue_lock(
                    job.project_id, job.queue_id, job.job_id
                )
            return

        # Create CancellationToken for this job
        cts = CancellationTokenSource()
        self._active_tokens[job.job_id] = cts

        try:
            # Extract params from job metadata
            message_id = job.job_metadata.get("message_id")
            message_source = job.job_metadata.get("source", "api")
            images = job.job_metadata.get("images")

            # Call the shared processing function — NOT modified
            result = await self._manager._process_message_with_tracking(
                instance_id=instance_id,
                message=job.message,
                message_id=message_id,
                cancellation_token=cts.token,
                is_retry=False,
                retry_count=0,
                message_source=message_source,
                images=images,
            )

            # Mark job complete
            await self._job_service.complete_job(
                job.job_id,
                demand_state=DemandState.COMPLETED,
                result_summary=result.content,
            )

        except OperationCancelledError:
            # Job was cancelled via CancellationToken
            await self._job_service.complete_job(
                job.job_id,
                demand_state=DemandState.CANCELLED,
                error="Message processing cancelled",
            )
        except Exception as e:
            logger.error(
                f"MessageJobHandler: error processing MESSAGE job {job.job_id[:8]}...: {e}"
            )
            await self._job_service.complete_job(
                job.job_id,
                demand_state=DemandState.FAILED,
                error=str(e),
            )
        finally:
            self._active_tokens.pop(job.job_id, None)

    async def cancel_message_job(self, job_id: str) -> None:
        """Cancel a MESSAGE job. Lives on MessageJobHandler, called via JobQueueService.
        
        PENDING: repository cancel_job() for PENDING→CANCELLED transition.
        PROCESSING: signal CancellationToken, handler completes the job on its own.
        """
        job = await asyncio.to_thread(self._job_repo.get, job_id)
        if job is None:
            return

        if job.status == "pending":
            # PENDING→CANCELLED via repository (complete_job() only handles PROCESSING→terminal)
            await asyncio.to_thread(self._job_repo.cancel_job, job_id)
        elif job.status == "processing":
            # Signal CancellationToken — handler will catch OperationCancelledError
            cts = self._active_tokens.get(job_id)
            if cts:
                cts.cancel(reason=CancellationReason.MANUAL)
            else:
                # Token not found (edge case: handler crashed, token cleaned up)
                # Force-cancel via state transition
                await self._job_service.complete_job(
                    job_id,
                    demand_state=DemandState.CANCELLED,
                    error="Cancelled (force, no active token)",
                )
```

### JobQueueService wrapper for `cancel_message_job()`

```python
# daemon/services/job_queue_service.py — NEW METHOD

async def cancel_message_job(self, job_id: str) -> None:
    """Cancel a MESSAGE-type job. Delegates to MessageJobHandler.
    
    This is the public API called by instance_lifecycle.terminate_instance()
    and any other external callers.
    
    Args:
        job_id: The job to cancel.
    """
    if self._message_job_handler is None:
        logger.warning(f"Cannot cancel MESSAGE job {job_id[:8]}... — no handler registered")
        return
    await self._message_job_handler.cancel_message_job(job_id)
```

**Wiring**: `JobQueueService.__init__()` does NOT create the handler (it needs `manager` which isn't available at JQS init time). Instead, `JobProcessor` creates the `MessageJobHandler` and registers it on the service:
```python
# In JobProcessor setup (after all services are initialized):
self._message_job_handler = MessageJobHandler(
    manager=self._instance_manager,
    job_queue_service=self._queue_service,
    job_repository=self._queue_service._repository,
)
self._queue_service._message_job_handler = self._message_job_handler
```

## Detailed Design: `enqueue_message_via_jq()`

```python
# In daemon/services/instance_messaging.py — NEW METHOD
# Mirrors enqueue_message() at lines 540-677 but replaces
# Task creation + WorkerPool notify with JobQueueService.enqueue(job_type="message").

async def enqueue_message_via_jq(
    self,
    instance_id: str,
    message: str,
    source: str = "api",
    priority: int = 1,
    images: list[str] | None = None,
) -> "AsyncMessageResult":
    """Enqueue a message via JobQueue instead of WorkerPool.
    
    Creates MessageQueue entry + all side effects (same as enqueue_message),
    then enqueues a MESSAGE-type job via JobQueueService.
    Does NOT create Task or notify WorkerPool.
    """
    from ..manager import AsyncMessageResult
    
    # Reject new messages during shutdown
    if self._cancellation_service.is_shutting_down:
        raise RuntimeError("Manager is shutting down, cannot accept new messages")
    
    # Determine message type based on source (exact same logic as enqueue_message)
    if source.startswith("internal_report:"):
        msg_type = MessageType.COMPLETION_REPORT.value
        message_id = str(uuid.uuid4())
    elif source.startswith("internal_error_report:"):
        msg_type = MessageType.ERROR_REPORT.value
        message_id = str(uuid.uuid4())
    elif source.startswith("internal_agent:"):
        msg_type = MessageType.AGENT.value
        message_id = str(uuid.uuid4())
    else:
        msg_type = MessageType.HUMAN.value
        message_id = str(uuid.uuid4())
    
    # Log image count if images are provided
    if images:
        logger.info(f"Processing message with {len(images)} image(s)")
    
    with Session(self._manager._engine) as session:
        # 1. Insert the message
        db_message = MessageQueue(
            message_id=message_id,
            instance_id=instance_id,
            content=message,
            source=source,
            type=msg_type,
            status=MessageStatus.READY.value,
            priority=priority,
            images=images,
            enqueued_at=datetime.now(timezone.utc),
        )
        session.add(db_message)
        
        # NOTE: No Task creation here — JobQueue handles job tracking instead.
        
        # 2. Update instance status if IDLE or PAUSED → RUNNING
        #    Also clear paused_at when transitioning away from PAUSED status
        #    Also update last_activity_at and increment version
        status_changed_to_running = False
        is_idle_to_running = False
        instance_agent_id = None
        instance = session.get(Instance, instance_id)
        if instance:
            instance_agent_id = instance.agent_id
            previous_status = instance.status
            if instance.status in (InstanceStatus.IDLE.value, InstanceStatus.PAUSED.value):
                instance.status = InstanceStatus.RUNNING.value
                instance.paused_at = None
                status_changed_to_running = True
                is_idle_to_running = previous_status == InstanceStatus.IDLE.value
            instance.last_activity_at = datetime.now(timezone.utc)
            instance.version = (instance.version or 1) + 1
        else:
            logger.warning(
                f"Instance {instance_id} not found in database during enqueue_message_via_jq. "
                f"This may indicate the instance was not properly persisted."
            )
        
        # 3. Create MESSAGE_RECEIVED event (event-sourced features)
        role = "system" if msg_type == MessageType.SYSTEM.value else "user"
        message_data = {
            "message_id": message_id,
            "role": role,
            "content": message,
            "source": source,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        event = Event(
            instance_id=instance_id,
            message_id=message_id,
            kind=EventKind.MESSAGE_RECEIVED.value,
            data=json.dumps(message_data),
            created_at=datetime.now(timezone.utc),
        )
        session.add(event)
        
        session.commit()
    
    # 4. Emit SSE status_change if instance transitioned to RUNNING
    if status_changed_to_running:
        await self._manager._live_hub.stream_status_change(
            instance_id, InstanceStatus.RUNNING.value, agent_id=instance_agent_id
        )
    
    # 5. Trigger title generation for first user message (fire-and-forget)
    self._maybe_trigger_title_generation(
        instance_id, message, is_idle_to_running and msg_type == MessageType.HUMAN.value
    )
    
    # 6. Look up instance metadata for JobQueue enqueue
    #    get_instance() returns CompiledStateGraph, NOT metadata.
    #    Use instance repository for agent_id and project_id lookup.
    #    Note: repository.get() returns None (not KeyError) for missing instances.
    instance_meta = self._manager._instance_repository.get(instance_id)
    if instance_meta is None:
        raise ValueError(f"Instance {instance_id} not found")
    
    agent_id = instance_meta.agent_id
    project_id = instance_meta.project_id

    # 7. Enqueue as MESSAGE job via JobQueueService
    #    instance_id goes to JobItem.instance_id column (not metadata)
    job = await self._manager._job_queue_service.enqueue(
        agent_id=agent_id,
        message=message,
        source=source,
        project_id=project_id,
        priority=priority,
        job_type="message",
        instance_id=instance_id,  # stored in JobItem.instance_id column
        metadata={
            "message_id": message_id,
            "source": source,
            "images": images,
        },
    )
    
    logger.debug(f"Enqueued MESSAGE job for message {message_id} via JobQueue for instance {instance_id}")

    return AsyncMessageResult(
        message_id=message_id,
        instance_id=instance_id,
        status="queued",
    )
```

**Key differences from `enqueue_message()`**:
- Steps 1-5 (MessageQueue, instance status, event, SSE, title) are IDENTICAL to `enqueue_message()`
- Step 6 (instance metadata lookup) is NEW — needed because we don't have `instance_agent_id` / `project_id` from the session's `Instance` object (it's an ORM model, different from the repository model)
- Step 7 replaces: `Task` creation + `worker_pool.notify_work()` → `JobQueueService.enqueue(job_type="message", instance_id=instance_id, ...)`
- `instance_id` passed as explicit parameter → stored in `JobItem.instance_id` column, NOT in `metadata`
- Source prefixes: `internal_report:`, `internal_error_report:`, `internal_agent:` (matches actual code)

## Detailed Design: Orphan Recovery Fix

**CRITICAL**: The `job_type == "message"` guard must be the **FIRST** statement inside the `for proc_job` loop. It must come BEFORE the `if proc_job.instance_id:` check. Otherwise, MESSAGE jobs with their real `instance_id` would hit `get_instance(real_id)` → if instance is terminated, `KeyError` → re-spawn path (wrong for MESSAGE jobs).

```python
# daemon/services/job_processor.py — _process_next_job() orphan recovery section
# Lines 186-244

for proc_job in (processing or []):
    # >>> FIRST: Guard MESSAGE jobs — fail, don't re-spawn <<<
    # MUST be before the `if proc_job.instance_id:` check below
    if getattr(proc_job, 'job_type', 'task') == 'message':
        logger.info(
            f"JobProcessor: orphan MESSAGE job {proc_job.job_id[:8]}... "
            f"(instance {proc_job.instance_id[:8] if proc_job.instance_id else 'N/A'}...) "
            f"— failing (no re-spawn)"
        )
        await self._queue_service.complete_job(
            proc_job.job_id,
            demand_state=DemandState.FAILED,
            error="Instance gone or unreachable, message job orphaned",
        )
        continue
    # <<< END FIRST GUARD >>>

    if proc_job.instance_id:
        try:
            await self._instance_manager.get_instance(proc_job.instance_id)
            continue  # Instance exists, skip
        except KeyError:
            # Branch 1: instance_id set but instance missing → orphan
            # ... existing re-spawn logic for TASK jobs ...
    else:
        # Branch 2: No instance_id → genuine orphan
        # ... existing re-spawn logic for TASK jobs ...
```

**Why the guard must be first**: After fixing Critical 1+2, MESSAGE jobs have their actual target `instance_id` in the column. If the instance was terminated, `get_instance(proc_job.instance_id)` raises `KeyError`, which would trigger the Branch 1 re-spawn — wrong for MESSAGE jobs. The guard prevents this.

## Detailed Design: JobProcessor Wiring

```python
# daemon/services/job_processor.py — _process_next_job(), BEFORE start_job()

# Around line 246 (after getting pending[0] as job):

job = pending[0]

# >>> NEW: Pre-check for MESSAGE jobs — DB-level concurrency gate <<<
# Check BEFORE start_job() to avoid unnecessary lock acquisition
if job.job_type == "message":
    if job.instance_id:
        active = await asyncio.to_thread(
            self._queue_service._repository.find_processing_message_jobs_by_instance,
            job.instance_id,
        )
        if active:
            # Another MESSAGE is processing for this instance — skip this poll cycle
            logger.debug(
                f"JobProcessor: MESSAGE job {job.job_id[:8]}... skipped — "
                f"instance {job.instance_id[:8]}... busy with another message"
            )
            continue  # Skip to next queue, job stays PENDING
# <<< END NEW >>>

# Try to start the job (acquires per-queue lock internally)
# For MESSAGE jobs, start_job() uses job.instance_id instead of generating UUID
try:
    started_job = await self._queue_service.start_job(job.job_id)
    if started_job is None:
        continue

# >>> NEW: Route MESSAGE jobs to MessageJobHandler <<<
if started_job.job_type == "message":
    await self._message_job_handler.handle(started_job)
    continue
# <<< END NEW >>>

# ... existing TASK job flow: spawn_instance_with_mcp() + enqueue_message() ...
```

**Rationale for split check**: The DB concurrency check happens BEFORE `start_job()` to avoid unnecessary lock acquisition and state transitions. If the instance is busy, we simply skip to the next queue/job in the poll cycle. The `MessageJobHandler.handle()` still has a secondary check as a safety net (in case of race between check and `start_job()`).

## Detailed Design: Instance Termination Cleanup

```python
# daemon/services/instance_lifecycle.py — terminate_instance()
# Around line 417, AFTER existing job cleanup (which handles TASK jobs):

# 8. Cancel ALL MESSAGE jobs for this instance
if self._job_queue_service is not None:
    try:
        message_jobs = self._job_queue_service._repository.find_jobs_by_instance(
            instance_id, job_type="message"
        )
        for msg_job in message_jobs:
            if msg_job.status in ("pending", "processing"):
                await self._job_queue_service.cancel_message_job(msg_job.job_id)
    except Exception as e:
        logger.warning(f"Failed to cancel MESSAGE jobs on terminate: {e}")
```

## Detailed Design: GET Status Endpoint Update

```python
# daemon/routers/messages.py — get_message_status()

@router.get("/{instance_id}/messages/{message_id}")
async def get_message_status(instance_id: str, message_id: str, request: Request):
    manager = _get_manager(request)
    
    try:
        await manager.get_instance(instance_id)
    except KeyError:
        raise HTTPException(status_code=404, ...)
    
    # Try JobQueue path first (HTTP-originated messages)
    job_item = None
    if manager._job_queue_service:
        # Find MESSAGE job with this instance_id + message_id in metadata
        jobs = manager._job_queue_service._repository.find_jobs_by_instance(
            instance_id, job_type="message"
        )
        job_item = next(
            (j for j in jobs if j.job_metadata.get("message_id") == message_id),
            None,
        )
    
    if job_item:
        # Return job-based status
        return {
            "message_id": message_id,
            "instance_id": instance_id,
            "status": job_item.status,
            "result_summary": job_item.result_summary,
            "error": job_item.error_message,
        }
    
    # Fallback: existing queue stats (internal/WorkerPool messages)
    stats = manager.get_queue_stats(instance_id)
    return {
        "message_id": message_id,
        "instance_id": instance_id,
        "queue_stats": {
            "pending_count": stats.pending_count,
            "processing_count": stats.processing_count,
            "oldest_message_age_seconds": stats.oldest_message_age_seconds,
        }
    }
```

## Key Files

- New: `daemon/services/message_job_handler.py` — MESSAGE job handler with DB-level concurrency gate
- `daemon/services/job_processor.py` — Route to handler, fix orphan recovery (guard FIRST in loop)
- `daemon/services/instance_messaging.py` — New `enqueue_message_via_jq()` method
- `daemon/routers/messages.py` — POST uses new method, GET has dual-path status
- `daemon/services/instance_lifecycle.py` — Termination cancels ALL MESSAGE jobs
- `daemon/services/job_queue_service.py` — `cancel_message_job()` method, `start_job()` override (Phase 1)

## Constraints

- `_process_message_with_tracking()` must NOT be modified
- WorkerPool path completely untouched — `enqueue_message()` still works for internal callers
- Same HTTP API contract (same endpoints, request/response format)
- `MessageQueue` entry still created (for SSE streaming via `_live_hub`)
- SSE streaming works because `_process_message_with_tracking()` emits via `_live_hub` — unchanged
- All `complete_job()` calls use exact signature: `complete_job(job_id, demand_state=..., error=..., result_summary=...)`
- `instance_id` stored in `JobItem.instance_id` column (existing column) — NOT in `job_metadata`

## Deliverables

- [ ] `MessageJobHandler` reads `job.instance_id` from column, safety-net concurrency check, CancellationToken management
- [ ] `JobProcessor._process_next_job()` does DB-level concurrency check BEFORE `start_job()` using `job.instance_id`
- [ ] `enqueue_message_via_jq()` passes `instance_id=instance_id` to `enqueue()` (column, not metadata)
- [ ] HTTP POST router calls `enqueue_message_via_jq()`
- [ ] Orphan recovery guard is FIRST statement in `for proc_job` loop (before `if proc_job.instance_id:`)
- [ ] `cancel_message_job()` on `JobQueueService` handles PENDING (repository `cancel_job()`) and PROCESSING (CancellationToken signal)
- [ ] GET status endpoint queries `find_jobs_by_instance()` by `instance_id` column, filters by `message_id` in metadata
- [ ] `terminate_instance()` calls `self._job_queue_service.cancel_message_job()` for ALL MESSAGE jobs via iteration
- [ ] SSE streaming works identically
