# Phase 4: Checkpoint Migration Strategy

> **Effort**: 2-3 hours
> **Priority**: High
> **Risk**: Medium (checkpoint corruption = lost conversation history)

## Goal

Verify that LangGraph checkpoints can be safely migrated from SQLite to PostgreSQL. Implement and test the export/import strategy for checkpoint data.

## Decisions

- **Strategy**: Export/import with downtime window (Option B)
- **Data transfer**: Raw row copy (checkpoints are pickle-serialized, both savers use same structure)
- **Verification**: Test that checkpoints created in SQLite can be read after import to PostgreSQL
- **Scope**: Migrate all checkpoints, even stale ones (preserves full conversation history)

## Changes

### 1. Checkpoint Schema Verification

**File**: `daemon/migrations/checkpoint_migrator.py` (NEW)

```python
"""LangGraph checkpoint migration logic.

Handles export/import of checkpoints from AsyncSqliteSaver to
AsyncPostgresSaver. Both savers use the same table structure
(checkpoints + writes), so raw row copy works.

Schema verification:
- SQLite: checkpoints (thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, type, checkpoint, metadata, ...)
- PostgreSQL: checkpoints (same columns, bytea instead of blob)
"""
import logging
from typing import AsyncIterator
from sqlalchemy import select, insert
from sqlalchemy.ext.asyncio import AsyncEngine

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

logger = logging.getLogger(__name__)


async def verify_checkpoint_schema_compatibility(
    sqlite_saver: AsyncSqliteSaver,
    postgres_saver: AsyncPostgresSaver,
) -> bool:
    """Verify both savers have compatible schema.
    
    Returns True if schemas match, False otherwise.
    """
    # Both savers should have 'checkpoints' and 'writes' tables
    # with the same column structure
    return True  # Implementation: compare via inspection


async def export_checkpoints_from_sqlite(
    sqlite_saver: AsyncSqliteSaver,
) -> AsyncIterator[dict]:
    """Yield all checkpoints from SQLite.
    
    Yields dicts with table='checkpoints' or 'writes' and row data.
    """
    # Implementation: SELECT * FROM checkpoints, then SELECT * FROM writes
    pass


async def import_checkpoint_to_postgres(
    postgres_saver: AsyncPostgresSaver,
    checkpoint_data: dict,
) -> None:
    """Insert a single checkpoint into PostgreSQL.
    
    Args:
        postgres_saver: Target PostgreSQL checkpointer
        checkpoint_data: Dict from export_checkpoints_from_sqlite()
    """
    # Implementation: INSERT INTO checkpoints (...) VALUES (...)
    pass
```

### 2. Checkpoint Integrity Test

**File**: `tests/integration/test_checkpoint_migration.py` (NEW)

```python
"""Test that checkpoints survive SQLite → PostgreSQL migration."""
import pytest
from langgraph.graph import StateGraph
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from daemon.migrations.checkpoint_migrator import (
    export_checkpoints_from_sqlite,
    import_checkpoint_to_postgres,
)


@pytest.mark.asyncio
async def test_checkpoint_round_trip(tmp_path, postgres_saver):
    """Checkpoint created in SQLite can be read from PostgreSQL after migration."""
    # 1. Create checkpoint in SQLite
    sqlite_db = tmp_path / "test.db"
    sqlite_saver = AsyncSqliteSaver.from_conn_string(f"sqlite:///{sqlite_db}")
    await sqlite_saver.setup()
    
    # Create a simple graph and run it
    graph = StateGraph(dict)
    graph.add_node("test", lambda state: {"result": "ok"})
    graph.set_entry_point("test")
    graph.set_finish_point("test")
    
    compiled = graph.compile(checkpointer=sqlite_saver)
    config = {"configurable": {"thread_id": "test-1"}}
    await compiled.ainvoke({"input": "test"}, config)
    
    # 2. Export from SQLite
    checkpoints = [c async for c in export_checkpoints_from_sqlite(sqlite_saver)]
    assert len(checkpoints) > 0
    
    # 3. Import to PostgreSQL
    for cp_data in checkpoints:
        await import_checkpoint_to_postgres(postgres_saver, cp_data)
    
    # 4. Read back from PostgreSQL
    postgres_compiled = graph.compile(checkpointer=postgres_saver)
    state = await postgres_compiled.aget_state(config)
    
    # 5. Verify state matches
    assert state.values == {"result": "ok"}, "Checkpoint data corrupted during migration"


@pytest.mark.asyncio
async def test_writes_table_migration(tmp_path, postgres_saver):
    """Writes table data migrates correctly."""
    # Similar test for writes table
    pass


@pytest.mark.asyncio
async def test_binary_data_integrity(postgres_saver):
    """Pickle-serialized data survives transfer."""
    # Test with complex Python objects in checkpoint
    pass
```

### 3. Add to Migration Worker (Phase 5)

The actual migration logic will be implemented in Phase 5, but the test framework and helper functions are established here.

## Testing

### Unit Test: Schema Compatibility

```python
def test_checkpoint_schema_compatibility():
    """Both savers have compatible schema."""
    from daemon.migrations.checkpoint_migrator import verify_checkpoint_schema_compatibility
    
    # Mock savers
    sqlite_saver = Mock()
    postgres_saver = Mock()
    
    result = verify_checkpoint_schema_compatibility(sqlite_saver, postgres_saver)
    assert result is True
```

### Integration Test: Round-Trip

The main test is the round-trip test above, which:
1. Creates a checkpoint in SQLite
2. Exports it
3. Imports to PostgreSQL
4. Reads it back
5. Verifies data integrity

### Performance Test

```python
@pytest.mark.asyncio
async def test_checkpoint_migration_performance(postgres_saver):
    """Migration of 100 checkpoints completes in reasonable time."""
    # Create 100 checkpoints
    # Migrate them
    # Measure time
    # Assert < 30 seconds
    pass
```

## Acceptance Criteria

- [ ] `checkpoint_migrator.py` module with export/import functions
- [ ] Schema compatibility verification function
- [ ] Round-trip test passes (SQLite → PostgreSQL → read back)
- [ ] Writes table migration test passes
- [ ] Binary data integrity test passes
- [ ] Performance test shows acceptable migration speed
- [ ] No data corruption in any test scenario
- [ ] All existing checkpoint tests pass (no regressions)

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Pickle serialization incompatibility | Round-trip test catches this early |
| Column type mismatch (BLOB vs BYTEA) | Schema compatibility check |
| Missing rows during transfer | Row count validation after migration |
| Concurrent writes during migration | Write pausing (implemented in Phase 5) |

## Rollback Plan

If checkpoint migration fails:
1. Stop migration immediately
2. PostgreSQL checkpoints table is empty or partial
3. User can retry migration
4. User can rollback to SQLite via config edit
5. No SQLite data loss (source is untouched)

## Estimated Diff Size

- 1 file new: `daemon/migrations/checkpoint_migrator.py` (+80 lines)
- 1 file new: `tests/integration/test_checkpoint_migration.py` (+100 lines)

**Total**: 2 files new, ~180 lines

## Next Phase

[Phase 5: Migration Worker + API](./06-phase-5-migration-worker.md)
