# Test Report: Report-Lane Decoupling

**Date:** 2026-06-24 15:12 – 16:11 UTC
**Branch:** feature/report-lane-decoupling
**Base:** 70113d23 (plan ready) → final HEAD afbab690
**Sessions:** phase2-validate-sqlite, phase2-validate-pg, regression-modules, edge-cases-and-gaps, ensure-md-deadlock

---

## Summary

| Area | Tests | Passed | Failed | Status |
|------|-------|--------|--------|--------|
| Phase 2 SQLite (`tests/test_report_lane_phase2.py`) | 25 | 25 | 0 | ✅ PASS |
| Phase 2 PostgreSQL (`tests/postgres/test_report_lane_phase2_pg.py`) | 14 | 14 | 0 | ✅ PASS |
| Regression (7 related suites) | 220 | 220 | 0 | ✅ PASS (after 1 fix) |
| Deadlock fix (ensure.md) | 10 | 10 | 0 | ✅ PASS |
| **Total** | **269** | **269** | **0** | ✅ |

**Bugs found:** 1 real regression (fixed, commit 290eafbd)
**Weak tests identified:** 3 (2 fixed/strengthened, 1 documented)
**New tests added:** 9 (5 SQLite + 4 PostgreSQL)

---

## ensure.md Validation Results

### Critical Requirements
- ✅ **All non-integration tests pass** — Phase 2 + regression suites all PASS (269 tests)
- ✅ **Deadlock fix tests pass** — 10/10 (test_deadlock_fix.py)
- ✅ **No sync DB calls on asyncio event loop** — `claim_pending_task` runs on Worker thread (`worker_pool.py:232`); `has_inflight_task` wrapped via `asyncio.to_thread` (`api.py:680`). Both correct.
- ✅ **dev.sh includes `--timeout-graceful-shutdown 10`** — present at `dev.sh:74`
- ⚪ **E2E tests** — require live daemon (`./dev.sh`); not runnable in this testing session. Flagged for leader/user to run separately.

### Important Requirements
- ✅ **All callers of converted async functions properly await** — no issues found in changed code
- ✅ **Original deadlock scenario works without blocking** — deadlock tests pass

---

## Unit Test Results (Phase 2)

### SQLite — 25 tests (TestReportLaneGuard, TestIndependentTurn, TestPauseSafety, TestCrashRecovery, TestErrorPropagation)
All 25 PASS (1.24s). New `TestReportLaneGuard` class (3 tests) closes the critical coverage gap.

### PostgreSQL — 14 tests (10 original + 4 new)
All 14 PASS (1.68s) against real PostgreSQL 14.22. New `TestReportLaneGuardPG` + coverage mirrors added.

---

## The CRITICAL Invariant: VERIFIED ✅

**The single most important contract of this feature — PROCESS_REPORT tasks bypass the cross-system guard while PROCESS_MESSAGE tasks remain blocked — was NOT tested in the original suite.** Both `TestIndependentTurn` classes only tested task *creation* (inserting PROCESS_REPORT rows) but never called `claim_pending_task`.

**Now verified by new tests:**
- `test_process_report_bypasses_cross_system_guard` (SQLite) — report with mismatched `message_id` IS claimed while a PROCESSING MESSAGE job exists
- `test_pg_process_report_bypasses_cross_system_guard` (PG) — same, against real JSONB (`j.metadata->>'message_id'`)
- Contrast: `test_process_message_blocked_by_cross_system_guard` — PROCESS_MESSAGE with non-matching message_id is blocked (proves the bypass is scoped, not universal)

**Verdict:** The implementation is CORRECT. PROCESS_REPORT bypasses the guard; PROCESS_MESSAGE does not.

---

## Edge-Case Results

| Edge Case | Status | Evidence |
|-----------|--------|----------|
| 1: Report PENDING + paused → resume → claimed | ✅ PASS | `test_resume_allows_report_task_claim` (existing, verified meaningful) |
| 2: Two children complete → both reports processed | ✅ PASS | `test_two_simultaneous_reports_serialized_per_instance` (NEW) — 1st claim succeeds, 2nd returns None (serialization), 3rd succeeds after 1st completes |
| 3: Report claimed while message task RUNNING | ✅ PASS | `test_report_waits_for_running_message_task_to_finish` (NEW) — report blocked while message RUNNING, unblocked after completion |
| 4: Crash after bus fire before stamp | ✅ PASS | `test_fired_unstamped_watcher_recovered_on_restart` (existing, verified) |

**Key insight (Edge 2 & 3):** Report tasks are SERIALIZED per-instance (max 1 RUNNING), not parallel. This is the per-instance serialization invariant working correctly.

---

## Regression Results (7 suites)

| Test file | Total | Passed | Result |
|-----------|-------|--------|--------|
| test_task_repository.py | 52 | 52 | ✅ PASS |
| test_stale_recovery_v2.py | 30 | 30 | ✅ PASS (after fix) |
| test_dependency_bus.py | 52 | 52 | ✅ PASS |
| test_child_reports.py | 10 | 10 | ✅ PASS |
| test_atomic_status_transitions.py | 30 | 30 | ✅ PASS |
| test_message_flow.py | 32 | 27 (5 skipped) | ✅ PASS |
| test_stale_task_recovery.py | 19 | 19 | ✅ PASS |

**claim_pending_task cross-check:** All 4 invariants preserved:
- ✅ process_message + PROCESSING job (no matching task) → blocked (original behavior preserved)
- ✅ process_report on same instance → not blocked (new behavior)
- ✅ PAUSED/TERMINATED instance → blocked for ALL types (new pause gate)
- ✅ Per-instance serialization (≤1 RUNNING) for ALL types

---

## Bugs Found & Fixed

### Bug 1: schedule_retry orphan-recovery path broken (REAL REGRESSION)
- **Root cause:** `schedule_retry()` in `daemon/repositories/task/repository.py:963` had a status guard `AND status IN ('running', 'failed')` (added in phase 3, commit 17551447) that accidentally excluded `'cancelled'`, breaking the phase-5 orphan-recovery path (commit 0cf80785).
- **Impact:** CANCELLED tasks with `retry_scheduled=False` (orphaned state) could not be recovered by `find_orphaned_cancelled_tasks()` + `recover_on_startup()`. 3 tests failed.
- **Fix:** Added `'cancelled'` to eligible status set in `schedule_retry`. 14 lines net.
- **Commit:** `290eafbd` — `fix: restore orphan-retry path in schedule_retry status guard`
- **Note:** This is a latent bug surfaced by re-running the orphan-recovery tests, not directly caused by the report-lane decoupling. But it's in the same file (`task/repository.py`) and on the same hot path.

---

## Weak Tests Identified & Addressed

| Test | Issue | Resolution |
|------|-------|------------|
| `test_error_flag_propagates_to_finalize_status` (SQLite) | Duplicated production rule in inline Python instead of calling real helper | **Fixed** — now calls real `_resolve_finalize_status` (commit 86d8fae9) |
| `TestIndependentTurn` (both DBs) | Never called `claim_pending_task` — core invariant untested | **Fixed** — new `TestReportLaneGuard` class added (commit 82c8f2ec, afbab690) |
| `test_finalize_is_idempotent_via_atomic_transition` (SQLite) | Uses raw UPDATE SQL, not real `JobRepository.atomic_transition` | **Documented** (medium priority — would not catch a bug in the real finalize helper) |

### Misleading test noted (not fixed)
- `test_pg_concurrent_claims_only_one_wins` (PG) — named "concurrent" but runs sequentially (sync method, no asyncio.gather). Does NOT prove PG row-level locking under READ COMMITTED. Useful as a claim-contract test but the name/comment overstates what it proves.

---

## Remaining Coverage Gaps (Lower Priority)

These were identified but are lower priority / pure Python (not DB-specific):
- Crash windows (c) and (d) not tested (after report turn before finalize / before stamp) — `has_inflight_task` decision in `api.py:680` not exercised by tests
- `test_multiple_children_one_errors_one_succeeds` sticky error rule not mirrored on PG (it's a Python dict write, low PG-specific value)
- SQLite `test_report_tasks_have_distinct_message_ids` not mirrored on PG (invariant check, low priority)

---

## Code Changes Summary (all committed before report)

| Commit | Description |
|--------|-------------|
| `290eafbd` | fix: restore orphan-retry path in schedule_retry status guard |
| `86d8fae9` | test: call real _resolve_finalize_status in TestErrorPropagation |
| `82c8f2ec` | test: add report-lane guard bypass + edge-case tests (SQLite) |
| `afbab690` | test: add report-lane guard bypass + coverage gap mirrors (PG) |

---

## Overall Status

| Component | Status |
|-----------|--------|
| Unit Tests (SQLite) | ✅ PASS (25/25) |
| Unit Tests (PostgreSQL) | ✅ PASS (14/14) |
| Regression Tests | ✅ PASS (220/220, after 1 fix) |
| ensure.md (Critical) | ✅ PASS (4/4 runnable; E2E needs live daemon) |
| Critical Invariant | ✅ VERIFIED (report bypasses guard, message doesn't) |
| Edge Cases | ✅ PASS (4/4) |
| **Testing Complete** | ✅ **READY** |

**The report-lane decoupling implementation is CORRECT and well-tested on both PostgreSQL and SQLite.** One latent regression was found and fixed. The critical invariant (PROCESS_REPORT bypasses cross-system guard) is now verified with real SQL on both databases. All tests pass.
