# Plan Overview: pause-resume-terminate-tree-fix (B1–B7 + SSE)

Date: 2026-08-24 (Rev 2.1 — architect-corrected @ 8abca8b5 + reviewer-corrected per council 2bb126df; decision log in `decisions.md`)
Branch: `feature/pause-resume-terminate-tree-fix` (planning artifacts committed here)
Status: **Rev 2.1 — reviewer council 2bb126df APPROVED (unanimous, 0 critical); required corrections W1/W2/§D/W6/W9 + kickoff W3/W4/W7/W8 folded. READY FOR IMPLEMENTATION DISPATCH.**
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
| B7 🟢 | Timestamp anomalies | (b) `completed_at` re-stamp: REVISED per architect — `rearm_with_lock` EXISTS (F9 closed, job_queue/repository.py:1974-2167); re-stamps are most plausibly re-arm + resume-finalize composition, i.e. **working-as-designed pending the 30-min repro-DB check (leader D2)**; P3 re-scoped to verify+pin (last-settle semantics), no COALESCE wiring. (a) +7h future rows + (c) detail-vs-list disagreement: deferred tickets FT-001/FT-002 | **P3 (verify+pin)** |
| SSE 🟡 | Child-cascade events dropped | `status_change` routed by node id only (live_event_hub.py:175-196); MEDIUM effort, self-corrects via 60s polling → ticket FT-003 | **Ticket** |

## 3. Phase Structure

| Phase | File | Defects | Core change | Size |
|-------|------|---------|-------------|------|
| **P1** | `phase1-plan.md` (Rev 2) | B1 + B4 | `get_tree_ids_permanent()` + hardened kill-switch wrapper `get_cascade_tree_ids` (removal ticket FT-004); 5 site-group switch (pause :2056, terminate :1385-1393 **enumerate-first restructure per AF1-C1**, hard-delete :1930, resume :2300, maintenance :831/:836); **normative terminal-skip rule** (classification gates ACTING, never TRAVERSAL); dead-letter via **`DeadLetterTurn`** (PENDING→FAILED, `reason='failed'` per D3) replacing the no-op `fail_task` path (AF2-C1) + **companion injection-row disposition** (AF2-C3) at verified seam `child_reports.py:2638-2663` + secondary seams `manager.py:6829-6945`; ~8-suite mock migration named task; 2 new e2e | 10 tasks (Rev 2) |
| **P2** | `phase2-plan.md` (Rev 2) | B2 + B3 | Deliver-before-compact **two-pass cutoff** (no-grace deliver pass + 60s-grace DELETE), **atomic with the DELETE** — no lane backstop exists (Q1 caveat); `fire_for_terminated_target` with **`FollowUp.metadata["child_outcome"]="terminated"`** (Q2 — payload does NOT carry outcome.status); patch seam corrected to `:1816` (`:1781` pre-existing duplicate → separate PR); JobItem-guarded resume SELECT; verified 5-lane enumeration recorded (Lane 2 excludes FIRED rows; Lane-3/4 fight resolved by P1's companion disposition); 16 unit + 2 new e2e | 12 tasks (Rev 2) |
| **P3** | `phase3-plan.md` (Rev 2) | B5 + B7b(verify+pin) + B6(dx) + tickets | `cascade_to_root: bool = True` param; **both branches enumerate via `get_cascade_tree_ids()`** (AF-B5 correction); `preserve_completed_at` flag DEFINED but UNWIRED (default `False` mandatory; task 3.8 deleted per AF-P3-7); B6 probe-first checklist (5 probes, 404-body classifier first); FT-001/002/003(+FT-004/005 via decisions) tickets; unit cases 7/8 pin the load-bearing default | 11 tasks (Rev 2) |

Research inputs (kept for provenance): `research-lineage.md`, `research-obligations.md`, `research-routing.md`.

## 4. Cross-Phase Reconciliation (dispatcher arbitration — READ BEFORE IMPLEMENTING)

All three phases modify `daemon/services/instance_lifecycle.py`. Textual-conflict map and merge order:

1. **Merge order: P1 → P2 → P3** (each rebases on the previous; single atomic merge to the feature branch is also acceptable per worktree pattern).
   - P2's e2e trees need P1's enumeration fix to reproduce B2/B3 conditions (P2 §Coupling).
   - P3's B5 e2e (subtree enumeration) **cannot pass before P1** (P3 §Sequencing) — ship 3.1/3.2 anytime, verify 3.3 after P1.
   - B6 diagnosis (3.4-3.6) runs last, on stable cascades.
2. **Shared-line arbitration — `resume_instance_cascade`:** P1 task 5 owns the one-line enumeration swap at `:2300`; P2 owns `_resume_cascade_db_sync` (:3789-3810 SELECT) and the compaction (:3608-3696). No textual overlap; P1 lands its line first (its recommendation, AF3).
3. **Terminate-path arbitration:** P1 task 3 modifies `terminate_instance` recursion (:1385-1393); P2 task 2.3 branches the `cancel_for_target` call sites (:74, :1775). Adjacent, not overlapping — but the terminal-skip classification P1 adds (skip TERMINAL_STATUSES children) must compose with P2's fire-parent-watcher decision: a skipped (already-terminal) child must NOT fire a second parent-watcher FollowUp. P2's idempotency guards (task 2.6) are the backstop; add an explicit unit case for terminate-of-already-terminal-child during P2 rebase.
4. **⚠️ P3 must adopt P1's wrapper — BOTH branches (architect-hardened, AF-B5):** the P3 sketch as originally written called raw `get_tree_ids` in the True branch, the else branch, AND the task-3.1 acceptance text — implemented verbatim, `/pause`, messaging, and watchover would silently bypass P1's kill-switch (a second side door beyond the else-branch issue originally flagged here). **Implementation instruction: EVERY enumeration call in P3's `cascade_to_root` parameterization — both branches and all acceptance/sequencing text — uses `repo.get_cascade_tree_ids(...)`.** The True branch inherits P1's swap at `:2056`; P3 rebases on P1. Additionally: the default `True` is load-bearing for 5 internal callers (`instance_messaging.py:1119/:3748`, `watchover_service.py:1004/:1470`, `manager.py:7690`) — pinned by unit cases 7/8.
5. **Shared test gate:** each phase runs its unit suites on landing; the mandatory 5-pack + release-gate e2e suite runs **once on the integrated batch** (see §6) — not 3×.

## 5. Architect Review — RESOLVED (Rev 2)

Review delivered at `architecture-recommendation.md` @ 8abca8b5 (1 council + 3 skill-per-worker dispatches, all citations in-code verified). Full log in `decisions.md`. Summary:

| ID | Resolution |
|----|------------|
| **AF1** | ✅ Staged deprecation (AF1-A + governance) — leader **D5**. `get_tree_ids()` kept during migration w/ corrected docstring, deprecated after V1/O1 pass; no new hierarchy readers; documented forever-dual flip condition (zero mechanical cost) |
| **AF2** | ✅ Enqueue guard at verified seam `child_reports.py:2638-2663` + secondary seams `manager.py:6829-6945` + drift sweep; silent dead-letter w/ payload retention. Dead-letter mechanism REPLACED (`DeadLetterTurn` — `fail_task` provably no-ops on PENDING); companion injection-row disposition mandatory (Lane-3/4 re-sweep) |
| **Q1–Q6** | ✅ All resolved — Q1 confirmed w/ atomicity caveat (two-pass cutoff + atomic-with-DELETE); Q2 modified (metadata encoding); Q3 confirmed (sites `:1775`/`:1845`); Q4/Q5 confirmed; Q6 optional-confirmed |
| **AF-B5** | ✅ SUBTREE via boolean param; **both branches use `get_cascade_tree_ids()`** (§4.4); 5 internal callers make default load-bearing → unit cases 7/8 |
| **AF-B6** | ✅ Probe-first timebox (5 ordered probes, 404-body classifier decisive; H1 harness-artifact top hypothesis — DB-read seam ruled out); "no small seam → ticket" accepted at 4h cap |
| **AF-P3-7** | ✅ `preserve_completed_at` default **`False` MANDATORY**; task 3.8 (wiring `True`) **DELETED** — `rearm_with_lock` exists, F9 closed; preserve-on-re-complete freezes failure-time stamps. B7(b) re-scoped verify+pin (**D2**, pending 30-min repro-DB check) |

**Correctness-gating corrections folded (would have shipped dead/corrupting fixes if implemented verbatim):** P1 terminate enumerate-first restructure; P1 dead-letter mechanism replacement + companion disposition; P2 two-pass atomic cutoff; P3 both-branch wrapper; P3 task-3.8 deletion.

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
| R5 | **B5 subtree fix re-imports B1** via either `get_tree_ids` branch | 1,3 | §4.4 hardened per architect: BOTH branches + acceptance text use `get_cascade_tree_ids()`; unit cases 7/8 pin the load-bearing default; P3 e2e covers it post-P1 |
| R5b | **Task-only dead-letter converts livelock into a silent metric lie** (Lane-3/4 perpetual re-sweep, `recovered` inflated) | 1 | Companion injection-row disposition at enqueue + sweep (AF2-C3); acceptance asserts lanes stop matching |
| R5c | **Deliver-before-compact strands rows fired <60s before resume; no lane backstop** | 2 | Two-pass cutoff (no-grace deliver pass) + deliver loop atomic with the DELETE pass (Q1 caveat); unit test 2.4 extended |
| R5d | **`completed_at` preserve-wiring freezes failure-time stamps on re-armed jobs** | 3 | Task 3.8 DELETED; default `False` mandatory (AF-P3-7); task 3.9 inverted to pin last-settle semantics; D2 flip condition documented |
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
4. B7(b): re-scoped **verify+pin** — re-arm→re-complete stamps `completed_at=T2` (last-settle) pinned by inverted task 3.9; 30-min repro-DB check (D2) gates the working-as-designed conclusion, with documented flip condition.
5. B6: probe-first diagnosis completes with 404-body class identified AND (seam classified OR harness artifact confirmed w/ corrected repro); FT-001/002/003/004/005 filed.
6. Integrated test gate §6 fully green; no non-canonical `terminal_reason` in new code paths; `DependencyWatcherState` unchanged; dependency_bus remains sole completion authority; Lane 3/4 stop matching dead-parent rows (no inflated `recovered`).
7. Architect answers + leader decisions D1–D5 recorded in `decisions.md` (DONE at Rev 2).

## 10. Follow-Up Tickets Filed by P3 (out of batch)

FT-001 (B7a future-dated rows), FT-002 (B7c status disagreement), FT-003 (SSE fan-out), **FT-004** (kill-switch removal criterion, ~+30 days post-soak + V1/O1), **FT-005** (Lane-5-vs-sweep policy coherence, leader D4) — plus optionally a B6 ticket. Deferred-with-name from P1: V1 (visibility tools enumeration), O1 (observer `_cleanup_descendants_of` — carries the AF1 flip-condition verification).

## 11. Out of Scope (batch guardrails)

Phase 4b/4c turn-reconciler migration (`_terminate_instance_db_sync`, `_finalize_job_db_sync`); F9/F16 residuals; `_llm_select`/HA splits; any `emit_terminal` API redesign beyond B3's minimum; new watcher states; schema changes; SSE implementation; B7a/B7c implementation.
