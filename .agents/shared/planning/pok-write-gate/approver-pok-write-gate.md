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

---

# Iteration 002 — 2026-11-24 — VERDICT: REJECTED

Pinned: HEAD `48abbce676dc76bd05bc560ea88583f78adb6205` · planning dir COMMITTED (29 files, unpushed) · porcelain clean. Dispatch: 3 fresh cold-context workers (same partition + new spine member `gate-rules-r6-r7-amendment.md` + new evidence home `evidence-rederiv-2026-11-23/`), one skill each (adversarial-plan-ledger-verification).

**Delivery-label crossing disclosed:** the completion-report display names for the p8-13 and spine workers arrived swapped; content + instance IDs are unambiguous and attribution below is by instance ID.

| Worker (instance) | Scope | Verdict | Blocking | Notes |
|---|---|---|---|---|
| approve-pwg2-p0-7 (59a5686d-b69c-4032-9aee-273a237543a7) | phase0–7 | APPROVED | 0 | 12 |
| approve-pwg2-p8-13 (a92e62b6-d1e3-4075-a057-75245116c3a6) | phase8–13 + briefs + both evidence dirs + supersession sweep | APPROVED | 0 (one flagged judgment call — ruled admissible, below) | 4 |
| approve-pwg2-spine (7c13bb91-7957-4e4b-adbc-971379400419) | spine 5 docs | REJECTED | 3 | 9 |

## Blocking Issues

1. **Drifted load-bearing anchors on the B1-fold retrieval-leg chain** — `plan-overview.md:158`, `:283` (T10.2), `:337` (fold-ledger row)
   - Claimed: `repository.py:232` (access_count increment) ← `pok_service.py:677` (sole caller); `pok_injection.py:198` (rank key).
   - Found: actual sites are `repository.py:378`, `pok_service.py:733`, `pok_injection.py:199`; the cited lines contain a docstring line, a signature, and an unrelated tuple element. Substance TRUE (one increment, one caller, rank-only read) — but the anchors were introduced by the fold AFTER Risk #13's verified-values list and T0.1's anchor list were written, so the plan's own re-grep preflight will not catch them; an implementer greping `:232` lands on a docstring.
   - Required change: `:232→:378`, `:677→:733`, `:198→:199` at the three plan-overview sites; add the three anchors to Risk #13's list and T0.1's anchor list. (House doctrine: approver rejects string-level drift, not design.)

2. **T3.3 refusal-precedence PIN contradicts the code it claims to preserve, and its cited evidence cannot support it** — `plan-overview.md:206` vs `daemon/tools/pok.py:240/247` and `technical-analysis.md:54-58`
   - Found: three live orderings — code = store→scope (`:243` store check precedes `:247` scope); plan PIN = scope→store→reason (transposed, while claiming "preserving existing codes"); technical-analysis mermaid = reason→scope. The six cited tests each pin a SINGLE refusal code in isolation — no pairwise precedence is pinned by any of them.
   - Why load-bearing: this order decides the enforcement point of mandatory-`reason` (requirement 1) and the T3.6 sweep footprint.
   - Required change: pin ONE canonical string, state its layer (tool vs service), state which of the three orderings it is, add a pairwise-precedence test or stop citing the six as the pin's evidence. House pattern: §PREDICATE LEDGER + closing ATTESTATION (plan-predicate-ledger-01-attestation).

3. **The authoritative rider mis-certifies a co-resident artifact** — `gate-rules-r6-r7-amendment.md:89` and `:165` (fold-ledger-designated authoritative, `plan-overview.md:334`)
   - Found: rider §2.4 cites `technical-analysis.md:136` as the source of the 4-value verdict closed set — that line actually asserts the SUPERSEDED 2-value `accept|reject` contract (4-value set is owned by `design-amendments-v2.md §8` / T7.1). Rider §4's sweep table certifies that stale line "still accurate" — a false certification inside the artifact the fold ledger names authoritative.
   - Required change: rider:89 → cite design-amendments §8 for the verdict set, technical-analysis:136 for the family set only; rider:165 → disposition "SUPERSEDED (2-value verdict contract)"; add the self-supersession banner to technical-analysis.md (also closes NOTE group D below).

All three fixes are mechanical; none requires re-planning (spine worker's own assessment).

## Aggregation adjudications (judgment band — reasons stated)

- **p8-13 flagged judgment call — RULED ADMISSIBLE:** the 9 supersession-sweep occurrences inside `gate-rules-r6-r7-amendment.md` and `funnel-and-agreement.md` are visibly-marked DROP-DECLARATIONS ("DROPPED 2026-11-24", "Not reproducible", "UNBACKED-NO-SOURCE") — history-role content by design, not live relayed claims. The residue gate's intent is "zero live relayed-figure claims"; that holds. No downgrade required (worker verdict was APPROVED). Disclosed as a superset of the leader's expected admissibility list.
- **B-2 cross-worker merge:** p0-7's "six tests match the pinned order" is class-coverage confirmation only (each test pins one code in isolation); the spine's code-opened pairwise analysis is the most specific variant and governs. Merged, not conflicting.
- **No downgrades of worker blockings; no upgrades; no new blocking introduced.**

## Iteration-001 blocker disposition (ledger comparison)

- **iter-001 B1 (funnel mis-attribution): ADDRESSED.** Re-derived funnel verified in-branch (sha `b55101a3…` matches plan-recorded and on-disk; 18 figures confirmed incl. 568→573 drift, 431 query-only, 384 injected lower-bound, 518 cited, 156/1,412); T10.2 re-based with bounds. The fold INTRODUCED the smaller B-1 anchor-drift defect above.
- **iter-001 B2 (32/64 unbacked): ADDRESSED.** Dropped plan-wide; supersession sweep = zero live occurrences; replacement bands B 13/16=81.2% and C 42/50=84.0% located with predicates. Residual: rider:169 sweep-row still lists phase11:6 as carrying 32/64 though the fold replaced it (rider's own §1.3 dated note supersedes) — note only.
- **12-note disposition:** ADDRESSED — record_verification seam (W2 half verified landing in Phase 8 exit criteria), req-1 narrowing (verified disclosed), task-count integrity (two-sided, all 14 phases), phase0 stale-anchor self-documentation. PARTIALLY ADDRESSED / STILL OPEN — digest arithmetic (K=48: 126×48=6048 vs "≈7250"; assert the bound), technical-analysis self-supersession banner, spine wave-qualifier at plan-overview:11/:41. RECURRING — task-map↔phase-file drift (T0.5 unowned in phase0-plan.md; phase11 T4≠T11.4 and T11.10 absent from phase file). CARRIED — see Conditions.

## Conditions tracked (binding on the next revision / build)

1. **Phase-12 authoring is load-bearing:** the kb-writer trio (65-of-81 / 83.5% / 7–18%) has NO in-branch source — T12.2's "re-derived and stamped at authoring, BEFORE the diff is presented" must genuinely execute, or unbacked figures reach the Emperor's draft. (448/26/38 of 512 now present at funnel:130 — improved.)
2. **112-vs-109 never-retrieved:** two zero-populations for one argument — reconcile or stamp both with snapshot times (bare-count fence at plan-overview:361).
3. **Over-cap families live application home** (iter-001 note 8): not re-verified this round — carried.

## Notes (non-blocking, consolidated)

A. Digest arithmetic: assert bound ≤8000 or restate per-row cost (phase7:17, plan-overview:125, arch:105, design-amendments:163).
B. Task-map↔phase-file reconciliation: T0.5 owner row; phase11 positions 4/10; T9.7 double-use residue.
C. T3.6/T3.3 sweep breakdown: 18+13=31≠37 — actual 22 indexed-upsert + 3 query + 7 curate + 5 bare = 37, 23 upsert invocations (order-dependent on B-2's fix).
D. technical-analysis.md unmarked superseded content + "Ready for Review" header — ONE banner closes: 2-outcome gate (:10,:52-71), project-history audit sink (:45,:80,:85,:153,:222), 12s/25s defaults (:220,:230), bare counts (:16).
E. Spine Wave-1 rows :11/:41 lack the wave-split qualifier the phase files carry (phase5:24-25, phase1:26-27).
F. proposed_body vs merged_body_proposal field-name alias (phase7:9 vs :14, arch:35) — pin `proposed_body`.
G. Parser fallback wording: align plan-overview:245 ("garbage→ASK/unavailable") to the twice-supported "garbage→unavailable, never re-labelled pok_gate_reject" (phase7:11, tech-analysis:173, arch:99).
H. SC#1 "missing/empty" reachability: with a no-default `reason: str`, omission raises ValidationError before the body runs — state the tool-level default explicitly or narrow SC#1 to "empty".
I. Grant-pin docstring stale on TWO fields (V10→11, 65→66 categories) — widen T4.3.
J. Bare `R6` fork-meaning (design-amendments:164, arch:106) vs rider §2.3's reserved provenance meaning — disambiguate inline.
K. Ledger-migration apply-time blast radius: add one clause to T1.2 (no backfill, no lock on POK tables, one new table at promote).
L. Phase-6 fixture arity: "two-table create_all" goes stale when the ledger adds a third.
M. Phase-7 intra-phase nits: T2 should depend on T10 (timeout knobs); T14's "Phase 7 boundary" label → name T1.
N. Guideline dual-touch (P5/P12) lacks a shared-seam risk row; "measured-state" section name does not exist in the guideline (§1–§10) — name the target once; vector-retire text has two claimants.
O. pok_service.py (4 phases) + PACKS.md (2 waves) sequencing notes; feeder ≤53 bound ~50× loose vs TSV-derived 33 — restate.
P. Soft shared-surface seams: risk 16 covers refusal-path adjacency only.

## Skills Used
adversarial-plan-ledger-verification (one per worker, cold-context; plan-review-*.md and the iteration-001 approver record excluded from worker evidence).

## Session IDs
- approve-pwg2-p0-7: 59a5686d-b69c-4032-9aee-273a237543a7
- approve-pwg2-p8-13: a92e62b6-d1e3-4075-a057-75245116c3a6
- approve-pwg2-spine: 7c13bb91-7957-4e4b-adbc-971379400419

Iteration 002 REJECTED → iteration 003 (FINAL before escalation) on the revised plan. Fix B-1/B-2/B-3 (all mechanical); ride the note fixes in the same round. Everything else — completeness, feasibility, safety/fences, measured backing, supersession hygiene, single-ownership, task-count integrity — verified clean this round.

---
*[Tracking: .agents/approver/pok-write-gate-tracking.md]*
