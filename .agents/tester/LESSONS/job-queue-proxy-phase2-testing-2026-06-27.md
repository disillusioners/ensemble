# Job-as-Queue-Proxy Phase 2 Testing — Findings & Patterns

**Date:** 2026-06-27
**Branch:** `feature/job-as-queue-proxy` (commits `203afe6d`, `ca4dde3d`, `facd61b1`, `6a83f007`)

## Key Findings

### 1. server_default Bug (RECURRING PATTERN)
`JobItem.admission_state` was declared with Python-side `Field(default=...)` only. Under `SQLModel.metadata.create_all()` (used by PostgreSQL test conftest), Python defaults are NOT translated to server defaults. Raw-SQL INSERTs that omit the column fail with `NOT NULL violation`.

**Fix:** Add `sa_column_kwargs={"server_default": text("'queued'")}` to the field.

**This is the SAME pattern as documented in architecture-migration-testing-2026-06-26.md (finding #3):** SQLModel `Field(default=...)` sets Python-side defaults only — NOT PostgreSQL server defaults. This keeps recurring because developers expect the Python default to propagate. ALWAYS add `server_default` for NOT NULL columns that might be inserted via raw SQL.

### 2. status_to_admission Mapping (7→4)
The `status_to_admission()` helper collapses 7 JobStatus values to 4 AdmissionState values:
- pending → queued
- processing → active
- paused → active (lock still held during pause)
- completed/failed/cancelled → done
- dead_letter → dead
- unknown → queued (fallback)

### 3. Dual-Write Sites (26 Verified)
Every status write site now also writes admission_state. Key sites:
- `atomic_transition` (generic path)
- `start_job` / `complete_job` / `fail_job` / `cancel_job`
- `atomic_retry` (FAILED→PENDING)
- `DeadLetterService.move_to_dlq` / `replay_from_dlq`
- `InstanceLifecycleService._terminate/pause/resume`
- `JobFeedbackObserver._finalize_job_db_sync`
- `JobRepository.update` explicitly REJECTS direct admission_state writes

### 4. DEFERRABLE Constraint Triggers (First in Codebase)
Two `DEFERRABLE INITIALLY DEFERRED CONSTRAINT TRIGGER`s:
- Fire at COMMIT time, not at statement time
- `SET CONSTRAINTS ALL IMMEDIATE` can force inline firing for testing
- Error: SQLSTATE 23000 (`integrity_constraint_violation`), NOT 23514
- Installed via `_ensure_postgres_columns`, NOT via .sql migration (PG skips those)
- Idempotent: safe to run multiple times

### 5. Dual-Path Migration Pattern Confirmed
- SQLite: `.sql` migration file adds column + backfill
- PostgreSQL: `_ensure_postgres_columns()` adds column + triggers at startup
- Both paths produce the same schema state

## Testing Strategy Used
3 parallel sessions:
1. **Existing suite** — broad regression detection (SQLite + PG)
2. **Dual-write tests** — targeted lifecycle verification (SQLite)
3. **PG constraint tests** — trigger enforcement verification (PostgreSQL)
Plus 1 verification session to confirm final state.

All completed within ~10 minutes total wall time.

## Gotchas for Future Testing
- PG constraint trigger tests need ALL NOT NULL columns in raw INSERTs (created_at, job_type, retry_count, acquired_at) — not just the columns being tested
- The `deleted_at` guard in trigger 2 cannot be tested via INSERT alone (trigger 1 fires first) — must use the UPDATE flow (insert valid pair → soft-delete → update)
- SQLSTATE for `integrity_constraint_violation` is 23000, not 23514
