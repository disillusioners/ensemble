# Plan Overview: Fix Pause-During-Report-Turn Orphan JobItem

> **Revision 2 (2026-08-01):** Major revision per active council review + architecture assessment. All orphan correlation re-keyed from `message_id` to `work_id` (prevents retry-cycle deadlock reproduction). 7 Phase 2 criticals (C1-C7) addressed. 9 warnings (W1-W9) incorporated. Added follow-up turn-reconciler bridge document. Hybrid bridge strategy: point-fixes ship now, reconciler migration is planned follow-up.

Date: 2026-08-01
Status: Ready for Review (Revision 2)
Bug Report: `docs/bugs/pause-during-report-turn-orphans-message-jobitem.md`
Planning Instance: planner[v2]
Workers: plan-worker-bug-a (d859386b), plan-worker-bug-b (80079d89), revise-worker-p1 (127985a3), revise-worker-p2 (e591e2b5), revise-worker-followup (c6af2ce0)

---

## Objective

Fix a production incident where a leader instance permanently deadlocked after an `ask_questions` pause fired mid-`process_report` turn. The original `process_message` task had already completed naturally (instance at `WAITING_CHILDREN`), and only a `process_report` task was in-flight when pause fired. This produced **two compounding bugs** with **4 root causes** across the most critical paths of the system:

- **Bug A (P0 — deadlock):** Orphaned `active` JobItem → permanent resume deadlock (RC1 routing gap + RC2 guard gap)
- **Bug B (P1 — stuck terminal state):** Orphaned `processing` `message_queue` rows block final COMPLETED transition (RC3 cascade gap + RC4 guard gap)

Both bugs stem from the same root: **the pause/resume cascade does not fully clean up the in-flight report turn's state.** Bug A orphans the `job_queue_items` mirror; Bug B orphans the `message_queue` row. Neither has a `process_report`-turn equivalent of the existing `process_message`-turn stale-message cleanup.

---

## Scope Assessment

**LARGE** — 4 root causes across 2 compounding bugs, touching ~6 production files across the daemon layer. The two bugs are largely independent (different tables, different failure modes) but share the same causal origin. The fix must preserve three critical design invariants: F1 bifurcation, Report-Lane Decoupling, and dual-driver (SQLite + PostgreSQL) compatibility.

---

## Hybrid Bridge Strategy (Council Decision)

The council directed a **hybrid bridge strategy**:
1. **Ship corrected point-fixes NOW** to unblock the production deadlock
2. **Track turn-reconciler migration as a planned follow-up** (separate effort)
3. **Point-fixes are NOT throwaway** — they establish the `work_id`-keyed correlation axis, the positive-polarity orphan-detection pattern, and the shared predicate helper that the reconciler will subsume

See [follow-up-turn-reconciler.md](./follow-up-turn-reconciler.md) for the bridge mapping.

---

## Critical Revision: `work_id` Re-Keying (Council Review)

The original plan (Revision 1) correlated orphan detection via `message_id`. The council review proved this would **reproduce the deadlock** via an automatic code path:

- `schedule_retry` (`repository.py:1793-1935`) mints a FRESH `work_id` but REUSES the parent's `message_id`
- A `NOT EXISTS` carve-out keyed on `message_id` finds the fresh PENDING retry Task and blocks it
- Result: the same deadlock via automatic retry cycle (~10 min)

**The fix:** All orphan correlation uses `Task.work_id == JobItem.job_id` (direct column join, no JSON extraction). This is already the primary linkage at `repository.py:640-645` and the linkage contract at `instance_messaging.py:1218-1222`.

For Phase 2 (message_queue reconciliation), `message_queue` has no `work_id` column. The correlation uses a two-path approach: (1) direct `processing_task_id → Task.id → work_id` (authoritative), (2) `message_id` as a candidate locator when `processing_task_id` is NULL, projected to `work_id`s with mixed states preserved.

---

## Bug Summary

### Bug A — Orphaned `active` JobItem → Permanent Resume Deadlock (P0)

| Root Cause | Location | Failure |
|---|---|---|
| **RC1 — Routing Gap** | `manager.py:4844-4912`, `repository.py:171-244` | `find_paused_or_running_by_instance` filters on `PROCESS_MESSAGE` only; when the original `process_message` Task is already `completed`, it returns `None` → resume misroutes to child branch → JobItem never finalized |
| **RC2 — Guard Gap** | `repository.py:646-765`, `:1408-1520` | Cross-system guard blocks fresh `process_message` Task when instance has `active` JobItem; 2 carve-outs exist but neither fires for `(active JobItem, terminal Task via work_id)` → permanent deadlock |

### Bug B — Orphaned `processing` `message_queue` Rows Block Final COMPLETED Transition (P1)

| Root Cause | Location | Failure |
|---|---|---|
| **RC3 — Cascade Gap** | `instance_lifecycle.py:3293-3534` | `_resume_cascade_db_sync` updates only `instances` + `task` + `job_queue_items`; never touches `message_queue` → `processing` rows become permanent orphans |
| **RC4 — Guard Gap** | `child_reports.py:1459-1469` (reachable), `:863, :2058`, `error_reporting.py:270` (bus-gated fallbacks) | Root-completion `pending_count` guard counts `processing`/`retrying` rows with no join to `task` → cannot distinguish in-flight from orphaned → instance stuck at `WAITING_CHILDREN` forever |

---

## Phase Index

| Phase | Name | Objective | Dependencies | Est. Time | Plan File |
|-------|------|-----------|-------------|-----------|-----------|
| **1** | Bug A — Deadlock Fix (Step A: guard hardening + Step B: routing fix) | Close RC1 + RC2; unblock resume and cleanly finalize the orphaned JobItem | None (ship first) | 7–9 days | [phase1-plan.md](./phase1-plan.md) |
| **2** | Bug B — Stuck Terminal State Fix (2.A: cascade reconciliation + 2.B: guard hardening + 2.5: cleanup) | Close RC3 + RC4; reconcile orphaned `message_queue` rows and make completion guard robust to any orphan class | Phase 1 recommended first | 5–6 days | [phase2-plan.md](./phase2-plan.md) |
| **Follow-up** | Turn-Reconciler Migration | Subsume all point-fixes into unified reconciliation primitive | After Phase 1+2 ship | Multi-week | [follow-up-turn-reconciler.md](./follow-up-turn-reconciler.md) |

> **Sequencing rationale:** Bug A (deadlock) is more urgent than Bug B (stuck terminal state). Phase 1 should ship first. Within each phase: Step A before Step B (Phase 1); 2.A before 2.B before 2.5 (Phase 2).

---

## Phase 1 Detail: Bug A — Deadlock Fix

### Step A — Guard Hardening (ship FIRST)
Broaden the orphan-exclusion carve-out in `claim_pending_task` (`repository.py:742-763`) to cover `active` JobItems whose backing Task (correlated via `work_id == job_id`) is terminal. Use a shared SQL predicate (no JSON extraction — direct column join). Mirror in `has_pending_tasks_blocked_by_busy_instance` (`:1465-1500`) with required `status_paused` bind expansion (W1). (Tasks A1–A5, ~3 days)

### Step B — Resume Routing Fix (ship SECOND)
Add repository primitive `find_resume_root_candidate_by_active_job` (correlates via `work_id == job_id`). Update `resume_processing_job` to use it as fallback → takes root branch → `_process_resume_finalize` transitions JobItem `active → done` via exact-ID overload (W2). (Tasks B1–B8, ~4–6 days)

**Key amendment (Revision 2):** All correlation via `work_id == job_id`, NOT `message_id`. F1 race-window trade-off explicitly accepted and documented (W3). Expanded test matrix including retry-scenario regression (W4).

---

## Phase 2 Detail: Bug B — Stuck Terminal State Fix

### Phase 2.A — Cascade Reconciliation (structural fix)
Add UPDATE 4 to `_resume_cascade_db_sync` scoped to Tasks cancelled by THIS cascade via `RETURNING` (C4). PostgreSQL uses a data-modifying CTE; SQLite captures returned rows (C3 concurrency model). Placed after UPDATE 2, before UPDATE 3 (W5). Restricted to `completion_report` type only (C5). (Tasks 1–5, 9, ~2 days)

### Phase 2.B — Completion-Guard Hardening (defense-in-depth)
Create one shared positive-polarity SQLAlchemy predicate (W8). Apply to `child_reports.py:1459` (reachable production site) + 3 bus-gated fallbacks (`:863`, `:2058`, `error_reporting.py:270`). Categorized all 8 sites — 4 parent-completion (1 reachable + 3 fallbacks), 4 child-report-decision (unchanged). (Tasks 6–8, 10, ~2 days)

### Phase 2.5 — Production Cleanup (W9)
Dry-run-first one-shot cleanup script for existing stuck instances. Requires `--instance-id`, mirrors bug-report remediation SQL. (Task 16, ~0.5 days)

**Key amendments (Revision 2):**
- C1: SQL polarity CORRECTED — positive condition (count when no-Task OR non-terminal exists; exclude only when all terminal)
- C2: No-Task rows PRESERVED (not finalized)
- C3: WriteGuardSession described accurately (all-or-nothing commit, NOT mutex); RETURNING-scoped CTE for concurrency
- C4: UPDATE 4 scoped to THIS cascade's RETURNING Tasks (not tree-wide sweep)
- C5: Restricted to `completion_report` only
- C6: 4 parent-completion sites identified (1 reachable + 3 fallbacks); 4 child-decision sites audited but unchanged
- C7: PG tests under `tests/postgres/` using `pg_engine`/`pg_two_connections`; shared scenario builders in `tests/helpers/`

---

## Design Tensions Preserved

| Tension | What It Is | How the Plan Preserves It |
|---|---|---|
| **F1 bifurcation** (2026-07-06) | Deliberately split carve-out branches to prevent a `completed` Task from releasing an `active` JobItem prematurely | Carve-out requires absence of `PENDING`/`RUNNING`/`PAUSED` backing Tasks (via `work_id`); negative tests pin this. F1 race-window (<1s) explicitly accepted as trade-off (W3). |
| **Report-Lane Decoupling** (2026-06-24) | `PROCESS_REPORT` deliberately bypasses cross-system guards | Does NOT broaden `find_paused_or_running_by_instance` to include `PROCESS_REPORT`. Report bypass tests unchanged. |
| **PostgreSQL PRIMARY** | PostgreSQL is the default dev/test DB | PG tests under `tests/postgres/` using existing fixtures. CI gate provisions DB before pytest (C7). |
| **Dual-driver** | All DB changes must work on SQLite and PostgreSQL | No `rowid`, no SQLite-only syntax. Shared scenario builders work with any engine. PostgreSQL CTE + SQLite RETURNING-capture branches. |

---

## Shared Pattern: `work_id`-Keyed Orphan Detection

Both phases converge on the same architectural pattern: orphan detection via `work_id` correlation with a positive-polarity condition.

**Phase 1 (JobItem → Task correlation):**
```sql
-- An active/queued JobItem is an orphan when no Task with
-- task.work_id = job_queue_items.job_id is non-terminal:
NOT EXISTS (
    SELECT 1 FROM task _orphan_check
    WHERE _orphan_check.work_id = j.job_id
      AND _orphan_check.status IN ('pending', 'running', 'paused')
)
```

**Phase 2 (message_queue → Task correlation, two-path):**
- Direct path: `processing_task_id → Task.id → work_id`
- NULL fallback: `message_id` as locator → project candidate `work_id`s → evaluate status
- Positive condition: count when `(no Task) OR (non-terminal exists)`; exclude only when `(Task exists) AND (all terminal)`

The `PAUSED` inclusion in the live-set is critical: a `PAUSED` Task may be re-armed by a later resume, so it is NOT an orphan.

---

## Risks & Mitigations (Cross-Phase Summary, Revised)

| Risk | Phase | Impact | Mitigation |
|------|-------|--------|------------|
| `message_id`-keyed correlation reproduces deadlock via retry path | 1+2 | **CRITICAL** | All correlation re-keyed to `work_id`; retry-scenario regression test (W4 case 1) |
| F1 race-window — active+terminal while parent mid-`astream` | 1 | High | Accept <1s window (W3); scope to fresh `PROCESS_MESSAGE` candidates; negative tests |
| Claim/busy-probe divergence (P1/F11) | 1 | High | Shared SQL helper; `status_paused` bind expansion on busy-probe (W1) |
| Double-finalize race | 1 | High | Conditional atomic transition; exact-ID overload threading (W2); contention test |
| SQL polarity inversion (guard counts orphans instead of excluding) | 2 | **CRITICAL** | Positive condition replaces `NOT EXISTS`; truth-table test precedes implementation (C1) |
| No-Task rows finalized (data loss) | 2 | **CRITICAL** | Require `EXISTS(terminal) AND NOT EXISTS(non-terminal)`; RETURNING-scoped UPDATE 4 (C2/C4) |
| WriteGuardSession mistaken for mutex | 2 | High | Accurate concurrency docs; RETURNING CTE scope; two-connection PG race test (C3) |
| UPDATE 4 reconciles historical incidents | 2 | High | Scoped to THIS cascade's RETURNING Tasks (C4) |
| Dropping unconsumed reports | 2 | Critical | Restrict to `completion_report` only (C5); preserve content for audit |
| Dual-driver SQL incompatibility | 1+2 | High | Existing helpers; dual-tree CI gate (C7); engine-agnostic scenario builders |
| Existing stuck instances | 2 | Medium | Phase 2.5 cleanup script with dry-run-first (W9) |

---

## Defense-in-Depth Philosophy

Both phases implement **cause + symptom** fixes for defense-in-depth:

| Bug | Cause Fix (prevents new orphans) | Symptom Fix (robust to any orphan class) |
|---|---|---|
| **A** | Step B: routing fix explicitly finalizes the JobItem | Step A: guard hardening admits any orphaned active JobItem |
| **B** | 2.A: cascade reconciliation resets orphaned rows | 2.B: guard hardening excludes terminal-backed rows from count |

The symptom fixes (Step A, 2.B) make the system robust to orphaned state under *any* failure mode. The cause fixes (Step B, 2.A) prevent the orphan from being created in the first place.

---

## Success Criteria

### Phase 1 (Bug A) — 12 criteria
- [ ] `claim_pending_task` admits fresh `PROCESS_MESSAGE` when `active` JobItem backed by terminal Task (via `work_id`)
- [ ] Guard blocks when backing Task is PENDING/RUNNING/PAUSED (no F1 regression)
- [ ] Claim and busy-probe agree (P1/F11 invariant); busy-probe binds `status_paused` (W1)
- [ ] Report-lane semantics unchanged
- [ ] Report-turn-pause resume selects root route (no `cascade_resume` artifact)
- [ ] `work_id` threaded to `_process_resume_finalize` via exact-ID overload (W2)
- [ ] Orphaned JobItem explicitly finalized + slot released
- [ ] Finalization is race-safe
- [ ] Retry-scenario regression: parent CANCELLED + retry child PENDING (same `message_id`, different `work_id`) → answer Task admitted (W4)
- [ ] Multi-JobItem-per-instance evaluates each independently (W4)
- [ ] E2E `test_pause_during_report_turn_then_resume` passes 10/10 PostgreSQL runs
- [ ] All existing pause/resume, report-lane, cold-resume suites pass

### Phase 2 (Bug B) — 15 criteria
- [ ] Truth-table tests pass for positive guard polarity on both DBs (C1)
- [ ] No-Task rows preserved/counted, never reconciled (C2)
- [ ] Retry attempts distinguished by `work_id` (mixed-attempt rows preserved)
- [ ] UPDATE 4 scoped to THIS cascade's RETURNING Tasks (C4)
- [ ] Only `completion_report` rows reconciled (C5)
- [ ] UPDATE 4 precedes UPDATE 3 (W5)
- [ ] PostgreSQL two-connection race test: no forbidden outcomes (C3)
- [ ] Exact production state reaches `COMPLETED` (W7)
- [ ] All 4 parent-completion sites use shared predicate (W8, C6)
- [ ] Child report-decision queries unchanged (C6)
- [ ] End-to-end pause-during-report-turn reaches `COMPLETED`
- [ ] Defense-in-depth: broadened guard alone resolves orphan condition
- [ ] All-or-nothing commit preserved (inject-error rollback test)
- [ ] Dual-driver CI: both SQLite and PostgreSQL pass (C7)
- [ ] Phase 2.5 cleanup: dry-run performs 0 writes (W9)

---

## Open Questions / Follow-ups

1. **`message_queue.work_id` column** — adding it would eliminate the NULL-fallback ambiguity in Phase 2. Not required for this fix; tracked as a future schema improvement.
2. **Durable consumption marker** — until the graph/checkpoint records that report content was consumed, UPDATE 4 must stay restricted to `completion_report`.
3. **Fallback site deletion** — the 3 bus-gated fallback guards are hardened, not removed. A separate cleanup can delete them after tests no longer depend on fallback behavior.
4. **Streaming error root cause** (Option C) — the proximate trigger remains a separate investigation.
5. **Turn-reconciler migration** — see [follow-up-turn-reconciler.md](./follow-up-turn-reconciler.md) for the bridge strategy and point-fix mapping.

---

## Tracking
- Created: 2026-08-01
- Last Updated: 2026-08-01 (Revision 2 — council review amendments applied)
- Status: Ready for Review
