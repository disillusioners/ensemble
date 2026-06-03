# Phase 2: PostgreSQL Drivers, Checkpointer Adapter & Compatibility

## Objective

Add PostgreSQL driver dependencies, implement PostgreSQL engine creation with proper connection pooling, create a `CheckpointerAdapter` abstraction to decouple `maintenance.py` from AsyncSqliteSaver internals, and verify schema/checkpoint compatibility between SQLite and PostgreSQL.

## Coupling

- **Depends on**: Phase 1 (needs `EnsembleConfig`, `manager.engine` property, clean factory.py)
- **Coupling type**: tight
- **Shared files with other phases**: `daemon/persistence.py`, `daemon/repositories/factory.py`, `daemon/services/maintenance.py`
- **Shared APIs/interfaces**: `CheckpointerAdapter` protocol, `get_checkpointer()` returns adapter, `create_engine_from_config()` handles PostgreSQL
- **Why this coupling**: Phase 3 will use the PostgreSQL abstractions and checkpointer adapter built here

## Context

### `maintenance.py` Direct AsyncSqliteSaver Internals (10+ accesses)

| Line | Access | Purpose |
|------|--------|---------|
| 312 | `self._checkpointer.lock` | Thread-safe access guard |
| 313 | `self._checkpointer.conn.execute(...)` | Query checkpoints table |
| 336 | `self._checkpointer.adelete_thread(...)` | Delete orphaned thread |
| 456 | `self._checkpointer.lock` | Thread-safe access guard |
| 458 | `self._checkpointer.conn.execute(...)` | Query checkpoints |
| 672 | `self._checkpointer.lock` | Thread-safe access guard |
| 674 | `self._checkpointer.conn.execute(...)` | Query checkpoint_ids |
| 691 | `self._checkpointer.conn.execute(...)` | Delete checkpoints |
| 699 | `self._checkpointer.conn.commit()` | Commit deletion |
| 703 | `self._checkpointer.conn.execute(...)` | Delete writes |
| 711 | `self._checkpointer.conn.commit()` | Commit deletion |

**Problem**: `AsyncPostgresSaver` does NOT have `.conn` or `.lock` attributes. `maintenance.py` will crash on first PostgreSQL startup.

### SQLModel Type Compatibility Concerns

| SQLite Type | PostgreSQL Type | Concern |
|-------------|-----------------|---------|
| `JSON` (SQLModel field) | `JSONB` (recommended) | JSON works in PG, but JSONB is preferred for querying |
| `BOOLEAN DEFAULT 0` | `DEFAULT FALSE` | SQLAlchemy handles conversion, but verify |
| `TEXT` | `TEXT` / `VARCHAR` | Compatible |
| Auto-increment (`INTEGER PRIMARY KEY`) | `SERIAL` / `IDENTITY` | SQLAlchemy handles via `sa_column` |
| `datetime('now')` | `NOW()` / `CURRENT_TIMESTAMP` | Only in SQL migration files (already skipped for PG) |

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add PostgreSQL dependencies | Add `psycopg[binary]>=3.1.0` (SQLAlchemy sync driver), `asyncpg>=0.29.0` (LangGraph async driver), `langgraph-checkpoint-postgres>=2.0.0` to `pyproject.toml` as optional `[postgres]` group | `pyproject.toml` |
| 2 | Enhance PostgreSQL engine creation | Update `create_engine_from_config()` PostgreSQL branch: use `postgresql+psycopg://` connection string, add `pool_size`, `max_overflow`, `pool_pre_ping`, `pool_recycle=300` | `daemon/repositories/factory.py` |
| 3 | Create `CheckpointerAdapter` protocol | Define abstract interface with 6 methods: `list_thread_ids()`, `get_checkpoints(thread_id, checkpoint_ns)`, `delete_checkpoints(thread_id, checkpoint_ns, exclude_ids)`, `delete_writes(thread_id, checkpoint_ns, exclude_ids)`, `adelete_thread(thread_id)`, `find_excess_checkpoint_groups(max_per_thread)`. The last method covers maintenance.py Operation D's GROUP BY + HAVING query. | `daemon/checkpoint_adapter.py` (NEW) |
| 4 | Implement `SqliteCheckpointerAdapter` | Wraps `AsyncSqliteSaver`, uses its `.conn` and `.lock` internally. Preserves existing behavior exactly. | `daemon/checkpoint_adapter.py` (NEW) |
| 5 | Implement `PostgresCheckpointerAdapter` | Wraps `AsyncPostgresSaver`, uses asyncpg connection pool for direct SQL. Same interface as SQLite adapter. | `daemon/checkpoint_adapter.py` (NEW) |
| 6 | Refactor `maintenance.py` | Replace all 10+ `self._checkpointer.conn`/`.lock` accesses with `CheckpointerAdapter` method calls. Zero direct access to saver internals. | `daemon/services/maintenance.py` |
| 7 | Abstract `get_checkpointer()` | Modify `persistence.py` to return `CheckpointerAdapter` (not raw saver). Return `SqliteCheckpointerAdapter` or `PostgresCheckpointerAdapter` based on `EnsembleConfig`. | `daemon/persistence.py` |
| 8 | Wire checkpointer into InstanceManager | Update `manager.initialize()` to pass `EnsembleConfig` to `get_checkpointer()`. Store adapter (not raw saver). | `daemon/manager.py` |
| 9 | Verify SQLModel schema creation on PostgreSQL | Test that `SQLModel.metadata.create_all(engine)` works with all 22 tables on PostgreSQL. Verify JSON, Boolean, UUID, DateTime column types create correctly. | Test script |
| 10 | Investigate checkpoint serialization compatibility | Load a real checkpoint from SQLite (via AsyncSqliteSaver), inspect binary format. Compare with AsyncPostgresSaver expected format. Document any differences and required transformations. | Investigation + documentation |

## Key Files

### New Files
- `daemon/checkpoint_adapter.py` — `CheckpointerAdapter` protocol + SQLite + PostgreSQL implementations

### Modified Files
- `pyproject.toml` — Add optional `[postgres]` dependency group
- `daemon/repositories/factory.py` — PostgreSQL engine with psycopg driver
- `daemon/persistence.py` — Return `CheckpointerAdapter` based on config
- `daemon/manager.py` — Wire config-driven checkpointer selection
- `daemon/services/maintenance.py` — Replace raw `.conn`/`.lock` with adapter methods

## `CheckpointerAdapter` Design

```python
"""Abstracts checkpoint database access for SQLite and PostgreSQL.

maintenance.py (and any future code) uses this adapter instead of
directly accessing AsyncSqliteSaver.conn / .lock internals.
"""

from abc import ABC, abstractmethod
from typing import Sequence


class CheckpointerAdapter(ABC):
    """Protocol for checkpoint database operations."""

    @abstractmethod
    async def list_thread_ids(self) -> list[str]:
        """Return all distinct thread_ids in checkpoints table."""

    @abstractmethod
    async def get_checkpoint_ids(
        self, thread_id: str, checkpoint_ns: str, limit: int
    ) -> list[str]:
        """Get checkpoint_ids ordered newest-first, limited to `limit`."""

    @abstractmethod
    async def delete_checkpoints_excluding(
        self, thread_id: str, checkpoint_ns: str, keep_ids: set[str]
    ) -> int:
        """Delete checkpoints NOT in keep_ids. Returns deleted count."""

    @abstractmethod
    async def delete_writes_excluding(
        self, thread_id: str, checkpoint_ns: str, keep_ids: set[str]
    ) -> int:
        """Delete writes NOT in keep_ids. Returns deleted count."""

    @abstractmethod
    async def adelete_thread(self, thread_id: str) -> None:
        """Delete all checkpoint data for a thread."""

    @abstractmethod
    async def find_excess_checkpoint_groups(
        self, max_per_thread: int
    ) -> list[tuple[str, str, int]]:
        """Find (thread_id, checkpoint_ns, count) groups exceeding max_per_thread.

        Used by maintenance.py Operation D (_prune_per_thread_checkpoints)
        to find threads with more than the allowed number of checkpoints.

        Returns list of (thread_id, checkpoint_ns, count) tuples where
        count > max_per_thread.
        """

    @property
    @abstractmethod
    def raw_saver(self):
        """Access to the underlying saver for LangGraph operations."""


class SqliteCheckpointerAdapter(CheckpointerAdapter):
    """Wraps AsyncSqliteSaver, using its .conn and .lock internally."""

    def __init__(self, saver: AsyncSqliteSaver):
        self._saver = saver

    async def list_thread_ids(self) -> list[str]:
        async with self._saver.lock:
            cursor = await self._saver.conn.execute(
                "SELECT DISTINCT thread_id FROM checkpoints"
            )
            rows = await cursor.fetchall()
            return [row[0] for row in rows]

    # ... other methods use same pattern ...

    @property
    def raw_saver(self):
        return self._saver


class PostgresCheckpointerAdapter(CheckpointerAdapter):
    """Wraps AsyncPostgresSaver, using asyncpg pool internally."""

    def __init__(self, saver: AsyncPostgresSaver, pool: asyncpg.Pool):
        self._saver = saver
        self._pool = pool

    async def list_thread_ids(self) -> list[str]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT DISTINCT thread_id FROM checkpoints"
            )
            return [row["thread_id"] for row in rows]

    # ... other methods use same pattern with asyncpg ...
```

## `maintenance.py` Refactoring Pattern

**Before** (direct access — crashes on PostgreSQL):
```python
async with self._checkpointer.lock:
    cursor = await self._checkpointer.conn.execute(
        "SELECT DISTINCT thread_id FROM checkpoints"
    )
    rows = await cursor.fetchall()
    checkpoint_threads = [row[0] for row in rows]
```

**After** (adapter — works on both):
```python
checkpoint_threads = await self._checkpointer.list_thread_ids()
```

This simplifies `maintenance.py` significantly while making it database-agnostic.

## Dependency Additions

```toml
# pyproject.toml
[project.optional-dependencies]
postgres = [
    "psycopg[binary]>=3.1.0",
    "asyncpg>=0.29.0",
    "langgraph-checkpoint-postgres>=2.0.0",
]
```

## Constraints

- PostgreSQL dependencies must be optional — SQLite-only installs should not require psycopg/asyncpg
- Use lazy imports for PostgreSQL modules (import inside the branch, not at module level)
- `get_checkpointer()` must handle missing driver gracefully → clear error: `"Install postgres support: pip install ensemble[postgres]"`
- Connection string format: `postgresql+psycopg://user:pass@host:port/dbname` for SQLAlchemy
- AsyncPostgresSaver connection string: `postgresql://user:pass@host:port/dbname` for asyncpg
- `CheckpointerAdapter` must preserve exact same semantics as current direct access (thread-safe, transactional)
- Checkpoint serialization investigation must use real data from `data_dev/checkpoints.db`

## Deliverables

- [ ] `pyproject.toml` updated with optional `[postgres]` dependency group
- [ ] PostgreSQL engine creation works with connection pooling
- [ ] `CheckpointerAdapter` protocol defined with 6 abstract methods (including `find_excess_checkpoint_groups`)
- [ ] `SqliteCheckpointerAdapter` wraps AsyncSqliteSaver (preserves behavior)
- [ ] `PostgresCheckpointerAdapter` wraps AsyncPostgresSaver (new)
- [ ] `maintenance.py` refactored to use adapter (zero direct `.conn`/`.lock` access)
- [ ] `get_checkpointer()` returns adapter based on config
- [ ] Daemon starts successfully with PostgreSQL (engine + checkpointer + maintenance)
- [ ] Daemon still starts with SQLite when no Postgres config exists
- [ ] SQLModel `create_all()` verified on PostgreSQL (all 22 tables)
- [ ] Checkpoint serialization compatibility investigated and documented
