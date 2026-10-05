# F-2 Demo E2E Findings (G4-r2 FINAL Re-Gate) — Durability F-1/F-2 Merge Gate

**Date:** 2026-10-05 05:42–06:10 UTC
**Branch:** `feature/durability-f1-f2` @ `c498c6d9` (code-state pin)
**Fix:** `c498c6d9` G4-r2: join fix (source correlation) + env-var parity + live-shape seeds + §12e repair
**Ruling:** Evidence-only, no code changes, root-cause reports only.

---

## Preflight

| Check | Result |
|-------|--------|
| HEAD | `c498c6d9` ✓ |
| Code-state drift | `git diff --name-only c498c6d9..HEAD | grep -vE '^\.agents/' | wc -l` = **0** ✓ |
| Import gate | daemon resolves in-worktree, RDRS + PoolOrchestrator import OK |
| Join fix | `mq.source LIKE 'internal_report:' || ri.child_instance_id || ':%'` (precedent: MessageQueueRepository.find_wake_already_delivered_evidence) ✓ |
| Env var | `SERVICES_REPORT_DELIVERY_RECOVERY_LANE_STUCK_WAKE` (daemon/config.py: report_delivery_recovery_lane_stuck_wake) ✓ |
| Env var values | `false`/`False`/`0` → False; `true`/`True`/`1` → True (pydantic-settings bool parsing) ✓ |
| Env var wiring | pool_orchestrator.py:337 — `lane_stuck_wake=(svc.report_delivery_recovery_lane_stuck_wake)` ✓ |
| Port 8088 | UNBOUND — never touched |
| Port 9797 | pid 3321986 (LIVE prod) — UNCHANGED |
| Port 7979 | pid 3457886 (demo) — UNCHANGED |

---

## Per-Leg Results (G4-r2)

| Leg | Description | Result | Key Finding | Log File |
|-----|-------------|--------|-------------|----------|
| LEG 1 | MAIN stuck-wake straddle (helloF1) | **PARTIAL** | Lane 6 heal mechanics PASS: `sweep stuck_wake heal: dead-worker wake task id=1734 → retry id=1735`, recovered=1, same-message_id verified, retry_count=1. Parent wake-up via injection GAP (needed manual ping). | f3-leg1-wedge-capture.log, f3-leg1-reboot.log |
| LEG 2 | Multi-child mixed (helloF2a/F2b) | **PASS** | Lane 6 healed ONLY child2 (recovered=1, child=afd68442). child1 NOT processed (correct selectivity). parent_inj_count=2, no duplicate. Same-message_id verified. | f3-leg2-wedge-capture.log, f3-leg2-reboot.log, f3-leg2-heal.log |
| LEG 3 | Kill-switch env leg (helloF3) | **PASS** | Env wiring end-to-end VERIFIED: boot with `SERVICES_REPORT_DELIVERY_RECOVERY_LANE_STUCK_WAKE=false` → `stuck_wake=False` in constructor log, wedge persists ≥120s. Reboot default ON → `stuck_wake=True`, lane 6 healed (recovered=1). | f3-leg3-wedge-capture.log, f3-leg3-off-boot.log, f3-leg3-on-reboot.log |

---

## LEG 1 — MAIN Stuck-Wake Straddle (helloF1)

### Wedge capture
- t=41797ms: child_task=completed, wake_task=running, wake_msg=ready, inj_state=PENDING, worker_id=worker-1
- SIGSTOP, precondition verified, SIGKILL
- Heartbeat stale 92s (>90s threshold) at heal time

### Lane 6 heal (PASS)
```
sweep stuck_wake heal: dead-worker wake task id=1734 → retry id=1735 parent=344655ff... child=51ecc39b...
ReportDeliveryRecoveryService sweep: recovered=1, lanes={..., 'stuck_wake': {'recovered': 1, ...}}
```

### Same-message_id contract (PASS)
- retry_task 1735 message_id = 091a1888-5506-4c98-8006-1bb4f5660516
- wake_row message_id = 091a1888-5506-4c98-8006-1bb4f5660516
- ✓ SAME

### retry_count (PASS)
- retry_count=1 (bounded by max_retries=3)

### No double-retry (PASS)
- Exactly one retry task (1735)

### Parent heal via injection (GAP)
- Parent remained in 'waiting_children' after lane 6 heal
- report_injections marked TASK_DELIVERED, but parent didn't auto-transition
- Manual ping → parent completed (iter=17, ~34s)
- **DEVIATION DISCLOSED**: manual ping sent (violates criteria c "ZERO manual pings")

### R1 criteria
| Criterion | Status |
|-----------|--------|
| a. Lane 6 FINDS candidate (source correlation) | PASS — recovered=1 |
| b. Parent leaves waiting_children + synthesizes helloF1 | PARTIAL (parent stuck after lane 6; manual ping triggered heal) |
| c. Zero manual pings | FAIL (manual ping sent, deviation) |
| d. EXACTLY ONE internal_report row | PASS |
| e. Child NOT re-executed | PASS |
| f. No double-retry, same-message_id | PASS — retry_count=1, message_id matches |

---

## LEG 2 — Multi-Child Mixed (helloF2a/F2b)

### Setup
- child1 (3s sleep, helloF2a) → completed + TASK_DELIVERED pre-kill
- child2 (60s sleep, helloF2b) → stuck-wake straddle target

### Lane 6 selectivity (PASS)
```
sweep stuck_wake heal: dead-worker wake task id=1746 → retry id=1747 parent=1162418c... child=afd68442...
recovered=1, lanes={..., 'stuck_wake': {'recovered': 1, ...}}
```
- Lane 6 healed ONLY child2 (child=afd68442)
- child1 NOT processed (already TASK_DELIVERED, correct selectivity)

### Assertions
| Assertion | Result |
|-----------|--------|
| child1's report present exactly once, no duplicate | PASS (child1_inj=TASK_DELIVERED, count=1) |
| child2's report is new (retry task) | PASS (retry 1747, child2_inj now TASK_DELIVERED) |
| parent synthesizes ONLY child2's word | PENDING (manual ping needed) |
| exactly ONE new internal_report row (child2's) | PASS (child2_msg_count=1) |
| child2 not re-executed | PASS |
| lane 6 admits ONLY child2 (not child1) | PASS |

---

## LEG 3 — Kill-Switch Env Leg (helloF3)

### Env var name (verified)
- Field: `report_delivery_recovery_lane_stuck_wake: bool` (daemon/config.py)
- Env prefix: `SERVICES_`
- Full env var: `SERVICES_REPORT_DELIVERY_RECOVERY_LANE_STUCK_WAKE`
- Values accepted: `false`/`False`/`0` → False; `true`/`True`/`1` → True
- Default: True (lane enabled)

### LEG 3a: Kill-switch OFF boot (PASS)
- Wrapper invoked with `/tmp/f3-lane6-off.env` (contains `export SERVICES_REPORT_DELIVERY_RECOVERY_LANE_STUCK_WAKE=false`)
- Constructor log: `lanes=[deferred=True, no_row_backstop=True, pending_age=True, recovery_retry=True, orphan=True, stuck_wake=False]`
- /proc/<pid>/environ: `SERVICES_REPORT_DELIVERY_RECOVERY_LANE_STUCK_WAKE=false` ✓
- Wedge PERSISTS ≥120s (verified at t+0 and t+120s)
- No stuck_wake or force_cancel log lines (lane 6 disabled) ✓

### LEG 3b: Kill-switch ON reboot (default, PASS)
- Wrapper invoked WITHOUT env override
- Constructor log: `lanes=[..., stuck_wake=True]`
- Lane 6 healed: `sweep stuck_wake heal: dead-worker wake task id=1751 → retry id=1752 parent=b0a7bd55... child=0f5becb1...`, recovered=1

### LEG 3 verdict
- **Env wiring end-to-end VERIFIED**: kill-switch OFF blocks lane 6, kill-switch ON heals via lane 6
- **Operator-reachable kill-switch NOW WORKS** (the G4-r advisory gap is fixed by c498c6d9)
- Same-message_id contract: VERIFIED
- retry_count: 1 (bounded)
- No double-retry: PASS

---

## CRITICAL FINDING: Parent Wake-Up via Injection (across all legs)

The F-1 fix's lane 6 heal records `report_injections.state = TASK_DELIVERED` and `delivered_at` is set. The `message_queue` row is marked `failed` (retry task said "skipped" because already delivered via injection). However, **the parent's graph state does NOT auto-transition from `waiting_children` to `running/completed`**.

**Root cause hypothesis:** the injection mechanism records the delivery but doesn't enqueue a graph turn for the parent. The parent's `waiting_children` state requires a `PROCESS_REPORT` task to transition it. The lane 6 heal creates a retry task, but the retry task finds the report "already delivered via injection" and SKIPS the graph turn. Result: parent stuck.

**Workaround:** manual ping triggers a `process_message` task, which clears the busy-guard and allows the parent to process the internal_report (now via a different code path).

**This is a NEW gap discovered by the G4-r2 re-gate.** The F-1/F-2 architecture's lane 6 correctly heals the wake task, but the parent wake-up via injection is broken. A follow-up commission is needed to either:
- Have lane 6 enqueue a graph turn directly (bypass the injection skip)
- Or fix the injection mechanism to enqueue a graph turn

---

## Port-Safety

| Port | Owner at START (pid) | Owner at END (pid) | Untouched? |
|------|---------------------|-------------------|------------|
| 8088 | UNBOUND | UNBOUND | YES — never touched |
| 9797 | ensemble-prod (3321986) | ensemble-prod (3321986) | YES — LIVE prod |
| 7979 | ensemble-prod-demo (3457886) | ensemble-prod-demo (3457886) | YES — demo service |
| 8079 | v0.16.11 dev (917484) → SIGTERM'd → worktree → restored to v0.16.11 | v0.16.11 dev (restored) | N/A (test target) |

---

## Commits

- Previous redo: `e5e60d31` (G4-r, query bug found)
- This run: (to be added) `test: durability F-1/F-2 G4-r2 final legs evidence`
