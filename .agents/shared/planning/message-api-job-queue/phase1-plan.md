# Phase 1: Model & Infra — JobType Field, Repository Queries, Queue Resolution

## Objective

Extend the JobQueue data model and infrastructure to support a `MESSAGE` job type: add `job_type` to `JobItem`, add the repository query methods needed for DB-level concurrency gating and termination cleanup, update `enqueue()` to accept `job_type` + `instance_id` and route MESSAGE jobs to `system_parallel_queue`, override `start_job()` for MESSAGE jobs, add `requeue` state transition.

## Coupling

- **Depends on**: None
- **Coupling type**: — (root phase)
- **Shared files with other phases**: `models.py`, `repository.py`, `job_queue_service.py`, `job_state_machine.py`
- **Shared APIs/interfaces**: `job_type` field, `find_processing_message_jobs_by_instance()`, `find_jobs_by_instance()`, `enqueue(job_type=..., instance_id=...)`, `atomic_transition(requeue)`, `start_job()` MESSAGE override
- **Why this coupling**: Phase 2 uses all types and methods added here.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add `job_type` field to `JobItem` model | `job_type: str = Field(default="task")` — values: `"task"`, `"message"`. No migration needed (default covers existing rows). | `daemon/repositories/job_queue/models.py` |
| 2 | Add `find_processing_message_jobs_by_instance()` to `JobRepository` | Indexed query on `JobItem.instance_id` column: `status == "processing" AND job_type == "message" AND instance_id == ? AND deleted_at IS NULL`. No Python-side JSON filtering. | `daemon/repositories/job_queue/repository.py` |
| 3 | Add `find_jobs_by_instance()` to `JobRepository` | Query on `JobItem.instance_id` column: `instance_id == ? AND status IN ("pending", "processing") AND deleted_at IS NULL`, optional `job_type` filter. Used for termination cleanup. | `daemon/repositories/job_queue/repository.py` |
| 4 | Update `enqueue()` — accept `job_type` + `instance_id` | Add `job_type: str = "task"` and `instance_id: str | None = None` parameters. Store in `JobItem` columns directly. Also update `_repository.create()` signature. | `daemon/services/job_queue_service.py`, `daemon/repositories/job_queue/repository.py` |
| 5 | Update `enqueue()` queue resolution for MESSAGE jobs | When `job_type == "message"` and `queue_id is None`, resolve `system_parallel_queue` instead of `system_fifo_queue`. | `daemon/services/job_queue_service.py` |
| 6 | Override `start_job()` instance_id for MESSAGE jobs | When `job.job_type == "message"`, use `job.instance_id` (set at enqueue time) instead of generating `uuid.uuid4()`. Prevents random UUID in `JobItem.instance_id`. | `daemon/services/job_queue_service.py` |
| 7 | Verify `auto_provision_system_queues()` creates `system_parallel_queue` | Confirm `system_parallel_queue` exists for all projects at startup. (Exploration shows it IS created by `auto_provision_system_queues()` in `job_queue_mgmt_service.py:91-106`.) | `daemon/services/job_queue_mgmt_service.py` (verify only) |
| 8 | Add `requeue` transition to `JobStateMachine` | Add `(PROCESSING, PENDING) → "requeue"` transition. Needed by Phase 2 safety-net back-transition. | `daemon/services/job_state_machine.py` |

## Detailed Design

### Task 1: `job_type` field

```python
# daemon/repositories/job_queue/models.py — JobItem class
class JobItem(SQLModel, table=True):
    # ... existing fields ...
    job_type: str = Field(default="task")  # "task" | "message"
```

No migration needed — SQLModel/SQLite will handle the new column with default value for existing rows (or you add a simple ALTER TABLE if strict mode).

### Task 2: `find_processing_message_jobs_by_instance()`

```python
# daemon/repositories/job_queue/repository.py
def find_processing_message_jobs_by_instance(self, instance_id: str) -> list[JobItem]:
    """Find PROCESSING MESSAGE jobs targeting a specific instance.
    
    Used for DB-level concurrency gate: if any exist, skip starting a new MESSAGE job.
    Uses JobItem.instance_id column (indexed) — no JSON filtering needed.
    """
    with SQLModelSession(self.engine) as db_session:
        stmt = (
            select(JobItem)
            .where(JobItem.status == JobStatus.PROCESSING.value)
            .where(JobItem.job_type == "message")
            .where(JobItem.instance_id == instance_id)
            .where(JobItem.deleted_at.is_(None))
        )
        return list(db_session.exec(stmt))
```

### Task 3: `find_jobs_by_instance()`

```python
# daemon/repositories/job_queue/repository.py
def find_jobs_by_instance(self, instance_id: str, job_type: str | None = None) -> list[JobItem]:
    """Find all active jobs for a given instance.
    
    Used for termination cleanup: cancel ALL MESSAGE jobs for an instance.
    Uses JobItem.instance_id column (indexed) — no JSON filtering needed.
    """
    with SQLModelSession(self.engine) as db_session:
        stmt = (
            select(JobItem)
            .where(JobItem.instance_id == instance_id)
            .where(JobItem.deleted_at.is_(None))
            .where(JobItem.status.in_(["pending", "processing"]))
        )
        if job_type:
            stmt = stmt.where(JobItem.job_type == job_type)
        return list(db_session.exec(stmt))
```

### Task 4: `enqueue()` accepts `job_type` + `instance_id`

#### 4a. Signature update:

```python
# daemon/services/job_queue_service.py — enqueue() signature
async def enqueue(
    self,
    agent_id: str,
    message: str,
    source: str = "api",
    project_id: str | None = None,
    priority: int = 5,
    metadata: dict[str, Any] | None = None,
    queue_id: str | None = None,
    idempotency_key: str | None = None,
    job_type: str = "task",       # NEW — "task" | "message"
    instance_id: str | None = None,  # NEW — set for MESSAGE jobs at enqueue time
) -> JobItem:
```

#### 4b. Body changes — `job_type` and `instance_id` flow to `_repository.create()`:

```python
# After normalize_project_id, idempotency check, agent_dir derivation,
# and queue_id resolution (all unchanged — see Task 5 for queue resolution changes):

# Create job with PENDING status — pass job_type and instance_id through
job = await asyncio.to_thread(
    self._repository.create,
    agent_id=agent_id,
    agent_dir=agent_dir,
    message=message,
    source=source,
    project_id=project_id,
    priority=priority,
    job_metadata=metadata,      # message_id, source, images still in metadata
    queue_id=resolved_queue_id,
    idempotency_key=idempotency_key,
    job_type=job_type,          # NEW — stored in JobItem.job_type column
    instance_id=instance_id,    # NEW — stored in JobItem.instance_id column (for MESSAGE jobs)
)

# Notify dispatch bus of new job (unchanged)
if self._dispatch_bus is not None:
    self._dispatch_bus.notify_new_job(project_id)

return job
```

#### 4c. `_repository.create()` update to accept `job_type` and `instance_id`:

```python
# daemon/repositories/job_queue/repository.py — create() update
def create(
    self,
    agent_id: str,
    agent_dir: str,
    message: str,
    source: str = "api",
    project_id: str | None = None,
    priority: int = 5,
    job_metadata: dict[str, Any | None] = None,
    queue_id: str | None = None,
    idempotency_key: str | None = None,
    job_type: str = "task",          # NEW
    instance_id: str | None = None,  # NEW — pre-set for MESSAGE jobs
) -> JobItem:
    with SQLModelSession(self.engine) as db_session:
        job = JobItem(
            job_id=str(uuid4()),
            agent_id=agent_id,
            agent_dir=agent_dir,
            message=message,
            source=source,
            project_id=project_id,
            priority=priority,
            status=JobStatus.PENDING.value,
            job_metadata=job_metadata or {},
            queue_id=queue_id,
            idempotency_key=idempotency_key,
            job_type=job_type,          # NEW
            instance_id=instance_id,    # NEW — None for TASK jobs, set for MESSAGE jobs
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        db_session.add(job)
        db_session.commit()
        db_session.refresh(job)
        return job
```

**Summary of flow**:
```
caller (enqueue_message_via_jq)
  → enqueue(job_type="message", instance_id=instance_id, metadata={"message_id": ..., ...})
    → _repository.create(job_type="message", instance_id=instance_id)
      → JobItem(job_type="message", instance_id="actual-target-uuid")
```

### Task 5: Queue resolution for MESSAGE jobs

```python
# In enqueue(), after normalize_project_id() — lines 304-331
# REPLACE the existing resolution block with:

resolved_queue_id = queue_id
if queue_id is None:  # project_id is always normalized (never None after line 257)
    if job_type == "message":
        # MESSAGE jobs → system_parallel_queue (parallel execution)
        queue = await asyncio.to_thread(
            self._queue_repo.get_by_name, project_id, "system_parallel_queue"
        )
    else:
        # TASK jobs → system_fifo_queue (serial execution, existing behavior)
        queue = await asyncio.to_thread(
            self._queue_repo.get_by_name, project_id, "system_fifo_queue"
        )
    if queue is not None:
        resolved_queue_id = queue.queue_id
    else:
        raise ValueError(
            f"No system {'parallel' if job_type == 'message' else 'fifo'} queue found "
            f"for project {project_id}. Ensure system queues are provisioned."
        )
```

**Note**: Condition changed from `if project_id and queue_id is None:` to `if queue_id is None:` because `normalize_project_id()` at line 257 always produces a valid `project_id` (converting None → `SYSTEM_DEFAULT_PROJECT_ID`). The old condition was never false after normalization.

### Task 6: Override `start_job()` for MESSAGE jobs

```python
# In start_job() at job_queue_service.py:891
# REPLACE: instance_id = str(uuid.uuid4())
# WITH:

if job.job_type == "message" and job.instance_id:
    # MESSAGE jobs: use the target instance_id (set at enqueue time)
    instance_id = job.instance_id
else:
    # TASK jobs: generate new UUID for the spawned instance
    instance_id = str(uuid.uuid4())
```

This is a 4-line change in the existing `start_job()` method. The rest of `start_job()` is unchanged — the `instance_id` variable flows to `acquire_queue_lock()`, `acquire()`, and `start_job_atomic()` as before.

**Why this matters**: Without this fix, `start_job_atomic(job_id, instance_id)` would write a random UUID to `JobItem.instance_id`, breaking:
- `find_processing_message_jobs_by_instance()` (queries by column)
- `find_jobs_by_instance()` (queries by column)
- `get_by_instance()` (existing method)
- Lock tracking (`JobLock.instance_id`)
- Orphan recovery (re-spawns targeting wrong UUID)

### Task 8: Add `requeue` transition to state machine

The safety-net check in Phase 2's `MessageJobHandler` needs to atomically transition a MESSAGE job back from PROCESSING → PENDING when the instance is already busy. The current state machine does NOT have this transition:

```python
# Current transitions from PROCESSING:
(PROCESSING, COMPLETED): "complete"
(PROCESSING, FAILED):    "fail"
(PROCESSING, CANCELLED): "abort"
# MISSING: (PROCESSING, PENDING) ← needed for requeue
```

**Add to `daemon/services/job_state_machine.py`**:

```python
# In the TRANSITIONS dict, add:
(_STATUS_PROCESSING, _STATUS_PENDING): "requeue",
```

This is a single-line addition. The transition is valid because:
- A job that was just started (PROCESSING) but can't actually run yet (instance busy) should go back to PENDING
- The job will be picked up on the next poll cycle when the instance is free
- `atomic_transition(job_id, from_status="processing", to_status="pending")` will now succeed

**Caution**: This transition should ONLY be used by the MESSAGE safety-net, never by TASK jobs. TASK jobs that start processing should complete/fail/cancel, not requeue.

## Key Files

- `daemon/repositories/job_queue/models.py` — Add `job_type` field to `JobItem`
- `daemon/repositories/job_queue/repository.py` — Add indexed queries using `JobItem.instance_id`, add `job_type` + `instance_id` to `create()`
- `daemon/services/job_queue_service.py` — Add `job_type` + `instance_id` to `enqueue()`, update queue resolution, override `start_job()` instance_id
- `daemon/services/job_queue_mgmt_service.py` — Verify only (system_parallel_queue creation)
- `daemon/services/job_state_machine.py` — Add `PROCESSING → PENDING` "requeue" transition

## Constraints

- `job_type` defaults to `"task"` — all existing code and callers work unchanged
- `instance_id` defaults to `None` in `create()` and `enqueue()` — backward compatible (TASK jobs leave it None, `start_job()` generates UUID for them)
- `instance_id` is stored in the **existing** `JobItem.instance_id` column — no schema migration beyond `job_type`
- No changes to `_process_message_with_tracking()`, WorkerPool, or `_process_message_with_tracking()`

## Deliverables

- [ ] `JobItem` has `job_type` column with default `"task"`
- [ ] `JobRepository.create()` accepts `job_type` and `instance_id` parameters
- [ ] `find_processing_message_jobs_by_instance(instance_id)` uses indexed `JobItem.instance_id` column query
- [ ] `find_jobs_by_instance(instance_id, job_type)` uses indexed `JobItem.instance_id` column query
- [ ] `JobQueueService.enqueue()` accepts `job_type` + `instance_id` and routes MESSAGE → `system_parallel_queue`
- [ ] `start_job()` uses `job.instance_id` for MESSAGE jobs instead of generating random UUID
- [ ] `JobStateMachine` has `(PROCESSING, PENDING) → "requeue"` transition
- [ ] Verified: `system_parallel_queue` exists for all projects at startup (via `auto_provision_system_queues()`)
