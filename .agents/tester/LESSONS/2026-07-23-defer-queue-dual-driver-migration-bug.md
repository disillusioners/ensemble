# Lesson: Defer-Queue Idle Gate — Cross-Driver Migration Bug

**Date:** 2026-07-23
**Branch:** `feature/defer-queue-idle-gate` @ `c7db8598`
**Session:** defer-queue-idle-gate-full-suite
**Refs:** commit `843e2c34` (pre-existing, `latest` branch)

## Symptom

`core_unit_test.sh` fails with 39 errors / 0 unexpected passes against the `feature/defer-queue-idle-gate` branch. All 39 failures share the same root cause:

```
sqlite3.OperationalError: near "CONSTRAINT": syntax error
daemon.migrations.runner.MigrationError: Migration 20260714_000001 failed
```

## Root Cause

`InstanceManager.__init__` invokes `_apply_migrations()`, which runs migration `20260714_000001_widen_job_queue_type_constraint.sql`. That migration contains:

```sql
ALTER TABLE job_queues DROP CONSTRAINT IF EXISTS ck_job_queues_queue_type;
```

`DROP CONSTRAINT` is **PostgreSQL-specific syntax**. SQLite supports `ALTER TABLE ... DROP COLUMN` and basic table operations, but does NOT support `DROP CONSTRAINT`. The migration runner applies ALL `.sql` migrations to BOTH PostgreSQL and SQLite, regardless of dialect.

The migration was introduced by commit `843e2c34` (already on `latest` branch BEFORE the defer-queue branch was based on it):

```
fix(migration): widen ck_job_queues_queue_type to include defer and background types
```

## Why This Wasn't Caught Earlier

- The `core_unit_test` pack was last run on `2026-07-12` (per `PACKS.md`) and reported PASS. The migration `20260714_000001` was created **after** that run, so the SQLite-path failure was never seen.
- The PostgreSQL conformance pack `postgres_test` passes cleanly (109 / 109) — confirming the migration is valid on the primary DB.
- The defer-queue branch inherits the bug from `latest` but did not introduce it.

## Recommendation

Fix the cross-driver migration bug. Three approaches, in order of preference:

1. **Best — gate on dialect in the runner.** Update `daemon/migrations/runner.py` to detect the engine dialect and skip unsupported SQL statements on SQLite. The dual-driver pattern is already established — same approach as `_ensure_postgres_columns` (per project critical notes).

2. **Alternative — driver-specific migrations.** Split into `20260714_000001_widen_job_queue_type_constraint.postgres.sql` and `20260714_000001_widen_job_queue_type_constraint.sqlite.sql` (or similar). The runner picks based on driver.

3. **Last resort — ORM-level constraint re-declaration.** Use SQLAlchemy's `__table_args__` to define the constraint, then have `_ensure_postgres_columns` re-create it on PG only. This is the most invasive change.

## What This Lesson Teaches

- **Dual-driver migrations MUST be dialect-aware.** SQLite supports a strict subset of PostgreSQL's DDL. Any migration that uses PostgreSQL-specific syntax (`DROP CONSTRAINT`, `ALTER TYPE`, `CREATE INDEX CONCURRENTLY`, etc.) will break the SQLite unit-test path.
- **Don't trust pre-merge "PASS" without re-running the full suite.** The `core_unit_test` pack ran 11 days ago before the broken migration was added. Stale "PASS" markers in `PACKS.md` are misleading.
- **Pre-existing failures are NOT a quick fix.** The worker correctly declined to apply a test-only fix to the migration. The right fix is a production-code change (dialect gating) that requires design discussion, not a quick edit.
- **Quarantine is for flaky tests, not for stable pre-existing bugs.** I did NOT add these 39 failures to `QUARANTINE.md` because they are not flaky — they are stable failures. The bug is real and should be fixed, not masked.

## Take-Action

- [ ] Fix the migration runner to handle dialect-specific SQL (or split migrations per dialect).
- [ ] Re-run `core_unit_test` after the fix to confirm 39 failures resolve.
- [ ] Add a CI lint that flags `DROP CONSTRAINT` / `ALTER TYPE` / etc. in migrations that target both DBs.
- [ ] Document the dual-driver migration rules in `daemon/migrations/runner.py` docstring.
