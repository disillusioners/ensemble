# L1 Attempt 1 — F-2 Wedge Capture Analysis

**Result:** WRONG WEDGE CAUGHT (F-1 wedge, not F-2 wedge)

## What happened
- Parent: 3541055b-9c12-4e7a-bfed-d184f4efa9b4 (tester)
- Child: 2feb0f5f-66e3-45f3-b7cd-973576694737 (worker, slept 45s)
- At t_kill (00:26:39Z):
  - Child instance: status=completed
  - Parent instance: status=waiting_children ✓
  - Wake task (id=1655): status='running' (CLAIMED, not pending)
  - Wake message (id=10a2555b): status='processing' (CLAIMED, not pending)
  - wake_msgs count: 1

## Root cause analysis
The F-2 lane 2 recovery is designed for **anchor-less** completed children —
i.e., the wake message_queue row was WIPED by `QUEUE_DISCARD_ON_STARTUP=true`.
The wipe happens for rows in 'pending'/'queued' state. In-flight tasks in
'running'/'processing' state are PRESERVED by F-1's arm-1 predicate
(`status IN ('running', 'paused')`).

I caught the wedge AFTER the wake was already claimed by the worker pool
(wake task 1655 was in 'running' state, started ~70ms after creation). The
F-1 predicate preserved the in-flight task across the boot. The F-2 lane 2
sweep at boot found the anchor (message_queue row 10a2555b in 'processing'
state) and SKIPPED — anchor exists, not anchor-less.

## Boot-time RDRS lane 2 log
```
00:27:21 - daemon.services.report_delivery_recovery - INFO - RDRS lane 2: post-wipe recovery (no-row backstop; F-2 wedge closure per durability-f1-f2 / phase2 task 2.8)
```
No per-row "sweep no_row_backstop" log appeared — lane 2 found 0 anchor-less children.

## Post-reboot state (after 120s wait)
- Parent: STILL waiting_children (NOT healed)
- Wake task 1655: STILL 'running' (orphaned in-flight task)
- Wake message 10a2555b: STILL 'processing' (orphaned in-flight message)

## Implication
To trigger F-2 lane 2 recovery, the kill MUST happen during the ~70ms window
between wake task CREATION (status='pending') and wake task CLAIM
(status='running'). This is the "F-2 wedge" per the task spec. My 200ms
polling missed this window in attempt 1.

## Next attempt plan
- 50ms polling (finer granularity)
- Watch wake task status explicitly (status='pending' is the precondition)
- Retry up to 3× per task spec
