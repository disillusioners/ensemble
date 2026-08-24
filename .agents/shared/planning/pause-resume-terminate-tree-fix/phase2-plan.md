# Plan Overview: Phase 2 — Pause/Resume/Terminate Tree-Propagation: B2+B3 (Watcher/Obligation Semantics)

Date: 2026-08-24
Author: planner[v2] via plan-creation worker
Status: Draft (architect review pre-flagged — see §Architect Flags)
Branch: feature/pause-resume-terminate-tree-fix @ e6007b8a (worktree)
Bug Source: `.agents/tester/RESULTS/2026-08-24-pause-resume-terminate-tree-propagation-repro.md` (live-repro evidence, 6 phases)
Companion plans: `.agents/shared/planning/pause-report-recovery/plan-overview.md` (v3.1, READ-ONLY contract pattern precedent), `.agents/shared/planning/fix-pause-report-turn-orphan/plan-overview.md` + `decisions.md` (hybrid bridge precedent, work_id-keyed correlation, point-fix + reconciler subsumption)

---

## Defects In Scope

| # | Defect | Severity | Live Evidence |
|---|--------|----------|---------------|
| **B2** | **Resume strands root when children completed DURING pause** | 🔴 critical | `phase 6a`: resume returns `200 {"resumed":true,"status":"no_active_job"}` + `route_outcome=invalid_or_missing_handle`; `_compact_fired_watchers_for_paused` deletes FIRED watchers; buffered child reports stranded in `report_injection`; msg count frozen 25→25; root never reaches terminal state until a NEW external message drains the buffer (log 2727–2731) |
| **B3** | **Terminate has no UP propagation** | 🔴 critical | `phase 6b-mid`: `DELETE mid_tree_child` cancels grandchild correctly ✅, but parent's watcher on the terminated child is CANCELLED (not FIRED-with-outcome); parent logs `waiting for 1 children (bus=True), deferring completion` forever on a ghost child (log 3335); drift reconciler silent |

**OUT of scope:** B1 (lineage enumeration — Phase 1 fix), B4 (terminate DOWN enumeration — Phase 1 fix), B5 (router defect — separate phase), B6 (memory wipe — separate phase), B7 (timestamp anomalies — separate phase), SSE (separate phase).

---

## Objective

A parent's completion obligation to its descendants survives pause/resume/terminate transitions: child completions that arrived during pause MUST be delivered (B2), and a mid-tree terminate MUST cause the parent's completion to fire with a terminal outcome (B3). Both defects share one root — the DependencyBus watcher lifecycle does not preserve obligation semantics across pause→resume and terminate paths. The fix preserves the bus's SOLE-completion-authority invariant and integrates with the pause-report-recovery `DEFERRED` state machine without duplicating marker logic.

---

## Verified Mechanics (re-cited from research-obligations input + code re-verification)

All citations re-verified against the worktree at branch head `e6007b8a` before writing this document. Page/line numbers are stable.

### Watcher state machine

| File:Line | Symbol | Verified role |
|-----------|--------|---------------|
| `daemon/repositories/dependency_bus/models.py:49-71` | `DependencyWatcherState` (PENDING / FIRED / CANCELLED) | All three terminal; no fourth state exists today. State strings UPPERCASE per the case-lockstep convention (precedent: pause-report-recovery C1) |
| `daemon/repositories/dependency_bus/repository.py:462-536` | `transition_state` (guarded `UPDATE … WHERE state='PENDING'`) | Backpressure primitive — `rowcount==0` means "another actor already transitioned"; exactly-once |
| `daemon/services/dependency_bus.py:551-720` | `emit_terminal(task_id, outcome)` | Fires PENDING→FIRED for every watcher on `task_id`; returns FollowUps for caller to enqueue; runs `outcome.status=='error'` sticky-parent-error logic; does NOT currently accept any outcome that would resolve a parent waiting on a TERMINATED child |
| `daemon/services/dependency_bus.py:1025-1098` | `cancel_for_target(target_instance_id)` | PENDING→CANCELLED for every watcher whose target matches; purges matching cache entries. Used by BOTH pause (optional) AND terminate (mandatory) at `instance_lifecycle.py:74` and `instance_lifecycle.py:1775` |
| `daemon/services/dependency_bus.py:996-1018` | `count_pending_for_target_sync` | Counts PENDING ONLY — FIRED and CANCELLED don't count. This is B3's gating function: `child_reports.py:1841-1842` waits on `count_pending_for_target_sync(...) == 0` |
| `daemon/repositories/dependency_bus/repository.py:351-411` | `mark_enqueued_by_source_target(source, target, ...)` | Stamps `enqueued_at` after FollowUp enqueue; dedup marker for restart's `_recover_fired_unsent` (1554-1606) |

### Resume cascade and watcher interaction

| File:Line | Symbol | Verified role |
|-----------|--------|---------------|
| `daemon/services/instance_lifecycle.py:2269-2340` | `resume_instance_cascade` | Enumerates tree via `get_tree_ids` (B1 lineage defect — Phase 1, OUT of this phase); filters nodes to `status == PAUSED`; for each, `_resume_cascade_db_sync` |
| `daemon/services/instance_lifecycle.py:3698-3856` | `_resume_cascade_db_sync` | Single-transaction UPDATE cascade. `ResumeTurn` flips `PAUSED → PENDING` at `3821-3826` — BLIND flip (no `job_queue_items` check) — drift risk in scope as sub-fix |
| `daemon/services/instance_lifecycle.py:3608-3696` | `_compact_fired_watchers_for_paused` | DELETE FIRED rows where `enqueued_at IS NOT NULL AND fired_at <= cutoff`. Critical: this is what DESTROYS the buffered reports' wake signals in B2 — the FIRED rows that `mark_enqueued` already stamped are gone before the parent turn runs again |

### Report obligation state machine (pause-report-recovery artifact)

| File:Line | Symbol | Verified role |
|-----------|--------|---------------|
| `daemon/repositories/report_injection/models.py:40-83, 299` | `ReportInjection`, `ReportInjectionState` (PENDING/DEFERRED/INJECTED/TASK_DELIVERED) | Pause-report-recovery Phase 1 already added `DEFERRED` and the partial unique index `uq_report_injections_oblig_triple` (migration 20260624_000004). DEFERRED→INJECTED/TASK_DELIVERED forbidden by design — only DEFERRED→PENDING is legal |
| `daemon/graph.py:275-300` | graph-node drain (`repo.claim_for_injection(instance_id)`) | Hot-path drain — invoked at graph dispatch. PAUSED instances skip drain (RAM hint `manager._report_injection_pending` checked first) |
| `daemon/services/report_delivery_recovery.py` | `ReportDeliveryRecoveryService` (5 lanes) | Periodic sweep. Phase 2 of pause-report-recovery already proved the design; B2's recovery reuses this infrastructure rather than inventing a parallel one. **Open: read `daemon/services/report_delivery_recovery.py` end-to-end before phase ships; enumerate the 5 lanes from the file's lane_* methods and verify the no-row backstop handles the B2 shape (FIRED+enqueued_at IS NULL rows for a paused parent)** |

### Revive semantics

| File:Line | Symbol | Verified role |
|-----------|--------|---------------|
| `daemon/services/instance_messaging.py:1486-1510` (read 1500-1530) | revive path | `send_message` to a TERMINATED instance reactivates it RUNNING via checkpoint reload (same machinery as reviving COMPLETED). **Open (architect):** if the parent's B3 fix fires a watcher with `outcome.status='terminated'` while the child is TERMINATED, AND `send_message` then revives the child, what happens to the FIRED obligation row + its FollowUp? |

---

## Scope

### In Scope (B2)
1. **Resume preserves buffered-report wake signals.** Pause→children complete→resume MUST deliver the buffered reports via the existing graph-node drain or its recovery equivalent. The current `_compact_fired_watchers_for_paused` deletes the rows BEFORE the parent has a chance to enqueue the FollowUp; the fix either preserves the rows, or delivers the buffered reports BEFORE compaction.
2. **Cancel-during-pause drift sub-fix.** The blind `PAUSED→PENDING` Task flip at `instance_lifecycle.py:3821-3826` does not check `job_queue_items`. Optional but composable — include if the fix is small; mark optional otherwise. Reconcile via the same `paused-on-terminal` reconciler pattern used at `job_recovery_service.py:528-534` (the precedent for terminal-on-resume).
3. **Architect review of obligation semantics** before any code lands (lineage duality + obligation semantics were pre-flagged — see §Architect Flags).

### In Scope (B3)
1. **Terminate fires (does not cancel) parent-side watchers.** When `DELETE mid_tree_child` runs, the parent's watcher on that child MUST transition to FIRED (with a terminal outcome, not the empty default), so the parent's `count_pending_for_target_sync(...) == 0` gate clears and completion fires. The fix proposes adding a new outcome to `emit_terminal` semantics — `outcome.status='terminated'` — and a sibling `cancel_for_target_with_outcome` (or method rename) that fires watchers with the outcome instead of cancelling them.
2. **Ghost-child detection on the parent side.** Even with B3 firing the watcher, the FollowUp that the parent waiting loop consumes must reference a real child — or the parent's completion logic must tolerate a terminated-child FollowUp. Detect-and-ack in the existing child-completion path at `child_reports.py:898/1244/1845` (the three idempotency guard sites per the live-repro evidence).
3. **Revive interaction.** If `send_message` revives the TERMINATED child before the parent's watcher fires, the obligation semantics must remain coherent — the FollowUp must still resolve the parent's wait, not silently double-deliver. (Architect flag — see below.)

### Out of Scope (do NOT touch in this phase)
- B1, B4 (lineage enumeration) — Phase 1 of this worktree, different defects, different plans
- B5, B6, B7 (router / memory / timestamp) — separate phases
- SSE delivery of cascade events — separate phase
- `emit_terminal` API redesign beyond the minimum needed for B3 — narrow the change; broader refactor is a follow-up
- New states on `DependencyWatcherState` — the fix uses existing PENDING/FIRED/CANCELLED with the outcome encoded in the FollowUp payload, not a new state (architect-decision; see §Architect Flags Q2)
- Schema changes — `report_injections` already has DEFERRED + `recovery_attempted_at` from pause-report-recovery Phase 1; no new columns needed
- Hard-delete cascade ordering (`hard_delete_instance` at `instance_lifecycle.py:1833`) — B3 is about soft terminate; hard_delete is a separate concern
- Drive-by refactors: pause gate, child_reports.py:898/1244/1845 idempotency bodies (we only ADD a new outcome branch; not rewrite existing logic)

---

## Recommended Approach (architect-decision binding)

### B2 — Recommended: **Deliver buffered reports BEFORE compaction** (Option 1 from research)

**Decision:** Modify `_compact_fired_watchers_for_paused` (`instance_lifecycle.py:3608-3696`) to FIRST iterate over FIRED rows where `enqueued_at IS NULL` (the buffered wake signals), re-enqueue each FollowUp via `manager.enqueue_message`, stamp `enqueued_at` via `mark_enqueued_by_source_target`, THEN run the existing DELETE pass.

**Invariant preserved:** Pause writes NOTHING to `job_queue_items` by design (Phase 4 invariant from the redesign, 2026-06-25); we do not violate that — we deliver via the bus's existing re-enqueue seam, not via JobItem creation.

**Justification vs alternatives:**
- **Option 2 (preserve all FIRED rows forever):** rejected — unbounded growth (Phase 2 Decision 3 / C3 reason). The 60s grace + enqueued_at stamp remains sound.
- **Option 3 (preserve only `enqueued_at IS NULL` rows):** rejected as a permanent policy — those rows belong to a FollowUp that has not yet been delivered; they are PAUSED on the bus's queue, not on the parent's queue. They WILL be re-enqueued on restart via `_recover_fired_unsent`. Preserving them on resume abandons the FollowUp-enqueue contract (the FollowUp is `manager.enqueue_message`'s responsibility, not the bus's).
- **Option 4 (don't compact at all):** rejected — same unbounded growth argument.
- **Option 1 (deliver-before-compact):** composes with the bus's existing `mark_enqueued_by_source_target` (which already accepts `enqueued_at=None`) and the `_recover_fired_unsent` restart path. The same code path runs on resume AND on restart — single invariant.

**Sub-fix (cancel-during-pause drift):** The blind `PAUSED→PENDING` flip at `instance_lifecycle.py:3821-3826` does not check whether a JobItem exists for the Task. This drift can produce `(JobItem CANCELLED, Task PENDING)` and a stranded work row — same shape as the B4 guard livelock. Compose by:
- Adding a guarded `WHERE NOT EXISTS (SELECT 1 FROM job_queue_items WHERE job_id = task.work_id AND admission_state IN ('queued','active'))` to the SELECT in `_resume_cascade_db_sync` (the SELECT at `3789-3810`). If a JobItem exists, the Task stays PAUSED and the resume cascade's active-resume seam (manager `_schedule_explicit_handle_resume` at `daemon/manager.py:6306-6527`) handles it under the existing FM-1 type-aware guard (pause-report-recovery Phase 2.3 same-PR mandate).
- Optional companion: a periodic reconciler that transitions stranded PAUSED Tasks (PAUSED + terminal JobItem) to a canonical terminal state, mirroring the `paused-on-terminal` precedent at `job_recovery_service.py:528-534`. **Mark optional**; include only if the guarded SELECT alone is insufficient (determined by test 2.4 below).

### B3 — Recommended: **Fire parent-side watcher with `outcome.status='terminated'`** (B3 Option 1 from research)

**Decision:** Replace the parent's-watcher cancellation in the terminate path with a fire-with-outcome transition. Specifically:
- Add a new public method on `DependencyBus`: `async def fire_for_terminated_target(target_instance_id: str, outcome: Outcome) -> list[FollowUp]` — symmetric to `cancel_for_target` but transitions PENDING→FIRED (using `transition_state`), stamps `fired_at`, and returns the FollowUps so the caller can enqueue them.
- In `instance_lifecycle.py` terminate flow (the `cancel_for_target` call sites at lines 74 and 1775), branch on `op`:
  - `op == 'pause'` → keep `cancel_for_target` (parent intentionally paused; no delivery).
  - `op == 'terminate'` → call `fire_for_terminated_target` with `outcome.status='terminated'`, enqueue each FollowUp, stamp `enqueued_at`.
- The FollowUp payload carries `child_instance_id` + `child_message_id` (existing contract). The parent's completion path at `child_reports.py:1841-1842` reads `count_pending_for_target_sync(...) == 0` → proceeds to finalize. The parent's TERMINATED child FollowUp routes through the same `_process_child_completion_and_notify_parent` path that `claim_for_injection` invokes — that path already handles "no message to deliver" via the existing tri-state (claimed / already_delivered / missing) on the natural enqueue path (pause-report-recovery Q2 v3.1 design).

**Invariant preserved:** DependencyBus remains SOLE completion authority. The FollowUp is the bus's contract; the parent consumes it via the same code path whether the child succeeded, errored, or was terminated.

**Justification vs alternatives:**
- **B3 Option 2 (parent-side CANCELLED-watcher detection):** rejected — `count_pending_for_target_sync` does not count CANCELLED, and rewriting it to count CANCELLED-as-pending would mis-classify CANCELLED rows from the resume cascade (a different lifetime). Adding a "CANCELLED means child-was-deliberately-stopped" branch into the parent's completion gate conflates two different reasons for CANCELLED (resume-cancelled-paused vs terminate-cancelled-mid-tree).
- **B3 Option 3 (a new watcher state `TERMINATED`):** rejected — adds a fourth state to a tight contract (PENDING/FIRED/CANCELLED only); every consumer (`emit_terminal`, `transition_state`, `_recover_fired_unsent`, `cancel_for_source`, `_sweep_orphan_watchers`) needs a new branch; the outcome in the FollowUp payload already carries the terminal status. Encoding the outcome in the payload is the smaller change.
- **B3 Option 1 (this plan):** composes with the existing FIRED-with-enqueued_at contract. `mark_enqueued_by_source_target` already exists; the enqueue_message seam is shared with `emit_terminal`'s existing FollowUps.

**Revive interaction (architect-flagged):** If `send_message` revives a TERMINATED child before the parent's watcher fires (parent is still waiting, child gets a new message via revival), the FollowUp should fire the parent ONCE, not double-deliver. The fix is at the call site in `instance_messaging.py:1486-1510` — the revival path must NOT call `enqueue_message` with a fresh FollowUp for an already-FIRED obligation; or the FollowUp's idempotency guard at `child_reports.py:898/1244/1845` (the existing three idempotency sites) catches the duplicate. The latter is the existing precedent; this plan does not change revival. **Architect must verify.**

---

## Phase 2 Tasks

| # | Task | File:Line | Acceptance | Flag |
|---|------|-----------|------------|------|
| 2.1 | **Read end-to-end `daemon/services/report_delivery_recovery.py`** (5-lane sweep) | `daemon/services/report_delivery_recovery.py:1-1116` | Enumerate the 5 lane methods by signature (lane_deferred, lane_no_row_backstop, lane_pending_age, lane_recovery_retry, lane_orphan); confirm the no-row-backstop handles the B2 shape (FIRED+enqueued_at IS NULL rows for a paused parent). Output: 1-paragraph note appended to this plan's `## Open Questions` section. | [IMPL-DETAIL] |
| 2.2 | **Add `DependencyBus.fire_for_terminated_target(target_instance_id, outcome)`** | New method in `daemon/services/dependency_bus.py` after `cancel_for_target` (~line 1098) | Symmetric to `cancel_for_target` — fetches `pending_rows` for target, transitions each to FIRED via `transition_state` with `fired_at`, returns the FollowUp list. Existing `transition_state` guard handles concurrent terminal events from the child (win = either-side-fire, exactly-once preserved). Unit test: 5 cases (empty, single, multi, race-with-emit_terminal, race-with-cancel_for_target). | [IMPL-DETAIL] (signature; public API) / [ARCHITECT] (Q2/Q4 — see below) |
| 2.3 | **Wire terminate path to `fire_for_terminated_target`** | `daemon/services/instance_lifecycle.py:74` and `:1775` | Branch `op`: `op == 'pause'` keeps `cancel_for_target`; `op == 'terminate'` calls `fire_for_terminated_target` with `outcome=Outcome(status='terminated')` and enqueues each returned FollowUp via `manager.enqueue_message`, then stamps `enqueued_at` via `mark_enqueued_by_source_target`. The 60s grace + `enqueued_at IS NOT NULL` filter in `_compact_fired_watchers_for_paused` now has something to compact. Failure handling: log + swallow (existing `try/except` pattern at `:74-82`). | [IMPL-DETAIL] |
| 2.4 | **Modify `_compact_fired_watchers_for_paused` to deliver-before-compact** | `daemon/services/instance_lifecycle.py:3608-3696` | New prefix loop (before the existing DELETE): iterate FIRED rows where `enqueued_at IS NULL AND fired_at <= cutoff_iso`, for each: re-enqueue FollowUp via `manager.enqueue_message`, stamp `enqueued_at` via `mark_enqueued_by_source_target` (the existing repo method already accepts `enqueued_at=None`; this plan passes `None` and lets the helper stamp). Then run the existing DELETE pass unchanged. Unit test: 4 cases (no buffered, buffered+enqueued, buffered+stale, mixed). | [ARCHITECT] Q1 — does the deliver-before-compact composition with the bus's `_recover_fired_unsent` (no double-delivery on restart)? |
| 2.5 | **Cancel-during-pause drift sub-fix (composable)** | `daemon/services/instance_lifecycle.py:3789-3810` (the SELECT inside `_resume_cascade_db_sync`) | Add `AND NOT EXISTS (SELECT 1 FROM job_queue_items WHERE job_id = task.work_id AND admission_state IN ('queued','active') AND deleted_at IS NULL)` to the SELECT. If the sub-fix's optional companion reconciler is needed (decided by test 2.6), add a periodic sweep mirroring `job_recovery_service.py:528-534` that transitions stranded PAUSED Tasks to a canonical terminal state (reason='failed'). | [IMPL-DETAIL] |
| 2.6 | **Idempotency guard at parent's three completion sites** | `daemon/services/child_reports.py:898, :1244, :1845` | Confirm the existing idempotency guards at these three sites catch a TERMINATED-child FollowUp (the existing `_process_child_completion_and_notify_parent` tri-state already returns "already_delivered" when the obligation is consumed). If any of the three does NOT catch this shape, add a one-line guard. **No rewriting** — only the minimum new branch. | [ARCHITECT] Q3 — verify ghost-child semantics (B3 candidate outcome='terminated') doesn't bypass the existing tri-state |
| 2.7 | **Revive-interaction check** | `daemon/services/instance_messaging.py:1486-1510` (read 1500-1530) | Confirm `send_message` revival does NOT re-enqueue a FollowUp for an already-FIRED obligation. The existing FollowUp-claim path at `_process_child_completion_and_notify_parent` should naturally suppress (the obligation's `report_injection` row is already INJECTED). **Architect review required** — see Q5. | [ARCHITECT] Q5 |
| 2.8 | **Unit tests — site-per-site** | new test files under `tests/unit/services/` and `tests/repositories/dependency_bus/` | Per task: 5 (B3 fire method) + 4 (B2 compact-reorder) + 3 (B2 drift sub-fix SELECT) + 2 (B3 idempotency guards) + 2 (revive non-replay) = **16 minimum unit tests** | [IMPL-DETAIL] |
| 2.9 | **E2E: new `test_resume_after_pause_with_children_completing_during_pause`** | new test under `tests/e2e/` | Real daemon (`./dev.sh`), POST pause on root, let 2 children complete (sleep 60), POST resume, assert root reaches `COMPLETED` with 2 reports in `report_injection.state='INJECTED'` and msg count advances by exactly 2 (no advance before resume; no double-deliver on resume+message). Uses `_pack e2e_workflows_ensure_test.sh` pattern from `.agents/tester/PACKS.md:9` (`-k` filter on the new test). | [IMPL-DETAIL] |
| 2.10 | **E2E: new `test_terminate_mid_tree_with_parent_waiting`** | new test under `tests/e2e/` | Real daemon, build 3-level tree (leader→tester→grandchild); grandchild starts `sleep 480`; mid-stream, `DELETE /api/instances/{tester}`; assert: grandchild graph cancelled (DOWN propagation — Phase 1 invariant preserved), leader reaches `COMPLETED` with exactly 1 report whose `child_instance_id == tester_id` (UP propagation via fire-with-terminated-outcome); leader message count advances by exactly 1; **no ghost-child deferring-completion loop**. | [IMPL-DETAIL] |
| 2.11 | **Regression: ensure.md MANDATORY 5-pack gate** | `.agents/tester/rules/ensure.md:47-53` | Run per pack (one-by-one, PYTEST_TIMEOUT=280, no -x, `--tb=short -q`): `turn_transitions_reconciler_unit_test`, `job_queue_unit_test`, `claim_guard_locks_unit_test`, `concurrency_atomic_unit_test`, `api_unit_test`. PLUS the 2 e2e: `test_pause_after_spawn_then_resume`, `test_terminate_after_spawn_then_revive`. PLUS the 2 new e2e (2.9, 2.10). | [IMPL-DETAIL] |
| 2.12 | **D2: Compile + run the gated packs locally** | follow ensure.md execution rules | All 5 packs + 4 e2e green; bundle results into the merge evidence | [IMPL-DETAIL] |

---

## Coupling Map

| | B2 (resume strand) | B3 (terminate UP) | Sub-fix (drift) | Tests |
|---|---|---|---|---|
| **B2 (resume strand)** | — | independent (different paths) | tight (same `resume_instance_cascade` flow) | loose |
| **B3 (terminate UP)** | independent | — | independent | loose |
| **Sub-fix (drift)** | tight | independent | — | loose |
| **Tests** | loose | loose | loose | — |

**Tight coupling between B2 and sub-fix:** both modify the resume cascade. Land in the same PR; CI runs the test gate once.
**Independent between B2 and B3:** different code paths, different bug-classes. Land in the same phase for coordination but separate commits.

---

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | **Architect review surfaces a fundamental obligation-semantics disagreement** | High | Medium | This plan explicitly defers the four architect decisions (Q1-Q5) below to a pre-implementation review; the plan is *binding* on the architect answer, but the chosen approach is presented with alternatives so a redesign is possible without re-research |
| 2 | **Deliver-before-compact double-delivers a FollowUp on resume + restart** | High | Low | `_recover_fired_unsent` filters `state='FIRED' AND enqueued_at IS NULL`. After deliver-before-compact, every re-enqueued FollowUp stamps `enqueued_at`, so the restart recovery sees an empty result. Test 2.4 pins this. |
| 3 | **B3 fires FollowUp after parent already finalized (revive race)** | High | Medium | The parent's three idempotency guards at `child_reports.py:898/1244/1845` already gate the natural-enqueue path's tri-state (claimed/already_delivered/missing). Revived child re-runs `_process_child_completion_and_notify_parent` — guard must return `already_delivered`. Test 2.7 + 2.10 pin this. **Architect must verify (Q5)**. |
| 4 | **`_compact_fired_watchers_for_paused` re-enqueue blocks the resume response** | Medium | Low | The compact already runs synchronously inside the resume path (existing); adding a small loop over FIRED rows adds at most N×(one enqueue + one UPDATE) per paused parent. For N<100 (the long-pause bound from Phase 2 Decision 3), <100ms typical. Bound via the existing 60s grace. No new latency class. |
| 5 | **Cancel-during-pause drift sub-fix creates a new stranded-pending class** | Medium | Low | The sub-fix is additive — it only adds a `NOT EXISTS` clause to the SELECT. If the sub-fix's optional companion reconciler is needed, it is gated on test 2.6 outcome (deferred by design). |
| 6 | **`emit_terminal`'s per-task lock races `fire_for_terminated_target`** | Medium | Low | Both methods use the same guarded `transition_state` primitive (`dependency_bus/repository.py:462-536`). Whichever transitions first wins (rowcount==0 for the loser). FollowUps are delivered exactly-once. Test 2.2 unit case pins this. |
| 7 | **Pause-resume idempotency on deliver-before-compact (pause→resume→pause→resume)** | Medium | Low | After first resume, FIRED rows have `enqueued_at IS NOT NULL`; subsequent resume's compact finds no buffered rows. No behavior change vs current. Test 2.4 cycle-2 case pins. |
| 8 | **Migration safety on existing prod DBs with FIRED rows from prior pause cycles** | Medium | Medium | No schema change in this phase. The deliver-before-compact is forward-only — pre-existing FIRED rows are processed on the next resume (the next pause/resume cycle that hits a paused node). No data repair needed. If a parent never resumes (FM-12 territory), pre-existing FIRED rows accumulate — same as current behavior; `_recover_fired_unsent` handles restart. |
| 9 | **B3 fires FollowUp with `outcome.status='terminated'`; downstream consumer rejects** | High | Low | The outcome string is consumed at `dependency_bus.py:638` (`if outcome.status == 'error': ...`) and in `_parent_errored` flag setting. A new `outcome.status='terminated'` value must NOT trip the error branch. Grep-audit checklist before merge: `grep -rn "outcome.status" daemon/ --include='*.py'` — every branch enumerated. Test 2.6 verifies. |
| 10 | **Test pack regressions in the 5 gated packs** | High | Low | All 5 packs baseline-green per PACKS.md; only changes in `dependency_bus.py`, `instance_lifecycle.py`, `child_reports.py`. Tight blast-radius; concurrency_atomic_unit_test + claim_guard_locks_unit_test cover the seam. |
| 11 | **Outbox drift in `_pause_cascade_db_sync` vs `_resume_cascade_db_sync`** | Low | Low | B2 changes are downstream of pause (in compact), upstream of resume (in SELECT). No outbox changes needed. Pause-outbox structure unchanged. |
| 12 | **Work_id-keyed correlation regression (per fix-pause-report-turn-orphan D-REV-1)** | Low | Low | B2's deliver-before-compact uses the FollowUp payload, which carries `(source_task_id, target_instance_id)` — same key contract as pause-report-recovery Phase 1. No `work_id` correlation introduced or removed. |

---

## Architect Flags (B2+B3 obligation semantics — pre-flagged for review)

These are the FIVE design questions whose answers bind the implementation. Each is presented with the recommended answer (justified above), the alternative, and the failure mode if the architect disagrees.

### Q1 [ARCHITECT] — Does deliver-before-compact (B2) compose with `_recover_fired_unsent`?

- **Recommended:** Yes — `_recover_fired_unsent` filters `state='FIRED' AND enqueued_at IS NULL`. After deliver-before-compact stamps `enqueued_at`, restart sees empty result. Single invariant.
- **Alternative:** Defer delivery to `_recover_fired_unsent` only (remove the deliver-before-compact loop). Tradeoff: pause-resume is silent for ≤restart; resume without restart leaves buffered reports stranded — this is the CURRENT bug.
- **Failure if disagreed:** Architect must specify the alternative delivery path. This plan does NOT support the alternative without re-research.

### Q2 [ARCHITECT] — Should `DependencyWatcherState` get a 4th state (`TERMINATED`) or encode the outcome in the FollowUp payload?

- **Recommended:** Encode in FollowUp payload — the outcome (`status`, `error`, etc.) is already serialized in `follow_up_payload` (per `FollowUp.from_payload` at the bus). Adding a state means every consumer branches.
- **Alternative:** New state — `state='TERMINATED'` distinct from FIRED/CANCELLED. Same backpressure, but the parent's `count_pending_for_target_sync` and `_recover_fired_unsent` both need updating; the WS broadcast / SSE schema needs a new state value.
- **Failure if disagreed:** Architect must specify the new-state schema. This plan binds the choice but the alternative is small (~3 files).

### Q3 [ARCHITECT] — Ghost-child semantics: does the parent's completion path tolerate a FollowUp whose child is TERMINATED?

- **Recommended:** Yes — the existing `_process_child_completion_and_notify_parent` tri-state (claimed/already_delivered/missing) already returns `already_delivered` when the obligation is consumed. The natural-enqueue path's `enqueue()` checks existence; the same check applies to a TERMINATED child. The ghost-child "waiting for 1 children (bus=True), deferring completion" loop is fixed because `count_pending_for_target_sync(...) == 0` after the FIRED transition (FIRED doesn't count as PENDING, and there's no PENDING for this source-task any more either).
- **Alternative:** Add a "ghost-child" branch at the parent completion site that recognizes a FollowUp whose child status is TERMINATED and short-circuits completion. Larger change; encodes a policy into the gate.
- **Failure if disagreed:** Architect must specify the ghost-child branch. Test 2.6 + 2.10 are the acceptance gates.

### Q4 [ARCHITECT] — `fire_for_terminated_target` signature: per-target vs cascade-aware?

- **Recommended:** Per-target only — `async def fire_for_terminated_target(target_instance_id: str, outcome: Outcome) -> list[FollowUp]`. The terminate path at `instance_lifecycle.py:1775` already handles the per-instance loop; the cascade enumeration is Phase 1 (lineage) and OUT of this phase.
- **Alternative:** Cascade-aware variant that takes a tree-ids list. Premature — depends on Phase 1's lineage fix.
- **Failure if disagreed:** Architect must specify. This plan binds to per-target.

### Q5 [ARCHITECT] — Revive interaction: does `send_message` to a TERMINATED child re-enqueue a duplicate FollowUp for an already-FIRED obligation?

- **Recommended:** No — the existing FollowUp-claim path consumes the obligation once (the `report_injection` row's `state='INJECTED'` after first delivery); revival does NOT re-create the report row (the child has no new message content). The tri-state suppresses.
- **Alternative:** Revival must explicitly skip enqueueing for a child whose last obligation is already FIRED. Adds a new branch at `instance_messaging.py:1486-1510`.
- **Failure if disagreed:** Architect must specify. Test 2.7 + 2.10 are the acceptance gates.

### Q6 [IMPL-DETAIL, but binding review point] — Sub-fix scope.

- **Recommended:** Include the sub-fix (task 2.5). The drift risk is documented in the evidence report (B4 tail); fixing it now prevents a follow-up phase.
- **Alternative:** Defer sub-fix to a Phase 3 follow-up. Smaller blast-radius for Phase 2.
- **Architect decision:** Include iff the test 2.6 outcome is inconclusive on the bug being present without it. **Marker: optional in the task list above.**

---

## Success Criteria

| # | Criterion | How to Measure | Threshold |
|---|-----------|----------------|-----------|
| 1 | B2: resume after pause with children completing during pause delivers reports | New e2e `test_resume_after_pause_with_children_completing_during_pause` (task 2.9) | 100% pass; root reaches COMPLETED; exactly N reports delivered (N = # children completed during pause); msg count advances by exactly N |
| 2 | B2: pause→resume→pause→resume cycle leaves no stranded reports | New unit test cycle-2 case (task 2.4) | 100% pass; second compact finds 0 buffered rows |
| 3 | B3: terminate mid-tree child completes the parent | New e2e `test_terminate_mid_tree_with_parent_waiting` (task 2.10) | 100% pass; parent reaches COMPLETED with exactly 1 report (the terminated-child report); no deferring-completion loop |
| 4 | B3: terminate race with concurrent child completion | New unit test (task 2.2 case "race-with-emit_terminal") | 100% pass; exactly-one delivery (whichever side wins) |
| 5 | Revive interaction: sending message to revived TERMINATED child does NOT duplicate FollowUp | New unit test (task 2.7) + e2e (task 2.10) | 100% pass; parent's `report_injection` row count is unchanged after revival |
| 6 | Cancel-during-pause drift sub-fix closes the JobItem+Task drift | New unit test (task 2.5) | 100% pass; `JobItem CANCELLED, Task PENDING` shape does NOT arise |
| 7 | `_compact_fired_watchers_for_paused` deliver-before-compact composes with `_recover_fired_unsent` | New unit test (task 2.4) | 100% pass; restart after deliver-before-compact sees empty `_recover_fired_unsent` result |
| 8 | DependencyBus remains SOLE completion authority | Grep audit + unit test | No new JobItem-creation sites added in the pause/resume/terminate paths; pause writes NOTHING to JobItems by design (Phase 4 invariant preserved) |
| 9 | All 5 mandatory packs + 4 e2e green on PG | ensure.md pack runs (task 2.11/2.12) | 100% pass; no regressions |
| 10 | Terminal reason for any stranded rows from this fix is canonical | Grep audit (`orphaned_no_task` is non-canonical per `work_status.py:60-125`) | 0 instances of non-canonical reason in new code paths; prefer `'failed'` |
| 11 | No new state on `DependencyWatcherState` (architect decision Q2 binding) | Grep audit on `dependency_bus/models.py` | State enum unchanged |
| 12 | Pause cascade idempotency under back-to-back resume | Unit test | Second resume is a no-op |

---

## Files Touched (consolidated)

| File | Change Type | Tasks |
|------|-------------|-------|
| `daemon/services/dependency_bus.py` | Add `fire_for_terminated_target` method (~line 1098) | 2.2 |
| `daemon/services/instance_lifecycle.py` | Modify terminate path branch (`:74`, `:1775`); reorder `_compact_fired_watchers_for_paused` (`:3608-3696`); guarded SELECT in `_resume_cascade_db_sync` (`:3789-3810`) | 2.3, 2.4, 2.5 |
| `daemon/services/instance_messaging.py` | Read-only verification at `:1486-1510`; architect-flagged potential one-line guard | 2.7 |
| `daemon/services/child_reports.py` | Read-only verification at `:898, :1244, :1845`; architect-flagged potential one-line guard | 2.6 |
| `daemon/services/report_delivery_recovery.py` | Read-only end-to-end (per task 2.1) | 2.1 |
| `tests/unit/services/test_dependency_bus_fire_for_terminated.py` | NEW — 5 cases | 2.2 |
| `tests/unit/services/test_compact_fired_watchers_deliver_before_compact.py` | NEW — 4 cases | 2.4 |
| `tests/unit/services/test_resume_cascade_drift_guard.py` | NEW — 3 cases | 2.5 |
| `tests/unit/services/test_parent_completion_idempotency_terminated.py` | NEW — 2 cases | 2.6 |
| `tests/unit/services/test_revive_non_replay.py` | NEW — 2 cases | 2.7 |
| `tests/e2e/test_resume_after_pause_with_children_completing_during_pause.py` | NEW | 2.9 |
| `tests/e2e/test_terminate_mid_tree_with_parent_waiting.py` | NEW | 2.10 |

**Total production-code change:** 3 files modified (`dependency_bus.py`, `instance_lifecycle.py`, optionally `instance_messaging.py` if Q5 requires). **Total test addition:** 7 new files, ~16 unit tests + 2 new e2e tests.

---

## Coupling to Other Phases (within this worktree)

| Phase | Defect | Coupling | Sequencing |
|-------|--------|----------|------------|
| Phase 1 | B1, B4 (lineage enumeration) | None — different code paths | Phase 1 first (or merged atomically) |
| Phase 2 (this) | B2, B3 (watcher/obligation) | Reads `_compact_fired_watchers_for_paused` (Phase 2 invariant unchanged); reads `_resume_cascade_db_sync` (Phase 4 invariant preserved) | After Phase 1 |
| Phase 3 | B5 (router) | None | Independent |
| Phase 4 | B6, B7 (memory + timestamps) | None | Independent |
| Phase 5 | SSE | Independent | Last |

**Hard sequencing:** Phase 1 must land first because Phase 2's e2e tests (2.9, 2.10) build trees with descendants — if cascade enumeration misses live descendants (B1), the e2e tests' tree setup won't match the B2/B3 repro conditions. **OR:** Phase 2 lands atomically with Phase 1 in a single worktree merge (current worktree pattern). Per the evidence report, this is the intended sequencing.

---

## Hard Constraints (project rules — every task honors)

- **Turn transitions via named transitions** (`AbortTurn`, `CompleteTurn`, `ResumeTurn`) + `reconcile_turn_mirror(work_id)` authoritative over 8 mirror tables — task 2.5's drift sub-fix uses `ResumeTurn` exclusively (already at `:3821-3826`).
- **`terminal_reason` MUST be canonical** (`_STATUS_CANONICAL_MAP` at `work_status.py:60-125`); prefer `'failed'`; never `'orphaned_no_task'`. Task 2.5's optional reconciler must emit canonical reasons.
- **`dependency_bus.py` is SOLE completion authority for jobs** — tasks 2.2/2.3 add a new bus method; no new JobItem-creation sites anywhere.
- **Pause writes NOTHING to JobItems by design (Phase 4 invariant)** — task 2.4's deliver-before-compact uses the bus's existing re-enqueue seam; no JobItem writes.
- **Message-type JobItems skip job_locks** (PG trigger) — tasks 2.3/2.4 do not touch this; the constraint is preserved.
- **Revive semantics must not break** — task 2.7's verification gates this.
- **Don't gold-plate** — B5/B6/B7/SSE are explicit OUT.

---

## Open Questions

1. **(added by task 2.1)** Enumerate the 5 lanes of `daemon/services/report_delivery_recovery.py` end-to-end and confirm the no-row-backstop handles the B2 shape (FIRED+enqueued_at IS NULL rows for a paused parent). Append note here on completion.
2. **(architect review)** Q1-Q5 (see §Architect Flags).
3. **(architect review)** Q6 (sub-fix scope inclusion) — decided at task 2.6 outcome.
4. **(post-architect-review)** If the architect picks the alternative for Q2 (new `DependencyWatcherState`), the plan's bound choice shifts and tasks 2.2/2.3/2.4 grow by ~3 files.