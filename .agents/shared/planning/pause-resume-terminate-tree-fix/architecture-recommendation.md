# Architecture Recommendation: pause-resume-terminate-tree-fix (B1–B7)

Date: 2026-08-24
Status: **COMPLETE — architect review delivered** (pre-implementation, diagnosis/design only)
Plan ref: `.agents/shared/planning/pause-resume-terminate-tree-fix/` @ branch `feature/pause-resume-terminate-tree-fix` (cefb9798)
Evidence: `.agents/tester/RESULTS/2026-08-24-pause-resume-terminate-tree-propagation-repro.md` (live repro, 6 phases)
Method: 1 council (AF1, 2 councilors on distinct models, skill `structural-design`) + 3 skill-per-worker dispatches (`resilience-design` → AF2, `data-flow-design` → Q1–Q6, `trade-off-analysis` → AF-B5/B6/P3-7). All read-only; all citations below were verified in-code by the dispatched units this session.

Instance IDs: council governor `19b0ca59-58fc-478c-9d1d-6cd2be2fcefb`; workers `bd740c83-26e7-4fea-9e30-49431809483d` (AF2), `cb277edc-74e2-4e14-bacd-8d9a6b5d5d79` (Q1–Q6), `70f6581e-c348-4c6c-880d-bbd18948047d` (P3 cluster).

---

## 0. Decision Summary

| ID | Question | Decision | Confidence | Plan impact |
|----|----------|----------|------------|-------------|
| **AF1** | Lineage duality end-state | **A mechanics + B governance → staged deprecation.** Switch 5 mutation sites to `get_tree_ids_permanent()` behind `get_cascade_tree_ids()` wrapper; keep `get_tree_ids()` during migration with corrected docstring, then deprecate; reject making hierarchy permanent | High | 🔴 Task 3 must be **restructured enumerate-first** (C1); 11 more corrections |
| **AF2** | Dead-letter path shape | **Axis 1 = 1a+1c** (enqueue-time guard at the *verified* seam + secondary reconcile seam + drift sweep); **Axis 2 = 2a** silent dead-letter with payload retention; reject claim-time carve-out and cleanup-only | High | 🔴 `fail_task` cannot dead-letter PENDING rows — mechanism must be replaced (§2.3); companion-artifact disposition added |
| **Q1–Q6** | Obligation semantics B2+B3 | **Q1 ✅ (with atomicity caveat) · Q2 ✏️ MODIFY · Q3 ✅ (site refs corrected) · Q4 ✅ · Q5 ✅ · Q6 ✅ (optional)** | High | Task 2.4 needs a two-pass cutoff fix; Q2 needs metadata encoding design |
| **AF-B5** | `/stop` semantics | **SUBTREE via `cascade_to_root: bool = True` boolean param** (option i); node-only rejected outright | High | 🔴 Re-import risk is **worse than §4.4 flagged** — both sketch branches call raw `get_tree_ids` |
| **AF-B6** | Diagnosis exit condition | **Probe-first timebox** (option A); 5-minute 404-body classifier leads; harness-artifact is the top hypothesis | Medium-High | Task 3.5 gets a deterministic probe checklist |
| **AF-P3-7** | `preserve_completed_at` default | **`False` (opt-in) is MANDATORY — and task 3.8 (wiring `True` at 3 call sites) must be DELETED.** `rearm_with_lock` exists; preserve-on-re-complete freezes false timestamps | High (default) / Medium (B7b reclassification) | plan-overview §5 row is wrong; phase3 §B7(b) factual claim is false |

**Overall verdict on the plan:** structurally sound; all four hard mechanisms (permanent enumeration, deliver-before-compact, fire-with-terminated-outcome, subtree `/stop`) confirmed viable. Three correctness-gating defects found in the plan as written — **P1-C1 (terminate restructure), AF2-C1 (dead-letter mechanism no-op), P3 task 3.8 (false timestamps)** — each of which would ship a dead or corrupting fix if implemented verbatim.

---

## 1. AF1 — Lineage Duality (B1+B4 core)

### 1.1 Decision

Adopt **Approach A mechanics with Approach B's governance artifacts folded in; rule the end-state staged deprecation (dual-track → deprecate)**; reject Approach C outright.

- **P1 mechanics (unanimous):** switch all 5 mutation sites — pause `instance_lifecycle.py:2056`, terminate `:1385-1393`, hard-delete snapshot `:1930`, resume `:2300`, maintenance `maintenance.py:831/:836` — to the new `get_tree_ids_permanent()` behind the `get_cascade_tree_ids()` wrapper (kill-switch `ENSEMBLE_CASCADE_LINEAGE`, default `permanent`).
- **End-state (synthesis ruling on the one real disagreement):** `get_tree_ids()` is kept **during migration** with a **corrected docstring** — the "active working set" framing is *false* (see §1.4) — then deprecated (marker → removal after one release cycle) once the V1 fast-follow and O1 verification pass land. No new hierarchy readers permitted. `instance_hierarchy` table and all deletion sites unchanged.
- **Flip condition (documented):** if O1's verification pass shows observer Tier-2 cleanup is *harmed* by terminal descendants (non-idempotent artifact corruption on re-clean), fall back to B's forever-dual end-state. Zero mechanical cost — the P1 diff is identical either way.

### 1.2 Trade-off Matrix (council-merged, weights 20/20/25/20/15)

| Axis | A. Permanent-only (staged deprecation) | B. Hybrid formalized forever | C. Hierarchy permanent |
|---|---|---|---|
| **Complexity** | Medium — 1 helper + ~15-line wrapper + 5 swaps + mock migration (~8 suites) | Medium-high — A's diff + contract docs + O1 verification pulled forward | Medium-low code / **high semantics** — rework 4 churn deleters, audit 6 more, add revive writers, rewrite c10 suite |
| **Scalability** | High — indexed per-level BFS (`ix_instances_parent_id`); admin-frequency; hierarchy keeps draining | High — identical runtime | **Worst** — junction table grows unboundedly; second permanent lineage store |
| **Maintainability** | High — transitional dual-source, **time-boxed with named exit** | Highest-if-contract-were-true; entrenches a **lossy table as canonical forever** (revive gap) | Lowest — transience semantics destroyed; every reader ambiguous forever |
| **Risk** | Medium-low — R2 covered by terminal-skip (post-C1); hard-delete behavior change observable (needs sign-off) | Low-medium — same operational risk + commits to a contract the revive path already violates | Highest — 9 readers change atomically; report-lane write surgery; schema change for soft-delete (**violates the no-migration batch constraint**) |
| **Cost** | Medium (~2–3 eng-days) | Medium-high (~3–4) | Highest (~5–7) |
| **Weighted** | **4.00** | 3.65 | 2.00 |

### 1.3 Rationale

- **C is strictly dominated:** it duplicates `parent_id`'s semantics in a second table forever, violates the blueprint "cascade cleanup drains correctly" invariant at its codification points (c10 acceptance suite `tests/unit/test_instance_children_junction_c10.py`; terminate Step-5 design comment `instance_lifecycle.py:3324-3329`), and its soft-delete variant requires a schema change — prohibited in this batch.
- **B's core premise is false:** the revive path (`instance_messaging.py:1510-1530`) transitions terminal→RUNNING but **never re-inserts a hierarchy row** — writers exist only at `repository.py:206` and `instance_lifecycle.py:3450`. A live revived instance is invisible to every hierarchy reader. A "working set" that misses live instances is not a working set. Decisive counterevidence, undisputed.
- **B's own end-state already contains 90-day V1/O1 migration tickets** — implying convergence, not permanence.
- **Transience is by design for drains, not for lineage** — the 5th deletion site (`_terminate_instance_db_sync`, `instance_lifecycle.py:3324-3333`, discovered by the council) makes the table *more* transient than the plan acknowledged; every terminate severs the subtree from transient view.

### 1.4 Terminal-Skip Rule (normative — plan C3; applies to ALL cascade sites including P3's `/stop`)

> **Classification gates ACTING on a node. It never gates TRAVERSAL of that node's subtree.** Enumerate the complete tree first, then classify per node; terminal/already-target-state nodes are skipped *as nodes* (into `skipped_ids`, status untouched, no re-stamp, no per-node emit) while their descendants — independent entries of the same complete list — are visited normally.

This answers the dispatcher's question directly: **skip terminal nodes, never their still-live descendants** — a live grandchild under a completed child MUST be acted on. Composition verified: pause composes today (enumerate `:2056` → classify `:2094-2102`); resume composes (`:2300` → only-PAUSED filter `:2325-2328`); **terminate does NOT compose as planned — see C1**.

### 1.5 🔴 C1 — Terminate must be restructured enumerate-first (correctness-gating)

The plan's Task 3 ("add terminal-skip classification BEFORE recursing") has two traced failure modes:

1. The re-entrancy guard (`instance_lifecycle.py:1362-1370`) returns **before** the child-enumeration block (`:1381-1396`) — call-through on a TERMINATED child never reaches its grandchildren (re-creates B4 one level down).
2. The guard checks only TERMINATED — **COMPLETED children pass and get re-terminated, re-stamping `terminal_reason="aborted"` over their true reason** — a direct violation of the canonical-terminal_reason hard constraint (the R2 hazard).

**Fix:** restructure terminate to enumerate-first — snapshot `get_cascade_tree_ids()` up front, classify per node, act on non-terminal nodes only — structurally mirroring pause/hard-delete/resume. Add the missing unit case: *terminal child with live grandchild → grandchild terminated, terminal child's status AND `terminal_reason` untouched* (e2e spec 2's revived-mid-sleep shape does not cover this).

### 1.6 Kill-switch verdict — KEEP, time-boxed, hardened

- **Reliable here:** the F2 env-allowlist stripping lives at `upgrade_journal.py:986-1003` for the bash upgrade-executor subprocess only; daemon Python reads its own `os.environ`. (F2 lesson does not apply.)
- **Narrowly useful:** churn deletes hierarchy rows within minutes-to-hours, so `hierarchy` fallback only helps un-churned trees — flipping back would NOT restore pre-fix behavior, it would expose a different broken state.
- **Mandatory hardening (C4):** boot-time loud log of resolved mode; unknown value → WARN + default `permanent`; docstring "deploy-window escape hatch — meaningful only for trees that haven't churned since P1 deployment"; restart-required semantics documented; explicit removal criterion (new ticket **FT-004**, ~+30 days post-soak + V1/O1 decisions).

### 1.7 Blast-Radius Inventory (condensed; full table in council record)

| Observer | Verdict |
|---|---|
| API shapes `paused_ids`/`skipped_ids` | 🟢 shape-compatible; content grows only in churned trees (the fix) |
| SSE per-node emits | 🟢 safe; classify-before-emit pinned as invariant (C8); routing limitation is orthogonal FT-003 |
| WS broadcaster | 🟢 creation-time only |
| **~8 test suites mocking `get_tree_ids`** (`test_pause_instance_cascade`, `test_tree_aware_pause_resume`, `test_maintenance`, `test_instance_hard_delete`, `test_paused_instance_ttl:756`, `test_instance_lifecycle_h10_l14`, `test_cascade_pause_resume`, `test_injection_sse`) | 🟡 **named mock-migration task (C2)** — not incidental edits |
| Zombie reaper, checkpoint orphan GC, watchover | 🟢 covered/inherited/unaffected |
| Protected-instance TTL | 🟡 intended polarity change; add metric `pinned_subtree_terminal_count` (C11) |
| Revive semantics (R2) | 🟢 **strictly better** — today the severed row at `:3331` hides revived orphans forever; pin with unit tests |
| `job_tree` display | 🟢 already renders from `parent_id` (`job_queue.py:~1583`); V1 deferred |
| **Hard-delete snapshot `:1930`** | 🟡 **the one user-facing behavior change** — completed-descendant checkpoints now swept by root hard-delete → **product sign-off required (C7)** |

### 1.8 AF1 plan corrections (phase1-plan.md unless noted)

| # | Severity | Location | Correction |
|---|---|---|---|
| C1 | 🔴 | Task 3 (`:72`) + Risks R1 | Rewrite T3 as enumerate-first restructure (see §1.5) + missing unit case |
| C2 | 🟡 | Tasks 2/4/5/6 acceptance | Name the mock migration explicitly (~8 suites) |
| C3 | 🟡 | Status-filter §(`:29`) + AF6 | Elevate terminal-skip rule to normative (§1.4) for all future cascade sites incl. P3 `/stop` |
| C4 | 🟡 | Task 1 + Rollback Story | Kill-switch hardening + FT-004 removal ticket |
| C5 | 🟢 | AF1 flag (`:150`) | Attach deciding evidence (revive gap; `job_tree` renders from parent_id; `list_parents_with_active_children` `:655-664` has zero callers) |
| C6 | 🟢 | plan-overview §2 + blueprint post-P1 | Amend "query-time lineage survives revive": hierarchy = lossy spawn-to-report edge table under revive; parent_id = lineage authority; no new hierarchy readers |
| C7 | 🟡 product | T4 acceptance | Hard-delete snapshot expansion is a behavior change — product sign-off |
| C8 | 🟢 | T3 acceptance | Pin classify-before-emit for future per-node SSE |
| C9 | 🟢 | T4 / coupling ¶ | T4 correctness does NOT depend on `:3331` ordering — permanent enumeration decouples the snapshot |
| C10 | 🟢 | Approach ¶ (`:26`) | Fix the "No recursive CTE" rationale (SQLite has had `WITH RECURSIVE` since 3.8.3); real argument = consistency with existing Python-side BFS helpers |
| C11 | 🟢 | Risks R6 + T6 | Add `pinned_subtree_terminal_count` metric |
| C12 | 🟢 | New helper docstring | `_MAX_TRAVERSAL_DEPTH` (256) truncates silently — document cap behavior |

---

## 2. AF2 — Dead-Letter Path for Reports-to-Dead-Parents (B4 tail)

### 2.1 Root cause — VERIFIED by direct code read

The pause gate in `claim_pending_task` (`task/repository.py:1315-1336`, bound `:1412-1413`) is `AND instance_id NOT IN (SELECT … WHERE status IN (paused, terminated))` with **no task_type discriminator** — its own comment (`:1316-1333`) states it applies "uniformly for every task type — user messages and reports alike." The research's "reports bypass" reading belongs to the **cross-system guard** (`:1337-1351`, job-coordination only). A PENDING `process_report` task targeting a TERMINATED instance is permanently unclaimable; the `[GUARD]` diagnostic (`:1459-1484`) fires every returning-None poll → the ~3s livelock. Confirms plan Research Correction #4.

### 2.2 Axis 1 — where the guard hooks

| Option | Complexity | Scalability | Maintainability | Risk | Cost |
|---|---|---|---|---|---|
| **1a Enqueue guard + drift sweep** ✅ | Med (2 seams + 1 predicate + terminal write) | ✅ O(stranded rows), 60s loop | ✅ Gate untouched; narrow predicate | 🟢 fail-closed at creation; sweep heals races | Small |
| 1b Claim-time carve-out | Med | ✅ | 🟡 violates the gate's documented uniform contract | 🔴 claimed task drives a real graph turn on a TERMINATED instance (checkpoint writes, possible child spawns; parent-cascade guard `:2916-2921` excludes COMPLETED/ERROR/PAUSED but **not TERMINATED**) | Small-Med |
| 1c Hybrid seams | Med | ✅ | 🟡 two enforcement points | 🟡 Med | Med |

**Decision: 1a + the 1c secondary seam.** TOCTOU: window 1 (terminate between read `:2638` and commit) and window 2 (post-creation pre-claim) both heal via the sweep (age ≥300s default); the sweep's own race vs concurrent revive is closed by folding the parent-status `EXISTS` **into the dead-letter UPDATE's WHERE clause**. The already-stranded `d14cbde5`-class rows need no one-time migration — the sweep's first run IS the repair (plan Task 9 correct). **1b rejected:** it erodes the gate invariant and risks graph turns on dead instances. **1c finding:** the reconcile artifact-manufacturing branches (`manager.py:6829-6945`, sub-shapes a/b) also INSERT PENDING `process_report` tasks with no parent-status check — a complete design guards both seams (cheapest as 1a + these two + sweep).

### 2.3 🔴 C1 — the plan's dead-letter mechanism is a no-op as written

`fail_task` (`task/repository.py:1878-1983`) pre-checks `prior_row[0] != RUNNING → return None` (`:1923-1928`); `AbortTurn.run` gates `status IN ('running','paused')` (`turn_transitions.py:402-415`). **No named transition can terminal-write a PENDING row.** Precedent that exists: `cancel_task`'s cold path (`:3197-3206`, guarded legacy UPDATE `pending/paused→cancelled`) + post-commit `reconcile_turn_mirror` (`:3247`).

**Fix:** dead-letter via a `DeadLetterTurn` named transition (PENDING→FAILED, `MIRROR_SET=ALL_8_MIRRORS`, registered in `TRANSITIONS`, `turn_transitions.py:583`) — or, if the transition set must stay frozen this batch, a repository wrapper following the `cancel_task` cold-path precedent + post-commit `reconcile_turn_mirror(work_id)`.

### 2.4 Axis 2 — disposition once detected

| Option | Complexity | Scalability | Maintainability | Risk | Cost |
|---|---|---|---|---|---|
| **2a Silent dead-letter + payload retained** ✅ | 🟢 Low | ✅ | ✅ queryable rows; one structured log key | 🟢 Low | Tiny |
| 2b Synthesized failure-report to live ancestor | High (ancestor walk `:343-361` + new obligation triple + composition) | 🟡 per-event LLM turn on ancestor | 🟡 second notification channel; duplicates B3 territory | 🟡 ancestor's dep-bus wait is keyed to the ORIGINAL parent — informs but does not resolve; double-report risk once B3 lands | Med-High |
| 2c Cleanup-only | 🟢 zero code | ✅ | ✅ | 🔴 **provably insufficient** — cleanup bucket 4 requires `EXISTS(job_queue_items …)` (`repository.py:2736-2741`); report tasks have **no JobItem** → never match; zombie reaper ignores TERMINATED; nuclear endpoint is operator-triggered | 0 (delivers 0) |

**Decision: 2a.** Delivery has no consumer (parent is dead); report content survives in `message_queue.content` + the dead-letter log line carries `report_message_id` for retrieval. **2b deferred** to the B3/B4 tree-coherence follow-up (it overlaps the fire-with-terminated-outcome lane and would double-report once P2 lands). **2c rejected on verified evidence.** Revive interplay: after dead-letter + injection-row disposition, a later revive delivers nothing historical — acceptable: TERMINATED is a deliberate operator act; holding obligations open "in case" is precisely the livelock being fixed.

### 2.5 Composition with the obligation invariant (the 5 lanes)

Lanes enumerated end-to-end: **Lane 1** `deferred` (`:522`), **Lane 2** `no_row_backstop` (`:856`), **Lane 3** `pending_age` retry=0 (`:1019`), **Lane 4** `recovery_retry` (`:1019`), **Lane 5** `orphan` (`:539` — DEFERRED rows for terminal parents; revive-first via `_try_revive_terminal_parent` `:748-792`).

**The fight (verified):** `find_pending_past_age` (`report_injection/repository.py:762-823`) has **no parent-status filter** — a dead-parent PENDING injection row is picked up by Lanes 3/4 past the 10-min bound; the reconcile sub-shape no-ops against the child's COMPLETED short-circuit (`child_reports.py:1760+`) → row stays PENDING → **re-swept forever, `recovered += 1` each cycle (metric lies)**.

**Therefore the combined shape is (C3):**
1. **Enqueue seam** — verified at `child_reports.py:2638-2663` (not the plan's "~:2510 region"): parent loaded `:2638`, PAUSED-only skip `:2639-2646`, Task INSERT `:2656-2663`. Extend the skip to `parent is None or parent.status == TERMINATED` → skip **BOTH** the Task INSERT and the `report_injections` INSERT (`:2731+`); message row (`:2592-2603`) retained but marked `MessageStatus.FAILED`; one log line.
2. **Drift sweep** — new pattern (e) in `reconcile_drift_states` (`job_recovery_service.py:488`, 60s loop): atomic UPDATE (target-status EXISTS folded into WHERE) + per-row `reconcile_turn_mirror(work_id)` + companion disposition (injection-row DELETE — no injection terminal state exists; INJECTED/TASK_DELIVERED would falsely signal delivery). Never touches JobItems; best-effort per-row try/except; never blocks startup.
3. Once the injection row is gone, Lanes 3/4 stop matching; Lane 5 reads DEFERRED only; Lane 2 filters `parent_not_terminal=True` → no re-creation fight. `dependency_bus` authority untouched. `uq_report_injections_oblig_triple` never violated (the row never enters INJECTED/TASK_DELIVERED — honestly reflecting non-delivery).

### 2.6 AF2 plan corrections (phase1-plan.md Task 8 unless noted)

| # | Severity | Correction |
|---|---|---|
| C1 | 🔴 | Replace `fail_task → AbortTurn(reason='failed')` with DeadLetterTurn / cold-path wrapper (§2.3) |
| C2 | 🟡 | Pin the seam: `child_reports.py:2638-2663` (not :2510 region) |
| C3 | 🔴 | Add companion-artifact disposition (skip injection INSERT at enqueue; sweep dispositions stranded companions) — else perpetual Lane-3/4 re-sweep with inflated `recovered` |
| C4 | 🟡 | Fold target-status EXISTS into the dead-letter UPDATE's WHERE (revive race) |
| C5 | 🟡 | Apply the same dead-parent check to reconcile sub-shapes a/b (`manager.py:6829-6945`) |
| C6 | 🟢 | Keep `reconcile_drift_states` placement; name predicate "pattern (e) — task-only-keyed, mirroring pattern (a)"; assert dependency_bus/JobItem completion untouched in acceptance |

**Disposition vocabulary:** `'failed'` (canonical, `_STATUS_CANONICAL_MAP` `work_status.py:66-122`) confirmed as the deliberate choice — it signals *delivery did not succeed*, the operator-visible fact; `'cancelled'` is the alternative canonical value matching the abort semantics. **Recommendation: `'failed'`** per plan intent; leader may flip to `'cancelled'` without architectural consequence (both canonical).

---

## 3. Q1–Q6 — Obligation Semantics (B2+B3)

### 3.1 Verdict table

| # | Verdict | Key reason |
|---|---|---|
| Q1 | **CONFIRM + 🔴 caveat** | `_recover_fired_unsent` (`dependency_bus.py:1554-1606`) sees empty after `enqueued_at` stamp — single invariant holds. **But no lane backstops a missed deliver-before-compact row** (Lane 2's NOT EXISTS *excludes* FIRED rows, `repository.py:715-725`) → the deliver loop must be atomic with the DELETE pass (crash mid-iteration must abort the DELETE) |
| Q2 | **MODIFY** | `Outcome.status` is **NOT** serialized in `follow_up_payload` (`dependency_bus.py:162-185` carries `{target_instance_id, message, source, metadata}` only). Encode via `FollowUp.metadata["child_outcome"] = "terminated"` at the `fire_for_terminated_target` construction site |
| Q3 | **CONFIRM + caveat** | Gate clears via `count_pending_for_target_sync == 0` (`child_reports.py:1823`); parent's LLM receives FollowUp.message via `_process_child_completion_and_notify_parent` (`:1490`). Caveats: actual PAUSED-guards are at `:1775`/`:1845` (not `:898`/`:1244`); metadata must be surfaced through the graph-node drain for LLM visibility |
| Q4 | **CONFIRM** | Per-target; symmetric to `cancel_for_target` (`:1025-1098`); cascade enumeration is P1's |
| Q5 | **CONFIRM** | Revival (`instance_messaging.py:1518-1540`) reactivates status + queues a fresh MessageQueue/Task; touches **neither** `dependency_watchers` nor `report_injection` — FIRED obligation stays terminal, no double-delivery |
| Q6 | **CONFIRM (optional)** | Include the guarded SELECT (`instance_lifecycle.py:3789-3811` is blind today); companion reconciler only if test 2.6 reveals stranded-PAUSED cases |

### 3.2 Key verifications

- **JAFP preserved:** `manager.enqueue_message` creates **no JobItem** (`instance_messaging.py:1619`) — B2/B3 delivery paths do not violate pause-writes-nothing-to-JobItems.
- **outcome.status audit:** every equality branch enumerated (`dependency_bus.py:635, :823` — both `== "error"`); `'terminated'` is already documented in the `Outcome` dataclass (`:117`). **Safe.**
- **Call-site correction for task 2.3:** `cancel_for_target` is called at `:1781` and `:1816` inline in terminate (plus `:74` for pause via `_cancel_bus_watchers_for`). **Patch `:1816`** (the post-commit seam); `:1781` is a pre-existing duplicate → separate cleanup PR. The plan's `:1775` ref is off.

> **[Rev 2.1 erratum — reviewer council 2bb126df, 2026-08-24T19:31:26Z]** The above premise (`cancel_for_target` called at `:1781`, `:1816`, AND `:74` for pause) is **stale** — the `:74` is the helper `_cancel_bus_watchers_for` *definition* but pause-side invocation was **REMOVED pre-Phase-2**. Evidence: the explicit pause-side-removal comment at `instance_lifecycle.py:2240-2266` (Phase 2 / Decision 2, 2026-06-25) — "DEPENDENCY-BUS WATCHERS ARE PRESERVED ON PAUSE" — confirms pause no longer calls the helper; the helper is retained ONLY for terminate. The currently-reachable terminate-side `cancel_for_target` call sites are exactly **two**: `:1781` (direct call, pre-existing duplicate) and `:1816` (via `_cancel_bus_watchers_for`, the post-commit seam — Rev 2's correct patch site). The `op=='pause'` branch in phase2-plan.md Rev 2's Task 2.3 design is **DEAD CODE**. See phase2-plan.md §D (Rev 2.1 Changelog) for the corrected task text.
- **Race → guard → test mapping:** deliver×restart → `_recover_fired_unsent` filter + test 2.4 cycle-2; fire×revive → revival never touches watcher rows + tests 2.7/2.10; fire×emit_terminal and fire×cancel → guarded `transition_state` WHERE state=PENDING (`repository.py:519`, rowcount=0 for loser) + test 2.2 race cases.

### 3.3 🔴 Task 2.4 correction — two-pass cutoff

The compact's 60s grace (`instance_lifecycle.py:3662`) means deliver-before-compact as written only catches `fired_at <= now-60s`. **A child that completed 30s before resume is silently stranded.** Fix: **pass 1** = deliver loop with `fired_at <= now` (all buffered, no grace); **pass 2** = the original 60s-grace DELETE. Plus the Q1 atomicity requirement: wrap the deliver loop so a failure aborts the DELETE pass (no lane backstop exists).

### 3.4 Q plan corrections (phase2-plan.md)

| Location | Fix |
|---|---|
| §Architect Flags Q2 (`:192-196`) | Replace "already serialized in follow_up_payload" with the `metadata.child_outcome` design (additive only) |
| Task 2.1 (`:134`) | Record the verified lane finding: Lane 2 NOT EXISTS (`repository.py:715-725`) **excludes** FIRED rows — B2 has NO lane fallback; deliver-before-compact must succeed atomically |
| Task 2.4 (`:137`) | Two-pass cutoff (§3.3) + atomic-with-DELETE requirement |
| Task 2.3 (`:136`) | Line refs `:1781`/`:1816` (patch `:1816`); `:74` is pause-only |
| §Flags Q3 (`:198`) | Site refs → `child_reports.py:1775`/`:1845`; add metadata-surfacing requirement for parent LLM visibility |
| Task 2.7 (`:140`) | Add: verify `claim_for_injection` returns `[]` on second delivery (guarded WHERE state=PENDING, `repository.py:886`) — the natural idempotency |
| Risks #2 (`:168`) | Strengthen: no lane backstop; wrap loop in single transaction |
| Risks #9 (`:175`) | Add: `Outcome` dataclass already documents `'terminated'` — no type contract change |

---

## 4. AF-B5 — `/stop` Semantics

### 4.1 Decision: SUBTREE via boolean parameter (option i)

Weighted 3.90 vs 3.75 (keep-whole-tree+deprecate) vs 3.25 (helper) vs ≤2.70 (node-only/soft-stop/leave-broken). The margin over keep-whole-tree is exactly the Risk axis: the current endpoint pauses the wrong target **and names the wrong target in its 200 response** — incoherent with fixing B1–B4 in the same batch. The margin over the helper is Complexity+Cost inside `instance_lifecycle.py`, the R1-contended file. **Node-only rejected outright:** pausing X while descendants run is the precise parent/child divergence state B2's machinery exists to survive — deliberate reintroduction is defect-generation. The helper is the right **post-merge refactor** if a second flag ever appears.

**New finding (plan missed):** `pause_instance_cascade` has **5 internal callers** besides the router — `instance_messaging.py:1119, :3748`, `watchover_service.py:1004, :1470`, manager facade `manager.py:7690`. The default `True` is load-bearing for watchover and messaging, not just `/pause` → pin it with a **service-level unit case 7** (`pause_instance_cascade(mid)` with no kwarg returns whole tree) and optional case 8 (kill-switch mode propagation).

### 4.2 🔴 Re-import risk — worse than plan-overview §4.4 states

§4.4 flagged only the else-branch. Verified raw `get_tree_ids` in the phase3 sketch at **lines 157 (True branch), 160 (else branch), and 193 (task 3.1 acceptance)**. Implemented verbatim, **`/pause`, messaging, and watchover would also bypass P1's kill-switch wrapper** — a second, sneakier side door.

**Exact corrections (phase3-plan.md):**
1. Line 157 → `tree_ids = repo.get_cascade_tree_ids(root_id)`
2. Line 160 → `tree_ids = repo.get_cascade_tree_ids(instance_id)`
3. Line 193 (task 3.1 acceptance) → same substitution + "True-branch inherits P1's swap at `:2056` — P3 rebases on P1"
4. §Verified Mechanics line 46 → annotate `:2056` becomes `get_cascade_tree_ids` after P1
5. §Sequencing lines 299-306 → replace both `get_tree_ids(...)` mentions with the wrapper
6. **plan-overview.md:48 (§4.4)** → extend the instruction to cover BOTH branches, not just the else

---

## 5. AF-B6 — Diagnosis Exit Condition

### 5.1 Static analysis — the divergence cannot be at the DB-read seam

Detail path: `instances.py:488-505` → `manager.py:9015` (pass-through) → `instance_lifecycle.py:2966-2991` → `repository.get:222-226` (`session.get` + `_enrich_instance:105-107`; no subclass overrides — factory `factory.py:380` returns the base class). List path: `instances.py:387` → `lifecycle:2906-2964` → `repository.list:444-488` (`select(Instance)`, no status/deleted_at filter) + identical post-processing. **A row visible to `select(Instance)` on the same engine is structurally visible to `session.get(Instance, pk)`.** The all-5-uniform, state-independent pattern favors request-level hypotheses:

| # | Hypothesis | Likelihood |
|---|---|---|
| H1 | Harness artifact — wrong path/port/base-URL (FastAPI routing-404 `{"detail":"Not Found"}`) or stale daemon | **High** |
| H2 | Stale comparison — list captured pre-resume vs live detail (F-DR1-2 split-brain class precedent) | Medium-High |
| H3 | Two processes / port confusion | Medium-Low |
| H4 | Row invisibility (id drift, delete+recreate) — `_resume_cascade_db_sync:3783-3798` touches status/paused_at/updated_at only | Low-Medium |
| H5 | KeyError misattribution | Low |

### 5.2 Probe-first timebox (option A, 4.00 vs 3.70 ticket-now)

Ordered probes, each ≤30 min (~2h of the 2–4h cap):
1. **(≤5 min, decisive classifier)** Reproduce one 404; inspect the **body**: plain `{"detail":"Not Found"}` → routing/harness artifact (H1) → probe 2; `INSTANCE_NOT_FOUND` → row-level path → probe 4.
2. **(≤15 min)** One-script back-to-back sweep: same client, same base URL, list+detail+messages for all 5 ids in one loop → falsifies H2/H3.
3. **(≤20 min)** Verify script URLs/ports vs live daemon; `ps` for a second daemon; grep repro script for the detail URL.
4. **(≤30 min, only if INSTANCE_NOT_FOUND)** Direct DB check on the daemon's DB: `SELECT instance_id,status,parent_id FROM instances WHERE instance_id IN (<5 ids>)`; byte-compare ids.
5. **(≤30 min)** SQLAlchemy echo on one detail request + engine log `Creating PostgreSQL engine:` line (split-brain check).

**Exit condition:** 404-body class identified **AND** (seam classified small/large **OR** harness artifact confirmed with corrected-repro green). Each eliminated hypothesis gets one evidence line in the bundle.

### 5.3 Confirmations

- **"No small seam found → ticket only" at the 4h cap: ACCEPTABLE.** B6 is 🟠 with a live workaround (list+messages serve the data); the bounded elimination has durable value.
- **Fallback ticket minimum content:** exact curl repro set including 404-body capture; DB snapshot queries (5-id SELECT + hierarchy rows + engine log line); eliminated-hypotheses table (H1–H5) with evidence; effort class per surviving hypothesis; corrected repro script + "possibly NOT-A-DEFECT" recommendation if H1 confirms.

---

## 6. AF-P3-7 — `preserve_completed_at` COALESCE Default

### 6.1 ✅ Verified re-arm finding (resolves the factual conflict — the KB was right, phase3-plan is wrong)

- **`rearm_with_lock` EXISTS:** definition `job_queue/repository.py:1974-2167`; call site `job_feedback_observer.py:1470-1474` (orphan-race post-commit re-check); referenced `job_recovery_service.py:211-216`. phase3-plan.md:85's "grep returned ZERO matches … F9 DEFERRED" is **factually false — F9 is closed** via this path.
- **Semantics:** `rearm_with_lock`'s UPDATE (`:2126-2144`) sets `admission_state='active'` + `instance_id` — it does **NOT clear `completed_at`**; `atomic_retry` (`:1278-1298`) clears `failed_at` but not `completed_at`. Every stamp site is guarded by `admission_state='active'` (complete_job `:2271-2277`, fail_job `:2294-2301`, terminate_job `:2500-2506`, **plus a 4th site the plan missed**: observer fail-safe `job_feedback_observer.py:1885-1891`). A DONE row cannot be re-stamped without a real re-entry first. The observed B7(b) re-stamps are most plausibly **F9 re-arm + C1 `_process_resume_finalize` composition — likely working as designed, not corruption.**

### 6.2 Decision

| Approach | Cplx | Scal | Maint | Risk(inv) | Cost(inv) | Total |
|---|---|---|---|---|---|---|
| **(A) Default `False`, NO call-site wiring; re-scope B7(b) as verify+pin** ✅ | 4 | 3 | 4 | 5 | 4 | **4.00** |
| (D) Drop tasks 3.7–3.9 entirely | 5 | 3 | 3 | 4 | 5 | 3.90 |
| (B) Default `False` + wire `True` at 3 sites (plan as written) | 4 | 3 | 3 | 2 🔴 | 4 | 3.15 |
| (C) Default `True` (plan-overview §5) | 4 | 3 | 2 | 1 🔴 | 3 | 2.55 |

**`preserve_completed_at: bool = False` is MANDATORY given the verified re-arm.** For a re-armed-then-re-completed job, `completed_at` should mean the **LAST settle** (the re-arm's own definition declares the first completion premature); COALESCE/preserve freezes the stale first timestamp — and on the retry flow it freezes the **failure-time** stamp on a row that later shows `completed`. **Go further than the phase3 plan: task 3.8 (wiring `True` into all three call sites) must be DELETED** — it produces the same false-timestamp corruption, just opt-in-ly. Keep the flag defined (reserved for a future deliberate first-touch caller), wire nothing.

**Task 3.9 inversion:** pin that re-arm→re-complete stamps `completed_at=T2` (last-settle semantics). Note the plan's original case-1 spec is mechanically wrong: a second `complete_job` on a DONE row does not "no-op" — `atomic_transition` **raises `InvalidTransitionError`** after rowcount=0 (`:1245-1259`), so the test as written cannot pass. Add the 30-min repro-DB verification (check the twice-re-stamped jobs' admission history for re-arm evidence) before concluding "not a defect."

### 6.3 AF-P3-7 plan corrections

1. `plan-overview.md:62` (§5 AF-P3-7 row): "Default True (preserve-by-default, recommended)" → "Default False (opt-in). VERIFIED: `rearm_with_lock` exists (`job_queue/repository.py:1974`, observer call `:1470`); retry flows re-enter without clearing `completed_at`; preserve-by-default freezes stale/failure timestamps on legitimately re-run jobs."
2. `phase3-plan.md:85` (§B7(b)): replace the false ZERO-matches/F9-DEFERRED claim with the verified citations.
3. `phase3-plan.md:199` (task 3.7): fix the justification (default False is required by the re-arm finding, not merely caller-compatible).
4. `phase3-plan.md:200` (task 3.8): **delete**.
5. `phase3-plan.md:236-240` (task 3.9): replace with the inverted last-settle pinning test; note raise-vs-noop mechanics.
6. `phase3-plan.md:335-336` (risk rows 4/5): rewrite — the premise "NO re-arm path exists today" is false; the real risk is the opposite direction.
7. Add the missed 4th stamp site (`job_feedback_observer.py:1885-1891`) to the §B7(b) site table.

---

## 7. Consolidated Correction Register (severity-ordered)

**🔴 Correctness-gating — the plan ships a dead or corrupting fix without these:**
1. **P1 Task 3 → enumerate-first terminate restructure** (AF1-C1; §1.5). Guard `:1362-1370` short-circuits before enumeration; COMPLETED children get re-stamped `"aborted"`.
2. **P1 Task 8(a) → replace `fail_task` dead-letter mechanism** (AF2-C1; §2.3). It no-ops on PENDING rows.
3. **P1 Task 8 → add companion-artifact disposition** (AF2-C3; §2.5). Task-only dead-letter leaves Lanes 3/4 re-sweeping forever with an inflated `recovered` metric.
4. **P2 Task 2.4 → two-pass cutoff + atomic-with-DELETE** (Q-caveats; §3.3). 60s grace strands rows fired <60s before resume; no lane backstop exists.
5. **P3 sketch lines 157/160/193 + overview §4.4 → BOTH branches use `get_cascade_tree_ids()`** (AF-B5; §4.2). As flagged, only the else-branch was covered.
6. **P3 task 3.8 → DELETE; default `False` mandatory** (AF-P3-7; §6.2). Wiring `True` writes false `completed_at` on re-armed/retried jobs.

**🟡 Significant (fix before/during implementation):** P1 mock migration as named task (~8 suites); kill-switch hardening + FT-004; verified enqueue seam `child_reports.py:2638-2663`; secondary seam guard `manager.py:6829-6945`; sweep atomicity (EXISTS-in-WHERE); task 2.3 call-site refs `:1781`/`:1816`; Q2 metadata encoding design; Q3 guard-site refs `:1775`/`:1845` + LLM visibility; hard-delete snapshot product sign-off; service-level default pin (test case 7); B6 probe checklist adoption; overview §5 AF-P3-7 row rewrite; phase3:85 factual correction.

**🟢 Improvements:** Q5 natural-idempotency verification note; blueprint amendment (post-P1); `pinned_subtree_terminal_count` metric; CTE rationale fix; traversal-cap docstring; classify-before-emit invariant pin; disposition-vocabulary note.

---

## 8. Cross-Report Convergence (independent corroboration)

- **Pause-gate root cause** confirmed independently by the council (§7.3 of its record) and the AF2 worker (§2.1) from different entry points.
- **Lane-level fights** found independently by two workers from opposite directions: Q-worker found Lane 2 *excludes* the B2 shape (no backstop for deliver-before-compact); AF2 worker found Lanes 3/4 *match* the dead-parent shape with no parent filter (perpetual re-sweep). Both must be addressed — P2 §3.3 and P1 §2.5 respectively.
- **Enumeration-source discipline** appears in every cluster: AF1's wrapper mandate, AF-B5's both-branch correction, and P2's drift SELECT — one rule covers all: **cascades enumerate permanent lineage via the wrapper; lane/gate predicates stay narrowly typed.**

## 9. Risks

- 🔴 If P3 merges before P1 without the wrapper corrections, `/stop` subtree enumeration misses churned descendants **and** `/pause`/watchover/messaging silently bypass the kill-switch.
- 🔴 Terminate Task 3 as written re-creates B4 one level down and violates canonical `terminal_reason` (re-stamp over true reason).
- 🔴 Task-only dead-letter (without companion disposition) converts the `[GUARD]` livelock into a silent Lane-3/4 metric lie.
- 🟡 B7(b) reclassification rests on an inferred mechanism (re-arm + resume-finalize composition) — gate the "not a defect" conclusion on the 30-min repro-DB check; **if the re-stamped jobs never transited `active`, an unguarded raw UPDATE exists somewhere this audit missed, and option B wiring becomes correct.**
- 🟡 Lane 5 (revive-first for DEFERRED) vs the new sweep (dead-letter PENDING) answer the same question differently — follow-up decision: align or document the asymmetry as intentional (pause-drop vs terminate-drop provenance).
- 🟡 Mode flag on the most safety-critical cascade method (`cascade_to_root`) — mitigated by cases 6–8 + param comment.
- 🟢 Kill-switch fallback utility decays with churn — FT-004 removal criterion documented.

## 10. Decisions Pending (leader/product, before implementation)

1. **Product sign-off: hard-delete snapshot expansion** — root hard-delete now sweeps completed-descendant checkpoints (previously survived). Accept or carve out.
2. **B7(b) disposition** — accept "likely working as designed" pending the repro-DB verification, or keep a guard ticket if the verification surprises.
3. **Dead-letter vocabulary** — `'failed'` (recommended) vs `'cancelled'`; both canonical; pick once.
4. **Lane-5-vs-sweep coherence** — align revive-first/dead-letter policies or document asymmetry (follow-up ticket).
5. **AF1 end-state confirmation** — staged deprecation (default) vs forever-dual (only if O1 verification fails; flip condition documented, zero mechanical cost).

## 11. Open Questions

- Does `d14cbde5`'s companion injection row survive in the repro DB? (Sweep covers either way; confirms C3 empirically.)
- B6 H1 requires the live repro environment (`/tmp/pause-repro-20260824/state.json`) — verify it exists before budgeting the cap.
- Whether any of the 5 internal `pause_instance_cascade` callers would ever want subtree semantics (watchover is documented whole-tree; not re-derived).
- Whether a hidden external `/stop` caller exists (research grep found 0 in `frontend/` and `agents/`; manual API consumers unverifiable).

## 12. Provenance

| Unit | Skill | Assignment |
|---|---|---|
| Council (2 councilors, agentic + coding) | structural-design | AF1: approaches A/B/C, terminal-skip rule, blast radius, kill-switch, end-state |
| Worker `bd740c83` | resilience-design | AF2: hook × disposition axes, TOCTOU, lane composition, revive interplay |
| Worker `cb277edc` | data-flow-design | Q1–Q6 verdicts, 5-lane enumeration, outcome audit, B2/B3 flow traces |
| Worker `70f6581e` | trade-off-analysis | AF-B5 options, AF-B6 hypotheses+probes, AF-P3-7 re-arm verification |

Aggregation, five-axis syntheses, and all rulings by the Architect. All hard constraints verified respected: dependency_bus sole completion authority; no new JobItem-creation sites (`enqueue_message` JAFP-verified); named transitions + `reconcile_turn_mirror` authority (DeadLetterTurn/cold-path per AF2-C1); canonical `terminal_reason` only; revive semantics survive (Q5 verified, R2 strictly improved); no schema migrations in-batch (Approach C rejected partly on this); merge order P1→P2→P3 with the §4.4 wrapper mandate extended to both branches.
