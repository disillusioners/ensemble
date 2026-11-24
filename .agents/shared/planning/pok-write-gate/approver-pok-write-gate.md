# Approver Verdict — POK Write Gate (Wave 1 + Wave 2)

## VERDICT: REJECTED
## Iteration: 001 (of 3)

Date: 2026-11-24 UTC · Approver mode: Plan Approval, fresh-eyes section-parallel dispatch (3 workers, one skill each)
Pinned at: HEAD `a1130e257a3c3bb3929455aec28cacafcbfaa1a7` · worktree `/home/nea/ensemble-src-wt-pok-write-gate` · branch `feature/pok-write-gate` · porcelain = untracked planning dir only (verified identical by all three workers)

## Worker verdicts (input to aggregation)

| Worker (instance) | Scope | Verdict | Blocking | Notes |
|---|---|---|---|---|
| approve-pwg-spine (2a147355-36ed-43c5-9cb4-0fd2210a34a3) | plan-overview.md, design-amendments-v2.md, architecture-recommendation.md, technical-analysis.md | APPROVED | 0 | 8 (2 material) |
| approve-pwg-p0-7 (9d1c351d-3a19-46ea-abf2-e82ea179cda9) | phase0–phase7 | APPROVED | 0 | 6 |
| approve-pwg-p8-13 (9feae149-7a8c-429d-83e1-ba3fe71c625c) | phase8–phase13 + census/overlap briefs + evidence-pok-sweep/ | REJECTED | 2 | 6 |

Aggregation rule: REJECTED if any worker raised a blocking issue. One worker raised two; neither is downgraded (reasons under "Aggregation adjudications").

## Blocking Issues

1. **Retrieval-funnel figures unbacked AND mis-attributed** — `phase12-plan.md:12`
   - Claim: the funnel figures (499→387→160→59 @175 reports) "derive from the pok-sweep mechanics measurement. Sources: evidence-pok-sweep/ (two TSVs + mechanics md, sha256-recorded)".
   - Found: mechanical grep across evidence-pok-sweep/, store-census-2026-11-23.md, store-effectiveness-rag-overlap-brief.md, and technical-analysis.md returns ZERO hits for 387 / "ever injected" / "ever cited" / "175 report" / the funnel chain. The mechanics md has no retrieval/injection/citation section (its §2 "reconciliation" figures 669/114/53 vs 679/121/59 are store total/superseded/archived counts, not a funnel).
   - Load-bearing: `phase10-plan.md:10` (T2) seeds the soft-max utilization basis on exactly this funnel — a Wave-2 design parameter, not just guideline prose.
   - Expected: every measured figure cited to in-branch evidence is actually contained in that evidence, or carries a disclosed canonical-only provenance + re-derivation step (the pattern phase12:12/:18 correctly applies to the admissibility set).
   - Required change: source-or-drop — point at the real source with provenance disclosure + a re-derivation step, or re-derive the funnel from branch-shipped artifacts, or re-base phase10 T2's utilization seed on a backed figure. A timestamp re-stamp does NOT cure this: the cited evidence lacks the numbers entirely.
   - Distinct from the caller's disclosed standing condition: that condition covers per-figure TIMESTAMP stamping at Phase-12 authoring; B1 is a source-EXISTENCE defect (an affirmative in-branch provenance claim that is mechanically false). Not absorbed.

2. **32/64 borderline-tail hand-label agreement unbacked** — `phase11-plan.md:6`
   - Claim: "lexical matching agreed with hand-labels on only 32/64" is the phase's own acceptance criterion for justifying any MECHANICAL pre-filter stage (mirrored at `plan-overview.md:131`, SC#29).
   - Found: grep across the entire planning dir matches only phase11:6 itself and the overview mirror. No named source, no canonical-only disclosure (unlike phase12's pattern), 0 hits in census/mechanics/briefs. The census lexical layer reports 134/499 proxy hits and a 60-row delta — not 64 rows / 32 agreement.
   - Expected: a phase-level acceptance criterion citing a MEASURED rate names a locatable measurement.
   - Required change: source-or-drop — cite the real artifact with provenance discipline, or re-derive from branch-shipped evidence, or strike the number and restate the criterion without it. As written the criterion is unverifiable at build time.

## Aggregation adjudications (judgment band — reasons stated)

- **Spine NOTE-1 (forward reference T1.4/T5.1 → `daemon/services/pok_gate.py`, born Phase 7) — kept NOTE, not blocking.** Reason: the split is disclosed in-cell ("(W2)"); both workers who examined the phenomenon agree the W1 halves carry W1-complete exit criteria and the coupling map registers the cross-wave edges (W1:P5↔W2:P7 tight, W1:P1↔W2:P7 loose); feasibility holds on the single branch (Phase 13 gates on ALL). The repo's plan contract prefers the wording-split shape — apply it in the same fix round as B1/B2 (cheap).
- **Duplicate observations merged:** pok_service.py multi-phase touches (Ph3/5/7 + Ph8/9) — distinct symbols, singly owned by every worker's check; sequencing caution only. record_verification zero-caller observations (spine + p0-7) merged into Note 9.
- **No upgrades, no new blocking issues introduced** by the approver.

## Notes (non-blocking — tracked for the fix round)

1. [material] **Digest budget arithmetic:** 126×48 = 6048 ≠ "≈7250" asserted alongside "per-row ≈126" (`plan-overview.md:122,248`; `design-amendments-v2.md:160`; `architecture-recommendation.md:105`). K=48 < 8000 holds under either figure, but SC#20's threshold as stated would fail a correct implementation — state it as a bound (≤8000) or correct the totals.
2. [material] **Forward-reference wording split** (see adjudication): move creation/consume wording per the plan contract so no W1 leg names a Phase-7-born symbol.
3. **Phase-12 measured-state set not branch-confirmable** (admissibility 448/26/38 of 512 + 82.8% without denominator; kb-writer trio 65-of-81 / 83.5% / 7–18%; boundary split ~87/7/6; 109 vs 112 never-retrieved; binds-vs-never behavior claims): disclosed canonical-only with a T2 re-derivation step — **CONDITION TRACKED** (the caller's standing condition): exact per-figure timestamps + provenance stamped at Phase-12 authoring BEFORE the guideline diff is presented (`phase12-plan.md:24`); verify the step covers every figure in this cluster, including the 82.8% denominator.
4. **Census arithmetic:** `store-census-2026-11-23.md:37` "remaining 53" vs TSV-derived 33; phase9 T6's ≤53 bound stays true but ~50× loose — restate from the TSV.
5. **Row-ID hygiene:** T12.7 cited at `plan-overview.md:322` does not exist (Phase 12 = T12.1–T12.6); T9.7 double-used with two meanings (`:274` task map vs `:338` advisories).
6. **Mutation-leg count:** Phase 6 says 4; Phase 3 T9 carries a 5th (signature-defaults removal RED).
7. **technical-analysis.md** header still "Status: Ready for Review" while superseded where contradicted — stamp a superseded-by line in its own header (design-amendments-v2.md:6-7 + plan-overview.md:7 already govern precedence).
8. **Live application of the two over-cap families** (guard-inv, prompt-assent) has no execution vehicle inside the fenced lane (fix + procedure owned; File cells say scratch fixture) — give the post-lane live application an explicit home.
9. **record_verification seam** (`daemon/repositories/pok/repository.py:384`, zero repo-wide callers today): Phase 5's wiring claim is the first caller — confirm the seam is reserved in W1 and actually called in W2 (this resolves the standing retire-with-disclosure question).
10. **Fence wording:** name the release-directory write that stage.sh performs as sanctioned staging alongside the tag push (`scripts/upgrade/stage.sh:78` verified: staging never touches `current`).
11. `phase8-plan.md:17` cites `:432/:433` without naming pok_service.py in the phase file (overview T8.9 supplies it).
12. **Requirement-1 narrowing** "every pok_upsert call" → "every non-no-op write": disclosed and ruling-traceable (no-op echo `pok_service.py:432` / supersede `:433` verified exact by two workers) — recorded, not a defect.

## What verified clean (context for the fix round)

All seven requirements-frame wires OWNED (spine CHECK 1 table maps each to phases/tasks); cross-doc supersession discipline clean (every older claim carries a visible marker — KNOWN_TOOL_NAMES 271→272, K=32→48, latency defaults, audit sink, taxonomy, rider reversal, reason REQUIRED); declared-vs-actual task counts agree for all 14 phases (132 rows); single-ownership holds on every edited path; fences honored everywhere including stage.sh no-flip; repo anchors content-verified (KNOWN_TOOL_NAMES=269 AST count, 54/55 grant census, .upsert(=97 sites, record_verification zero callers, guideline sha byte-match ce49ddd…/134 lines); phase12's recorded evidence sha256s match the shipped files exactly; the 499/tier/overlap/1024-cap/269-name number set is fully backed.

## Skills Used
adversarial-plan-ledger-verification — the bank's plan-verification execution skill (no literal `plan-approval` exists in this deployment's bank); one skill per worker, cold-context dispatch, plan-review-*.md excluded from worker scope.

## Session IDs
- approve-pwg-spine: 2a147355-36ed-43c5-9cb4-0fd2210a34a3
- approve-pwg-p0-7: 9d1c351d-3a19-46ea-abf2-e82ea179cda9
- approve-pwg-p8-13: 9feae149-7a8c-429d-83e1-ba3fe71c625c

Iteration 001 REJECTED → iteration 002 opens on the revised plan (fix B1+B2; ride the cheap note fixes in the same round). Max 3 iterations.

---
*[Tracking: .agents/approver/pok-write-gate-tracking.md]*
