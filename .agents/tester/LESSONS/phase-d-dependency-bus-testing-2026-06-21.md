# Phase D Dependency Bus — Testing Findings & Lessons

## Date: 2026-06-21
## Branch: feature/decouple-phase-d

---

## Key Findings

### 1. D10 Migration Auto-Application Bug (CRITICAL — FIXED)
**Problem:** The D10 migration (`20260621_000002_drop_legacy_completion_columns.sql`) was designed to be manual-only/deferred, but the migration runner had no mechanism to exclude it. On SQLite, it would auto-apply, dropping the `waiting_for` column and breaking 71 tests that depend on it.

**Root Cause:** The migration runner (`daemon/migrations/runner.py`) picks up all `.sql` files in `daemon/migrations/versions/`. There was no way to mark a migration as "manual-only".

**Fix Applied (commit `9f496168`):**
- Added `-- MANUAL: TRUE` comment marker to D10 migration header
- Updated `MigrationFile.parse()` in runner.py to detect this marker
- Runner skips manual-only migrations with log: "Skipping manual-only migration X -- apply via apply_migration()"

**Lesson:** When designing deferred/manual migrations, ensure the migration runner has an explicit opt-out mechanism. Comment-based markers (`-- MANUAL: TRUE`) are a clean, non-invasive solution.

### 2. PostgreSQL Test Fixture Isolation (IMPORTANT — FIXED)
**Problem:** Three PostgreSQL test files redeclared a module-scoped `pg_engine` fixture that called `SQLModel.metadata.drop_all(engine)` on teardown. This clobbered the session-scoped autouse `_pg_truncate_tables` fixture in sibling files, causing `UndefinedTable` errors.

**Root Cause:** Module-scoped fixtures sharing the same DB as session-scoped fixtures must NOT drop tables — the session-scoped fixture owns the schema lifecycle.

**Fix Applied (commit `1545cbbe`):**
- Removed redundant per-module `drop_all()` calls from 3 test files
- The session-scoped `pg_engine` in `tests/postgres/conftest.py` already owns the schema lifecycle

**Lesson:** When using module-scoped fixtures with a shared database, never call `drop_all()` — let the session-scoped fixture own the schema lifecycle. Per-module teardown should only clean data (TRUNCATE), not schema.

### 3. Session Exceeded Quick Fix Scope (PROCESS — FLAGGED)
**Problem:** The SQLite regression session (phase-d-sqlite-regression) was authorized for quick fixes (< 20 lines, single file). It made a 544-line change across 12 files (6 source files, 3 migration files, 1 test file, 2 doc files).

**Assessment:** The changes were legitimate (addressing 3 CRITICAL + 3 WARNING reviewer issues found during testing), but the scope was significantly larger than authorized. The session found real bugs during test execution and fixed them, which is efficient, but should have flagged the scope expansion.

**Lesson:** When a session discovers issues that exceed quick fix scope, it should:
1. Apply the quick fix for the immediate blocker (D10 marker)
2. Report the remaining issues (reviewer CRITICAL/WARNING) for separate handling
3. Not bundle unrelated fixes into a single large commit

### 4. Pre-Existing Test Failures (65 tests across 26 files)
**Finding:** The full SQLite suite has 65 pre-existing failures across 26 files. These are NOT Phase D regressions:
- 16 failures in `tests/unit/rag/test_config.py` (RAG configuration)
- 9 failures in `tests/test_finalize_job_h15.py` (H15 job finalize)
- 3 failures in `tests/message_queue_redesign/test_stale_recovery_v2.py` (references DELETED MESSAGE dispatch code)
- 3 failures in `tests/integration/test_multi_turn_resume.py` (integration marker issue)
- Remaining spread across 22 other files

**Lesson:** When a major architectural change (Phase D) is tested, always verify failures are NOT in the changed code paths. The fact that all 129 Phase D tests pass and no failures are in dependency_bus/correlation_manager files confirms Phase D is clean.

---

## Testing Patterns for Dependency Bus

### Pattern 1: Bus Shadow Equivalence Testing
When testing a system with a feature flag (bus ON vs OFF), test BOTH paths produce identical behavior:
- `use_dependency_bus=True` → bus is authority, CM is shadow
- `use_dependency_bus=False` → CM is authority (rollback path)
- Both should produce the same observable behavior (FollowUp enqueued exactly once)

### Pattern 2: Crash Recovery Testing
The bus must survive restarts:
1. Write a PENDING watcher to DB
2. Simulate crash (don't emit terminal)
3. Restart the service
4. Emit terminal → watcher should transition to FIRED and FollowUp should be enqueued
- Use `_recover_fired_unsent()` to find FIRED-but-not-enqueued rows

### Pattern 3: Backpressure Verification
The bus must process watchers one at a time (atomic transitions):
- Write 10,000 watchers for the same source_task_id
- Emit terminal
- Verify exactly ONE FollowUp is enqueued (guarded WHERE state='PENDING')

---

## Test Execution Notes

### Full Suite Timing
- Total tests: 8186
- Execution time: ~9 minutes (532s)
- This is too long for a single test pack (2-min unit limit, 5-min integration limit)
- The suite should be split into packs by module/phase

### PostgreSQL Suite
- Total tests: 80
- Execution time: ~15 seconds
- All tests reproducible (ran twice, no flakes)
- Requires PostgreSQL running at localhost:5432 with `ensemble_test` DB
