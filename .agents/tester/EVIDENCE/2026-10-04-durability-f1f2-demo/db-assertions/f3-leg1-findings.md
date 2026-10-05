# F3 LEG 1 — MAIN Stuck-Wake Straddle (helloF1)

**Date:** 2026-10-05 05:42–05:52 UTC
**Result:** PARTIAL — Lane 6 heal mechanics PASS, parent wake-up via injection GAP

## Wedge capture (SIGSTOP→verify→SIGKILL)
- t=41797ms: child_task=completed, parent=waiting_children, wake_task=running, wake_msg=ready, inj_state=PENDING, worker_id=worker-1
- SIGSTOP at t=41797ms, precondition verified, SIGKILL at t=41899ms
- Heartbeat stale_delta: 92s (>90s threshold) at heal time

## Post-reboot lane 6 heal
```
05:46:24 - daemon.services.report_delivery_recovery - INFO - sweep stuck_wake heal: dead-worker wake task id=1734 → retry id=1735 parent=344655ff... child=51ecc39b...
05:46:24 - daemon.services.report_delivery_recovery - INFO - ReportDeliveryRecoveryService sweep: recovered=1, lanes={..., 'stuck_wake': {'recovered': 1, ...}}
```
- Lane 6 FOUND the candidate (via source correlation fix)
- force_cancel_and_schedule_retry cancelled task 1734, minted retry 1735
- Retry 1735 claimed by worker-1 at 05:47:24
- Retry 1735 found "already delivered via report-injection (INJECTED) — skipping PROCESS_REPORT graph turn"
- report_injections state: PENDING → TASK_DELIVERED at 05:46:24
- message_queue row: ready → failed (retry task said "skipped": true)

## Same-message_id contract: VERIFIED
- retry_task 1735 message_id = 091a1888-5506-4c98-8006-1bb4f5660516
- wake_row message_id = 091a1888-5506-4c98-8006-1bb4f5660516
- ✓ SAME

## retry_count: 1 (bounded by max_retries=3)

## No double-retry: PASS (exactly one retry task 1735)

## Parent heal: GAP
- Parent remained in 'waiting_children' after lane 6 heal
- Injection recorded TASK_DELIVERED but parent's graph state didn't transition
- Manual ping sent at 05:51:45 → parent completed at 05:52:19 (iter=17, ~34s)

## DEVIATION DISCLOSED
- Manual ping sent (violates criteria c "ZERO manual pings")
- Rationale: injection mechanism didn't auto-wake the parent
- R4ii from previous round (deadlock gone in healed state) NOW VERIFIED with lane 6 mechanics

## R1 criteria
| Criterion | Status |
|-----------|--------|
| a. Lane 6 FINDS candidate (source correlation) | PASS — recovered=1 |
| b. Parent leaves waiting_children + synthesizes helloF1 | PARTIAL — parent stuck after lane 6; manual ping triggered heal |
| c. Zero manual pings | FAIL — manual ping sent (deviation) |
| d. EXACTLY ONE internal_report row | PASS — 1 row (status=failed, inj=TASK_DELIVERED) |
| e. Child NOT re-executed | PASS — 1 task row, completed |
| f. No double-retry, same-message_id | PASS — retry_count=1, message_id matches |
