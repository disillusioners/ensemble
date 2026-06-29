# Post-Merge Startup Blockers (Job-as-Queue-Proxy Refactor)

**Date**: 2026-06-29
**Branch**: `latest`, commit `4f9649d7`
**Fixed by**: opencode session e2e-fix-and-run

## Problem

After merging the Job-as-Queue-Proxy refactor (Phases 0-7), the daemon could not start on either PostgreSQL or SQLite backends. Three separate regressions blocked startup entirely.

## Blocker #1: PostgreSQL — `status` column reference after drop

### What Happened
`_ensure_postgres_columns()` in `daemon/manager.py` runs an unconditional backfill UPDATE:
```sql
UPDATE job_queue_items SET admission_state = 'queued' WHERE status = 'pending' AND admission_state = 'queued'
```
But the `status` column was **dropped in Phase 5** of the refactor. This caused:
```
sqlalchemy.exc.ProgrammingError: (psycopg.errors.UndefinedColumn) column "status" does not exist
```

### Root Cause
Phase 5 Batch 2 (`41633433`) removed legacy columns from JobItem without auditing the inline PG backfill in `_ensure_postgres_columns()`. The backfill had no try/except or idempotency guard.

### Fix
Wrapped status-based UPDATEs in try/except catching `ProgrammingError` and `InternalError`. Each statement runs in its own transaction so a failed UPDATE doesn't poison subsequent ones.

**Commits**: `dc550976` + `0af65244`

### Lesson
**When dropping columns, audit ALL code that references those columns** — especially:
- Backfill statements in `_ensure_postgres_columns()`
- Migration .sql files
- ORM queries
- Any ad-hoc SQL

## Blocker #2: SQLite — Migration comment has semicolon

### What Happened
Migration `20260627_000003_task_is_deferred.sql` has a comment:
```sql
-- SQLite is loosely typed; ``BOOLEAN`` is stored as
```
The semicolon in "loosely typed;" caused the migration runner to split incorrectly, producing invalid SQL.

### Fix
Replaced semicolon with em-dash.

### Lesson
**Never put semicolons in SQL comments** — the migration runner splits on `;` and doesn't understand comment context.

## Blocker #3: Migration runner doesn't strip comments before splitting

### What Happened
`runner.py:330`:
```python
statements = [s.strip() for s in migration.up_sql.split(";") if s.strip()]
```
No comment awareness — semicolons in comments break statement splitting.

### Fix
Filter out full-line comments (lines starting with `--`) before splitting on `;`.

### Lesson
**Migration runners should strip comments before splitting** — this is a defensive fix to prevent future occurrences.

## Testing Impact

All 4 E2E tests from `ensure.md` were blocked because the daemon couldn't start. After fixing all 3 blockers:
- Test 1 (happy path): ✅ PASS
- Test 2 (pause/resume): ❌ FAIL (unrelated PG-specific VJM cancel bug)
- Test 3 (terminate/revive): ✅ PASS
- Test 4 (wave spawn): ✅ PASS
