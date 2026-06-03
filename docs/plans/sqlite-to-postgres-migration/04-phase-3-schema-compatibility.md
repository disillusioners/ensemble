# Phase 3: Schema Compatibility

> **Effort**: 2-3 hours
> **Priority**: High
> **Risk**: Medium (schema mismatches can cause data corruption)

## Goal

Ensure schema compatibility between SQLite and PostgreSQL. The existing `MigrationRunner` (for schema migrations) must be guarded against PostgreSQL. PostgreSQL gets a fresh schema via `SQLModel.metadata.create_all()`.

## Decisions

- **MigrationRunner stays SQLite-only** - it uses `sqlite_master` and `PRAGMA` which don't work on PostgreSQL
- **PostgreSQL schema**: Use `SQLModel.metadata.create_all()` for fresh schema creation
- **Schema validation**: Verify all 22 tables can be created on PostgreSQL without errors

## Changes

### 1. Guard MigrationRunner

**File**: `daemon/migrations/runner.py`

**Before**:
```python
class MigrationRunner:
    def __init__(self, engine: Engine):
        self.engine = engine
    
    async def run_migrations(self):
        # Uses sqlite_master, PRAGMA table_info()
        # ... SQLite-specific code ...
```

**After**:
```python
class MigrationRunner:
    def __init__(self, engine: Engine, config: PersistenceConfig):
        self.engine = engine
        self.config = config
        
        if config.is_postgres:
            raise ValueError(
                "MigrationRunner is SQLite-only. "
                "PostgreSQL uses SQLModel.metadata.create_all()."
            )
    
    async def run_migrations(self):
        # ... existing SQLite-specific code ...
```

### 2. Add PostgreSQL Schema Initializer

**File**: `daemon/migrations/postgres_schema.py` (NEW)

```python
"""PostgreSQL schema initialization.

PostgreSQL uses SQLModel.metadata.create_all() instead of the
SQL-based migration runner. This module handles PostgreSQL-specific
schema setup.
"""
import logging
from sqlalchemy import Engine
from sqlmodel import SQLModel

logger = logging.getLogger(__name__)


def initialize_postgres_schema(engine: Engine) -> None:
    """Create all SQLModel tables on PostgreSQL.
    
    This is called once on first PostgreSQL initialization or after
    a migration. It creates the full schema fresh - no incremental
    migrations needed.
    
    Args:
        engine: PostgreSQL engine
    """
    logger.info("Creating PostgreSQL schema via SQLModel.metadata.create_all()")
    
    # Import all models to ensure they're registered with SQLModel.metadata
    from daemon.repositories.instance.models import Instance
    from daemon.repositories.task.models import Task
    from daemon.repositories.message.models import Message
    # ... import all 8 model files ...
    
    SQLModel.metadata.create_all(engine)
    logger.info("PostgreSQL schema created successfully")


def verify_schema_compatibility(engine: Engine) -> list[str]:
    """Verify PostgreSQL schema matches expected model schema.
    
    Returns list of warnings/issues found. Empty list = all good.
    """
    # Implementation: compare SQLModel metadata with PostgreSQL information_schema
    # Return list of missing tables, columns, or type mismatches
    warnings = []
    # ... implementation ...
    return warnings
```

### 3. Update Manager Initialization

**File**: `daemon/manager.py`

**Before**:
```python
async def initialize(self):
    config = load_config()
    self._engine = create_engine_from_config(persistence_config)
    
    # Run migrations
    runner = MigrationRunner(self._engine)
    await runner.run_migrations()
```

**After**:
```python
async def initialize(self):
    config = load_config()
    persistence_config = load_persistence_config()
    self._engine = create_engine_from_config(persistence_config)
    
    if persistence_config.is_postgres:
        # PostgreSQL: create fresh schema
        initialize_postgres_schema(self._engine)
    else:
        # SQLite: run SQL-based migrations
        runner = MigrationRunner(self._engine, persistence_config)
        await runner.run_migrations()
```

### 4. Schema Type Compatibility Audit

**File**: `docs/plans/sqlite-to-postgres-migration/schema-compatibility-audit.md` (NEW)

Document any model-level changes needed for PostgreSQL compatibility:

| Model | SQLite Type | PostgreSQL Type | Issue | Fix |
|-------|-------------|-----------------|-------|-----|
| `Instance.created_at` | TEXT | TIMESTAMPTZ | String vs datetime | Models use `str`, both backends accept |
| `Message.content` | TEXT | TEXT | None | ✓ |
| `Task.status` | TEXT (enum) | TEXT (enum) | None | ✓ |
| `*JSON fields* | TEXT | JSONB | SQLModel handles | ✓ |

**Finding**: All 22 tables are SQLModel-agnostic. `SQLModel.metadata.create_all()` will generate correct PostgreSQL schema automatically.

## Testing

### Unit Test: MigrationRunner Guard

```python
# tests/unit/test_migration_runner_guard.py
import pytest
from daemon.migrations.runner import MigrationRunner
from daemon.config import PersistenceConfig, PostgresConfig

def test_migration_runner_rejects_postgres():
    """MigrationRunner raises on PostgreSQL config."""
    config = PersistenceConfig(
        database="postgres",
        postgres=PostgresConfig(url="postgresql://localhost/test"),
    )
    engine = create_engine(config.postgres.url)
    
    with pytest.raises(ValueError, match="MigrationRunner is SQLite-only"):
        MigrationRunner(engine, config)
```

### Integration Test: PostgreSQL Schema Creation

```python
# tests/integration/test_postgres_schema.py
import pytest
from sqlalchemy import inspect
from daemon.migrations.postgres_schema import initialize_postgres_schema

@pytest.mark.asyncio
async def test_create_all_tables_on_postgres(postgres_engine):
    """All 22 tables can be created on PostgreSQL."""
    initialize_postgres_schema(postgres_engine)
    
    inspector = inspect(postgres_engine)
    tables = inspector.get_table_names()
    
    # Verify expected tables exist
    expected_tables = [
        "instance", "task", "message", "message_queue",
        "checkpoint", "write", "error_report", "child_report",
        # ... all 22 tables ...
    ]
    
    for table in expected_tables:
        assert table in tables, f"Missing table: {table}"


@pytest.mark.asyncio
async def test_schema_is_idempotent(postgres_engine):
    """Running create_all twice doesn't error."""
    initialize_postgres_schema(postgres_engine)
    initialize_postgres_schema(postgres_engine)  # Should not raise
```

### Schema Validation Test

```python
def test_verify_schema_compatibility(postgres_engine):
    """Schema validation returns no warnings."""
    initialize_postgres_schema(postgres_engine)
    warnings = verify_schema_compatibility(postgres_engine)
    assert len(warnings) == 0, f"Schema issues: {warnings}"
```

## Acceptance Criteria

- [ ] `MigrationRunner` raises clear error on PostgreSQL config
- [ ] `initialize_postgres_schema()` creates all 22 tables successfully
- [ ] Schema creation is idempotent (can run multiple times)
- [ ] Schema validation returns no warnings
- [ ] Manager uses correct initialization path per backend
- [ ] All existing SQLite tests pass (no regressions)
- [ ] Integration test creates all tables on PostgreSQL
- [ ] Schema compatibility audit document created

## Rollback Plan

If issues arise:
1. Revert `daemon/migrations/runner.py` changes
2. Delete `daemon/migrations/postgres_schema.py`
3. Revert `daemon/manager.py` changes
4. Existing SQLite migration runner works as before

No data migration needed—schema-only changes.

## Estimated Diff Size

- 1 file modified: `daemon/migrations/runner.py` (+10 lines)
- 1 file new: `daemon/migrations/postgres_schema.py` (+50 lines)
- 1 file modified: `daemon/manager.py` (+10 lines, -5 lines)
- 1 file new: `schema-compatibility-audit.md` (+30 lines)

**Total**: 3 files modified, 2 files new, ~100 lines

## Next Phase

[Phase 4: Checkpoint Migration Strategy](./05-phase-4-checkpoint-strategy.md)
