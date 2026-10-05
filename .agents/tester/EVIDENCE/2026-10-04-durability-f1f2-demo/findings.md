# F-2 Demo E2E Findings (G4-r Re-Gate) — Durability F-1/F-2 Merge Gate

**Date:** 2026-10-05 04:40–04:55 UTC
**Branch:** `feature/durability-f1-f2` @ `f53a0638` (code-state pin)
**Worktree:** `/home/nea/ensemble-src-wt-durability`
**Fix:** `3a2bbdf8` G4 fix: NEW additive lane 6 `stuck_wake` + `f53a0638` review close-out
**Ruling:** Evidence-only, no code changes, root-cause reports only.

---

## Preflight

| Check | Result |
|-------|--------|
| HEAD | `f53a0638` ✓ |
| Code-state protocol (drift = non-.agents/ files) | `git diff --name-only f53a0638..HEAD | grep -vE '^\.agents/' | wc -l` = **0** ✓ |
| Import gate | `daemon.__file__` resolves in-worktree, RDRS import OK |
| Lane 6 anchors (pinned from fix code) | |
| → field | `lane_stuck_wake: bool = True` (report_delivery_recovery.py:392) |
| → env var | **NO env var** (config.py has NO `lane_stuck_wake`) — constructor param only |
| → boot-time marker | `ReportDeliveryRecoveryService started: ... stuck_wake={self._lane_stuck_wake}]` |
| → per-row success log | `sweep stuck_wake heal: dead-worker wake task id=<X> → retry id=<Y>` |
| → per-row error log | `sweep stuck_wake force_cancel failed wake_task_id=<X>` |
| → duck-typed log | `stuck_wake lane: task_repo has no force_cancel_and_schedule_retry` |
| → heartbeat stale threshold | **90s** (find_stuck_wake_candidates default; 3× heartbeat cadence) |
| → max_retries | **3** (lane 6 caller, line 905) |
| → backoff | base=60s, max=3600s (force_cancel_and_schedule_retry defaults) |
| → same-message_id contract | retry task carries SAME message_id as original wake (f53a0638 §3 review close-out) |
| Port 8088 | UNBOUND (re-verified) — never touched |
| Port 9797 | pid 3321986 (LIVE prod) — UNCHANGED |
| Port 7979 | pid 3457886 (demo) — UNCHANGED |

---

## Per-Leg Results (G4-r)

| Leg | Description | Result | Key Finding | Log File |
|-----|-------------|--------|-------------|----------|
| R1 | LIVE stuck-wake straddle (helloR1r) | **PARTIAL** | Wedge captured correctly (SIGSTOP, precondition verified, SIGKILL). Heartbeat stale (183s > 90s). Lane 6 ran (recover_on_startup) but `find_stuck_wake_candidates` returned 0. **QUERY BUG**: join `mq.message_id = ri.child_message_id` doesn't match (mq.message_id=ddbeef1d, ri.child_message_id=34cedf8d — different values). | r1r-wedge-capture.log, r1r-reboot.log |
| R2 | Kill-switch OFF (helloR2r) | **GAP (per Advisory)** | No env var for lane 6 (config.py:0 matches). pool_orchestrator.py instantiates without kwarg (defaults ON). Unit tests with `lane_stuck_wake=False` exist (test_report_delivery_recovery_pg.py:1095,1195; test_report_delivery_recovery_service.py:323) and PASS. PG tests can't run in this env. Operational gap documented. | n/a (evidence in r2r-killswitch-off.txt) |
| R3 | Multi-child mixed (helloR3rA/B) | **INCOMPLETE** | child1 delivered naturally. child2's wake task (1727) was created AFTER poller started. Poller v1 looked at wrong task (latest, not child2-specific). Poller v2 crashed (empty DAEMON_PID). Daemon crashed (pre-existing DependencyBus bug). child2 completed naturally. | r3r-crash.log |
| R4(i) | No duplicate execution | **CODE-LEVEL ONLY** | lane 6 never processed (R1 query bug). Code-level: max_retries=3, backoff 60-3600s, same-message_id contract per f53a0638 §3. retry_count bounded. | n/a (evidence in r4r-negatives.txt) |
| R4(ii) | Manual-ping deadlock GONE (healed state) | **PASS** | Parent healed naturally. Manual ping sent → normal response (parent completed at iter=7, ~14s). No deadlock. | r4r-ping-test.log |

---

## R1 (Stuck-Wake Straddle) — CRITICAL FINDING

### Wedge capture (SIGSTOP→verify→SIGKILL)
- t=34982ms: `child_task=completed`, `wake_task=running` (worker_id=worker-4), `wake_msg=ready`, `inj_state=PENDING`
- SIGSTOP at t=34983ms, precondition verified, SIGKILL at t=35085ms
- Daemon dead, heartbeat at 04:42:25

### Post-reboot (waited 100s, heartbeat stale_delta=183s > 90s threshold)
- Lane 6 enabled: `lanes=[..., stuck_wake=True]` ✓
- `ReportDeliveryRecoveryService started: ... stuck_wake=True` ✓
- `recover_on_startup` called
- `find_stuck_wake_candidates` returned **0 candidates**
- Parent STILL stuck (waiting_children, wake_msg=ready, inj_state=PENDING)

### ROOT CAUSE: query data-shape mismatch

The fix's `find_stuck_wake_candidates` query joins:
```sql
JOIN message_queue mq ON mq.message_id = ri.child_message_id
```

This assumes `mq.message_id` = `ri.child_message_id`. But in the captured state:
- `ri.child_message_id` = `34cedf8d-ac99-4b57-a00d-8a90d014eb06` (child's message)
- `mq.message_id` = `ddbeef1d-32bd-4aee-8b7c-c5715bd1e01b` (wake message — DIFFERENT)

The `message_queue.source` encodes the child's message_id: `internal_report:<child_iid>:<child_message_id>`. The wake message is a SEPARATE message with its own `message_id`. The query's join condition is wrong — it should match on `mq.source` containing the `child_message_id`, not on `mq.message_id` directly.

**Verified manually:** relaxing the join to match on `source` pattern returns 1 row (the captured wedge). The original query returns 0 rows.

**Implication:** lane 6 is enabled but CANNOT heal the captured wedge. The fix's query is broken.

### R1 PASS criteria
| Criterion | Status | Notes |
|-----------|--------|-------|
| a. Parent leaves waiting_children + synthesizes helloR1r | **FAIL** | Parent stuck (lane 6 didn't process) |
| b. ZERO manual pings | **PASS** | No manual pings between kill and observation |
| c. EXACTLY ONE internal_report row | **PASS** | 1 row (status=ready, PENDING inj) |
| d. Child NOT re-executed | **PASS** | Child has 1 task row, completed |
| e. Lane-6 marker + result counters | **PARTIAL** | Lane 6 boot marker present, but ZERO per-row processing (query bug) |

---

## R2 (Kill-Switch OFF) — OPERATIONAL GAP

Per Advisory: lane 6 kill-switch has NO config.py env var.

| Check | Result |
|-------|--------|
| `grep config.py lane_stuck_wake` | empty (no env var) |
| `grep config.py stuck_wake` | empty (no env var) |
| pool_orchestrator.py instantiation | without kwarg (defaults ON) |
| Lanes 1-5 env vars | each have `report_delivery_recovery_lane_*` env |
| Lane 6 env var parity | **MISSING** |
| Unit tests with `lane_stuck_wake=False` | `test_report_delivery_recovery_pg.py:1095,1195`; `test_report_delivery_recovery_service.py:323` |
| Unit test result | `pytest tests/unit/test_report_delivery_recovery_service.py -k disabled` → **2 passed, 37 deselected** |
| PG test result | PG harness not available in this env |
| Boot with lane 6 OFF via env | **IMPOSSIBLE** (no env var) |

**GATE FINDING (per Advisory):** "kill-switch-OFF not operator-exercisable as-deployed — env-var parity with lanes 1-5 missing (config.py follow-up needed)"

---

## R3 (Multi-Child Mixed) — INCOMPLETE

- child1 (3s): completed + TASK_DELIVERED naturally
- child2 (35s): poller missed the wedge
  - v1 poller looked at LATEST process_report (child1's, completed)
  - v2 poller crashed (empty DAEMON_PID)
  - child2 completed naturally
- Daemon crashed (pre-existing DependencyBus bug — R0 finding)

**Gap:** cannot test multi-child lane 6 behavior because:
1. Poller didn't target child2 specifically
2. Daemon crashed before v2 poller could start
3. Child2 completed before any wedge could be captured

---

## R4 (Lane-6 Negatives)

### R4(i) No duplicate execution
- RUNTIME: cannot verify (lane 6 never processed — R1 query bug)
- CODE-LEVEL: `force_cancel_and_schedule_retry` at task/repository.py:5094
  - `max_retries=3` (bounded, caller at report_delivery_recovery.py:905)
  - `backoff_base=60s, backoff_max=3600s` (production defaults)
  - `next_retry_at = now + backoff` (per f53a0638 §3)
  - **same-message_id contract**: retry task carries SAME message_id as original wake (per f53a0638 review close-out §3, verified by PG test claimability pin at test_report_delivery_recovery_pg.py ~2475)
  - **retry_count bounded** by the primitive's own logic (not the lane caller)

### R4(ii) Manual-ping deadlock GONE in healed state
- Parent 2acdf6b2 + child 474f4c8c (normal completion, 15s sleep)
- Parent healed naturally at 04:52:48
- Manual ping at 04:53:28 → normal response
- Parent completed again at iter=7 (~14s)
- **VERDICT: deadlock IS GONE in healed state**

**Caveat:** the healed state test uses NATURAL healing, not lane 6 healing. If lane 6 had healed the parent, the retry task would be in PENDING with next_retry_at in the future — a different busy-guard state. The test as-designed only verifies manual-ping in the post-natural-heal state, not the post-lane-6-heal state.

---

## §12e Decisions.md Structural Corruption (preflight-cited)

At `decisions.md:1280` (from f53a0638), there is a stray orphan header:
```
The §12a window-to-lane maps
 — Additive Lane 6 (Block-1 G4 merge-gate, 2026-10-05)
```

The first line is NOT a `##` header (it's a continuation fragment) but appears as a standalone line. This is a docs-only formatting issue (markdown structure). Evidence in `db-assertions/decisions-12e-corrupt.txt`.

---

## DECISIVE GATE VERDICT

**The fix does NOT heal the captured wedge.** The `find_stuck_wake_candidates` query has a data-shape mismatch: it joins `message_queue.message_id = report_injections.child_message_id`, but these are different values in practice (wake message vs child's message). Lane 6 is enabled but finds 0 candidates, so no heal occurs. The parent remains stuck.

**Operator-reachable kill-switch is MISSING.** Lane 6 has no env var in config.py. Lanes 1-5 have `report_delivery_recovery_lane_*` env vars; lane 6 diverges. Boot with lane 6 OFF is impossible as-deployed.

**R4(ii) manual-ping in healed state works** (parent healed naturally, ping processed normally, no deadlock).

**Recommendations for follow-up:**
1. Fix the query join: match on `mq.source LIKE 'internal_report:<child_iid>:<child_message_id>'` instead of `mq.message_id = ri.child_message_id`
2. Add `lane_stuck_wake` env var in config.py with `env_prefix="SERVICES_"` for parity with lanes 1-5
3. Fix the §12e decisions.md orphan header (docs-only)
