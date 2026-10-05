# F4 LEG 1 — LEG-1 FINAL Live Proof (Wake-Through) — **FAIL**

**Date:** 2026-10-05 07:09–07:19 UTC
**Result:** **FAIL** — parent did NOT heal within 300s with zero pings

## Wake-Through Fix Anchors (4b601955)
1. **task_processor.py:444-451 area** — the `already_delivered` skip at task_processor.py (NOT modified by 4b601955 — it's the DIVERTED path)
2. **lane-6 dispatch call** — `_run_stuck_wake_lane` now calls `self._manager._process_child_completion_and_notify_parent(child_id, child_message_id)` via the manager-loop bridge (sanctioned pattern from `_handle_recover_deferred_report`)
3. **Idempotency guard** — the called function checks "already in terminal state (completed), skipping _process_child_completion_db_sync (idempotency)"
4. **Schedule-pin test** — `TestG4R3StuckWakeParentScheduleSeam` (new class in test_report_delivery_recovery_pg.py:2263+)

## b1d222e6 (doc-only verification)
- Commit message: "docs(durability-f1-f2): W-2 lane-6 exception"
- `git show b1d222e6 --stat` shows: `daemon/services/report_delivery_recovery.py | 57 +++` (additive)
- Content: W-2 BLOCK AMENDMENT at module head (docstring/comment, 57 lines of documentation)
- **Effective change: doc-only** (no behavioral code; the amendment is a comment block sanctioning the direct call)

## Wedge capture (SIGSTOP→verify→SIGKILL)
- t=40219ms: child_task=completed, wake_task=running, wake_msg=ready, inj_state=PENDING, worker_id=worker-2
- SIGSTOP, precondition verified, SIGKILL at t=40322ms
- Heartbeat stale 92s (>90s threshold) at heal time

## Lane 6 wake-through dispatch at 07:13:24
```
sweep stuck_wake heal: dead-worker wake task id=1756 → retry id=1757 parent=5c791341... child=fb23ca4f...
_process_child_completion_and_notify_parent called: instance=fb23ca4f..., message_id=334e080b
Instance fb23ca4f... already in terminal state (completed), skipping _process_child_completion_db_sync (idempotency)
ReportDeliveryRecoveryService sweep: recovered=1, lanes={..., 'stuck_wake': {'recovered': 1, ...}}
```

**CRITICAL: the wake-through dispatch was a NO-OP**
- The function `_process_child_completion_and_notify_parent(instance=fb23ca4f, message_id=334e080b)` was called
- The function's idempotency guard fired: child is already completed → skip _process_child_completion_db_sync
- The function returned early WITHOUT creating a wake row
- No wake row was created for the parent

## Retry task 1757 at 07:14:25
```
Processing message task 1757: message=df63a2ec..., instance=5c791341...
Task 1757: report df63a2ec... already delivered via report-injection (INJECTED) — skipping PROCESS_REPORT graph turn
```
- The retry task found the report "already delivered" and SKIPPED the graph turn
- Same-message_id contract: VERIFIED (1757.message_id = df63a2ec = wake_row.message_id)
- retry_count: 1 (bounded by max_retries=3)
- No double-retry: PASS

## Zero-ping observation (t+0 to t+300)
- api_msgs: 1→0 at t+35s (the parent's initial API message was processed and completed by the LLM)
- ZERO manual pings between kill and observation (confirmed)
- report_injections: PENDING → TASK_DELIVERED at t+35s (lane 6's injection recorded)
- **Parent: STILL in 'waiting_children' at t+300s — NEVER healed**

## Manual ping (after zero-ping observation)
- Sent at 07:19:xx → parent completed normally
- R4ii (deadlock gone in healed state) from previous rounds: STILL VERIFIED

## R1 criteria (LEG 1)
| Criterion | Status |
|-----------|--------|
| a. Lane 6 recovers via source correlation | **PASS** (recovered=1) |
| b. Parent leaves waiting_children + synthesizes helloF4, zero pings | **FAIL** (parent stuck 300s) |
| c. EXACTLY ONE internal_report row | PASS (1 row) |
| d. Child NOT re-executed | PASS (1 task row, completed) |
| e. No double-retry, same-message_id | PASS (retry_count=1, message_id matches) |
| f. Schedule dispatch observable in boot log | **PARTIAL** (the dispatch log line IS present: `_process_child_completion_and_notify_parent called: instance=fb23ca4f..., message_id=334e080b`, but the call was a no-op due to idempotency guard) |

## ROOT CAUSE (NEW gap)
The wake-through fix's call to `_process_child_completion_and_notify_parent` is a NO-OP because:
1. The function's idempotency guard checks if the child is already in terminal state
2. The child IS already completed (from the natural completion before the kill)
3. The function returns early, skipping the wake row creation
4. No wake row is created → parent remains stuck

The retry task (1757) also skips because "already delivered via report-injection (INJECTED)".

The fix's intent was correct (call the same primitive the natural wake uses), but the primitive's idempotency guard prevents the wake row from being created when the child is already terminal.

## Recommendation (follow-up commission)
- The function `_process_child_completion_and_notify_parent` needs to be modified so the idempotency guard does NOT skip the wake row creation
- OR: the function should be called with a different argument (e.g., a force-create flag)
- OR: the injection mechanism needs to be modified to enqueue a graph turn for the parent
