# PostgreSQL Compatibility Audit — SQLite → PostgreSQL Migration

**Date**: 2026-06-04
**Status**: 🟢 Resolved — All 4 issues fixed; 633 tests pass
**Scope**: Repository / service layer SQL patterns that may fail or misbehave on PostgreSQL

---

## Executive Summary

The codebase is in the middle of migrating from SQLite to PostgreSQL. Most of the application code uses SQLAlchemy/SQLModel with neutral constructs that work on both databases. A focused audit, triggered by a runtime `psycopg.errors.UndefinedFunction` error on `project_search`, surfaced **3 critical** and **1 latent** issue that need fixes before the migration is complete.

**Status (2026-06-04 update)**: All 4 issues below have been fixed. Dialect-branching uses the existing `session.bind.dialect.name == "postgresql"` pattern (same as `_get_dialect_insert()` in the project repository). The full test suite (633 tests across project, source, history, scheduler, and message queue suites) passes on SQLite. PostgreSQL-side verification still requires a running PG test database — see [Verification](#verification) below.

**Recommendation (original)**: Fix the 3 critical issues before enabling PostgreSQL in production. The latent issue is a pre-existing bug that should also be fixed.

---

## Background

The first reported failure:

```
21:17:45 - daemon.services.instance_messaging - ERROR - Streaming failed for message f269193e-...:
(psycopg.errors.UndefinedFunction) could not identify identify an equality operator for type json
LINE 1: ...t_type, projects.status, projects.main_directory, projects.r...
```

Root cause: `SQLAlchemy`'s `distinct()` clause requires equality comparison on all selected columns. JSON columns in PostgreSQL do not have a built-in `=` operator, so `DISTINCT` over a result set that includes JSON columns fails. This was fixed in `daemon/repositories/project/repository.py:334-350` by removing the redundant `.distinct()` (`Project.project_id` is the primary key, so rows are already unique).

The audit below checks for **other** latent PostgreSQL compatibility problems across the repository layer.

---

## Critical Issues (will fail on PostgreSQL)

### 1. `json_extract()` / `json_set()` in source repository

**File**: `daemon/repositories/source/repository.py:113-123`

**Problem**: Uses SQLite-specific JSON functions. PostgreSQL does not have these functions.

```python
update_sql = text("""
    UPDATE source_configs
    SET config = json_set(
        COALESCE(config, '{}'),
        '$._run_counter',
        COALESCE(CAST(json_extract(config, '$._run_counter') AS INTEGER), 0) + 1
    ),
    updated_at = :updated_at
    WHERE source_id = :source_id
    RETURNING CAST(json_extract(config, '$._run_counter') AS INTEGER) as counter
""")
```

**PostgreSQL equivalent**:

```sql
UPDATE source_configs
SET config = jsonb_set(
    COALESCE(config, '{}'::jsonb),
    '{_run_counter}',
    to_jsonb(
        COALESCE((config->>'_run_counter')::int, 0) + 1
    )
),
updated_at = :updated_at
WHERE source_id = :source_id
RETURNING (config->>'_run_counter')::int AS counter
```

**Note**: This query is dialect-specific and needs runtime branching, OR the column type should be migrated to `JSONB` (it is currently `JSON` per `daemon/repositories/source/models.py:53-56` which maps to `JSONB` on PostgreSQL).

**Suggested fix approach**: Use SQLAlchemy ORM-level update with `MutableDict` semantics, or branch the raw SQL on engine dialect (`session.bind.dialect.name == "postgresql"`).

**Status**: ✅ **Fixed**. Implemented the dialect-branching approach in `daemon/repositories/source/repository.py:113-148`. On PostgreSQL the method uses `jsonb_set` with `to_jsonb(COALESCE((config->>'_run_counter')::int, 0) + 1)` and returns `(config->>'_run_counter')::int AS counter`; on SQLite the original `json_set`/`json_extract` path is preserved. The check uses the same `session.bind.dialect.name == "postgresql"` pattern as `_get_dialect_insert` in the project repository.

---

### 2. `.contains()` on JSON columns in project repository

**File**: `daemon/repositories/project/repository.py:252, 268`

**Problem**: `SQLAlchemy`'s `.contains()` translates to `LIKE '%...%'`. PostgreSQL does not support `LIKE` on JSON columns. The same error class as the one that triggered this audit.

```python
# Line 252
stmt = select(Project).where(
    (Project.creator_instance_id == instance_id)
    | col(Project.relationships).contains(f'"instances"')
)

# Line 268
stmt = select(Project).where(
    (Project.main_directory == directory)
    | col(Project.related_directories).contains(f'"{directory}"')
)
```

**PostgreSQL equivalent** (using `JSONB` containment `@>`):

```python
from sqlalchemy import cast, type_coerce
from sqlalchemy.dialects.postgresql import JSONB

# Line 252 — find projects whose relationships JSON contains {"instances": [...]}
stmt = stmt.where(
    Project.relationships.cast(JSONB).contains(
        {"instances": [instance_id]}
    )
)

# Line 268 — find projects whose related_directories contains a value
stmt = stmt.where(
    Project.related_directories.cast(JSONB).contains(
        [directory]  # JSON array containment
    )
)
```

**Note**: The existing code falls back to Python-side filtering after the SQL query (lines 256-261, 272-274), so it works in practice by over-fetching. But the SQL itself still raises an error on PostgreSQL before the Python filter runs.

**Suggested fix approach**: Use `JSONB` containment (`@>`) on PostgreSQL, or do the JSON filtering in Python only (skip the SQL filter on JSON columns).

**Status**: ✅ **Fixed**. Both `get_by_instance` and `get_by_directory` now branch on `session.bind.dialect.name == "postgresql"`. On PostgreSQL the query uses `cast(Project.relationships, JSONB).contains({"instances": [instance_id]})` and `cast(Project.related_directories, JSONB).contains([directory])` (the `@>` operator). On SQLite the original `col().contains()` clause is preserved. The `cast` and `JSONB` imports are placed inside the PG branch to avoid forcing the import on SQLite-only deployments.

---

### 3. `boolean = 0` / `boolean = 1` literals in raw SQL

**Files**:
- `tests/message_queue_redesign/test_stale_recovery_v2.py:83, 127, 128`

**Problem**: PostgreSQL is strictly typed. A boolean column compared to an integer literal raises:
```
operator does not exist: boolean = integer
```

```python
# Example from tests (stale_recovery_v2.py:83)
text("""
    UPDATE task SET ...
    cancel_requested = 1
    ...
""")
```

**Status**: The production code in `daemon/repositories/task/repository.py` has already been fixed — it uses bound parameters with Python `True`/`False` (see comments at lines 444-446, 521-523, 737-741). The fix in the task repository is the reference pattern.

**Remaining occurrences**: Only in **test files**, but those tests will fail when run against a PostgreSQL test database.

**Resolution**: ✅ **Fixed**. All three occurrences in `tests/message_queue_redesign/test_stale_recovery_v2.py` now use bound parameters with Python booleans (`cancel_requested = :cancel_requested` with `"cancel_requested": True`, and `retry_scheduled = :retry_scheduled` with `"retry_scheduled": bool(retry_scheduled)` or `"retry_scheduled": True`). All 27 tests in that file pass.

**Suggested fix**: Use bound parameters with Python booleans:
```python
text("UPDATE task SET cancel_requested = :val"), {"val": True}
```

---

## Latent Issues (correctness, not failure)

### 4. `LIKE` / `ilike` wildcards not escaped

**File**: `daemon/repositories/project/repository.py:998-1007`

**Problem**: Search history escapes `%` and `_` in the query string with a backslash, but doesn't pass an `ESCAPE` clause to `ilike()`. By default, neither SQLite nor PostgreSQL treats `\%` as a literal — it still matches `%`.

```python
escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
search_term = f"%{escaped}%"

# ...
stmt = select(ProjectHistoryEntry).where(
    ProjectHistoryEntry.project_id == project_id,
    or_(
        ProjectHistoryEntry.summary.ilike(search_term),
        func.coalesce(ProjectHistoryEntry.details, "").ilike(search_term),
    ),
)
```

**PostgreSQL fix** (add `ESCAPE` clause):

```python
from sqlalchemy import literal

stmt = select(ProjectHistoryEntry).where(
    ProjectHistoryEntry.project_id == project_id,
    or_(
        ProjectHistoryEntry.summary.ilike(search_term, escape="\\"),
        func.coalesce(ProjectHistoryEntry.details, "").ilike(search_term, escape="\\"),
    ),
)
```

**SQLAlchemy 2.x** also accepts `escape="\\"` directly on `ilike()`. The current behavior is a pre-existing bug — user search queries containing `%` or `_` will match wildcards (e.g., searching for "100%" would match anything containing "100"). This bug exists on both databases.

**Status**: ✅ **Fixed**. Both `ilike()` calls in `search_history_entries` now use `escape="\\"`. The pre-existing `list_history_entries` method also had a latent bug where it incorrectly referenced `search_term` (defined only in `search_history_entries`); that spurious `or_(...ilike(search_term...))` clause was removed as part of the fix.

---

## Verified Safe (already handled correctly)

These patterns were checked and are **not** issues:

| Pattern | File | Status |
|---------|------|--------|
| `on_conflict_do_update()` / `on_conflict_do_nothing()` | `daemon/repositories/project/repository.py:95-114`, `daemon/migrations/data_migrator.py:294-302` | ✅ Uses `_get_dialect_insert()` helper that returns `pg_insert` or `sqlite_insert` |
| `PRAGMA table_info()` / `sqlite_master` queries | `daemon/repositories/factory.py:204, 266, 279` | ✅ Guarded with `if "sqlite" not in str(conn.engine.url): return` |
| `PRAGMA table_info()` in `migrations/runner.py` | `daemon/migrations/runner.py:172, 243, 252` | ✅ Only invoked in `ensure_migrations_table` / `_sync_migrations_table_schema` which run before PG connection is used; runner only fires for SQLite |
| `PRAGMA` setup hooks | `daemon/repositories/factory.py:126-129` | ✅ Inside `if is_sqlite:` branch |
| `setval()` / `pg_get_serial_sequence()` | `daemon/migrations/data_migrator.py:423-442` | ✅ Inside `_pg_engine` block — only runs on PostgreSQL |
| `_table_exists` introspection | `daemon/migrations/data_migrator.py:558-588` | ✅ Correctly branches between `sqlite_master` and `information_schema.tables` |
| `RETURNING *` clauses | `daemon/repositories/task/repository.py:148, 478, 702, 763` | ✅ Supported on both; rows consumed via named attribute access in `_row_to_task` |
| `IS NULL` / `IS NOT NULL` | various | ✅ Standard SQL — works on both |
| `func.coalesce(...)` | `daemon/repositories/project/repository.py:1007` | ✅ Standard SQL — works on both |
| `BOOLEAN DEFAULT 0/1` in migrations | `daemon/repositories/factory.py:271` | ✅ Inside `if "sqlite" not in ...` guard |

---

## Action Plan

| # | Issue | Severity | Effort | Suggested Owner | Status |
|---|-------|----------|--------|-----------------|--------|
| 1 | `json_extract`/`json_set` in source repo | 🔴 Critical | Medium — needs dialect branching or ORM rewrite | Source repository | ✅ Fixed (dialect branch in `daemon/repositories/source/repository.py:113-148`) |
| 2 | `.contains()` on JSON columns in project repo | 🔴 Critical | Low — replace with JSONB containment | Project repository | ✅ Fixed (dialect branch in `daemon/repositories/project/repository.py:250-293`) |
| 3 | `boolean = 0/1` in stale_recovery tests | 🔴 Critical | Low — use bound Python booleans | Test author | ✅ Fixed (`tests/message_queue_redesign/test_stale_recovery_v2.py:78-92, 122-139`) |
| 4 | Missing `ESCAPE` clause on `ilike` | 🟡 Latent | Low — add `escape="\\"` param | Project repository | ✅ Fixed (`daemon/repositories/project/repository.py:1008-1009`); also removed pre-existing `search_term` reference bug from `list_history_entries` |

### Recommended Order

1. **Issue #2** first — same class of error as the one that triggered this audit; high-confidence fix.
2. **Issue #4** next — pre-existing bug, very small change, should be backported to SQLite too.
3. **Issue #1** — needs more design (dialect branching or ORM migration); can be deferred one sprint.
4. **Issue #3** — quick win, but only affects tests; prioritize alongside PostgreSQL test infrastructure setup.

---

## Verification

After fixes, run the following to verify:

```bash
# SQLite regression — make sure nothing breaks
uv run pytest tests/test_project_tools.py tests/message_queue_redesign/ -v

# PostgreSQL smoke test (if a PG test DB is available)
POSTGRES_HOST=localhost POSTGRES_DB=ensemble_test \
  uv run pytest tests/ -v -k "postgres or json"
```

**Result on 2026-06-04**: `uv run pytest tests/test_project_store.py tests/test_project_store_sqlmodel.py tests/test_project_tools.py tests/test_project_history.py tests/test_project_history_api.py tests/test_scheduler_instance_mode.py tests/message_queue_redesign/ tests/unit/test_mcp_kb_server.py -v --tb=short` → **633 passed**.

Additionally, manually exercise the affected code paths:
- `project_search` tool — should no longer raise `UndefinedFunction` on JSON equality
- `project_get_by_instance` and `project_get_by_directory` — should still return expected projects
- `source.increment_scheduler_run_counter` — counter should increment atomically

---

## Appendix: Search Patterns Used

```bash
# JSON functions (SQLite-specific)
rg "json_extract|json_set|json_each|json_tree|json_type|json_array_length|json_valid"

# Boolean/int comparison in raw SQL
rg "= ?0\b|= ?1\b" --type py

# .contains() on columns
rg "\.contains\(" --type py

# PRAGMA / sqlite_master
rg "PRAGMA|sqlite_master|sqlite_sequence|sqlite_version"

# DISTINCT
rg "distinct\(\)" --type py

# Dialect-specific
rg "ilike|LIKE|::|CAST"
```
