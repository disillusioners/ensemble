# F-2 Demo E2E Findings (LEG-1 FINAL Live Proof — Wake-Through) — **FAIL**

**Date:** 2026-10-05 07:09–07:21 UTC
**Branch:** `feature/durability-f1-f2` @ `b1d222e6` (code-state pin)
**Fixes:** `4b601955` (wake-through) + `b1d222e6` (W-2 doc amendment)
**Ruling:** Evidence-only, no code changes, root-cause report.

---

## Preflight

| Check | Result |
|-------|--------|
| HEAD | `b1d222e6` ✓ |
| Code-state drift | `git diff --name-only 4b601955..HEAD | grep -vE '^\.agents/' | wc -l` = **1** (daemon/services/report_delivery_recovery.py — W-2 block, doc-only) ✓ |
| Import gate | daemon resolves in-worktree, RDRS import OK |
| Port 8088 | UNBOUND — never touched |
| Port 9797 | pid 3321986 (LIVE prod) — UNCHANGED |
| Port 7979 | pid 3457886 (demo) — UNCHANGED |

---

## Wake-Through Fix Anchors (4b601955)

### (i) task_processor.py:444-451 area — the DIVERTED path
- The `already_delivered` skip at task_processor.py is NOT modified by 4b601955
- It's the path the retry task takes: "Task 1757: report df63a2ec... already delivered via report-injection (INJECTED) — skipping PROCESS_REPORT graph turn"
- This is why the retry alone can't heal the parent

### (ii) Lane-6 dispatch call — `report_delivery_recovery.py:985+`
- `self._manager._process_child_completion_and_notify_parent(child_id, child_message_id)` via `asyncio.run_coroutine_threadsafe(...).result(8.0)`
- Sanctioned pattern from `_handle_recover_deferred_report` at `manager.py:8586-8596`
- Boot log: `07:13:24 _process_child_completion_and_notify_parent called: instance=fb23ca4f..., message_id=334e080b`

### (iii) Idempotency guard
- `child_reports.py:2773-2787` child-status guard returns `idempotency_skip` on re-entry
- Boot log shows: "Instance fb23ca4f... already in terminal state (completed), skipping _process_child_completion_db_sync (idempotency)"
- **The guard fired BEFORE the wake row creation step** — the function returned early

### (iv) Schedule-pin test
- `tests/unit/test_report_delivery_recovery_service.py::TestG4R3StuckWakeParentScheduleSeam::test_lane6_heal_dispatches_parent_via_natural_primitive`
- Asserts `assert_awaited_once_with(child_id, child_content_message_id)`
- Meta-tested red→green on revert→restore (per the commit message)

---

## b1d222e6 (doc-only verification)

`git show b1d222e6 --stat`:
```
daemon/services/report_delivery_recovery.py | 57 +++++++++++++++++++++++++++++
```

- Commit message: "docs(durability-f1-f2): W-2 lane-6 exception - sanction the post-materialization direct call"
- Content: W-2 BLOCK AMENDMENT at module head (lines 68+ of report_delivery_recovery.py)
- **Effective change: doc-only** (no behavioral code; the 57 lines are a comment block sanctioning the direct call)

---

## W-2 Quotes (verbatim)

### Module head (b1d222e6 amendment) — `report_delivery_recovery.py:68+`:
> **LANE-6 EXCEPTION (G4-r3, 2026-10-05; commit ``4b601955``, ``decisions.md §12d`` companion note).** The seam-(a) blanket prohibition above is UNCHANGED for every other caller. **The ONE explicit exception is ``_run_stuck_wake_lane``** (this module, at :985), which calls ``self._manager._process_child_completion_and_notify_parent(child_id, child_message_id)`` DIRECTLY, POST-materialization, from the sweep thread via the sanctioned manager-loop bridge (``run_coroutine_threadsafe(...).result(8.0)`` — the same pattern as ``_handle_recover_deferred_report`` at ``manager.py:8586-8596`` and the revival seam at ``manager.py:9118``).

### decisions.md §12d companion note:
> **§12d companion note — Lane-6 exception (G4-r3, 2026-10-05; commit ``4b601955``).** The seam-(a) blanket prohibition above is UNCHANGED for every other caller. **The ONE explicit exception is `_run_stuck_wake_lane`** (at `daemon/services/report_delivery_recovery.py:985` in the 4b601955 snapshot), which calls `self._manager._process_child_completion_and_notify_parent(child_id, child_message_id)` DIRECTLY, POST-materialization, from the sweep thread via the sanctioned manager-loop bridge.

### Existing W-2 DO-NOT-CALL block (the prohibition, unchanged):
> **DO NOT** make a **DIRECT** call into ``ChildReportsService._process_child_completion_and_notify_parent(self, instance_id, completed_message_id)`` at ``child_reports.py:2458`` — takes the **CHILD** id first; passing the parent id silently no-ops on the root branch. **The prohibition is on DIRECT calls; the transitive re-entry into `_process_child_completion_and_notify_parent` POST-materialization, through the sanctioned RDRS chain, is NOT prohibited.**

---

## LEG 1 — Zero-Ping Observation (t+0 to t+300)

### Wedge capture (SIGSTOP→verify→SIGKILL)
- t=40219ms: child_task=completed, wake_task=running, wake_msg=ready, inj_state=PENDING, worker_id=worker-2
- SIGSTOP, precondition verified, SIGKILL at t=40322ms
- Heartbeat stale 92s (>90s threshold) at heal time

### Lane 6 wake-through dispatch at 07:13:24
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

### Retry task 1757 at 07:14:25
```
Processing message task 1757: message=df63a2ec..., instance=5c791341...
Task 1757: report df63a2ec... already delivered via report-injection (INJECTED) — skipping PROCESS_REPORT graph turn
```
- Same-message_id contract: VERIFIED (1757.message_id = df63a2ec = wake_row.message_id)
- retry_count: 1 (bounded by max_retries=3)
- No double-retry: PASS

### Zero-ping observation table

| t+offset | parent_status | inj_state | api_msgs | int_reports |
|----------|---------------|-----------|----------|-------------|
| t+5s | waiting_children | PENDING | 1 | 1 |
| t+30s | waiting_children | PENDING | 1 | 1 |
| t+35s | waiting_children | **TASK_DELIVERED** | 0 | 1 |
| t+60s | waiting_children | TASK_DELIVERED | 0 | 1 |
| t+120s | waiting_children | TASK_DELIVERED | 0 | 1 |
| t+180s | waiting_children | TASK_DELIVERED | 0 | 1 |
| t+240s | waiting_children | TASK_DELIVERED | 0 | 1 |
| **t+300s** | **waiting_children (NEVER HEALED)** | TASK_DELIVERED | 0 | 1 |

### Manual ping (after zero-ping observation)
- Sent at 07:20:16 → parent completed at iter=16 (~32s)
- R4ii (deadlock gone in healed state) from previous rounds: STILL VERIFIED

---

## R1 Criteria (LEG 1)

| Criterion | Status |
|-----------|--------|
| a. Lane 6 recovers via source correlation | **PASS** (recovered=1) |
| b. Parent leaves waiting_children + synthesizes helloF4, **zero pings** | **FAIL** (parent stuck 300s) |
| c. EXACTLY ONE internal_report row | PASS (1 row) |
| d. Child NOT re-executed | PASS (1 task row, completed) |
| e. No double-retry, same-message_id | PASS (retry_count=1, message_id matches) |
| f. Schedule dispatch observable in boot log | **PARTIAL** (dispatch log line present but the call was a no-op due to idempotency guard) |

---

## ROOT CAUSE (NEW gap — wake-through fix is FLAWED)

The wake-through fix's call to `_process_child_completion_and_notify_parent` is a NO-OP because:

1. The function's idempotency guard checks if the child is already in terminal state
2. The child IS already completed (from the natural completion before the kill)
3. The function returns early, skipping the wake row creation (step 3 of the documented flow)
4. No wake row is created → parent remains stuck

The retry task (1757) at 07:14:25 also skips because "already delivered via report-injection (INJECTED)".

**The fix's intent was correct** (call the same primitive the natural wake uses), **but the primitive's idempotency guard prevents the wake row from being created when the child is already terminal**.

**Same gap as F3-r2** (injection records TASK_DELIVERED but parent doesn't transition). The fix attempted to bypass the gap by calling the primitive directly, but the primitive's own idempotency guard creates a new gap.

---

## DECISIVE VERDICT: **FAIL**

The LEG-1 FINAL live proof is a **FAIL**. The wake-through fix does NOT heal the captured wedge in the zero-ping observation window:

1. Lane 6 dispatches the parent-schedule primitive (boot log line present) — but the dispatch is a NO-OP
2. The primitive's idempotency guard fires BEFORE the wake row creation step
3. No wake row is created → parent remains in 'waiting_children'
4. The retry task also skips ("already delivered via report-injection")
5. Parent NEVER transitions from 'waiting_children' within 300s with zero pings

**The overall merge verdict does NOT flip to READY.**

### Recommendation (follow-up commission)
- The function `_process_child_completion_and_notify_parent` needs to be modified so the idempotency guard does NOT skip the wake row creation
- OR: the function should be called with a different argument (e.g., a force-create flag)
- OR: the injection mechanism needs to be modified to enqueue a graph turn for the parent
- OR: the lane 6 dispatch should bypass the function entirely and directly create a wake row

---

## Port-Safety

| Port | Owner at START (pid) | Owner at END (pid) | Untouched? |
|------|---------------------|-------------------|------------|
| 8088 | UNBOUND | UNBOUND | YES — never touched |
| 9797 | ensemble-prod (3321986) | ensemble-prod (3321986) | YES — LIVE prod |
| 7979 | ensemble-prod-demo (3457886) | ensemble-prod-demo (3457886) | YES — demo service |
| 8079 | v0.16.11 dev (925026) → SIGKILL'd → worktree → restored to v0.16.11 | v0.16.11 dev (restored) | N/A (test target) |

---

## Commits

- Previous: `08b64b7d` (G4-r2, injection gap found)
- This run: (to be added) `test: durability F-1/F-2 LEG-1 final live proof (wake-through)`
