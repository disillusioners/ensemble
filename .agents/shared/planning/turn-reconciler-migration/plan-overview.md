# Turn-Reconciler Named Transitions Migration — Plan Overview

| Field | Value |
|---|---|
| **Date** | 2026-08-01 |
| **Status** | PLAN REVISED (v4) — Approver Iteration 002 SQL defects resolved; Inc 1+2 awaiting re-approval, Inc 3+4 APPROVED |
| **Scope** | HUGE — 4-increment architectural migration, 4,600 lines of plan artifacts |
| **Goal** | Make the "cascade-forgets-a-mirror" bug class structurally impossible by introducing a turn reconciler, named transitions, a turn suspension handle, and carve-out deletion |
| **Design Doc** | `docs/plans/turn-reconciler-named-transitions.md` (440 lines) |
| **Bug Origin** | `docs/bugs/pause-during-report-turn-orphans-message-jobitem.md` (2026-08-01, Bug A + Bug B) |
| **Regression Baseline** | 404 existing tests must pass throughout all increments |

---

## Review History

| Version | Reviewer | Outcome |
|---------|----------|---------|
| v1 | Initial plan | 5 workers dispatched, 6 artifacts created |
| v2 | Council (2 reviewers) | 8 blockers + 10 warnings → all resolved |
| v3 | Approver (Iter 001) | 5 blocking issues in Inc 1+Inc 2 → all resolved. Inc 3+Inc 4 APPROVED |
| **v4** | **Approver (Iter 002)** | **3 SQL correctness defects in Inc 1 §4 handlers → all resolved** |

---

## Approver v4 Fixes Applied (3 SQL correctness defects)

| Issue | Increment | Fix |
|-------|-----------|-----|
| **#1** | Inc 1 | Mirrors #3/#4/#5: bare `EXISTS` → `(:task_exists = false OR EXISTS(...))` — prevents orphan survival when Task row is missing |
| **#2** | Inc 1 | Mirror #6 (`report_injections`): TODO prose → concrete SQL `SET state = 'TASK_DELIVERED' WHERE state = 'PENDING' AND (:task_exists = false OR ...)` |
| **#3** | Inc 1 | Mirror #8 (`job_watchers`): prose → concrete SQL `DELETE ... WHERE NOT EXISTS(task)` — only deletes when Task row gone entirely (not terminal, to protect retry children) |

---

## 1. Executive Summary

The agents-ensemble queue/task/job system stores the same logical fact ("this turn is in flight / paused / done") across **8 mirror tables** whose updates are hand-written SQL statements, each touching a different subset. Every new lifecycle event re-selects a subset and leaves the rest orphaned — producing production incidents discovered weeks later.

This migration introduces four structural changes, each independently shippable, that together make "the cascade forgot table X" a structurally impossible class:

1. **A turn reconciler** (`reconcile_turn_mirror(work_id)`) — always runs all 8 handlers; no fast-path skip; WAITING_CHILDREN exception for `job_queue_items`
2. **Carve-out deletion** — ~230 lines → ~7 lines (WAITING_CHILDREN retained); coherent two-tier rollback
3. **Named transitions** — cascades become thin wrappers; 3 chokepoints (`complete_task`/`cancel_task`/`fail_task`) routed through transitions (D8)
4. **Turn suspension handle** — resume targets turn by ID; legacy backfill; guarded SQLite migration

### 8 Mirror Tables (corrected from design doc's 5)

| # | Table | Reconciliation | Notes |
|---|-------|----------------|-------|
| 1 | `task` | Authority — reconciler reads status as snapshot | |
| 2 | `job_queue_items` | Terminal → done; **EXCEPT waiting_children instances** (D13) | JobItem stays active for child-completion semaphore |
| 3 | `message_queue` | Terminal → completed | `processing_task_id` is dead code, write is defensive |
| 4 | `job_locks` | Terminal → DELETE | |
| 5 | `dependency_watchers` | Cancel pending when target INSTANCE has no in-flight tasks | Instance-scoped (C1 fix), NOT task-scoped |
| 6 | `report_injections` | Terminal → reconcile injection state | |
| 7 | `instances` | SOFT — verify-and-flag only (D11) | Tree-scoped, not per-turn |
| 8 | `job_watchers` | Terminal → clean dangling subscriptions | |

---

## 2. Increment Sequencing & Dependencies

```
Inc 1 (Reconciler + Invariant + Property Tests) ← v4, awaiting re-approval
 │
 ├──→ Inc 2 (Delete Carve-Out Pile) ✅APPROVED ─┐
 │    (WAITING_CHILDREN RETAINED)                │
 │                                               ├──→ [Migration Complete]
 └──→ Inc 3 (Named Transitions) ✅APPROVED       │
     (fail_task in D8 chokepoint)                │
          │                                      │
          └──→ Inc 4 (Turn Handle) ✅APPROVED ───┘
               (backfill migration)
```

| Increment | Status | Depends On |
|-----------|--------|------------|
| **Inc 1** | v4 revised — awaiting Approver re-approval | None |
| **Inc 2** | ✅ **APPROVED** (v3) | Inc 1 (hard gate, 8 items) |
| **Inc 3** | ✅ **APPROVED** (v2) | Inc 1 (hard gate) |
| **Inc 4** | ✅ **APPROVED** (v2) | Inc 3 (hard gate) |

---

## 3. Increment Summaries (v3)

### Increment 1 — Reconciler + Invariant + Property Tests (v4)
**File:** `increment1-plan.md` (850 lines) | **Status:** v4 revised, awaiting re-approval

- `reconcile_turn_mirror(work_id)` — **always runs all 8 handlers** (no fast-path skip)
- **WAITING_CHILDREN exception** in `job_queue_items` SQL (D13): JobItem stays `active` for `waiting_children` instances
- **Claim ordering** (§5.2, D14): reconciler runs AFTER guard — by design; relies on cascades + periodic sweep
- **Property tests with corruption injection** (Issue 5): `CORRUPT_MIRROR` command + 6 directed corruption scenarios
- 6 verified call sites; Phase 5 Python-side invariant; Phase 9 Hypothesis state machine
- dependency_watchers: `target_instance_id` (instance-scoped, C1 fix)

### Increment 2 — Delete Carve-Out Pile (APPROVED, v3)
**File:** `increment2-plan.md` (635 lines) | **Status:** ✅ APPROVED

- Deletes 3 carve-out blocks (~230 lines); replaces with ~7-line helper (WAITING_CHILDREN retained)
- **Coherent two-tier rollback** (Issue 4): Tier 1 = full git revert (all protections); Tier 2 = no rollback (divergence is correct)
- W4 retry-regression hard gate; forward-compatibility test (C8)

### Increment 3 — Named Transitions Refactor (APPROVED, v2)
**File:** `increment3-plan.md` (1,402 lines) | **Status:** ✅ APPROVED

- 7 named transitions with `MIRROR_SET` frozensets; 3 D8 chokepoints (`complete_task`/`cancel_task`/`fail_task`)
- Full caller map (22 sites); write guard (C7); feature flag (C9); phased migration (4a/4b/4c)

### Increment 4 — Turn Handle + Routing (APPROVED, v2)
**File:** `increment4-plan.md` (726 lines) | **Status:** ✅ APPROVED

- 2 new columns (triple-registration); legacy backfill; guarded SQLite migration; composite index; full-chain E2E

---

## 4. Design Decisions (D1–D14)

| ID | Decision | Status | Increment |
|----|----------|--------|-----------|
| D1 | Reconciler covers 8 mirror tables | Accepted | Inc 1 |
| D2 | `work_id` is authoritative axis | Accepted | All |
| D3 | Reconciler starts additive | Accepted | Inc 1→2 |
| D4 | 3 REPLACE / 2 COEXIST | Accepted | Inc 1,2,4 |
| D5 | Independently shippable increments | Accepted | All |
| D6 | Python-side runtime invariant check | Accepted | Inc 1 |
| D7 | Triple-registration for new columns | Accepted | Inc 4 |
| D8 | `complete_task`/`cancel_task`/**`fail_task`** chokepoints | Amended | Inc 3 |
| D9 | WAITING_CHILDREN RETAINED | Resolved | Inc 2 |
| D10 | Property test asserts all 8 tables | Accepted | Inc 1 |
| D11 | `instances` soft reconciliation | Accepted | Inc 1 |
| D12 | No table merge | Accepted | — |
| D13 | WAITING_CHILDREN is instance-lifecycle, not mirror-consistency | Accepted (v3: exception in Inc 1 SQL) | Inc 1+2 |
| **D14** | **Claim-time reconciler runs AFTER guard (by design)** | **NEW (v3)** | Inc 1+2 |

---

## 5. File Index (v4)

| File | Lines | Status |
|------|-------|--------|
| `plan-overview.md` | This file | v4 |
| `increment1-plan.md` | 850 | v4 — awaiting re-approval |
| `increment2-plan.md` | 635 | ✅ APPROVED (v3) |
| `increment3-plan.md` | 1,402 | ✅ APPROVED (v2) |
| `increment4-plan.md` | 726 | ✅ APPROVED (v2) |
| `decisions.md` | 842 | v3 (D14 added, D13 annotated) |
| **Total** | **4,645** | |

---

## 6. Next Actions

1. **Submit Inc 1 + Inc 2 for Approver re-review** — all 5 blocking issues resolved
2. **Inc 3 + Inc 4 are APPROVED** — implementation can proceed once Inc 1 lands
3. Update design doc §4.1 to reflect 8-table mirror set
