# Migration Parser Bug — Colon-Bind-Param in Comment + Missing UP Marker

**Date:** 2026-06-19
**Branch:** feature/concurrency-fixes
**Commit:** dd48be65 ("fix: migration parser bug — colon-bind-param in comment + missing UP marker")
**Session:** migration-investigate

## Root Cause

The agents-ensemble migration runner (`daemon/migrations/runner.py`) parses migration files as follows:
1. Extract `up_sql` via regex `--\s*UP\s*\n(.*?)(?=--\s*DOWN|$)`
2. Split `up_sql` by semicolons: `up_sql.split(";")`
3. Feed each chunk to `sqlalchemy.text(stmt)`
4. Execute

**The runner does NOT strip SQL comments before splitting.** This means any `:identifier` (colon-prefixed word) — even inside `--` comments — is interpreted by `sqlalchemy.text()` as a bind parameter, causing `BindParameterError`.

## Bug 1: Colon in Comment (migration 000002)
- **File:** `daemon/migrations/versions/20260619_000002_add_version_columns_to_task_and_job_queue_items.sql`
- **Line 55:** `-- \`WHERE status = :from_status\` guard and does NOT route through the`
- The `:from_status` inside the comment was parsed as a bind parameter.
- **Introduced by commit:** `12f0ad94` (review findings). The earlier `b7895c05` fix only caught semicolons-in-comments, not colon-bind-params.
- **Fix:** Changed `:from_status` → `(from_status)` (parens form, matching line 9's safe pattern).

## Bug 2: Missing -- UP Marker (migration 120000)
- **File:** `daemon/migrations/versions/20260619_120000_fix_idempotency_index_include_deleted_at.sql`
- File had DDL (DROP INDEX + CREATE UNIQUE INDEX) but NO `-- UP` section header.
- `MigrationFile.parse()` requires `--\s*UP\s*\n`; missing it raises `ValueError`.
- `discover_migrations()` catches this and SILENTLY SKIPS the file.
- **Introduced by commit:** `80280a2f` (service layer hardening).
- **Fix:** Added `-- UP\n\n` between header docs and DDL.

## Impact
- Bug 1 was a HARD BLOCKER — migrations aborted on fresh DB (could not start the application).
- Bug 2 was SILENT — the index never got applied via migration (but SQLModel.metadata.create_all created it anyway on fresh DBs).

## Quick Fix Applied
- 2 files changed, 3 insertions, 1 deletion.
- Verified: 43 migrations now discovered (was 42), all apply cleanly on fresh SQLite.
- `task.version` and `job_queue_items.version` columns created successfully.
- Refined `idx_job_idempotency` index with `deleted_at IS NULL` predicate applied.

## Lesson
- **Never use colon-prefix words in migration SQL comments.** Use parens form like `(from_status)` instead.
- **All migration files MUST have `-- UP` section headers.** Missing markers cause silent skips.
- The migration runner should ideally strip comments before splitting, but the immediate fix is in the migration files themselves.
