# Architecture Decisions: SQLite → PostgreSQL Migration

## Decision 1: Config File Format

**Decision**: Use `ensemble.json` (new file), not modify existing `config.yaml`

**Rationale**: 
- `config.yaml` uses `${VAR:-default}` env substitution and is loaded via a custom YAML loader
- `ensemble.json` is simpler (standard JSON), machine-writable, and isolated from the LLM/daemon config
- Auto-creation is cleaner with JSON than YAML (no indentation concerns)
- Config precedence: `ensemble.json` > ENV vars > defaults

**Alternatives considered**:
- Add `database` section to `config.yaml` — would work but couples DB selection to LLM config
- Use only ENV vars — no persistence of user's choice across restarts

## Decision 2: PostgreSQL Dependencies as Optional

**Decision**: PostgreSQL drivers in `[project.optional-dependencies]` group named `postgres`

**Rationale**:
- SQLite-only users should not need to install psycopg/asyncpg
- Keeps the base install lightweight
- Clear error message when Postgres selected but drivers not installed

## Decision 3: Migration is One-Shot, In-Memory State

**Decision**: No persistent migration state table. Migration state lives in memory only.

**Rationale**:
- Migration is a one-time operation, not a recurring background job
- Simpler implementation (no migration state model, no table)
- If migration fails, retry is idempotent (ON CONFLICT DO NOTHING — see Decision 6)
- State machine: `idle → running → completed/failed/cancelled` — daemon restart resets to idle

## Decision 4: Write Pausing During Migration

**Decision**: `threading.Event` + atomic counter (`threading.Lock`-protected); two-layer enforcement (async gate + sync guard).

**Why `threading` primitives, not `asyncio`**:
- The codebase runs synchronous SQLAlchemy writes inside `asyncio.to_thread()` worker threads (~70+ call sites, `ThreadPoolExecutor(max_workers=4)`)
- It also runs sync `Session()` directly inside `async def` functions (6 sites in services/tools)
- `asyncio.Lock` and `asyncio.Event` raise `RuntimeError` when acquired/waited from non-event-loop threads
- `threading.Event` and `threading.Lock` work identically from both the event loop thread and worker threads

**Drain mechanism** (`WritePauseGuard`):
- `_active_writes` counter protected by `threading.Lock`
- `_drain_event` is a `threading.Event`, set when counter reaches 0, cleared when writes are active
- `pause_writes()`: sets `_write_paused = True`, calls `_drain_event.wait()` (blocks until counter = 0)
- `resume_writes()`: clears `_write_paused`
- `write_enter()`: increments counter, clears event (thread-safe)
- `write_exit()`: decrements counter, sets event if counter = 0 (thread-safe)

**Enforcement** — two layers:
1. **Async gate** (Layer 1): Check `is_write_paused` in async router/service code BEFORE `asyncio.to_thread()`. Prevents dispatching new writes to thread pool. ~70 sites.
2. **Sync guard** (Layer 2): `WriteGuardSession` wraps `Session` for the 6 direct `Session()` sites in services/tools. Calls `write_enter()`/`write_exit()` via `__enter__`/`__exit__`.

**Unguarded paths** (safe — startup only):
- `engine.connect()` + `conn.execute(text(...))` in `migrations/runner.py` (3 sites) and `factory.py` (1 site)
- These run only during startup, before the migration worker exists. Skipped for PostgreSQL (Phase 1). No guard needed.

**Why this over other options**:
- `asyncio.Lock`/`asyncio.Event`: crashes from `to_thread()` worker threads — **FATAL**
- `threading.Lock` for drain: would block the event loop from async context without `run_in_executor`
- Acquire gate above `to_thread()` only: misses 6 direct `Session()` sites in async functions
- Per-method decoration: ~30+ methods across 12+ files, easy to miss one

**Implementation**:
- `pause_writes()` is called infrequently (only during migration), and the migration worker is the only caller
- Brief write unavailability is acceptable for a maintenance operation the user explicitly triggers

## Decision 5: Batch Size 500 Rows

**Decision**: Insert data in batches of 500 rows per commit

**Rationale**:
- Balances memory usage (500 rows in memory per batch) vs transaction overhead
- PostgreSQL handles 500-row inserts efficiently
- Provides meaningful progress updates (every 500 rows = 1 progress event)

## Decision 6: Idempotent Migration via ON CONFLICT DO NOTHING

**Decision**: Use `INSERT ... ON CONFLICT DO NOTHING` for all table migrations.

**Rationale**:
- Makes migration safe to retry without truncating or tracking progress
- First run: inserts all rows
- Retry after failure: skips already-inserted rows, continues from where it stopped
- No persistent progress tracking needed
- No self-contradiction between "track in memory" and "restart from scratch"

**This replaces the contradictory approach** of "completed tables skipped on retry" + "no persistent state." Idempotent inserts make both statements true simultaneously.

## Decision 7: Checkpoint Migration via Export/Import

**Decision**: Export checkpoint rows from SQLite, insert into PostgreSQL.

**Rationale**:
- LangGraph's `AsyncSqliteSaver` and `AsyncPostgresSaver` have different internal connection management
- Direct SQL COPY not possible due to potential serialization differences
- Export to Python dicts via `CheckpointerAdapter`, then insert via PG adapter
- Simpler than trying to replay conversations through LangGraph

**Risk**: Binary data format differences between SQLite and PostgreSQL checkpoints. **Mitigated by**: Phase 2 Task 10 (serialization compatibility investigation) + Phase 5 round-trip validation.

## Decision 8: Lazy Imports for PostgreSQL Modules

**Decision**: Import psycopg, asyncpg, and langgraph-checkpoint-postgres inside functions, not at module level.

**Rationale**:
- Base install doesn't include PostgreSQL drivers
- Module-level imports would crash SQLite-only installs with ImportError
- Lazy imports provide clear error messages when Postgres selected but drivers missing

## Decision 9: Post-Migration Switch-Over Requires Restart

**Decision**: After migration completes, `ensemble.json` is updated to `"database": "postgres"`, but the daemon continues using SQLite for the current session. User must restart the daemon to switch.

**Rationale**:
- Safest approach — no complex hot-swap of database connections mid-session
- Engine, checkpointer, and all services are initialized once at startup
- Hot-swapping would require: re-creating engine, re-initializing all repos, re-connecting checkpointer, migrating in-flight operations — extremely risky
- User explicitly triggered migration and expects a maintenance window
- Frontend shows clear "Restart required" prompt

**Alternative considered (rejected)**: Hot-swap engine at runtime — too many moving parts, too much risk.

## Decision 10: CheckpointerAdapter for Database-Agnostic Maintenance

**Decision**: Create `CheckpointerAdapter` protocol with SQLite and PostgreSQL implementations. `maintenance.py` uses adapter instead of raw `.conn`/`.lock` access.

**Rationale**:
- `AsyncSqliteSaver` exposes `.conn` (aiosqlite connection) and `.lock` (asyncio.Lock) — SQLite-specific internals
- `AsyncPostgresSaver` has completely different internals — no `.conn` or `.lock`
- 10+ accesses in `maintenance.py` would crash on PostgreSQL without abstraction
- Adapter pattern encapsulates the difference and simplifies `maintenance.py` code

## Decision 11: Cancel Support with Asyncio Cooperative Cancellation

**Decision**: Add `CANCELLED` state + `POST /api/migration/cancel` endpoint. Worker checks `_cancel_requested` flag between batches (cooperative, not forced).

**Rationale**:
- Long-running migration may get stuck or user may change mind
- Cooperative cancellation is safe — no mid-transaction abort
- Worker checks flag after each batch (every 500 rows) — responsive enough
- On cancel: resume writes, report cancelled state via SSE, clean up

## Decision 12: ensemble.json Loads Before config.yaml in Lifespan

**Decision**: In the FastAPI lifespan, `EnsembleConfig.load_or_create()` runs BEFORE `load_config()` and `InstanceManager` initialization.

**Rationale (chicken-and-egg resolution)**:
- `InstanceManager.__init__` needs to know which database to create
- `load_config()` loads `config.yaml` which has `PersistenceConfig.db_path` — this path depends on which DB is selected
- Loading `ensemble.json` first resolves: which DB → then `load_config()` can use correct defaults
- Precedence: ENV vars → `ensemble.json` → `config.yaml` defaults

## Decision 13: Skip SQL Migration Files for PostgreSQL

**Decision**: When engine is PostgreSQL, skip `MigrationRunner.run_pending_migrations()` entirely. Use `SQLModel.metadata.create_all()` for schema + backfill `schema_migrations` with all 31 versions marked as applied.

**Rationale**:
- 5 of 31 SQL files contain SQLite-specific syntax (`INSERT OR IGNORE`, `datetime('now')`, `strftime`, `BOOLEAN DEFAULT 0`, `sqlite_master` queries)
- Rather than maintaining two sets of migration files, SQLModel models are the source of truth for PostgreSQL
- SQLite migrations are only needed for in-place schema evolution of existing SQLite databases
- PostgreSQL gets fresh schema from models (already up-to-date)
- Backfilling `schema_migrations` prevents any future confusion about applied migrations

## Decision 14: Dialect-Aware Upsert Helper

**Decision**: Replace `sqlite_insert` in `project/repository.py` with a helper that detects dialect and uses appropriate upsert syntax.

**Implementation**:
```python
def dialect_aware_upsert(model_class, index_elements, update_dict):
    """Returns an insert statement with dialect-appropriate conflict handling."""
    from sqlalchemy import insert
    # SQLAlchemy 2.0+ has generic insert().on_conflict_do_update()
    # For SQLite: uses sqlite dialect
    # For PostgreSQL: uses postgresql dialect
    stmt = insert(model_class).values(**values)
    return stmt.on_conflict_do_update(
        index_elements=index_elements,
        set_=update_dict
    )
```

**Note**: SQLAlchemy's generic `insert().on_conflict_do_update()` works on both SQLite and PostgreSQL (since SQLAlchemy 2.0). This may simply be a matter of replacing `from sqlalchemy.dialects.sqlite import insert` with `from sqlalchemy import insert`.

**Rationale**:
- `sqlite_insert` is SQLite-specific import — crashes when psycopg is the driver
- SQLAlchemy's generic `insert()` with `on_conflict_do_update()` handles both dialects
- Minimal code change — same behavior on both databases

## Decision 15: ORM-Layer Data Migration (Not Raw SQL)

**Decision**: Read rows as SQLModel objects from SQLite, write as SQLModel objects to PostgreSQL. No raw SQL `INSERT INTO pg SELECT * FROM sqlite`.

**Rationale**:
- SQLite `BOOLEAN DEFAULT 0` stores `0`/`1` — PostgreSQL expects `TRUE`/`FALSE`. SQLModel reads SQLite booleans as Python `bool`, psycopg writes Python `bool` as PG `TRUE`/`FALSE`.
- SQLite `JSON` stores as text strings — PostgreSQL `JSONB` needs proper JSON parsing. SQLModel reads JSON columns as Python `dict`, psycopg serializes correctly.
- SQLite `TEXT` timestamps — SQLModel reads as Python `datetime`, psycopg writes as PG `TIMESTAMP`.
- Direct SQL transfer would require manual type coercion for every column. ORM handles it automatically.

## Decision 16: Explicit Per-Table ON CONFLICT Targets

**Decision**: Build a conflict target map by introspecting primary keys from `SQLModel.metadata` at startup. Use `ON CONFLICT (pk_col1, pk_col2) DO NOTHING` with explicit column names.

**Rationale**:
- Implicit `ON CONFLICT DO NOTHING` (no target) behavior varies between SQLite and PostgreSQL
- PostgreSQL requires explicit conflict targets for tables with multiple unique constraints
- Primary key introspection from `SQLModel.metadata.sorted_tables` is deterministic and always correct
- Example: `job_queue_items` has composite PK `(job_id)` but also unique constraints — must target the PK specifically

## Decision 17: Table Order from SQLModel.metadata.sorted_tables

**Decision**: Use `SQLModel.metadata.sorted_tables` to generate migration order at runtime, not a hardcoded list.

**Rationale**:
- PostgreSQL validates foreign keys BEFORE conflict detection
- Out-of-order inserts produce FK violations that `ON CONFLICT DO NOTHING` cannot swallow
- `sorted_tables` returns topological order (parents before children) — exactly what we need
- Adding/removing models in future automatically updates the migration order
