# F3 LEG 2 — Multi-Child Mixed (helloF2a, helloF2b)

**Date:** 2026-10-05 05:55–05:59 UTC
**Result:** PASS (lane 6 selectivity) + GAP (parent wake-up via injection)

## Setup
- Parent 1162418c + 2 children
  - Child1 50dcc93d (3s sleep, helloF2a) → completed + TASK_DELIVERED pre-kill
  - Child2 afd68442 (60s sleep, helloF2b) → stuck-wake straddle target

## Wedge capture (SIGSTOP, child2-specific poller)
- t=45403ms: child2_task=completed, parent=waiting_children, wake_task=running, wake_msg=ready, inj_state=PENDING, worker_id=worker-1
- SIGSTOP, precondition verified, SIGKILL
- Wait 100s, heartbeat stale >90s

## Post-reboot lane 6 heal
```
05:59:18 - sweep stuck_wake heal: dead-worker wake task id=1746 → retry id=1747 parent=1162418c... child=afd68442...
05:59:18 - ReportDeliveryRecoveryService sweep: recovered=1, lanes={..., 'stuck_wake': {'recovered': 1, ...}}
```
- Lane 6 healed ONLY child2 (child=afd68442)
- child1 NOT processed (already TASK_DELIVERED, correct selectivity)
- Same-message_id contract: verified
- retry_count: 1 (bounded)

## Assertions
| Assertion | Result |
|-----------|--------|
| child1's report present exactly once, no duplicate | PASS (child1_inj=TASK_DELIVERED, count=1) |
| child2's report is new (retry task) | PASS (retry 1747, child2_inj now TASK_DELIVERED) |
| parent synthesizes ONLY child2's word | PENDING (manual ping needed) |
| exactly ONE new internal_report row (child2's) | PASS (child2_msg_count=1) |
| child2 not re-executed | PASS (1 task row, completed) |
| lane 6 admits ONLY child2 (not child1) | PASS (recovered=1, child=afd68442) |

## GAP: parent wake-up via injection
- Same as LEG 1: injection records TASK_DELIVERED but parent doesn't auto-process
- Manual ping triggered parent completion (iter=17, ~34s)
- Deadlock from previous round IS GONE in healed state (with lane 6 mechanics)
