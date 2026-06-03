# Phase 1: Engine Abstraction, SQLite Coupling Cleanup & Config System

## Objective

Eliminate ALL SQLite-specific coupling points — 13 `manager._engine` accesses across 7 files, `sqlite_master`/`PRAGMA` usage in `factory.py` and `runner.py`, `sqlite_insert` dialect in `project/repository.py` — and create the `ensemble.json` config system with automatic PostgreSQL ENV detection.

## Coupling

- **Depends on**: None
- **Coupling type**: — (root phase)
- **Shared files with other phases**: `daemon/manager.py`, `daemon/config.py`, `daemon/repositories/factory.py`, `daemon/migrations/runner.py`
- **Shared APIs/interfaces**: `manager.engine` property, `EnsembleConfig` class, `is_sqlite_engine()` helper, dialect-aware upsert helper
- **Why this coupling**: Phase 2 needs the public engine property, clean factory.py, and config system to create PostgreSQL engines

## Context

### `manager._engine` Access Sites (13 total, 7 files)

| # | File | Line | Access |
|---|------|------|--------|
| 1 | `daemon/api.py` | 142 | `create_job_repository(engine=manager._engine, ...)` |
| 2 | `daemon/api.py` | 145 | `LockRepository(engine=manager._engine)` |
| 3 | `daemon/api.py` | 149 | `JobQueueRepository(engine=manager._engine)` |
| 4 | `daemon/api.py` | 154 | `JobWatcher.metadata.create_all(manager._engine)` |
| 5 | `daemon/api.py` | 155 | `JobWatcherRepository(engine=manager._engine)` |
| 6 | `daemon/api.py` | 225 | `SQLModelInstanceRepository(engine=manager._engine)` |
| 7 | `daemon/api.py` | 241 | `DeadLetterRepository(engine=manager._engine)` |
| 8 | `daemon/services/instance_messaging.py` | 594 | `Session(self._manager._engine)` |
| 9 | `daemon/services/instance_messaging.py` | 1187 | `Session(self._manager._engine)` |
| 10 | `daemon/services/child_reports.py` | 591 | `Session(self._manager._engine)` |
| 11 | `daemon/services/instance_lifecycle.py` | 339 | `Session(self._manager._engine)` |
| 12 | `daemon/services/error_reporting.py` | 159 | `Session(self._manager._engine)` |
| 13 | `daemon/tools/instance.py` | 484 | `Session(manager._engine)` |

> **Note**: There are also ~6 test files that mock `manager._engine`. These will need updating to mock `manager.engine` instead.

### SQLite-Specific Code in Core Files

**`daemon/repositories/factory.py`** — 7 SQLite-specific sites:

| Line | Code | Location | Guarded? |
|------|------|----------|----------|
| 120 | `PRAGMA journal_mode=WAL` | `set_sqlite_pragma` listener | ✅ Yes — inside `if is_sqlite:` branch (line 108) |
| 121 | `PRAGMA busy_timeout=30000` | `set_sqlite_pragma` listener | ✅ Yes — inside `if is_sqlite:` branch |
| 122 | `PRAGMA synchronous=NORMAL` | `set_sqlite_pragma` listener | ✅ Yes — inside `if is_sqlite:` branch |
| 123 | `PRAGMA foreign_keys=ON` | `set_sqlite_pragma` listener | ✅ Yes — inside `if is_sqlite:` branch |
| 148 | `SELECT sql FROM sqlite_master` | `_add_agent_id_column()` | ❌ No guard — runs on any engine |
| 205 | `SELECT sql FROM sqlite_master` | `run_migrations()` | ❌ No guard — runs on any engine |
| 218 | `SELECT sql FROM sqlite_master` | `run_migrations()` | ❌ No guard — runs on any engine |

**Key finding**: Lines 120-123 (the 4 PRAGMA calls) are already safely guarded inside the `if is_sqlite:` branch of `create_engine_from_config()` (line 108). They will NOT execute when the engine is PostgreSQL — no fix needed for these.

**Fix needed only for**: Lines 148, 205, 218 — the 3 `sqlite_master` queries in `_add_agent_id_column()` and `run_migrations()` which run outside the `is_sqlite` guard.

**`daemon/migrations/runner.py`** — 3 SQLite-specific sites:
- Line 243: `SELECT name FROM sqlite_master WHERE type='table'` (in `_table_exists`)
- Line 252: `PRAGMA table_info({table_name})` (in `_column_exists`)
- Plus `_is_rename_migration_needed` also uses `_table_exists` → `sqlite_master`

**`daemon/repositories/project/repository.py`** — 1 site:
- Line 10: `from sqlalchemy.dialects.sqlite import insert as sqlite_insert`
- Line 559: `stmt = sqlite_insert(ProjectMetadataRecord).values(...)` — used for atomic upsert

### Config Loading Order (api.py lifespan)

```
Line 125: config = load_config()          ← YAML + ENV vars
Line 133: manager = InstanceManager(config)
Line 134: await manager.initialize()
Line 142-241: Access manager._engine (7 times)
```

**Decision**: `ensemble.json` must load in the lifespan BEFORE `load_config()` / `InstanceManager`. This is the chicken-and-egg resolution.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Expose public `engine` property | Add `@property` on `InstanceManager` returning `self._engine`. Replace all 13 `manager._engine` / `self._manager._engine` accesses across 7 files with `manager.engine`. Update 6 test files that mock `_engine`. | `daemon/manager.py`, `daemon/api.py`, `daemon/services/instance_messaging.py`, `daemon/services/child_reports.py`, `daemon/services/instance_lifecycle.py`, `daemon/services/error_reporting.py`, `daemon/tools/instance.py`, plus ~6 test files |
| 2 | Guard `factory.py` `run_migrations()` and `_add_agent_id_column()` | Both functions use `sqlite_master` queries (lines 148, 205, 218). These are NOT inside the `if is_sqlite:` guard. Fix: wrap function bodies in an `is_sqlite` check (e.g., `if "sqlite" not in engine.url.drivername: return`). Note: The 4 PRAGMA calls (lines 120-123) are already guarded inside `create_engine_from_config()`'s `if is_sqlite:` branch — no fix needed there. | `daemon/repositories/factory.py` |
| 3 | Guard `MigrationRunner` for PostgreSQL | In `api.py` lifespan or `manager.py`, skip `MigrationRunner.run_pending_migrations()` when engine is PostgreSQL. For PG, call `SQLModel.metadata.create_all()` + backfill `schema_migrations` table with all migration versions (mark as already applied). | `daemon/manager.py`, `daemon/api.py` |
| 4 | Replace `sqlite_insert` with dialect-aware upsert | In `project/repository.py`, replace `sqlite_insert` with a helper that uses `sqlite_insert` for SQLite and `postgresql.insert(...).on_conflict_do_update(...)` for PostgreSQL. Create a small utility function. | `daemon/repositories/project/repository.py`, optionally a new `daemon/repositories/upsert.py` |
| 5 | Create `EnsembleConfig` class | Load/save `ensemble.json` with atomic writes. Fields: `database` (sqlite/postgres), `postgres` (host, port, db, user, password), `sqlite` (instances_db, checkpoints_db). | `daemon/ensemble_config.py` (NEW) |
| 6 | Load `ensemble.json` before `load_config()` in lifespan | Add `ensemble_config = EnsembleConfig.load_or_create(data_dir)` at the START of the lifespan (before line 125). Pass `ensemble_config` to `load_config()` and `InstanceManager` so they know which database to use. Precedence: ENV vars → ensemble.json → defaults. | `daemon/api.py` |
| 7 | Add Postgres ENV auto-detection | In `EnsembleConfig.load_or_create()`: if `POSTGRES_HOST` AND `POSTGRES_DB` are both set AND `ensemble.json` doesn't exist → create `ensemble.json` with `"database": "postgres"`. | `daemon/ensemble_config.py` |
| 8 | Add health endpoint fields | Extend `/api/health` to report: `current_database` (sqlite/postgres), `postgres_env_available` (bool). Frontend uses this for conditional menu visibility. | Health endpoint file or `daemon/api.py` |

## Key Files

### Modified
- `daemon/manager.py` — Add `engine` property, integrate `EnsembleConfig`, guard `MigrationRunner`
- `daemon/api.py` — 7x `_engine` → `.engine`, load `ensemble.json` before `load_config()`
- `daemon/repositories/factory.py` — Guard `run_migrations()` + `_add_agent_id_column()` for PostgreSQL (3 `sqlite_master` sites at lines 148, 205, 218; 4 PRAGMAs at lines 120-123 already safe)
- `daemon/repositories/project/repository.py` — Replace `sqlite_insert` with dialect-aware helper
- `daemon/services/instance_messaging.py` — 2x `_engine` → `.engine`
- `daemon/services/child_reports.py` — 1x `_engine` → `.engine`
- `daemon/services/instance_lifecycle.py` — 1x `_engine` → `.engine`
- `daemon/services/error_reporting.py` — 1x `_engine` → `.engine`
- `daemon/tools/instance.py` — 1x `_engine` → `.engine`
- ~6 test files — Update mocks

### New
- `daemon/ensemble_config.py` — `EnsembleConfig` with load/save/auto-detect
- `daemon/repositories/upsert.py` (optional) — Dialect-aware upsert helper

## Constraints

- `ensemble.json` must be created atomically (write to temp file, then `os.replace()`)
- If `ensemble.json` doesn't exist and no Postgres ENV → default to SQLite (no file created)
- `manager.engine` property should be read-only (no setter)
- Must not break existing dev/prod startup sequences
- `run_migrations()` and `MigrationRunner` must be no-ops on PostgreSQL (not crash)
- Dialect-aware upsert must support both SQLite and PostgreSQL with same API
- `ensemble.json` loads BEFORE `config.yaml` / `InstanceManager` in lifespan

## `ensemble.json` Schema

```json
{
  "database": "sqlite",
  "postgres": {
    "host": "localhost",
    "port": 5432,
    "db": "ensemble",
    "user": "ensemble",
    "password": ""
  },
  "sqlite": {
    "instances_db": "./data/instances.db",
    "checkpoints_db": "./data/checkpoints.db"
  }
}
```

Minimal first-start auto-created version:
```json
{
  "database": "postgres"
}
```

## ENV Variables for PostgreSQL

```bash
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=ensemble
POSTGRES_USER=ensemble
POSTGRES_PASSWORD=secret
```

Detection rule: If `POSTGRES_HOST` AND `POSTGRES_DB` are both set → PostgreSQL available.

## Lifespan Loading Order (After This Phase)

```
1. ensemble_config = EnsembleConfig.load_or_create(data_dir)  ← NEW: first
2. config = load_config()                                       ← May use ensemble_config
3. manager = InstanceManager(config, ensemble_config)           ← Uses ensemble_config for DB selection
4. manager.initialize()                                         ← Creates engine based on config
5. ... services use manager.engine ...                           ← Public property
```

## Deliverables

- [ ] `daemon/ensemble_config.py` with atomic load/save/auto-detect
- [ ] `manager.engine` public read-only property
- [ ] All 13 `_engine` accesses across 7 files updated to `.engine`
- [ ] ~6 test files updated to mock `engine` instead of `_engine`
- [ ] `factory.py` `run_migrations()` guarded with `is_sqlite` check
- [ ] `MigrationRunner` skipped for PostgreSQL engines
- [ ] `sqlite_insert` replaced with dialect-aware upsert helper
- [ ] `ensemble.json` auto-created on first start when Postgres ENV detected
- [ ] `ensemble.json` loads BEFORE `config.yaml` in lifespan
- [ ] `/api/health` reports database type and Postgres availability
- [ ] Existing SQLite-only startup unchanged (backward compatible)
