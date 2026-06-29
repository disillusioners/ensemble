# Job-as-Queue-Proxy Phase 7 FINAL Pre-Merge — Critical Findings

**Date:** 2026-06-29
**Branch:** `feature/job-as-queue-proxy` (HEAD: `dfab3e6d` Phase 7b)

## Two MERGE BLOCKERS Found

### BLOCKER 1: PostgreSQL `status` column model-schema mismatch
**Root cause:** Phase 5 dropped the `status` column from the PostgreSQL DB schema, but the `JobItem` SQLModel still defines `status` as a field. On PostgreSQL, every INSERT via SQLModel includes `status`, which fails with:
```
psycopg.errors.UndefinedColumn: column "status" of relation "job_queue_items" does not exist
```

**Why SQLite passes but PG fails:** SQLite tests use `SQLModel.metadata.create_all()` which creates tables from the model definition (including `status`). PostgreSQL has already run the migration that dropped `status`, so the column is gone from the schema. This is the classic "create_all masks migration drift" pattern.

**Impact:** 29 NEW PostgreSQL test failures. ALL JobItem write paths fail on PostgreSQL.
- test_concurrent_enqueue (5)
- test_concurrent_status_transitions (10)
- test_optimistic_locking (5)
- test_jq_proxy_phase2_constraints (8)
- test_pg_restart_survival (1, pre-existing)

**Fix:** Remove `status` column from `JobItem` SQLModel. Either remove entirely or convert to a computed `@property` that derives from `admission_state` via `_ADMISSION_TO_LEGACY_STATUS`.

### BLOCKER 2: 3 test files import removed `JobStatus` enum
Phase 7b (`dfab3e6d`) removed `JobStatus` from `daemon.repositories.job_queue.__init__.py`, but 3 test files still import it:
- `tests/unit/services/test_jq_proxy_phase4_finalize_terminal.py`
- `tests/unit/services/test_jq_proxy_phase4_lifecycle_regression.py`
- `tests/unit/test_resume_flow_redesign.py`

**Impact:** `ImportError: cannot import name 'JobStatus'` → 56 tests uncollectable.

**Fix:** Update imports from `JobStatus` to `AdmissionState` in these 3 files.

## What PASSED Cleanly

### Functional Smoke Test (11/11 ✅)
Full `admission_state` lifecycle verified:
- queued → active → done (create/start/complete)
- active → done + failed_at → queued (fail/retry)
- active → dead → queued (DLQ/replay)
- active stays active during pause/resume (instance-level concern)

### API Backward Compat (✅)
- `_ADMISSION_TO_LEGACY_STATUS` mapping at `models.py:62`
- 18 production call sites verified
- JobResponse schema: 24/24 fields present
- Legacy status strings emitted for all 4 AdmissionState values

### Regression Grep Checks (✅ ALL PASS)
- No `JobStatus.X.value` in production code
- No kill-switch remnants
- No failed_at DROP in PG helper
- failed_at still in JobItem model (as retry-eligibility marker)

## SQLite Suite Status
- **Total collected:** 8256 (+ 56 uncollectable = 8312 total)
- **Passed:** ~6343
- **Failed:** ~260 (mostly pre-existing: RAG disabled, mock config, reasoning_content)
- **Skipped:** ~201
- **Duration:** ~15 min serial (too slow for single opencode session, sharded into 8 groups)
- **3 collection errors** (BLOCKER 2)

## Pattern: create_all Masks Migration Drift
**Gotcha:** When testing with in-memory SQLite via `SQLModel.metadata.create_all()`, tables are created from the model definition — NOT from migration scripts. If a migration dropped a column but the model still has it, SQLite tests will PASS (column exists from create_all) while PostgreSQL tests will FAIL (migration already ran).

**Lesson:** Always run PostgreSQL tests to verify migrations are correctly reflected in models. SQLite-only testing can mask model-schema mismatches.

## Pattern: Phase 7b Left Test Files Behind
Phase 7b removed `JobStatus` enum from production code (`daemon/repositories/job_queue/__init__.py`), but didn't update all test files that imported it. This is a common refactoring gap: production code cleanup without corresponding test cleanup.

**Lesson:** When removing a symbol from production code, grep ALL test files for imports of it before committing.
