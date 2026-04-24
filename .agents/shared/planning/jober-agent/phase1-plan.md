# Phase 1: Job Watch Infrastructure (REVISED v4)

## Changes in This Revision (v4)
**Blocker fix:**
- **Blocker**: Added Path 7 — `JobRecoveryService._fail_orphaned_job()` → FAILED. This runs during daemon startup and bypasses `JobQueueService.complete_job()`, directly calling `atomic_transition()`. Total paths: **7** (was 6).

**Key implications of Path 7:**
- `JobRecoveryService` currently has no reference to `JobQueueService` — must be wired in bootstrap
- Bootstrap ordering is critical: `watcher_repo` must be created and wired into `JobRecoveryService` BEFORE `recover_on_startup()` runs
- The watching instance may not be running yet at startup — notifications queue as DB messages for later delivery when instance resumes
- `recover_on_startup()` is async — can call `notify_watchers()` directly (no `run_coroutine_threadsafe` needed)

---

## Objective
Build the backend infrastructure for job watching: a subscription model + repository, four new agent-facing tools, shared notification service, integration hooks in **ALL 7 terminal transition paths**, `watch` parameter on `job_create`, startup reconciliation, and auto-cleanup mechanisms. After this phase, any agent can watch a job and receive lifecycle notifications as messages **regardless of how the job reaches a terminal state** — including during daemon startup recovery.

## Coupling
- **Depends on**: None (root phase)
- **Coupling type**: —
- **Shared files with other phases**: 
  - `daemon/tools/job_queue.py` — Phase 2 references tool names defined here
  - New `daemon/repositories/job_queue/watcher_models.py` — shared model
- **Shared APIs/interfaces**: Tool names (`watch_job`, `unwatch_job`, `list_watched_jobs`, `watch_jobs`) + `notify_watchers()` service method

## Context
- **Current state**: Jobs have 7 states (PENDING → PROCESSING → COMPLETED/FAILED/CANCELLED/TERMINATED/DEAD_LETTER). Terminal transitions happen in **7 places**:
  - Path 1: `JobFeedbackObserver._process_event()` — COMPLETED/FAILED from lifecycle events
  - Path 2: `JobQueueService.cancel_job()` — CANCELLED
  - Path 3: `JobQueueService.complete_job()` / `complete_job_sync()` — COMPLETED/FAILED/TERMINATED
  - Path 4: `InstanceLifecycleService.terminate_instance()` — TERMINATED (calls complete_job_sync)
  - Path 5: `DeadLetterService.move_to_dlq_standalone()` — DEAD_LETTER
  - Path 6: `JobRetryEngine.maybe_retry()` → calls `move_to_dlq()` — DEAD_LETTER (retry exhaustion)
  - Path 7: `JobRecoveryService._fail_orphaned_job()` — FAILED (daemon startup, orphaned PROCESSING jobs)
- **Key insight**: Extract `notify_watchers()` into `JobQueueService` so all 7 paths call the same function.
- **Transaction boundary note**: `move_to_dlq()` (Path 6's underlying call) runs inside a shared DB session/transaction. We cannot call async `notify_watchers()` mid-transaction. Solution: notify AFTER commit at each call site.
- **Bootstrap ordering note**: Path 7 runs at daemon startup (line 193 in `api.py`) BEFORE `JobFeedbackObserver` starts (line ~205). The `watcher_repo` and `notify_watchers()` must be available before `recover_on_startup()` runs. See Task 7 for bootstrap sequence.

## Tasks

### Task 1: Create Watcher Model & Repository
**Details**: New SQLite-backed storage for job-instance watch pairs. Use JSON column for events.

**Key Files**:
- `daemon/repositories/job_queue/watcher_models.py` — **CREATE**
- `daemon/repositories/job_queue/watcher_repository.py` — **CREATE**
- `daemon/repositories/job_queue/models.py` — **MODIFY** (export)

**Implementation**:
```python
# watcher_models.py
class JobWatcher(SQLModel, table=True):
    __tablename__ = "job_watchers"
    
    watch_id: str = Field(primary_key=True, default_factory=lambda: str(uuid.uuid4()))
    job_id: str = Field(foreign_key="job_queue_items.job_id", index=True)
    instance_id: str = Field(foreign_key="instances.instance_id", index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    # JSON list of terminal states to watch for
    # Includes ALL terminal states including dead_letter
    watch_events: list[str] = Field(
        default_factory=lambda: ["completed", "failed", "cancelled", "terminated", "dead_letter"],
        sa_column=Column(JSON)
    )
```

**Repository methods**:
- `add_watch(job_id, instance_id, watch_events=None)` — default includes dead_letter
- `remove_watch(job_id, instance_id) -> bool`
- `get_watchers_for_job(job_id) -> list[JobWatcher]`
- `get_watches_for_instance(instance_id) -> list[JobWatcher]`
- `remove_all_watches_for_instance(instance_id) -> int`
- `remove_all_watches_for_job(job_id) -> int`
- `count_watches_for_instance(instance_id) -> int`
- `reconcile_terminal_watches(job_service)` — finds watches where job is already in any terminal state (including dead_letter), notifies, then cleans up

**Constraints**:
- Composite unique on `(job_id, instance_id)`
- Max 50 watches per instance (enforced in tool)
- Index on `job_id` and `instance_id`

---

### Task 2: Create Job Watch Tools
**Details**: Four new tools in `daemon/tools/job_queue.py`.

**Key Files**:
- `daemon/tools/job_queue.py` — **MODIFY**

**Changes to `create_job_tools()`**: Add `watcher_repo: JobWatcherRepository | None = None`. If None, skip watch tools (backward compatible).

**`watch_job`** (with terminal check including dead_letter):
```python
@register_tool_category("job")
@tool
async def watch_job(job_id: str, events: list[str] | None = None) -> str:
    """Watch a job for lifecycle events. If already terminal, sends immediate notification."""
    job = await job_service.get_job(job_id)
    if not job:
        return "Error: Job not found"
    
    # Terminal state check — includes dead_letter
    terminal_states = {"completed", "failed", "cancelled", "terminated", "dead_letter"}
    if job.status in terminal_states:
        await job_service.notify_watchers(job_id, job.status, job.error_message)
        return f"Job already {job.status}. Immediate notification sent."
    
    # Register watch
    default_events = ["completed", "failed", "cancelled", "terminated", "dead_letter"]
    watcher_repo.add_watch(job_id, current_instance_id, events or default_events)
    return f"Watch registered for {job_id}. Will notify on terminal states."
```

Similar updates for `watch_jobs()` (bulk), `unwatch_job()`, `list_watched_jobs()`.

---

### Task 3: Add `watch` Parameter to `job_create`
**Key Files**: `daemon/tools/job_queue.py`

**Watch registered BEFORE enqueue — no race condition.**

```python
# In job_create():
job_item = await job_service.enqueue(...)  # Creates PENDING job

if watch and watcher_repo is not None:
    # Safe: job is still PENDING, observer only processes PROCESSING jobs
    watcher_repo.add_watch(job_item.job_id, current_instance_id)
    result += f"\nWatch registered atomically before dispatch."

return result
```

---

### Task 4: Create Shared Notification Service — ALL 7 Paths
**Details**: Extract `notify_watchers()` into `JobQueueService`. Wire it into every terminal transition path.

**Key Files**:
- `daemon/services/job_queue_service.py` — **MODIFY** (add `notify_watchers()` + add `watcher_repo` to constructor + call from `cancel_job()`, `complete_job()`, `complete_job_sync()`)
- `daemon/services/job_feedback_observer.py` — **MODIFY** (call shared notifier from `_process_event()`)
- `daemon/services/instance_lifecycle.py` — **MODIFY** (already calls `complete_job_sync()` which triggers notification)
- `daemon/services/dead_letter_service.py` — **MODIFY** (call `notify_watchers()` after commit in `move_to_dlq_standalone()`)
- `daemon/services/job_retry_engine.py` — **MODIFY** (call `notify_watchers()` after commit when `move_to_dlq()` called)
- `daemon/services/job_recovery_service.py` — **MODIFY** (call `notify_watchers()` after `atomic_transition()` in `_fail_orphaned_job()`)

**New `notify_watchers()` method** (in `JobQueueService`):
```python
async def notify_watchers(self, job_id: str, status: str, error: str | None = None) -> int:
    """Notify ALL watchers for a job. Called from EVERY terminal path.
    
    Returns number of watchers notified.
    Safe to call even if no watchers exist (returns 0).
    If watching instance is not running, message queues in DB for later delivery.
    """
    if self._watcher_repo is None:
        return 0
    
    try:
        watchers = self._watcher_repo.get_watchers_for_job(job_id)
        if not watchers:
            return 0
        
        # Get job for notification details
        job = await asyncio.to_thread(self._repository.get, job_id)
        if job is None:
            return 0
        
        notified = 0
        for watcher in watchers:
            if status not in watcher.watch_events:
                continue
            
            notification = (
                f"[JOB_EVENT] Job {job_id[:8]}... reached status '{status}'.\n"
                f"Agent: {job.agent_id}\n"
                f"Result: {job.result_summary or 'N/A'}\n"
                f"Error: {error or 'None'}\n"
                f"\n"
                f"```json\n"
                f"{json.dumps({
                    'job_id': job_id,
                    'status': status,
                    'agent_id': job.agent_id,
                    'result': job.result_summary or '',
                    'error': error,
                    'timestamp': datetime.utcnow().isoformat()
                }, ensure_ascii=False)}\n"
                f"```"
            )
            
            await self._instance_manager.enqueue_message(
                instance_id=watcher.instance_id,
                message=notification,
                source=f"internal_agent:job_event:{job_id}:{status}",
            )
            notified += 1
        
        # Cleanup: terminal states are final, no need to keep watches
        self._watcher_repo.remove_all_watches_for_job(job_id)
        return notified
        
    except Exception as e:
        logger.warning(f"Failed to notify watchers for job {job_id[:8]}...: {e}")
        return 0
```

**Wiring into each path:**

**Path 1 — `JobFeedbackObserver._process_event()`**:
After successful transition, replace inline notification code with:
```python
await self._job_queue_service.notify_watchers(job.job_id, status, error_message)
```

**Path 2 — `JobQueueService.cancel_job()`**:
After successful `atomic_transition(..., to_status=CANCELLED)`:
```python
await self.notify_watchers(job.job_id, "cancelled")
```

**Path 3 — `JobQueueService.complete_job()`**:
After successful terminal transition (COMPLETED/FAILED/TERMINATED):
```python
await self.notify_watchers(job_id, demand_state.value, error)
```

**Path 3b — `JobQueueService.complete_job_sync()`** (sync version):
Schedule notification on event loop:
```python
if self._loop and self._loop.is_running():
    asyncio.run_coroutine_threadsafe(
        self.notify_watchers(job_id, demand_state.value, error),
        self._loop,
    )
```

**Path 4 — `terminate_instance()`**: Already calls `complete_job_sync(TERMINATED)`. Path 3b's notification covers this — no additional code needed.

**Path 5 — `DeadLetterService.move_to_dlq_standalone()`**:
After `session.commit()`, schedule notification:
```python
if self._job_queue_service and self._loop and self._loop.is_running():
    asyncio.run_coroutine_threadsafe(
        self._job_queue_service.notify_watchers(job_id, "dead_letter", job.error_message),
        self._loop,
    )
```

**Path 6 — `JobRetryEngine.maybe_retry()` → `move_to_dlq()`**:
After `session.commit()` in the else branch, schedule notification:
```python
if self._job_queue_service and self._loop and self._loop.is_running():
    asyncio.run_coroutine_threadsafe(
        self._job_queue_service.notify_watchers(job_id, "dead_letter", job.error_message),
        self._loop,
    )
```

**Path 7 — `JobRecoveryService._fail_orphaned_job()`** (NEW):
After successful `atomic_transition()` in `_fail_orphaned_job()`:
```python
# After the successful atomic_transition block
if self._job_queue_service is not None:
    await self._job_queue_service.notify_watchers(job.job_id, "failed", error_message)
```

**Important notes for Path 7:**
- `_fail_orphaned_job()` is already async — can call `notify_watchers()` directly (no `run_coroutine_threadsafe` needed)
- This runs during daemon startup. The watching instance may not be running yet. `enqueue_message()` handles this gracefully — it persists the message to the DB. When the instance is later spawned/resumed, queued messages are delivered.
- The `job_queue_service` must be wired into `JobRecoveryService` before `recover_on_startup()` is called. See Task 7 for bootstrap ordering.

---

### Task 5: Integrate into JobFeedbackObserver
**Key Files**: `daemon/services/job_feedback_observer.py` — **MODIFY**

**Changes**:
- Constructor: add optional `watcher_repo` param (for backward compat) — though primary notification goes through `job_queue_service`
- In `_process_event()`: After successful `atomic_transition()`, call `await self._job_queue_service.notify_watchers(job.job_id, status, error_message)`
- Remove any inline notification code — centralized in `notify_watchers()`

---

### Task 6: Auto-Cleanup + Startup Reconciliation
**Key Files**:
- `daemon/services/instance_lifecycle.py` — **MODIFY** (cleanup watches on instance terminate)
- `daemon/api.py` — **MODIFY** (create repo, wire deps, call reconciliation on startup)

**Reconciliation** (in `JobQueueService`):
```python
async def reconcile_terminal_watches(self) -> int:
    """Scan for watches where job is already terminal. Notify and cleanup."""
    if self._watcher_repo is None:
        return 0
    
    # All terminal states including dead_letter
    terminal_states = ["completed", "failed", "cancelled", "terminated", "dead_letter"]
    
    all_watches = self._watcher_repo.get_all_active_watches()
    reconciled = 0
    
    for watch in all_watches:
        job = await asyncio.to_thread(self._repository.get, watch.job_id)
        if job and job.status in terminal_states:
            await self.notify_watchers(job.job_id, job.status, job.error_message)
            reconciled += 1
    
    return reconciled
```

Called from `api.py` during daemon startup, AFTER `recover_on_startup()` (since Path 7 already handles orphan notifications during recovery, reconciliation catches anything recovery didn't cover).

---

### Task 7: Bootstrap Wiring (CRITICAL ORDERING)
**Key Files**: 
- `daemon/api.py` — Create `watcher_repo`, wire into all services
- `daemon/tools/instance.py` — Pass `watcher_repo` through `create_job_tools_if_available()`

**Bootstrap sequence in `api.py` (must follow this order):**

```
1. Create engine, existing repos (job_repository, lock_repo, etc.)
2. CREATE watcher_repo  ← NEW: must happen early
3. Wire watcher_repo into JobQueueService  ← NEW
4. Create JobRecoveryService with job_queue_service reference  ← MODIFIED
5. Run recover_on_startup()  ← Path 7 notifications fire here
6. Run reconcile_terminal_watches()  ← catches anything recovery missed
7. Initialize DeadLetterService, wire into retry engine  ← Paths 5, 6
8. Initialize JobFeedbackObserver  ← Path 1
9. ... rest of startup
```

**Specific wiring:**
```python
# Step 2: Create watcher_repo early (after engine + existing repos)
from daemon.repositories.job_queue.watcher_repository import JobWatcherRepository
JobWatcher.metadata.create_all(engine)
watcher_repo = JobWatcherRepository(engine)

# Step 3: Wire into JobQueueService
job_queue_service._watcher_repo = watcher_repo

# Step 4: Wire JobQueueService into JobRecoveryService (NEW)
# Currently JobRecoveryService only takes job_repo, lock_repo, instance_repo
# Add job_queue_service parameter:
job_recovery = JobRecoveryService(
    job_repository=job_repository,
    lock_repository=lock_repo,
    instance_repository=instance_repo,
    job_queue_service=job_queue_service,  # NEW — for notify_watchers
)

# Step 5: Recovery runs with Path 7 notification capability
recovery_stats = await job_recovery.recover_on_startup()

# Step 6: Reconciliation (catches watches for jobs already terminal before this startup)
await job_queue_service.reconcile_terminal_watches()

# Step 7-8: DeadLetterService, RetryEngine, Observer — as before
# ... wire watcher_repo into each as needed ...
```

**Note**: `JobRecoveryService.__init__()` must be modified to accept optional `job_queue_service` parameter (backward compatible — default None).

---

## Key Files Summary

| File | Action | Purpose |
|------|--------|---------|
| `daemon/repositories/job_queue/watcher_models.py` | **CREATE** | JobWatcher with JSON column (dead_letter in defaults) |
| `daemon/repositories/job_queue/watcher_repository.py` | **CREATE** | CRUD + reconciliation queries |
| `daemon/services/job_queue_service.py` | **MODIFY** | `notify_watchers()` + calls from paths 2, 3, 3b |
| `daemon/services/job_feedback_observer.py` | **MODIFY** | Call shared notifier (path 1) |
| `daemon/services/instance_lifecycle.py` | **MODIFY** | Cleanup + covered by path 4 (via complete_job_sync) |
| `daemon/services/dead_letter_service.py` | **MODIFY** | Notify after commit in `move_to_dlq_standalone()` (path 5) |
| `daemon/services/job_retry_engine.py` | **MODIFY** | Notify after commit when `move_to_dlq()` called (path 6) |
| `daemon/services/job_recovery_service.py` | **MODIFY** | Notify after `atomic_transition()` in `_fail_orphaned_job()` (path 7) |
| `daemon/tools/job_queue.py` | **MODIFY** | 4 tools + atomic watch=True + dead_letter in defaults |
| `daemon/tools/instance.py` | **MODIFY** | Pass watcher_repo |
| `daemon/api.py` | **MODIFY** | Bootstrap ordering + startup reconciliation + wire recovery service |

## Constraints
- Must be backward compatible — if `watcher_repo` is None, everything works as before
- Watch notifications must be async and non-blocking
- Max 50 watches per instance to prevent abuse
- Notification message format must include structured JSON block
- Source prefix must be `internal_agent:` for correct message classification
- `notify_watchers()` failure must not break job processing (try/except wrapper)
- `move_to_dlq()` shared-session version must NOT contain notification code — call sites handle it after commit
- **Bootstrap ordering**: `watcher_repo` + `job_queue_service` must be wired into `JobRecoveryService` BEFORE `recover_on_startup()` runs
- Startup notifications (Paths 6, 7, reconciliation) queue as DB messages — delivered when watching instance resumes
- Zero performance impact on existing job processing

## Deliverables
- [ ] Shared `notify_watchers()` called from **all 7** terminal paths
- [ ] Atomic `watch=True` registration before enqueue
- [ ] `watch_job()` immediate notification for already-terminal jobs (including dead_letter)
- [ ] Default `watch_events` includes all 5 terminal states: `completed`, `failed`, `cancelled`, `terminated`, `dead_letter`
- [ ] JSON column for `watch_events`
- [ ] Notifications with `internal_agent:` prefix + structured JSON block
- [ ] Dead letter path (standalone) calls `notify_watchers()` after commit (Path 5)
- [ ] Retry engine calls `notify_watchers()` after commit on DLQ move (Path 6)
- [ ] **Orphan recovery calls `notify_watchers()` after atomic_transition (Path 7)**
- [ ] **Bootstrap ordering verified: watcher_repo ready before `recover_on_startup()`**
- [ ] Startup reconciliation for crash recovery
- [ ] All 4 new tools + backward compatibility
- [ ] Auto-cleanup on instance termination
