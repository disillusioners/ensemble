# Plan: Background Maintenance Service + Checkpoint Cleanup

## Objective
Create a reusable `MaintenanceService` background loop and a `CheckpointCleanupJob` as its first registered job, cleaning up orphaned/expired checkpoint data. Also enable WAL mode on the checkpoint DB.

## Scope Assessment
**small** — Single logical component (maintenance loop + one job), ~5-6 files touched, follows established patterns in the codebase.

## Context
- Project: agents-ensemble
- Working Directory: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- The checkpoint DB (`data/checkpoints.db`) grows indefinitely with zero cleanup
- Config values already exist in `daemon/config.py` / `daemon/constants.py` but are unused (dead code)
- Existing background task patterns: `SourceCleanup` (simple loop), `RetryScheduler` (loop + lock)
- Terminated instances are **recoverable** — `resume_processing_job()` can revive them. Do NOT delete checkpoint data on termination alone.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Enable WAL mode on checkpoint DB | After `aiosqlite.connect()`, execute `PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000; PRAGMA synchronous=NORMAL;`. Keep in `get_checkpointer()`. | `daemon/persistence.py` |
| 2 | Create `MaintenanceService` class | Generic background loop in `daemon/services/maintenance.py`. Registers jobs, checks interval + idle condition, runs jobs. `start()` / `stop()` lifecycle. | `daemon/services/maintenance.py` (new) |
| 3 | Create `CheckpointCleanupJob` class | 4 cleanup operations as private methods. Registered with MaintenanceService on daemon startup. | `daemon/services/maintenance.py` (same file) |
| 4 | Add `MAINTENANCE_CHECK_INTERVAL_MINUTES` config | Add to `DaemonConfig` / `PersistenceConfig`. Default 15 minutes. | `daemon/config.py`, `daemon/constants.py` |
| 5 | Integrate into daemon startup | Start `MaintenanceService` in `InstanceManager.initialize()`, register cleanup job, cancel on shutdown. | `daemon/manager.py` |
| 6 | Write tests | Unit tests for interval/idle logic, each cleanup operation, integration test. | `tests/test_maintenance.py` (new) |

## Design Details

### MaintenanceService

```python
# daemon/services/maintenance.py

@dataclass
class MaintenanceJob:
    name: str
    min_interval_hours: float
    last_run: datetime | None
    execute: Callable[[], Coroutine[Any, Any, None]]

class MaintenanceService:
    def __init__(self, check_interval_minutes: int = 15):
        self._jobs: list[MaintenanceJob] = []
        self._check_interval = check_interval_minutes * 60
        self._task: asyncio.Task | None = None
        self._running = False
        # References for idle check (set by manager)
        self._job_queue_service: JobQueueService | None = None

    def register(self, name, min_interval_hours, execute_fn) -> None: ...
    def set_job_queue_service(self, svc) -> None: ...
    async def start(self) -> None: ...   # asyncio.create_task(self._loop())
    async def stop(self) -> None: ...    # cancel task, await

    async def _loop(self) -> None:
        await asyncio.sleep(60)  # initial delay
        while self._running:
            await self._run_pending_jobs()
            await asyncio.sleep(self._check_interval)

    async def _run_pending_jobs(self) -> None:
        for job in self._jobs:
            if self._is_due(job) and self._is_idle():
                await job.execute()
                job.last_run = utcnow()

    def _is_due(self, job) -> bool: ...
    def _is_idle(self) -> bool: ...  # Check job_queue for active jobs
```

### CheckpointCleanupJob

4 operations executed sequentially within the job's `execute()`:

| Op | Description | Logic |
|----|-------------|-------|
| A | Delete orphaned threads | SELECT thread_ids from checkpoints → subtract known instance_ids → DELETE orphans |
| B | Prune old terminal instances | SELECT terminal instances older than `CHECKPOINT_TTL_HOURS` → DELETE their checkpoint data |
| C | Enforce MAX_INSTANCE_HISTORY cap | Count terminal instances with checkpoint data → if > `CHECKPOINT_MAX_COUNT` → prune oldest by `updated_at` |
| D | Historical checkpoint pruning | For each active thread, keep only latest `CHECKPOINT_MAX_PER_THREAD` checkpoints |

**DB access**: Use `aiosqlite.connect()` directly to checkpoint DB (same pattern as `persistence.py`). Use instance repository for instance status queries.

### WAL Mode on Checkpoint DB

Add to `get_checkpointer()` in `persistence.py`:

```python
conn = await aiosqlite.connect(str(db_path))
await conn.execute("PRAGMA journal_mode=WAL")
await conn.execute("PRAGMA busy_timeout=5000")
await conn.execute("PRAGMA synchronous=NORMAL")
```

### Integration in manager.py

```python
# In initialize():
self._maintenance_service = MaintenanceService(
    check_interval_minutes=self._config.maintenance_check_interval_minutes
)
self._maintenance_service.set_job_queue_service(self._job_queue_service)
self._maintenance_service.register("checkpoint_cleanup", 24, self._checkpoint_cleanup.execute)
await self._maintenance_service.start()

# In shutdown:
await self._maintenance_service.stop()
```

### Config Changes

| File | Add | Default |
|------|-----|---------|
| `daemon/config.py` | `maintenance_check_interval_minutes: int` to `PersistenceConfig` | 15 |
| `daemon/constants.py` | `MAINTENANCE_CHECK_INTERVAL_MINUTES = 15` | 15 |

Repurpose `checkpoint_max_count` as `MAX_INSTANCE_HISTORY` (keep field name, update docs/comments).
Add `CHECKPOINT_MAX_PER_THREAD` = 1 to constants.

## Key Files

- `daemon/persistence.py` — WAL mode addition
- `daemon/services/maintenance.py` — **NEW** — MaintenanceService + CheckpointCleanupJob
- `daemon/config.py` — Add maintenance interval config
- `daemon/constants.py` — Add MAINTENANCE_CHECK_INTERVAL_MINUTES, CHECKPOINT_MAX_PER_THREAD
- `daemon/manager.py` — Integration (start/stop maintenance service)
- `tests/test_maintenance.py` — **NEW** — Tests

## Constraints
- Follow `SourceCleanup` pattern (simple loop with `start()`/`stop()`)
- Do NOT delete checkpoint data for terminated instances unless TTL exceeded
- Keep single file for maintenance service + checkpoint job (SMALL scope)
- Reuse existing config values rather than creating new ones where possible

## Success Criteria
- [ ] Checkpoint DB uses WAL mode
- [ ] `MaintenanceService` runs as background loop with configurable check interval
- [ ] `CheckpointCleanupJob` handles all 4 cleanup operations
- [ ] Jobs only run when due AND system is idle
- [ ] Graceful start/stop integrated into daemon lifecycle
- [ ] Unit tests cover interval logic, idle check, and each cleanup operation
- [ ] Terminated instances are NOT cleaned up unless TTL is exceeded

## Tracking
- Created: 2025-05-30
- Status: draft
