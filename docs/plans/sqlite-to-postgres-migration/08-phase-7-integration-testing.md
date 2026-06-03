# Phase 7: Integration Testing

> **Effort**: 4-6 hours
> **Priority**: P0 MANDATORY
> **Risk**: Low (testing only)

## Goal

Comprehensive end-to-end testing of the entire migration flow. Verify all acceptance criteria from previous phases. Ensure no regressions in existing functionality.

## Test Categories

### 1. End-to-End Migration Tests

**File**: `tests/integration/test_e2e_migration.py` (NEW)

```python
"""End-to-end migration tests.

Tests the complete flow: SQLite → Migration Worker → PostgreSQL.
Requires both SQLite (file) and PostgreSQL (test instance) available.
"""
import pytest
from sqlalchemy import select, func
from sqlmodel import Session

from daemon.services.migration_worker import MigrationWorker


@pytest.mark.asyncio
async def test_full_migration_with_realistic_data(
    sqlite_manager, postgres_engine, tmp_path
):
    """Migrate realistic dataset (100s of rows across all tables)."""
    # 1. Create test data in SQLite
    # ... insert instances, tasks, messages, checkpoints ...
    
    # 2. Run migration
    worker = MigrationWorker(manager, config)
    await worker.run()
    
    # 3. Verify all data in PostgreSQL
    # ... assert row counts match ...
    # ... assert specific records match ...
    
    # 4. Verify status
    assert worker.status == MigrationStatus.COMPLETED


@pytest.mark.asyncio
async def test_migration_with_empty_database():
    """Migration handles empty database gracefully."""
    # No data in SQLite
    worker = MigrationWorker(manager, config)
    await worker.run()
    assert worker.status == MigrationStatus.COMPLETED


@pytest.mark.asyncio
async def test_migration_with_large_dataset():
    """Migration handles 10K+ rows without OOM."""
    # Insert 10K rows
    # Run migration
    # Verify all rows migrated
    pass


@pytest.mark.asyncio
async def test_migration_resumability():
    """Failed migration can be resumed."""
    # Start migration
    # Simulate failure at 50%
    # Restart migration
    # Verify it resumes from last completed table
    pass
```

### 2. Checkpoint Integrity Tests

**File**: `tests/integration/test_checkpoint_integrity.py` (NEW)

```python
"""Test that checkpoints survive migration without corruption."""
import pytest
from langgraph.graph import StateGraph


@pytest.mark.asyncio
async def test_conversation_history_preserved():
    """Full conversation history readable after migration."""
    # 1. Create conversation in SQLite
    # ... run graph with multiple steps ...
    
    # 2. Migrate
    worker = MigrationWorker(manager, config)
    await worker.run()
    
    # 3. Read conversation from PostgreSQL
    # ... load graph with PostgreSQL checkpointer ...
    state = await compiled.aget_state(config)
    
    # 4. Verify all messages preserved
    assert len(state.values["messages"]) == original_count


@pytest.mark.asyncio
async def test_checkpoint_metadata_preserved():
    """Checkpoint metadata (timestamps, user ids) survives migration."""
    pass


@pytest.mark.asyncio
async def test_writes_table_integrity():
    """Writes table data migrates correctly."""
    pass


@pytest.mark.asyncio
async def test_binary_blob_transfer():
    """Pickle-serialized blobs transfer correctly."""
    pass
```

### 3. Rollback Tests

**File**: `tests/integration/test_rollback.py` (NEW)

```python
"""Test rollback scenarios."""
import pytest


@pytest.mark.asyncio
async def test_rollback_via_config_edit():
    """Changing config back to sqlite works after partial migration."""
    # 1. Start migration
    # 2. Stop mid-migration
    # 3. Edit config to "sqlite"
    # 4. Restart daemon
    # 5. Verify daemon uses SQLite
    # 6. Verify all data still accessible
    pass


@pytest.mark.asyncio
async def test_rollback_after_completed_migration():
    """Rollback works even after successful migration."""
    # 1. Complete migration
    # 2. Edit config to "sqlite"
    # 3. Restart daemon
    # 4. Verify daemon uses SQLite
    # 5. Verify SQLite data untouched
    pass


@pytest.mark.asyncio
async def test_failed_migration_preserves_sqlite():
    """Failed migration doesn't corrupt SQLite."""
    # 1. Start migration with invalid PostgreSQL config
    # 2. Migration fails
    # 3. Verify SQLite database is unchanged
    pass
```

### 4. Concurrent Access Tests

**File**: `tests/integration/test_concurrent_access.py` (NEW)

```python
"""Test behavior during migration with concurrent requests."""
import pytest
import asyncio


@pytest.mark.asyncio
async def test_writes_paused_during_migration():
    """Write operations queued during migration, replayed after."""
    # 1. Start migration
    # 2. Attempt write during migration
    # 3. Verify write is queued
    # 4. Wait for migration to complete
    # 5. Verify write is replayed to PostgreSQL
    pass


@pytest.mark.asyncio
async def test_reads_work_during_migration():
    """Read operations work during migration (against SQLite)."""
    # 1. Start migration
    # 2. Perform read operations
    # 3. Verify reads succeed (against SQLite source)
    pass


@pytest.mark.asyncio
async def test_sse_progress_events():
    """SSE stream emits progress events during migration."""
    # 1. Start migration
    # 2. Subscribe to SSE
    # 3. Verify events received for each phase
    # 4. Verify row counts in events
    pass
```

### 5. Configuration Tests

**File**: `tests/integration/test_config_flow.py` (NEW)

```python
"""Test configuration loading and auto-detection."""
import pytest
from pathlib import Path


def test_auto_create_ensemble_json_on_database_url(monkeypatch, tmp_path):
    """DATABASE_URL env var triggers ensemble.json auto-creation."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
    
    config = load_persistence_config(
        config_yaml_path=tmp_path / "config.yaml",  # doesn't exist
        ensemble_json_path=tmp_path / "ensemble.json",
    )
    
    assert config.database == "postgres"
    assert (tmp_path / "ensemble.json").exists()


def test_ensemble_json_takes_priority_over_config_yaml(tmp_path):
    """ensemble.json overrides config.yaml."""
    # Create both files
    # Load config
    # Verify ensemble.json wins
    pass


def test_rollback_by_editing_ensemble_json(tmp_path):
    """Editing ensemble.json back to sqlite works."""
    # 1. Start with postgres
    # 2. Edit to sqlite
    # 3. Reload config
    # 4. Verify uses sqlite
    pass
```

### 6. Frontend Integration Tests

**File**: `frontend/src/app/settings/database/database.component.integration.spec.ts` (NEW)

```typescript
describe('Database Component Integration', () => {
  let component: DatabaseComponent;
  let httpMock: HttpTestingController;
  let migrationService: MigrationService;

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [DatabaseComponent, HttpClientTestingModule],
      providers: [MigrationService],
    });

    component = TestBed.createComponent(DatabaseComponent).componentInstance;
    httpMock = TestBed.inject(HttpTestingController);
    migrationService = TestBed.inject(MigrationService);
  });

  it('triggers migration on button click', () => {
    spyOn(window, 'confirm').and.returnValue(true);
    
    component.startMigration();
    
    const req = httpMock.expectOne('/api/migration/start');
    expect(req.request.method).toBe('POST');
    req.flush({ status: 'started' });
  });

  it('shows progress during migration', () => {
    // Start migration
    // Simulate SSE event
    // Verify progress signal updates
  });

  it('handles migration failure', () => {
    // Simulate failure event
    // Verify error state
  });
});
```

## Test Execution

### Run All Migration Tests

```bash
# Backend
pytest tests/integration/test_e2e_migration.py -v
pytest tests/integration/test_checkpoint_integrity.py -v
pytest tests/integration/test_rollback.py -v
pytest tests/integration/test_concurrent_access.py -v
pytest tests/integration/test_config_flow.py -v

# Frontend
cd frontend && ng test --include='**/database.component**'
```

### Pre-Migration Checklist

- [ ] All 22 tables have test data
- [ ] At least 1 LangGraph checkpoint exists
- [ ] PostgreSQL test instance running
- [ ] SQLite test database created
- [ ] Migration worker initialized

### Post-Migration Verification

- [ ] All row counts match (SQLite vs PostgreSQL)
- [ ] All checkpoints readable from PostgreSQL
- [ ] Daemon starts with PostgreSQL config
- [ ] All API endpoints work
- [ ] Frontend shows PostgreSQL as current database
- [ ] Rollback to SQLite works

## Performance Benchmarks

```python
@pytest.mark.benchmark
@pytest.mark.asyncio
async def test_migration_performance(benchmark):
    """Migration completes in reasonable time."""
    # 1000 rows
    # Target: < 30 seconds
    
    result = benchmark(worker.run)
    assert result is not None
```

**Expected Performance**:
- 1K rows: < 5 seconds
- 10K rows: < 30 seconds
- 100K rows: < 5 minutes

## Acceptance Criteria

- [ ] All end-to-end tests pass
- [ ] All checkpoint integrity tests pass
- [ ] All rollback tests pass
- [ ] All concurrent access tests pass
- [ ] All configuration tests pass
- [ ] All frontend integration tests pass
- [ ] Performance benchmarks meet targets
- [ ] No regressions in existing tests
- [ ] Test coverage > 80% for new code
- [ ] All edge cases covered (empty DB, large DB, failures)

## Test Data Setup

### Script: Generate Test Data

**File**: `tests/fixtures/generate_migration_test_data.py` (NEW)

```python
"""Generate realistic test data for migration tests."""
import random
from datetime import datetime, timedelta
from sqlmodel import Session

from daemon.repositories.instance.models import Instance
from daemon.repositories.task.models import Task
from daemon.repositories.message.models import Message


def generate_test_data(engine, scale: str = "small"):
    """Generate test data.
    
    Args:
        engine: SQLAlchemy engine (SQLite)
        scale: "small" (100 rows), "medium" (1K rows), "large" (10K rows)
    """
    counts = {
        "small": 100,
        "medium": 1000,
        "large": 10000,
    }
    n = counts[scale]
    
    with Session(engine) as session:
        for i in range(n):
            instance = Instance(
                project=f"test-project-{i}",
                status="running",
                created_at=datetime.utcnow().isoformat(),
            )
            session.add(instance)
            
            # Add related tasks and messages
            for j in range(3):
                task = Task(
                    instance_id=instance.id,
                    name=f"task-{j}",
                    status="completed",
                )
                session.add(task)
        
        session.commit()
```

## Rollback Plan

If tests fail:
1. Identify failing test
2. Fix bug in corresponding phase
3. Re-run tests
4. No data risk (tests use isolated test databases)

## Estimated Diff Size

- 1 file new: `tests/integration/test_e2e_migration.py` (+200 lines)
- 1 file new: `tests/integration/test_checkpoint_integrity.py` (+150 lines)
- 1 file new: `tests/integration/test_rollback.py` (+100 lines)
- 1 file new: `tests/integration/test_concurrent_access.py` (+150 lines)
- 1 file new: `tests/integration/test_config_flow.py` (+100 lines)
- 1 file new: `tests/fixtures/generate_migration_test_data.py` (+80 lines)
- 1 file new: `frontend/src/app/settings/database/database.component.integration.spec.ts` (+100 lines)

**Total**: 7 files new, ~880 lines

## Final Acceptance Criteria

Before shipping the feature:
- [ ] All phases 0-6 complete
- [ ] All Phase 7 tests pass
- [ ] No regressions in existing test suite
- [ ] Documentation updated (README, API docs)
- [ ] Migration guide written
- [ ] Rollback procedure documented
- [ ] Performance benchmarks meet targets
- [ ] Security review completed
- [ ] User acceptance testing completed

## Next Steps

After Phase 7 completion:
1. Update main README with PostgreSQL support
2. Write user-facing migration guide
3. Add migration section to API documentation
4. Create demo video / screenshots
5. Announce feature in release notes
