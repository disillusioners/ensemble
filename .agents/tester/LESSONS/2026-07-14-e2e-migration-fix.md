# E2E Test Fix — Missing ck_job_queues_queue_type Migration

**Date:** 2026-07-14
**Commits:** `843e2c34` (migration fix), `5dc5bc67` (Makefile fix)

## Root Cause

The `background` queue type was added to the JobQueue model (`daemon/repositories/job_queue/models.py:186`) but the corresponding database migration to widen the `ck_job_queues_queue_type` CHECK constraint was never created.

**Before fix:**
- PostgreSQL constraint: `CHECK (queue_type IN ('fifo', 'parallel', 'defer'))` — missing `'background'`
- SQLite migration: `CHECK (queue_type IN ('fifo', 'parallel'))` — missing `'defer'` and `'background'`

**Symptom:** All 4 E2E workflow tests timed out at 5 minutes. Tests passed spawn/child-creation phases but hung indefinitely in polling loops waiting for leader instances to reach terminal status.

**Cascade chain:**
1. Daemon startup → tries to insert `system_background_queue` → CheckViolation → WARNING logged
2. `system_defer_queue` and `system_background_queue` rows never created in `job_queues` table
3. Code paths routing to defer/background queues silently fail or enqueue against missing queue
4. Leader instances never receive child-report events → never reach `completed` status
5. E2E polling loops (`_wait_for_leader_completion_safe`, `_wait_for_completion`) loop forever

## Fix Applied

### Commit `843e2c34`
1. Created `daemon/migrations/versions/20260714_000001_widen_job_queue_type_constraint.sql` (SQLite UP/DOWN)
2. Updated `_ensure_postgres_columns()` in `daemon/manager.py` with idempotent DROP/ADD constraint
3. Applied directly to live PostgreSQL DB

### Commit `5dc5bc67`
- Makefile `make sync` changed from `uv sync` to `uv sync --extra dev`
- Without this, pytest-timeout was never installed, and `PYTEST_TIMEOUT=280` was silently ignored

## Before/After

| Metric | Before | After |
|--------|--------|-------|
| Daemon startup | 2 warnings (CheckViolation) | 0 warnings |
| E2E test 1 | TIMEOUT (300s) | PASS (77s) |
| E2E test 2 | TIMEOUT (300s) | PASS (101s) |
| E2E test 3 | TIMEOUT (300s) | PASS (59s) |
| E2E test 4 | TIMEOUT (300s) | FAIL (207s — deferred job stuck pending) |

## Key Lesson

**Per critical note in project history:** "Phase D enqueued_at on dependency_watchers added via .sql migration which NO-OPs on PostgreSQL. Must use _ensure_postgres_columns() for ALL new columns on existing tables."

This applies to **constraints** too, not just columns. Any CHECK constraint change MUST be reflected in both the SQLite migration AND `_ensure_postgres_columns()` for PostgreSQL.

## Remaining Issue

Test 4 (`test_wave_spawn_with_defer_queue`) fails even after the fix — the deferred job reaches `pending` status but is never claimed/processed. This is a separate runtime issue in the defer queue admission/claim path, not related to the constraint fix.
