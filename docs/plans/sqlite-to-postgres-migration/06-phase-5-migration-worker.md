# Phase 5: Migration Worker + API

> **Effort**: 8-12 hours
> **Priority**: P0 CORE WORK
> **Risk**: High (data migration is the riskiest part)

## Goal

Implement the migration worker that transfers all data from SQLite to PostgreSQL with SSE progress streaming. This is the core functionality that makes the entire feature work.

## Decisions

- **Transaction strategy**: Per-table commits, 500-row batches
- **Progress reporting**: SSE stream with real-time updates
- **Write pausing**: Queue writes in memory during migration
- **Resumability**: Track completed tables in migration state table
- **Rollback**: Config flip (SQLite data never modified)

## Changes

### 1. Migration Worker Service

**File**: `daemon/services/migration_worker.py` (NEW)

```python
"""Background worker for SQLite → PostgreSQL data migration.

Handles the actual data transfer with progress reporting via SSE.
Migration is additive - SQLite data is never modified.
"""
import asyncio
import logging
from datetime import datetime
from enum import Enum
from typing import AsyncIterator

from sqlalchemy import select, insert, text
from sqlmodel import Session
from sse_starlette.sse import EventSourceResponse

from daemon.config import PersistenceConfig
from daemon.manager import InstanceManager

logger = logging.getLogger(__name__)


class MigrationStatus(str, Enum):
    """Migration status states."""
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class MigrationPhase(str, Enum):
    """Migration phases."""
    INITIALIZING = "initializing"
    MIGRATING_TABLES = "migrating_tables"
    MIGRATING_CHECKPOINTS = "migrating_checkpoints"
    VALIDATING = "validating"
    FINALIZING = "finalizing"


class MigrationProgress:
    """Progress event for SSE stream."""
    def __init__(
        self,
        phase: MigrationPhase,
        table: str | None = None,
        status: MigrationStatus = MigrationStatus.RUNNING,
        rows_total: int = 0,
        rows_migrated: int = 0,
        message: str = "",
    ):
        self.phase = phase
        self.table = table
        self.status = status
        self.rows_total = rows_total
        self.rows_migrated = rows_migrated
        self.message = message
        self.timestamp = datetime.utcnow().isoformat()
    
    def to_dict(self) -> dict:
        return {
            "phase": self.phase.value,
            "table": self.table,
            "status": self.status.value,
            "rows_total": self.rows_total,
            "rows_migrated": self.rows_migrated,
            "message": self.message,
            "timestamp": self.timestamp,
        }


class MigrationWorker:
    """Background worker for database migration.
    
    Coordinates the SQLite → PostgreSQL migration with progress
    reporting via SSE. Uses per-table commits for resumability.
    """
    
    BATCH_SIZE = 500
    
    def __init__(self, manager: InstanceManager, config: PersistenceConfig):
        self.manager = manager
        self.config = config
        self.status = MigrationStatus.IDLE
        self.progress_queue: asyncio.Queue[MigrationProgress] = asyncio.Queue()
        self._subscribers: list[asyncio.Queue] = []
    
    def subscribe(self) -> asyncio.Queue:
        """Subscribe to progress events.
        
        Returns a queue that receives MigrationProgress events.
        """
        queue = asyncio.Queue()
        self._subscribers.append(queue)
        return queue
    
    async def run(self) -> None:
        """Execute the full migration.
        
        Steps:
        1. Initialize PostgreSQL schema
        2. Migrate all tables (per-table commits)
        3. Migrate checkpoints
        4. Validate row counts
        5. Update ensemble.json
        """
        try:
            self.status = MigrationStatus.RUNNING
            await self._emit(MigrationProgress(MigrationPhase.INITIALIZING, message="Starting migration"))
            
            # 1. Initialize PostgreSQL schema
            await self._initialize_postgres_schema()
            
            # 2. Migrate tables
            await self._migrate_tables()
            
            # 3. Migrate checkpoints
            await self._migrate_checkpoints()
            
            # 4. Validate
            await self._validate()
            
            # 5. Update config
            await self._update_config()
            
            self.status = MigrationStatus.COMPLETED
            await self._emit(MigrationProgress(
                MigrationPhase.FINALIZING,
                status=MigrationStatus.COMPLETED,
                message="Migration completed successfully"
            ))
        
        except Exception as e:
            self.status = MigrationStatus.FAILED
            logger.exception("Migration failed")
            await self._emit(MigrationProgress(
                MigrationPhase.FINALIZING,
                status=MigrationStatus.FAILED,
                message=f"Migration failed: {e}"
            ))
            raise
    
    async def _initialize_postgres_schema(self) -> None:
        """Create PostgreSQL schema."""
        from daemon.migrations.postgres_schema import initialize_postgres_schema
        initialize_postgres_schema(self.manager.engine)
        await self._emit(MigrationProgress(
            MigrationPhase.INITIALIZING,
            message="PostgreSQL schema created"
        ))
    
    async def _migrate_tables(self) -> None:
        """Migrate all 22 tables with per-table commits."""
        from daemon.migrations.data_migrator import get_table_migration_order
        
        tables = get_table_migration_order()
        total_tables = len(tables)
        
        for idx, table in enumerate(tables, 1):
            await self._emit(MigrationProgress(
                MigrationPhase.MIGRATING_TABLES,
                table=table,
                message=f"Migrating table {idx}/{total_tables}: {table}"
            ))
            
            rows_migrated = await self._migrate_table(table)
            
            await self._emit(MigrationProgress(
                MigrationPhase.MIGRATING_TABLES,
                table=table,
                rows_migrated=rows_migrated,
                message=f"Completed {table} ({rows_migrated} rows)"
            ))
    
    async def _migrate_table(self, table_name: str) -> int:
        """Migrate a single table with batched inserts.
        
        Returns total rows migrated.
        """
        sqlite_engine = self._get_sqlite_engine()
        pg_engine = self.manager.engine
        
        # Read all rows from SQLite
        with Session(sqlite_engine) as sqlite_session:
            rows = sqlite_session.exec(select(text("*")).select_from(text(table_name))).all()
        
        total_rows = len(rows)
        if total_rows == 0:
            return 0
        
        # Batch insert into PostgreSQL
        migrated = 0
        for i in range(0, total_rows, self.BATCH_SIZE):
            batch = rows[i:i + self.BATCH_SIZE]
            
            with Session(pg_engine) as pg_session:
                for row in batch:
                    pg_session.execute(
                        text(f"INSERT INTO {table_name} VALUES ({','.join([':' + str(j+1) for j in range(len(row))])})"),
                        dict(row._mapping) if hasattr(row, '_mapping') else row.__dict__
                    )
                pg_session.commit()
            
            migrated += len(batch)
            
            # Emit progress
            await self._emit(MigrationProgress(
                MigrationPhase.MIGRATING_TABLES,
                table=table_name,
                rows_total=total_rows,
                rows_migrated=migrated,
                message=f"{table_name}: {migrated}/{total_rows}"
            ))
        
        return migrated
    
    async def _migrate_checkpoints(self) -> None:
        """Migrate LangGraph checkpoints."""
        from daemon.migrations.checkpoint_migrator import (
            export_checkpoints_from_sqlite,
            import_checkpoint_to_postgres,
        )
        
        # Get SQLite and PostgreSQL savers
        sqlite_saver = self._get_sqlite_saver()
        pg_saver = self._get_postgres_saver()
        
        # Ensure PostgreSQL schema exists
        await pg_saver.setup()
        
        # Export and import
        count = 0
        async for checkpoint_data in export_checkpoints_from_sqlite(sqlite_saver):
            await import_checkpoint_to_postgres(pg_saver, checkpoint_data)
            count += 1
            
            if count % 100 == 0:
                await self._emit(MigrationProgress(
                    MigrationPhase.MIGRATING_CHECKPOINTS,
                    rows_migrated=count,
                    message=f"Migrated {count} checkpoints"
                ))
        
        await self._emit(MigrationProgress(
            MigrationPhase.MIGRATING_CHECKPOINTS,
            rows_migrated=count,
            message=f"Completed checkpoint migration ({count} total)"
        ))
    
    async def _validate(self) -> None:
        """Validate row counts match between SQLite and PostgreSQL."""
        # Implementation: compare row counts for all tables
        await self._emit(MigrationProgress(
            MigrationPhase.VALIDATING,
            message="Validating migration"
        ))
    
    async def _update_config(self) -> None:
        """Update ensemble.json to use PostgreSQL."""
        # Implementation: write {"database": "postgres"} to ensemble.json
        pass
    
    async def _emit(self, progress: MigrationProgress) -> None:
        """Emit progress to all subscribers."""
        for queue in self._subscribers:
            await queue.put(progress)
    
    def _get_sqlite_engine(self):
        """Get SQLite engine (for reading source data)."""
        # Implementation: create separate engine for SQLite
        pass
    
    def _get_sqlite_saver(self):
        """Get SQLite checkpointer."""
        pass
    
    def _get_postgres_saver(self):
        """Get PostgreSQL checkpointer."""
        pass
```

### 2. Data Migrator Module

**File**: `daemon/migrations/data_migrator.py` (NEW)

```python
"""SQLite → PostgreSQL data migration logic.

Handles per-table data transfer with batched inserts and
dependency-aware table ordering.
"""
from typing import List


def get_table_migration_order() -> List[str]:
    """Return tables in dependency order (parents before children).
    
    This ensures foreign key constraints are satisfied during migration.
    """
    return [
        # Level 0: No dependencies
        "agent",
        "project",
        "user",
        
        # Level 1: Depend on level 0
        "instance",
        "message_queue",
        
        # Level 2: Depend on level 1
        "task",
        "message",
        "checkpoint",
        "write",
        
        # Level 3: Depend on level 2
        "child_report",
        "error_report",
        "job",
        # ... all 22 tables ...
    ]
```

### 3. API Endpoints

**File**: `daemon/routers/migration.py` (NEW)

```python
"""Migration API endpoints."""
import logging
from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/migration", tags=["migration"])


@router.post("/start")
async def start_migration(request: Request):
    """Start the database migration.
    
    Returns 202 Accepted with migration_id. Client should connect
    to /events for progress updates.
    """
    worker = request.app.state.migration_worker
    
    if worker.status.value == "running":
        raise HTTPException(409, "Migration already in progress")
    
    # Start migration in background
    import asyncio
    asyncio.create_task(worker.run())
    
    return {"status": "started", "migration_id": "current"}


@router.get("/status")
async def get_status(request: Request):
    """Get current migration status."""
    worker = request.app.state.migration_worker
    return {
        "status": worker.status.value,
    }


@router.get("/events")
async def migration_events(request: Request):
    """SSE stream of migration progress events."""
    worker = request.app.state.migration_worker
    queue = worker.subscribe()
    
    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                
                try:
                    progress = await asyncio.wait_for(queue.get(), timeout=1.0)
                    yield {
                        "event": "progress",
                        "data": progress.to_dict(),
                    }
                except asyncio.TimeoutError:
                    yield {"event": "heartbeat", "data": {}}
        finally:
            worker._subscribers.remove(queue)
    
    return EventSourceResponse(event_generator())
```

### 4. Update API Lifespan

**File**: `daemon/api.py`

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ... existing setup ...
    
    # Initialize migration worker
    from daemon.services.migration_worker import MigrationWorker
    app.state.migration_worker = MigrationWorker(manager, persistence_config)
    
    # Include migration router
    from daemon.routers.migration import router as migration_router
    app.include_router(migration_router)
    
    yield
    
    # ... existing shutdown ...
```

## Testing

### Unit Test: Migration Progress

```python
# tests/unit/test_migration_progress.py
def test_progress_to_dict():
    progress = MigrationProgress(
        phase=MigrationPhase.MIGRATING_TABLES,
        table="instance",
        rows_total=100,
        rows_migrated=50,
    )
    data = progress.to_dict()
    assert data["phase"] == "migrating_tables"
    assert data["table"] == "instance"
    assert data["rows_total"] == 100
    assert data["rows_migrated"] == 50
```

### Integration Test: Full Migration

```python
# tests/integration/test_full_migration.py
import pytest
from daemon.services.migration_worker import MigrationWorker

@pytest.mark.asyncio
async def test_migrate_sqlite_to_postgres(
    sqlite_manager, postgres_engine, tmp_path
):
    """Full migration from SQLite to PostgreSQL."""
    # 1. Create test data in SQLite
    # ... insert test instances, tasks, messages ...
    
    # 2. Run migration
    worker = MigrationWorker(manager, config)
    await worker.run()
    
    # 3. Verify data in PostgreSQL
    # ... query PostgreSQL and assert data matches ...
    
    # 4. Verify row counts
    assert worker.status == MigrationStatus.COMPLETED
```

### Integration Test: Migration with SSE

```python
@pytest.mark.asyncio
async def test_migration_emits_sse_events():
    """Migration emits progress events via SSE."""
    worker = MigrationWorker(manager, config)
    queue = worker.subscribe()
    
    # Start migration
    asyncio.create_task(worker.run())
    
    # Collect events
    events = []
    async for _ in range(10):  # Collect first 10 events
        event = await queue.get()
        events.append(event)
    
    # Verify events emitted
    assert len(events) > 0
    assert any(e.phase == MigrationPhase.MIGRATING_TABLES for e in events)
```

## Acceptance Criteria

- [ ] `MigrationWorker` service with async run() method
- [ ] Per-table commits with 500-row batches
- [ ] SSE progress streaming via asyncio.Queue
- [ ] API endpoints: `/start`, `/status`, `/events`
- [ ] Migration state tracking (running, completed, failed)
- [ ] Resumability via migration state table
- [ ] Write pausing during migration
- [ ] Row count validation after migration
- [ ] Config update after successful migration
- [ ] Unit tests for progress tracking
- [ ] Integration test for full migration flow
- [ ] SSE events test
- [ ] No data loss in any failure scenario

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| OOM during large table migration | 500-row batches, per-table commits |
| Connection pool exhaustion | Separate engines for SQLite/PostgreSQL |
| Partial migration failure | Per-table commits = resumable |
| Concurrent writes during migration | Write queue (in-memory) |
| Network failure during PostgreSQL write | Per-batch commits, retry logic |

## Rollback Plan

If migration fails at any point:
1. Stop migration worker
2. PostgreSQL database has partial data
3. SQLite database is untouched
4. User can:
   - Retry migration (resumes from last completed table)
   - Rollback to SQLite via config edit (`"database": "sqlite"`)
5. No data loss possible

## Estimated Diff Size

- 1 file new: `daemon/services/migration_worker.py` (+300 lines)
- 1 file new: `daemon/migrations/data_migrator.py` (+50 lines)
- 1 file new: `daemon/routers/migration.py` (+80 lines)
- 1 file modified: `daemon/api.py` (+10 lines)

**Total**: 3 files new, 1 file modified, ~440 lines

## Next Phase

[Phase 6: Frontend Settings Sub-page](./07-phase-6-frontend.md)
