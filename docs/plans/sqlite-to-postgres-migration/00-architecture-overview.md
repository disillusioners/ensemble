# Architecture Overview

## Current State

### Database Architecture

The daemon uses **two SQLite databases** with distinct access patterns:

| Database | ORM/Driver | Tables | Access Pattern |
|----------|------------|--------|----------------|
| `instances.db` | SQLModel + SQLAlchemy | 22 (8 model files) | Repository pattern |
| `checkpoints.db` | aiosqlite + `AsyncSqliteSaver` | 2 (LangGraph-managed) | Direct saver usage |

**Key observation**: The repository layer (8 files, 22 tables) is already database-agnostic—it takes an `Engine` parameter, not a connection string. This is the architectural win that makes this migration feasible.

### Configuration

Currently uses `config.yaml` + `.env` files. No `ensemble.json` exists.

```yaml
# config.yaml
llm:
  model: gpt-4o
  temperature: 0.7

daemon:
  host: 0.0.0.0
  port: 8079

persistence:
  database: data/ensemble.db
  checkpoint_database: data/checkpoints.db
```

### Direct Engine Access (P0 Risk)

Five services and one tool bypass the repository layer by accessing `manager._engine` directly for cross-table transactions:

| File | Line | Pattern |
|------|------|---------|
| `daemon/services/instance_messaging.py` | 594, 1187 | `Session(self._manager._engine)` |
| `daemon/services/child_reports.py` | 591 | `Session(self._manager._engine)` |
| `daemon/services/instance_lifecycle.py` | 339 | `Session(self._manager._engine)` |
| `daemon/services/error_reporting.py` | 159 | `Session(self._manager._engine)` |
| `daemon/tools/instance.py` | 484 | `Session(manager._engine)` |

These sites will work unchanged once `manager.engine` is exposed as a public property pointing at the correct database.

## Target State

### Configuration

New `ensemble.json` file auto-generated on first start if `DATABASE_URL` env var is present:

```json
{
  "database": "postgres",
  "postgres": {
    "url": "postgresql://user:pass@localhost:5432/ensemble",
    "pool_size": 5,
    "max_overflow": 10
  },
  "sqlite": {
    "instances_db": "data/ensemble.db",
    "checkpoints_db": "data/checkpoints.db"
  }
}
```

### Database Abstraction

A single `DatabaseProvider` abstraction (implicit via `manager.engine` + `get_checkpointer()`) returns the correct engine/saver based on config:

```python
# manager.py
class InstanceManager:
    @property
    def engine(self) -> Engine:
        """Public engine accessor. Returns SQLite or PostgreSQL engine."""
        return self._engine

# persistence.py
def get_checkpointer(config: PersistenceConfig) -> BaseCheckpointSaver:
    """Returns AsyncSqliteSaver or AsyncPostgresSaver."""
    if config.database == "postgres":
        return AsyncPostgresSaver.from_conn_string(config.postgres.url)
    return AsyncSqliteSaver.from_conn_string(config.sqlite.checkpoint_db)
```

### Migration Flow

```
┌─────────────────────────────────────────────────────────────┐
│ User: Settings → Database → Click "Migrate to PostgreSQL" │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ POST /api/migration/start                                  │
│   → MigrationWorker.run() (background task)                 │
│   → Returns 202 Accepted with migration_id                  │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ MigrationWorker (async):                                    │
│   1. Pause writes (write queue)                            │
│   2. create_all() PostgreSQL schema                         │
│   3. For each table:                                       │
│      a. Read all rows from SQLite                          │
│      b. Batch insert into PostgreSQL (500/batch)           │
│      c. Validate row count                                 │
│      d. Yield progress event via SSE                        │
│   4. For each checkpoint:                                  │
│      a. Read row from SQLite                               │
│      b. Insert into PostgreSQL                             │
│      c. Yield progress event via SSE                        │
│   5. Update ensemble.json → "database": "postgres"         │
│   6. Resume writes                                         │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ SSE Stream: /api/migration/events                           │
│   → Real-time progress: { table, phase, count, status }     │
└─────────────────────────────────────────────────────────────┘
```

### Rollback Strategy

The migration is **additive**—SQLite data is never modified. Rollback is a config change:

```bash
# 1. Stop daemon
# 2. Edit ensemble.json
{
  "database": "sqlite"
}
# 3. Restart daemon
```

PostgreSQL database stays intact (with partial or full data) for retry. No data loss possible because the source (SQLite) is untouched.

## Architectural Patterns

### 1. Engine Injection, Not Direct Access

Replace all 6 `manager._engine` with `manager.engine` (public property). One-time mechanical change.

### 2. SSE for Long Operations

Reuse the existing `asyncio.Queue` + `EventSourceResponse` pattern from `JobSseService`:

```python
# Pattern (already exists in daemon/routers/jobs.py)
@router.get("/api/migration/events")
async def migration_events(request: Request):
    queue = migration_worker.subscribe()
    return EventSourceResponse(queue_iterator(queue))
```

### 3. Repository Pattern Already Does the Work

95% of the codebase is already DB-agnostic. The repository layer (8 files) is the friend.

### 4. Checkpoint Dual-Write Strategy

During migration, writes queue in memory, replay to PostgreSQL after migration completes. SQLite stays as fallback.

```python
# MigrationWorker
async def run(self):
    # Pause writes
    await self._pause_writes()
    
    # Migrate data
    await self._migrate_instances_db()
    await self._migrate_checkpoints_db()
    
    # Update config
    await self._update_ensemble_json()
    
    # Resume writes
    await self._resume_writes()
```

## Dependencies

### New Python Packages

```toml
# pyproject.toml
[project]
dependencies = [
    # ... existing ...
    "psycopg[binary]>=3.2.0",                    # SQLAlchemy PostgreSQL driver
    "asyncpg>=0.30.0",                            # LangGraph checkpointer (bundled)
    "langgraph-checkpoint-postgres>=2.0.0",      # PostgreSQL checkpointer
]
```

### Frontend

No new packages required. Reuses existing Angular Material components and SSE service patterns.

## Success Criteria

- [ ] User can start with SQLite, migrate to PostgreSQL via UI click
- [ ] All 22 tables migrated with row count validation
- [ ] All LangGraph checkpoints migrated and readable
- [ ] Rollback to SQLite works via config edit
- [ ] Frontend shows real-time progress via SSE
- [ ] No data loss in any failure scenario
- [ ] Existing tests pass (no regressions)
- [ ] New integration tests cover migration flow

## Open Questions

None—all architectural decisions resolved during planning phase.

## References

- [LangGraph PostgreSQL Checkpointer Docs](https://langchain-ai.github.io/langgraph/concepts/persistence/#postgres)
- [SQLAlchemy 2.0 PostgreSQL Dialect](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html)
- [psycopg 3 Documentation](https://www.psycopg.org/psycopg3/docs/)
