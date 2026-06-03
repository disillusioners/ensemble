# Phase 2: Driver + Checkpoint Abstraction

> **Effort**: 4-6 hours
> **Priority**: High
> **Risk**: Medium (driver changes can affect all DB access)

## Goal

Add PostgreSQL driver support and abstract the LangGraph checkpointer to support both SQLite and PostgreSQL backends. This is the foundation for all subsequent migration work.

## Decisions

- **PostgreSQL driver**: `psycopg[binary]` (v3) for SQLAlchemy/SQLModel
- **LangGraph checkpointer**: `asyncpg` (bundled with `langgraph-checkpoint-postgres`)
- **Abstraction**: `get_checkpointer()` returns `AsyncSqliteSaver` or `AsyncPostgresSaver` based on config

## Changes

### 1. Update Dependencies

**File**: `pyproject.toml`

```toml
[project]
dependencies = [
    # ... existing dependencies ...
    
    # PostgreSQL support
    "psycopg[binary]>=3.2.0",
    "asyncpg>=0.30.0",
    "langgraph-checkpoint-postgres>=2.0.0",
]
```

**Install**:
```bash
uv add psycopg[binary] asyncpg langgraph-checkpoint-postgres
```

### 2. Verify Factory PostgreSQL Branch

**File**: `daemon/repositories/factory.py`

The existing `create_engine_from_config()` should already have a PostgreSQL branch. Verify it works:

```python
# Verify this branch exists and is correct
if config.database == "postgres":
    engine = create_engine(
        config.postgres.url,
        pool_size=config.postgres.pool_size,
        max_overflow=config.postgres.max_overflow,
        pool_timeout=config.postgres.pool_timeout,
        echo=False,
    )
```

**Action**: Read the current `factory.py`, verify the PG branch, update connection string format if needed (`postgresql+psycopg://...`).

### 3. Abstract Checkpointer

**File**: `daemon/persistence.py`

**Before**:
```python
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

def get_checkpointer() -> AsyncSqliteSaver:
    """Get LangGraph checkpointer (SQLite only)."""
    db_path = config.persistence.checkpoint_database
    return AsyncSqliteSaver.from_conn_string(f"sqlite:///{db_path}")
```

**After**:
```python
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.base import BaseCheckpointSaver

from daemon.config import PersistenceConfig

def get_checkpointer(config: PersistenceConfig) -> BaseCheckpointSaver:
    """Get LangGraph checkpointer based on database type.
    
    Returns AsyncSqliteSaver for SQLite backend, AsyncPostgresSaver
    for PostgreSQL backend. Both implement the BaseCheckpointSaver
    interface.
    
    Args:
        config: Persistence configuration
    
    Returns:
        Configured checkpointer instance
    
    Raises:
        ValueError: If postgres config is missing
    """
    if config.is_postgres:
        if not config.postgres:
            raise ValueError("Postgres config required for PostgreSQL checkpointer")
        checkpointer = AsyncPostgresSaver.from_conn_string(
            config.postgres.url,
        )
    else:
        db_path = config.sqlite.checkpoints_db
        checkpointer = AsyncSqliteSaver.from_conn_string(
            f"sqlite:///{db_path}",
        )
    
    return checkpointer
```

### 4. Guard SQLite-Specific Code

**File**: `daemon/persistence.py`

The existing code likely has SQLite-specific operations like `PRAGMA busy_timeout`. Guard these:

```python
# Before
async def configure_connection(db_path: str):
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA busy_timeout = 5000")

# After
async def configure_connection(config: PersistenceConfig):
    if config.is_postgres:
        # PostgreSQL has its own connection pooling, no PRAGMA needed
        return
    
    db_path = config.sqlite.checkpoints_db
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA busy_timeout = 5000")
```

### 5. Update Manager Initialization

**File**: `daemon/manager.py`

**Before**:
```python
async def initialize(self):
    config = load_config()
    self._engine = create_engine_from_config(config.persistence)
    self._checkpointer = get_checkpointer()
    await self._checkpointer.setup()
```

**After**:
```python
async def initialize(self):
    config = load_config()
    persistence_config = load_persistence_config()
    self._engine = create_engine_from_config(persistence_config)
    self._checkpointer = get_checkpointer(persistence_config)
    await self._checkpointer.setup()
```

## Dependencies Between Changes

```
Update pyproject.toml
    ↓
Install new packages
    ↓
Verify factory.py PG branch
    ↓
Abstract get_checkpointer()
    ↓
Guard SQLite-specific code
    ↓
Update manager.initialize()
```

## Testing

### Unit Test: Checkpointer Abstraction

```python
# tests/unit/test_checkpointer_abstraction.py
import pytest
from daemon.persistence import get_checkpointer
from daemon.config import PersistenceConfig, SqliteConfig, PostgresConfig

def test_get_sqlite_checkpointer(tmp_path):
    """SQLite config returns AsyncSqliteSaver."""
    config = PersistenceConfig(
        database="sqlite",
        sqlite=SqliteConfig(checkpoints_db=str(tmp_path / "test.db")),
    )
    checkpointer = get_checkpointer(config)
    assert isinstance(checkpointer, AsyncSqliteSaver)

def test_get_postgres_checkpointer():
    """Postgres config returns AsyncPostgresSaver."""
    config = PersistenceConfig(
        database="postgres",
        postgres=PostgresConfig(url="postgresql://localhost/test"),
    )
    checkpointer = get_checkpointer(config)
    assert isinstance(checkpointer, AsyncPostgresSaver)

def test_postgres_checkpointer_requires_config():
    """Postgres checkpointer raises if config missing."""
    config = PersistenceConfig(database="sqlite")
    # This should not raise (sqlite path)
    checkpointer = get_checkpointer(config)
    
    # Force postgres without config
    config_postgres = PersistenceConfig(
        database="postgres",
        postgres=None,
    )
    with pytest.raises(ValueError, match="Postgres config required"):
        get_checkpointer(config_postgres)
```

### Integration Test: Engine Creation

```python
# tests/integration/test_engine_creation.py
import pytest
from daemon.repositories.factory import create_engine_from_config
from daemon.config import PersistenceConfig, PostgresConfig, SqliteConfig

def test_create_sqlite_engine(tmp_path):
    config = PersistenceConfig(
        database="sqlite",
        sqlite=SqliteConfig(instances_db=str(tmp_path / "test.db")),
    )
    engine = create_engine_from_config(config)
    assert engine.dialect.name == "sqlite"

@pytest.mark.asyncio
async def test_create_postgres_engine():
    """Requires a running PostgreSQL test instance."""
    config = PersistenceConfig(
        database="postgres",
        postgres=PostgresConfig(url="postgresql://test:test@localhost:5432/test_db"),
    )
    engine = create_engine_from_config(config)
    assert engine.dialect.name == "postgresql"
    # Cleanup
    engine.dispose()
```

### Connection Pool Test

```python
def test_postgres_engine_has_pool():
    """PostgreSQL engine has connection pool configured."""
    config = PersistenceConfig(
        database="postgres",
        postgres=PostgresConfig(
            url="postgresql://localhost/test",
            pool_size=5,
            max_overflow=10,
        ),
    )
    engine = create_engine_from_config(config)
    assert engine.pool.size() == 5
    assert engine.pool._max_overflow == 10
```

## Acceptance Criteria

- [ ] `psycopg[binary]`, `asyncpg`, `langgraph-checkpoint-postgres` added to `pyproject.toml`
- [ ] All packages installed successfully
- [ ] `get_checkpointer()` supports both SQLite and PostgreSQL
- [ ] `create_engine_from_config()` verified for PostgreSQL
- [ ] SQLite-specific code (`PRAGMA`) guarded with backend check
- [ ] `manager.initialize()` uses new checkpointer abstraction
- [ ] Unit tests cover both backends
- [ ] Integration test verifies PostgreSQL engine creation
- [ ] Connection pool configured correctly
- [ ] No breaking changes to existing SQLite deployments

## Rollback Plan

If issues arise:
1. Revert `pyproject.toml` changes
2. Revert `persistence.py` changes
3. Revert `manager.py` changes
4. Revert `factory.py` changes (if modified)
5. `uv remove psycopg asyncpg langgraph-checkpoint-postgres`
6. Existing SQLite checkpointer works as before

No data migration needed—driver-only changes.

## Estimated Diff Size

- 1 file modified: `pyproject.toml` (+3 lines)
- 1 file modified: `daemon/persistence.py` (+30 lines, -10 lines)
- 1 file modified: `daemon/manager.py` (+5 lines, -3 lines)
- 1 file possibly modified: `daemon/repositories/factory.py` (+10 lines if PG branch needs updates)

**Total**: 2-4 files, ~50 lines changed

## Next Phase

[Phase 3: Schema Compatibility](./04-phase-3-schema-compatibility.md)
