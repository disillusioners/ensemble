# Plan Tracking: Jober Agent — Job Orchestrator

## Iteration 001 — REJECTED

**Date**: 2026-04-25
**Verdict**: REJECTED
**Reviewer**: Approver (independent)

### Blocking Issues

1. **`dead_letter` missing from default `watch_events` (phase1-plan.md line 63)**
   - Expected: `["completed", "failed", "cancelled", "terminated", "dead_letter"]`
   - Found: `["completed", "failed", "cancelled", "terminated"]` — `dead_letter` absent
   - Impact: Jobs reaching DEAD_LETTER won't trigger watcher notifications by default, contradicting the plan's own success criteria ("When a watched job reaches a terminal state via ANY path")
   - Note: Line 107 terminal check DOES include `dead_letter` — internal inconsistency

2. **Missing terminal transition paths — DeadLetterService.move_to_dlq() and JobRetryEngine.maybe_retry() not hooked**
   - Expected: `notify_watchers()` called from ALL terminal transition paths
   - Found: Plan only identifies 4 paths (observer, cancel, complete, terminate) but there are at least 6. Missing:
     - `daemon/services/dead_letter_service.py` → `move_to_dlq()` (line 127, 198): directly sets `job.status = "dead_letter"`
     - `daemon/services/job_retry_engine.py` → `maybe_retry()` (line 233-235): calls `move_to_dlq()` when retries exhausted
   - Impact: Jobs that enter DEAD_LETTER (via dead letter service or retry exhaustion) will NOT trigger watcher notifications — watched jobs will hang forever in those scenarios
   - Fix: Add `notify_watchers()` call in `DeadLetterService.move_to_dlq()` and document these paths alongside the existing 4

### Non-blocking Notes

- Plan description says `AgentRegistry.discover()` uses "non-`_` prefix" pattern. Actual code uses explicit `SKIP_DIRS = frozenset({"_trash", "_baby_template"})`. Not a functional issue — `agents/jober/` will be discovered correctly.
- Plan is otherwise thorough: good risk analysis, solid edge case coverage, well-structured phases, correct message classification logic.


## Iteration 002 — REJECTED

**Date**: 2026-04-24
**Verdict**: REJECTED
**Reviewer**: Approver (independent)

### Blocking Issues

1. **Missed terminal path: `JobRecoveryService._fail_orphaned_job()` bypasses service layer**
   - Expected: `notify_watchers()` called from ALL terminal transition paths
   - Found: `daemon/services/job_recovery_service.py:149-179` calls `self._job_repository.atomic_transition(from_status="processing", to_status="failed")` **directly**, bypassing `JobQueueService.complete_job()` entirely. The plan's hook at Path 3 (`complete_job()`) will NOT catch this transition.
   - Impact: If a watched job is an orphan recovered on daemon startup, the watcher will never be notified. The watch will remain stale until TTL cleanup or manual intervention.
   - Fix: Add `notify_watchers()` call in `_fail_orphaned_job()` after `atomic_transition()`, or refactor to use `JobQueueService.complete_job()`.

### Non-blocking Notes

- `job_processor.py` has pre-existing API mismatch: 6 calls use `success=False` but signature expects `demand_state=DemandState`. Not a plan issue, but implementer should be aware.
- All iteration 001 issues properly addressed (dead_letter defaults + paths 5/6).
