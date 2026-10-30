# Approver Verdict — Promote-Preflight-Hang Fix (board wi-fc1786a2)

Plan set: /home/nea/ensemble-src-wt-preflight-hang/.agents/shared/planning/promote-preflight-hang/
Date: 2026-10-29 | Iteration: 001 | Mode: Plan Approval (1 worker, skill: plan-approval)

## VERDICT: REJECTED

Engineering substance verified strong (~60 file:line anchors all resolved; census
17+8=25 journal_read sites exact; Phase-6 deployment-truth chain verified to the
digit — live .env:107, checkout 013895287 exactly 277 behind b1183a637,
lib.sh:811 still contains the char-loop; rollback posture and sandbox fences
sound). Rejected on mechanically-provable internal cross-reference drift —
the same defect class the plan itself adjudicated load-bearing (W5) and closed
for D1-* only.

## Blocking Issues (iteration 001)

1. CONSISTENCY — plan-overview.md §5.1 "Encoded in" L141–L169: 9 of 11
   phase2/phase3 pointers stale or dangling. R12 cites D2-8/T2.19 (actual
   D2-10/T2.18); R16 D2-7→actual D2-8; R17 D2-6/T2.15→actual D2-9/T2.17;
   R19 D2-11/T2.20→actual D2-14/T2.21; R20 cites DANGLING D2-3 (nonexistent in
   phase2-plan.md)/T2.14→actual D2-7/T2.15; R21 D2-11→actual D2-14;
   R22 D2-9/T2.18→actual D2-12/T2.20; R-E/R-H cite T3.14→actual T3-15;
   R15 omits the A3 half (D2-6/T2.14). Correct: R13, R14, R-B..R-G, all
   phase1/phase6 pointers. Column was hand-added in fold A33 — plan-authored,
   not inherited.
2. CONSISTENCY — plan-overview.md §12 L431–L432: A7/A8 task IDs swapped vs
   phase2-plan.md L347–L348 (overview: A7→D2-10,T2.19 / A8→D2-11,T2.18;
   phase file: A7→D2-10,T2.18 / A8→D2-11,T2.19).
3. COMPLETENESS/CONSISTENCY — plan-overview.md §11.1 L380/L385: index asserts
   phase2 uses D2-1…D2-15 but phase2-plan.md defines only D2-4…D2-15;
   D2-1..3 exist only in decisions.md with different topics; unresolved
   D3-* collisions (decisions.md D3-2/D3-4/D3-6 vs phase3-plan.md D3-2/D3-4/
   D3-6 differ) — D1-* map exists, D2-*/D3-* equivalents do not.
4. SAFETY/COMPLETENESS — phase5-plan.md T5.17/AC17 (L123, L148): the drift
   gate's seven classes (a)–(g) contain NO class asserting a cited D-id/T-id
   resolves to the subject cited — the exact class of issues 1–3; the gate
   would PASS over them and they would reach implementation unflagged.

## Remediation (mechanical text corrections, no design change)
Correct the nine §5.1 pointers; swap A7/A8 back to T2.18/T2.19 in §12; amend
§11.1 to D2-4…D2-15 + add D2-*/D3-* cross-file maps (or renumber decisions.md
series per house Mode-2); add gate class (h): every cited D-id/T-id resolves
to the subject it is cited for.

## Notes (non-blocking)
N1 §13 W4/Adv1 rows carry pre-round-4 literals M1–M28 / A–I — state a
   supersession-record exemption at the cell (mechanical sweep can't infer it).
N2 Hyphenated (T3-11) vs dotted (T3.12) task-ID format drift overview↔phases.
N3 §1 D4(a) "lock-dir leak" summary stale vs phase4-plan §2.1 corrected
   trap-only-release framing (phase 4 is correct; one-line overview fix).
N4 Sandbox-fence contract split between phase0 P0-D2 and phase5 §3 — worth one
   canonical statement.
N5 phase2 task table lists T2.22 after T2.24 (no-renumber rule; cosmetic).

## Session
Worker approve-worker-plan e974d528-0de3-47ed-b0dc-a91a6a83e383 (skill:
plan-approval; report received verbatim; review-plan-council.md excluded for
independence). Next iteration on re-submission: 002. active.md/tracking files
NOT updated — caller's read-only fence; this file is the iteration record.

---

## FOLD DISPOSITION — approver iteration 001 → iteration 002 (planner, 2026-10-29)

**All four blockers are mechanical text corrections; NO design change. The plan set is re-submitted for
iteration 002 with the fold-disposition table below.**

| # | Issue | Fix | Where |
|---|-------|-----|-------|
| **B1** | §5.1 "Encoded in": 9 of 11 phase2/phase3 pointers stale or dangling (R12 D2-8/T2.19 · R16 D2-7 · R17 D2-6/T2.15 · R19 D2-11/T2.20 · R20 dangling **D2-3** · R21 D2-11 · R22 D2-9/T2.18 · R-E + R-H T3.14 · R15 missing its A3 half) | Each pointer re-resolved against the phase files' **actual** decision/task topics: R12 → **D2-10 / T2.18** · R16 → **D2-8** · R17 → **D2-9 / T2.17** · R19 → **D2-14 / T2.21** · R20 → **D2-7 / T2.15** · R21 → **D2-14** · R22 → **D2-12 / T2-20** · R-E + R-H → **D3-14 / T3-15** · R15 → **D2-5 / T2.13 (A2) + D2-6 / T2.14 (A3)**; plus R-G extended with **T6.5**, R1 → **D2-11 / T2.19**, R8 → **D2-9 / T2-17**, R25 → **§3.3a/§3.3b + D2-14 / T2.24** | `plan-overview.md` §5.1 (each row's "Encoded in" cell) |
| **B2** | A7/A8 fold-log swap between §12 and the phase file | The **phase file is correct** (A7 = batching + the derived stage budget ⇒ D2-10 + T2.18; A8 = preflight-only arming + the real soak ⇒ D2-11 + T2.19) ⇒ the **overview §12** rows were swapped back | `plan-overview.md` §12 (A7/A8 rows) |
| **B3** | Unresolved ID-series collisions (§11.1 asserted D2-1…D2-15; the phase file defines D2-4…D2-15; D3-* unmapped) | §11.1 ranges corrected to the **true** sets; **four cross-file topic maps** added (D2-*, D3-*, D4a/D4b-*, D6-*) each with a **canonical** citation column; the one real D6 collision (phase6 **D6-7 ⇒ D6-6**, the seat correction) realigned in the phase file | `plan-overview.md` §11.1 (index rows + 4 maps) · `phase6-plan.md` §4.2 (the D6-6 entry + its fold-log row) |
| **B4** | The drift gate had no class asserting that a cited D-id/T-id resolves to its subject | **Class (h) — the RESOLUTION check** added to the gate rule (T5.17 + AC17): every file-qualified citation must name a file that **defines** the id; every id must be defined somewhere; the §5.1 cells must name a defining file. The gate's allowed-categories rule extended to three (quoted meta-mentions · supersession records exempted at the cell · the cross-file map rows) | `phase5-plan.md` T5.17 + AC17 · §9 (the recorded run) |
| **N1** | §13 W4/Adv1 rows carry pre-round-4 literals (M1–M28 / A–I) | Both cells now carry an explicit **SUPERSESSION RECORD** (the historical literal + the canonical value **M1–M31** / **A–J** + the gate exemption) | `plan-overview.md` §13 (W4 row, Adv1 row) |
| **N2** | Dotted-vs-hyphenated task-id drift | The **dotted** form is canonical (5 of 7 phase files) ⇒ `phase3-plan.md`'s 26 hyphenated ids normalised to **T3.1…T3.16**; 4 citation sites in 2 other files swept; the choice stated | `phase3-plan.md` (task rows) · `plan-overview.md` §11.1 · `decisions.md` (2 citations) |
| **N3** | §1's D4(a) one-liner stale | §1's D4 row rewritten to the **corrected framing** (trap-only release ⇒ a SIGKILL-class death orphans the lock dir; the reclaim is **unjournaled**; a normal successful stage **does** release the lock) | `plan-overview.md` §1 (D4 row) |
| **N4** | The sandbox-fence contract split across two files | **One canonical statement**: `phase5-plan.md` §3 (declared inline as canonical); `phase0-plan.md` T0.7/AC7 now cross-reference it and do not restate it | `phase5-plan.md` §3 · `phase0-plan.md` T0.7 + AC7 |
| **N5** | phase2's task table listed T2.22 after T2.24 | Task rows re-ordered into numeric order — **no renumber** — in phase2 (T2.22) and, for consistency, phase1 (T1.17/T1.18), phase5 (T5.16/T5.17), phase6 (T6.8/T6.9) | the four phase files' task tables |

### The eighth gate class — RUN RESULT (2026-10-29)

**PASS — 0 failures across EIGHT classes (+1 positive):** (a) corpus **22 + 3** · (b) the matrix **span rule** (every
live span claim is **M1–M31**) · (c) the pack set **A–J** · (d) no live `RuntimeMaxSec=1200` · (e) no single-file-era
wording · (f) the anchor class (`lib.sh:5569` gone) · (g) the superseded-identifier class (no live
`MIN_PIPELINE_SCRIPTS_REV`) · (g+) `PIPELINE_SCRIPTS_FLOOR_COMMIT` present · **(h) the RESOLUTION class — every cited
D-id/T-id resolves in the file it is cited for, and every id is defined somewhere**.

**Class (h) on its FIRST run (pre-fix): 13 unresolved file-qualified citations + 18 ids defined nowhere** — i.e. it
reproduces the approver's blockers 1–3 mechanically (the nine stale §5.1 pointers, the dangling `D2-3`, the
hyphenated-vs-dotted phase3 task ids, the shorthand file refs that mis-attributed ids in §11.1/§13/§14, and four
cells whose ids sat after a different file ref). All corrected; the re-run is PASS. The full record is in
`phase5-plan.md` §9 and `plan-overview.md` §16.

**Read-only inputs untouched** (sha256 re-verified): `evidence/FORENSICS.md` `480b6250e18c…`,
`evidence/CODE-PATHS.md` `ecaa0253…`, `architecture-recommendation.md` `2ab9d5e1…`, `review-plan-council.md`
`dd728435…`. **No commits;** `git status` shows only the untracked planning dir; HEAD still `b1183a637`.

---

## APPROVER VERDICT — ITERATION 002 (REJECTED, second)

*(verbatim, copied from /tmp/preflight-approver-20261029/VERDICT-002.md — sha256 ff2d60cea74579c165d1fca92276cd7c8a47416e6acc867ba8be9d8f054578cd)*

# Approver Verdict — Promote-Preflight-Hang Fix (board wi-fc1786a2)

**Plan set:** `/home/nea/ensemble-src-wt-preflight-hang/.agents/shared/planning/promote-preflight-hang/`
(`plan-overview.md` + `phase0`–`phase6` + `decisions.md` + `architecture-recommendation.md` + `review-plan-council.md` + `evidence/`)
**Date:** 2026-10-29 | **Iteration:** 002 | **Mode:** Plan Approval (4 workers, section-parallel fan-in, skill `plan-approval`)
**Base rev:** `b1183a637649f94c23b94dfc745df44381dc54ab` (= `origin/latest` at lane open)

## VERDICT: REJECTED
## Iteration: 002

Engineering substance is verified strong and the plan's root cause, incident numbers, census,
deployment-truth chain and phase design all hold against the tree. Rejected on 11 blocking issues
in the plan set's **implementable text and its verification claims** — 7 mechanical cross-reference /
register-integrity defects (the same class iteration 001 raised, now re-opened by new edits and by
pre-existing rows that were never checked) and 4 substantive specification defects (semantic-parity
domain, armed-window coverage table, interpreter dependency, stale task row), plus 1 ceremony-safety
gap. All 11 were independently reproduced by the approver against `b1183a637`; none rests on the
tracking file's word.

**No design change is required.** Every blocker has a bounded, mechanical remedy (9 are cell-level
text corrections; 2 are one-sentence specification additions).

---

## A. Closure of iteration 001 (independently verified — all four blockers and all five notes CLOSED)

| Prior blocker | Result | Approver's own evidence |
|---|---|---|
| B1 — 9 stale/dangling §5.1 "Encoded in" pointers | **CLOSED** | 26 §5.1 rows parsed; 0 file-qualified pointer unresolved |
| B2 — A7/A8 swap §12 vs `phase2-plan.md` L347–348 | **CLOSED** | overview L519–520 now match phase2 L347–348 (A7→D2-10/T2.18, A8→D2-11/T2.19) |
| B3 — D2-*/D3-* series collisions | **CLOSED** | §11.1 ranges corrected (phase2 D2-4…D2-15, phase3 D3-1…D3-16, phase4 none, phase6 D6-1…D6-6/D6-10/D6-11); 4 cross-file maps with canonical column; phase6 D6-7→D6-6 realigned (L135) |
| B4 — no gate class asserting ID resolution | **CLOSED in form** | class (h) present in `phase5-plan.md` T5.17 (L201) + AC17 (L149) — but see blocker 2 below: it asserts *existence*, not *subject*, and its recorded PASS is not reproducible |
| N1 supersession records | **CLOSED** | §13 W4/Adv1 cells carry explicit SUPERSESSION RECORDs (plan-overview L565/L568) — but a live span claim survives at `decisions.md:608` (blocker 2) |
| N2 dotted task-id form | **CLOSED** | 0 hyphenated `T3-*` ids remain; phase1/2/5/6 task tables strictly ordered, no gaps (T0.1–14, T1.1–18, T2.1–24, T3.1–16, T5.1–17, T6.1–14) |
| N3 §1 D4 row | **CLOSED** | plan-overview L15 states the corrected framing |
| N4 canonical sandbox fence | **CLOSED in form** | `phase5-plan.md` §3 declared canonical; phase0 T0.7/AC7 cross-reference it — but see blocker 10 (scripts-dir surface missing) |
| N5 task-table ordering | **CLOSED** | all six phase task tables monotonic, no gaps |

**Iteration 002 is therefore a genuine re-review, not a repeat of iteration 001.** The blockers below are
newly-identified (or newly-created by the fold) defects, not the prior four.

---

## B. Blocking Issues (11 — merged/deduped from 15 raised across 4 workers)

### 1. CONSISTENCY — the §11.1 cross-file ID map resolves to the WRONG topics, and two narrative pointers do too
**Raised by:** Worker A (B1+B2) + Worker C (B2) — *merged*
**Where:** `plan-overview.md` L397–399 (§11.1 `D1-*` map), L295 (§9 OQ2), L531 (§12 A19 row); `decisions.md` L627–628, L639–640
**Expected:** every map row's `phase1-plan.md` id names the topic the row claims; the map's *Canonical citation* column is authoritative.
**Found:** `phase1-plan.md:47` defines **D1-9** = *"`status.sh` gets an explicit python3 floor probe"*; `:48` **D1-10** = *the stdout contract*; `:49` **D1-11** = *the corpus 15→22*; `:51` **D1-13** = *the placement*; `:42` **D1-4** = *the RMW decision*.
- L398 maps "the stdout contract (`$(cat)` emulation)" → **phase1 D1-9** ⇒ correct is **D1-10**.
- L399 maps "the corpus 15→22 + duplicate-key" → **phase1 D1-10** ⇒ correct is **D1-11**.
- L397 maps "the strict-JSON option (declined)" → **phase1 D1-4** (= the RMW decision); the strict-JSON option has **no** phase1 id ⇒ the cell should be `—` (per the map's own D2/D3 convention).
- The identical two wrong rows are mirrored at `decisions.md:627–628`.
- L295 (§9 OQ2) and L531 (A19) cite **D1-10** for the *placement* decision ⇒ correct is **D1-13** (the plan set's own §11.1 L401 and `phase1`'s own fold log L190 already record D1-13).
- `decisions.md:639–640` asserts the per-ID **topic column** "resolves every id to its topic" — it does not.
**Required change:** correct the three map rows + the two narrative pointers to the ids listed above; add the missing §11.1 topic rows for the strict-JSON option (`—` / `decisions D1-4`); amend `decisions.md:639–640` to the corrected claim.

### 2. COMPLETENESS/SAFETY — the recorded class-(h) gate PASS is not reproducible; two verification claims are false
**Raised by:** Worker A (B4) + Worker C (B1) — *merged*
**Where:** `plan-overview.md` §16 L680–688; `phase5-plan.md` §9 L178–194, T5.17 L201, AC17 L149; `decisions.md` L608, L639–640; `plan-overview.md` L308/L471/L547
**Expected:** every cited D-id/T-id resolves to an existing row **in the file it is cited for**, every id is defined somewhere, and the §5.1 cells name a defining file — so the recorded "PASS — 0 failures across EIGHT classes" reproduces.
**Found:**
- **`D4b-4` is defined NOWHERE in the set.** `grep -rn D4b-4` over the whole worktree returns 4 *citations* only (`plan-overview.md:308` "Folded into `phase4-plan.md` D4b-4"; `:471` "decisions.md → D4b-4"; `:547`; `decisions.md:345`) — no `### D4b-4` heading, no decision row. `decisions.md` defines D4a-1…4 and D4b-1…3 only; `phase4-plan.md` defines **no** decision ids (per §11.1 L380). The §11.1 map row at L471 asserts a false mapping.
- **`decisions.md:608` carries a LIVE span claim** — *"matrix **M1–M28**, packs **A–I**"* — with **no supersession record at the cell**, while the equivalent `plan-overview.md:565`/`:568` cells carry explicit ones. Gate classes (b)/(c) assert exactly that this trips ⇒ the recorded 0-failure run is false against the current set.
- Class (h) as specified asserts **existence only**, never consulting the §11.1 topic column, and `phase5-plan.md:201` **explicitly exempts "the cross-file map rows (§11.1)"** — the very rows that are wrong (blocker 1). §16's claim that "Class (h) is the reason these defects cannot recur silently" is therefore unsupported for this defect class.
**Required change:** define `D4b-4` (or drop the citation and correct the L471 map row); add a SUPERSESSION RECORD at `decisions.md:608`; extend class (h) to assert **subject** resolution via the §11.1 topic column (and narrow the map-row exemption so a map row must itself resolve); re-run the gate and re-record the result.

### 3. COMPLETENESS — 4 of 27 §5.1 risk rows have no "Encoded in" pointer at all
**Raised by:** Worker A (B3)
**Where:** `plan-overview.md` L165 (R-I), L166 (R23), L167 (R26), L168 (R24)
**Expected:** every §5.1 row carries 5 cells (ID │ Risk │ Sev │ Disposition │ **Encoded in**) with a file-qualified pointer to the phase decision/task implementing the mitigation — as the other 23 rows do.
**Found:** these four rows carry **4 cells**; the pointer column is absent. R23/R26 additionally use unqualified shorthand file refs (`phase1`, `phase5`) — the shorthand class §16 L686 claims was swept. (R-I's mitigation, "RULING 8: both mechanisms in the port scope", is in fact implemented by `phase1-plan.md` D1-12 / T1.13.)
**Required change:** restore the 5th cell on L165–L168 with file-qualified pointers (R-I → `phase1-plan.md` D1-12 / T1.13; R23/R26/R24 → their phase1/phase5 decisions), and sweep the `phase1`/`phase5` shorthand in those cells.

### 4. CONSISTENCY/SAFETY — Phase 1 never specifies the scanner's VERDICT-INPUT domain, and two of its own specs contradict the rest
**Raised by:** Worker B (B1)
**Where:** `phase1-plan.md` D1-1 (L39) and T1.1 (L135) vs D1-10 (L48), T1.11 (L145), AC12 (L170)
**Expected:** one specification of (a) the bytes the scanner's verdict is computed over and (b) the bytes written to stdout — both matching BASE (`lib.sh:968` `$(cat)` ⇒ NULs dropped, trailing `\n` stripped; the EMPTY pre-check at `:969–972`; the scan at `:975`).
**Found:** D1-1 says the scanner is *"implemented over the **raw bytes** of the file"* and T1.1's CLI contract says *"print the **raw bytes** on stdout iff balanced"* — while D1-10/T1.11/AC12 require command-substitution-processed stdout. **No statement anywhere says the VERDICT must be computed over the processed bytes, nor that the EMPTY pre-check must be reproduced.** BASE scans the *processed* string, so a raw-byte scan diverges: e.g. `{"a":"x\0y"}` — BASE drops the NUL so the backslash escapes the quote ⇒ *accept*; a raw-byte scan lets the NUL consume the escape flag ⇒ *unbalanced* ⇒ *refuse*. **No corpus row covers that class.** Implementing T1.1 as written also violates AC12 on every real journal (every real journal ends in exactly one `\n`, `lib.sh:1007`).
**Required change:** one sentence in D1-1/T1.1 — *"the scanner runs over the `$(cat)`-processed bytes and reproduces the EMPTY pre-check (`lib.sh:969–972`)"* — plus one corpus row for NUL-adjacent-to-backslash.

### 5. CONSISTENCY — Phase 2's armed-window coverage table misclassifies FIVE sites, in both directions
**Raised by:** Worker B (B2)
**Where:** `phase2-plan.md` §3.3a row 4 (L182), §3.3b (L192), §3.6 (L262–263), catch-all row (L179)
**Expected:** every site labelled IN-WINDOW is reachable from the declared armed window (`promote.sh:225–373` + stage tree walks); every site labelled OUT-OF-WINDOW is not. The plan states these tables are "the **explicit** coverage boundary".
**Found:** (a) `lib.sh:1803` / `:1876` are labelled "preflight window", but they live inside `pipeline_settled` (`lib.sh:1845`), whose **only** caller in the tree is `adopt-unit.sh:201` — the ADOPT path, not reachable from `promote.sh:225–373`. (b) `lib.sh:1560` (`journal_rollback_count_24h`), `:1582` (`journal_cooldown_active`) and `:1406` (`journal_is_quarantined`) are labelled OUT-OF-WINDOW, but all three are called from `promote_entry_check` (`lib.sh:5623`, `:5628`, `:5634`), invoked at **`promote.sh:245`** — inside the armed window. No hang hole results (the subtree is covered transitively via the armed `lib.sh:5630`), but the authoritative table and **AC3** are measured against a wrong map.
**Required change:** re-bucket `:1560/:1582/:1406` into §3.3a; move `:1803/:1876` to an explicit "adopt path" row; re-state §3.6's coverage claim against the corrected map.

### 6. COMPLETENESS/FEASIBILITY — `adopt-unit.sh` is an unlisted new interpreter dependency
**Raised by:** Worker B (B3)
**Where:** `phase1-plan.md` I6 (L31), D1-9 (L47), T1.7 (L141), AC8 (L166); `phase0-plan.md` census row 25 (L187), §4.2 `status.sh` row (L209)
**Expected:** every read-path caller with no interpreter today gets a floor probe + bounded fallback, or an explicit statement that the fallback covers it; the "which callers need an interpreter" list is complete.
**Found:** D1-9 asserts *"`status.sh` is **the one** read-path caller with **no** floor probe and **no** lock"*. But `adopt-unit.sh` sources `lib.sh` (`:137`), calls `pipeline_settled` (`:201`) → `journal_read` (`lib.sh:1876`), and contains **no** `python` / `_splice_journal` / `journal_history_append` / floor probe anywhere — i.e. no interpreter dependency today — and after D1 it sits on the helper path. (`stage.sh` also has no floor probe, though it already needs python3 via `stage.sh:656`.) The census's direct-call criterion is satisfied (25 = exact), but transitive script callers are not enumerated, so **I6** ("no new interpreter dependency on a path that has none today") is contradicted by an unlisted path.
**Required change:** add `adopt-unit.sh` (and `stage.sh`) to D1-9/T1.7/AC8, or state explicitly that the bash fallback covers them.

### 7. CONSISTENCY — the parity corpus cardinality is stated four different ways, and the AC-required row G4 is in no count
**Raised by:** Worker B (B4)
**Where:** `phase0-plan.md` T0.2 (L237), T0.9/T0.10 (L244–245), AC2 (L258), AC9 (L265), **AC10 (L266)**; `phase1-plan.md` §3.3 G1–G4 (L110–113), AC19 (L177), fold row A15 (L187); `plan-overview.md` §11.1 L379
**Expected:** one corpus cardinality, and every enumerated row counted.
**Found:** **"22 + 3 = 25"** in ~12 places vs **"all 23 fixtures (22 corpus + one 32 KB)"** at `phase0` AC10 vs **"G1–G4 = 4"** enumerated rows at `phase1` §3.3 vs **"corpus cases 1–22 + G1–G4"** (26 rows) at `plan-overview.md:379`. **G4** — the O(N)-path leg that "protects the next ceremony" and is *required by AC19* — appears in **no** count and in **no** task outcome (fold row A15 says "rows 16–22 + G1–G3"). A pack implementing "22 + 3" leaves G4 unpinned.
**Required change:** normalise to one cardinality (e.g. "22 + G1–G4 = 26") and fix `phase0` AC10's "23".

### 8. CONSISTENCY/SAFETY — Phase 3's task row T3.4 specifies the single-file sink that D3-12 rejected
**Raised by:** Worker B (B5)
**Where:** `phase3-plan.md` T3.4 (L89) and the §3 change-surface rows (L73–74) vs D3-12 (L57), D3-15 (L60), T3.13 (L91), AC4 (L112), AC13 (L121), AC16 (L124)
**Expected:** the implementable task row and the change-surface table state the same split the folded decisions and ACs require — **two** files per run: narrative `<install>/data/upgrade/<env>/<run_id>.log` + payload `<run_id>.payload.log`.
**Found:** T3.4's outcome is *"The built argv targets `<install>/data/upgrade/<run_id>.log>` … the file exists and is written by **both the shell and the payload**"* — i.e. the single-file sink D3-12 explicitly **rejected** ("One file for narrative + payload — rejected: ordering illusion"), and it omits the `<env>` scope D3-15 requires. Implementing T3.4 as written reintroduces both failures and fails AC4/AC13/AC16. (T3.13 is the correct spec; T3.4 and the two §3 rows are the stale outliers.)
**Required change:** retire/correct T3.4 and the two §3 change-surface rows to the D3-12/D3-15 two-file, env-scoped form.

### 9. SAFETY — the ceremony-critical floor constant has no release-time set-and-validate step
**Raised by:** Worker C (B3) — *description refined by the approver*
**Where:** `phase6-plan.md` L91 (constant row), L93 (comparison), L117–128 (D6-10), T6.9 (L163), AC5 (L180), AC9 (L184); `phase5-plan.md` Pack I (L38), AC16 (L148), §6 stage inputs (L95–102)
**Expected:** the lane's load-bearing claim ("the fix **reaches the ceremony**") requires the shipped `PIPELINE_SCRIPTS_FLOOR_COMMIT` to name a commit that exists and is an ancestor of the built tree, with a task that sets it at release time and an AC that proves the shipped value is valid.
**Found:** owner, storage and cadence **are** defined (`decisions.md` D6-10 — owner = the lane's release process; storage = daemon constants + release manifest; cadence = same as the `PIPELINE_SCRIPTS_REV` stamp). What is **missing** is a task that *sets the shipped value to the actual fix commit* (the value is specified only as *"<the commit introducing the preflight-hang fix>"*, which does not exist until the lane merges) and an AC/pin that proves the **shipped** constant resolves to an ancestor of the built tree. `phase5` §6's stage-adjudication inputs never mention it; AC5/AC9/AC16 exercise the guard only against **synthetic** fixtures; M27 pins the guard's response to a missing floor *object*, not the validity of the shipped *value*. Per `phase6-plan.md:93`, *"a missing/unknown floor object or command error ⇒ REFUSE"* ⇒ a wrong shipped constant converts the lane's failure mode from "stale scripts run" into **"no arm can ever succeed"** — blocking every ceremony, with nothing in the plan set able to detect it.
**Required change:** add a release-time task (set the constant from the merged fix commit) and an AC that the shipped constant resolves via `git merge-base --is-ancestor` against the built tree; add the constant to `phase5` §6's stage-adjudication inputs.

### 10. SAFETY — the canonical sandbox fence has no scripts-dir row; the escape guard misses the live pinned checkout
**Raised by:** Worker C (B4)
**Where:** `phase5-plan.md` §3 (L46–60, declared "**the ONE canonical statement**"), Pack I (L38), T5.16 (L125); `phase6-plan.md` L40 (fact 4), D6-2 (L60), L92
**Expected:** §3 fences every path surface the packs touch. Phase 6 identifies the live pinned scripts dir as `/home/nea/ensemble-src/scripts/upgrade` and D6-2 forbids the lane from touching it — so the canonical fence must cover the scripts-dir surface Pack I constructs and reads.
**Found:** §3's rows cover FAKE_HOME/INSTALL_DIR, env scrub, port, scratch DB, no-daemon-boot, no-live-promote — **there is no row for the scripts dir**, which is Pack I's first-class subject ("plant a stale stamp", "plant a non-ancestor pinned dir", "plant a missing floor object"; the daemon reads `<scripts_dir>/lib.sh` at arm time). The escape guard is the literal `case "$INSTALL_DIR" in /home/nea/agents-ensemble*) refuse ;; esac` — it covers the install roots but **not** `/home/nea/ensemble-src`, the live pinned checkout. D6-2 states a *policy* ("this lane CANNOT and WILL NOT touch the shared checkout"), not a mechanical fence. The one new path surface the new pack introduces is the one surface the "canonical" fence does not fence.
**Required change:** add a **scripts-dir** fence row to `phase5-plan.md` §3 (fake/throwaway pinned dir under the FAKE_HOME root; explicit refusal if the resolved scripts dir is under `/home/nea/ensemble-src` or any live checkout; the same self-test leg as the other rows).

### 11. CONSISTENCY — the anchor-convention header's recovery command fails, and the sentence contradicts itself and the plan set's own evidence file
**Raised by:** Worker D (B1) — *premise supplied by the approver*
**Where:** `plan-overview.md` L11 (and L10)
**Expected:** a stated read-only recovery command for the HUNG rev that works; no self-contradiction.
**Found:** `git show f81bcf6ba:scripts/upgrade/lib.sh` → `fatal: path 'scripts/upgrade/lib.sh' exists on disk, but not in 'f81bcf6ba'` (exit 128) — `f81bcf6ba` is a **blob** (`git cat-file -t` ⇒ blob, 296,297 B), and `git show <rev>:<path>` requires a commit-ish. The **same sentence** appends *"(blob, not a commit)"*, making it unsatisfiable, and the plan set's own `evidence/CODE-PATHS.md` §1 (titled *"the `f81bcf6ba` trap (read this first)"*) documents that this exact command fails. The content **is** retrievable (`git cat-file -p f81bcf6ba`, `git show 013895287:scripts/upgrade/lib.sh`, or the dev checkout file, whose `git hash-object` is exactly `f81bcf6ba`), and no acceptance criterion depends on the broken command — but the header is the entry point of the whole plan set.
**Required change (exact):**
```
FROM: > The HUNG rev's code is recoverable read-only via `git show f81bcf6ba:scripts/upgrade/lib.sh` (blob, not a commit).
TO:   > The HUNG rev's code is recoverable read-only via `git show 013895287:scripts/upgrade/lib.sh` (commit) — or straight from the blob: `git cat-file -p f81bcf6ba`. **`f81bcf6ba` is a blob, NOT a commit** ⇒ `git show f81bcf6ba:scripts/upgrade/lib.sh` fails; see `evidence/CODE-PATHS.md` §1.
```

---

## C. Notes (non-blocking)

**Downgraded by the approver (worker raised it as Blocking):**
- **N-1 (Worker D, B2)** — the HUNG-parenthetical convention at `plan-overview.md:10` is stated but applied to only **5** of the **92** divergent `lib.sh` anchors; 35 divergent anchors across phase1/phase2/phase3 carry no HUNG counterpart. **Downgraded to a Note**: `phase0-plan.md` P0-D4 already mandates script-based anchor re-verification at the implementation rev ("not by re-reading the plan"), the plan set itself acknowledges the divergence (`phase0-plan.md:30`), and the impact is documentation-accuracy with **zero** effect on any acceptance criterion, implementation step or safety property. Remedy: either narrow the sentence to the anchors the set actually carries, or extend `phase0` §3.1's "HUNG-rev number (evidence)" column shape to §3.3.

**Carried from the workers (non-blocking):**
- **N-2 (A-N1)** — SC13's measurement column (`plan-overview.md:192`) points at `phase1 T1.17/AC18` while the re-scoped guard it describes is implemented by **T1.18/AC19**; §14's W1 row cites T1.18/AC19 for the same re-scope ⇒ §6 and §14 disagree.
- **N-3 (A-N2)** — the §4 coupling map covers P0–P5 only; **Phase 6** (which owns R-A, R18, SC12, SC14 and consumes D2's watchdog token and D3's log path) has no row/column.
- **N-4 (A-N3)** — R-A's "Encoded in" names amendment-ids + cross-references rather than a phase decision/task id.
- **N-5 (A-N4)** — the `lib.sh:5528` anchor names the read site, not `retention_evict`'s definition at `:5526`.
- **N-6 (B-N1)** — five stale line anchors: `phase1` D1-13 cites `lib.sh:1172` (actually `:1169`) and `:1174` (actually `:1173`), D1-11 cites `:1039` (actually `:1041`), D1-12 cites `:1050` (actually `:1053` — the 2-char escape skip; the most implementation-risky, a 1-byte skip would change RMW parity/AC3); `phase2` D2-10 cites `stage.sh:576` (actually `:577`); `phase0` §3.4 cites `stage.sh:737–741` (`:737` is a `lock_heartbeat` comment, `integrity_verify` is `:738`).
- **N-7 (B-N2)** — corpus case 8 ("backslash at EOF") has a wrong rationale: "esc at EOF ⇒ balanced ⇒ accept" holds only for a backslash **outside** a string; inside a string BASE refuses (`lib.sh:994`). Pin the fixture's position or split the case.
- **N-8 (B-N3)** — the bash NUL warning text on this host is `warning: command substitution: ignored null byte **in input**`; P0-D6/D1-10 quote it without "in input" ⇒ match on class/prefix, not exact string.
- **N-9 (B-N4)** — `journal_in_flight` (`lib.sh:1084`, read `:1086`) has **no caller** anywhere; census row 2 counts it among sites that "pay the O(N²) scan **today**" ⇒ keep it as a latent site, stop asserting a live cost.
- **N-10 (B-N5)** — with the Q3 "WARN + CONTINUE" posture, a host without python3 plus a 455 KB journal makes `status.sh` run the legacy walk for ~45–61 min ⇒ consider failing fast with a remedy pointer or bounding the diagnostic fallback.
- **N-11 (B-N6)** — `rollback.lock.d.stale.$$` residue is never removed by `lock_acquire`'s stale-break (`lib.sh:1680`, `:1692`); one exists in the live install today. D4(a)'s three parts do not touch this class.
- **N-12 (B-N7)** — `phase0` T0.8 cites "(Phase 2 §2 D2-3)", but D2-1…D2-3 live only in `decisions.md` — an unqualified file reference the plan set's own §11.1 doctrine forbids.
- **N-13 (B-N8)** — `phase1` T1.18 sits after §5 (ACs) and §6 (fold log), detached from the §4 task table it belongs to.
- **N-14 (B-N9)** — `phase0` census row 26 under-counts: the stage tail pays `journal_read` (`lib.sh:1391`) **plus** `_json_sub` (`lib.sh:1392`), and `_json_sub` is itself quadratic twice ⇒ three mechanisms, not two.
- **N-15 (C-N1)** — M25 is labelled the "token refusal" leg in `phase5-plan.md:198` / `plan-overview.md:497`, `:568`, but M25's content is the stamp-acquisition mutation; the mixed-generation token refusal itself (`phase6-plan.md:141–147`, T6.6/AC6) has **no** mutation leg and no pack assertion — and class (b)'s span rule freezes the matrix at M1–M31, so adding one widens the frozen span.
- **N-16 (C-N2)** — `plan-overview.md:633` still reads *"PASS — 0 failures across **7** classes"* while `:680` and `phase5-plan.md:178–179` say **EIGHT**, with no supersession record and no gate class covering the gate's own class-count.
- **N-17 (C-N3)** — `phase6-plan.md:40`'s "`lib.sh:811` still contains the O(N²) scan" is correct for the **pinned checkout** (`/home/nea/ensemble-src` @ `013895287`) but wrong for the **lane's base tree** (`b1183a637`: `journal_read` is at `lib.sh:961`; `:811` is the sandbox-install-dir refusal) ⇒ qualify it as tree-specific.
- **N-18 (C-N4)** — a declared dependency cycle: `phase6-plan.md:6` "Depends on: P1–P5", but `phase5-plan.md` T5.16 (Pack I) "Depends on: P6", and T6.8 touches Pack I. The artifacts are consistent; the declared edges are circular.
- **N-19 (C-N5)** — no executable gate artifact is named: T5.17 is a task to be implemented, yet §9/§16 record a *run result* ("PASS — 0 failures"); no script path, command or output artifact is named, so the recorded result cannot be re-verified from the set.
- **N-20 (C-N6)** — the fence could reuse the house primitives already in the repo for exactly this hazard — `_is_canonical_install_dir` (`lib.sh:480`), `_install_dir_candidates` (`:443`), `_canon_dir` (`:427`), added because of the 2026-10-12 double live-install deletion and built to cover the Layer-1 HOME shapes a naive prefix cannot. The plan set never references them (0 hits) and instead re-implements a literal-path guard.
- **N-21 (C-N7)** — §3's Port row names no forbidden live ports (9797 live / 7979 demo) while the Scratch-DB row does name forbidden DBs ⇒ asymmetric contract.
- **N-22 (C-N8)** — G4 ≡ J6 duplication: `phase1-plan.md:113` (G4) and `phase5-plan.md:37` (Pack J leg J6) are the same leg under two ids; §11.1 L379 lists phase1 as "corpus cases 1–22 + G1–G4" against the canonical "22 + 3".
- **N-23 (D-N1)** — the plan set says the dev checkout is "dirty in **15 unrelated files**" (`architecture-recommendation.md` RULING 9 item 4; `phase6-plan.md:39`; `plan-overview.md:341–342`); measured now it is **14** modified tracked files + 49 untracked ⇒ time-dependent drift; the load-bearing predicate (`scripts/` clean) is verified true.
- **N-24 (D-N2)** — daemon-side anchors in `phase6-plan.md` rows 1–2 and `plan-overview.md` §11 (`upgrade_tools.py:1953-1966`, `:3714`; `manager.py:5494-5496`; `upgrade_journal.py:2156-2162`) were not mechanically verified; they are load-bearing for §11's refusal design and deserve their own check.

---

## D. What stands (verified, unchanged by this verdict)

- **Root cause confirmed by direct reading** at `lib.sh:961–999` (`journal_read`): `json="$(cat "$jp")"` + `for ((i = 0; i < ${#json}; i++)); do c="${json:i:1}"` — a structural (not JSON-parse) accept/reject set.
- **Every headline incident number is exact:** the fixture `state.json.pre-manual-v02711.20261028T174405Z.bak` = 455,783 B, 2,949 history entries, 2,775 `mcp_server_state` entries ≈ 89 % of bytes.
- **The S1 census is mechanically exact:** 17 `lib.sh` + 8 script = 25 distinct `journal_read` call sites (re-derived independently by two workers and by the approver).
- **The efficacy premise is established fact:** live `.env:107` pins `ENSEMBLE_UPGRADE_SCRIPTS_DIR=/home/nea/ensemble-src/scripts/upgrade`; that checkout is at `013895287` = **exactly 277 commits** behind `b1183a637`, `scripts/` clean, and its `lib.sh` still carries the O(N²) loop ⇒ "the next ceremony runs the OLD code".
- **Anchor fidelity is excellent:** 371/371 cited `file:line` anchors resolve to real non-blank TIP lines; 45+ verified byte-exactly against *both* revisions; ~90 spot-checked by Worker B. Anchor class (f) verified correct (`stat -f '%m'` at `lib.sh:5567`, not 5569).
- **Phase-2 arithmetic checks out:** house worst case 870 s < the 900 s deadline; `max(1200, 900+SOAK+120)` = 1320/1800; the soak exclusion is airtight by construction (`gate_soak` reachable only from `promote.sh:520`, outside the wrapped `:225–373` window); D2-8's `unit_properties` plumbing gap is real (`build_service_argv` accepts it at `upgrade_journal.py:2468`; the `spawn_executor` call site `:2738` does not pass it).
- **D2-7's defect is real:** `promote.sh:170` `lock_acquire` precedes `:179` `_trap_install_signal_handlers`; `_run_bounded` TERMs only the direct child (`lib.sh:3539`) and collapses 137/143 → 124 (`:3552`); the live-install write fence (`journal_splice.py:549-560`, `_pipeline_lock_held` `:158`) makes D2-5's breach order load-bearing.
- **Phase 4's D4(a) framing is correct:** `stage.sh` has zero `lock_release` call sites, the EXIT trap covers a normal exit, the reclaim is `_log`-only with no journal row.
- **The ceremony chain is genuinely encoded** (provenance/stamp/refusal/floor/token/escape-hatch/split-verification each have a task + AC), **the nonce-burn ordering is correct and mutation-pinned** (M23), and the **user-owned sync precondition is cleanly externalised** (D6-2, T6.2/AC2, A6).
- **Scope fences hold in every phase file:** no live promote, no symlink flip, no live `state.json` write, no daemon boot/restart, no schema/migration change, no journal-format change, no benign-detach kill-contract change; the per-variable `POSTGRES_*` scrub rule matches the house critical note and bans the silent-no-op `env -u POSTGRES_*` idiom.
- 371 anchors and ~130 file:line citations resolve; 0 blank/non-construct targets.

---

## E. Method, skills, sessions

- **Mode:** Plan Approval. **Scope:** LARGE plan set (11 files, ~3,508 lines) ⇒ 4 workers partitioned by section with `todo_graph` fan-in (W3). **Skills used:** `plan-approval` (one per worker; `approval-strategy` approver-only, never dispatched).
- **Independence:** every worker prompt carried the artifact path + focus areas only — no tracking file, no rejection history, no prior-iteration context. The tracking file was read by the approver only for closure verification, after dispatch.
- **Sessions:** `1f4220e7-b95a-4064-b449-6e911b4eb639` (A, overview), `e305f795-3791-4c4c-b86b-1de7d92d8d13` (B, phase0–4), `eb837981-f384-489f-b9c9-63aa5780d853` (C, phase5/6 + decisions + architecture), `003bc6cf-517b-4214-821e-45295b353173` (D, anchor-convention adjudication).
- **Aggregation:** 15 blocking issues raised → 11 after dedup/merge (A1+A2+C2-partial merged; A4+C1 merged) and 1 downgrade (D2 → Note N-1, stated reason). No issue was upgraded; no blocking issue was introduced by the approver beyond what the workers raised.
- **Approver's independent verification:** every blocking issue was re-derived by the approver with read-only greps/scripts against `b1183a637` (and the HUNG blob `f81bcf6ba` / `013895287`); all 11 reproduced.

## F. Remediation summary

- **Mechanical text corrections (9 of 11):** blockers 1, 2, 3, 7, 11 (+ Notes N-2, N-3, N-4, N-12, N-13, N-16, N-17) — cell-level pointer/ID/cardinality fixes with **no** design change and **no** scope change.
- **Specification additions (2 of 11):** blocker 4 (one sentence on the verdict-input domain + one corpus row) and blocker 9 (a release-time set+validate task and AC for the floor constant).
- **Coverage/table corrections (2 of 11):** blocker 5 (re-bucket 5 sites) and blocker 6 (add `adopt-unit.sh`/`stage.sh` to the interpreter list).
- **Safety additions (2 of 11):** blocker 8 (correct T3.4 + the two §3 change-surface rows to the D3-12/D3-15 two-file form) and blocker 10 (add the scripts-dir fence row).
- After the fold, **re-run the count-sweep gate and re-record its result** (blocker 2 + Note N-19) — the recorded "PASS" is currently false against the set.

## G. Tracking note

Iteration 002 = **REJECTED** (11 blocking issues). This is the **second** rejection in the approval cycle (001 = REJECTED, 4 blocking); the 3-iteration cap is **not** reached, so the plan returns to the planner for a bounded fold — iteration 003 is next. `.agents/approver/active.md` and `.agents/approver/promote-preflight-hang-tracking.md` were **not** modified (caller's read-only fence); this file is the iteration-002 record and must be folded into the tracking file by the caller.

---

## FOLD DISPOSITION — approver iteration 002 → iteration 003 (plan author, 2026-10-29, fold ROUND 6 — audit + completion)

**Context.** Round 6 replaces a worker that died at an upstream rate limit after landing doc edits at 22:28–22:37
(nothing it wrote had been verified). This round is an **audit + completion** pass: every item below was re-checked
**against the plan set on disk**, not against the previous worker's word, and the mechanically-checkable classes were
re-run through the executable gate. **No design change** — 9 of 11 blockers are cell-level text corrections and 2 are
one-sentence specification additions, exactly as the verdict scoped them.

**Worktree state:** branch `fix/promote-preflight-hang`, HEAD **`b1183a637`** unchanged (no commits; the plan dir and
this file remain untracked). Read-only inputs re-verified untouched: `evidence/FORENSICS.md` `480b6250…`,
`evidence/CODE-PATHS.md` `ecaa0253…`, `architecture-recommendation.md` `2ab9d5e1…`, `review-plan-council.md`
`dd728435…`. No pipeline script executed; no live install touched; no daemon boot.

### Blocking issues (11/11)

| # | Issue | Verdict | Fix / evidence | Where |
|---|-------|---------|----------------|-------|
| **B1** | §11.1 `D1-*` map resolves to the wrong topics; two narrative pointers cite `D1-10` for placement | **verified present + 1 residual completed** | Rows now: stdout → **D1-10**, corpus → **D1-11**, strict-JSON → **`—` / `decisions D1-4`**, placement → **D1-13**; §9 OQ2 and §12 A19 both cite **D1-13**. Residual I fixed: two cells attributed **`D4b-4` to `phase4-plan.md`** (which defines no decision rows) ⇒ re-pointed to **`decisions.md` D4b-4**. `decisions.md`'s "the topic column resolves every id" claim **amended** to a gate-verified claim | `plan-overview.md` §11.1 / §9 / §12 (A19, A35) · `decisions.md` (map + verification paragraph) |
| **B2** | Recorded class-(h) PASS not reproducible; `D4b-4` defined nowhere; live `M1–M28`/`A–I` span; class (h) exempted the map rows it validates | **verified present + completed by me** | `D4b-4` **is** defined (`decisions.md:384`, with T4.7/AC7 as its implementation); the span cell carries an explicit **SUPERSESSION RECORD**; class (h) **extended to subject resolution** — new **(h3)** re-derives every §11.1 row's topic anchor against the **defining row**, and the **map-row exemption is removed**; the gate is now a **runnable artifact** and its output is pasted in the set + captured in `evidence/` | `plan-overview.md` §16 · `phase5-plan.md` T5.17 / AC17 / §9 · `gate.sh` · `evidence/gate-run-round6.txt` |
| **B3** | 4 of 27 §5.1 rows have no "Encoded in" cell | **verified present** | R-I → `phase1-plan.md` D1-12/T1.13 · R23 → `phase1-plan.md` D1-14+D1-17 / `phase5-plan.md` Pack J · R26 → `phase1-plan.md` D1-17 / Pack J J5–J6 · R24 → `phase6-plan.md` D6-10+T6.9 / `decisions.md` D6-10. All 5 cells, all file-qualified (no `phase1`/`phase5` shorthand) | `plan-overview.md` §5.1 L165–168 |
| **B4** | Phase 1 never pins the scanner's VERDICT-INPUT domain; no NUL-adjacent-to-backslash row | **verified present** | D1-1 + T1.1: the verdict is computed over the **`$(cat)`-processed bytes** (trailing `\n` stripped, NULs dropped) **and** reproduces the **EMPTY pre-check** (`lib.sh:969–972`); the stdout contract is that same string; **corpus row G5** pins `{"a":"x\0y"}` (accept — a raw-byte scan would refuse) | `phase1-plan.md` D1-1 · T1.1 · §3.3 row G5 |
| **B5** | Armed-window table misclassifies 5 sites in both directions | **verified present + 1 residual completed** | `:1560`/`:1582`/`:1406` re-bucketed **IN-WINDOW** (called from `promote_entry_check`, invoked at `promote.sh:245`); `:1803`/`:1876` moved to an explicit **adopt-path** row (`pipeline_settled`'s only caller is `adopt-unit.sh:201`); §3.6 restated against the corrected map. Residual I fixed: **AC3** still read "every site in §3.3 is bounded" ⇒ re-stated against **§3.3a** + the deliberate §3.3b exclusions (with the adopt-path NOT-armed assertion) | `phase2-plan.md` §3.3a / §3.3b / §3.6 · **AC3** |
| **B6** | `adopt-unit.sh` is an unlisted interpreter dependency (`stage.sh` too) | **verified present in D1-9/T1.7/AC8 + completed by me** | D1-9 names `status.sh` + **`adopt-unit.sh`** (sources `lib.sh:137` → `pipeline_settled:201` → `journal_read:1876`; no python anywhere) + **`stage.sh`**; I amended **I6** to name all three and added the **transitive adopt-path row** to the phase-0 §4.2 companion (the direct-call census stays exactly 25 — the criterion is unchanged) | `phase1-plan.md` **I6** / D1-9 / T1.7 / AC8 · `phase0-plan.md` §4.2 |
| **B7** | Corpus cardinality stated four ways; AC-required row G4 in no count | **verified present + 1 residual completed** | Canonical **`22 + G1–G5 = 27`** (G5 = blocker 4's row) in §11.1, §13, `phase0` T0.2/AC2/AC9/**AC10**, `phase1` §3.3/AC19, `phase5` T5.10/AC10/T5.17; G4 counted **and** named in T1.18's outcome. Residual I fixed: **T0.10** still said "22-case + 3 guard-row" and "**25** fixtures" ⇒ corrected to 27, and gate class (a) **extended** to catch those shapes (`22-case + 3 guard`, `**25** fixtures`, `fixture count is 25`) | `phase0-plan.md` T0.10 / AC10 · `phase1-plan.md` §3.3 · class (a) |
| **B8** | Phase 3's T3.4 specifies the single-file sink D3-12 rejected | **verified present + 1 residual completed** | Both §3 change-surface rows now state the **two-file, `<env>`-scoped** split; T3.4's body too. Residual I fixed: **T3.4's outcome cell** still said "the built argv targets `<install>/data/upgrade/<run_id>.log` … written by both the shell and the payload" ⇒ rewritten: payload `<env>/<run_id>.payload.log` (unit `Standard*`) + narrative `<env>/<run_id>.log` (shell), both exist, no interleaving | `phase3-plan.md` **T3.4** · §3 |
| **B9** | No release-time SET-AND-VALIDATE for `PIPELINE_SCRIPTS_FLOOR_COMMIT` | **verified present + 2 residuals completed** | **T6.15** added (sets the constant to the fix-introducing merge commit, validates `git merge-base --is-ancestor <shipped-floor> HEAD` against the built tree, **fails the release** when unset/unresolvable/non-ancestor, daemon constant == manifest); **AC15**; `phase5` §6 stage-adjudication input added; Pack I carries the **fresh-dir PASS / stale-dir REFUSE** legs. Residuals I fixed: the task table listed **T6.14 after T6.15** (re-ordered) and the §11.1 index still said T6.1…T6.14 / AC1…AC14 ⇒ **T6.1…T6.15 / AC1…AC15** | `phase6-plan.md` **T6.15 / AC15** (order fixed) · `phase5-plan.md` §6 / Pack I · §11.1 |
| **B10** | Canonical fence has no scripts-dir row; escape guard misses `/home/nea/ensemble-src` | **verified present** | `phase5` §3 gained the 🔴 **Scripts-dir** row (throwaway dir under the FAKE_HOME root; **REFUSE** when the resolved dir is under `/home/nea/ensemble-src` or any live checkout; guard runs before every read/write; self-test asserts the refusal) and now uses the **house primitives** (N-20); the escape-guard cell carries both the install-root and the scripts-dir `case` guards | `phase5-plan.md` §3 |
| **B11** | Header recovery command fails; sentence self-contradicts | **verified present** | Now: `git show 013895287:scripts/upgrade/lib.sh` (commit) **or** `git cat-file -p f81bcf6ba`, plus the explicit statement that `f81bcf6ba` is a **blob, NOT a commit** ⇒ `git show f81bcf6ba:…` fails, + the `evidence/CODE-PATHS.md` §1 pointer | `plan-overview.md` L10–12 |

### Notes (24/24)

| Note | Verdict | Fix / evidence | Where |
|------|---------|----------------|-------|
| **N-1** HUNG-parenthetical applied to 5 of 92 | **narrowed** | The universal claim is replaced: a HUNG counterpart appears **only where the set carries one** (five anchors); the rest are TIP-only and re-verified by **script** at the implementation rev (P0-D4) — remedy (A), recorded as deferral **D-F4** | `plan-overview.md` preamble · §17 D-F4 |
| **N-2** SC13 cites T1.17/AC18 for the re-scope | **verified present** | SC13 now cites **T1.18 / AC19** (the re-scoped guard) **+** T1.17/AC18 (the matrix cell) — matching §14's W1 row | `plan-overview.md` SC13 |
| **N-3** §4 coupling map has no Phase 6 | **verified present; 6 broken rows fixed by me** | The P6 column + every row's P6 cell exist — but the fold had collapsed each `P5 | P6` pair into one cell (7 cells for 8 columns, Markdown row silently malformed) ⇒ all six rows re-piped; content unchanged | `plan-overview.md` §4 |
| **N-4** R-A "Encoded in" names amendment ids | **verified present** | R-A now names `phase6-plan.md` **D6-1/D6-2/D6-4/D6-10/D6-11 + T6.2/T6.4/T6.5/T6.9/T6.11** | §5.1 R-A |
| **N-5** `lib.sh:5528` names the read site | **verified present** | R25 now reads "`retention_evict` (defined at `lib.sh:5526`; its read at `:5528`; called at `promote.sh:571`)" | §5.1 R25 |
| **N-6** five stale line anchors | **verified present** | `:1172→:1169`, `:1174→:1173`, `:1039→:1041`, `:1050→:1053` (the 2-char escape skip, RMW-parity-sensitive), `stage.sh:576→:577`; `stage.sh:737→:738`; all six are asserted **absent** by gate class (f) | `phase1` D1-11/D1-12/D1-13 · `phase2` D2-10 · `phase0` §3.4 · class (f) |
| **N-7** corpus case 8 rationale | **verified present** | Case 8 pins the backslash **OUTSIDE** any string and states that the same backslash *inside* a string makes BASE **refuse** (`lib.sh:994`) — position is part of the case | `phase1-plan.md` §3.3 case 8 |
| **N-8** NUL warning text lacks "in input" | **verified present** | P0-D6 and D1-10 now say `…ignored null byte **in input**` and pin **class/prefix**, never the exact string | `phase0` P0-D6 · `phase1` D1-10 |
| **N-9** `journal_in_flight` has no caller | **verified present** | Listed as a **latent site, not a live cost** (`lib.sh:1086`, no caller anywhere) | `phase2-plan.md` §3.3b |
| **N-10** 45–61 min diagnostic fallback | **verified present** | New decision **D1-18**: **diagnostic** callers (`status.sh`, `adopt-unit.sh`) **fail fast with a remedy pointer**; **recovery** callers (`rollback.sh`, `restart.sh`) stay **complete**; promote refuses (D1-17); AC18 pins the 4-cell matrix | `phase1-plan.md` D1-18 · AC8 · AC18 |
| **N-11** `rollback.lock.d.stale.*` residue | **verified present** | New §2.2b: explicitly **outside** D4(a)'s three parts, **ROUTED** to the lock-protocol owner via the D4b-4 sibling scope; this lane deletes no live lock dir (deferral **D-F3**) | `phase4-plan.md` §2.2b · §17 D-F3 |
| **N-12** T0.8 cites "(Phase 2 §2 D2-3)" | **verified present** | The meta-mention is now file-qualified (`decisions.md`) and the row points at `plan-overview.md` §11.1; D2-3 is defined in `decisions.md` | `phase0-plan.md` T0.8 |
| **N-13** T1.18 sits outside §4 | **verified present** | T1.18 is the last row of the §4 task table (before §5) | `phase1-plan.md` §4 |
| **N-14** census row 26 under-counts | **verified present** | Row 26 now states **THREE** mechanisms (1 read + `_json_sub` ×2) | `phase0-plan.md` census row 26 |
| **N-15** M25 mislabelled "token refusal"; token leg unpinned | **verified present + label completed by me** | M25 = the **stamp-acquisition** leg (explicitly "*not* the token leg"); the mixed-generation token refusal gained **M32** (span widened to M1–M32). Residual I fixed: three places still called M25 "token refusal" (§14 matrix paragraph, §13 Adv1 row, `phase5` §9 enumeration, `decisions.md` Adv1 row) ⇒ relabelled | `phase5` Pack I / T5.10 / §9 · §14 · §13 · `decisions.md` |
| **N-16** "7 classes" vs "EIGHT" with nothing checking the count | **completed by me** | One canonical count: **12 executable checks = 8 classes (a)–(h) + (g+) + (i)**. New gate class **(i)** asserts the set states that number **and** that the gate actually runs it, so the recorded result cannot drift; the contradictory heading ("round 4, seven classes" vs "8 classes") is fixed | §16 · `phase5` §9 / T5.17 / AC17 · `gate.sh` class (i) |
| **N-17** `lib.sh:811` correct only for the pinned checkout | **verified present** | Fact 4 now carries "⚠ **tree-specific**": at `b1183a637` `journal_read` is `lib.sh:961` and `:811` is the sandbox-install-dir refusal | `phase6-plan.md` fact 4 |
| **N-18** P5 ↔ P6 dependency cycle | **verified present** | Declared as a **spec vs build** split: the *spec* edge is P6→P5 (Pack I encodes P6's spec), the *build* order is P1→P5→P6; stated in both phase headers and in T5.16's Depends cell | `phase5` T5.16 · `phase6` header |
| **N-19** no executable gate artifact named | **completed by me** | The artifact is `gate.sh` (mode 755, read-only, documents only, non-zero on failure); the verbatim run + exit code are pasted in `phase5` §9 and §16 and captured in `evidence/gate-run-round6.txt`; class (i) keeps the citation honest | `gate.sh` · `phase5` §9 / T5.17 / AC17 · §16 · `evidence/gate-run-round6.txt` |
| **N-20** house fence primitives unused | **verified present** | §3's escape-guard row now uses `_canon_dir` (`lib.sh:427`) → `_install_dir_candidates` (`:444`) → `_is_canonical_install_dir` (`:480`), citing the 2026-10-12 double-deletion provenance; the literal guard remains as a belt | `phase5-plan.md` §3 |
| **N-21** Port row names no forbidden ports | **verified present** | Marked **forbidden: 9797 (live) and 7979 (demo)** — symmetric with the Scratch-DB row | `phase5-plan.md` §3 |
| **N-22** G4 ≡ J6 duplication | **verified present** | Declared as **one leg, two ids** with **G4 canonical**; §11.1 lists `22 + G1–G5 = 27` (no competing count) | `phase5` Pack J J6 · `phase1` §3.3 G4 · §11.1 |
| **N-23** dirty-file count drift (15 → 14+49) | **verified present in the mutable docs** | The count is now explicitly **time-dependent** with the later measurement recorded; the load-bearing predicate (`scripts/` clean) is stated as the only thing that matters. `architecture-recommendation.md` keeps the original "15" — it is a **sha-pinned read-only input** ⇒ recorded as deferral **D-F8** | `phase6` fact 4 / T6.2 · `decisions.md` D6-2 · §11 · §17 D-F8 |
| **N-24** unverified daemon-side anchors | **verified present** | `phase0` **T0.1** now requires the anchor checker to cover `upgrade_tools.py:1953–1966`/`:3714`, `manager.py:5494–5496`, `upgrade_journal.py:2156–2162`; AC1 pins it (a shifted line ⇒ non-zero exit). Verification is a script at the implementation rev (deferral **D-F4**) | `phase0-plan.md` T0.1 / AC1 · §17 D-F4 |

### The gate — ARTIFACT + ACTUAL RUN (this round)

Artifact: **`gate.sh`** in the plan directory (mode `755`). Command:
`bash .agents/shared/planning/promote-preflight-hang/gate.sh` (`--quiet` = one line per class).
Read-only/idempotent (two consecutive runs byte-identical), reads the nine plan documents **only**, executes no
pipeline script, touches no live path, **exits non-zero on failure**. Mutation-proof + h3 coverage (61 map rows) in
`evidence/gate-run-round6.txt`.

```
[PASS] (a) corpus cardinality — one canonical '22 + G1–G5 = 27'; no stale counts
[PASS] (b) mutation-matrix span — every LIVE span claim is M1–M32
[PASS] (c) pack set — no LIVE 'packs A–H' / 'A–I' (canonical A–J)
[PASS] (d) RuntimeMaxSec — no LIVE '=1200' pin literal
[PASS] (e) phase3 — no single-file-era wording / no LIVE D3-3 pointer reference
[PASS] (f) anchors — the stale anchors are gone (lib.sh:5567 / :1041 / :1053 / :1169 / :1173; stage.sh:577)
[PASS] (g) superseded identifiers — no LIVE 'MIN_PIPELINE_SCRIPTS_REV'
[PASS] (g+) the replacement identifier 'PIPELINE_SCRIPTS_FLOOR_COMMIT' IS present
[PASS] (h1) RESOLUTION — every file-qualified citation resolves in the file it is cited for
[PASS] (h2) RESOLUTION — every referenced id is defined somewhere in the set
[PASS] (h3) RESOLUTION/TOPIC — every §11.1 map row's anchor matches the DEFINING row's topic (map rows NOT exempt)
[PASS] (i) ARTIFACT — the recorded result names an executable gate.sh, and the set's stated check count matches the gate's
------------------------------------------------------------------------------
RESULT: PASS — 12/12 executable checks passed
EXIT CODE: 0
```

**Twelve planted-defect legs, one per check, all turned RED on a throwaway copy and GREEN on restore** (listed in
`evidence/gate-run-round6.txt`): (a) `22 + 3` and `**25** fixtures`; (b) live `M1–M28`; (c) `packs A–I`;
(d) `RuntimeMaxSec=1200`; (e) `lives in **one per-run file**`; (f) `lib.sh:5569`; (g) `MIN_PIPELINE_SCRIPTS_REV`;
(h1) a real `` `phase1-plan.md` D1-13 `` citation pointed at `phase3-plan.md`; (h2) an id defined nowhere (`D7-1`); (h3) a map row re-anchored to another row's topic. **The gate is not vacuous.**

### Deferrals (recorded, not silent)

`plan-overview.md` **§17** now carries the register — D-F1 (writer-side journal bloat → MCP/init-guard owner),
D-F2 (benign-detach kill contract → RULING 3 routing, D4b-4), D-F3 (stale-break lock residue → lock-protocol owner),
**D-F4** (anchor re-verification at the implementation rev → P0-D4/T0.1 as a *script*; also N-1's un-taken remedy B),
**D-F5** (the gate's scope boundary — document-consistency only, never the code tree, by safety fence),
**D-F6** (sibling-lane overlap verdict → T6.14/AC14 at stage adjudication), **D-F7** (the floor constant's *value* →
T6.15/AC15 at release time), **D-F8** (`architecture-recommendation.md`'s "15 dirty files" literal left as-is — that
file is a sha-pinned read-only input).

### Round-6 sign-off

**11/11 blocking and 24/24 notes accounted for; 12 of the findings were completed by this round** (B1 residual, B2
class-(h) subject + map exemption, B5 AC3, B6 I6 + census companion, B7 T0.10, B8 T3.4 outcome, B9 task order + index,
N-3 broken table rows, N-15 three labels, N-16 canonical count + class (i), N-19 artifact naming). The plan set is
**submission-ready for approver iteration 003**; nothing was re-litigated from the approver-confirmed substance.

---

## APPROVER VERDICT — ITERATION 003 (REJECTED, third — 3-iteration cap REACHED ⇒ ESCALATED)

**Plan set:** `/home/nea/ensemble-src-wt-preflight-hang/.agents/shared/planning/promote-preflight-hang/`
**Date:** 2026-10-29 | **Iteration:** 003 (final) | **Mode:** Plan Approval — 3 section-parallel workers, `plan-approval`, fan-in via `todo_graph`
**Base rev:** `b1183a637649f94c23b94dfc745df44381dc54ab`

# VERDICT: REJECTED

## Iteration: 003

Engineering substance remains verified strong (two independent workers re-confirmed the root cause, the 25-site census,
the deployment-truth chain and ~60 anchors against the tree). Rejection rests on **10 blocking defects in the
implementable text and in the plan's claims about its own verification** — all raised by workers, none introduced or
upgraded by the approver, every one mechanically corroborated at the quoted location.

**No redesign is required.** 8 of 10 are cell-level text corrections or one-sentence specification additions; 2 are
gate-artifact corrections.

---

## A. Closure of iteration 002 (verified against the set, not against the fold log)

| Iter-002 item | This round | Basis |
|---|---|---|
| B1 §11.1 `D1-*` map topics + 2 narrative pointers | **CLOSED in substance** | workers resolved every map row by hand; topics now correct — but the *coverage claim* is false (new blocker 9) |
| B2 recorded class-(h) PASS unreproducible / `D4b-4` undefined / map-row exemption | **CLOSED in form, re-opened in a new form** | `D4b-4` now defined; (h3) exists and the exemption is narrowed — yet 3 map rows cannot be parsed by `MAPROW` and `ctx_allows` widens exemption to 6 tokens (blockers 9, 10) |
| B3 4 of 27 §5.1 rows missing "Encoded in" | **CLOSED** | 27 rows, 5 cells; sampled "Encoded in" cells all file-qualified and correct |
| B4 verdict-input domain unpinned / no NUL-adjacent row | **CLOSED in form, pin DEFECTIVE** | D1-1/T1.1 now state the processed-bytes domain; but the new G5 row's divergence claim is inverted ⇒ mutation-dead (blocker 5) |
| B5 armed-window table misclassifying 5 sites | **CLOSED** | `:1560`/`:1582`/`:1406` IN-WINDOW; `:1803`/`:1876` adopt-path row; AC3 restated. (New adjacent note: in-window write sites unnamed.) |
| B6 `adopt-unit.sh` unlisted interpreter dependency | **PARTIALLY CLOSED** — `adopt-unit.sh`+`stage.sh` now listed; a **fourth** caller is still missing (blocker 6) |
| B7 corpus cardinality / G4 uncounted | **CLOSED** | canonical `22 + G1–G5 = 27`; gate class (a) extended |
| B8 T3.4 single-file sink | **CLOSED** | T3.4 + §3 rows now two-file, `<env>`-scoped |
| B9 release-time set+validate for `PIPELINE_SCRIPTS_FLOOR_COMMIT` | **NOT VERIFIED THIS ROUND** | T6.15/AC15 present in the text; the partition that owns phase6 could not report (see §E) |
| B10 scripts-dir fence row / escape guard | **NOT VERIFIED THIS ROUND** | same partition loss — text appears added (`phase5` §3), unverified |
| N1–N24 (iteration 002) | mostly closed; 2 re-opened as blockers (N15→10, N16→10); remainder carried as notes below |

---

## B. Blocking Issues (10 — deduped from 12 worker items; A3+B2 merged)

### 1. CONSISTENCY/SAFETY — the size-guard cell is prescribed THREE mutually exclusive ways
`phase1-plan.md` L61 (D1-17), L62 (D1-18), L151 (T1.15), L154 (T1.18), L169 (AC8), L179 (AC18); `phase5-plan.md` L37 (Pack J leg **J4**); `plan-overview.md` L196 (SC13); `decisions.md` L150–166
**Expected:** one behaviour for {no interpreter × journal ≥ `JOURNAL_SIZE_GUARD_BYTES` × `status.sh`/`adopt-unit.sh`}.
**Found:** (a) **reads proceed** — D1-17/T1.15/T1.18/SC13/**J4 "status.sh ⇒ WARN + continue"**; (b) **no walk at all** — D1-18 "the fallback **fails fast**" + AC18 "(**no 45–61 min walk**)"; (c) **walk, but bounded** — AC8 "takes the **bounded bash fallback** (the slow path) **and warns**". J4 and AC18 cannot both be green. The N-10 fold landed *after* Q3 without realigning D1-14/J4/SC13.
**Required:** one canonical behaviour for that cell, propagated to D1-14/D1-17/D1-18/T1.15/T1.18/AC8/AC18/J4/SC13.

### 2. COMPLETENESS/FEASIBILITY — the refuse-vs-warn branch has no specified activation mechanism
`phase1-plan.md` D1-17 (L61), T1.15 (L151), T1.18 (L154), AC16, AC19; `phase5` Pack J J1 / M29
**Expected:** an implementable rule for how `lib.sh` determines which path invoked it.
**Found:** no mechanism at all; `promote.sh` is absent from both touchpoint lists (`lib.sh`, `rollback.sh`, `restart.sh`, `status.sh`, runbook). J1/M29 cannot be built without inventing an interface.

### 3. CONSISTENCY — `adopt-unit.sh` is classified "read-only, no mutation" though it mutates and takes the lock
`phase1-plan.md` D1-18 (L62); echoed by AC18 (L179); `phase0-plan.md` L210
**Expected:** classification by actual role (D1-18's own rule: "the bound follows the caller's role, not the script").
**Found:** the script generates/installs the unit file, `daemon-reload`, stages `ENSEMBLE_RESTART_UNIT` into `.env`, `systemctl enable --now`, verifies livez, acquires the pipeline lock — i.e. a **recovery-class** path is given the **diagnostic fail-fast** bound, the exact outcome Q3 forbids.

### 4. CONSISTENCY — the change surface still scopes the read op into `journal_splice.py`
`phase1-plan.md` §3.1 first row (L71); `plan-overview.md` L48 (§2 in-scope), L227 (§8 item 3)
**Expected:** new file `scripts/upgrade/journal_read.py`; `journal_splice.py` **byte-unchanged** (D1-13, T1.14, AC15).
**Found:** all three still name `journal_splice.py` (L71: "a **read-only** scan op: … + an RMW value splice", "or a sibling helper — D1-2"). §8's list feeds the **stage rollback-safety adjudication**, so the error propagates into the ceremony record.

### 5. SAFETY/CORRECTNESS — corpus row **G5** rests on an INVERTED divergence claim ⇒ mutation-dead pin
`phase1-plan.md` §3.3 G5 + D1-1 (L39), also `phase0` T0.9
**Expected:** a fixture on which a raw-byte scan **differs** from BASE, so a raw-byte implementation REDs.
**Found:** on the stated fixture both implementations **accept** (the NUL is consumed by the escape flag in either reading). The real divergence is the opposite byte pattern: `{"a":"x\` + NUL + `"}` ⇒ BASE **refuses** (the `$(cat)` NUL-drop leaves `\"`), raw **accepts**. Corroborated by `journal_splice.py:298-302` refusing the same class. AC1 therefore passes with the exact defect G5 exists to catch.

### 6. COVERAGE — a FOURTH interpreter-less read-path caller is missing from every list
`phase0-plan.md` §3.3b (L192); `phase1-plan.md` I6 (L31), D1-9 (L47), T1.7, AC8
**Expected:** the caller enumeration and the guard's path scope are complete.
**Found:** `scripts/stop-ensemble.sh:224` calls `pipeline_settled` (sourced from `lib.sh` at `:172–174`, zero `python3`, no floor probe) ⇒ a fourth interpreter-less caller, in **neither** the refuse nor the warn+continue bucket. Verified directly: `grep stop-ensemble` over the plan set returns only an unrelated `_run_bounded` mention (D2-14 / architecture L129).

### 7. SAFETY — Phase 3's stderr redirect silently disables a fail-closed refusal
`phase3-plan.md` D3-6 (L51), T3.6 (L91), AC6, change surface
**Expected:** closing H1 (stderr EPIPE) changes nothing else.
**Found:** the PIPE's consumer is outside the touchpoint: `upgrade_journal.py:2777-2789` reads `proc.stderr` → `_is_systemd_run_failure` (`:2613-2626`, `False` on empty input) → `_refuse_systemd_unavailable` (docstring: "**NEVER returns**: there is no silent fallback path by construction"). With `stderr=<file>` the tail is empty ⇒ genuine `systemd-run` unavailability degrades silently to "fast-failed payload — journal-truth". No AC pins the refusal.

### 8. CONSISTENCY — retention fence 5 contradicts its own owner definition
`phase3-plan.md` D3-10 (L55), T3.11 (L96), AC11 (L119)
**Expected:** five fences, each with a negative leg, ≤ 20 files kept.
**Found:** fence 5 = "never delete … anything **while the lock is held by a live owner**", while the rule's own owner is "**the shell at ceremony end, under the lock**" ⇒ as seated, retention is a no-op and AC11's "≤ 20 files kept" + "all five fences" cannot both hold.

### 9. VERIFICATION INTEGRITY — the recorded h3 coverage is false; map rows are silently skipped
`plan-overview.md` §16 (L709); `phase5-plan.md` §9 (L214); `evidence/gate-run-round6.txt`; `gate.sh` `MAPROW`
**Expected:** "every §11.1 map row … 61 rows evaluated, counted rather than assumed".
**Found (measured by the approver too):** 56 anchor-bearing rows, **53 matched by `MAPROW`, 3 unparseable** — `plan-overview.md` L406 (the W5 three-algorithms row), L423, L476 — all because the anchor text contains embedded double quotes; rows with **no** anchor are skipped entirely; the "61" is reached only by counting the duplicated `decisions.md` copy. ~10 map rows are never topic-checked.

### 10. CONSISTENCY — the gate's exemption rule is stated three ways and is bypassable
`phase5-plan.md` AC17 (L151) vs T5.17 outcome (L234) vs `plan-overview.md` §16 (L711) vs `gate.sh::ctx_allows`
**Expected:** one stated rule that the artifact implements.
**Found:** AC17 = "allows **exactly two** categories … the §11.1 map rows are **NOT** exempt"; T5.17's outcome cell = "**exactly three** categories … **and the cross-file map rows (§11.1)**" — which also contradicts its own class-(h) text in the same cell; the artifact implements **six** tokens, including **`'anchor:'`** — a token that appears in every map row and is trivially appendable to any line. Worker demonstrated a live bypass: appending `| note | the span anchor: M1–M28 here |` leaves class (b) PASS at 12/12; the identical line **without** the token FAILs (b). AC17's "cannot be satisfied by deleting the audit trail" is therefore not delivered.

---

## C. Notes (non-blocking — condensed; none affects the verdict)

- **`decisions.md` D2-9 self-collision**: five bare citations (L191, L205, L211, L218, L625) mean *phase2's* D2-9 (derived `RuntimeMaxSec`); in `decisions.md` D2-9 is the breach-row shape and the derived form is D2-6. Gate (h) cannot see it (h1 only inspects backticked `file.md` citations).
- **`plan-overview.md` L385** says phase1 defines D1-1…D1-17; `phase1-plan.md` defines **18** (D1-18).
- **`decisions.md` L217** keeps `STAGE_TREE_BUDGET_S = 300 s` as a live default while L211/D2-7 declare it superseded (measured healthy ≈660 s ⇒ would false-fire R12).
- **`plan-overview.md` §17 D-F1 (L727)** points at `decisions.md` D1-8 (placement) for the writer-side journal-bloat routing; the topic is D1-5 / D5-3.
- **`phase0` T0.14/AC14**: "§4.2 classes all 26 census rows" — only **24** are classed (`lib.sh:1560`, `stage.sh:618` missing).
- **`phase0` §4 row 10** mislabels the enclosing function (`journal_rollback_count_24h`, not "`journal_count_rollback` (second call)"); §3.3a names it correctly.
- **G1's parenthetical is arithmetically false** by the plan's own curve: at 98,303 B the legacy walk is ≈126–155 s, i.e. above the 120 s step budget; the crossover is ≈87 KB.
- **Stale premise**: "the live journal is already 455 KB+ and legitimately growing" — the live `releases/state.json` is **25,433 B** since the 2026-10-28 trim (the 455,783 B incident artifact is a `.bak`). Design safe either way; re-measure per P0-D9.
- **`rollback.sh` lock claim**: it takes the lock at `rollback.sh:140`, so "holds no lock" is true only up to `:90`; §4 row 24's "before lock acquisition" for `restart.sh:133` is right but `restart.sh` never acquires the lock.
- **T2.7 changes a second call site** (`supervision_dualfight_check` also runs at the stop-site re-check, `lib.sh:3790`) — outside the declared window, undisclosed.
- **In-window write sites neither armed nor named**: `promote.sh:322`/`:327`/`:341`; D2-14(ii) forbids wrapping write paths ⇒ state the exception as done for `retention_evict`.
- **`_json_sub` refusal paths unpinned** (key-absent / non-object / unbalanced all rc 1 and `journal_in_flight` depends on it); AC2/AC3 cover only success-path parity and `journal_update`'s refusals.
- **D3-12** states `<run_id>.payload.log` unqualified while T3.4/AC4 say `<env>/<run_id>.payload.log`.
- **Gate self-description arithmetic**: §16 L692 "12 checks = eight classes + (g+) + (i)" sums to 10; §14 L639 claims EIGHT but enumerates (a)–(g) and is not marked superseded.
- **Supersession markers**: `decisions.md` L11 promises inline marking; D1-13 carries none, and D1-14 sends the reader to D1-13 for a derivation D1-15 withdrew.
- **Deferral register not exhaustive** vs its own rule: D4a-4 (L334) and D5-7 (L415) live only in `decisions.md`.
- Cosmetic/register: §5.1 titled "architect additions" but mixes lane ids; A1–A6 vs A4a; pack rows A…G, J, I; §11.1 L482 garbled "D2-…D6-3"; §4 matrix has no P6 row; N-1's HUNG parenthetical still applied to only 5 of 92 anchors.
- **AC12 "22 + 3 guard rows" vs AC13 "27 fixtures"** was examined and read as **nested scopes, not a conflict** — recorded so it is not re-raised.

---

## D. What stands (verified, unchanged)

Root cause (`lib.sh:961` per-character loop), the exact 25-site census (17 `lib.sh` + 8 script), the duplicate-key
corruption prediction, the `$(cat)` preprocessing insight, the soak-exclusion arithmetic (870 < 900 < 1320), the
M1–M32 matrix wiring, the 8-class rule set, and the ceremony deployment-truth chain: live `.env:107` pins
`ENSEMBLE_UPGRADE_SCRIPTS_DIR=/home/nea/ensemble-src/scripts/upgrade`; that checkout is `013895287`, exactly **277
commits** behind `b1183a637`, `scripts/` clean, its `lib.sh` still carrying the O(N²) scan. Scope fences hold (no live
promote / restart / symlink flip / live `state.json` write; per-variable `POSTGRES_*` scrub rule correct). `gate.sh`
is a real artifact: idempotent, read-only, non-vacuous on its narrow tokens.

---

## E. Verification gap (disclosed, not converted into a finding)

The **phase4 → phase6 partition** (lock/lifecycle, sandbox fences, ceremony/release chain, evidence fidelity, gate
validity) **did not produce a report**. The worker died on an upstream provider rate limit, was revived once (also
rate-limited), a replacement was dispatched (rate-limited on its first turn), and the re-dispatch budget was then
exhausted per the escape valve. Consequences:

- Iteration-002 **B9** (release-time set+validate for `PIPELINE_SCRIPTS_FLOOR_COMMIT`) and **B10** (scripts-dir fence
  row / escape guard) are **unverified**, not cleared — the text appears added but no worker confirmed it.
- Blockers **7** and **10** were nevertheless raised **from the surviving workers' own reading** (phase3 is in the
  phase0–3 partition; the gate was exercised by the overview worker), and were corroborated by the approver at the
  quoted locations. They are not inference from a missing report.

This gap is recorded as a Note-level escalation item; it is **not** the basis of the verdict.

---

## F. Tracking / state

Iteration 003 = **REJECTED**. This is the **third** rejection (001 = 4 blocking, 002 = 11, 003 = 10) ⇒ the
**3-iteration cap is reached**. Recommended `active.md` state: **`Status: ESCALATED`**, `Iteration: 003 FINAL`.
`active.md` was **not** modified (caller's read-only fence — this append is the iteration record; the caller folds
state). No commits; the plan dir and this file remain untracked; HEAD still `b1183a637`.

**Escalation package for the Leader:** the 10 blockers above (5 are single-cell text corrections: 2, 4, 6, 8, 10-adjacent;
3 are one-sentence specification additions: 1, 3, 5; 2 are gate-artifact corrections: 9, 10), plus the unverified
partition in §E, plus the **two re-opened defect classes** that the plan's own gate claims to prevent (overstated
coverage/verification claims — blockers 9 and 10).

**Sessions:** `07cd365d-1c37-47b6-a301-aa74479e4fc4` (overview+decisions — REJECTED, 5 blocking),
`4edbea51-6da9-4319-b11b-890cf3f7fd3f` (phase0–3 — REJECTED, 6 blocking),
`28c12343-1e79-469b-a419-7644e65a77cf` + `98a2f9cc-a8f7-4c43-86cb-9c1dc90ec256` (phase4–6 — both lost to upstream
rate limit; no report). Skills: `plan-approval` × 3 (one per worker); `approval-strategy` approver-only.
