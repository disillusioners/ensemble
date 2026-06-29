# E2E Test Report: ensure.md Post-Merge Verification

**Date**: 2026-06-29
**Branch**: `latest`
**Base Commit**: `4f9649d7` (Job-as-Queue-Proxy refactor fully merged)
**Session**: e2e-fix-and-run (ses_0ebc2e876ffeqPn7INtJK1R3eR)

## Summary

| # | Test | Result | Duration | Notes |
|---|------|--------|----------|-------|
| 1 | `test_parent_child_workflow_happy_path` | ✅ PASS | 43.6s | Parent→child happy path, VJM assertions pass |
| 2 | `test_pause_after_spawn_then_resume` | ❌ FAIL | 85.7s | VJM cancel-propagation assertion fails on PG |
| 3 | `test_terminate_after_spawn_then_revive` | ✅ PASS | 37.3s | Terminate→spawn→revive works correctly |
| 4 | `test_wave_spawn_with_defer_queue` | ✅ PASS | 46.2s | Wave spawn + defer queue + VJM assertions pass |

**Overall: 3/4 PASS**

## Startup Blockers Found & Fixed (3 Critical Quick Fixes)

### Blocker #1 — PostgreSQL: `status` column reference after drop
- **Location**: `daemon/manager.py` ~line 2082, inside `_ensure_postgres_columns()`
- **Error**: `sqlalchemy.exc.ProgrammingError: (psycopg.errors.UndefinedColumn) column "status" does not exist`
- **Root Cause**: The backfill UPDATE referenced the `status` column which was dropped in Phase 5 of the Job-as-Queue-Proxy refactor. No try/except or idempotency guard.
- **Fix**: Wrapped status-based UPDATEs in try/except catching `ProgrammingError` and `InternalError`. Each statement runs in its own transaction.
- **Commit**: `dc5509766675ade1fe0d97afa7b1704f4426b196` + `0af6524411509569d74e9f342e9b7ed05e07ee2b`

### Blocker #2 — SQLite: Migration comment has semicolon
- **Location**: `daemon/migrations/versions/20260627_000003_task_is_deferred.sql`
- **Error**: `sqlite3.OperationalError: near "``": syntax error`
- **Root Cause**: Comment line "loosely typed; ``BOOLEAN``" contains a semicolon that the migration runner splits on.
- **Fix**: Replaced semicolon in comment with em-dash.
- **Commit**: `dc5509766675ade1fe0d97afa7b1704f4426b196`

### Blocker #3 — Defensive: Migration runner strips comments before splitting
- **Location**: `daemon/migrations/runner.py` ~line 330
- **Root Cause**: `up_sql.split(";")` doesn't strip SQL comment lines before splitting.
- **Fix**: Filter out lines starting with `--` before splitting on `;`. Applied to both UP and DOWN paths.
- **Commit**: `dc5509766675ade1fe0d97afa7b1704f4426b196`

## Test 2 Failure Details (Needs Investigation)

### `test_pause_after_spawn_then_resume` — FAIL

**Error**:
```
AssertionError: Work surface did not reflect cancellation of 7e93e7bb... (last status=completed)
tests/e2e/test_e2e_workflows.py:1735
```

**Analysis**:
- The test creates a JobItem, waits for it to appear in `/api/work` as `kind="job"`, calls `POST /api/jobs/{id}/cancel` (returns 200), then polls `/api/work/{id}` for `status="cancelled"` for 30s.
- The cancel endpoint returns 200 (success) but the work surface stays at `completed`.
- This is a **PostgreSQL-specific bug** in the VJM cancel path.
- The same test PASSES on SQLite.
- NOT caused by the 3 startup fixes — this is a pre-existing PG-specific bug in cancel-propagation.

**Needs**: Investigation of the cancel endpoint's interaction with the work-surface projection on PostgreSQL.

## Special Attention Areas Validated

1. ✅ **Job creation, queuing, status transitions** — Working (Tests 1, 3, 4)
2. ✅ **Instance spawning and child reports** — Working (Tests 1, 3, 4)
3. ✅ **Job completion flows** — Terminal states reached correctly
4. ⚠️ **Pause/resume cascade** — Cascade works, VJM cancel assertion fails on PG (Test 2)
5. ✅ **API response shapes** — Legacy status strings preserved via backward-compat mapping
6. ✅ **Virtual job management** — `GET /api/work`, work_id, job_create, job_list all functional (Tests 1, 4)

## Commits Applied

| Commit | Description |
|--------|-------------|
| `dc5509766675ade1fe0d97afa7b1704f4426b196` | Fix 3 startup blockers: PG status column guard + SQLite migration comment + runner comment-awareness |
| `0af6524411509569d74e9f342e9b7ed05e07ee2b` | Per-statement transaction fix for blocker #1 |

## Overall Status

- **Startup Blockers**: ✅ RESOLVED (3 fixes committed)
- **E2E Tests**: 3/4 PASS
- **Testing Complete**: ❌ NOT READY — Test 2 VJM cancel failure on PG needs investigation
