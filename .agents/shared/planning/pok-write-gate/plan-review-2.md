# Plan Review 2 — Delta Re-Review (fold verification, plan-review-1 → planner fold)

**Verdict: CONDITIONS — scoped to the delta only.** The fold is substantially sound: A1 closed at its cited site AND at the planner's extra site, 12/13 warnings closed as folded, both Q-rulings correctly adopted (Q2 byte-identical in both fence texts, zero stealth widening), no advisory expanded into work, task map = 128 rows confirmed with 128/128 six-cell population. But **one A1-class residue survives (C1 — must fold before build start)**, one warning's fix landed only in the ledger not the body (C2), and the fold introduced three chain gaps plus two count drifts (C3/C4/C5).

- **Date:** 2026-11-23
- **Mode:** Standard delta re-review — single worker `review-worker-pokgate-delta` (`bd468d92-60b3-42d6-8d4e-d82933341cca`), skill `post-fold-residue-sweep`
- **Baseline:** `plan-review-1.md` (same dir)
- **Aggregate: 1 CRITICAL · 5 WARNING · 9 ADVISORY · 0 SCOPE-EXPANSION**
- **Independently re-derived (not trusted from prose):** `KNOWN_TOOL_NAMES`=269 (worker re-derivation; `pok_gate_answer` absent ✓) · `.invoke(` = 37 / 9 ✓ · `.upsert(` = 97 ✓ · explicit `pok` grant census = 54 ✓ · sealed candidates = 17 (ari 4, PM 4, leader 5, devops 4), `meta.json` entries = 0 ✓ · all 16 sampled File-cell anchors resolve in the worktree ✓ · `architecture-recommendation.md` = 186 lines ✓

---

## Item verdicts (commission's 5 verification asks)

| # | Ask | Verdict |
|---|---|---|
| 1 | A1 closure TOTAL on all normative surfaces | **DEFECT — 12/13 sites closed; one unmarked normative residue (C1)** |
| 2 | W1–W13 closed as folded, new legs carry PACKS cells | **DEFECT — 12 VERIFIED · 1 partial (W9 → C2); fold introduced 3 chain gaps (C3)** |
| 3 | Q1 "71" deleted / gate retained; Q2 carve-out both fence texts, nothing widened | **VERIFIED (both)** |
| 4 | 18 advisories untouched (fold discipline) | **VERIFIED** — no expansion/drop/work conversion; 11 remain open, no tracking surface (C6/ADVISORY-7) |
| 5 | Task map 128 rows / six cells; "was 111" narrative | **128 CONFIRMED · six-cell 128/128 ✓ · BUT phase files hold 132 → 4 tasks unchained (C3) · "111" exists only in the planner's message, absent from artifacts (cannot be re-verified in-set)** |

---

## CONDITIONS (all delta-scoped, all mechanical)

### C1 🔴 CRITICAL — must fold BEFORE build start (A1-class residue; inherits A1's REJECT-if-unfolded disposition)
`plan-overview.md:16` (Wave-1 "In Scope" bullet) still reads in unmarked normative present tense: `family-count fix (strict per-key, 19/21 dissolve, 2 supersede-merges)` — prescribing the content-destructive shape for the 2 real families (`guard-inv`=4, `prompt-assent`=4) with **no supersede marker, no history note, no x-ref**, while the same file's phase table (`:37`), risk register (`:84`), success ledger (`:105`), and task map (`:179`) all prescribe split/re-key + archive. 13-site both-variant sweep: 12 OK (marked history / RESOLVED / correct wording), this is the only bare residue.
**Fix (one string):** replace `19/21 dissolve, 2 supersede-merges` with `19/21 dissolve, 2 SPLIT/RE-KEY + ARCHIVE (never content-merge)` (character-identical to `plan-overview.md:37`).

**Planner's extra-closure claim CONFIRMED:** `design-amendments-v2.md:57` now reads `originally drafted as a merge-supersede; SUPERSEDED by §13.4#1 + review A1: SPLIT/RE-KEY + ARCHIVE under a ruled basis (no content-merge)` — marked ✓, hazardous phrase removed ✓, replacement prescribed ✓.

### C2 🟡 — W9's fix landed only in the ledger; the body still carries the stale figure
Ledger correct (`plan-overview.md:190`: **37** `.invoke(` + **9**, with class breakdown — independently confirmed). But `phase3-plan.md:14` (the execution contract) still reads `Tool sweep (~18 .invoke in test_pok_tools_unit.py + 9 …)` — under-scopes the sweep by ~19 call sites; unswept upserts will RED once `reason` is REQUIRED.
**Fix:** mirror `plan-overview.md:190` verbatim into `phase3-plan.md:14`.

### C3 🟡 — 4 phase-file tasks unchained (task map 128 vs phase files 132); T12.6 numbering gap
Machine recount: phase files = 132 tasks (5+7+8+9+10+6+10+14+14+9+11+17+7+5); overview phase table sums to 132 ✓; **task map = 128**. Missing rows:
- `phase6-plan.md:13` (counter carry-over dropped → RED mutation) — no row, no `pok_write_gate_w1_unit_test` cell (map jumps T6.4→T6.5)
- `phase10-plan.md:18` (surface expired/never-answered gates, `phase=ASK_EXPIRED`) — fold-added, test leg, no pack cell
- `phase10-plan.md:19` (J partition wiring: MERGE-target selection) — fold-added, test leg, no pack cell; `grep "partition" plan-overview.md` → 0 hits
- `phase12-plan.md:22` exists as T12.7 but Phase-12 IDs are off-by-one: map carries T12.1–T12.5 + T12.7, **T12.6 does not exist**, and `T12.5`/`T12.7` resolve to the WRONG phase-file tasks (librarian-scope = phase-12 #6, not #7). Violates the map's own header invariant (`:160`: "One row per task: task → artifact → File cell → leg row → PACKS.md row").

**Fix:** add the three rows with File cell + leg row + PACKS cell (counter-carry-over → `pok_write_gate_w1_unit_test`; both Phase-10 legs → `pok_write_gate_w2_unit_test`), renumber Phase-12 map IDs to match `phase12-plan.md:17-23`, or record explicit deletion/merge notes.

### C4 🟡 — Phase-6 exit criterion counts 3 mutation legs; the task table lists 4
`phase6-plan.md:28` `3 mutation legs RED (family/reason/grant)` vs four mutation tasks at `:10-13` (incl. counter carry-over, appended without updating the exit criterion or the map).
**Fix:** `4 mutation legs RED (family/reason/grant/counter-carry-over)` — after C3's map row lands.

### C5 🟡 — W4-class over-broad claim survives at 3 sites ("supersede+archive for MERGE")
`plan-overview.md:240` (T8.2), `phase8-plan.md:4` (objective), `design-amendments-v2.md:82` state the execution step unqualified, while the F2 fork (no-delta MERGE = NO write + stamp only) is pinned in 4 OTHER places — `phase8-plan.md:10` contradicts its own `:4`.
**Fix (identical sentence ×3):** `supersede (MERGE delta, count-preserving); NO write (MERGE no-delta); archive residue under a ruled basis`.

### C6 🟢 — advisory bundle (optional, fold-at-convenience)
1. **Fold ledger absent (ADVISORY-7, governance):** `grep -rn "plan-review"` over the 20 plan files → 0 hits; closure evidence lives only in messages. Add `## Fold ledger (plan-review-1 → plan-review-2)` to `plan-overview.md` naming A1/W1–W13/Q1/Q2 dispositions + open advisories. Until then, `plan-review-1.md` + this file are the ONLY tracking surface for the 11 open advisories.
2. Q2 carve-out invisible at phase level — add one sentence to `phase13-plan.md` Risks (`phase13-plan.md` has no fence block; the baseline's ":11" was a task row, not fence text).
3. Reconcile §13.6 ↔ Phase-11 fork-pin sets: §13.6 (`design-amendments-v2.md:181-187`) has TTL but not alias/stub/switch; Phase-11 (`phase11-plan.md:19-25` + mirror `plan-overview.md:281-287`) has alias/stub/switch but TTL only rides a mutation leg (`:16`).
4. Mark stale upstream figures in `architecture-recommendation.md`: `:57` `271→272` (true 269→270, re-derived) and `:120,:184` bare "71" — add `(superseded by design-amendments-v2.md:181)`.
5. Resolve dangling x-ref `phase13-plan.md:4` `**W9**` — the only in-set W9 is plan-review-1's tool-sweep warning; unresolvable as written.
6. Mirror the `maintenance_runs` clause into overview T10.4 (`plan-overview.md:265`) — body (`phase10-plan.md:12`) has it, mirror doesn't (ADVISORY-1).
7. Split W7's acceptance cells (not just exit criteria) at `phase1-plan.md:12` / `phase5-plan.md:9` (ADVISORY-2).
8. Name the 4 baseline-regen entries at `plan-overview.md:202` / `phase4-plan.md:16` (ADVISORY-4 — 17 sealed candidates, 4 edited `tools_note.md`).
9. Anchor off-by-one: content fence is `pok_service.py:395`, not `:394` (`phase3-plan.md:12`, `plan-overview.md:188`).

---

## Q-ruling adoption detail (commission item 3)

- **Q1 VERIFIED:** bare "71" gone from plan-of-record (`plan-overview.md:62` now carries the reconciliation gate with census basis `DUPLICATE=2 / exact-body dup groups=0`; `:153` PRE-P4 gate; `:302` fence `Cite snapshot times, never bare counts`; delta doc `:169`/`:193-199`). Residuals all classified: baseline review file (history, OK), arch doc unmarked (→ C6.4), census prose-anchor (unrelated, OK).
- **Q2 VERIFIED:** carve-out present in BOTH extant fence texts — `plan-overview.md:302` and `design-amendments-v2.md:127` **byte-identical (diff-verified, 72 words each)**; stealth-widening scan clean (all other fence clauses preserved verbatim). `phase13-plan.md` has no fence block at all → phase-level invisibility noted as C6.2, not a defect against the ruling.

## Fold-discipline detail (commission item 4)

No advisory was converted into task/risk/criterion work. Items 7/8/14 of the baseline's 18 were found already satisfied by pre-existing content (not new work). **11 remain open** (3 real-judge shadow-mix, 4 pytest-mark registration, 6 kill-switch parity pin, 9 T9.7 grep artifact, 15 throughput risk row, 17 PACKS invocation/marker block, 18 FAKE_HOME/mktemp venue + partials 1, 8, 11) — fold-at-convenience per baseline framing; NOT build blockers. Disposition: **keep open**, tracked by this file until a fold ledger exists (C6.1).

## Gaps

None — single worker, completed, evidence-dense (re-derived counts, quoted lines, machine recount), no re-dispatch needed, no sanity markers.

## Process notes

- Worker's final message does not attest the mandated first-action `skill_feedback` call (second consecutive instance of this telemetry gap across both review rounds). Reports otherwise carry verifiable evidence; flagged, non-blocking.
- Sweep-criterion recommendation (structural blindness): the plan's own consistency sweep does not hunt BOTH variants of folded wording across summary/scope rows, nor two-sided task counts (map vs phase files). Add both as sweep criteria in the fold ledger.
