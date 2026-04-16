# Job System Improvements — Approval Tracking

## Iteration 001 — REJECTED

**Date:** 2026-04-09
**Verdict:** REJECTED

### Blocking Issues

1. **Non-atomic state transitions** (HIGH)
   - All concurrent paths (timeout, callback, cancel, recovery) modify job state via read-then-write pattern
   - State machine validation operates on stale data between get() and update()
   - Must apply single-session atomic pattern (like start_job_atomic()) to ALL transitions
   - Affected: Phase 1 (complete_job, fail_job, cancel_job), Phase 2 (timeout fallback), Phase 3 (retry transitions)

2. **NULL timeout allows forever-running jobs** (HIGH)
   - JobSystemConfig.default_job_timeout_minutes is Optional[int] — all three cascade levels can be None
   - Timeout monitor query skips jobs where max_duration_seconds IS NULL
   - Must make default_job_timeout_minutes non-optional with hard 60-minute default

3. **Non-atomic auto-retry flow** (MEDIUM)
   - Retry sequence: mark FAILED (commit) → calculate backoff → update PENDING (commit)
   - Crash between commits leaves FAILED with retry_count++ but next_retry_at=NULL
   - RetryScheduler won't pick it up (queries WHERE next_retry_at <= now())
   - Must make entire FAILED→PENDING transition atomic with pre-calculated retry metadata

4. **move_to_dlq() atomicity unspecified** (MEDIUM)
   - DLQ replay has atomic sketch (single transaction)
   - DLQ enqueue (FAILED→DEAD_LETTER) is two separate operations
   - Crash between insert DLQ row and update job status = inconsistent state
   - Must use shared session for both operations in single transaction

### Notes (Non-blocking)

- Phase 3 coupling description contradicts Phase Index dependency claim
- Missing job_retry_engine.py and dead_letter_service.py from plan-overview Files Affected summary
- SQLite busy_timeout should be specified (5000ms+)
- DeadLetterItem.job_id deletion guard needed
- Migration DOWN strategy needs specifics for older SQLite

## Iteration 002 — APPROVED

**Date:** 2026-04-15
**Verdict:** APPROVED
**Reviewer:** approver (independent evaluation with council)

### Previous Issues Resolution

All 4 blocking issues from iteration 001 have been adequately addressed:

1. **Non-atomic transitions** → ADR-008 added documenting the atomic transition principle. All state transitions now specify `UPDATE…WHERE status=?` + rowcount check pattern. `atomic_transition()` method specified in Phase 1 Task 1.3. Applied consistently across all phases.

2. **NULL timeout** → Phase 2 Task 4.1 now mandates a concrete 60-minute default through the resolution chain (explicit param → queue default → config default). The fallback chain ALWAYS produces a value. `-1` escape hatch requires deliberate action.

3. **Non-atomic auto-retry** → Phase 3 Task 2.4 specifies single atomic transaction: `atomic_transition(FAILED→PENDING, retry_count+=1, next_retry_at=calculated)` in one UPDATE statement. Sequence diagram confirms single session scope.

4. **move_to_dlq() atomicity** → Phase 3 Task 1.4 explicitly wraps INSERT + UPDATE in single SQLite session with rowcount verification and rollback on failure.

### Evaluation Details

Three independent council sessions verified:

**Session 1 (Atomicity & Safety):**
- Auto-retry single-transaction: design is correct, achievable via dedicated atomic method
- DLQ move with rollback: correct pattern, job stays FAILED on rollback
- Timeout race handling: idempotent transitions handle both race orderings correctly
- DLQ replay code sketch: uses read-then-write in illustration (see notes below)
- Instance liveness: acceptable for single-process daemon architecture

**Session 2 (Completeness & Feasibility):**
- All 8 gaps have traceable tasks ✅
- State machine consistent between plan-overview and phase1-plan ✅
- ADRs align with implementation tasks ✅
- Phase coupling assessment matches actual dependencies ✅
- No scope creep detected ✅
- SQLite busy_timeout already configured (30000ms) in factory.py ✅

**Session 3 (Database Correctness):**
- Migration: ALTER TABLE, partial unique index, DROP COLUMN all valid for SQLite 3.51.3 ✅
- rowcount: works with SQLAlchemy Core update() expressions ✅
- Heartbeat contention: ~0.67 writes/sec — trivial ✅
- Datetime queries: valid SQLite syntax ✅
- Multi-table transactions: achievable via single-method pattern ✅

### Notes (Non-blocking)

- DLQ replay code sketch in Phase 3 Task 4.3 (W5 fix note) shows `session.get()` + mutate pattern instead of atomic UPDATE...WHERE. The task description itself correctly requires atomic behavior — the code sketch is an illustration, not the specification. Implementer should follow the task description and ADR-008.
- Heartbeat asyncio.Task exception handling not explicitly specified — should follow existing background loop pattern (try/except + logger.exception, continue loop).
- RetryScheduler error handling not explicitly specified — should follow job_processor.py pattern.
- `is_instance_alive()` implementation is straightforward for single-process architecture (instances dict is empty on restart → all PROCESSING jobs are orphaned).

## Iteration 003 — APPROVED

**Date:** 2026-04-19
**Verdict:** APPROVED
**Reviewer:** approver (independent evaluation with council)

### Evaluation Summary

Independent evaluation from scratch with one council session. Council raised 5 concerns; all independently assessed as non-blocking:

1. **Observer blind spot for pre-instance failures** — NOT a blocker. JobProcessor calls complete_job() directly for pre-instance failures. Observer only handles post-instance completions. Two-path architecture is correct.
2. **FAILED→CANCELLED race with retry engine** — NOT a blocker. StaleTaskRecovery operates on tasks, not jobs. No existing job-level retry engine. Plan's double transition is safe.
3. **Async observer not atomic** — NOT a blocker. terminate_instance() calls complete_job_sync() synchronously before yielding. Plan's race analysis correct.
4. **Instance never starts** — NOT a blocker. Task-level StaleTaskRecovery + TimeoutMonitor handle this. Startup recovery catches remaining orphans. ADR-009 correct.
5. **Lock release ordering** — Known existing issue, addressed by Phase 1's atomic_transition() integration.

### Previous Issues (Iteration 001) — Still Resolved

All 4 blocking issues remain adequately addressed in the current plan text.

### Notes (Non-blocking)

- Phase 3 retry integration with cancellation: cancel_job() should skip maybe_retry() and go straight CANCELLED. Plan implies this but doesn't explicitly state it.
- DLQ replay code sketch uses read-then-write illustration (noted in iteration 002) — task description itself correctly requires atomic behavior.
