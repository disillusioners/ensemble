# E2E Tests — ensure.md Release Gate Validation
Date: 2026-07-14
Session IDs: e2e-start-daemon, e2e-create-pack, e2e-run-tests, e2e-diagnose, e2e-fix-migration, e2e-rerun-*

## Summary

| # | Test | Result | Runtime | Notes |
|---|------|--------|---------|-------|
| 1 | test_parent_child_workflow_happy_path | ✅ PASS | 77s | Post-fix |
| 2 | test_pause_after_spawn_then_resume | ✅ PASS | 101s | Post-fix |
| 3 | test_terminate_after_spawn_then_revive | ✅ PASS | 59s | Post-fix |
| 4 | test_wave_spawn_with_defer_queue | ❌ FAIL | 207s | Deferred job stuck in `pending` — real bug |

**Overall: 3/4 PASS, 1 FAIL**

## Root Cause Found & Fixed

### Bug: Missing DB migration — `ck_job_queues_queue_type` constraint

The `system_background_queue` feature was added to the model (`daemon/repositories/job_queue/models.py:186`) defining `'fifo', 'parallel', 'defer', 'background'`, but the corresponding SQL migration to widen the CHECK constraint was never created. The original constraint only allowed `'fifo', 'parallel'` (later `'defer'` was added ad-hoc to PostgreSQL, but `'background'` was missing).

**Impact:** Daemon startup failed to provision `system_background_queue` → cascade failure → leader instances never reached terminal status in E2E tests.

**Fix (commit `843e2c34`):**
1. Created `daemon/migrations/versions/20260714_000001_widen_job_queue_type_constraint.sql`
2. Updated `_ensure_postgres_columns()` in `daemon/manager.py` with idempotent DROP/ADD constraint
3. Applied constraint directly to live PostgreSQL `ensemble_dev`
4. Committed all changes

**Secondary fix (commit `5dc5bc67`):**
- Makefile `make sync` now runs `uv sync --extra dev` instead of `uv sync` so pytest-timeout installs properly
- Without this, `PYTEST_TIMEOUT=280` in the pack script was silently ignored

## Test 4 Failure Details

**File:** `tests/e2e/test_e2e_workflows.py:2418`
**Assertion:** `assert job_status == "completed"` — got `'pending'`
**Context:** The deferred job was admitted but never ran to completion. After 120s waiting, the job remained in `pending` status.
**Error:** `Deferred job did not reach 'completed' (got 'pending'). Acceptable end-state is 'completed' (success).`

This indicates a real runtime issue with defer queue job processing — the job was queued to the defer queue but never claimed/processed by the JobProcessor. Needs investigation of the defer queue admission/claim path.

## ensure.md Validation Results

### Release Gate (E2E)
- [x] E2E: Normal parent→child workflow completes (happy path) — ✅ PASS (77s)
- [x] E2E: Pause after spawn, then resume works correctly — ✅ PASS (101s)
- [x] E2E: Terminate after spawn, then revive documented — ✅ PASS (59s)
- [ ] E2E: Wave spawn (2 children) + defer queue ordering + cross-system — ❌ FAIL (deferred job stuck pending)

**ensure.md Release Gate: 3/4 critical requirements PASS, 1 FAIL**

## Packs Created
- `test/packs/e2e_workflows_ensure_test.sh` — E2E workflows pack (dual-layer timeout, 4 tests)

## Lessons Learned
1. Missing DB migrations can cause silent cascade failures — daemon starts but system queues fail, blocking E2E flows
2. The `make sync` command didn't install dev dependencies, causing pytest-timeout to be silently absent
3. All 4 E2E tests combined can't fit in 5 min (combined ~450s) — must run individually
4. pyproject.toml `timeout = 30` config is too short for E2E tests — need `--override-ini="timeout=280"` or `PYTEST_TIMEOUT=280` env override
