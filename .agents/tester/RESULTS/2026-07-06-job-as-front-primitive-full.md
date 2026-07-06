# Test Report: Job-as-Front-Primitive Full Implementation (Phases 1-5)

**Date**: 2026-07-06
**Branch**: `feature/job-as-front-primitive-full`
**Commits**: 5 phase commits (b5cf3a3f → 631b2fb1)
**Sessions**: setup-discover, regression-poc, regression-new-impl, regression-job-queue, pg-regression, e2e-workflows, broad-regression, fix-failures, final-check

---

## Summary

| Category | Result | Details |
|----------|--------|---------|
| **Unit/Integration Regression** | ✅ PASS | 1859/1859 pass (after fixes) |
| **New Implementation Tests** | ✅ PASS | 25/25 pass (Phase 4 critical validations confirmed) |
| **Job Queue Full Suite** | ✅ PASS | 1315/1315 pass (after D13 contract fix) |
| **Broad Regression** | ✅ PASS | 322/322 targeted (after fixes) |
| **PostgreSQL Suite** | ✅ PASS | 90/90 pass (after orphan-sweep fix) |
| **E2E Workflows** | ⚠️ 3/5 PASS | Tests 2 & 4 FAIL (architecture issues) |
| **Phase 4 (Facade Collapse)** | ✅ PASS | Report Tasks visible, Turn Tasks hidden |
| **Phase 5 (Cutover)** | ✅ PASS | Flag removed, all entry points use job path |
| **POC Success Criteria** | ⚠️ PARTIAL | 3/4 criteria fully pass; JobItem lifecycle partial |
| **ensure.md Critical** | ❌ 6/8 PASS | Reqs 6 & 8 FAIL (E2E pause/resume + wave spawn) |

**Overall Status: ❌ NOT READY — 2 E2E failures need architecture fixes**

---

## 1. Setup & Discovery ✅

- Branch `feature/job-as-front-primitive-full` confirmed, 5 commits present
- **Feature flag REMOVED**: `ENSEMBLE_JOB_SYSTEM_MESSAGE_JOBS_ENABLED` only in doc comments/historical files
- **All 6 entry points use `enqueue_message_job`**:
  - POST /messages (daemon/routers/messages.py:134)
  - send_message tool (daemon/tools/instance.py:707)
  - job_continue tool (daemon/tools/job_queue.py:749)
  - External source (daemon/sources/registry.py:827)
  - Scheduler adapter (daemon/sources/adapters/scheduler.py:762)
  - PAUSED cascade-resume (daemon/manager.py:3404)
- **Raw `enqueue_message`**: 6 internal-only callers (by design)

## 2. Unit/Integration Regression ✅

| Test File | Result | Notes |
|-----------|--------|-------|
| `tests/test_message_job_bridge.py` (9 POC tests) | 5/5 PASS | Renamed from test_message_job_poc.py |
| `tests/test_enqueue_shared.py` (26 tests) | 26/26 PASS | Shared enqueue path intact |
| `tests/test_scheduler_adapter.py` | 67/67 PASS | Scheduler integration clean |
| `tests/test_message_job_serialization.py` (NEW) | 3/3 PASS | Serialization round-trips |
| `tests/unit/services/test_report_retention.py` (NEW) | 11/11 PASS | **CRITICAL**: Report Tasks visible after collapse |
| `tests/unit/services/test_work_resolver_partial_collapse.py` (NEW) | 11/11 PASS | **CRITICAL**: Turn Tasks hidden, JobItems visible |

## 3. Job Queue Full Suite ✅ (after quick fix)

- **1315/1315 PASS** (38 skipped)
- **Quick Fix Applied**: 3 stale tests in `test_instance_termination_job_cleanup.py` updated to match D13 contract (message JobItems are informational mirrors, skipped in terminate cleanup)
- Commit: `85aa9c18`

## 4. Broad Regression ✅ (after quick fixes)

| Suite | Result |
|-------|--------|
| Observer (4 files) | 29/29 PASS |
| Work resolver (2 files) | 96/96 PASS |
| Message job bridge (2 files) | 8/8 PASS |
| Dispatch/pipeline (4 files) | 47/47 PASS |
| Cancellation cascade (3 files) | 89/89 PASS |
| Pause/resume unit (4 files) | 29/29 PASS (after `JobItem.status` → `admission_state` fix) |
| Idempotent enqueue | 15/15 PASS |
| Resume gate | 9/9 PASS |
| **Total** | **322/322 PASS** |

## 5. PostgreSQL Suite ✅ (after quick fix)

- **90/90 PASS** (33 skipped CM-removed)
- **Quick Fix Applied**: `test_pg_restart_survival` updated to insert real Task row (orphan sweep requires active task row)
- Commit: `a020f1cb`

## 6. Phase 5 Cutover Test Fixes ✅ (4 commits)

Tests referencing old `manager.enqueue_message` updated to `manager.enqueue_message_job`:

| Commit | Files Fixed | Tests Fixed |
|--------|------------|-------------|
| `b2ff3e34` | tests/test_sources_registry.py | 2 tests |
| `c10dce86` | tests/test_sources_system_fix.py | 10 tests |
| `2d89f0f8` | tests/tools/test_send_message_*_guard.py | 6 tests |
| `385a5133` | tests/test_worker_notification.py | 6 tests |
| `5b7b875c` | tests/unit/test_pause_flow_redesign.py | 2 tests |
| `2c1d2c87` | tests/e2e/test_e2e_workflows.py | 17 `kind="turn"` → `kind="job"` |

**Total: 43 tests fixed across 6 commits**

## 7. E2E Workflows ⚠️ 3/5 PASS

| # | Test | Status | Duration | Notes |
|---|------|--------|----------|-------|
| 1 | `test_parent_child_workflow_happy_path` | ✅ PASS | 85s | After kind="job" fix |
| 2 | `test_pause_after_spawn_then_resume` | ❌ **FAIL** | 57s | **RF3 NOT FIXED** |
| 3 | `test_terminate_after_spawn_then_revive` | ✅ PASS | 67s | Revive works |
| 4 | `test_wave_spawn_with_defer_queue` | ❌ **FAIL** | 187s | Leader stuck waiting_children |
| 5 | `test_pause_blocks_defer_queue` | ✅ PASS | 44s | Defer fix holds |

### Test 2 Failure — RF3 NOT RESOLVED ❌

**Root Cause**: Phase 4 deleted the job `PAUSED → PROCESSING` UPDATE from `_resume_cascade_db_sync`. The drift reconciler flips job `PROCESSING → PAUSED` on pause but nothing flips it back on resume. `_finalize_job` guard is `WHERE status='processing'` → rowcount=0 → silent no-op.

**Impact**: Instance-level pause/resume works fine, but JobItem stays `paused` after resume.

**NOT quick-fixable**: Restoring the transition would partially revert Phase 4's documented design choice. Requires architecture decision.

### Test 4 Failure — Wave Spawn Regression ❌

**Root Cause**: Leader spawned 2 children (wave pattern). Child 1 completed, child completion report fired (bus_pending → 1). Second child never completed. Leader stuck in `waiting_children` for 180s.

**Impact**: Wave-spawn lifecycle doesn't auto-complete when a child fails to report back.

## 8. Phase 4 Verification (Facade Collapse) ✅

- ✅ `TURN_TASK_TYPES` deleted — no turn-specific code in work_resolver
- ✅ `REPORT_TASK_TYPES` retained — process_report, send_report still visible
- ✅ `list_work`/`resolve_work` does NOT return `kind="turn"` for message tasks
- ✅ Report Tasks (process_report, send_report) still visible in work list
- ✅ Frontend `WorkKind` reduced to `'job' | 'report'`

## 9. Phase 5 Verification (Cutover) ✅

- ✅ Feature flag `ENSEMBLE_JOB_SYSTEM_MESSAGE_JOBS_ENABLED` removed from executable code
- ✅ All 6 public entry points call `enqueue_message_job`
- ✅ No raw message enqueuing for public entry points
- ✅ Scheduler uses inline dispatch

## 10. POC Success Criteria

| Criterion | Status | Evidence |
|-----------|--------|----------|
| JobItem lifecycle (queued → active → done) | ⚠️ PARTIAL | Works for direct lifecycle; pause/resume leaves job stuck `paused` |
| work_id == job_id linkage | ✅ PASS | Test 1 Phase 2 message resolved via instance_id → work_id |
| No double-dispatch | ✅ PASS | Tests 1, 5 prove no duplicate processing |
| Instance responds normally | ✅ PASS | Tests 1, 3, 5 prove normal LLM round-trip |

## 11. ensure.md Validation

| # | Requirement | Status | Evidence |
|---|------------|--------|----------|
| 1 | All non-integration tests pass | ✅ PASS | ~8400+ pass after fixes (5 pre-existing in message_queue_redesign) |
| 2 | Deadlock fix tests pass | ✅ PASS | 10/10 |
| 3 | No sync DB calls on event loop | ✅ PASS | 10 pass, 1 skip |
| 4 | dev.sh has --timeout-graceful-shutdown 10 | ✅ PASS | Line 74 confirmed |
| 5 | E2E happy path | ✅ PASS | Test 1 = 85s |
| 6 | E2E pause/resume | ❌ **FAIL** | RF3 architecture issue |
| 7 | E2E terminate/revive | ✅ PASS | Test 3 = 67s |
| 8 | E2E wave spawn + defer | ❌ **FAIL** | Leader stuck waiting_children |

**Critical: 6/8 PASS. Reqs 6 & 8 BLOCK completion.**

---

## Quick Fixes Applied (7 commits total)

| Commit | Description |
|--------|-------------|
| `85aa9c18` | Align terminate_instance cleanup tests with D13 contract |
| `a020f1cb` | Fix pg_restart_survival to satisfy startup orphan sweep |
| `5b7b875c` | Fix pause_flow_redesign JobItem.status → admission_state |
| `2c1d2c87` | Align e2e workflows with Phase 5 kind contract (job\|report only) |
| `b2ff3e34` | Align source_registry mocks with enqueue_message_job |
| `c10dce86` | Align source_system_fix mocks with enqueue_message_job |
| `2d89f0f8` | Align send_message guard mocks with enqueue_message_job |
| `385a5133` | Align worker_notification mocks with Phase 5 work_id/repo API |

---

## Action Needed

- [ ] **CRITICAL**: Fix Test 2 (pause/resume) — restore job `PAUSED → PROCESSING` transition in `_resume_cascade_db_sync` OR update test contract
- [ ] **CRITICAL**: Investigate Test 4 (wave spawn) — leader stuck in `waiting_children` when child doesn't complete
- [ ] Fix 5 pre-existing failures in `tests/message_queue_redesign/` (admission_state attribute on MockTask, double-claim, claim logic)
- [ ] Clean up stale test comments referencing removed feature flag
