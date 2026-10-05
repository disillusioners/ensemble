# F3 LEG 3 — Kill-Switch Env Leg (helloF3)

**Date:** 2026-10-05 06:02–06:09 UTC
**Result:** PASS (env wiring end-to-end verified) + GAP (parent wake-up via injection)

## Env var name (verified)
- Field: `report_delivery_recovery_lane_stuck_wake: bool` (daemon/config.py)
- Env prefix: `SERVICES_`
- Full env var: `SERVICES_REPORT_DELIVERY_RECOVERY_LANE_STUCK_WAKE`
- Values accepted: `false`, `False`, `0` (all → False); `true`, `True`, `1` (all → True)
- Default: True (lane enabled)

## LEG 3a: Kill-switch OFF boot
- Wrapper invoked with `/tmp/f3-lane6-off.env` (contains `export SERVICES_REPORT_DELIVERY_RECOVERY_LANE_STUCK_WAKE=false`)
- Constructor log: `lanes=[deferred=True, no_row_backstop=True, pending_age=True, recovery_retry=True, orphan=True, stuck_wake=False]`
- /proc/<pid>/environ: `SERVICES_REPORT_DELIVERY_RECOVERY_LANE_STUCK_WAKE=false` ✓
- Wedge PERSISTS ≥120s:
  - At t+0: parent=waiting_children, wake_msg=ready, inj=PENDING
  - At t+120s: parent=waiting_children, wake_msg=ready
- No stuck_wake or force_cancel log lines (lane 6 disabled) ✓
- WEDGE PERSISTENCE VERIFIED: kill-switch OFF blocks lane 6

## LEG 3b: Kill-switch ON reboot (default)
- Wrapper invoked WITHOUT env override
- Constructor log: `lanes=[..., stuck_wake=True]`
- Lane 6 ran at boot (recover_on_startup):
  ```
  06:08:40 - sweep stuck_wake heal: dead-worker wake task id=1751 → retry id=1752 parent=b0a7bd55... child=0f5becb1...
  06:08:40 - ReportDeliveryRecoveryService sweep: recovered=1, lanes={..., 'stuck_wake': {'recovered': 1, ...}}
  ```
- Lane 6 healed the wedge (task 1751 → retry 1752)
- wake_msg now 'failed' (retry task processed it)

## Manual ping (deviation)
- Parent still in 'waiting_children' after lane 6 heal (same injection gap as LEG 1/2)
- Manual ping → parent completed

## LEG 3 verdict
- **Env wiring end-to-end VERIFIED**: kill-switch OFF blocks lane 6, kill-switch ON heals via lane 6
- **Operator-reachable kill-switch NOW WORKS** (the G4-r advisory gap is fixed)
- Same-message_id contract: VERIFIED (retry 1752.message_id = wake_row.message_id)
- retry_count: 1 (bounded)
- No double-retry: PASS
