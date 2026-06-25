# Pause/Resume Feature Redesign — Approval Tracking

## Iteration 001 — 2026-06-25 05:35 UTC

**Verdict: REJECTED**

### Blocking Issues

1. **CRITICAL — `job_state_machine.py` TRANSITIONS dict completely missing from plan**
   - Expected: The plan must update `daemon/services/job_state_machine.py` TRANSITIONS dict (lines 20-41) with `(PROCESSING, PAUSED)`, `(PAUSED, PROCESSING)`, `(PAUSED, CANCELLED)` entries
   - Found: Plan Phase 1 Tasks 3-4 point to `job_queue_service.py` and `repository.py` for transition updates — these are NOT the enforcement gate. The authoritative validation gate is `job_state_machine.validate_transition()` called by `atomic_transition()` at `repository.py:664`. Without updating TRANSITIONS, every pause/resume throws `InvalidTransitionError`
   - Verified: `repository.py:664` → `job_state_machine.validate_transition()` → checks TRANSITIONS dict. No plan file mentions `job_state_machine.py`

2. **HIGH — Paused-while-running-worker-pool-task scenario unaddressed**
   - Expected: Plan should describe how a RUNNING task (which has `worker_id` set — actively executing in a worker) is safely transitioned to PAUSED
   - Found: Plan Phase 2 Task 2 says "atomically transition tasks WHERE status = 'running' → 'paused'" but does NOT describe: (a) how the worker is notified, (b) how the worker handles finding its task is now PAUSED, (c) whether the worker's `CancelledError` handler properly leaves the task in PAUSED, (d) whether the worker releases its slot
   - Risk: Worker continues processing a PAUSED task and may complete it, creating inconsistent state

### Non-blocking Observations

- `_process_event` status-filter description is slightly oversimplified but the diagnosis is correct (no-op turn → no event → no finalize)
- Batched UPDATE for `job_queue_items` may encounter multiple rows per instance — needs explicit handling
- `_process_resume_finalize` should reuse existing `_finalize_job` path rather than reimplementing
- Claim 1 (no DB migration), Claim 3 (premature completion bug location), Claim 5 (cascade helpers only touch instances), Claim 6 (crash recovery treats PAUSED as alive) are all VERIFIED CORRECT

## Iteration 002 — 2026-06-25 05:58 UTC

**Verdict: APPROVED**

### Previous Issues (Iteration 001) — Resolution Check

1. **B1 (TRANSITIONS dict missing)** — RESOLVED. Phase 1 Task 3 now explicitly targets `daemon/services/job_state_machine.py:20-41` (TRANSITIONS dict) with pairs `(PROCESSING, PAUSED)`, `(PAUSED, PROCESSING)`, `(PAUSED, CANCELLED)`. Decision 1 includes "⚠️ Enforcement gate" note referencing `repository.py:664` → `validate_transition()`. Verified: `repository.py:664` calls `job_state_machine.validate_transition()` before UPDATE. TRANSITIONS dict at lines 20-41 is the authoritative gate.

2. **B2 (Worker-during-pause unaddressed)** — RESOLVED. Phase 2 Task 6 provides 4-part breakdown: (a) cooperative cancellation notification, (b) CancelledError handler re-raises without complete_task/complete_job, (c) finally-block PAUSED status re-check guard, (d) concurrency slot release via ExecutionGate unwind. Verified: `manager.py:2944-2991` complete_task block and `instance_messaging.py:1456` CancelledError handler both exist as referenced.

### Independent Verification (Fresh-Eye Check)

| Area | Verification | Result |
|------|-------------|--------|
| Cascade raw SQL bypasses `validate_transition` | `_pause_cascade_db_sync` (line 1802) and `_finalize_job_db_sync` (line ~1940) both use raw SQL with `WHERE status = 'processing'` guards — architecturally consistent existing pattern. Recovery path correctly uses `atomic_transition` (needs TRANSITIONS pairs — covered). | NON-BLOCKING — consistent with codebase patterns |
| Task-level state machine | `grep` for validate_transition/state_machine in `daemon/repositories/task/` returns empty. No gate to bypass. | NON-BLOCKING — no gate exists, raw SQL is the pattern |
| Double-finalize safety (C1) | `_finalize_job_db_sync` line ~1945 uses `.where(JobItem.status == JobStatus.PROCESSING.value)`, rowcount=0 → idempotent skip | VERIFIED CORRECT |
| Bus watcher recovery (C4) | `api.py:743-760` confirmed: `_get_processing_job_for_instance` returns None for PAUSED → `mark_enqueued` drops watcher. Fix (check instance status before stamping) is correct | VERIFIED CORRECT |
| Job recovery (C2) | `job_recovery_service.py:132-143` confirmed: PAUSED instances hit "alive" branch. Fix location correct | VERIFIED CORRECT |
| Plan internal consistency | Pause/resume transitions symmetric (PROCESSING↔PAUSED for jobs, RUNNING→PAUSED / PAUSED→PENDING for tasks). Decisions coherent. Phase dependencies logical. | CONSISTENT |
| Requirements completeness | All stated requirements (first-class PAUSED, eliminate premature completion, no zombie jobs, crash recovery, dual-driver support, 213+ test migration) addressed across 6 phases | COMPLETE |

### Non-blocking Observations (Notes for Implementation)

- **Cascade UPDATE multi-row note**: Phase 2 notes the batched `UPDATE job_queue_items` may hit multiple PROCESSING rows per instance. Plan acknowledges this and defers handling to existing zombie cleanup logic — verify during implementation that cleanup handles PAUSED jobs too (plan notes this at phase2-plan.md:96).
- **Compaction hook is defined but secondary**: `_compact_fired_watchers_for_paused` runs only on resume (not periodically). Future background sweeper noted as optional. Acceptable for initial redesign.
- **PAUSED → FAILED not supported** (Decision 10): Must resume first, then fail. This is a deliberate design choice — document clearly in implementation.

### Council Verification
Council session `approve-pause-resume-002` (4 independent verification tasks) returned: APPROVED with all concerns verified non-blocking. Raw-SQL bypass in cascade confirmed architecturally consistent with `_finalize_job_db_sync` pattern.
