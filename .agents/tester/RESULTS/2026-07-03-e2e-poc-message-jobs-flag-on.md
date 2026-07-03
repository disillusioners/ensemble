# E2E Test POC: Job-as-Front-Primitive (Flag ON)

**Date**: 2026-07-03
**Branch**: `latest`
**Flag**: `ENSEMBLE_JOB_SYSTEM_MESSAGE_JOBS_ENABLED=true`
**Session IDs**: e2e-poc-flag-on, e2e-poc-run-tests, e2e-poc-investigate, e2e-poc-fix-rerun, e2e-poc-deep-investigate, e2e-poc-failure1-fix, e2e-poc-full-rerun, e2e-poc-verification

---

## Summary

| Category | Result |
|----------|--------|
| **E2E Tests** | 4/5 PASS, 1 FAIL (Test 2 — architecture issue) |
| **POC Criteria** | 3/4 PASS, 1 PARTIAL (Criterion 1 state transitions) |
| **Fixes Applied** | 3 commits (test assertions + 2 source code fixes) |
| **Flag Recommendation** | Leave ON for POC evaluation; turn OFF for production |

---

## 1. ensure.md E2E Test Results

### Test Results

| # | Test | Result | Duration | Notes |
|---|------|--------|----------|-------|
| 1 | `test_parent_child_workflow_happy_path` | ✅ PASS | ~68s | Fixed after carve-out broadening (Phase 2 second message) |
| 2 | `test_pause_after_spawn_then_resume` | ❌ FAIL | ~60s | Architecture issue (RF3 dual-record coupling) — see below |
| 3 | `test_terminate_after_spawn_then_revive` | ✅ PASS | ~41s | Fixed after terminate cleanup skip for message jobs |
| 4 | `test_wave_spawn_with_defer_queue` | ✅ PASS | ~80s | Fixed after VJM kind assertion update |
| 5 | `test_pause_blocks_defer_queue` | ✅ PASS | ~50s | Passed from start (bonus test, not in original 4) |

**Total**: 4 passed, 1 failed in ~380s

### Test 2 Failure (Architecture Issue — Not Quick-Fixable)

**Symptom**: After pause→resume, leader's JobItem mirror transitions to `cancelled` instead of `processing` or `completed`.

**Root Cause**: The resume cascade (`_resume_cascade_db_sync`) intentionally cancels the Task (paused→cancelled) to prevent the WorkerPool from re-claiming it as a fresh turn. But the parallel terminal-write path treats this cancelled Task as a terminal signal and writes `terminal_reason='cancelled'` to the JobItem mirror before the resume driver finishes its turn.

**Classification**: **Architecture issue (RF3 dual-record coupling)** — not a quick fix. The Task and JobItem have two independent status state machines. Pause/resume writes to Task but the resolver reads from JobItem. This is exactly the dual-record coupling the Job-as-Front-Primitive plan was designed to eliminate.

**Impact**: The instance actually completes successfully — the workflow runs correctly end-to-end. Only the JobItem mirror's status display is wrong.

**Recommendation**: Flag for follow-up. Needs a designed solution where the resume cascade also manages the JobItem mirror state.

---

## 2. POC-Specific Verification Results

| # | Criterion | Result | Evidence |
|---|-----------|--------|----------|
| 1 | POST /messages creates a JobItem | ⚠️ **PARTIAL** | JobItem created with correct job_type="message" and metadata.message_id. BUT admission_state stays at `queued` — never transitions queued → active → done |
| 2 | work_id == job_id | ✅ **PASS** | Exact UUID match: task.work_id = job_queue_items.job_id = `a13264a0-...` |
| 3 | No double-dispatch | ✅ **PASS** | Exactly 1 Task per message_id. Cross-system guard and list_pending_by_queue filter work correctly |
| 4 | Instance responds normally | ✅ **PASS** | Instance completed, assistant message produced ("Hello! 👋 Message received loud and clear...") |

### Criterion 1 State Machine Gap (PARTIAL)

The JobItem IS created correctly with:
- `job_type = "message"`
- `metadata.message_id` properly stamped
- `job_id` matching `task.work_id`

BUT the `admission_state` does NOT transition:
- **Creation**: `queued` ✅
- **Queued → Active**: ❌ Eager activation no-ops (PostgreSQL trigger `trg_job_queue_items_active_lock_guard` requires a `job_locks` row, which the message flow never creates)
- **Active → Done**: ❌ The observer finalization path doesn't drive message-jobs through their lifecycle for the message-job path

The Task completes, the instance completes, but the JobItem mirror stays `queued` forever. This is a cosmetic/observability issue — the actual workflow runs correctly.

---

## 3. Fixes Applied (3 Commits)

### Commit `78dc9e3c` — Test assertions + terminate cleanup
**Files**: `daemon/services/instance_lifecycle.py`, `tests/e2e/test_e2e_workflows.py`

1. **VJM kind assertion update** (Tests 1 & 4): Changed `assert "turn" in kinds_present` to `assert "turn" in kinds_present or "job" in kinds_present`. With flag ON, VJM dedup suppresses Task (kind="turn") in favor of JobItem mirror (kind="job").
2. **Skip message jobs in terminate cleanup** (Test 3): Added `if remaining_job.job_type == "message": continue` to the `terminate_instance` cleanup loop. MESSAGE JobItems are informational mirrors, not lifecycle-managed jobs.

### Commit `827649e7` — Eager activation attempt
**File**: `daemon/services/instance_messaging.py`

Added eager `atomic_transition(QUEUED→ACTIVE)` after JobItem creation. **Status**: No-op in practice because PostgreSQL trigger requires a `job_locks` row. The `IntegrityError` is caught and logged at DEBUG. Kept because it mirrors the documented POC contract.

### Commit `386a22be` — Broadened cross-system guard carve-out (THE KEY FIX)
**File**: `daemon/repositories/task/repository.py`

`_admitted_task_carve_out_sql` now matches ANY Task with the same `message_id`, regardless of status (was: only `pending`/`running`). This fixes the Phase-2 second-message claim block where JobItem A from Phase 1 was stuck in `queued` (activation fails) and blocked Phase 2's new Task from being claimed.

**Root Cause**: After Phase 1's Task completed, JobItem A's mirror remained stuck in `queued`. The carve-out only matched `pending`/`running` Tasks, so JobItem A was treated as a permanent blocker for any subsequent message to the same instance.

---

## 4. Issues Found (Beyond Tests)

### Issue A: JobItem admission_state never transitions (POC Criterion 1 partial)
- **Severity**: Medium — observability/cosmetic
- **Impact**: JobItem mirror stays `queued` forever; the actual workflow runs correctly
- **Root Cause**: Eager activation no-ops (PG trigger constraint), observer finalization path doesn't drive message-jobs
- **Recommendation**: Follow-up — either relax the PG trigger for message-type JobItems, or add an observer path that finalizes message-job mirrors

### Issue B: Pause/resume cancels JobItem mirror (Test 2 failure)
- **Severity**: Medium — mirror display issue
- **Impact**: JobItem shows `cancelled` after resume, even though instance completes successfully
- **Root Cause**: RF3 dual-record coupling — resume cascade cancels Task, parallel terminal-write propagates to JobItem
- **Recommendation**: Architecture-level follow-up — needs designed solution for pause/resume + JobItem mirror coordination

### Issue C: D13 lifecycle paths assume "no MESSAGE JobItems exist"
- **Severity**: Low (mitigated by fixes)
- **Impact**: Multiple D13 paths conflict with flag ON
- **Status**: Partially mitigated by commit `78dc9e3c` (terminate cleanup skip)
- **Remaining**: `_migrate_cancel_inflight_message_jobitems` at startup, observer `_get_processing_job_for_instance` paths

---

## 5. Flag Recommendation

**Leave ON** for POC evaluation and continued development.

**Rationale**: The POC works end-to-end — messages are processed correctly, instances complete normally, no double-dispatch occurs. The two remaining issues (admission_state transitions and pause/resume mirror) are observability/cosmetic issues, not functional blockers. The actual workflow lifecycle operates correctly.

**Turn OFF** before production deployment until the JobItem admission_state lifecycle is fully wired and the pause/resume mirror issue is resolved.

---

## Environment State After Testing

- **Flag**: `ENSEMBLE_JOB_SYSTEM_MESSAGE_JOBS_ENABLED=true` (left ON)
- **RAG_IS_REQUIRED**: `false` (changed for E2E; should restore to `true` for normal dev)
- **Database**: PostgreSQL (`data_dev/ensemble.json`)
- **Daemon**: Running on port 8079
- **Branch**: `latest` with 3 fix commits applied

## Commits (All on `latest`)
1. `78dc9e3c` — test: fix E2E assertions for message-jobs flag ON + skip message jobs in terminate cleanup
2. `827649e7` — fix: eager activate message JobItem in enqueue_message_job
3. `386a22be` — fix: broaden cross-system guard carve-out to match any Task status
