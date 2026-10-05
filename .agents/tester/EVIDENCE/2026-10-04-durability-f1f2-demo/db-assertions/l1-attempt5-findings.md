# L1 Attempt 5 — F-2 Wedge Caught (F-1 wedge variant), Watchdog Recovery

**Result:** MIXED — wedge caught, parent healed via WaitingChildrenWatchdog (not lane 2)

## Setup
- Parent: f165c8f1-9d16-4771-9ec1-e826cf5097c4 (tester)
- Child: f539e51c-4406-4225-9494-cfc540b5bff3 (worker, slept 25s)
- Wedge caught at: i=8777 t=34838ms (after ~30s of polling)
- Condition: child=completed, parent=waiting_children, wake_msg=ready, wake_task=running

## What happened
The poller caught the wedge with wake_msg in 'ready' state (pre-processing).
The message_queue row was wiped by the boot (QUEUE_DISCARD_ON_STARTUP=true).
The report_injections row survived with state='TASK_DELIVERED' (created at 00:38:23, kill at 00:38:38).

## Lane 2 behavior at boot
- Lane 2 ran at 00:38:49 ("RDRS lane 2: post-wipe recovery")
- Lane 2 found delivery evidence (report_injections row with state='TASK_DELIVERED')
- Lane 2 SKIPPED — anchor exists (the report_injections row)
- NO per-row "sweep no_row_backstop" log

## Recovery mechanism
- The WaitingChildrenWatchdog detected the wedge at 00:38:50
- It enqueued a "system:watchdog:wedge" message to the parent
- The parent processed the wedge notice and synthesized the child's word
- Parent LLM response: "Child completed and reported: `helloL1` — exactly as specified."

## L1 PASS criteria assessment
- a. Parent left waiting_children (→ completed) ✓; reply synthesizes "helloL1" ✓
- b. ZERO manual pings: no API messages between t_kill and heal (need to verify)
- c. EXACTLY ONE internal_report:<child_iid> injection: FAIL — 0 internal_report rows in message_queue (wiped + not re-created)
- d. Child NOT re-executed: child has 1 task row (process_message, completed); checkpoint unchanged
- e. Lane-2 sweep/recovery marker present: ✓ (RDRS lane 2 log at boot)

## Root cause analysis
The F-2 lane 2 recovery is designed for the case where the wake was NOT yet created
or NOT yet delivered (no report_injections row, or state != 'TASK_DELIVERED').
In my test, the report_injections row was already created with state='TASK_DELIVERED'
(15 seconds before the kill). Lane 2 saw the delivery evidence and skipped.

The actual recovery was performed by the WaitingChildrenWatchdog, which detected
the wedge (parent in waiting_children, no live carrier, zero non-terminal children)
and enqueued a wedge notice message to wake the parent.

## Implication
The F-2 lane 2 wedge as defined in the task spec (wake NOT yet claimed) is
extremely narrow in a dev environment — the report_injections row is created
within ~70ms of the wake task creation. Catching the pre-delivery window
requires <10ms polling, which is at the edge of what's achievable.

The WaitingChildrenWatchdog provides a fallback recovery mechanism that handles
the wedge even when lane 2 doesn't fire. This is a layered defense.
