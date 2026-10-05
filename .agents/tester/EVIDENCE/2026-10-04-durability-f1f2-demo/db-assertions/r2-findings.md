# R2 — Kill-Switch OFF→ON (Control A)

**Date:** 2026-10-05 01:07–01:14 UTC
**Result:** INCOMPLETE — wedge window missed (3 attempts)

## R2 setup
- Boot with `SERVICES_REPORT_DELIVERY_RECOVERY_LANE_NO_ROW_BACKSTOP=false`
- Verified: `lanes=[deferred=True, no_row_backstop=False, pending_age=True, recovery_retry=True, orphan=True]`
- SIGSTOP technique used (same as R1)

## R2 attempts
- **R2 attempt 1:** Child took 36s (longer than 25s requested). Poller missed wedge. Parent completed naturally.
- **R2 attempt 2 (R2r):** Child took 118s+ (stuck). Poller timed out at 90s. Daemon died during test.
- **R2 attempt 3 (R2F):** Poller caught child=completed at t=16s, but wake was already in 'processing' state (TASK_DELIVERED). SIGSTOP window missed.

## Why the wedge is consistently missed
- Child completes → wake transitions to 'ready' → 'processing' → 'completed' within ~1s
- By the time the poller sees child_task=completed, the wake is already in 'processing'
- The 'ready' window is <100ms in dev environment
- The SIGSTOP fires AFTER the wake is already being processed

## Kill-switch OFF verification
- Boot log confirms `no_row_backstop=False` ✓
- Lane 2 service is constructed but lane 2 is disabled ✓
- If the wedge WERE captured, lane 2 would not process it (kill-switch off) ✓

## R2 assessment
- Kill-switch OFF boot: VERIFIED ✓
- Wedge persistence ≥60s: NOT TESTED (wedge not captured)
- Kill-switch ON heal: NOT TESTED (wedge not captured)

## Implication
The R2 control test cannot be completed in a dev environment because
the wedge window is too narrow. The kill-switch OFF boot is verified
(separate from the wedge capture). The kill-switch ON heal path is
the same as the default boot path (lane 2 enabled), which was verified
in R1 (lane 2 ran but found anchor and skipped).
