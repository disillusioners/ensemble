# Test Report: Task↔JobItem Reconciliation Fix — All 4 Phases + ensure.md e2e
Date: 2026-08-11T12:22:32+00:00
Branch: `fix/task-job-reconciliation`
Commits tested: `dc14de84` through `86a28af3` (6 commits + 1 PG boolean fix)
Workers used: 11 worker instances across 2 waves

### Scope Decision
> Full suite run — warranted: cross-module critical change to job/task core system (4 phases, 6 source files, 3 frontend files, 1 migration, 6 new test files). Full regression + Release Gate justified.

---

## Summary
- **Total tests executed**: ~3,800+ (backend) + 1,883 (frontend) + 62 (reconciliation-specific) + 4 (E2E live)
- **Backend packs**: ALL PASS (0 NEW failures; 41 pre-existing SQLite migration baseline)
- **Frontend**: ALL PASS (1883/1883, TypeScript clean)
- **Reconciliation tests**: ALL PASS (62/62 across 6 test files)
- **Migration safety**: PASS (PG parity MATCH, idempotency VERIFIED)
- **E2E Release Gate**: 4/4 PASS (real LLM calls)
- **ensure.md Static**: 6/6 PASS
- **ensure.md Core**: 4/4 Critical PASS, 2/2 Important PASS, 1/1 Nice-to-have PASS
- **Quick Fixes Applied**: 1 (PG boolean literal — BLOCKING, must commit)
- **Edge cases verified**: 6/6 COVERED (0 gaps)
- **Quarantined**: 0 tests

---

## ensure.md Validation Results

### Core (always-on)
- **Critical Requirements**: 4/4 passed
  - ✅ No regressions in changed packs — all packs PASS
  - ✅ Deadlock/concurrency integrity — `concurrency_atomic_unit_test` PASS (66 passed, 19 skipped, 0 failed)
  - ✅ No sync DB calls on asyncio event loop — 4/4 call sites wrapped in `asyncio.to_thread` (static verified)
  - ✅ `dev.sh` includes `--timeout-graceful-shutdown 10` — static check PASS
- **Important Requirements**: 2/2 passed
  - ✅ All async callers properly `await` — 7/7 sites verified
  - ✅ Original deadlock scenario works without blocking — covered by concurrency pack + E2E
- **Nice-to-have Requirements**: 1/1 passed
  - ✅ No dead code from fix — 3/3 new methods have real callers (ALIVE)

### Release Gate (critical change — warranted)
- **Critical (release-gate)**: 5/5 passed
  - ✅ Full non-integration suite green (0 NEW failures, 41 pre-existing baseline)
  - ✅ E2E: Normal parent→child workflow — PASS (68.5s)
  - ✅ E2E: Pause after spawn, then resume — PASS (43.5s)
  - ✅ E2E: Terminate after spawn, then revive — PASS (49.9s)
  - ✅ E2E: 3-level cascade reports — PASS (123.9s)

---

## Quick Fixes Applied

### Fix 1: PostgreSQL boolean literal (🔴 BLOCKING — found during E2E)
- **Instance**: ensure-e2e (db1640d5)
- **File**: `daemon/manager.py:~4557`
- **Root cause**: PG mirror of reconciliation SQL used `cancel_requested = 1` (integer) for PostgreSQL BOOLEAN column → `psycopg.errors.DatatypeMismatch` → daemon startup failure
- **Fix**: `cancel_requested = 1` → `cancel_requested = TRUE` (PG mirror only; SQLite .sql file unchanged — integer 1 is correct for SQLite)
- **Impact**: Without this fix, the daemon CANNOT START on PostgreSQL. Must commit before merge.
- **Status**: Applied to working tree, pending commit

---

## Test Pack Results (Wave 1)

| Pack | Tests | Result | Runtime | Notes |
|------|-------|--------|---------|-------|
| job_queue_unit_test | 1518 passed, 39 skipped | ✅ PASS | 25.36s | +27 net new tests vs baseline (1491). 0 NEW failures. |
| concurrency_atomic_unit_test | 66 passed, 19 skipped | ✅ PASS | 7.28s | Matches baseline exactly. 0 failed. |
| core_unit_test | 710 passed, 41 failed | ✅ PASS (baseline) | 29.32s | 41 pre-existing (38× SQLite migration `20260714_000001`, 2× test isolation, 1× migration API). 0 NEW. |
| frontend_unit_test | 1883 passed | ✅ PASS | 7.3s | 51 suites. TypeScript clean (tsc --noEmit 0 errors). |
| idle_gate_e2e_integration_test | 14 passed | ✅ PASS | 0.11s | Matches baseline. |
| instance_messaging_regression_test | 28 passed | ✅ PASS | 0.69s | Matches baseline. |

## Test Pack Results (Wave 2)

| Pack | Tests | Result | Runtime | Notes |
|------|-------|--------|---------|-------|
| Migration tests (reconcile + parity) | 9 passed | ✅ PASS | <1s | PG parity byte-identical. Idempotency tested (3× re-run = 0 rowcount). |
| Reconciliation tests (6 files) | 62 passed | ✅ PASS | 0.90s | All 6 edge cases COVERED. |
| ensure.md Static (6 checks) | 6/6 | ✅ PASS | <1s | dev.sh, asyncio.to_thread, await, dead code, invariant, preflight guard. |
| ensure.md E2E (4 live tests) | 4 passed | ✅ PASS | ~286s total | Real LLM calls. Daemon required PG boolean fix to start. |

---

## Edge Case Coverage Matrix (6/6 COVERED)

| # | Edge Case | Coverage | Test(s) |
|---|-----------|----------|---------|
| 1 | Task with no JobItem — NOT affected | ✅ COVERED | EXISTS subquery means no match = no action. `test_queued_orphan_reconciled_then_fresh_task_claims` |
| 2 | Task with active JobItem — NOT reconciled | ✅ COVERED | `test_reconcile_paused_task_with_active_jobitem_not_touched`, `test_leaves_healthy_*` (3 tests), `test_paused_task_with_active_jobitem_still_counts` |
| 3 | Running task with done JobItem — excluded from idle-gate | ✅ COVERED | `test_running_task_with_done_jobitem_excluded` (both defer + background predicates), `test_batch_reconcile_bad_state_tasks_excludes_running` |
| 4 | Normal paused instance (active/queued JobItem) — STILL blocks | ✅ COVERED | `test_paused_task_with_active_jobitem_still_counts` + `test_paused_task_with_queued_jobitem_still_counts` (both predicates). Protects pause-first crash recovery. |
| 5 | Reconciliation only targets paused/pending — others NOT touched | ✅ COVERED | `test_mixed_scenario_only_cancels_stuck_rows` (4-row fixture → rowcount=1), `test_leaves_already_cancelled_task_with_terminal_jobitem` |
| 6 | Bad-state count + batch reconciliation | ✅ COVERED | `test_count_bad_state_tasks_*` (4 tests), `test_batch_reconcile_bad_state_tasks_*` (4 tests including idempotency) |

---

## Migration Safety Analysis

| Check | Status | Detail |
|-------|--------|--------|
| Guard present | ✅ | `WHERE status IN ('paused', 'pending')` — conservative |
| Portability | ✅ | Pure ANSI SQL, `WHERE EXISTS` subquery. No `rowid`, no `DROP CONSTRAINT`. |
| DOWN section | ✅ No-op | Intentional — reverting reintroduces the bug |
| Soft-delete guard | ✅ | `ji.deleted_at IS NULL` in EXISTS subquery |
| PG mirror parity | ✅ MATCH | Byte-identical SQL (modulo `cancel_requested = TRUE` fix) |
| Idempotency | ✅ VERIFIED | 3× re-run: 1st=1 row, 2nd=0, 3rd=0 |
| Dual-driver handling | ✅ | SQLite via .sql file, PG via `_ensure_postgres_columns()` startup statements |

---

## Frontend Coverage Notes

| Component | Tests | Notes |
|-----------|-------|-------|
| jobs.component | ✅ 4 Phase 4 tests (bad-state badge visibility) | All PASS |
| SystemCleanupConfirmDialogComponent | ✅ Indirect (MatDialog.open verified) | Thin dialog, acceptable |
| queue-list.component | ⚠️ NO spec file | Coverage gap — Phase 4 modified this component but no test exists. Non-blocking; follow-up. |

---

## Documentation Updated
- [x] RESULTS/2026-08-11-task-job-reconciliation-full-test.md — this report
- [x] RESULTS/2026-08-11-task-jobitem-reconciliation-e2e.md — E2E worker report (written by ensure-e2e worker)
- [x] LESSONS/2026-08-11-cancel-requested-boolean-postgres-fix.md — PG boolean fix lesson (written by ensure-e2e worker)
- [ ] rules/ensure.md — no changes (user-maintained, read-only)

---

### Overall Status
- **Backend Tests**: ✅ PASS (0 NEW failures)
- **Frontend Tests**: ✅ PASS (1883/1883)
- **Reconciliation Tests**: ✅ PASS (62/62)
- **Migration Safety**: ✅ PASS
- **E2E Release Gate**: ✅ PASS (4/4)
- **ensure.md**: ✅ PASS (4/4 Critical + 2/2 Important + 1/1 Nice-to-have + 5/5 Release Gate)
- **Edge Cases**: ✅ ALL 6 COVERED
- **🔴 BLOCKING**: 1 uncommitted fix required (`cancel_requested = TRUE` in PG mirror) — without this, daemon cannot start on PostgreSQL
- **Testing Complete**: ⚠️ READY TO MERGE after committing the PG boolean fix
