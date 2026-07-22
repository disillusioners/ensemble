# ensure.md Validation Lessons — Injection Queue Change Set

**Date:** 2026-07-22
**Branch:** feature/injection-queue (commit 85097179)

---

## Lesson 1: Pre-Existing SQLite Migration Bug Causes 38 Failures in Manager Tests

### Problem
`c2_core_regression_unit_test.sh` and `c2_pg_manager_unit_test.sh` both show 38 failures in `tests/test_manager.py`. Every failure is the same root cause:

```
sqlite3.OperationalError: near "CONSTRAINT": syntax error
[SQL: ALTER TABLE job_queues DROP CONSTRAINT IF EXISTS ck_job_queues_queue_type]
```

### Root Cause
Migration `20260714_000001_widen_job_queue_type_constraint.sql` uses `ALTER TABLE ... DROP CONSTRAINT IF EXISTS` — PostgreSQL-only syntax. SQLite does not support `DROP CONSTRAINT`.

### Why PG Pack Also Fails
`c2_pg_manager_unit_test.sh` sets `DATABASE_URL` to PostgreSQL, but the test fixtures in `tests/test_manager.py` use SQLite in-memory databases via mock configurations, not the `DATABASE_URL`. The env var does not override the test fixture DB engine.

### Impact on Injection Validation
**None.** The injection queue change set (commit 85097179) only touched test files (`test_injection_graph.py`, `test_loop_breaker_integration.py`). The migration file was introduced by a separate commit (`843e2c34`). The 38 failures are pre-existing and unrelated.

### Recommendation
Fix the migration to use `_ensure_postgres_columns()` or a dialect-aware approach for SQLite compatibility. This is tracked in the critical notes as a known constraint.
