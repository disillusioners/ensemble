# Phase 3: Migration Worker, API & Write-Pausing

## Objective

Build the core migration worker that transfers data from SQLite to PostgreSQL with real-time SSE progress reporting, implement write-pausing during migration, create the migration API router with cancel support, and define the complete API contract consumed by the frontend.

## Coupling

- **Depends on**: Phase 2 (needs PostgreSQL engine, `CheckpointerAdapter`, verified schema compatibility)
- **Coupling type**: tight
- **Shared files with other phases**: `daemon/services/migration_worker.py` (NEW), `daemon/routers/migration.py` (NEW)
- **Shared APIs/interfaces**: Full API contract defined below — Phase 4 consumes these endpoints
- **Why this coupling**: Uses PostgreSQL engine + checkpointer adapter from Phase 2 to create schema, insert data, and migrate checkpoints

## Context

- **22 tables** across 8 model files, all using standard SQLModel types
- **31 SQL migration files** in `daemon/migrations/versions/` — 5 confirmed with SQLite-specific syntax. All will be skipped for PostgreSQL (Phase 1 guards).
- **Checkpoint data**: `data_dev/checkpoints.db` is 24MB — migration must handle large data without OOM
- **Existing SSE pattern**: `sse_starlette.sse.EventSourceResponse` with `asyncio.Queue` per connection (see `notifications.py`, `messages.py`)
- **DI pattern**: `create_service_dependency()` factory in `daemon/utils.py`

## API Contract (Phase 3 ↔ Phase 4)

### Types

```typescript
// Shared between backend and frontend

interface MigrationAvailability {
  migration_available: boolean;    // true if PG ENV set AND current DB is SQLite
  current_database: "sqlite" | "postgres";
  postgres_configured: boolean;    // PG ENV vars present?
  can_start: boolean;              // false if already on PG or migration running
}

interface MigrationStatusResponse {
  status: "idle" | "running" | "completed" | "failed" | "cancelled";
  current_phase: string | null;    // "initializing" | "migrating_tables" | etc.
  current_table: string | null;
  tables_completed: number;
  tables_total: number;
  checkpoints_migrated: number;
  error: string | null;
  started_at: string | null;       // ISO 8601
  completed_at: string | null;     // ISO 8601
}

interface MigrationStartResponse {
  migration_id: string;
  status: "running";
  message: string;
}

interface MigrationCancelResponse {
  status: "cancelled";
  message: string;
}

// SSE event types
type MigrationSSEEvent =
  | { event: "progress"; data: MigrationProgressEvent }
  | { event: "log"; data: MigrationLogEvent }
  | { event: "complete"; data: MigrationCompleteEvent }
  | { event: "error"; data: MigrationErrorEvent }
  | { event: "cancelled"; data: MigrationCancelledEvent };

interface MigrationProgressEvent {
  phase: string;
  table: string | null;
  rows_total: number;
  rows_migrated: number;
  status: "running";
  timestamp: string;
}

interface MigrationLogEvent {
  level: "info" | "warn" | "error";
  message: string;
  timestamp: string;
}

interface MigrationCompleteEvent {
  status: "completed";
  tables_migrated: number;
  checkpoints_migrated: number;
  message: string;
  timestamp: string;
}

interface MigrationErrorEvent {
  status: "failed";
  error: string;
  message: string;
  timestamp: string;
}

interface MigrationCancelledEvent {
  status: "cancelled";
  message: string;
  timestamp: string;
}
```

### Endpoints

| Method | Path | Success | Error |
|--------|------|---------|-------|
| `GET` | `/api/migration/availability` | 200 + `MigrationAvailability` | — |
| `GET` | `/api/migration/status` | 200 + `MigrationStatusResponse` | — |
| `POST` | `/api/migration/start` | 202 + `MigrationStartResponse` | 409 (already running), 400 (not eligible) |
| `POST` | `/api/migration/cancel` | 200 + `MigrationCancelResponse` | 409 (not running) |
| `GET` | `/api/migration/events` | SSE stream (`MigrationSSEEvent`) | — |

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Implement write-pause mechanism | Add `WritePauseGuard` (threading.Event + atomic counter) on `InstanceManager`. Methods: `pause_writes()` (set flag, wait for drain), `resume_writes()` (clear flag), `is_write_paused` property. Two-layer enforcement: async gate before `asyncio.to_thread()` + sync guard via `WriteGuardSession` for direct Session sites. | `daemon/manager.py`, `daemon/utils.py` (or new guard module) |
| 2 | Create table migration order | Build dependency-aware ordered list of 22 tables respecting FK constraints (parents before children). Verify with `ForeignKey` introspection from `SQLModel.metadata`. | `daemon/migrations/data_migrator.py` (NEW) |
| 3 | Implement idempotent batch data migrator | Per-table: read from SQLite → `INSERT ... ON CONFLICT DO NOTHING` into PostgreSQL (500 rows/batch). Idempotent: safe to retry without truncating. Use `server-side cursor` for large tables to avoid OOM. | `daemon/migrations/data_migrator.py` |
| 4 | Implement checkpoint migrator | Export checkpoints from SQLite (via `SqliteCheckpointerAdapter`) → import into PostgreSQL (via `PostgresCheckpointerAdapter`). Handle serialization format differences identified in Phase 2 Task 10. | `daemon/migrations/checkpoint_migrator.py` (NEW) |
| 5 | Create MigrationWorker service | Background worker orchestrating: pause writes → create PG schema → migrate tables → migrate checkpoints → validate → update `ensemble.json` → resume writes. State machine: `idle → running → completed/failed/cancelled`. Uses `asyncio.Lock` to prevent concurrent starts. | `daemon/services/migration_worker.py` (NEW) |
| 6 | Implement cancel support | Add `CANCELLED` state to state machine. Worker checks `self._cancel_requested` flag between batches. Cancel endpoint sets flag. Clean shutdown: resume writes before exiting. | `daemon/services/migration_worker.py` |
| 7 | Create migration API router | 5 endpoints as defined in API contract. SSE endpoint uses `asyncio.Queue` pattern (same as existing `notifications.py`). Wire via `create_service_dependency()` factory. | `daemon/routers/migration.py` (NEW) |
| 8 | Wire migration router into app | Register in `daemon/routers/__init__.py`. Wire `MigrationWorker` in lifespan: create worker with manager reference, store on `app.state`. | `daemon/routers/__init__.py`, `daemon/api.py` |
| 9 | Implement validation step | After migration: compare row counts between SQLite and PostgreSQL for all 22 tables + checkpoint counts. Report mismatches in SSE. | `daemon/services/migration_worker.py` |
| 10 | Update `ensemble.json` on completion | After successful validation: atomically write `"database": "postgres"` to `ensemble.json`. This is the switch-over point. Daemon reads new config on NEXT restart. | `daemon/services/migration_worker.py`, `daemon/ensemble_config.py` |

## Key Files

### New Files
- `daemon/migrations/data_migrator.py` — Table ordering + idempotent batch migration
- `daemon/migrations/checkpoint_migrator.py` — Checkpoint export/import
- `daemon/services/migration_worker.py` — Migration orchestrator with SSE + cancel
- `daemon/routers/migration.py` — 5 API endpoints

### Modified Files
- `daemon/manager.py` — Add write-pause mechanism (`pause_writes`, `resume_writes`, `is_write_paused`)
- `daemon/routers/__init__.py` — Register migration router
- `daemon/api.py` — Wire MigrationWorker in lifespan, register router

## Table Migration Order (FK-respecting)

**CRITICAL**: Must use `SQLModel.metadata.sorted_tables` to generate the order at runtime, not a hardcoded list. PostgreSQL validates foreign keys BEFORE conflict detection — out-of-order inserts produce FK violations that `ON CONFLICT DO NOTHING` cannot swallow.

`SQLModel.metadata.sorted_tables` returns tables in topological order (parents before children), which is exactly what we need. Example:

```python
from sqlmodel import SQLModel
from daemon.repositories.instance.models import Instance, InstanceHierarchy, InstanceStatus
# ... import all models to register with SQLModel.metadata ...

sorted_tables = SQLModel.metadata.sorted_tables
# Returns: [projects, instances, job_queues, ...] in correct FK order
```

The hardcoded reference order below is for documentation purposes only:

```
 1. projects, schema_migrations, mcp_servers, task, event (no FK deps)
 2. critical_notes, project_metadata_records, project_tags, project_shortnames, 
    project_history, instances (FK → projects)
 3. instance_hierarchy (FK → instances)
 4. source_configs (FK → instances)
 5. instance_mappings (FK → source_configs)
 6. processed_external_messages, schedule_executions (FK → source_configs)
 7. job_queue_items (FK → job_queues, instances)
 8. job_locks (FK → job_queue_items)
 9. dead_letter_items (FK → instances)
10. job_watchers (FK → instances, job_queue_items)
11. message_queue (FK → task)
```

## Migration Worker State Machine

```
IDLE ──(POST /start)──► RUNNING ──► COMPLETED
    │                      │  ▲
    │                      │  │
    │                      ▼  │
    │                   CANCELLED (POST /cancel)
    │                      │
    └──────────────────────┘ (reset on next start)
                      │
                      ▼
                    FAILED
```

- **IDLE**: No migration in progress. Can start.
- **RUNNING**: Migration active. Protected by `asyncio.Lock`. Checks `_cancel_requested` between batches.
- **COMPLETED**: Migration done. `ensemble.json` updated to `"database": "postgres"`. User must restart daemon.
- **FAILED**: Migration errored. Error message in `/status`. Can retry (idempotent — `ON CONFLICT DO NOTHING`).
- **CANCELLED**: User cancelled. Writes resumed. Can retry.

## Idempotency Strategy

Every table migration uses `INSERT ... ON CONFLICT DO NOTHING` with an **explicit per-table conflict target**:

```sql
INSERT INTO {table} ({columns}) VALUES ({values})
ON CONFLICT ({primary_key_columns}) DO NOTHING
```

**Explicit conflict targets** (not implicit): Each table must declare which columns constitute the unique constraint for conflict detection. Build a `CONFLICT_TARGETS` map at startup by introspecting each table's primary key from `SQLModel.metadata`:

```python
def build_conflict_targets() -> dict[str, list[str]]:
    """Map table_name → [pk_column_names] for ON CONFLICT targets."""
    targets = {}
    for table in SQLModel.metadata.sorted_tables:
        pk_cols = [col.name for col in table.primary_key.columns]
        targets[table.name] = pk_cols
    return targets
```

This means:
- First run: inserts all rows
- Retry after failure: skips already-inserted rows, continues from where it stopped
- No need for persistent progress tracking
- No need to truncate tables before retry

## Type Coercion During Data Migration

**CRITICAL**: Direct SQL `INSERT INTO pg SELECT * FROM sqlite` will NOT work because:
- SQLite `BOOLEAN DEFAULT 0` stores `0`/`1`, PostgreSQL expects `TRUE`/`FALSE`
- SQLite `JSON` stores as text, PostgreSQL `JSONB` needs proper JSON parsing
- SQLite `TEXT` timestamps need validation for PostgreSQL `TIMESTAMP` compat

**Solution**: Use **ORM-layer migration** (read as SQLModel objects from SQLite, write to PostgreSQL):

```python
def migrate_table(sqlite_engine, pg_engine, model_class, conflict_cols):
    """Read rows as SQLModel objects (auto-coerced types), write to PG."""
    with Session(sqlite_engine) as src:
        rows = src.exec(select(model_class)).all()

    with Session(pg_engine) as dst:
        for row in rows:
            # SQLModel object has Python-native types (bool, dict, datetime)
            # SQLAlchemy driver handles PG-specific serialization
            stmt = insert(model_class).values(**row.model_dump())
            stmt = stmt.on_conflict_do_nothing(index_elements=conflict_cols)
            dst.execute(stmt)
        dst.commit()
```

Reading via `Session.exec(select(Model))` returns Python objects with correct types (`bool`, `dict`, `datetime`). Writing via `Session.execute(insert(Model))` lets the PostgreSQL driver (`psycopg`) handle serialization. No manual type coercion needed.

## Write-Pause Mechanism

### Design Choice: `threading.Event` + Atomic Counter

**Why not `asyncio` primitives**: The codebase runs synchronous SQLAlchemy writes inside `asyncio.to_thread()` worker threads (~70+ call sites across routers, `ThreadPoolExecutor(max_workers=4)`). It also runs sync `Session()` directly inside `async def` functions (6 sites in services/tools). `asyncio.Lock` and `asyncio.Event` raise `RuntimeError` when used from non-event-loop threads. The write-pause mechanism MUST work across the async/sync boundary.

**Chosen**: `threading.Event` + `threading`-safe atomic counter. Both work identically from the event loop thread and from worker threads.

```python
import threading

class WritePauseGuard:
    """Thread-safe write-pause mechanism.

    Works across async/sync boundary:
    - Migration worker calls pause_writes() from async context
    - Repositories call write_enter()/write_exit() from sync worker threads
      (via asyncio.to_thread) or directly from async functions
    """

    def __init__(self):
        self._write_paused = False
        self._active_writes = 0
        self._lock = threading.Lock()  # Protects counter
        self._drain_event = threading.Event()  # Signaled when counter hits 0
        self._drain_event.set()  # Initially: no writes in flight

    @property
    def is_write_paused(self) -> bool:
        return self._write_paused

    def pause_writes(self) -> None:
        """Block new writes, wait for in-flight writes to finish.

        Called by migration worker (async, but this is sync-safe).
        Can be awaited or called directly.
        """
        self._write_paused = True
        # Wait for all in-flight writes to complete
        self._drain_event.wait()

    def resume_writes(self) -> None:
        """Allow writes again."""
        self._write_paused = False

    def write_enter(self) -> None:
        """Called at start of every write operation (from any thread)."""
        if self._write_paused:
            raise RuntimeError("Writes are paused for database migration")
        with self._lock:
            self._active_writes += 1
            self._drain_event.clear()

    def write_exit(self) -> None:
        """Called at end of every write operation (from any thread)."""
        with self._lock:
            self._active_writes -= 1
            if self._active_writes == 0:
                self._drain_event.set()
```

### Enforcement: Async-Gate Above `to_thread()` + Sync Guard Below

Writes flow through TWO layers, both must be controlled:

**Layer 1 — Async gate** (router/service async functions): Check `_write_guard.is_write_paused` BEFORE calling `asyncio.to_thread()`. This prevents dispatching new write work to the thread pool while paused.

```python
# In router async functions (e.g., projects.py, sources.py):
@router.post("/")
async def create_project(...):
    if manager._write_guard.is_write_paused:
        raise HTTPException(503, "Writes paused for database migration")
    project = await asyncio.to_thread(repo.save, ...)
```

**Layer 2 — Sync guard** (inside thread, at Session level): `WriteGuardSession` wraps `Session`, calls `write_enter()`/`write_exit()` around write operations. This catches the 6 direct `Session()` sites that bypass the thread pool.

```python
class WriteGuardSession:
    """Wraps a SQLModel Session. Calls write_enter/write_exit around writes."""

    def __init__(self, session: Session, guard: WritePauseGuard):
        self._session = session
        self._guard = guard

    def __enter__(self):
        self._guard.write_enter()
        return self

    def __exit__(self, *args):
        self._guard.write_exit()
        self._session.close()

    def add(self, instance):
        return self._session.add(instance)

    def delete(self, instance):
        return self._session.delete(instance)

    def execute(self, statement, *args, **kwargs):
        return self._session.execute(statement, *args, **kwargs)

    def commit(self):
        self._session.commit()

    # Read methods pass through:
    def exec(self, statement, *args, **kwargs):
        return self._session.exec(statement, *args, **kwargs)

    def get(self, *args, **kwargs):
        return self._session.get(*args, **kwargs)

    def flush(self):
        self._session.flush()

    # ... delegate remaining Session methods ...
```

### Why Not Other Options

| Option | Rejected Because |
|--------|-----------------|
| `asyncio.Lock` for drain | Raises `RuntimeError` from `to_thread()` worker threads |
| `asyncio.Event` for drain | Same — cannot `set()`/`clear()` from non-event-loop threads |
| `threading.Lock` for drain | Would block the event loop if acquired from async context without `run_in_executor` |
| Acquire gate above `to_thread()` only | Misses 6 direct `Session()` sites in async functions |
| Per-method decoration | ~30+ methods across 12+ files, easy to miss one |

### Unguarded Paths (Safe — Startup Only)

`engine.connect()` + `conn.execute(text(...))` exists in 4 sites:
- `daemon/migrations/runner.py` (lines 140, 170, 296) — **startup only**, skipped for PostgreSQL (Phase 1 Task 3)
- `daemon/repositories/factory.py` (line 202) — `run_migrations()` — **startup only**, skipped for PostgreSQL (Phase 1 Task 2)

These run before the migration worker exists and will never execute during a live migration. No guard needed.

### Affected Write Paths

| File | Methods (write operations) |
|------|---------------------------|
| `repositories/instance/repository.py` | `save()`, `update_status()`, `delete()` |
| `repositories/project/repository.py` | `save()`, `update()`, `set_metadata_record()`, `delete_metadata_record()`, `add_history_entry()`, `delete_history_entry()` |
| `repositories/source/repository.py` | `save()`, `update()`, `delete()`, `save_mapping()`, `delete_mapping()`, `mark_message_processed()` |
| `repositories/job_queue/repository.py` | `create()`, `update_status()`, `soft_delete()` |
| `repositories/job_queue/queue_repository.py` | `create_queue()`, `update_queue()`, `delete_queue()` |
| `repositories/job_queue/dead_letter_repository.py` | `save()`, `delete()`, `delete_all()` |
| `repositories/job_queue/lock_repository.py` | `acquire_lock()`, `release_lock()` |
| `repositories/job_queue/watcher_models.py` (repo) | `create()`, `delete()` |
| `repositories/message_queue/repository.py` | `enqueue()`, `update_status()`, `delete()` |
| `repositories/mcp_server/repository.py` | `save()`, `update()`, `delete()` |
| `repositories/task/repository.py` | `create()`, `update()` |
| `repositories/event/repository.py` | `create()` |
| `services/instance_messaging.py` | Direct `Session()` writes (2 sites) |
| `services/child_reports.py` | Direct `Session()` writes (1 site) |
| `services/instance_lifecycle.py` | Direct `Session()` writes (1 site) |
| `services/error_reporting.py` | Direct `Session()` writes (1 site) |
| `tools/instance.py` | Direct `Session()` writes (1 site) |

**Implementation strategy**:
- Layer 1 (async gate): Add `is_write_paused` check in ~70 `asyncio.to_thread()` call sites in routers + services. This is the primary enforcement — catches all router-initiated writes.
- Layer 2 (sync guard): `WriteGuardSession` wraps the 6 direct `Session()` sites in services/tools. Use `WriteGuardSession` instead of `Session` at those 6 locations.

## Post-Migration Switch-Over

After successful migration + validation:
1. Worker calls `EnsembleConfig.update_database("postgres")`
2. This atomically writes `"database": "postgres"` to `ensemble.json`
3. Worker resumes writes
4. Worker sends `complete` SSE event
5. **Daemon continues using SQLite for THIS session** (no hot-swap)
6. Frontend shows: "Migration complete. Please restart the daemon to use PostgreSQL."
7. On next daemon restart → reads `ensemble.json` → starts with PostgreSQL

This is the **restart-required** approach. Simple, safe, no complex hot-swap logic.

## Constraints

- Migration is **additive** — SQLite data is never modified
- Batch size: 500 rows per insert to balance memory and performance
- Each table migrated idempotently (`ON CONFLICT DO NOTHING`)
- Use server-side cursors for large tables (>10K rows) to avoid OOM
- SSE stream must send keepalive every 15 seconds
- Only one migration at a time (enforced by `asyncio.Lock`)
- Cancel checks happen between batches (not mid-insert)
- Write-pausing must be bulletproof: all write paths must check the flag

## Deliverables

- [ ] `WritePauseGuard` with `threading.Event` + atomic counter (works across async/sync boundary)
- [ ] Async gate: `is_write_paused` check before `asyncio.to_thread()` in routers/services
- [ ] Sync guard: `WriteGuardSession` wrapping `Session` for 6 direct-Session sites
- [ ] `pause_writes()` / `resume_writes()` on `InstanceManager` delegating to `WritePauseGuard`
- [ ] FK-respecting table migration order via `SQLModel.metadata.sorted_tables` (not hardcoded)
- [ ] ORM-layer data migration (SQLModel objects, not raw SQL) for automatic type coercion
- [ ] Explicit per-table conflict targets from primary key introspection
- [ ] Idempotent batch data migrator with `ON CONFLICT (pk) DO NOTHING`
- [ ] Checkpoint export/import between SQLite and PostgreSQL
- [ ] `MigrationWorker` with 5-state machine + `asyncio.Lock` concurrency guard
- [ ] Cancel support with clean shutdown
- [ ] Migration API router with 5 endpoints matching API contract
- [ ] SSE progress stream with real-time events
- [ ] Row count validation after migration
- [ ] `ensemble.json` atomic update on completion
- [ ] Router wired into app lifespan
- [ ] API contract fully implemented (enables Phase 4 frontend work)
