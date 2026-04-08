# Phase 2: Backend Core Services

## Objective
Implement the repository, service, and processor changes needed to manage named queues, handle per-queue locking (including N-concurrent parallel queues), auto-provision system queues on project creation, and rework the `JobProcessor` to poll per-queue instead of globally.

## Coupling
- **Depends on**: Phase 1 (data models must exist)
- **Coupling type**: tight
- **Shared files with other phases**: 
  - `daemon/repositories/job_queue/repository.py` — shared with Phase 3
  - `daemon/services/job_queue_service.py` — shared with Phase 3
  - `daemon/services/job_processor.py` — shared with Phase 3
  - `daemon/services/job_lock_manager.py` — shared with Phase 3
- **Shared APIs/interfaces**: `JobQueueRepository`, `JobQueueService` public methods
- **Why this coupling**: Phase 2 implements the business logic that Phase 3 exposes via API

## Context
- Phase 1 created the `JobQueue` model, `QueueType` enum, `queue_name_lower` column, and migration
- Current `JobLockManager` uses `project_id → LockInfo` (1 lock per project)
- Current `JobProcessor` polls globally for pending jobs, checks project pause, acquires lock
- Current `JobQueueService` has `enqueue()`, `start_job()`, `complete_job()`, `trigger_next_job()`
- **W5 (Pre-existing Bug):** `start_job()` does `self.get()` then `self.update()` in separate DB sessions — not atomic
- **W6 (Pre-existing):** `_try_start_job()` uses `acquire_sync()` which bypasses `asyncio.Lock`

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | **Create `JobQueueRepository`** | New repository following existing pattern (sync, `__init__(engine)`, session-per-operation). Methods: `create()`, `get(queue_id)`, `get_by_name(project_id, queue_name)` (uses `queue_name_lower`), `list_by_project(project_id)`, `get_system_fifo(project_id)`, `get_system_parallel(project_id)`, `update(queue_id, **updates)`, `delete(queue_id)`, `count_jobs_by_status(queue_id)`, `count_by_queue_and_status(queue_id, statuses)`. Ensure all queries use `queue_name_lower` for lookups. | `daemon/repositories/job_queue/queue_repository.py` (new file) |
| 2 | **Extend `JobRepository`** | Add queue-aware query methods: `list_pending_by_queue(queue_id)`, `list_by_queue(queue_id, status, limit, offset)`, `reassign_queue_atomic(from_queue_id, to_queue_id, target_statuses)` (atomic conditional UPDATE for W4 fix), update `create()` to accept `queue_id`. Also refactor `start_job()` to use a single session — atomic transition from PENDING→PROCESSING (W5 fix). | `daemon/repositories/job_queue/repository.py` |
| 3 | **Rework `JobLockManager`** | Change from `project_id → LockInfo` to `(project_id, queue_id) → list[LockInfo]`. **REMOVE** `can_acquire()` as a separate public method. Instead, `acquire_queue_lock()` takes `concurrency_limit` and performs the capacity check **inside** the `asyncio.Lock` — making it atomic (C5 fix). New methods: `acquire_queue_lock(project_id, queue_id, job_id, instance_id, concurrency_limit)`, `release_queue_lock(project_id, queue_id, job_id)`, `is_queue_locked(project_id, queue_id)`, `get_queue_lock_count(project_id, queue_id)`. **Remove** all legacy `acquire(project_id, ...)` methods (C3 fix — lock manager must only operate on real queue IDs). **Remove** `acquire_sync()` — all lock operations must go through async path (W6 fix). | `daemon/services/job_lock_manager.py` |
| 4 | **Create `JobQueueService`** (queue management) | New service for queue CRUD operations: `create_queue(project_id, name, type, concurrency, description)`, `get_queue(queue_id)`, `list_queues(project_id)`, `update_queue(queue_id, **updates)` (validates name collision against system queues, validates FIFO concurrency), `delete_queue(queue_id)` (validates non-system, uses atomic SQL for orphan reassignment — W4 fix), `start_queue(queue_id)`, `stop_queue(queue_id)`, `auto_provision_system_queues(project_id)`. Reserved names: "system_fifo_queue" and "system_parallel_queue" cannot be used for custom queues. | `daemon/services/job_queue_mgmt_service.py` (new file) |
| 5 | **Extend `JobQueueService`** (job operations) | Update `enqueue()` to accept optional `queue_name` or `queue_id`. Resolve defaults at service layer: `project_id` set, no queue → resolve to `system_fifo_queue` for that project; `project_id` set, queue specified → validate queue exists and is not paused. Update `get_next_pending_job()` to accept optional `queue_id` filter. Update `start_job()` to use queue-aware locking and atomic repository call (W5 fix). Update `trigger_next_job()` to trigger per-queue. | `daemon/services/job_queue_service.py` |
| 6 | **Rework `JobProcessor`** | Change `_process_loop()` to iterate queues instead of global job list: (a) Get all active (non-paused) queues across all projects, (b) For each queue: call `acquire_queue_lock()` which atomically checks capacity + acquires, (c) Get next pending job for that queue, (d) Spawn instance. **Remove** separate `can_acquire()` call (C5 fix). Make all lock operations async — no `acquire_sync()` (W6 fix). Add inline comments explaining two-level pause check: queue `is_paused` first, then project `job_queue_paused` as master override. | `daemon/services/job_processor.py` |
| 7 | **Hook system queue auto-provisioning into project creation** | Hook at the **router/manager layer**, NOT in the repository (W2 fix). After `project_repo.create()` returns in the project creation endpoint, call `JobQueueService.auto_provision_system_queues(project_id)` via `asyncio.to_thread()` or `BackgroundTasks`. The repository must remain synchronous and unaware of queues. | `daemon/api.py` or `daemon/routers/projects.py` |
| 8 | **Handle queue deletion — orphaned jobs** | In `JobQueueService.delete_queue()`: (a) Validate queue is not system, (b) Validate no PROCESSING jobs exist → return `409 Conflict` if any, (c) Use `reassign_queue_atomic(from_queue_id, to_queue_id, target_statuses=["pending"])` — only reassign PENDING jobs, (d) Delete queue. Terminal jobs (completed/failed/cancelled) can be left with stale `queue_id`. | `daemon/services/job_queue_mgmt_service.py` |

## Key Files
- `daemon/repositories/job_queue/queue_repository.py` — **NEW**: Queue CRUD repository
- `daemon/repositories/job_queue/repository.py` — **MODIFY**: queue-aware queries, atomic `start_job()`, `reassign_queue_atomic()`
- `daemon/services/job_lock_manager.py` — **MODIFY**: per-queue locking, atomic `acquire_queue_lock()`, no phantom "default" (C3), no `acquire_sync()` (W6)
- `daemon/services/job_queue_mgmt_service.py` — **NEW**: Queue management service
- `daemon/services/job_queue_service.py` — **MODIFY**: queue-aware job ops, default resolution at service layer
- `daemon/services/job_processor.py` — **MODIFY**: per-queue polling, atomic locking, fully async
- `daemon/api.py` / `daemon/routers/projects.py` — **MODIFY**: Hook auto-provisioning (W2)

## Detailed Design: JobLockManager Extension

### New State
```python
_queue_locks: dict[tuple[str, str], list[LockInfo]]  # (project_id, queue_id) → [LockInfo, ...]
_lock: asyncio.Lock  # MUST be held for ALL lock/unlock operations
```

### Key Method — Atomic Acquisition (C5 fix)
```python
async def acquire_queue_lock(
    self,
    project_id: str,
    queue_id: str,
    job_id: str,
    instance_id: str,
    concurrency_limit: int,  # REQUIRED — no default
) -> bool:
    """
    Atomically: check capacity AND acquire under a single lock hold.
    Eliminates the TOCTOU race between can_acquire() and acquire().
    """
    async with self._lock:
        key = (project_id, queue_id)
        current_count = len(self._queue_locks.get(key, []))
        if current_count >= concurrency_limit:
            return False  # No capacity

        lock_info = LockInfo(job_id, project_id, queue_id, instance_id, datetime.now(timezone.utc))
        if key not in self._queue_locks:
            self._queue_locks[key] = []
        self._queue_locks[key].append(lock_info)
        return True
```

## Detailed Design: Auto-Provisioning Hook (W2 fix)

```python
# In daemon/routers/projects.py
@router.post("/", ..., status_code=201)
async def create_project(..., background_tasks: BackgroundTasks):
    project = await asyncio.to_thread(project_repo.create, **params)
    # Queue auto-provisioning runs after response is built
    background_tasks.add_task(
        job_queue_mgmt_service.auto_provision_system_queues,
        project.project_id
    )
    return project
```

## Constraints
- All repository calls remain synchronous (called via `asyncio.to_thread()`)
- Lock manager remains in-memory (not persisted to DB)
- Lock manager NEVER uses phantom queue names — only real queue IDs (C3)
- `acquire_queue_lock()` is fully atomic under `asyncio.Lock` — no separate capacity check (C5)
- All lock operations are async — no `acquire_sync()` bypass (W6)
- Queue deletion only affects PENDING jobs — PROCESSING jobs return `409 Conflict`
- FIFO queues must have `concurrency_limit=1` — enforced by validator
- Auto-provisioning hooked at router/manager layer, not repository (W2)

## Deliverables
- [ ] `JobQueueRepository` with CRUD + queries using `queue_name_lower`
- [ ] `JobRepository` with queue-aware queries, `start_job_atomic()` (W5), `reassign_queue_atomic()` (W4)
- [ ] `JobLockManager` with atomic `acquire_queue_lock()` (C5), no phantom "default" (C3), fully async (W6)
- [ ] `JobQueueService` (management) with reserved-name validation
- [ ] Queue deletion: PROCESSING jobs block (409), only PENDING jobs reassigned
- [ ] Queue deletion: atomic conditional UPDATE, no read-then-write (W4)
- [ ] `JobQueueService` (job ops): default queue resolution at service layer
- [ ] `JobProcessor`: per-queue polling with atomic locking (C5, W6)
- [ ] Auto-provisioning at router/manager layer (W2)
