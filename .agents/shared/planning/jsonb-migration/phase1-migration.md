# Phase 1: JSON → JSONB Column Migration

## Objective
Replace all 17 `Column(JSON)` declarations with `Column(JSONBType)` across 9 model files so that fresh PostgreSQL databases create JSONB columns via `create_all()`. Add idempotent `ALTER TABLE ... ALTER COLUMN ... TYPE jsonb USING ...::jsonb` statements to `_ensure_postgres_columns()` so existing PostgreSQL databases convert in place on next startup. SQLite is unaffected (JSONBType resolves to JSON on SQLite).

## Coupling
- **Depends on**: None (root phase)
- **Coupling type**: — (root)
- **Shared files with other phases**: `daemon/manager.py` (Phase 2/3 test code calls `_ensure_postgres_columns()` indirectly via `EnsembleManager.__init__`)
- **Shared APIs/interfaces**: None — model column type changes are transparent to repositories (same Python dict/list API)
- **Why this coupling**: Phase 2's PG fixtures exercise the full manager init path, which calls `_ensure_postgres_columns()`. But Phase 2 can start scaffolding before Phase 1 review completes.

## Context
- **JSONBType**: Defined in `daemon/repositories/infra/types.py:35-89`. Resolves to `JSONB()` on PostgreSQL, `JSON()` on SQLite. `impl = JSON`, `cache_ok = True`. Processors are no-ops.
- **_ensure_postgres_columns()**: `daemon/manager.py:1573-1745`. Runs on every PG startup inside a single transaction (`self._engine.begin()`). List of statements executed sequentially. No try/except — failures abort startup. Uses `IF NOT EXISTS` for idempotency.
- **Existing pattern**: All statements are `ADD COLUMN IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`, `CREATE UNIQUE INDEX IF NOT EXISTS`, or `DELETE ... WHERE ctid NOT IN ...` (dedup). **No `ALTER COLUMN TYPE` exists yet** — this is the first type conversion.
- **OpenCodeSessionRecord**: Uses a separate SQLite DB (`data/opencode_skill.json` area). Changing its model columns to `JSONBType` is a no-op on SQLite. If PG is ever used for opencode sessions, it'll get JSONB automatically.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Replace `Column(JSON)` → `Column(JSONBType)` in source models | 2 columns: `config`, `mapping_metadata`. Add import `from daemon.repositories.infra.types import JSONBType`. | `daemon/repositories/source/models.py:55,104` |
| 2 | Replace in project models | 5 columns: `meta_value`, `related_directories`, `project_metadata`, `relationships`, `entry_metadata`. Add import. | `daemon/repositories/project/models.py:178,207,217,222,304` |
| 3 | Replace in job_queue models | 2 columns: `job_metadata`, `dead_letter metadata_json`. Add import. | `daemon/repositories/job_queue/models.py:183,354` |
| 4 | Replace in watcher models | 1 column: `watch_events`. Add import. | `daemon/repositories/job_queue/watcher_models.py:46` |
| 5 | Replace in instance models | 1 column: `instance_metadata` (`metadata`). Add import. | `daemon/repositories/instance/models.py:60` |
| 6 | Replace in message_queue models | 2 columns: `message_metadata`, `images`. Add import. | `daemon/repositories/message_queue/models.py:61,76` |
| 7 | Replace in mcp_server models | 2 columns: `config`, `config_schema`. Add import. | `daemon/repositories/mcp_server/models.py:23,29` |
| 8 | Replace in opencode repository | 2 columns: `latest_response`, `questions`. Add import. | `daemon/opencode/repository.py:87,91` |
| 9 | Add `ALTER COLUMN TYPE jsonb` to `_ensure_postgres_columns()` | Add idempotent conversion block with `USING col::jsonb`. See Implementation Pattern below. | `daemon/manager.py:1624-1745` |
| 10 | Write JSONB migration verification test | Test that fresh PG DB has `jsonb` type on all 24 JSON columns (query `information_schema.columns`). Runs on SQLite too (verify `JSONBType` still resolves to text/json). | `tests/migration/test_jsonb_migration.py` |

## Key Files

| File | Purpose |
|------|---------|
| `daemon/repositories/infra/types.py` | `JSONBType` definition — NO CHANGES (already correct) |
| `daemon/repositories/source/models.py` | 2 JSON columns → JSONBType |
| `daemon/repositories/project/models.py` | 5 JSON columns → JSONBType |
| `daemon/repositories/job_queue/models.py` | 2 JSON columns → JSONBType |
| `daemon/repositories/job_queue/watcher_models.py` | 1 JSON column → JSONBType |
| `daemon/repositories/instance/models.py` | 1 JSON column → JSONBType |
| `daemon/repositories/message_queue/models.py` | 2 JSON columns → JSONBType |
| `daemon/repositories/mcp_server/models.py` | 2 JSON columns → JSONBType |
| `daemon/opencode/repository.py` | 2 JSON columns → JSONBType |
| `daemon/manager.py` | Add `ALTER COLUMN TYPE jsonb` block to `_ensure_postgres_columns()` |

## Constraints
- **SQLite must still work**: `JSONBType` resolves to `JSON` on SQLite — no behavior change. Verify all existing SQLite tests pass.
- **No breaking API changes**: Repositories already use Python `dict`/`list` types. JSONB is transparent.
- **Handle existing data**: `ALTER COLUMN TYPE jsonb USING col::jsonb` will fail if a column contains malformed JSON. The conversion block must validate first or use a safe `USING` expression.
- **Idempotency**: `_ensure_postgres_columns()` runs on every startup. PostgreSQL has no `ALTER COLUMN TYPE ... IF NOT EXISTS`. The block must check current type first (query `information_schema.columns`) and only ALTER columns that are still `json`.
- **Column ordering**: All `ALTER` statements go in the same single-transaction block. If any fails, startup aborts (existing failure semantics).
- **GIN indexes**: `infra_assets.attributes` and `infra_assets.relationships` already have GIN indexes (created later in the statement list). The 17 newly-converted columns don't need GIN indexes unless future query patterns require it. Out of scope.

## Implementation Pattern: `ALTER COLUMN TYPE` block

PostgreSQL has no `ALTER COLUMN TYPE ... IF NOT EXISTS`. The conversion must be conditional. Two options:

### Option A: PL/pgSQL DO block (recommended — single statement)
```sql
DO $$
DECLARE
    r RECORD;
BEGIN
    FOR r IN
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND data_type = 'json'   -- only columns still typed as json
          AND (table_name, column_name) IN VALUES
              ('source_configs','config'),
              ('instance_mappings','mapping_metadata'),
              ('project_metadata_records','meta_value'),
              ('projects','related_directories'),
              ('projects','metadata'),
              ('projects','relationships'),
              ('project_history','entry_metadata'),
              ('job_queue_items','metadata'),
              ('dead_letter_items','metadata'),
              ('job_watchers','watch_events'),
              ('instances','metadata'),
              ('message_queue','metadata'),
              ('message_queue','images'),
              ('mcp_servers','config'),
              ('mcp_servers','config_schema'),
              ('opencode_sessions','latest_response'),
              ('opencode_sessions','questions')
    LOOP
        EXECUTE format('ALTER TABLE %I ALTER COLUMN %I TYPE jsonb USING %I::jsonb',
                       r.table_name, r.column_name, r.column_name);
    END LOOP;
END $$;
```

This is ONE statement in the `statements` list, self-contained, idempotent (only converts `json` columns), and safe to re-run.

### Option B: Python-side check before ALTER
Loop in Python, query `information_schema.columns`, build ALTER statements dynamically. More control but more code.

**Recommendation: Option A** — single self-contained statement, fits the existing "list of SQL strings" pattern, no Python logic changes.

### Data validation (optional pre-flight)
Before the DO block, consider adding a check statement:
```sql
-- Raises an error if any column contains invalid JSON, before the ALTER
-- (PostgreSQL's ::jsonb cast will fail on invalid JSON anyway, but this gives a clearer error)
```
In practice, if all data was inserted via SQLAlchemy `JSON` type, it's already valid JSON. Skip explicit validation unless dev data is known to be dirty.

## Model Change Pattern (per file)

For each model file, the change is mechanical:

**Before:**
```python
from sqlalchemy import Column
from sqlalchemy.types import JSON

class SourceConfig(SQLModel, table=True):
    config: dict = Field(sa_column=Column(JSON))
```

**After:**
```python
from sqlalchemy import Column
from daemon.repositories.infra.types import JSONBType

class SourceConfig(SQLModel, table=True):
    config: dict = Field(sa_column=Column(JSONBType))
```

Notes:
- If `JSON` import becomes unused after replacement, remove it from imports.
- The `Column("db_col_name", JSON)` form becomes `Column("db_col_name", JSONBType)` — keep the DB column name string if present.
- Preserve all other column attributes (`nullable`, `default`, `server_default`, etc.).

## Optional Cleanup (not required, document in decisions.md)

After migration, these runtime casts become no-ops (columns are already JSONB):
- `daemon/repositories/project/repository.py:286` — `cast(Project.relationships, JSONB)`
- `daemon/repositories/project/repository.py:313` — `cast(Project.related_directories, JSONB)`

These are harmless but redundant. Can be cleaned up in a follow-up. **Not in scope for this phase.**

## Deliverables
- [ ] 9 model files updated: all 17 `Column(JSON)` → `Column(JSONBType)`
- [ ] `daemon/manager.py` `_ensure_postgres_columns()` extended with JSON→JSONB DO block
- [ ] `tests/migration/test_jsonb_migration.py` — verifies column types on both PG and SQLite
- [ ] All existing tests pass (SQLite unchanged)
- [ ] Fresh PG DB creates JSONB columns (verified by test)
- [ ] Existing PG DB converts JSON→JSONB on startup (verified by test)
