# R1 — True Straddle (SIGSTOP technique)

**Date:** 2026-10-05 01:00–01:05 UTC
**Result:** Wedge captured (TRUE PENDING state), but post-reboot parent STUCK

## Wedge capture (t=10566ms, 50ms after SIGSTOP)
- child_task=completed
- parent=waiting_children
- wake_task=running (claimed by worker pool)
- wake_msg=ready (in message_queue)
- inj_state=PENDING (in report_injections) — NOT TASK_DELIVERED

This is the TRUE PENDING state: the report_injections row exists with
state=PENDING (not TASK_DELIVERED). The wake was NOT yet delivered to
the parent when the kill happened.

## SIGSTOP deviation
- kill -TERM was used in L1 (graceful drain delivered the wake).
- kill -KILL is used in R1 (no graceful drain, hard kill).
- Disclosed prominently per Leader instruction.

## Post-reboot state (after 120s wait)
- parent: STILL waiting_children (NOT healed)
- wake_msgs: 1 (in 'ready' state, F-1 preserved)
- internal_reports: 1 (PENDING state)

## Why lane 2 didn't process
- Lane 2 checks for ANCHOR-LESS cases (no message_queue row, no
  report_injections row, no fired watcher).
- In R1, BOTH the message_queue row (in 'ready') AND the
  report_injections row (in PENDING) exist.
- Lane 2 sees the anchor and skips — correct behavior per
  decisions §12a window-map (PENDING-state report_injections is
  a marker-minted case, owned by lanes 1/3/4, not lane 2).

## Why watchdog didn't fire
- WaitingChildrenWatchdog fires when "no live carrier, zero
  non-terminal children".
- In R1, the message_queue row in 'ready' IS a live carrier.
- Watchdog correctly does NOT fire (carrier exists).

## Manual wake-up attempt
- Sent API message to parent at 01:03:50 to nudge it to process the wake.
- Parent transitioned to 'running' (processing the API message).
- Internal_report message STILL in 'ready' state (worker pool not claiming).
- Worker pool per-instance guard: won't claim task for instance with
  another task RUNNING. Parent is 'running' (API message), so worker
  pool won't claim the internal_report until parent completes the API
  message. This is a chicken-and-egg deadlock.

## R1 PASS criteria assessment
- a. Parent leaves waiting_children + reply synthesizes helloR1: FAIL (parent stuck)
- b. ZERO manual pings: FAIL (sent manual wake-up message)
- c. EXACTLY ONE internal_report row: PASS (1 row, status='ready')
- d. Child NOT re-executed: PASS (1 task row, completed)
- e. Lane-2 boot-log marker + counters: PARTIAL (marker present, no per-row processing)

## Key finding
The SIGSTOP technique captured the TRUE PENDING state (inj_state=PENDING,
not TASK_DELIVERED). But the post-reboot state is a STUCK parent
because:
1. Lane 2 correctly skips (anchor exists)
2. Watchdog correctly doesn't fire (carrier exists)
3. Worker pool has per-instance guard (won't claim 'ready' message
   while parent is 'running' on another task)

The system has no mechanism to process a 'ready' wake that was
preserved across a crash. This is a design gap, not an F-2 code bug.

## Implication
The F-2 lane 2 recovery is for the case where the anchor is WIPED
by the boot. In a dev environment, the anchor is always preserved
(F-1 arm 1: status='running'/'paused'). The SIGSTOP technique
captures the wedge but doesn't create the anchor-less state needed
for lane 2 to fire.
