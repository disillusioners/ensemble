# Phase 1: Storage Layer

## Objective

Create a new `shared_context` repository domain with a `SharedContextMetadataRecord` table and `SharedContextMetadataRepository` class supporting batch CRUD operations. Wire it into the factory and EnsembleManager. Create the SQLite migration file.

## Coupling

- **Depends on**: None (root phase)
- **Coupling type**: — (root)
- **Shared files with other phases**: 
  - `daemon/repositories/factory.py` — also modified by nothing else (Phase 2/3 import from it)
  - `daemon/repositories/__init__.py` — re-export
  - `daemon/manager.py` — repository instantiation
- **Shared APIs/interfaces**: `SharedContextMetadataRepository` class (consumed by Phase 2 tool and Phase 3 injection)
- **Why this coupling**: Phase 2 and Phase 3 both import the repository class; this phase defines the contract

## Context

- **Pattern source**: `daemon/repositories/project/` domain (especially `ProjectMetadataRecord` table and `set_metadata_record`/`delete_metadata_record`/`list_metadata_records` methods)
- **Key difference from project metadata**: No coupling to parent entity (no `project.updated_at` mutation, no `_enrich_project` call). Context metadata is standalone.
- **Dual DB**: PostgreSQL is primary. `SQLModel.metadata.create_all()` handles new tables on both DBs. SQLite migration file is for existing SQLite DBs.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create domain directory | Create `daemon/repositories/shared_context/` with `__init__.py`, `models.py`, `repository.py` | `daemon/repositories/shared_context/` |
| 2 | Define SQLModel table | `SharedContextMetadataRecord` with columns: `id` (int PK autoincrement), `context_key` (str, indexed), `meta_key` (str), `meta_value` (JSONBType, nullable), `created_at` (str ISO), `updated_at` (str ISO). UniqueConstraint on `(context_key, meta_key)`. | `daemon/repositories/shared_context/models.py` |
| 3 | Implement repository class | `SharedContextMetadataRepository(engine)` with `_get_dialect_insert()`, `get_record()`, `upsert_record()`, `delete_record()`, `list_records()`, and batch methods `batch_upsert()`, `batch_delete()`. | `daemon/repositories/shared_context/repository.py` |
| 4 | Create `__init__.py` re-exports | Export `SharedContextMetadataRecord`, `SharedContextMetadataRepository` | `daemon/repositories/shared_context/__init__.py` |
| 5 | Add factory function | `create_shared_context_repository(config, engine, create_tables)` — mirrors `create_project_repository` pattern | `daemon/repositories/factory.py` |
| 6 | Re-export from `__init__` | Add to `daemon/repositories/__init__.py` `__all__` and imports | `daemon/repositories/__init__.py` |
| 7 | Wire into EnsembleManager | Add `self._shared_context_repository = create_shared_context_repository(engine=self._engine, create_tables=False)` near line 741 | `daemon/manager.py` |
| 8 | Create SQLite migration | `YYYYMMDD_HHMMSS_create_shared_context_metadata_table.sql` with `-- UP`/`-- DOWN` sections | `daemon/migrations/versions/` |

## Key Files

### New Files

#### `daemon/repositories/shared_context/models.py`

```python
"""Shared context metadata models."""
from datetime import datetime, timezone
from typing import Any, Optional
from sqlalchemy import Column, Integer, String, UniqueConstraint, Index
from sqlmodel import Field, SQLModel
from daemon.repositories.infra.types import JSONBType


class SharedContextMetadataRecord(SQLModel, table=True):
    """Dedicated table for shared context key-value metadata, scoped by context_key."""
    __tablename__ = "shared_context_metadata"

    id: Optional[int] = Field(
        default=None, sa_column=Column(Integer, primary_key=True, autoincrement=True)
    )
    context_key: str = Field(sa_column=Column(String, nullable=False, index=True))
    meta_key: str = Field(sa_column=Column(String, nullable=False))
    meta_value: Any = Field(sa_column=Column(JSONBType, nullable=True))
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    __table_args__ = (
        UniqueConstraint("context_key", "meta_key", name="uq_shared_context_metadata_key"),
        Index("ix_shared_context_metadata_context_key", "context_key"),
    )
```

#### `daemon/repositories/shared_context/repository.py`

```python
"""Shared context metadata repository."""
import logging
from datetime import datetime, timezone
from typing import Any, Optional
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlmodel import Session
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .models import SharedContextMetadataRecord

logger = logging.getLogger(__name__)


class SharedContextMetadataRepository:
    """Repository for shared context metadata KV pairs, scoped by context_key."""

    def __init__(self, engine: Engine):
        self.engine = engine

    @staticmethod
    def _get_dialect_insert(session: Session):
        """Get dialect-appropriate insert function for upsert operations."""
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as pg_insert
            return pg_insert
        return sqlite_insert

    # ---- Single-record operations ----

    def get_record(self, context_key: str, meta_key: str) -> Optional[SharedContextMetadataRecord]:
        """Get a single metadata record by (context_key, meta_key)."""
        with Session(self.engine) as session:
            return session.exec(
                select(SharedContextMetadataRecord).where(
                    SharedContextMetadataRecord.context_key == context_key,
                    SharedContextMetadataRecord.meta_key == meta_key,
                )
            ).first()

    def upsert_record(self, context_key: str, meta_key: str, meta_value: Any) -> SharedContextMetadataRecord:
        """Insert or update a metadata record (atomic upsert)."""
        if not meta_key or not meta_key.strip():
            raise ValueError("meta_key cannot be empty")
        now = datetime.now(timezone.utc).isoformat()
        with Session(self.engine) as session:
            insert_fn = self._get_dialect_insert(session)
            stmt = insert_fn(SharedContextMetadataRecord).values(
                context_key=context_key,
                meta_key=meta_key,
                meta_value=meta_value,
                created_at=now,
                updated_at=now,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["context_key", "meta_key"],
                set_={"meta_value": meta_value, "updated_at": now},
            )
            session.execute(stmt)
            session.commit()
            return self.get_record(context_key, meta_key)

    def delete_record(self, context_key: str, meta_key: str) -> bool:
        """Delete a metadata record. Returns True if deleted, False if not found."""
        with Session(self.engine) as session:
            record = session.exec(
                select(SharedContextMetadataRecord).where(
                    SharedContextMetadataRecord.context_key == context_key,
                    SharedContextMetadataRecord.meta_key == meta_key,
                )
            ).first()
            if record:
                session.delete(record)
                session.commit()
                return True
            return False

    def list_records(self, context_key: str) -> list[SharedContextMetadataRecord]:
        """List all metadata records for a context_key."""
        with Session(self.engine) as session:
            return session.exec(
                select(SharedContextMetadataRecord).where(
                    SharedContextMetadataRecord.context_key == context_key
                )
            ).all()

    # ---- Batch operations ----

    def batch_upsert(self, context_key: str, items: dict[str, Any]) -> list[SharedContextMetadataRecord]:
        """Batch upsert multiple KV pairs. items = {key: value, ...}"""
        now = datetime.now(timezone.utc).isoformat()
        with Session(self.engine) as session:
            insert_fn = self._get_dialect_insert(session)
            for meta_key, meta_value in items.items():
                if not meta_key or not meta_key.strip():
                    raise ValueError(f"meta_key cannot be empty: {meta_key!r}")
                stmt = insert_fn(SharedContextMetadataRecord).values(
                    context_key=context_key,
                    meta_key=meta_key,
                    meta_value=meta_value,
                    created_at=now,
                    updated_at=now,
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=["context_key", "meta_key"],
                    set_={"meta_value": meta_value, "updated_at": now},
                )
                session.execute(stmt)
            session.commit()
        return self.list_records(context_key)

    def batch_delete(self, context_key: str, keys: list[str]) -> int:
        """Batch delete multiple keys. Returns count of deleted records."""
        deleted = 0
        with Session(self.engine) as session:
            for meta_key in keys:
                record = session.exec(
                    select(SharedContextMetadataRecord).where(
                        SharedContextMetadataRecord.context_key == context_key,
                        SharedContextMetadataRecord.meta_key == meta_key,
                    )
                ).first()
                if record:
                    session.delete(record)
                    deleted += 1
            session.commit()
        return deleted

    def get_all_as_dict(self, context_key: str) -> dict[str, Any]:
        """Get all KV pairs for a context_key as a simple dict {key: value}."""
        records = self.list_records(context_key)
        return {r.meta_key: r.meta_value for r in records}
```

#### `daemon/repositories/shared_context/__init__.py`

```python
"""Shared context repository module."""
from .repository import SharedContextMetadataRepository
from .models import SharedContextMetadataRecord

__all__ = [
    "SharedContextMetadataRepository",
    "SharedContextMetadataRecord",
]
```

#### Migration: `daemon/migrations/versions/YYYYMMDD_HHMMSS_create_shared_context_metadata_table.sql`

```sql
-- Migration: create shared_context_metadata table
-- Created: 2026-07-12
-- Description: Create dedicated shared_context_metadata table for context-key-scoped KV metadata

-- UP

CREATE TABLE IF NOT EXISTS shared_context_metadata (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    context_key TEXT NOT NULL,
    meta_key TEXT NOT NULL,
    meta_value JSON,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_shared_context_metadata_context_key
    ON shared_context_metadata(context_key);

CREATE UNIQUE INDEX IF NOT EXISTS uq_shared_context_metadata_key
    ON shared_context_metadata(context_key, meta_key);

-- DOWN

DROP TABLE IF EXISTS shared_context_metadata;
```

### Modified Files

#### `daemon/repositories/factory.py`

Add after `create_project_repository` (~line 365):

```python
def create_shared_context_repository(
    config: DatabaseConfig | None = None,
    engine: Engine | None = None,
    create_tables: bool = True,
) -> SharedContextMetadataRepository:
    """Create a SharedContextMetadataRepository from configuration or shared engine."""
    from .shared_context.repository import SharedContextMetadataRepository

    if engine is None:
        if config is None:
            raise ValueError("Either config or engine must be provided")
        engine = create_engine_from_config(config)

    if create_tables:
        SQLModel.metadata.create_all(engine)

    return SharedContextMetadataRepository(engine)
```

Also add `"create_shared_context_repository"` to `__all__`.

#### `daemon/repositories/__init__.py`

Add to imports and `__all__`:

```python
from .factory import create_shared_context_repository
# ... add to __all__ list
```

#### `daemon/manager.py`

**Import** (near line 33):
```python
from .repositories.factory import create_shared_context_repository
```

**Instantiation** (near line 741, after `self._project_repository`):
```python
# Shared context metadata repository for context-key-scoped KV injection
self._shared_context_repository = create_shared_context_repository(
    engine=self._engine, create_tables=False
)
```

**Accessor** (add a property or method alongside other repository accessors):
```python
def get_shared_context_repository(self) -> "SharedContextMetadataRepository":
    return self._shared_context_repository
```

## Constraints

- PostgreSQL is PRIMARY dev/test DB. Must support both SQLite and PostgreSQL.
- Use `_get_dialect_insert()` pattern for upserts (not generic `sqlalchemy.insert`).
- Do NOT mutate any parent entity's `updated_at` (unlike project metadata pattern).
- Use `meta_key`/`meta_value` naming (avoid reserved word `metadata`).
- Migration file is SQLite-only; PostgreSQL handles new tables via `create_all()`.
- `create_tables=False` when wiring into EnsembleManager (MigrationRunner handles table creation).

## Deliverables

- [ ] `daemon/repositories/shared_context/` directory with 3 files
- [ ] `SharedContextMetadataRecord` table model with UniqueConstraint
- [ ] `SharedContextMetadataRepository` with single + batch operations
- [ ] Factory function `create_shared_context_repository()` in `factory.py`
- [ ] Re-exports in `daemon/repositories/__init__.py`
- [ ] EnsembleManager instantiation + accessor
- [ ] SQLite migration file
- [ ] Table created successfully on both SQLite and PostgreSQL
