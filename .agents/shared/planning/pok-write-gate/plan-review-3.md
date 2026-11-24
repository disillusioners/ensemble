# Plan Review 3 — Final Verification Pass (cycle 3, the cap)

**Verdict: CONDITIONS — build may proceed to the approver once COND-1/COND-2 land (two mechanical one-liners, both in `plan-overview.md`); COND-3 is fold-if-times-exist, else DISCLOSED to the approver as a standing condition. Nothing in this pass requires redesign or a re-cycle.**

- **Date:** 2026-11-23
- **Mode:** Standard delta verification — worker `review-worker-pokgate-delta` (`bd468d92-60b3-42d6-8d4e-d82933341cca`, revived), skill `post-fold-residue-sweep`
- **Baselines:** `plan-review-1.md`, `plan-review-2.md`
- **Aggregate: 0 CRITICAL · 2 WARNING · 7 ADVISORY · items 1,2,3,4,5,8 VERIFIED · item 6 DEFECT (tracking arithmetic) · item 7 = 5/6 sub-items VERIFIED, (b) DEFECT**

---

## Item verdicts (the 8 verification asks)

| # | Ask | Verdict |
|---|---|---|
| 1 | C1 closure TOTAL; sweep = exactly 1 hit | **VERIFIED substantively** — 0 prescriptive sites anywhere. Scope note: whole-set count is **2 allowed defect-quoting sites** (`design-amendments-v2.md:169` + byte-frozen `architecture-recommendation.md:116`), not 1 — the planner's sweep excluded the arch doc. Disclosure only (A-1). |
| 2 | C2 exact 37 (18+13) / 9 (6) + RED warning | **VERIFIED** — `phase3-plan.md:14`; totals re-derived (37/9/97 ✓); bonus: `:12` anchor off-by-one fixed (`pok_service.py:395`). **[CORRECTION 2026-11-24, approver-002 item C: the 18+13 breakdown is WITHDRAWN — content-measured breakdown is 22 indexed-upsert + 3 query + 7 curate + 5 bare-loop = 37; the total 37 stands]** |
| 3 | 131 × six cells; map == phase files == phase-table; 10 extra rows = advisories table | **VERIFIED** — three-way **131 = 131 = 131**; arithmetic reconciled (Phase-12 merged `Rewrite §2`+`Rewrite §4(i)` → one row, 132→131; map +3 chained rows, 128→131); 10 extra rows confirmed = advisories table. Nits: A-2 (planner's "5-field" descriptor wrong — 0 five-field rows exist; advisories rows are 3-field), A-3 (no named merge record), A-4 (Phase-12 ID map not 1:1). |
| 4 | C4 "4 mutation legs RED" | **VERIFIED** — `phase6-plan.md:28`, matches task table `:10-13` |
| 5 | C5 F2 wording at all three MERGE sites | **VERIFIED (3/3)** — `plan-overview.md:255`, `phase8-plan.md:4`, `design-amendments-v2.md:82`; zero unqualified variants survive in any spelling. A-5: `phase8-plan.md:10` (body task row) omits the `NO write (MERGE no-delta)` clause the other two carry. |
| 6 | C6: fold ledger / advisories table / arch doc untouched | **DEFECT (advisory-tracking arithmetic)** — ledger ✓ (`:317-327`, A1/W1–W13/Q1/Q2/C1–C6 all named); arch doc ✓ byte-untouched (sha256 `0d1ff4b2163393fb…39c8`, 27,394 B, mtime 17:55); advisory 11 legitimately closed with evidence (§13.2a `:151-155`); **advisories table = the defect** → W-1. |
| 7 | Evidence addendum (a)–(f) | **(a) ✓** SC#29/SC#30 with methods (`:131-132`, 30 contiguous); **(b) ✗** → W-2; **(c) ✓ ledger / A-6 body**; **(d) ✓** exactly 5 PRE-FIX/POST-FIX sites (A-7: 6th mention marked in substance, not token); **(e) ✓** byte-exact evidence artifacts — full sha256 recomputed: `b1f5842d60031cc165a5045bf3817101cb745c840b8d5ad2247ea287673c9b59` (census TSV, 499 lines) / `6737281cfc7eddfb2f82a1af3317c1560ca465225162b503292a2ddd5d075ab1` (mech-labels, 500) / `b819401e4b5a0017423d0144143ba6c3effeef3761eea4013820b82c0d4eaf52` (mechanics, 574) — but these back the CENSUS numbers, not the admissibility/funnel figures; **(f) ✓** kb-writer SETTLED, no stale narrowing/await framings (`:149`, `:164`, delta `:67`; targeted greps clean) |
| 8 | Nothing else moved | **VERIFIED** — 0 TODO/FIXME/TBD/XXX; `git status` = 25 untracked files all under the planning dir; `git diff --stat` empty; HEAD unchanged (`a1130e257`); mtime blast radius = intended files only (20:52–20:58); arch doc + phase0/7/9/10 + baselines untouched |

---

## CONDITIONS (the cap's contract: mechanical one-liners only; larger → disclosed standing)

### COND-1 🟡 (one-liner, `plan-overview.md:344`)
The advisories row `| 11 | Phase-13 staging-env-only + Emperor-ceremony promote gate … | PARTIAL |` is **advisory 2's text under advisory 11's number**, and advisory 2 is actually **satisfied** (`phase13-plan.md:4` `NO promote`, `:13` Emperor-ceremony ownership, `:20` carve-out sentence) — the "carve-out sentence" it credits is advisory 13's subject, already adopted (Q2, round 2). **Fix:** relabel `11` → `2` and mark CLOSED (or drop the row).

### COND-2 🟡 (one-liner, `plan-overview.md:335-344`)
**Advisory 16 (`quick`-latency re-measure owner + date) was silently dropped** from the tracking table — not closed: `plan-overview.md:170` and `design-amendments-v2.md:211` still carry the 2026-09-07 probe with no owner and no re-measure date; the owner lives at `:89` (Risk#6) and the date at `phase7-plan.md:29`, split across sites. Net: lead-in `:331` says "11 remain open" over 10 rows. **Fix:** re-insert `| 16 | quick-latency re-measure needs owner + date (partial: probe date cited at :170 / phase7-plan.md:29; owner and re-measure date unassigned) | PARTIAL |` — table becomes 11 rows and the header becomes true.

### COND-3 🟡 (fold-or-disclose, `phase12-plan.md:12,:13,:14`)
The three new measured-state bullets (admissibility 87.5% / 82.8% agreement; retrieval funnel 499→387→160→59; budget shape) carry **no snapshot time** — against the section's own header (`:6` "cite snapshot times, NEVER bare counts") and the fence (`plan-overview.md:350`) — and are **unsourced in the branch**: 0 hits for every figure across all three evidence artifacts, store-census, store-effectiveness, and technical-analysis. The 512-row admissibility set is also unreconciled against the 499 @13:5xZ census snapshot (acknowledged only in a Risks row, `:34`).
**If the leader can supply the measurement times in one line:** append `@<time>` + source per bullet. **If not:** DISCLOSE to the approver as a standing condition — "the guideline measured-state will carry three measured figures without snapshot times or in-branch provenance" — and do NOT re-cycle the plan at this cap.

### Disclosed, non-blocking (ADVISORY — fold at convenience or carry as notes)
- **A-1** Fold-report precision: state C1 sweep result as "2 defect-quoting sites (delta doc + byte-frozen arch doc); 0 prescriptive."
- **A-2** Planner's row recipe wrong: `plan-overview.md` has 0 five-field rows (field census: 2×4, 3×18, 4×39, 6×164, 10×10); advisories rows are 3-field, 10-field rows are the Coupling Map.
- **A-3** No named merge record for Phase-12 `Rewrite §2`+`Rewrite §4(i)` → one task (132→131); add to fold ledger `:326`.
- **A-4** Phase-12 map IDs not 1:1 with phase-file task numbers (`T12.3` spans tasks 3+5; `T12.5`+`T12.6` split task 6); note the mapping in the ledger.
- **A-5** `phase8-plan.md:10` — insert `NO write (MERGE no-delta); ` after `supersede (MERGE delta, count-preserving);` (body-wins gap vs the other two F2 sites).
- **A-6** `phase10-plan.md:10` — append the funnel triplet `499→387 (77%)→160 (32%)→59 (11.8%)` (ledger `:278` has it; body doesn't).
- **A-7** `plan-overview.md:153` — prefix the 6th family-fix mention with the PRE-FIX/POST-FIX tokens for sweep consistency.

---

## Confirmed clean (standing context for the approver)

- **C1 closure is substantively TOTAL:** 0 prescriptive content-merge sites in the whole set; 2 allowed defect-quoting sites; 1 marked-history site; 8 post-fold/negated sites; baselines carry their own history.
- **Ownership chain:** three-way 131 = 131 = 131 with six cells on 131/131; the three previously-unchained tasks now carry File cell + leg row + PACKS cell (`plan-overview.md:229,:286,:287`); no stale `T12.*` x-ref survives.
- **Fences:** all prior scratch fences retained; arch doc byte-frozen (full sha above); nothing outside the planning dir + evidence subdir moved; no commits, HEAD unchanged.
- **Structural sweep criteria adopted:** the plan now carries BOTH criteria recommended in plan-review-2 (`:346` — both-variant folded-wording grep + two-sided task counts).
- **Standing, non-blocking:** 9 genuinely-open advisories remain fold-at-convenience ({3,4,6,9,15,17,18} OPEN + 16, 1, 8 PARTIAL — per COND-1/COND-2's corrected table).

## Gaps

None — single worker, completed, evidence re-derived (hashes recomputed byte-exactly, counts machine-derived, git read-only).

## Process notes

- `skill_feedback` first-action attestation absent from the worker's final message for the **third consecutive round** (council round + delta rounds 2/3). Reported as an anomaly this round (once). Reports otherwise verifiable; non-blocking.
