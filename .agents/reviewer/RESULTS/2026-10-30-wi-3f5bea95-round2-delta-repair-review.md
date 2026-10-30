# Round 2 — Delta Re-Review (plan) + R1 Council Review (harness repair) — wi-3f5bea95

- **Target:** plan set @ `c3d87af26`, worktree `/home/nea/ensemble-src-wt-eventloop-sync-db-hotpaths`, branch `fix/eventloop-sync-db-hotpaths`
- **Date:** 2026-10-29/30 · **Round-1 artifact:** `.agents/reviewer/RESULTS/2026-10-29-wi-3f5bea95-plan-gate-review.md`
- **Units:** `29a8b290` (C-1..C-4) · `0d004e53` (C-5..C-8) · `62112576` (rulings/HEAD-pin/G11/phantom sweep) · council `8a9465ba` (2 councilors, `agentic`+`coding`, unanimous)
- **VERDICTS: PLAN = 🔴 REJECT (narrow — one new critical) · REPAIR = 🔴 REJECT (3 blocking, fails-closed)**

---

## PART A — PLAN (delta)

### Round-1 critical closure scoreboard

| # | Item | Status | Verdict carrier |
|---|---|---|---|
| C-1 | pre-conversion limit slice | **PARTIAL** | Job-branch `root_only` child filter (`work_resolver.py:1469`) placement vs `source_rows` accumulation unspecified → post-slice placement silently under-delivers for the production caller (`kind="job"`, `root_only=True`); no pin covers delivered-population. Tie-break totality CLOSED (stability dependency stated). Minor: Job rows carry ISO strings — `_parse_iso_datetime` step never stated. |
| C-2 | admission gate | **PARTIAL** | Leak/deadlock CLOSED (done-callback release). Over-release NOT mechanically closed: task 1.1 prescribes `run_in_executor` whose returned future is the **asyncio wrapper** — its done-callback fires on cancellation while the thread still runs. Pin (x) as worded passes under the wrong implementation; the claimed `finally`-release mutation cannot redden it. |
| C-3 | four exact-count packs | **🔴 NOT CLOSED** | **Every stated new count value is wrong or inapplicable.** All four packs are `kind="job"` fixtures — the Task branch never runs (`work_resolver.py:1363`), so `:295` stays 2, `:324` stays 1, `:410` stays 0, `:750` stays ≤5. Implementing 3.10 literally (a) writes `==3` where the count stays 2 → deterministic RED, (b) loosens `≤5`→`≤6` — a silent weakening the rubric's own discipline forbids. `test_mission_resolver.py:770/:998` belong to the separate `MissionResolver._batch_instances` (`mission_resolver.py:787` — different class, untouched by this lane). `test_job_queue_proxy_phase1.py:703/:734` are **docstring lines** (real asserts: `:788` `<=2`, `:863` `<=2` — unchanged) — the round-1 phantom-anchor defect class reintroduced. The packs that CAN move (task-bearing `kind="report"`/union) are named nowhere. |
| C-4 | G4 predicate (e) | **CLOSED** | (e) is a lettered conjunct in the predicate verbatim (`plan-overview.md:254`), INCONCLUSIVE branch explicit, D-62 admissibility (real load, daemon's own captures, synthesized stacks never satisfy G1/G4) closes the zero-load tautology. Textbook. |
| C-5 | G6 collection fences | **CLOSED** | All four fences present in three homes (task/G6 row/D-46); fallback PRE-AGREED with named recipe (v0.27.11 compensating-evidence), decline ⇒ HARD FAIL. 🟢 add a dated "fallback ratified" slot to 5.10. |
| C-6 | phantom anchor | **CLOSED** | `:951` trigger, `:152-153` else-branch, `:383-405` mission-ref degrade all verified in-tree; observable "0 that were 1 at BASE" attached to the real trigger per-job. 🟢 D-47's own bullet still says `if work_record is None:` at `:153` (no such if there); `technical-analysis.md:121/:211` + `decisions.md:79` retain the phantom as unamended history. |
| C-7 | boot smoke | **PARTIAL** | All elements present incl. per-variable scrub (glob idiom named and banned) and fail-closed fallback. Two gaps: "bridged read in flight" NOT operationalised (no deterministic recipe, no was-open-when-SIGTERM assertion — prose, not predicate); `DB_OFFLOAD_DRAIN_BOUND_S` has **no numeric default** and "+ margin" undefined (sibling constant got its number; an 80s pick already violates the ⊂90s arithmetic). |
| C-8 | commit units / fences | **CLOSED (mechanically)** | tree-sha `88009e7bf…` re-computed == plan pin; porcelain over harness path EMPTY; three units match branch reality (`c3d87af26` = tester identity, 9 files, all harness-path); `git add -A`/`commit -a` banned in three places; G12 = attribution. 🟢 5.8's "checked in the commit history" is not observable from history — reword to per-commit path-set ⊆ class; #17 needs a file-list boundary (BASELINE.md already committed). |

### Rulings / state blocks

- **RULING 1 (G1 three-part): PARTIAL.** All five elements present in the gate row (`plan-overview.md:251`) — but the MECHANISED predicate `captures >= 1 AND loop-section-hits == 0 ⇒ PASS` omits the attribution conjunct, so an unattributable capture with hits==0 PASSes while clause (b) calls the identical case a FAIL. This is a false-green path on a **new** on-loop wedge site — the exact class the ruling exists to close. One-line fix.
- **RULING 2 (G2): CLOSED.** Provenance in-band, genuinely one-sided (below-band PASS annotated), BFS measured-not-gated with an anti-fabrication reason.
- **RULING 3 (commit units): CLOSED.** Three units, explicit paths, bans, tree-sha, G12 attribution — consistent across overview/phase5/DO-NOT #15-#17; no contradiction found.
- **HEAD-PIN: PARTIAL.** §Harness State is complete (explicit STALE table, repair facts, council PENDING under R1, BASE-still-red kept) — but the retraction did not propagate: `phase5-plan.md:11` still asserts "the harness cannot express a HEAD run today" under a **(binding)** label, contradicting 5.1 in the same file; `decisions.md:153,445` + D-37 body retain stale present-tense claims; `grep -c c3d87af26 decisions.md` = 0.
- **G11: NOT CLOSED (one line).** Operative surfaces all corrected (G11 row, rollback preview, 5.7(c), D-57) and live facts re-verified (`current → releases/v0.27.12`, `/livez` 0.27.12, v0.27.13 staged-unpromoted) — but `decisions.md:159` (D-13, the rollback-safety adjudication itself) still says "vs live v0.27.13". The exact line W-6 named, inside the adjudication surface.
- **New-phantom sweep: 0 new phantoms** in the anchor layer (23 probes: 20 exact / 2 near / 1 inherited) — **but** C-3 introduced two "exact-but-vacuous" docstring anchors (`test_job_queue_proxy_phase1.py:703/:734`), which is the phantom class by effect if not by content (see C-3).

### PLAN verdict: 🔴 REJECT (narrow)

One new critical (C-3/F-4) that actively damages tests if implemented as written, plus mandatory folds: C-2 release-future spec (F-3), C-1 child-filter placement (F-1), RULING 1 predicate conjunct, G11 D-13 line, HEAD-PIN retraction propagation, C-7 two one-liners. All are plan-text fixes measured in lines, not redesigns. Everything else round 1 demanded is genuinely closed — including the mechanically verified commit-unit story.

---

## PART B — REPAIR (council, R1 gate)

### 🔴 REJECT — unanimous (2/2), both models, independent evidence paths

**Blocking 1 — G9 HEAD-mode unit bug (both councilors).** `main.py:186-191,214,592-596` feeds **sample-summed** `statements_by_shape` into **per-request** rules (`modes.py:340-362`). Measured on the committed artifact `raw/BASELINE.full.head.json`: missions `{20,10}` over 10 samples vs pinned `(2,1)`; flat on-loop `60` vs `max_on_loop=2`. The missions shape is by-design unchanged ⇒ **fails deterministically forever; a correct fix cannot pass this gate**. BASE mode compares per-request against per-request (`main.py:601-621`) — hence BASE 0-fail, HEAD-on-same-tree 15-fail. The truth table hid it because cell 3 routes through `simulate_report` (`modes.py:706-713`), which normalizes units — a path the real gate never takes.

**Blocking 2 — G4 reference provenance unasserted (councilor 1).** `--reference` validated for existence only (`run_all.sh:96-99`; `main.py:844-848` records but never asserts). Point it at `BASELINE.full.head.simulated.json` (lag clamped to tick floor, `modes.py:670-688`) and every G4 condition passes on a broken tree. ~2-line fix: require `reference.mode == "base"` and `simulation.applied` absent/false.

**Blocking 3 — truth-table artifact overclaims (councilor 2).** §8 is an empty `<!--TRUTH_TABLE-->` placeholder; cell 4 never run; yet the doc asserts "REPAIRED — separates BROKEN from FIXED in both directions". Phase 5.1 acceptance needs committed BASE-red evidence; what exists is workspace-local plus a claim the doc cannot back. Interlocks with Blocking 1: the table cannot be honestly completed until units are consistent.

**Passed clean:** commit purity (9 files all harness-path, tester identity, tree-sha matches plan pin, porcelain empty, zero production diff) · loop-thread scoping genuinely fixed (`modes.py:95-134`, `*_any_thread` demoted to diagnostic) · BASE-only fences mode-switched · G2 one-sided · G4 legs paired · synthetic leg drives REAL concurrent HTTP (141 reqs/0 err BASE, 140 HEAD), literal stacks confined to labelled `discriminator_selftest` · **BASE-still-red proof REAL and reproducible** (BASE PASS 49 fences 0 fail, family 3/8; HEAD-on-base FAIL 15, family 4/8; red = `repository.py`+`sqlalchemy` in the loop-thread section) · no false-green weakening (49 vs 33 fences, superset).

**Ride-alongs (non-blocking):** W-1 G4 noise floor (measured 0.9% false-red same-tree; P(0/8)≈2.3% on the BASE positive control) · W-3 synthetic leg is activity-fenced only, not the `STALL_HEAD==0 ∧ STALL_BASE>0` gate; captures not attributed to the leg window · W-4 simulated runs emit `verdict: PASS` + exit 0 (`main.py:1073-1083`) — README's "refuses to print as gate evidence" is not enforced by the CLI · W-5 capture non-vacuity aggregate not per-capture (one HEAD capture had `loop_sections=0`) · INCONCLUSIVE is not a code state — reconcile wording.

**Decisive nuance:** the defect fails CLOSED, not open — a good tree cannot go green, a bad tree is still convicted (4/8 captures on the unfixed tree; `g9_on_loop_head` corroborates). The repair swapped round-1's "cannot run HEAD mode" for its mirror: "runs and convicts correctly, but a fixed tree cannot clear". Detection capability for the original `instances.py:476` shape is intact.

---

## Cross-axis interaction (both verdicts must fix consistently)

The same defect is visible from both sides: the harness compares **summed vs per-request** (repair Blocking 1) while the plan's G9 row freezes "round-trip totals" **without declaring the unit class** (plan F-5). Fix both in one motion: normalize units at the gate boundary (divide by samples, or publish a per-request variant) AND declare the unit class next to every frozen G9 total in the plan.

## Operational note (project-level, not a finding)

All 8 worker dispatch failures this round were the **known proxy-side `coding`-alias defect** — `agents/worker/meta.json` pins `coding`, which the council independently observed dying mid-run while `agentic` completed. Re-dispatching workers with `model="agentic"` resolved it completely (3/3 clean runs, zero throttles). This is a third data point for the morning-list item.

## Required before ANY HEAD functional run

1. REPAIR Blocking 1+2+3 (unit normalization + mutation-meaningful unit selftest leg; reference-provenance asserts; complete + honest truth table, commit the BASE-red artifacts).
2. PLAN F-4 (rewrite 3.10 values; re-anchor pack 4 to `:788`/`:863`; drop the MissionResolver pack; add the task-bearing sweep; acknowledge the union-batch alternative).
3. PLAN F-3 (specify `_EXECUTOR.submit` + `asyncio.wrap_future` + callback on the **concurrent** future; strengthen pin (x) to assert max concurrent thread executions ≤ W under a cancellation storm).
4. PLAN F-1 (child filter stays pre-accumulation; delivered-count pin), RULING-1 conjunct, G11 D-13 line, HEAD-PIN retraction stamps, C-7 in-flight-read recipe + numeric drain bound.
5. Re-run this delta gate on the folded set (single worker; the anchor layer is settled).
