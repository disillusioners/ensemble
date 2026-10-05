# F5 LEG 1 — LEG-1 Round-5 Live Proof (Ping-Seam Wake)

**Date:** 2026-10-05 07:47–08:15 UTC
**Fix HEAD:** `3c7c4df6` (ping-seam wake)
**Result:** **PARTIAL PASS** — parent leaves waiting_children + synthesizes helloF5 with ZERO pings, but instance status stuck at `running` (not `completed`) due to pre-existing pending-tasks guard

---

## Preflight anchors (verified)

### (i) lane-6 `enqueue_message` call site
```
self._manager.enqueue_message(
    parent_id,
    "[system:wedge-resolve] Your child has completed. The
     pending report is preserved in your message queue;
     process it to continue.",
    source="system:wedge-resolve",
    priority=1,
)
```
via the sanctioned manager-loop bridge (asyncio.run_coroutine_threadsafe + .result(8.0)).

### (ii) auto-resume seam (instance_messaging.py:1968 area)
```
if instance.status in (
    InstanceStatus.IDLE.value,
    InstanceStatus.WAITING_CHILDREN.value,
) or is_terminal_revival:
    instance.status = InstanceStatus.RUNNING.value
```
The `_prepare_enqueued_message` auto-resumes WAITING_CHILDREN → RUNNING.

### (iii) :2773 guard untouched (verified)
```
if instance.status in (
    InstanceStatus.COMPLETED.value,
    InstanceStatus.ERROR.value,
):
    logger.info(f"Instance {instance_id[:8]}... already in terminal state ... skipping _process_child_completion_db_sync (idempotency)")
```

### (iv) effect-pin test
`TestG4R3StuckWakeParentScheduleSeam::test_lane6_heal_dispatches_parent_via_ping_seam_and_turn_runs` — asserts the END-TO-END EFFECT (instances.status transition), seeded from f4- row shapes.

### What was removed (−215 lines)
The round-4 natural-primitive dispatch (`_process_child_completion_and_notify_parent` call + `run_coroutine_threadsafe` invocation) — replaced by the ping-seam `enqueue_message` dispatch.

---

## Wedge capture (SIGSTOP→verify→SIGKILL)
- t=35706ms: child_task=completed, wake_task=running, wake_msg=ready, inj_state=PENDING, worker_id=worker-1
- SIGSTOP at t=35706ms, precondition verified, SIGKILL at t=35808ms

## Post-reboot: Lane 6 heal (PASS)
```
07:50:59 - sweep stuck_wake heal: dead-worker wake task id=1761 → retry id=1762 parent=35ea11b6... child=3cca8bf4...
07:50:59 - ReportDeliveryRecoveryService sweep: recovered=1, lanes={..., 'stuck_wake': {'recovered': 1, ...}}
```

## Zero-ping observation
| t+offset | parent_status | inj_state | wedge_msgs | api_msgs |
|----------|---------------|-----------|------------|----------|
| t+5s | waiting_children | PENDING | 0 | 1 |
| t+35s | waiting_children | PENDING | 0 | 0 |
| **t+40s** | **running** | **TASK_DELIVERED** | **1** | 0 |
| t+120s | running | TASK_DELIVERED | 1 | 0 |
| t+300s | running | TASK_DELIVERED | 1 | 0 |
| t+600s | running | TASK_DELIVERED | 1 | 0 |
| t+900s+ | running | TASK_DELIVERED | 1 | 0 |

**Parent left waiting_children at t+40s with ZERO pings.** The ping-seam dispatched the `[system:wedge-resolve]` message, the auto-resume seam transitioned the parent from `waiting_children` → `running`.

## Parent LLM turn (PASS)
The parent's LLM turn ran (07:51:13 → 07:51:26), processed the wedge-resolve message, read the injection from history, and synthesized:

```
Child completed. Final report:

**TEST RESULT: PASS**

- Child: `3cca8bf4-9218-4f2b-89be-7745fbd4391c` (worker, `test-sleep-helloF5`) — exactly one spawned
- Task: sleep ~40s via bash → reply `helloF5`
- Evidence (from child's message log): bash tool call (sleep) followed by final reply: `helloF5` — exact expected token
- No further actions taken, per test instructions.
```

**helloF5 IS synthesized in the final reply.** The wedge-resolve marker text does NOT leak into the reply.

---

## R1 criteria

| Criterion | Status |
|-----------|--------|
| a. Lane 6 recovers via source correlation | **PASS** (recovered=1) |
| b. Parent LEAVES waiting_children + synthesizes helloF5, zero pings | **PARTIAL PASS** (left waiting_children ✓, synthesized helloF5 ✓, zero pings ✓, but instance status stuck at `running` — never reaches `completed`) |
| c. EXACTLY ONE internal_report row | **PASS** (1 row) |
| d. Child NOT re-executed | **PASS** (1 task row, completed) |
| e. No double-retry, same-message_id | **PASS** (retry_count=1, message_id 44181c6f matches) |
| f. Wedge-resolve dispatch observable | **PASS** (`[system:wedge-resolve]` message in parent history, source='system:wedge-resolve') |

---

## Informational answers

### (1) Does the `[system:wedge-resolve]` message appear in the parent's transcript?
**YES.** Query by `source='system:wedge-resolve'`:
```
source: system:wedge-resolve
status: completed
content: [system:wedge-resolve] Your child has completed. The pending report is preserved in your message queue; process it to continue.
```

### (2) Benignness assessment
**BENIGN.** The parent's final reply does NOT leak the wedge-resolve marker text. The reply is a clean synthesis of the child's test result:
- The LLM read the `[system:wedge-resolve]` nudge as an operational signal
- The LLM processed the internal_report injection from history (child's full report)
- The LLM produced a clean test-result reply with the helloF5 token synthesized
- No marker pollution, no sidetracking

---

## REMAINING GAP (pre-existing, NOT introduced by ping-seam)

The parent's instance status remains `running` (not `completed`) after the LLM turn completes. The `job_feedback_observer` aborted the terminal transition at 07:51:26 because the retry task 1762 was still PENDING:
```
07:51:26 - Observer: aborting terminal transition for 35ea11b6... — instance has 1 PENDING task(s) (0 orphan, 1 live/deferred) not registered in the bus (F14), deferring finalization
07:51:26 - Instance 35ea11b6... completed message with 0 pending children but 1 pending task(s), deferring completion (pending-tasks guard)
```

After retry task 1762 completed (~07:52), no event re-fired the terminal transition. The parent remains in `running` indefinitely.

**This is a pre-existing gap** (the pending-tasks guard defers terminal transition, but no re-arm after the blocking task completes). It is NOT introduced by the ping-seam fix — the same gap existed in the F4 round (where the parent was stuck in `waiting_children`; at least now the parent is in `running` and the LLM turn ran).

**Assessment:** The ping-seam fix achieves its PRIMARY GOAL — the parent leaves waiting_children, processes the injection, synthesizes the reply with ZERO pings. The `running` (not `completed`) status is a pre-existing deferred-finalization gap that needs a separate follow-up commission.

---

## Port-Safety
- 8088: UNBOUND — never touched
- 9797: pid 3321986 (LIVE prod) — UNCHANGED
- 7979: pid 3457886 (demo) — UNCHANGED
- 8079: v0.16.11 dev (930440) → SIGKILL'd → worktree → restored to v0.16.11
