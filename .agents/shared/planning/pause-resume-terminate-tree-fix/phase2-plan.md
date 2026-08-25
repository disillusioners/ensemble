# Plan Overview: Phase 2 — Pause/Resume/Terminate Tree-Propagation: B2+B3 (Watcher/Obligation Semantics)

Date: 2026-08-24
Author: planner[v2] via plan-creation worker
Status: Rev 2.1 — reviewer-corrected (council 2bb126df: W1, W2, §D, W3, W4 folded) — see §Rev 2.1 Changelog. Rev 2 is the architect-corrected baseline (commit `8abca8b5`, see §Rev 2 Changelog).
Branch: feature/pause-resume-terminate-tree-fix @ e6007b8a (worktree)
Bug Source: `.agents/tester/RESULTS/2026-08-24-pause-resume-terminate-tree-propagation-repro.md` (live-repro evidence, 6 phases)
Companion plans: `.agents/shared/planning/pause-report-recovery/plan-overview.md` (v3.1, READ-ONLY contract pattern precedent), `.agents/shared/planning/fix-pause-report-turn-orphan/plan-overview.md` + `decisions.md` (hybrid bridge precedent, work_id-keyed correlation, point-fix + reconciler subsumption)
Architect reference: `.agents/shared/planning/pause-resume-terminate-tree-fix/architecture-recommendation.md` (commit `8abca8b5`) — §3 (Q1–Q6 verdicts + §3.3 two-pass cutoff), §7 register items #4/#5 (correctness-gating). D4 (Lane-5/sweep coherence) deferred to follow-up ticket **FT-005**. Rev 1 preserved in git (commit `cefb9798`). **Rev 2.1 erratum (council 2bb126df, this revision):** architect note §3.2 `:185` repeated a stale premise about pause/terminate `cancel_for_target` call sites — source-verified Rev 2.1, see §D in §Rev 2.1 Changelog and the bracketed erratum appended at `architecture-recommendation.md:185`.

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
| `daemon/services/dependency_bus.py:1025-1098` | `cancel_for_target(target_instance_id)` | PENDING→CANCELLED for every watcher whose target matches; purges matching cache entries. **§D (Rev 2.1) — currently-reachable callers are terminate-only.** The only currently-reachable call sites are terminate-side: `instance_lifecycle.py:1781` (direct `await bus.cancel_for_target(instance_id)`, pre-existing duplicate — fold into Task 2.3) and `instance_lifecycle.py:1816` (via `_cancel_bus_watchers_for(self._manager, instance_id, "terminate_instance")`, the post-commit seam — Task 2.3 patches this helper's body). Pause-side invocation was **REMOVED pre-Phase-2** (evidence: `instance_lifecycle.py:2240-2266` Phase 2 Decision 2, 2026-06-25 — "DEPENDENCY-BUS WATCHERS ARE PRESERVED ON PAUSE"). The helper `_cancel_bus_watchers_for` is defined at `:74-84` but its only current caller is terminate. **See architecture-recommendation.md:185 Rev 2.1 erratum note for the corrected premise.** |
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
| `daemon/repositories/report_injection/models.py:40-83, 299` | `ReportInjection`, `ReportInjectionState` (PENDING/DEFERRED/INJECTED/TASK_DELIVERED) | Pause-report-recovery Phase 1 already added `DEFERRED` and the partial unique index `uq_report_injections_oblig_triple`. **Citation (Rev 2.1):** the index is defined inline in migration `20260819_000001_report_injections_deferred_marker.sql` (line 114) — NOT `20260624_000004_report_injection_partial_unique.sql` (Rev 2 incorrectly cited that filename; that file does not exist in `daemon/migrations/versions/`). DEFERRED→INJECTED/TASK_DELIVERED forbidden by design — only DEFERRED→PENDING is legal |
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
- **§D (Rev 2.1):** in `instance_lifecycle.py` terminate flow, **patch UNCONDITIONALLY — NO `op` branching.** The currently-reachable `cancel_for_target` call sites are terminate-side only: `:1781` (direct call, pre-existing duplicate — fold into this task) and `:1816` (via `_cancel_bus_watchers_for`, the post-commit seam — patch the helper's body). The helper's `op` parameter becomes logging-only. Pause-side `cancel_for_target` invocation does NOT exist (removed pre-Phase-2, evidence: `instance_lifecycle.py:2240-2266`); designing an `op=='pause'` branch is **dead code**. Both terminate sites call `fire_for_terminated_target` with `outcome.status='terminated'`; the caller enqueues each returned FollowUp via `manager.enqueue_message` and stamps `enqueued_at` via `mark_enqueued_by_source_target`.
- The FollowUp payload carries `child_instance_id` + `child_message_id` (existing contract). The parent's completion path at `child_reports.py:1841-1842` reads `count_pending_for_target_sync(...) == 0` → proceeds to finalize. The parent's TERMINATED child FollowUp routes through the same `_process_child_completion_and_notify_parent` path that `claim_for_injection` invokes — that path already handles "no message to deliver" via the existing tri-state (claimed / already_delivered / missing) on the natural enqueue path (pause-report-recovery Q2 v3.1 design). **Task 2.13 (Rev 2.1 — W4) further ensures the encoded `child_outcome` metadata is surfaced through this drain to the parent's LLM.**

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
| 2.1 | **Read end-to-end `daemon/services/report_delivery_recovery.py`** (5-lane sweep) | `daemon/services/report_delivery_recovery.py:1-1116` + `daemon/repositories/report_injection/repository.py:585-823` | **Architect-verified lane enumeration (binding as of Rev 2):** Lane 1 `_run_deferred_lane` (`report_delivery_recovery.py:522`), Lane 2 `_run_no_row_backstop_lane` (`:856`, queries `find_completed_children_without_delivery` at `repository.py:585-763`), Lane 3 `_run_pending_age_lane` retry=0 (`:1019`, queries `find_pending_past_age` at `repository.py:762-823`), Lane 4 `_run_pending_age_lane` recovery_retry (`:1019`, same query, retry interval gate), Lane 5 `_run_orphan_lane` (`:539`, DEFERRED rows for terminal parents, revive-first via `_try_revive_terminal_parent` `:748-792`). End-to-end read remains implementation verification. **KEY FINDING #1 (binding — Q1 caveat, atomicity rationale):** Lane 2's NOT EXISTS predicate (`report_injection/repository.py:715-725`) is gated on `DependencyWatcherState.FIRED` — it **excludes** FIRED rows from the no-row backstop. Therefore deliver-before-compact (Task 2.4) has NO sweep-time lane fallback; only `_recover_fired_unsent` (restart path) sees un-stamped FIRED rows. **KEY FINDING #2 (binding — separate defect class):** `find_pending_past_age` (`repository.py:762-823`) has **NO parent-status filter** — dead-parent PENDING rows get re-swept forever by Lanes 3/4 with inflated `recovered` counts. This is the same defect class as Phase 1 §2.5's companion-artifact disposition; cross-reference P1 §2.5 for the symmetric fix (parent-status guard on the PENDING-lane query). Output: 1-paragraph note appended to this plan's `## Open Questions` section noting any new discrepancies with the architect's enumeration. | [IMPL-DETAIL] |
| 2.2 | **Add `DependencyBus.fire_for_terminated_target(target_instance_id, outcome)`** | New method in `daemon/services/dependency_bus.py` after `cancel_for_target` (~line 1098) | Symmetric to `cancel_for_target` — fetches `pending_rows` for target, transitions each to FIRED via `transition_state` with `fired_at`, returns the FollowUp list. Existing `transition_state` guard handles concurrent terminal events from the child (win = either-side-fire, exactly-once preserved). Unit test: 5 cases (empty, single, multi, race-with-emit_terminal, race-with-cancel_for_target). | [IMPL-DETAIL] (signature; public API) / [ARCHITECT] (Q2/Q4 — see below) |
| 2.3 | **Wire terminate path to `fire_for_terminated_target`** | `daemon/services/instance_lifecycle.py:1816` (post-commit seam via `_cancel_bus_watchers_for` — **PATCH THIS**) + `:1781` (pre-existing direct-call duplicate — see FOLD note below) | **§D (Rev 2.1) — stale-premise correction.** Rev 2's premise — "pause calls `cancel_for_target` via `:74` AND terminate calls via `:1781`/`:1816`; branch on `op`" — is **stale**. Source-verified (council 2bb126df): the helper `_cancel_bus_watchers_for` is defined at `:74-84` but pause-side invocation was REMOVED pre-Phase-2 (evidence: explicit pause-removal comment at `instance_lifecycle.py:2240-2266`, Phase 2 Decision 2, 2026-06-25 — "DEPENDENCY-BUS WATCHERS ARE PRESERVED ON PAUSE"). The only currently-reachable `cancel_for_target` call sites are terminate-side: `:1781` (direct `await bus.cancel_for_target(instance_id)` call, pre-existing duplicate) and `:1816` (via `_cancel_bus_watchers_for(self._manager, instance_id, "terminate_instance")`, the post-commit seam — Rev 2's correct patch site). **Revised task text (binding):** (a) **PATCH `:1816` UNCONDITIONALLY** — replace the helper's body to call `bus.fire_for_terminated_target(instance_id, outcome=Outcome(status='terminated'))` instead of `bus.cancel_for_target(instance_id)`. **NO `op` branching.** The helper's `op` parameter becomes a logging-only field; the function body is terminate-only by current call graph. (b) **FOLD `:1781` into this task** — add a TODO comment at `:1781` referencing Task 2.3, then route that call site through `_cancel_bus_watchers_for` (or inline the same `fire_for_terminated_target` call) so the two terminate paths converge. The reviewer allowed either ticket-it or fold-it; folding closes the duplicate within Phase 2 instead of leaking a future bug-class. (c) **DROP the dead `op=='pause'` branch design entirely** — no pause-side `cancel_for_target` invocation exists today; designing a branch for a non-existent call is dead code. (d) **NEW path-coverage unit test (Rev 2.1, Rev 2.1 §D):** for every reachable terminate path (direct `:1781` AND helper `:1816`), assert the watcher row transitions to **FIRED-with-outcome** (never CANCELLED) — i.e., `state='FIRED' AND fired_at IS NOT NULL` after the call returns, and the resulting FollowUp carries `metadata["child_outcome"]="terminated"`. This also covers W5 (the path-coverage dimension). Failure handling: log + swallow (existing `try/except` pattern at `:74-82`); a bus failure does not fail the terminate path. The 60s grace + `enqueued_at IS NOT NULL` filter in `_compact_fired_watchers_for_paused` (Task 2.4) now has something to compact — terminate-fired FollowUps arrive at the parent's resume cascade as buffered reports. | [IMPL-DETAIL] |
| 2.4 | **Modify `_compact_fired_watchers_for_paused` to deliver-before-compact (two-pass, ordering-gated)** | `daemon/services/instance_lifecycle.py:3608-3696` | **Two-pass cutoff (binding, per architect §3.3):** **Pass 1 (DELIVER):** iterate FIRED rows where `enqueued_at IS NULL AND fired_at <= now()` (ALL buffered rows, **no grace** — a child that completed 30s before resume must NOT be silently stranded by the 60s grace); for each: re-enqueue FollowUp via `manager.enqueue_message`, stamp `enqueued_at` via `mark_enqueued_by_source_target` (the existing repo method already accepts `enqueued_at=None`; this plan passes `None` and lets the helper stamp). **Pass 2 (DELETE):** run the original 60s-grace DELETE pass unchanged (`enqueued_at IS NOT NULL AND fired_at <= cutoff_iso`). **W2 (Rev 2.1) — atomicity reframe: ordering + per-row stamp (NOT a single transaction).** A single transaction spanning Pass 1 + Pass 2 is **not literally implementable** — Pass 1 calls async `manager.enqueue_message`, which owns its own session/transaction (architect-verified at `instance_messaging.py:1619`). The Rev 2 "wrap Pass 1 + Pass 2 in a single transaction" claim is **withdrawn**. The Rev 2.1 safety model is **ordering + per-row stamp discipline:** (a) **Ordering (binding):** Pass 1 executes strictly BEFORE Pass 2. (b) **Early-abort (binding):** any enqueue failure (exception OR asyncio.CancelledError) in Pass 1 ABORTS the cascade before Pass 2 begins — a single failed FollowUp must not silently allow the DELETE to delete the whole buffered set. (c) **Per-row stamp (LOAD-BEARING safety):** immediately after each successful enqueue, call `mark_enqueued_by_source_target` to stamp `enqueued_at`. The DELETE's `enqueued_at IS NOT NULL` predicate is the durability guarantee — **a stamped row survives the DELETE by construction** (it is already stamped when DELETE runs); **an unstamped row is caught next cycle** (Pass 1 re-runs on the next resume, and on restart the `_recover_fired_unsent` lane catches it via `state='FIRED' AND enqueued_at IS NULL`). (d) **Sync→async bridge (binding):** `_compact_fired_watchers_for_paused` is invoked from the **sync** `_resume_cascade_db_sync` cascade, but `manager.enqueue_message` is **async**. Use `asyncio.run_coroutine_threadsafe(enqueue_coro, loop)` from the sync context to the manager's event loop — the existing precedent for sync-resume-cascade → async-bus dispatch is the `_schedule_explicit_handle_resume` path at `daemon/manager.py:6134+`. Capture each enqueue's result via `concurrent.futures.Future`; a future-raised exception triggers the early-abort. (e) **Idempotency of the healing path (NEW unit test, Rev 2.1):** "pre-existing FIRED row (from a crash between fire and enqueue) → resume → exactly one enqueue delivered" — pins the load-bearing claim that a stamped row is invisible to a second Pass 1 iteration (no double-deliver) and an unstamped row IS picked up (no silent loss). **Rev 2 note preserved (binding):** no lane backstops a missed deliver row — Lane 2's `find_completed_children_without_delivery` (`report_injection/repository.py:715-725`) excludes FIRED rows; `_recover_fired_unsent` is the only recovery path → the **per-row stamp** is the load-bearing mitigation, replacing the impossible single-transaction claim. Unit tests (Rev 2.1 expanded): 7 cases — (a) no buffered, (b) buffered+fresh (<60s, MUST deliver), (c) buffered+stale (>60s, MUST deliver), (d) buffered+enqueued (already-stamped, MUST skip — idempotency pin), (e) mid-Pass-1-crash (early-abort, Pass 2 not entered, all rows survive to next cycle / restart), (f) **NEW pre-existing FIRED row → exactly-one-enqueue (healing-path idempotency, Rev 2.1 W2)**, (g) **NEW W3 sub-class repro (Rev 2.1)**: pre-existing FIRED row with `enqueued_at IS NOT NULL` (stamped strand) vs `enqueued_at IS NULL` (unstamped strand) — pin which strand shape the repro actually produces AND assert that each strand drains through its named durable lane (see W3 below). **W3 (Rev 2.1) — stamped-vs-unstamped sub-class (binding).** Task 2.4's healing depends on whether stranded rows are stamped or unstamped. **Step 1 — unit repro FIRST (before any other Task 2.4 unit case):** construct a pre-existing FIRED row in two shapes — stamped (`enqueued_at IS NOT NULL`) vs unstamped (`enqueued_at IS NULL`) — and assert that Pass 1's `enqueued_at IS NULL` predicate selects ONLY the unstamped strand (the stamped strand is invisible to Pass 1 and is therefore expected to drain via a DIFFERENT lane). **Step 2 — extend test (d):** `buffered+enqueued` MUST assert the healing (exactly-one-enqueue — the row was already stamped, Pass 1 skipped, no double-deliver). **Step 3 — durable delivery paths for both sub-classes (named, binding):** (i) **unstamped FIRED rows** (`state='FIRED' AND enqueued_at IS NULL`) — restart-recoverable via `_recover_fired_unsent` (`dependency_bus.py:1554-1606`) at next process start; (ii) **stamped-but-undelivered FIRED rows** (`state='FIRED' AND enqueued_at IS NOT NULL`) — drain via the durable `report_injection` claim at next dispatch (`graph.py:283` `claim_for_injection(instance_id)` is the hot-path; the `report_delivery_recovery.py` lane set is the periodic backstop). Both lanes are named in the acceptance; the test (g) sub-class repro pins that each stranded row is reachable by its corresponding lane, not silently lost. | [ARCHITECT] Q1 ✅ with ordering+stamp caveat — `_recover_fired_unsent` sees empty after stamp, but NO lane backstops a missed row → per-row stamp discipline is mandatory |
| 2.5 | **Cancel-during-pause drift sub-fix (composable)** | `daemon/services/instance_lifecycle.py:3800-3811` (the `task` SELECT inside `_resume_cascade_db_sync`) | **W1 (Rev 2.1) — guard widening.** Add `AND NOT EXISTS (SELECT 1 FROM job_queue_items WHERE job_id = task.work_id AND deleted_at IS NULL)` to the SELECT. **Rationale (binding, Rev 2.1):** a task-owning JobItem in **ANY** non-deleted state (`queued`, `active`, `cancelled`, `dead`, soft-deleted-elsewhere) means the JobItem lane owns the resume decision — the JobItem state machine is authoritative over the Task `PAUSED→PENDING` flip. The Rev 2 admission-state filter (`IN ('queued','active')`) was too narrow: a CANCELLED JobItem still evaded the guard and the blind `PAUSED→PENDING` flip fired for cancelled-during-pause jobs, leaving `(JobItem CANCELLED, Task PENDING)` rows that no lane recovered (the same Task↔JobItem reconciliation gap already tracked in critical notes — drift reconciler gap from Phase 4b/4c). The widened guard treats the JobItem's existence (regardless of admission state) as the gate: if any non-deleted JobItem exists for the task's `work_id`, the JobItem lane decides; otherwise the Task can resume. If the optional companion reconciler is needed (decided by test 2.6), add a periodic sweep mirroring `job_recovery_service.py:528-534` that transitions stranded PAUSED Tasks to a canonical terminal state (`reason='failed'`; canonical per `_STATUS_CANONICAL_MAP` at `work_status.py:60-125` — never the non-canonical `'orphaned_no_task'`). **Unit test cases:** (a) JobItem=`active` + Task=PAUSED → Task stays PAUSED (covered by Rev 2); (b) JobItem=`cancelled` + Task=PAUSED → Task stays PAUSED (Rev 2.1 case — the bug); (c) JobItem=`queued` + Task=PAUSED → Task stays PAUSED (Rev 2 case); (d) no JobItem + Task=PAUSED → Task flips to PENDING (Rev 2 case); (e) JobItem soft-deleted (`deleted_at IS NOT NULL`) + Task=PAUSED → Task flips to PENDING (the `deleted_at IS NULL` filter is load-bearing — soft-deleted rows do NOT own the resume decision). | [IMPL-DETAIL] |
| 2.6 | **Idempotency + PAUSED-guard verification at parent completion sites** | `daemon/services/child_reports.py:898, :1244` (general idempotency sites) + `child_reports.py:1775, :1845` (PAUSED-guard sites — per architect Q3 correction) | **Architect-corrected line refs (Rev 2):** the **PAUSED-guard** sites are `:1775` and `:1845`; `:898` and `:1244` are general idempotency/duplicate-suppression sites (NOT PAUSED-specific). Confirm both sets correctly handle a TERMINATED-child FollowUp: (a) the general idempotency tri-state (`claimed` / `already_delivered` / `missing`) at `:898`/`:1244`/`:1845` returns `already_delivered` on second delivery; (b) the PAUSED-guards at `:1775`/`:1845` do NOT bypass the ghost-child termination path. Also confirm the `FollowUp.metadata["child_outcome"] = "terminated"` (per Q2) is surfaced through the graph-node drain (`daemon/graph.py:275-300`) so the parent's LLM can distinguish terminated from errored from completed. **No rewriting** — only the minimum new branch + the payload-surfacing wiring. | [ARCHITECT] Q3 ✅ CONFIRMED with caveats (site refs corrected; metadata surfacing added) |
| 2.7 | **Revive-interaction check (Q5 ✅ — with natural-idempotency verification)** | `daemon/services/instance_messaging.py:1486-1510` (read 1500-1530) + `daemon/repositories/report_injection/repository.py:886` | Architect verdict CONFIRMED: revival (`instance_messaging.py:1518-1540`) reactivates status + queues a fresh MessageQueue/Task; it touches **NEITHER** `dependency_watchers` nor `report_injection` → the FIRED obligation stays terminal, no double-delivery. **Required verification (binding, per architect §3.4 Q5):** add an explicit sub-assertion that `claim_for_injection(parent_instance_id)` returns `[]` on second delivery — the natural idempotency mechanism is the guarded `WHERE state='PENDING'` UPDATE on `report_injection` rows (`daemon/repositories/report_injection/repository.py:886`). On the second call, every row has already transitioned to `INJECTED` (or `TASK_DELIVERED`), so the SELECT yields zero rows and the guarded UPDATE would have no-op'd anyway. **This is the load-bearing test** for Q5: it pins both halves of the invariant (revival doesn't re-create the obligation row + the claim path doesn't double-deliver an already-claimed row). Existing FollowUp-claim path at `_process_child_completion_and_notify_parent` already returns "already_delivered" via the tri-state. Unit test cases: (a) revival + claim → 0 rows returned; (b) two consecutive claims → 0 rows on second call. | [ARCHITECT] Q5 ✅ (natural-idempotency confirmed) |
| 2.8 | **Unit tests — site-per-site** | new test files under `tests/unit/services/` and `tests/repositories/dependency_bus/` | Per task (Rev 2.1 expanded): 5 (B3 fire method) + 7 (B2 compact-reorder — Rev 2.1 added the W3 sub-class repro (g)) + 5 (B2 drift sub-fix SELECT — Rev 2.1 widened to 5 cases) + 2 (B3 idempotency guards) + 2 (B3 path-coverage — Rev 2.1 §D, every reachable terminate path lands FIRED-with-outcome) + 2 (revive non-replay) + 2 (Rev 2.1 W4 — child_outcome payload-surfacing, Task 2.13) = **25 minimum unit tests** | [IMPL-DETAIL] |
| 2.9 | **E2E: new `test_resume_after_pause_with_children_completing_during_pause`** | new test under `tests/e2e/` | Real daemon (`./dev.sh`), POST pause on root, let 2 children complete (sleep 60), POST resume, assert root reaches `COMPLETED` with 2 reports in `report_injection.state='INJECTED'` and msg count advances by exactly 2 (no advance before resume; no double-deliver on resume+message). Uses `_pack e2e_workflows_ensure_test.sh` pattern from `.agents/tester/PACKS.md:9` (`-k` filter on the new test). | [IMPL-DETAIL] |
| 2.10 | **E2E: new `test_terminate_mid_tree_with_parent_waiting`** | new test under `tests/e2e/` | Real daemon, build 3-level tree (leader→tester→grandchild); grandchild starts `sleep 480`; mid-stream, `DELETE /api/instances/{tester}`; assert: grandchild graph cancelled (DOWN propagation — Phase 1 invariant preserved), leader reaches `COMPLETED` with exactly 1 report whose `child_instance_id == tester_id` (UP propagation via fire-with-terminated-outcome); leader message count advances by exactly 1; **no ghost-child deferring-completion loop**. | [IMPL-DETAIL] |
| 2.11 | **Regression: ensure.md MANDATORY 5-pack gate** | `.agents/tester/rules/ensure.md:47-53` | Run per pack (one-by-one, PYTEST_TIMEOUT=280, no -x, `--tb=short -q`): `turn_transitions_reconciler_unit_test`, `job_queue_unit_test`, `claim_guard_locks_unit_test`, `concurrency_atomic_unit_test`, `api_unit_test`. PLUS the 2 e2e: `test_pause_after_spawn_then_resume`, `test_terminate_after_spawn_then_revive`. PLUS the 2 new e2e (2.9, 2.10). | [IMPL-DETAIL] |
| 2.12 | **D2: Compile + run the gated packs locally** | follow ensure.md execution rules | All 5 packs + 4 e2e green; bundle results into the merge evidence | [IMPL-DETAIL] |
| 2.13 | **(Rev 2.1 — W4) Wire `child_outcome` metadata through parent-LLM-visible drain path** | `daemon/graph.py:275-300` (drain via `claim_for_injection`) + `_process_child_completion_and_notify_parent` (parent report assembly) | **W4 (Rev 2.1) — child_outcome wiring ownership.** `FollowUp.metadata["child_outcome"] = "terminated"` (set by Task 2.2's `fire_for_terminated_target` and surfaced through Task 2.3's terminate flow) is currently unowned downstream — the metadata reaches the FollowUp but is not guaranteed to surface through the parent's LLM-visible `report_injection` payload. **Task 2.13 routes it:** (a) **payload-surfacing wiring** — at `_process_child_completion_and_notify_parent` (the path `claim_for_injection` invokes per `graph.py:275-300`), copy `FollowUp.metadata["child_outcome"]` into the resulting `report_injection` row's payload (the field that becomes the injected message content the parent LLM reads). The metadata key is additive only; existing payload fields are not touched. (b) **Dependency on Task 2.2 / 2.3 (binding):** Task 2.13 is downstream of Tasks 2.2 and 2.3 — without the upstream `fire_for_terminated_target` setting the metadata, Task 2.13 has nothing to surface. (c) **Coupling to Task 2.6 (binding):** the parent's idempotency guard at `child_reports.py:898`/`:1244`/`:1845` MUST pass the `child_outcome` through unchanged (no field-stripping on the idempotent re-delivery path). **Unit test (Rev 2.1 — 2 cases):** (i) terminate → fire → parent drain → claim → the injected message content the parent LLM receives carries a `child_outcome=terminated` marker; (ii) two consecutive claims (replay test) → second claim returns `[]` AND, if the parent's turn processes the same payload twice, the metadata is byte-identical on both deliveries (idempotency of the surfacing). | [IMPL-DETAIL] |

---

## Coupling Map

| | B2 (resume strand) | B3 (terminate UP) | Sub-fix (drift) | W4 wiring (2.13) | Tests |
|---|---|---|---|---|---|
| **B2 (resume strand)** | — | independent (different paths) | tight (same `resume_instance_cascade` flow) | independent | loose |
| **B3 (terminate UP)** | independent | — | independent | downstream (2.13 reads `fire_for_terminated_target`'s metadata) | loose |
| **Sub-fix (drift)** | tight | independent | — | independent | loose |
| **W4 wiring (2.13)** | independent | downstream | independent | — | loose |
| **Tests** | loose | loose | loose | loose | — |

**Tight coupling between B2 and sub-fix:** both modify the resume cascade. Land in the same PR; CI runs the test gate once.
**Independent between B2 and B3:** different code paths, different bug-classes. Land in the same phase for coordination but separate commits.
**Task 2.13 (W4) is downstream of Tasks 2.2/2.3** (needs the `child_outcome` metadata set upstream) and lands in the same PR as B3 — no separate coupling conflict.

---

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | **Architect review surfaces a fundamental obligation-semantics disagreement** | High | Medium | This plan explicitly defers the four architect decisions (Q1-Q5) below to a pre-implementation review; the plan is *binding* on the architect answer, but the chosen approach is presented with alternatives so a redesign is possible without re-research |
| 2 | **Deliver-before-compact double-delivers a FollowUp on resume + restart** | High | Low | `_recover_fired_unsent` (`dependency_bus.py:1554-1606`) filters `state='FIRED' AND enqueued_at IS NULL`. After deliver-before-compact, every re-enqueued FollowUp stamps `enqueued_at`, so the restart recovery sees an empty result. Test 2.4 pins this. **Strengthened (Rev 2 — architect §3.1 Q1 caveat) → reframed (Rev 2.1 — W2):** Lane 2's `find_completed_children_without_delivery` (`report_injection/repository.py:585-763`, NOT EXISTS predicate at `:715-725`) is gated on `DependencyWatcherState.FIRED` and therefore **excludes** FIRED rows from the no-row backstop — there is no sweep-time lane that can recover from a missed deliver. The Rev 2 mitigation "wrap Pass 1 + Pass 2 in a single transaction" is **NOT implementable** (Pass 1's `manager.enqueue_message` owns its own session/transaction). **Rev 2.1 mitigation (LOAD-BEARING):** (a) ordering — Pass 1 strictly before Pass 2; (b) early-abort on enqueue failure before Pass 2; (c) per-row `enqueued_at` stamp immediately after each successful enqueue — the DELETE's `enqueued_at IS NOT NULL` predicate guarantees a stamped row survives by construction; an unstamped row is caught next cycle (next resume's Pass 1 OR process restart's `_recover_fired_unsent`). The per-row stamp discipline replaces the impossible single-transaction claim as the load-bearing mitigation. |
| 3 | **B3 fires FollowUp after parent already finalized (revive race)** | High | Medium | The parent's three idempotency guards at `child_reports.py:898/1244/1845` already gate the natural-enqueue path's tri-state (claimed/already_delivered/missing). Revived child re-runs `_process_child_completion_and_notify_parent` — guard must return `already_delivered`. Test 2.7 + 2.10 pin this. **Architect must verify (Q5)**. |
| 4 | **`_compact_fired_watchers_for_paused` re-enqueue blocks the resume response** | Medium | Low | The compact already runs synchronously inside the resume path (existing); adding a small loop over FIRED rows adds at most N×(one enqueue + one UPDATE) per paused parent. For N<100 (the long-pause bound from Phase 2 Decision 3), <100ms typical. Bound via the existing 60s grace. No new latency class. |
| 5 | **Cancel-during-pause drift sub-fix creates a new stranded-pending class** | Medium | Low | The sub-fix is additive — it only adds a `NOT EXISTS` clause to the SELECT. If the sub-fix's optional companion reconciler is needed, it is gated on test 2.6 outcome (deferred by design). |
| 6 | **`emit_terminal`'s per-task lock races `fire_for_terminated_target`** | Medium | Low | Both methods use the same guarded `transition_state` primitive (`dependency_bus/repository.py:462-536`). Whichever transitions first wins (rowcount==0 for the loser). FollowUps are delivered exactly-once. Test 2.2 unit case pins this. |
| 7 | **Pause-resume idempotency on deliver-before-compact (pause→resume→pause→resume)** | Medium | Low | After first resume, FIRED rows have `enqueued_at IS NOT NULL`; subsequent resume's compact finds no buffered rows. No behavior change vs current. Test 2.4 cycle-2 case pins. |
| 8 | **Migration safety on existing prod DBs with FIRED rows from prior pause cycles** | Medium | Medium | No schema change in this phase. The deliver-before-compact is forward-only — pre-existing FIRED rows are processed on the next resume (the next pause/resume cycle that hits a paused node). No data repair needed. If a parent never resumes (FM-12 territory), pre-existing FIRED rows accumulate — same as current behavior; `_recover_fired_unsent` handles restart. |
| 9 | **B3 fires FollowUp with `outcome.status='terminated'`; downstream consumer rejects** | High | Low | The outcome string is consumed at `dependency_bus.py:638` (`if outcome.status == 'error': ...`) and in `_parent_errored` flag setting. A new `outcome.status='terminated'` value must NOT trip the error branch. Grep-audit checklist before merge: `grep -rn "outcome.status" daemon/ --include='*.py'` — every branch enumerated. Test 2.6 verifies. **Added (Rev 2 — architect §3.2 outcome audit):** the `Outcome` dataclass at `dependency_bus.py:117` already documents `'terminated'` as a documented value — no type-contract change needed. Architect-verified equality-branch enumeration: the only two `outcome.status ==` comparisons in the codebase are at `:635` and `:823`, **both** `== "error"` — `'terminated'` does not trip either branch. **Safe by construction.** |
| 10 | **Test pack regressions in the 5 gated packs** | High | Low | All 5 packs baseline-green per PACKS.md; only changes in `dependency_bus.py`, `instance_lifecycle.py`, `child_reports.py`. Tight blast-radius; concurrency_atomic_unit_test + claim_guard_locks_unit_test cover the seam. |
| 11 | **Outbox drift in `_pause_cascade_db_sync` vs `_resume_cascade_db_sync`** | Low | Low | B2 changes are downstream of pause (in compact), upstream of resume (in SELECT). No outbox changes needed. Pause-outbox structure unchanged. |
| 12 | **Work_id-keyed correlation regression (per fix-pause-report-turn-orphan D-REV-1)** | Low | Low | B2's deliver-before-compact uses the FollowUp payload, which carries `(source_task_id, target_instance_id)` — same key contract as pause-report-recovery Phase 1. No `work_id` correlation introduced or removed. |
| 13 | **(Rev 2.1 — W1) `(CANCELLED JobItem, PAUSED Task)` residue class persists for pre-fix rows** | Medium | Medium | The widened W1 guard (Task 2.5) closes the bug forward, but ANY `(JobItem CANCELLED, Task PAUSED)` rows created BEFORE this fix lands remain stranded on existing instances — they cannot be flipped by the SELECT (the guard would now block them, but they were already PAUSED at fix-time). The drift reconciler gap is already tracked in critical notes (Task�JobItem reconciliation gap, Phase 4b/4c deferred). **Cross-reference, do not re-derive:** the reconciler in `daemon/services/instance_lifecycle.py` + `daemon/repositories/task/repository.py:2126-2241` is the known backlog item. This plan does NOT add a backfill sweep — that is the existing reconciler's scope. The plan's only obligation: the widened guard must be safe against rows already in the residue class (it is — the guard transitions PAUSED→PENDING only for tasks whose work_id has NO non-deleted JobItem; residue-class tasks stay PAUSED, awaiting the existing reconciler). |

---

## Architect Flags (B2+B3 obligation semantics — pre-flagged for review)

These are the FIVE design questions whose answers bind the implementation. Each is presented with the recommended answer (justified above), the alternative, and the failure mode if the architect disagrees.

### Q1 [ARCHITECT] ✅ CONFIRMED with ordering+stamp caveat — Does deliver-before-compact (B2) compose with `_recover_fired_unsent`?

- **Recommended (binding, with caveat):** Yes — `_recover_fired_unsent` (`daemon/services/dependency_bus.py:1554-1606`) filters `state='FIRED' AND enqueued_at IS NULL`. After deliver-before-compact stamps `enqueued_at`, restart sees empty result. Single invariant holds.
- **🔴 Reframed (Rev 2.1 — W2): per-row stamp discipline replaces single-transaction.** **No lane backstops a missed deliver-before-compact row.** Lane 2's `find_completed_children_without_delivery` (`daemon/repositories/report_injection/repository.py:585-763`) has a NOT EXISTS predicate at `:715-725` gated on `DependencyWatcherState.FIRED` — it **excludes** FIRED rows, so it cannot recover a missed deliver-before-compact row. If the deliver loop crashes mid-iteration (after some FollowUps were re-enqueued + stamped, others not yet), the un-stamped FIRED rows are **not** visible to any lane on the next sweep; only the restart-path `_recover_fired_unsent` (`dependency_bus.py:1554-1606`) would catch them on process restart. **Rev 2 proposed** wrapping Pass 1 + Pass 2 in a single transaction; **Rev 2.1 (this revision) withdraws that claim** — `manager.enqueue_message` owns its own session/transaction (architect-verified at `instance_messaging.py:1619`), so a single shared transaction is not literally implementable. **The Rev 2.1 discipline (binding):** (a) **ordering** — Pass 1 strictly before Pass 2; (b) **early-abort** — any enqueue failure aborts before Pass 2 begins; (c) **per-row `enqueued_at` stamp immediately after each successful enqueue** (the DELETE's `enqueued_at IS NOT NULL` predicate is the load-bearing durability guarantee — a stamped row survives by construction; an unstamped row is caught next cycle, either by the next resume's Pass 1 or by process-restart `_recover_fired_unsent`); (d) **sync→async bridge** via `asyncio.run_coroutine_threadsafe` from the sync `_resume_cascade_db_sync` cascade to the manager's event loop (precedent: `_schedule_explicit_handle_resume` at `daemon/manager.py:6134+`). This is the load-bearing constraint for Task 2.4 (already encoded there).
- **Alternative (rejected):** Defer delivery to `_recover_fired_unsent` only (remove the deliver-before-compact loop). Tradeoff: pause-resume is silent for ≤restart; resume without restart leaves buffered reports stranded — this is the CURRENT bug.
- **Failure if disagreed:** Architect must specify the alternative delivery path. This plan does NOT support the alternative without re-research.

### Q2 [ARCHITECT] ✏️ RESOLVED-MODIFIED — Should `DependencyWatcherState` get a 4th state (`TERMINATED`) or encode the outcome in the FollowUp payload?

- **Recommended (binding, Rev 2 correction):** Encode the outcome in **`FollowUp.metadata["child_outcome"]`** — a new additive key in the existing opaque-metadata dict. **Rev 1's claim that "the outcome is already serialized in `follow_up_payload`" is FALSE** (architect verification: `daemon/services/dependency_bus.py:162-185` — `FollowUp.to_payload` round-trips only `{target_instance_id, message, source, metadata}`; `Outcome.status` is **not** in the payload). Adding a state means every consumer branches.
- **Encoding design (binding):** at the `fire_for_terminated_target` construction site, set `FollowUp.metadata["child_outcome"] = "terminated"`. Additive only — no existing metadata keys are touched. The value is opaque to the bus but readable by `_process_child_completion_and_notify_parent` and the graph-node drain (per Q3 caveat — must be surfaced for parent-LLM visibility).
- **Rationale for additive metadata (not a 4th state):** `Outcome.status == "terminated"` is already documented in the `Outcome` dataclass (`dependency_bus.py:117`); every equality branch in the codebase is `== "error"` (`:635`, `:823`) — `'terminated'` does not trip them. The metadata key is the smallest-surface contract change; the 4th-state alternative would force updates to `count_pending_for_target_sync`, `_recover_fired_unsent`, the WS broadcast / SSE schema, and every consumer.
- **Alternative (rejected):** New state — `state='TERMINATED'` distinct from FIRED/CANCELLED. Same backpressure, but the parent's `count_pending_for_target_sync` and `_recover_fired_unsent` both need updating; the WS broadcast / SSE schema needs a new state value.
- **Failure if disagreed:** Architect must specify the new-state schema. This plan binds the choice but the alternative is small (~3 files).

### Q3 [ARCHITECT] ✅ CONFIRMED with caveats — Ghost-child semantics: does the parent's completion path tolerate a FollowUp whose child is TERMINATED?

- **Recommended (binding):** Yes — gate clears via `count_pending_for_target_sync(...) == 0` (`child_reports.py:1823`); parent's LLM receives FollowUp.message via `_process_child_completion_and_notify_parent` (`child_reports.py:1490`). The existing tri-state (`claimed` / `already_delivered` / `missing`) already returns `already_delivered` when the obligation is consumed. The ghost-child "waiting for 1 children (bus=True), deferring completion" loop is fixed because `count_pending_for_target_sync(...) == 0` after the FIRED transition (FIRED doesn't count as PENDING, and there is no PENDING for this source-task any more either).
- **Architect site-ref correction (binding, replaces Rev 1):** the actual **PAUSED-guard sites** are at `child_reports.py:1775` and `:1845` — NOT `:898`/`:1244` as Rev 1 listed (the latter two are general idempotency sites, not PAUSED-specific). Task 2.6's File:Line column must reflect this correction.
- **Caveat (binding, new requirement):** the encoded `FollowUp.metadata["child_outcome"]` (per Q2) MUST be surfaced through the graph-node drain (`daemon/graph.py:275-300` + the parent's `report_injection` payload assembly) for **parent-LLM visibility** — the parent's reasoning loop needs to know the child was terminated (not errored, not completed) to take the right follow-up action. This is an additive payload field, not a behavior change at the gate.
- **Alternative (rejected):** Add a "ghost-child" branch at the parent completion site that recognizes a FollowUp whose child status is TERMINATED and short-circuits completion. Larger change; encodes a policy into the gate.
- **Failure if disagreed:** Architect must specify the ghost-child branch. Test 2.6 + 2.10 are the acceptance gates.

### Q4 [ARCHITECT] ✅ CONFIRMED — `fire_for_terminated_target` signature: per-target vs cascade-aware?

- **Recommended (binding):** Per-target only — `async def fire_for_terminated_target(target_instance_id: str, outcome: Outcome) -> list[FollowUp]`. Architect-verified symmetric to `cancel_for_target` (`daemon/services/dependency_bus.py:1025-1098`). **§D (Rev 2.1):** `cancel_for_target`'s only currently-reachable call sites are terminate-side (`:1781` direct + `:1816` via helper; pause-side removed pre-Phase-2 per `instance_lifecycle.py:2240-2266`) — so the symmetric claim is now narrower: the new method parallels the **terminate** path only, not a "both pause and terminate" framing. The terminate path at `instance_lifecycle.py:1816` (post-commit seam — corrected Rev 1 `:1775` ref) already handles the per-instance loop; cascade enumeration is Phase 1 (lineage, AF1 wrapper) and OUT of this phase.
- **Alternative (rejected):** Cascade-aware variant that takes a tree-ids list. Premature — depends on Phase 1's lineage fix.
- **Failure if disagreed:** Architect must specify. This plan binds to per-target.

### Q5 [ARCHITECT] ✅ CONFIRMED with verification — Revive interaction: does `send_message` to a TERMINATED child re-enqueue a duplicate FollowUp for an already-FIRED obligation?

- **Recommended (binding, architect-verified):** No — revival (`daemon/services/instance_messaging.py:1518-1540`) reactivates `instances.status` (e.g. COMPLETED → RUNNING) and queues a fresh MessageQueue/Task for the new user message. It touches **NEITHER** `dependency_watchers` NOR `report_injection` → the FIRED obligation stays terminal. No double-delivery.
- **Verification gate (binding, per architect §3.1 Q5 + Task 2.7):** the existing `claim_for_injection(parent_instance_id)` returns `[]` on second delivery because the guarded `WHERE state='PENDING'` UPDATE (`daemon/repositories/report_injection/repository.py:886`) makes already-claimed rows (state=INJECTED or TASK_DELIVERED) invisible to subsequent claims. This is the natural idempotency mechanism — no explicit one-line guard in `instance_messaging.py` is needed.
- **Alternative (rejected):** Revival must explicitly skip enqueueing for a child whose last obligation is already FIRED. Adds a new branch at `instance_messaging.py:1486-1510`. Not required by the natural idempotency mechanism.
- **Failure if disagreed:** Architect must specify. Test 2.7 + 2.10 are the acceptance gates.

### Q6 [IMPL-DETAIL, but binding review point] ✅ CONFIRMED optional — Sub-fix scope.

- **Recommended (binding, architect-verified):** **Include the sub-fix (task 2.5) — but only the guarded SELECT, not the companion reconciler unless test 2.6 proves it necessary.** Architect-verified: the SELECT at `daemon/services/instance_lifecycle.py:3789-3811` is **blind today** (no `NOT EXISTS` clause on `job_queue_items`). Adding the guarded SELECT is the architecturally correct fix and is the smaller surface. The companion reconciler is **optional** and only warranted if test 2.6 reveals stranded-PAUSED cases — same gating language as Rev 1, now backed by architect verification.
- **Alternative (rejected):** Defer sub-fix to a Phase 3 follow-up. Smaller blast-radius for Phase 2 — rejected because the drift risk is documented in the evidence report (B4 tail) and fixing it now prevents a follow-up phase.
- **Architect decision (binding):** Include the guarded SELECT iff test 2.6 is inconclusive on the bug being present without it. **Marker: the SELECT is required; the companion reconciler is optional** in the task list above.

---

## Success Criteria

| # | Criterion | How to Measure | Threshold |
|---|-----------|----------------|-----------|
| 1 | B2: resume after pause with children completing during pause delivers reports | New e2e `test_resume_after_pause_with_children_completing_during_pause` (task 2.9) | 100% pass; root reaches COMPLETED; exactly N reports delivered (N = # children completed during pause); msg count advances by exactly N |
| 2 | B2: pause→resume→pause→resume cycle leaves no stranded reports | New unit test cycle-2 case (task 2.4) | 100% pass; second compact finds 0 buffered rows |
| 3 | B3: terminate mid-tree child completes the parent | New e2e `test_terminate_mid_tree_with_parent_waiting` (task 2.10) | 100% pass; parent reaches COMPLETED with exactly 1 report (the terminated-child report); no deferring-completion loop |
| 4 | B3: terminate race with concurrent child completion | New unit test (task 2.2 case "race-with-emit_terminal") | 100% pass; exactly-one delivery (whichever side wins) |
| 5 | Revive interaction: sending message to revived TERMINATED child does NOT duplicate FollowUp | New unit test (task 2.7) + e2e (task 2.10) | 100% pass; parent's `report_injection` row count is unchanged after revival |
| 6 | Cancel-during-pause drift sub-fix closes the JobItem+Task drift for **ANY** non-deleted JobItem (Rev 2.1 widening) | New unit test (task 2.5, all 5 cases) | 100% pass; no `(JobItem <any state>, Task PENDING)` shape arises when the JobItem is non-deleted — the JobItem lane always owns the resume decision if the JobItem exists |
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
| `daemon/services/instance_lifecycle.py` | **§D (Rev 2.1)** modify terminate path UNCONDITIONALLY — replace `cancel_for_target` body in `_cancel_bus_watchers_for` (`:74-84`, now terminate-only) with `fire_for_terminated_target`; route `:1781` (pre-existing direct-call duplicate) through the same helper; reorder `_compact_fired_watchers_for_paused` (`:3608-3696`); guarded SELECT in `_resume_cascade_db_sync` (`:3800-3811`) | 2.3, 2.4, 2.5 |
| `daemon/services/instance_messaging.py` | Read-only verification at `:1486-1510`; architect-flagged potential one-line guard | 2.7 |
| `daemon/services/child_reports.py` | Read-only verification at `:898, :1244, :1845`; architect-flagged potential one-line guard | 2.6 |
| `daemon/services/report_delivery_recovery.py` | Read-only end-to-end (per task 2.1) | 2.1 |
| `daemon/graph.py` | **W4 (Rev 2.1):** copy `FollowUp.metadata["child_outcome"]` into `report_injection` payload at the `_process_child_completion_and_notify_parent` path invoked by `claim_for_injection` (`:275-300`) | 2.13 |
| `daemon/services/dependency_bus.py` + `daemon/services/instance_lifecycle.py` payload contract | **W4 (Rev 2.1):** ensure `fire_for_terminated_target`'s metadata propagates to the FollowUp's `report_injection` insertion without field-stripping | 2.13 |
| `tests/unit/services/test_dependency_bus_fire_for_terminated.py` | NEW — 5 cases | 2.2 |
| `tests/unit/services/test_compact_fired_watchers_deliver_before_compact.py` | NEW — 7 cases (Rev 2.1 added W3 sub-class repro) | 2.4 |
| `tests/unit/services/test_resume_cascade_drift_guard.py` | NEW — 5 cases (Rev 2.1 widened: includes the CANCELLED-JobItem case and the soft-deleted-JobItem case) | 2.5 |
| `tests/unit/services/test_parent_completion_idempotency_terminated.py` | NEW — 2 cases | 2.6 |
| `tests/unit/services/test_terminate_path_coverage_fire_with_outcome.py` | **NEW (Rev 2.1 §D)** — 2 cases: every reachable terminate path lands FIRED-with-outcome (never CANCELLED) | 2.3 |
| `tests/unit/services/test_revive_non_replay.py` | NEW — 2 cases | 2.7 |
| `tests/unit/services/test_child_outcome_payload_surfacing.py` | **NEW (Rev 2.1 W4)** — 2 cases: metadata reaches parent's `report_injection` payload; byte-identical on replay | 2.13 |
| `tests/e2e/test_resume_after_pause_with_children_completing_during_pause.py` | NEW | 2.9 |
| `tests/e2e/test_terminate_mid_tree_with_parent_waiting.py` | NEW | 2.10 |

**Total production-code change:** 4 files modified (`dependency_bus.py`, `instance_lifecycle.py`, `graph.py` [W4 Rev 2.1], optionally `instance_messaging.py` if Q5 requires). **Total test addition:** 9 new files, ~25 unit tests + 2 new e2e tests.

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
- **JAFP preserved — `manager.enqueue_message` creates NO JobItem** (architect-verified, `daemon/services/instance_messaging.py:1619`). B2/B3 delivery paths (tasks 2.3, 2.4) route through `manager.enqueue_message` for the FollowUp re-enqueue, which is the existing in-memory MessageQueue/Task seam — NOT a JobItem-creation seam. This preserves the Phase 4 invariant end-to-end (verified Rev 2): pause→resume and mid-tree terminate do not violate "pause writes nothing to JobItems". Task 2.7's revive path uses the same `enqueue_message` seam — also no JobItem creation. **Rev 2.1 — Task 2.13 (W4) preserves JAFP:** the `child_outcome` metadata wiring at `_process_child_completion_and_notify_parent` / `claim_for_injection` (`graph.py:275-300`) is a payload-field copy only — no new JobItem, no new MessageQueue row, no new side-effect. The existing drain path is reused end-to-end.

---

## Open Questions

1. **(added by task 2.1)** Enumerate the 5 lanes of `daemon/services/report_delivery_recovery.py` end-to-end and confirm the no-row-backstop handles the B2 shape (FIRED+enqueued_at IS NULL rows for a paused parent). Append note here on completion.
   **[T2.1 completion note, 2026-08-24 — P2 implementer]** End-to-end read done (recovery service :1-1116 + report_injection repository :580-829 + claim paths). The architect's 5-lane enumeration is CONFIRMED with no structural discrepancy: Lane 1 `_run_deferred_lane` (:522), Lane 2 `_run_no_row_backstop_lane` (:856), Lane 3/4 `_run_pending_age_lane` retry=0 / recovery_retry (:1019), Lane 5 `_run_orphan_lane` (:539, revive-first via `_try_revive_terminal_parent` :748-792). KEY FINDING #1 re-confirmed at current line refs: Lane 2's NOT EXISTS predicate (`repository.py:721-732`, drifted from plan's :715-725) is gated on `dw.state == 'FIRED'` — the B2 shape (FIRED watcher, `enqueued_at IS NULL`, non-terminal i.e. paused parent) is EXCLUDED from the no-row backstop; PAUSED parents are otherwise included by `parent_not_terminal=True` (PAUSED is not in `_PARENT_TERMINAL_STATUSES`), so the FIRED exclusion is the sole gate that strands them. No sweep-time lane visits `dependency_watchers` at all except as Lane 2's exclusion — the entire 5-lane set operates on `report_injection` + `message_queue` rows, so an unstamped FIRED watcher row is invisible to every lane and only `_recover_fired_unsent` (restart path) can see it. The Rev 2.1 conclusion stands and is strengthened: the per-row `enqueued_at` stamp discipline in Task 2.4 Pass 1 is the ONLY within-process durability guarantee. KEY FINDING #2 re-confirmed: `find_pending_past_age` (:768-829) has NO parent-status filter — dead-parent PENDING rows are re-swept forever by Lanes 3/4 (busy-check returns False for terminal parents, so each cycle counts them as `recovered`); same defect class as P1 §2.5, parent-status guard belongs in a follow-up. One new nuance recorded for P3: Lanes 3/4's busy-check DOES protect paused parents' PENDING injection rows from re-entry (skipped_busy), so the B2-adjacent shape is partially fenced at the injection layer even though the watcher layer is unfenced.
2. **(architect review — RESOLVED Rev 2; REFRAMED Rev 2.1)** Q1 ✅ (with **ordering+stamp caveat**, Rev 2.1 W2 — single-transaction claim withdrawn), Q2 ✏️ MODIFIED (metadata encoding), Q3 ✅ (with caveats), Q4 ✅, Q5 ✅, Q6 ✅ (optional) — see §Architect Flags.
3. **(architect review)** Q6 (sub-fix scope inclusion) — decided at task 2.6 outcome.
4. **(post-architect-review)** If the architect picks the alternative for Q2 (new `DependencyWatcherState`), the plan's bound choice shifts and tasks 2.2/2.3/2.4 grow by ~3 files.
5. **(D4 — deferred to follow-up ticket FT-005, Rev 2)**: Lane-5 (`lane_orphan`, `report_delivery_recovery.py:539`) revives terminal parents **first** (`_try_revive_terminal_parent` :748-792) before considering the obligation dead-lettered. Phase 1 §2.5's new dead-letter sweep (companion-artifact disposition, AF2 axis-2 = 2a silent dead-letter with payload retention) answers the same "leader obligation to a dead parent" question **differently** — silent dead-letter without revival. These two policies are asymmetric (pause-drop vs terminate-drop provenance per architect §9 follow-up decision). **Resolution path:** follow-up ticket **FT-005** (owner: leader) — decide whether to align the policies (unify on revive-first OR unify on silent dead-letter) or document the asymmetry as intentional with named-rationale comments at both seams. **OUT of this phase.**
6. **(Rev 2.1 — W3) Stamped-vs-unstamped sub-class pinning.** Task 2.4 test (g) MUST run FIRST among the W3 unit cases — before implementing the per-row stamp discipline in production code, the sub-class repro confirms which strand shape is actually produced by the current crash window between `fire_for_terminated_target` and `mark_enqueued_by_source_target`. If the repro produces BOTH strands (stamped-and-undelivered + unstamped) the dual-lane model (Task 2.4 acceptance) is mandatory. If the repro produces ONLY one strand, the dual-lane model is still required by the per-row-stamp discipline (the unobserved strand would still arise under different failure shapes) — the repro is a confidence-builder, not a gate. **Document repro output** in a 1-paragraph note appended to this Open Questions section before merging.
   **[T2.4 W3 repro completion note, 2026-08-24 — P2 implementer]** The sub-class repro (`tests/unit/services/test_compact_fired_watchers_deliver_before_compact.py::test_g_w3_stamped_vs_unstamped_strand_repro`) was authored and run FIRST — before the two-pass production change landed — and it passed against the pre-fix code (it pins the DB predicate + drain lanes directly, independent of the compact implementation). Repro output: BOTH strand shapes are producible from the crash window — the unstamped strand (FIRED, `enqueued_at IS NULL`, created by firing via the real guarded `transition_state` and crashing before the stamp) is selected EXACTLY by Pass 1's `enqueued_at IS NULL` predicate (asserted: `[wid_unstamped] == selected`), and is surfaced by `_recover_fired_unsent` on a restart-bus over the same engine (asserted: unstamped in the recovered set, stamped NOT in it); the stamped strand (FIRED, `enqueued_at IS NOT NULL`, crash after stamp) is invisible to Pass 1 and drains via `claim_for_injection` at graph dispatch with exactly-once semantics (asserted: first claim returns the content, second claim returns `[]`). The dual-lane model is therefore CONFIRMED as mandatory, matching the Rev 2.1 expectation. Baseline confirmation: the six deliver-path cases ((a)–(f) + ordering pin) all FAILED against the pre-fix code with `enqueue await_count == 0` — the literal B2 repro (no delivery exists today) — and all passed after the two-pass change landed.

---

## Rev 2 Changelog

This revision applies the correction list from `architecture-recommendation.md` (commit `8abca8b5`) §3.1 (Q1–Q6 verdicts), §3.2 (key verifications), §3.3 (two-pass cutoff), and §3.4 (Q plan corrections). Rev 1 is preserved in git (commit `cefb9798`). All changes are editorial — no source code touched, no commit made.

### Applied corrections (item # → what changed)

1. **Task 2.4 (correctness-gating) — rewrote** as two-pass cutoff: Pass 1 = deliver loop with `fired_at <= now()` (ALL buffered, NO grace — the 60s grace would silently strand a child that completed 30s before resume); Pass 2 = original 60s-grace DELETE unchanged. **Atomicity requirement added** (Q1 caveat): wrap Pass 1 + Pass 2 in a single transaction — no lane backstops a missed deliver row (Lane 2's NOT EXISTS at `repository.py:715-725` excludes FIRED rows; `_recover_fired_unsent` is the only recovery path → a crash mid-loop must abort the DELETE, not silently lose the row). Unit-test cases expanded to 5 (added `buffered+fresh (<60s)` and `mid-loop-crash` cases).

2. **Q2 flag section — rewrote.** Rev 1's claim "outcome already serialized in `follow_up_payload`" was false (`dependency_bus.py:162-185` confirms the payload carries only `{target_instance_id, message, source, metadata}`). Corrected design: encode `FollowUp.metadata["child_outcome"] = "terminated"` at the `fire_for_terminated_target` construction site — additive only, opaque to the bus but readable by `_process_child_completion_and_notify_parent` + the graph-node drain. Marked Q2 **✏️ RESOLVED-MODIFIED**.

3. **Task 2.1 — updated.** 5 lanes enumerated per architect §2.5: Lane 1 `_run_deferred_lane` (:522), Lane 2 `_run_no_row_backstop_lane` (:856, queries `find_completed_children_without_delivery`), Lane 3/4 `_run_pending_age_lane` retry=0 + recovery_retry (:1019, queries `find_pending_past_age`), Lane 5 `_run_orphan_lane` (:539, revive-first via `_try_revive_terminal_parent` :748-792). End-to-end read retained as implementation verification. **Two distinct KEY FINDINGS recorded:** (a) Lane 2's NOT EXISTS excludes FIRED → Q1 atomicity rationale (in Task 2.4 + Q1 flag + Risks #2); (b) `find_pending_past_age` has NO parent-status filter → dead-parent PENDING rows re-swept forever by Lanes 3/4 with inflated `recovered` (separate defect class, cross-referenced to P1 §2.5 companion-artifact disposition).

4. **Task 2.3 — line refs corrected.** Rev 1's `:1775` was off. Architect-verified: `cancel_for_target` is called at `:1781` and `:1816` inline in terminate, plus `:74` which is pause-only via `_cancel_bus_watchers_for`. **PATCH ONLY `:1816`** (post-commit seam). `:1781` is a pre-existing duplicate → separate cleanup PR (do NOT bundle; flag for follow-up).

5. **Q3 flag section — rewrote.** PAUSED-guard site refs corrected to `child_reports.py:1775`/`:1845` (not `:898`/`:1244` as Rev 1 listed — the latter two are general idempotency sites, not PAUSED-specific). **New caveat added:** the encoded `FollowUp.metadata["child_outcome"]` MUST be surfaced through the graph-node drain (`daemon/graph.py:275-300`) for parent-LLM visibility. Marked Q3 **✅ CONFIRMED with caveats**. Task 2.6's File:Line column split to distinguish general idempotency sites (`:898, :1244`) from PAUSED-guard sites (`:1775, :1845`) with the metadata-surfacing requirement explicit.

6. **Task 2.7 — added claim_for_injection idempotency verification.** Required sub-assertion that `claim_for_injection(parent_instance_id)` returns `[]` on second delivery — the natural idempotency mechanism is the guarded `WHERE state='PENDING'` UPDATE on `report_injection` rows (`repository.py:886`). On the second call, every row is already `INJECTED` (or `TASK_DELIVERED`), so the SELECT yields zero rows. Load-bearing test for Q5.

7. **Q5 flag section — rewrote as ✅ CONFIRMED with verification.** Revival (`instance_messaging.py:1518-1540`) reactivates status + queues fresh MessageQueue/Task; touches **NEITHER** `dependency_watchers` NOR `report_injection` → FIRED obligation stays terminal, no double-delivery. Verification gate: `claim_for_injection` returns `[]` on second call (per Q5 ✅).

8. **Q6 flag section — rewrote as ✅ CONFIRMED optional.** Include the guarded SELECT (architect-verified blind today at `instance_lifecycle.py:3789-3811`); companion reconciler **only** if test 2.6 reveals stranded-PAUSED cases. Same gating language as Rev 1, now architect-verified.

9. **Q1 flag section — rewrote as ✅ CONFIRMED with atomicity caveat.** `_recover_fired_unsent` (`dependency_bus.py:1554-1606`) sees empty after `enqueued_at` stamp, single invariant holds. **But** no lane backstops a missed deliver row (Lane 2's NOT EXISTS at `repository.py:715-725` excludes FIRED rows; only restart-path catches them). Therefore deliver loop MUST be wrapped in a single transaction with the DELETE pass.

10. **Q4 flag section — rewrote as ✅ CONFIRMED.** Per-target only; architect-verified symmetric to `cancel_for_target` (`:1025-1098`). Line ref corrected to `instance_lifecycle.py:1816` (post-commit seam).

11. **Risks #2 — strengthened.** No lane backstop exists; wrap deliver loop in a single transaction (mandatory, load-bearing mitigation). Atomicity replaces the unit-test pin as the primary control.

12. **Risks #9 — added Outcome dataclass audit.** `Outcome` dataclass at `dependency_bus.py:117` already documents `'terminated'` as a documented value — no type-contract change needed. Equality-branch enumeration verified: only two `outcome.status ==` comparisons in the codebase (`:635`, `:823`), **both** `== "error"` — `'terminated'` does not trip either branch. Safe by construction.

13. **JAFP note added to Hard Constraints.** `manager.enqueue_message` creates NO JobItem (architect-verified at `instance_messaging.py:1619`). B2/B3 delivery paths route through the existing in-memory MessageQueue/Task seam — preserves the Phase 4 invariant end-to-end. Task 2.7's revive path uses the same seam.

14. **D4 reference added to Open Questions (FT-005).** Lane-5 (`lane_orphan`, `report_delivery_recovery.py:539`, revive-first via `_try_revive_terminal_parent`) vs Phase 1 §2.5's new dead-letter sweep (AF2 axis-2 = 2a silent dead-letter) answer the same "leader obligation to a dead parent" question differently. Asymmetric (pause-drop vs terminate-drop provenance per architect §9). Resolution deferred to follow-up ticket **FT-005** (owner: leader) — align the policies or document the asymmetry as intentional. OUT of this phase.

15. **Header updated.** Status line now reads "Draft → **Rev 2 — architect-corrected per architecture-recommendation.md 8abca8b5 (Q1–Q6 resolved)** — see §Rev 2 Changelog". Added explicit Architect reference line documenting `8abca8b5` (the architect-review commit) and `cefb9798` (the Rev 1 preservation commit). D4 (Lane-5/sweep coherence) flagged as FT-005 in the header note.

### Out-of-scope items preserved

- No source-code touched, no commit made (per dispatch contract).
- Task numbering unchanged (no renumbering of 2.1–2.12).
- §Architect Flags section structure preserved beyond resolving Q1–Q6 entries.
- All OUT-of-scope items (B1/B4/B5/B6/B7/SSE) preserved verbatim.
- No new states on `DependencyWatcherState` — Q2's metadata-encoding alternative chosen over 4th-state schema.

---

## Rev 2.1 Changelog

This revision applies the correction list from reviewer council `2bb126df` (verdict timestamp 2026-08-24T19:31:26Z). Rev 2 is preserved above; Rev 1 is preserved in git (commit `cefb9798`); Rev 2 is the architect-corrected baseline (commit `8abca8b5`). **All Rev 2.1 changes are editorial — no source code touched, no commit made (per dispatch contract).**

### Applied corrections (item → what changed)

1. **(W1) Task 2.5 — guard widening.** Dropped the Rev 2 admission-state filter (`IN ('queued','active')`); the new predicate is `NOT EXISTS (SELECT 1 FROM job_queue_items WHERE job_id = task.work_id AND deleted_at IS NULL)` — ANY non-deleted JobItem means the JobItem lane owns the resume decision. Pinned the bug-class: Rev 2's filter was too narrow because a CANCELLED JobItem evaded it → blind `PAUSED→PENDING` flip fired for cancelled-during-pause jobs → `(JobItem CANCELLED, Task PENDING)` residue. Unit test cases expanded from 3 to 5 (added CANCELLED-JobItem case and soft-deleted-JobItem case). Success Criterion 6 rescoped to match: no `(JobItem <any state>, Task PENDING)` shape arises when the JobItem is non-deleted. Risks #13 added — pre-fix residue class persists for existing instances; cross-referenced to the existing Task↔JobItem reconciler gap (Phase 4b/4c deferred) per critical notes.

2. **(W2) Task 2.4 — atomicity reframe.** Rev 2's "wrap Pass 1 + Pass 2 in a single transaction" claim is **withdrawn** — `manager.enqueue_message` owns its own session/transaction (architect-verified at `instance_messaging.py:1619`), so a single shared transaction is not literally implementable. Rev 2.1's safety model is **ordering + per-row stamp discipline**: (a) Pass 1 strictly before Pass 2 (ordering); (b) early-abort on enqueue failure before Pass 2; (c) per-row `enqueued_at` stamp immediately after each successful enqueue — the DELETE's `enqueued_at IS NOT NULL` predicate is the load-bearing durability guarantee (a stamped row survives by construction; an unstamped row is caught next cycle, either by the next resume's Pass 1 or by process-restart `_recover_fired_unsent`); (d) sync→async bridge via `asyncio.run_coroutine_threadsafe` from the sync `_resume_cascade_db_sync` to the manager's event loop (precedent: `_schedule_explicit_handle_resume` at `daemon/manager.py:6134+`); (e) NEW idempotency unit test for the healing path — "pre-existing FIRED row → exactly one enqueue delivered". Unit-test cases expanded from 5 to 7 (added the pre-existing FIRED idempotency case (f) and the W3 sub-class repro (g)). Risks #2 wording updated to match — the per-row stamp discipline replaces the impossible single-transaction claim as the load-bearing mitigation. Q1 flag section rewritten.

3. **(§D) Task 2.3 — stale-premise correction.** Source-verified: only terminate-side `cancel_for_target` call sites are currently reachable — `instance_lifecycle.py:1781` (direct call, pre-existing duplicate) and `:1816` (via `_cancel_bus_watchers_for`, the post-commit seam). Pause-side invocation was REMOVED pre-Phase-2 (evidence: explicit pause-removal comment at `instance_lifecycle.py:2240-2266`, Phase 2 Decision 2, 2026-06-25). Rev 2's "branch on `op`" design with an `op=='pause'` arm is **dead code** — dropped entirely. Task 2.3 rewritten: (a) PATCH `:1816` UNCONDITIONALLY (no op-branch; the helper's `op` parameter becomes logging-only); (b) FOLD `:1781` into Task 2.3 (reviewer allowed either ticket-it or fold-it; folding closes the duplicate within Phase 2) with a TODO comment; (c) ADD path-coverage unit test — every reachable terminate path (direct `:1781` AND helper `:1816`) lands **FIRED-with-outcome** (never CANCELLED) with `metadata["child_outcome"]="terminated"`. Verified Mechanics table for `cancel_for_target` (`:41`) and B3 Recommended Approach (`:115`) updated to reflect the narrower terminate-only call graph. Q4 flag section updated to drop the false "both pause and terminate" framing. Files Touched row updated. **Bracketed erratum note appended** at `architecture-recommendation.md:185` (without rewriting the architect's original text).

4. **(W3) Task 2.4 acceptance — stamped-vs-unstamped sub-class.** Task 2.4's healing depends on which sub-class the stranded rows fall into. Folded into acceptance: (a) unit repro FIRST (test case (g) — construct pre-existing FIRED row in two shapes: stamped `enqueued_at IS NOT NULL` vs unstamped `enqueued_at IS NULL`, assert Pass 1's predicate selects ONLY the unstamped strand); (b) extended test (d) `buffered+enqueued` to ASSERT the healing (exactly-one-enqueue); (c) named BOTH durable delivery paths: unstamped FIRED rows → restart-recoverable via `_recover_fired_unsent` (`dependency_bus.py:1554-1606`); stamped-but-undelivered FIRED rows → drain via durable `report_injection` claim at next dispatch (`graph.py:283` `claim_for_injection`, plus `report_delivery_recovery.py` periodic backstop). Open Questions #6 added — sub-class pinning MUST run first among the W3 unit cases; document repro output before merging.

5. **(W4) New Task 2.13 — child_outcome wiring.** Added task 2.13: route `FollowUp.metadata["child_outcome"] = "terminated"` (set by Task 2.2's `fire_for_terminated_target` and surfaced through Task 2.3's terminate flow) through `_process_child_completion_and_notify_parent` (the path `claim_for_injection` invokes at `graph.py:275-300`) so the parent's LLM-visible `report_injection` payload carries it. Dependency on Tasks 2.2/2.3 is binding (without upstream, 2.13 has nothing to surface). Unit test cases (2): (i) terminate → fire → parent drain → claim → injected message content carries `child_outcome=terminated` marker; (ii) two consecutive claims (replay) → second claim returns `[]` AND payload is byte-identical (idempotency). Coupling Map extended with `W4 wiring (2.13)` column and a downstream-of-B3 note. Files Touched extended with `daemon/graph.py` row + new `tests/unit/services/test_child_outcome_payload_surfacing.py` row. JAFP constraint section updated — Task 2.13 is a payload-field copy only, no new JobItem / MessageQueue / side-effect. Test count in Task 2.8 expanded from 16 to 25.

6. **Citation fix.** Rev 2 cited migration `20260624_000004_report_injection_partial_unique.sql` for the `uq_report_injections_oblig_triple` partial unique index. That file does NOT exist in `daemon/migrations/versions/`. Source-verified: the index is defined inline at line 114 of `daemon/migrations/versions/20260819_000001_report_injections_deferred_marker.sql`. Verified Mechanics table (`:57`) updated with the corrected citation AND a `(Rev 2.1)` flag naming the wrong filename Rev 2 used.

7. **Header updated.** Status line now reads "Rev 2.1 — reviewer-corrected (council 2bb126df: W1, W2, §D, W3, W4 folded) — see §Rev 2 Changelog. Rev 2 is the architect-corrected baseline (commit `8abca8b5`, see §Rev 2 Changelog)." Architect reference line updated to flag the §3.2 `:185` stale premise (Rev 2.1 erratum note appended at `architecture-recommendation.md:185`).

### Out-of-scope items preserved (Rev 2.1)

- No source-code touched, no commit made (per dispatch contract).
- Existing tasks 2.1–2.12 NOT renumbered. **Task 2.13 ADDED** (W4); no other renumbering.
- §Architect Flags section structure preserved beyond Rev 2's Q1–Q6 resolutions; Q1 rewritten (W2 reframing), Q4 updated (§D narrowing), all others preserved.
- All OUT-of-scope items (B1/B4/B5/B6/B7/SSE) preserved verbatim.
- No new states on `DependencyWatcherState` — Q2's metadata-encoding alternative chosen over 4th-state schema.
- Bracketed erratum at `architecture-recommendation.md:185` is **appended**, not a rewrite — the architect's original text is preserved with the erratum as a clearly-marked note below it.

---

## Rev 2.2 Errata (2026-08-24 — Round 2 council dfa8a149)

The Round 2 council (verdict `dfa8a149`, 2026-08-24) returned
NEEDS-FIXES after reachability re-trace. Three errata notes
follow — each is binding for the Round 2 implementation; the
implementation commit documents them in code (see
`child_reports.py:404-498` and `dependency_bus.py:1223-1263` for
the binding changes).

### (i) T2.5 narrowing acceptance (deviation-1) — binding

The T2.5 guard in `_resume_cascade_db_sync` was narrowed (Rev 2.1
W1) to::

    AND NOT EXISTS (
      SELECT 1 FROM job_queue_items
      WHERE job_queue_items.job_id = task.work_id
        AND job_queue_items.deleted_at IS NULL
        AND job_queue_items.job_type <> 'message'
    )

The `job_type <> 'message'` qualifier was the ACCEPTED
deviation-1 from the previous round (council review dfa8a149,
unanimous). JAFP: message-type JobItems are pure mirrors of the
Task row, created by `enqueue_message_job`; the Task lifecycle
owns their visibility. The WorkerPool re-claim drives both Task
and its message-type mirror atomically, so holding a Task PAUSED
because its mirror exists would block the
`PAUSED→PENDING` semantic that the P1 c171a289 full-chain test
pins. The qualifier excludes the JAFP pure-mirror lane from the
guard — a regression-pin test (`test_resume_cascade_drift_guard.py
::test_f_message_type_jobitem_task_flips_pending`) is the binding
acceptance that future re-widening must not break.

### (ii) Task 2.13 real location — fire/enqueue site, NOT graph.py

Task 2.13's `child_outcome` marker MUST land at the SOURCE
(`fire_for_terminated_target` in `dependency_bus.py:1255-1263`,
suffixing `fu.message` with `[child_outcome: <status>]` before
the payload is persisted), NOT in `graph.py` or in the
`child_reports._process_child_completion_and_notify_parent`
marker-lookup block (`child_reports.py:1525-1561`). The latter
block is reached ONLY from natural completion paths
(`manager.py:6399`/`:8922` + completion sweep `:6543`) — NEVER
from terminate. The terminate path reaches the parent via
`_cancel_bus_watchers_for` → `manager.enqueue_message` directly,
with no `_process_child_completion_and_notify_parent` call.
Pre-Round-2 code enriched metadata only (`dataclass_replace`
on the frozen FollowUp) which is invisible to the parent-LLM-
visible content. The marker MUST therefore land in the FollowUp's
`message` text at the source.

The `child_reports.py:1525-1561` lookup block is PRESERVED for
the natural-completion path (where `_process_child_completion_and_notify_parent`
creates the `report_injections` row that the LLM injects); it is
harmless there. It is dead code on the terminate path by design.

### (iii) Stamped-strand modeling error + Blocker-1 fix option (a)

The Rev 2 plan modeled the C1 stamp as conditional on a CRASH
window between `fire_for_terminated_target` and
`mark_enqueued_by_source_target` — the assumption was that
`enqueued_at IS NULL` only arose when the process crashed
mid-stamp. The pre-fix code at `child_reports.py:423-441`
stamps UNCONDITIONALLY: every fired FollowUp gets the stamp
whether or not the parent is paused. Live repro (the 2026-08-24
stranded rows) confirmed BOTH strand shapes are producible, and
the unconditional stamp makes the stamped strand the
production reality — not the modeled crash-only shape.

**Blocker-1 fix option chosen: (a) — gate the C1 stamp on
parent-not-paused.** The stamp is now conditional on
`fu.target_instance_id` not being in the `PAUSED` state. Stamp
semantics become honest: `enqueued_at` means "delivered via the
normal enqueue path". Rows fired while the parent is paused
stay un-stamped → resume's Pass 1 selects them, re-enqueues
each FollowUp via `manager.enqueue_message`, and stamps
immediately after the successful enqueue. Pass 2 DELETEs by
the unchanged `enqueued_at IS NOT NULL` predicate — the
DELETE safety predicate is preserved EXACTLY (the row
population entering the predicate shifts, but the predicate
text is unchanged).

**Composition check (exactly-once delivery):** With C1 un-stamping
for paused parents, the durable `report_injections` row
(created by `_process_child_completion_and_notify_parent` at
`child_reports.py:2864-2875`) AND the Pass-1 FollowUp enqueue
BOTH exist for the same child-completion event. The dedup
is via:

  * The `uq_report_injections_oblig_triple` partial unique
    index (defined in
    `daemon/migrations/versions/20260819_000001_report_injections_deferred_marker.sql:114`)
    — INSERTs into `report_injections` deduplicate by the
    triple.
  * The `claim_for_injection` guarded `WHERE state='PENDING'`
    UPDATE on `report_injection` rows
    (`daemon/repositories/report_injection/repository.py:886`) —
    the second claim returns `[]`, no double-injection.
  * The Pass-1 enqueue creates a fresh `MessageQueue` + `Task`
    pair for the parent; when the parent's turn runs, it
    drains the PENDING `report_injections` row via
    `claim_for_injection` (the natural idempotency mechanism).

The pair deduplicates to exactly-one delivery to the parent.

**Unit-test coverage (post-P2-closure fast-follow, 2026-08-24):**

* **Compact side (resume Pass 1 → stamp → Pass 2 DELETE):**
  `test_compact_fired_watchers_deliver_before_compact.py
  ::test_h_blocker1_chain_paused_parent_no_stamp_then_resume_heals`
  pins the COMPACT half of the chain. It drives `bus.emit_terminal`
  to fire the watcher (PENDING→FIRED) and then hand-rolls the
  un-stamped precondition via a SQL `UPDATE` before invoking
  `_compact_fired_watchers_for_paused`. **It does NOT exercise the
  C1 stamp-gate itself** — the un-stamped state is asserted via the
  hand-rolled UPDATE, not by driving the production
  `_emit_terminal_via_bus` stamping seam. The compact half is the
  natural-completion path's resume-cycle observable shape
  (stamped-and-delivered row → Pass 2 reaps it → no double-
  delivery on a second resume cycle).

* **Stamping side (the `_parent_status == "paused"` gate inside
  `_emit_terminal_via_bus`):** P2 closure fast-follow W-C.a added
  `test_child_outcome_payload_surfacing.py
  ::test_iii_stamp_gate_held_for_paused_parent` and
  `::test_iv_stamp_gate_not_held_for_running_parent`. These drive
  the REAL stamping seam — the production
  `ChildReportsService._emit_terminal_via_bus` helper against a
  real `DependencyBus` + a real `SQLModelInstanceRepository` —
  with a PAUSED parent (asserts `enqueued_at` stays NULL — gate
  HELD) and a RUNNING parent (asserts `enqueued_at` is set — gate
  NOT HELD). Together they pin both halves of the gate's
  contract; a revert (gate always trips or never trips) is
  caught. The earlier pin referencing the pre-existing
  `test_child_outcome_payload_surfacing.py` suite for stamp-gate
  coverage was a false pin — that suite covered the Round 2
  Blocker 2 `[child_outcome: terminated]` marker, NOT the
  parent-paused stamp gate. W-C.b corrected the
  `test_compact_fired_watchers_deliver_before_compact.py::test_h`
  comment to repoint at the new W-C.a tests.

**Why NOT option (b) widen-predicate:** the
`enqueued_at IS NOT NULL` predicate in Pass 2's DELETE
(`instance_lifecycle.py:4159`) is the durability guarantee.
Widening it to `enqueued_at IS NOT NULL OR <other>` would
break the invariant that a stamped row was delivered. Option
(b) would require also widening the `_recover_fired_unsent`
predicate (`dependency_bus.py:1554-1606`) and the
`_recover_fired_unsent` restart-time dedup. Option (a) keeps
the predicates unchanged.

**Why NOT option (c) stamp-after-enqueue:** the C1 stamp is
called inside `_emit_terminal_via_bus` (an async helper
reached from `_process_child_completion_and_notify_parent`'s
`emit_terminal` call); the actual message enqueue for the
natural-completion path happens elsewhere
(`_process_child_completion_and_notify_parent`'s
`MessageQueue`/`Task` INSERT). Reordering C1 to stamp-after-
enqueue in `_emit_terminal_via_bus` would require moving the
stamp into a helper that the natural-completion path also
calls — a structural change that breaks the JAFP invariant
(manager.enqueue_message creates NO JobItem) by introducing
a new helper. Option (a) is the minimum-surface fix.


