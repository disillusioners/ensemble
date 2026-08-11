# LESSONS: cancel_requested Boolean Type Mismatch (PostgreSQL)

**Date:** 2026-08-11
**Discovered during:** ensure.md Release Gate E2E validation — Task↔JobItem Reconciliation Fix

## Bug

`daemon/manager.py:4557` in `_ensure_postgres_columns()` — the startup reconciliation SQL for stuck tasks used `cancel_requested = 1` (integer literal) for a PostgreSQL boolean column.

## Root Cause

The `task.cancel_requested` column is defined as `bool` (`daemon/repositories/task/models.py:161`). On PostgreSQL, boolean columns reject integer literals — they require `TRUE`/`FALSE`. On SQLite (the old default DB), `1`/`0` work fine because SQLite uses dynamic typing.

This bug was introduced by the Task↔JobItem reconciliation fix — the new startup SQL statement mirrored the SQLite migration's `cancel_requested=1` convention, but the PostgreSQL path (`_ensure_postgres_columns()`) runs raw SQL without type adaptation.

The surrounding statements in the same function correctly used `TRUE`/`FALSE`:
```sql
UPDATE task SET is_deferred = TRUE ...
UPDATE task SET is_background = TRUE ...
```

But the reconciliation statement used:
```sql
UPDATE task SET status = 'cancelled', cancel_requested = 1, ...
```

## Impact

**Daemon fails to start on PostgreSQL** — `Application startup failed. Exiting.` blocks ALL functionality, not just the reconciliation feature.

## Fix Applied

`daemon/manager.py:4557`: `cancel_requested = 1` → `cancel_requested = TRUE`

## Pattern to Remember

**PostgreSQL boolean columns need `TRUE`/`FALSE` literals, not `1`/`0`.** When writing raw SQL for the `_ensure_postgres_columns()` path (or any raw SQL on PostgreSQL), always use SQL boolean keywords. This is a recurring gotcha documented in the project's critical notes:

> 🔴 **[constraint]** PostgreSQL is the PRIMARY dev/test DB. No SQLite-only syntax (rowid) in migrations. Use `_ensure_postgres_columns()` for new columns.

The corollary: raw SQL strings inside `_ensure_postgres_columns()` must also avoid SQLite-only conventions like integer-as-boolean.

## Detection

The bug was caught immediately at daemon startup with a clear error:
```
psycopg.errors.DatatypeMismatch: column "cancel_requested" is of type boolean but expression is of type integer
```
