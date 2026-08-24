# Plan Overview: pause-resume-terminate-tree-fix (B1–B7 + SSE)

Date: 2026-08-24
Branch: `feature/pause-resume-terminate-tree-fix` @ e6007b8a (planning artifacts committed here)
Status: **Draft — awaiting architect review** (2 pre-flagged hard decisions + 3 additional flags, §Architect Review Routing)
Evidence: `.agents/tester/RESULTS/2026-08-24-pause-resume-terminate-tree-propagation-repro.md` (static + live repro, 6 phases, ~130 evidence files)
Synthesized by: planner[v2] (aggregation of 3 research passes + 3 plan-creation worker outputs)

---

## 1. Objective

Fix the 4 confirmed critical tree-propagation defects (B1–B4) plus the composable secondary defects (B5, B7b), run a timeboxed diagnosis for B6, and file assessment-only follow-up tickets for B7a/B7c/SSE — such that **pause/resume/terminate cascades operate on the complete permanent lineage** and **a parent's completion obligation to its descendants survives pause/resume/terminate transitions**, without violating any project invariant (dependency_bus SOLE completion authority, pause-writes-nothing-to-JobItems, named transitions + reconcile_turn_mirror, canonical terminal_reason, revive semantics, report-lane decoupling).

## 2. Root-Cause Map (verified during planning — supersedes the repro's static hypotheses where noted)

| # | Defect | Root cause (file:line) | Fix phase |
|---|--------|------------------------|-----------|
| B1 🔴 | Pause does not cascade DOWN | Cascades enumerate via `get_tree_ids()` over transient `instance_hierarchy` (repository.py:313-341); rows deleted on child completion (child_reports.py:922, error_reporting.py:233) → churned subtrees invisible (`children=0`) | **P1** |
| B4 🔴 | Terminate-root misses live children | **Primary mechanism** (P1 worker correction): inline hierarchy child query inside `terminate_instance` (instance_lifecycle.py:1385-1393), not just the `:1930` hard-delete snapshot. Tail: orphaned report-to-dead-parent → PENDING forever + `[GUARD]` livelock — **strong hypothesis** (P1 worker correction #4): the pause gate at task/repository.py:~1315-1334 excludes TERMINATED targets for ALL task types; the "reports bypass" comment applies only to the cross-system guard (:1337-1391) | **P1** |
| B2 🔴 | Resume strands root (children completed DURING pause) | `_compact_fired_watchers_for_paused` (instance_lifecycle.py:3608-3696) deletes FIRED watchers before the buffered reports are delivered; no SuspendTurn handle → `invalid_or_missing_handle` → no dispatch → frozen | **P2** |
| B3 🔴 | Terminate has no UP propagation | Terminate path cancels parent-side watcher via `cancel_for_target` (instance_lifecycle.py:74, :1775) — CANCELLED not FIRED-with-outcome → parent's `count_pending_for_target_sync` gate never clears → ghost-child wait | **P2** |
| B5 🟠 | `/stop` acts on wrong target | `/stop` delegates to `pause_instance` (instances.py:1366-1376) which calls the re-rooting `pause_instance_cascade` (instance_lifecycle.py:2050). NOT a mechanical one-liner — a semantics decision (resolved: SUBTREE; see P3) | **P3** |
| B6 🟠 | Detail 404 post-resume | "In-memory wipe" theory REFUTED by P3 verification (read path is DB-backed: instances.py:488-505 → instance_lifecycle.py:2966-2991 → repository.py:222-226). Root cause genuinely unknown → timeboxed diagnosis | **P3 (diagnosis)** |
| B7 🟢 | Timestamp anomalies | (b) `completed_at` re-stamp: unconditional stamp at job_queue/repository.py:2275/2298/2504 — COALESCE guard, fix in P3. (a) +7h future rows + (c) detail-vs-list disagreement: deferred tickets FT-001/FT-002 | **P3 (b only)** |
| SSE 🟡 | Child-cascade events dropped | `status_change` routed by node id only (live_event_hub.py:175-196); MEDIUM effort, self-corrects via 60s polling → ticket FT-003 | **Ticket** |

## 3. Phase Structure

| Phase | File | Defects | Core change | Size |
|-------|------|---------|-------------|------|
| **P1** | `phase1-plan.md` | B1 + B4 | New `get_tree_ids_permanent()` (+ optional `ENSEMBLE_CASCADE_LINEAGE` kill-switch wrapper `get_cascade_tree_ids`); switch 5 site groups (pause :2056, terminate :1385-1393, hard-delete :1930, resume :2300, maintenance :831/:836); terminal-skip classification in terminate recursion; dead-letter path for reports-to-dead-parents (enqueue-time guard + drift-reconciler sweep, canonical `reason='failed'`); data-repair via idempotent sweep; 2 new e2e | 10 tasks |
| **P2** | `phase2-plan.md` | B2 + B3 | Deliver-before-compact in `_compact_fired_watchers_for_paused`; new `DependencyBus.fire_for_terminated_target()` (fire-with-outcome instead of cancel on terminate); JobItem-guarded resume SELECT (cancel-during-pause drift); idempotency verification at child_reports.py:898/1244/1845 + revive interaction; 16 unit + 2 new e2e | 12 tasks |
| **P3** | `phase3-plan.md` | B5 + B7b + B6(dx) + tickets | `cascade_to_root: bool = True` param on `pause_instance_cascade`; `/stop` passes `False` → subtree semantics; `preserve_completed_at` COALESCE guard on `atomic_transition`; B6 timeboxed 2–4h diagnosis (fix-if-small-or-ticket); FT-001/002/003 tickets; 11 unit + 1 new e2e | 12 tasks |

Research inputs (kept for provenance): `research-lineage.md`, `research-obligations.md`, `research-routing.md`.

## 4. Cross-Phase Reconciliation (dispatcher arbitration — READ BEFORE IMPLEMENTING)

All three phases modify `daemon/services/instance_lifecycle.py`. Textual-conflict map and merge order:

1. **Merge order: P1 → P2 → P3** (each rebases on the previous; single atomic merge to the feature branch is also acceptable per worktree pattern).
   - P2's e2e trees need P1's enumeration fix to reproduce B2/B3 conditions (P2 §Coupling).
   - P3's B5 e2e (subtree enumeration) **cannot pass before P1** (P3 §Sequencing) — ship 3.1/3.2 anytime, verify 3.3 after P1.
   - B6 diagnosis (3.4-3.6) runs last, on stable cascades.
2. **Shared-line arbitration — `resume_instance_cascade`:** P1 task 5 owns the one-line enumeration swap at `:2300`; P2 owns `_resume_cascade_db_sync` (:3789-3810 SELECT) and the compaction (:3608-3696). No textual overlap; P1 lands its line first (its recommendation, AF3).
3. **Terminate-path arbitration:** P1 task 3 modifies `terminate_instance` recursion (:1385-1393); P2 task 2.3 branches the `cancel_for_target` call sites (:74, :1775). Adjacent, not overlapping — but the terminal-skip classification P1 adds (skip TERMINAL_STATUSES children) must compose with P2's fire-parent-watcher decision: a skipped (already-terminal) child must NOT fire a second parent-watcher FollowUp. P2's idempotency guards (task 2.6) are the backstop; add an explicit unit case for terminate-of-already-terminal-child during P2 rebase.
4. **⚠️ P3 must adopt P1's wrapper:** P3's `cascade_to_root=False` branch as written calls `repo.get_tree_ids(instance_id)`. After P1, cascades MUST call `get_cascade_tree_ids()` (kill-switch wrapper). **Implementation instruction: P3 task 3.1's else-branch uses `repo.get_cascade_tree_ids(instance_id)`, not raw `get_tree_ids`.** Without this, the B5 subtree fix re-introduces the B1 bug class through a side door.
5. **Shared test gate:** each phase runs its unit suites on landing; the mandatory 5-pack + release-gate e2e suite runs **once on the integrated batch** (see §6) — not 3×.

## 5. Architect Review Routing

Pre-flagged by caller (route to architect BEFORE implementation):

| ID | Decision | Source | Question |
|----|----------|--------|----------|
| **AF1** | Lineage duality | P1 | Deprecate `instance_hierarchy` for cascades entirely, or does any consumer need transient working-set semantics? Remaining transient consumers after P1: visibility tools (V1, deferred) + observer cleanup (O1, deferred). |
| **AF2** | Reports-to-dead-parents terminal path | P1 | Enqueue-time guard + reconcile sweep (recommended, fail-closed) vs claim-time pause-gate carve-out for TERMINATED targets. |
| **Q1–Q6** | Obligation semantics | P2 | Deliver-before-compact vs `_recover_fired_unsent` composition; 4th watcher state vs payload-encoded outcome; ghost-child tolerance; `fire_for_terminated_target` signature; revive/double-delivery interaction; drift sub-fix scope. |
| **AF-B5** | `/stop` public semantics | P3 (additional flag) | SUBTREE (recommended) vs soft-stop vs keep-whole-tree-and-accelerate-deprecation. User-visible behavior change on a deprecated endpoint; 0 external callers found. |
| **AF-B6** | B6 exit condition | P3 (additional flag) | Confirm "no small seam found → ticket only" is acceptable at the 4h cap. |
| **AF-P3-7** | `preserve_completed_at` default | P3 open question 3 | Default `True` (preserve-by-default, recommended) vs `False` (opt-in). No caller relies on re-stamping (verified). |

The two hard ones (AF1 lineage duality, Q1–Q6 obligation semantics) match the caller's stated architect-routing plan. AF2, AF-B5, AF-B6, AF-P3-7 are the additional flags surfaced during planning.

## 6. Test Gate (integrated, run once before merge to `latest`)

Per `.agents/tester/rules/ensure.md` (change set touches job/task/queue system — full e2e MANDATORY):

- **Packs (one-by-one, 5-min cap each, no `-x`):** `claim_guard_locks_unit_test`, `turn_transitions_reconciler_unit_test`, `job_queue_unit_test`, `concurrency_atomic_unit_test` (+ per-phase changed-file suites).
- **Release-gate e2e (exact commands in ensure.md:47-53; PYTEST_TIMEOUT=280, queue cleanup pre-flight, SSL env unset):** `test_parent_child_workflow_happy_path`, `test_pause_after_spawn_then_resume`, `test_terminate_after_spawn_then_revive`, `test_three_level_cascade_reports`.
- **New e2e (5):** P1: `test_pause_root_churned_tree_no_new_work`, `test_terminate_root_prechurn_live_child_not_orphaned` · P2: `test_resume_after_pause_with_children_completing_during_pause`, `test_terminate_mid_tree_with_parent_waiting` · P3: `test_stop_instance_subtree_pause`.
- **New unit (minimum 33 across phases):** P1 ~15 (enumeration/classification/snapshot/guard-repro/dead-letter) · P2 16 · P3 11 (overlapping coverage on `/stop` + COALESCE).

## 7. Consolidated Risks (top cross-phase items; full tables in each phase plan)

| # | Risk | Phases | Mitigation |
|---|------|--------|------------|
| R1 | **Same-file merge conflicts** (`instance_lifecycle.py` touched by all 3 phases) | 1,2,3 | Merge order P1→P2→P3 (§4); dispatcher-arbitrated ownership per line range |
| R2 | **Revive semantics regression** — permanent enumeration exposes TERMINATED/COMPLETED children to cascades | 1 | Classification-before-action in terminate recursion (P1 task 3); pause already classifies (:2094-2102); unit tests pin terminal-status preservation |
| R3 | **Double-delivery of FollowUps** (deliver-before-compact × restart recovery; fire-on-terminate × revive) | 2 | `enqueued_at` stamping contract + tri-state idempotency guards (child_reports.py:898/1244/1845); unit tests 2.4/2.7 pin both races |
| R4 | **Report-lane decoupling / bus-authority erosion** | 2 | No new JobItem-creation sites; new bus method only; sweep finalizes orphaned report Tasks only, never JobItems |
| R5 | **B5 subtree fix re-introduces B1** if P3 uses raw `get_tree_ids` | 1,3 | §4.4 implementation instruction (wrapper adoption); P3 e2e covers it post-P1 |
| R6 | **Pause-first ordering sensitivity** in shared pause path | 1,2,3 | All three phases use existing pause→quiesce→mutate→resume seams; no new ordering introduced |
| R7 | **B4 dead-letter could mask other starvation** | 1 | Predicate scoped to `process_report`-type PENDING with TERMINATED/missing target only; `[GUARD]` diagnostic left intact otherwise |
| R8 | **Known deferred debt not worsened** — `_terminate_instance_db_sync` raw DELETEs (Phase 4b/4c) | 1,2 | Explicitly out of scope in both plans; both only depend on its ordering, don't migrate it |

## 8. Rollback (batch-level)

- Every phase is additive/mechanical with straight-revert stories (per-phase tables in each plan); no schema migrations anywhere in the batch.
- P1 kill-switch `ENSEMBLE_CASCADE_LINEAGE=hierarchy` provides hot-path fallback without code revert.
- Dead-letter writes are terminal-on-garbage (idempotent sweep; reverting re-exposes the defects, corrupts nothing).
- `/stop` response shape is byte-compatible before/after (only `paused_ids` content changes).

## 9. Success Criteria (batch-level)

1. All 4 critical defects (B1–B4) closed, each pinned by its new e2e passing in the integrated gate.
2. `/stop` targets the requested subtree; `/pause` whole-tree semantics unchanged (unit case 6 + existing e2e).
3. No PENDING-forever report rows, no `[GUARD]` livelock; dev-env stranded row `d14cbde5` repaired via the sweep.
4. `completed_at` no longer re-stamped (P3 unit cases).
5. B6: small fix shipped OR ticket with diagnosis bundle; FT-001/002/003 filed.
6. Integrated test gate §6 fully green; no non-canonical `terminal_reason` in new code paths; `DependencyWatcherState` unchanged; dependency_bus remains sole completion authority.
7. Architect answers recorded for AF1, AF2, Q1–Q6, AF-B5, AF-B6, AF-P3-7 (decisions.md to be appended post-review).

## 10. Follow-Up Tickets Filed by P3 (out of batch)

FT-001 (B7a future-dated rows), FT-002 (B7c status disagreement), FT-003 (SSE fan-out) — plus optionally a B6 ticket. Deferred-with-name from P1: V1 (visibility tools enumeration), O1 (observer `_cleanup_descendants_of`).

## 11. Out of Scope (batch guardrails)

Phase 4b/4c turn-reconciler migration (`_terminate_instance_db_sync`, `_finalize_job_db_sync`); F9/F16 residuals; `_llm_select`/HA splits; any `emit_terminal` API redesign beyond B3's minimum; new watcher states; schema changes; SSE implementation; B7a/B7c implementation.
