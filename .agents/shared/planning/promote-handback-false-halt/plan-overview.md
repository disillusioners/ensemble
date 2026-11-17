# Plan Overview — promote-machinery defect fix: healthy-boot false-halt

**Commission:** "promote-machinery defect fix: healthy-boot false-halt" (Emperor directive 2026-11-17 "fix it"; aftermath of v0.31.0 promote run `r-20261117-171207-e2f9`)
**Lane:** branch `fix/promote-handback-false-halt` @ base `0181dadbf` (== origin/latest at lane open) · worktree `/home/nea/ensemble-src-wt-hbfix`
**Planned:** 2026-11-17 (UTC) by the planner dispatch tree (2 explorers, 4 workers; instance IDs in the delivery report)
**Status:** PLANNING COMPLETE — no implementation, no rehearsals executed, no commits to `scripts/upgrade/**`, no stage, **no promote** (the live promote ceremony stays the Emperor's).

---

## 1. Objective

A **healthy boot must never halt a promote again**. Every future ceremony walks this code (`scripts/upgrade/**`, blast radius declared ceremony-critical). Secondary objectives: forensic honesty (journal labels that match reality), the restart arm's identical defect fixed in the same lane, and board item `wi-d3579d7c` #1 (promote-kind flipped=false live closer, same dead-actor pattern) closed by removing its producer.

## 2. Root cause (verified, not assumed)

Full evidence: [`research-findings-lane.md`](research-findings-lane.md) (fixture timeline) and [`research-findings-code.md`](research-findings-code.md) (code sites). Summary:

1. **rc conflation** — `_run_bounded` remaps only `137|143→124` (`lib.sh:4856-4882`); three journal sites label ANY nonzero rc as `kind=budget … timeout` (`lib.sh:6332-6339` — worst, hard-codes `rc=124`; `:6356-6360` — the incident; `:6699-6701` — the restart-arm twin). The incident's rc was **3** (`is-active`'s normal "not active"/`activating`), not a timeout.
2. **The killer** — `_gate_serving_attribution` (`lib.sh:8004-8092`) Layer 1 requires every port-holder ∈ the **argv-anchored** `_supervision_owned_pids` set (`:6921-6950`); MCP children with **inherited listen fds** (mid-spawn) fall outside it, and the green-class fail-fast (`:6093-6133`, return 1 at `:6105`) halts on the FIRST refused green. Layer 2's unit arm (cgroup containment, `:8100-8137`) **would have accepted them** but runs only after Layer 1 refuses.
3. **Zero unit-arm retry** (`:6377` single start, `:6395-6397` single verify) vs launchd's ×2 bootstrap retry (`:6541-6557`).
4. **Advisory consumed by nothing on the halt path** — `supervision_boot=UNIT_MANAGED` is read only by `adopt-unit.sh:477-497`; the halt text's `verified=UNIT_MANAGED` is the frozen pre-stop classification (`promote.sh:698`).
5. **HB_ERR stash is launchd-only** (writers `lib.sh:5634-5635`, `:5720-5721`) ⇒ "hand-back error: none captured" is structural on Linux.

Recovery precedent: the daemon-side serving-green closer (`upgrade_journal_sweep.py:2325`, predicates P1–P9) committed at 17:39:04Z — third occurrence of this class; the fix removes the producer, the closer's contract is untouched.

## 3. Artifact map (all in this directory)

| File | Author | Content |
|---|---|---|
| `research-findings-code.md` | explorer | grep-verified code sites, drift table, test-harness inventory (45 KB) |
| `research-findings-lane.md` | explorer | fixture timeline, restart twins, release/floor/staging mechanics (31 KB) |
| `phase1-plan.md` | worker | Code-fix design: A/B/C + restart arm + coalescence; 28-row anchor register; tasks T1–T12; commits C0–C6; 29 design invariants I-A1…I-E3 |
| `phase2-plan.md` | worker | Test + rehearsal strategy; negative proof NP-1…NP-11 (+31 mutations); rehearsal tiers 0/1/2/3/3b; registration RG-1..RG-7 |
| `phase3-plan.md` | worker | Release mechanics: floor/stamp re-pin commit-graph (P3-D1), version bump, staging sequence, rollback_safe |
| `decisions.md` | worker | **The decision record** — 22 ratified decisions, one binding amendment, corrections register, consistency pass, escalation |
| `plan-overview.md` | planner | This file (synthesis only) |

**Read order for the coding commission:** decisions.md §2 (binding amendment) → phase1-plan.md → phase2-plan.md → phase3-plan.md.

## 4. Phase structure

| Phase | Name | Objective | Detail |
|---|---|---|---|
| 1 | Code fixes (bash-only, `scripts/upgrade/**`) | A: honest rc taxonomy at 3 sites (refinement D-P1-1: rc=3 → `_log` INFO, no journal row). B **(load-bearing)**: containment ladder as a second accept path — R1 owned → R2 unit cgroup (**AMENDED segment-aware**, decisions.md §2) → R3 ppid-ancestry → R4 launchd lineage → refuse; + 5 s pooled deferral `_GATE_ATTR_DEFER_S` (test-only seam) for unattributable-yet; advisory rescue rejected for the verdict. C: systemd-lane HB_ERR stash (`_handback_stash`) + T11 rollback reader. Restart arm: honest labels + journal rows for the 7 incident-relevant exit-78 classes (allowlist pinned). Zero daemon-side changes except the floor constant re-pin (Phase 3). | `phase1-plan.md` |
| 2 | Tests + rehearsal + NEGATIVE PROOF | Legs A0–A7, B1–B10, C-1…C-8, restart legs; **NP-1…NP-11** — a genuinely wrong release must still halt (wrong version / absent version / non-2xx / foreign holder / no listener each mapped to the check that catches it, each with a reddening counter-mutation). Blocking prerequisite P2-T1: new systemctl stub generation (no existing stub emits nonzero is-active rc). New inherited-fd fixture (real forking server) — the incident shape is unmodelled in the corpus. Rehearsal tiers SCRATCH/SANDBOX ONLY, DANGER LIST embedded per step; Tier-3 promote rehearsal routed to the tester gate (D-P2-1). | `phase2-plan.md` |
| 3 | Release mechanics | Floor commit-graph **P3-D1**: stamp/floor = merge commit `M`, re-pin rides the version-bump commit `B` (a commit cannot embed its own sha — two-commit minimum is structural); `F(fixes) → M(merge) → B(bump+re-pin) → T(tag)`; stage from detached tip-identity checkout; **no `--skip-build`** (the re-pin is daemon code compiled into the frozen binary); explicit `ENSEMBLE_ROLLBACK_SAFE=1` (delta-empty; rollback returns the defect until re-promote); version slot re-derived on three axes at bump time (currently `v0.31.1` — other lanes active). Ceremony preconditions incl. the **operator-owned scripts-pin advance** (live `.env` `ENSEMBLE_UPGRADE_SCRIPTS_DIR` targets the scripts-pin worktree — the fix reaches live only via that pin). | `phase3-plan.md` |

**Ordering:** Phase 1 → Phase 2 (tests re-cut to the landed design; the D-P2-3 amendment feeds back into Phase 1's implementation) → tester gate (Tier-3) → Phase 3 staging → Emperor's ceremony. Phases 1 and 2 share the branch's fix commits; Phase 3 owns the bump/tag/stage commits.

## 5. A/B/C adoption (ratified in `decisions.md` §1, L-1…L-4)

- **A — ADOPT with refinement** (D-P1-1): `124→budget` (legacy wording byte-preserved), `3→INFO log, no row`, `4/other→kind=nonzero_exit + true rc`; journal EVENT kind stays `subprocess_wait_timeout` (informational; renaming = churn for zero forensic gain).
- **B — ADOPT as "reorder-as-second-accept-path"** (not pure reorder): Layer 1 keeps its zero-cost fast accept; only on nonmatch does the containment ladder decide; deferral applies only to *unattributable-yet*, never to a positively-foreign holder; exhaustion ⇒ rc 1 ⇒ existing halt chain, B4 leave-txn-open, NO nohup fallback — unchanged.
- **C — ADOPT exactly as scoped** + T11 (rollback `HB_ERR` reader rides the lane).
- **Restart arm — ADOPT minimal** (labels + 7 journal rows; pre-`journal_init` exits stay silent per D-P2-2 allowlist; non-journal channel deferred).
- **One binding amendment** (`decisions.md` §2): R2 cgroup matching is **segment-aware** (`pcg == cg` or `pcg` contains `"$cg/"`), NP-6 is a hard pin, M-NP1b must redden it. Phase-1 §5.3's body is stale by design — the ledger wins (house rule: body vs ledger, diff every cycle).

## 6. Top risks (deduped across phases; detail in each phase file)

| # | Risk | Severity | Mitigation | Owner detail |
|---|---|---|---|---|
| 1 | **Over-permissive widening** — the ladder admits something it shouldn't | High | R1–R4 two-rung structure; positively-foreign refusal invariant (P5/P8/P9); NP-4/NP-6 hard pins with M-NP1/M-NP1b counter-mutations; segment-aware R2 (amendment) | phase1 §12, phase2 §9 |
| 2 | **Stale re-pin invisible at stage time** — every stage predicate passes on the old pair; the guard silently stops enforcing the fix | High | T7 asserts the pair equals `M` specifically; manifest↔binary constant cross-check | phase3 §8 (P3-R1) |
| 3 | **Tip moved** — origin/latest now `eca98397d`, +55 past the base, drift touches `daemon/` + `PACKS.md` | High | House FINALIZE: fetch + verify unmoved, moved ⇒ STOP and report; rebase-vs-merge is the **leader's call** (escalated, §9 below) | phase3 §8 (P3-R2) |
| 4 | **Fixture-model fiction** — the inherited-fd holder shape is unmodelled; stubs could pass while reality fails | High | Mandatory Layer-2 real-forking-server leg + real-pids/real-`/proc` leg (B1c); can feed back a Phase-1 correction | phase2 §15 (R-1) |
| 5 | **Collateral pin breakage** — frozen 7b/7c log contracts, rc=124 WARN, existing stub expectations | High | Must-survive vs planned-delta regression matrix authored BEFORE T5; enumerated collisions listed | phase2 §15 (R-3) |
| 6 | **Fix doesn't reach live** — ceremony runs the pinned scripts tree, not the release | High | Operator-owned scripts-pin advance recorded as ceremony precondition (D-P3-2) | phase3 §4.4 |

## 7. Disclosure set (D-P3-3 — CHANGELOG + this overview carry the same five)

1. Fix summary (healthy-boot false-halt; rc honesty; attribution ladder; stash; restart arm).
2. Floor/stamp re-pin to `M` (and why: enforcement, not cosmetics).
3. `rollback_safe=1` explicit — delta-empty rationale; rollback returns the defect until re-promote.
4. No-promote disclosure — lane stages only; ceremony is the Emperor's.
5. Scripts-pin precondition — operator advances `ENSEMBLE_UPGRADE_SCRIPTS_DIR` to the release tag before the ceremony.

## 8. Deferred register (see `decisions.md` §6; project future list)

- T12: Darwin launchd `_flip_verify_portattribution` widening (future id `143a045c`).
- Non-journal restart-audit channel for pre-`journal_init` exits (future id `28ca6ab0`).
- NP-11 Darwin parity leg — declared gap (follows T12).
- Tier-3 promote rehearsal execution — owned by the tester gate (D-P2-1), not deferred.

## 9. Open question escalated to the leader (the lane's single escalated decision)

**D-P3-1 — rebase or merge onto `origin/latest`?** The tip has moved 55 commits since the lane base and the drift touches this lane's surface (`daemon/`, `PACKS.md`). House FINALIZE makes this the leader's call; the mechanics are agnostic (only `M`'s identity — and hence the stamp/floor value — changes). **Blocking for execution (T2/T3), not for this plan.** See `decisions.md` §9.

## 10. Success criteria (lane-level)

1. A healthy-boot promote rehearsal (scratch) completes with **no halt** — the incident shape (version-correct green + inherited-fd holders mid-spawn) boots through.
2. The negative proof holds: all NP legs refuse/halt as specified, each reddened by its named counter-mutation.
3. Journal rows never claim `timeout` for rc≠124; `hand-back error: none captured` never appears on the systemd lane; the 7 restart classes journal a cause.
4. Stage completes ceremony-ready: stamp == floor == `M`, 4-way sha identity, manifest floor field matches, rollback_safe=1 — **without promoting**.
5. Zero daemon-side code changes beyond the `PIPELINE_SCRIPTS_FLOOR_COMMIT` constant.
