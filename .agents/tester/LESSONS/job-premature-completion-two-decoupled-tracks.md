# Lesson: Job Premature Completion — Two Decoupled Completion Tracks

**Date:** 2026-06-20
**Type:** Bug investigation (no code changes made)
**Severity:** High — jobs reach terminal `completed` while children still running

## The Bug
A parent JOB transitions to `completed` while its child instances are still running.

## Root Cause
Two INDEPENDENT completion tracks that are not synchronized:

1. **Instance completion** (`daemon/services/child_reports.py`, `_process_child_completion_and_notify_parent`)
   - Tracks `waiting_for` counter (incremented on spawn+send, decremented on child report)
   - Defers instance completion when `waiting_for > 0`
   - This track WORKS CORRECTLY

2. **Job finalization** (`correlation_manager.py` + `job_feedback_observer.py`)
   - Tracks message resolutions keyed by `(child_id, message_id)`
   - When all KNOWN message resolutions are acked → fires `handle_correlation_complete` → `status=completed`
   - **NOT gated on instance `waiting_for == 0`**
   - This is where the bug lives

## Trigger: Multi-wave child spawning
The most common trigger is an agentic workflow where the parent spawns children in waves:
- Wave 1: spawn 2 children → `waiting_for=2`
- Wave 1 acks → CM sees all known resolutions complete → **job finalized `completed`**
- Wave 2: parent spawns MORE children (investigate → spawn fixer pattern)
- Wave 2 children run under an already-terminal job

## Key log markers to search for
```
CM correlation complete: parent=<id>, status=completed, had_error=False
Observer: finalized job <job_id>... status=completed... (released N lock(s), instance_was_terminal=False)
```
followed later by:
```
Instance <id>... completed message but waiting for N children (CM=True), deferring completion
CM callback: no active PROCESSING job for instance <id>..., skipping
```

The last line is the telltale signature: when the final child reports, there's no active processing job because it was prematurely finalized.

## Variant: job_continue + watch_job
Calling `job_continue` dispatches a child JOB (not a spawned child instance), so `waiting_for=0`. Parent finalizes `completed` while watched child job still runs.

## Affected files (for fix work)
- `daemon/services/correlation_manager.py` — message-resolution tracking
- `daemon/services/job_feedback_observer.py` — job finalization on CM callback
- `daemon/services/child_reports.py` — instance `waiting_for` deferral (works correctly, but decoupled)

## Investigation report
See `.agents/tester/RESULTS/2026-06-20-job-premature-completion-investigation.md`
