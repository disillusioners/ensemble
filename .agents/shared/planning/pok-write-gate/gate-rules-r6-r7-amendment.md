# Gate Rules R-RET / R-PROV — Rider Amendment (fold of census R6 / R7)

Date: 2026-11-24T03:17Z
Author: technical-analysis worker — sole author of this rider amendment
Status: RIDER on a CLOSED, approver-reviewed plan set — leader-lane commit + approver re-review pending (this worker does NOT commit, stage, or push)
Base: branch `feature/pok-write-gate`, worktree `/home/nea/ensemble-src-wt-pok-write-gate`, base `a1130e257a3c3bb3929455aec28cacafcbfaa1a7`
Fence: planning docs only; no code, no DB/LLM contact, no live-store write, no commit/stage/push; main-checkout cutlist is READ-ONLY evidence; file writes confined to `.agents/shared/planning/pok-write-gate/`.

This rider folds TWO census-proposed gate rules into the closed plan set. Because the plan set already carries TWO different R-ladders (census gate-rule ladder and plan-lane gate-rule ladder) and a THIRD architect-fork R-ladder (discovered during the sweep, §2.3), the folded rules are given **plan-lane-native mnemonic labels** — `R-RET` (retrievability) and `R-PROV` (provenance-vs-precedence) — and the bare census numbers `R6`/`R7` appear ONLY in provenance/mapping text, never in the operative contract.

---

## 1. Verbatim proposals + full provenance chain

### 1.1 Verbatim proposals (source of truth)

From `/home/nea/ensemble-src/.agents/shared/planning/pok-sweep/cutlist-gap-2026-11-23.md` §7 "Deliverable 6 — gate rule set R0–R5 reconciled against THIS census" (re-read at use time; lines 121–128; main checkout, READ-ONLY):

> **R6 — retrievability test** (slug must share vocabulary with the body's operative terms; catches the 6 mis-slugged + C4 class)

> **R7 — provenance-vs-precedence date test** (a date/sha is admissible as evidence, inadmissible as the claim — the explicit form of the C1 rule, needed because judges keep re-litigating it)

Motivating failure modes, same section, verbatim:

> (4) **retrievability death** — 6 mis-slugged + 41 NOFAM∧ZA rows that can never surface, which NO current R addresses

> (1) dated-provenance vs state-anchor confusion (63-row C1 mass)

Section header framing, verbatim: "gate rule set R0–R5 reconciled against THIS census"; the R-set is stated as "R0 gate-is-a-judge-not-a-filter · R1 durability · R2 decision-relevance · R3 rolling-smell ADVISORY-only · R4 fires only on non-operational (G) · R5 no-home escape."

### 1.2 Provenance chain (full)

| Link | Value |
|---|---|
| Source artifact | `cutlist-gap-2026-11-23.md` §7 (main checkout, `83da04de`, READ-ONLY evidence) |
| Census lane | `7ce77354` |
| Emperor dispositions | board `wi-f0a9418c` / history `0a188127` |
| Relay lane | `pok-disposition-exec-r1` at `2026-11-24T02:25:57Z` |
| Adjudication context | mechanical rolling regex is 6× over-inclusive; "THE GATE MUST BE A JUDGE, NOT A FILTER" |
| This rider | `2026-11-24T03:17Z`, technical-analysis worker, worktree planning dir only |

### 1.3 Measured figures — cited with snapshot times, never bare counts

Two independent measurement passes support the "judge, not filter" finding. They are **different passes with different denominators and are NOT to be added together or conflated**:

- **Cutlist §7 pass** (measured 2026-11-23; store snapshot `~18:57:56Z` per cutlist §9 SQL appendix, 705 rows total): rolling regex flags **92 rows**, only **20–25 true-R** (precision ~27%); it also **misses 5 of 25 true-R** (alt date/sha/tip/branch shapes). A filter would have condemned **63 durable C1 rows**. **25 G rows carry ZERO mechanical tokens** (judge-only by construction). Dominant failure modes: 63-row C1 mass, 25 G + 7 borderline generic-wisdom, 71 DUPSLUG-active, **6 mis-slugged + 41 NOFAM∧ZA** (retrievability death), 32-row no-home set.
- **Semantic 512/512 pass** (POK rolling-smell critical note, semantic pass over 512 active rows): **O=448 / G=26 / R=38**; the lexical rolling-smell regex flagged **106 rows of which only 15 are truly rolling** (6× over-inclusive) and **misses 23 of 38 true rolling rows**. This is the finding the Emperor ruling records; the cutlist §7 pass (92/20–25, misses 5/25) is a second, corroborating pass.
- **Plan-set baseline** (plan-overview §Research Insights, `499 active @2026-11-23 12:29Z` census era; 512 @08:32Z / 562 live @~11:1xZ on 2026-11-24 — three populations, never bare counts): OH 396 / RS 74 / EPISODE 21 / GK 4 / INCIDENT 2 / DUP 2; RAG corpus 13,110 docs @2026-11-24T11:01:01Z (was 12,968, stale +142); overlap = measured LOWER BOUND 10 (kb-writer 6), same-run 9/7/2, exact-hash 0 by construction; the 512-era overlap class counts are WITHDRAWN as live claims (T12.2 fold, `evidence-rederiv-2026-11-24/`, quoted only in the fold ledger); ~87% boundary-PASS.
- **Burden-of-proof figure, FLAGGED UNBACKED**: the plan's SC#29 / the phase11 acceptance criterion cited "lexical matching agreed with hand-labels on only 32/64" as the justification threshold for any mechanical pre-filter (dropped 2026-11-24 — see the dated provenance note below). The approver's iteration-001 **B2** blocking issue records that this figure has no locatable in-branch source and is unverifiable at build time (source-or-drop pending in the leader fix round). This rider therefore does **not** treat 32/64 as established measurement; it is cited only as the reason R-RET's lexical leg is **advisory-only** (§3.2). (figure "32/64" DROPPED 2026-11-24 in the B1/B2 evidence fold — see the dated provenance note in §1.3)
- **Dated provenance note — B1/B2 evidence fold, 2026-11-24 (appended to §1.3; supersedes the burden-of-proof figure above).** The "32/64" figure is **DROPPED as unbacked**: the per-row hand labels for the 64-row semantic borderline band were never persisted (history `2bf04468` + `cutlist-gap-2026-11-23.md:50`), so no per-row agreement against that band is computable. The surfaces it named — **SC#29** (plan-overview.md) and **the phase11 acceptance criterion** (phase11-plan.md) — now carry the **MEASURED** bands from the independent re-derivation (`evidence-rederiv-2026-11-23/funnel-and-agreement.md`, sha256 `b55101a3…8ada`, Figure 2): **Band B 13/16 = 81.2%** (18 named hand-judged borderline rows; R/G axis n=16; the C5 near-dup pair excluded at 0/2) and **Band C 42/50 = 84.0%** (50-row hand-classified tail; 5/25 hand-R lexical misses, 3/25 hand-G lexical false-positives), each with its predicate (`ROLLING:` present ⟺ hand-R, absent ⟺ hand-G). **The R-RET ruling at this site is UNCHANGED and STRENGTHENED:** 81–84% lexical-vs-label agreement still leaves 16–19% error on exactly the contested rows, so no measured rate backs mechanical use of the lexical leg — it stays advisory-only, Wave 2, shadow default OFF.

---

## 2. Ladder reconciliation decision (REJECT-grade: do not conflate)

### 2.1 The ladders as re-verified at use time

| Rule | CENSUS ladder (cutlist §7, lane 2bf04468) | PLAN-LANE gate-rule ladder (design-amendments §1 / KV addenda) |
|---|---|---|
| R0 | gate-is-a-judge-not-a-filter | format (mechanical content fence) |
| R1 | durability | operational ("does the body still teach the same lesson in a repo with no ensemble?") |
| R2 | decision-relevance | rolling ("does the rule survive the date/version it cites?") |
| R3 | rolling-smell ADVISORY-only | no-handle (advisory-only, never rejects) |
| R4 | fires only on non-operational (G) | already-in-RAG (never fires on operational; R1 beats R4) |
| R5 | no-home escape | no-home escape |
| R6 | **retrievability (the folded rule)** | *(does not exist)* |
| R7 | **provenance-vs-precedence (the folded rule)** | *(does not exist)* |

Only R0, R3 and R5 align even loosely in spirit; R1/R2/R4 carry **different meanings** in the two ladders. The folded rules are numbered against the CENSUS ladder.

### 2.2 A THIRD ladder exists in this same directory (sweep discovery)

`technical-analysis.md` "Open Questions" (`:230-235`) and `architecture-recommendation.md` §3 (`:104-107`) use `R1–R6` as **architect-fork decision IDs**, not gate rules: R1 = D1 latency budget, R2 = D5×D7 gate seam, R3 = first audit caller, R4 = digest K, R5 = grant-pin rider, **R6 = stub-mode semantics**. `design-amendments-v2.md:6` ("Supersedes: technical-analysis.md R1–R6 defaults where contradicted") and `:137/:160/:161` use this fork ladder.

**Consequence:** writing bare "R6" into the operative contract would collide with **three** distinct meanings (census retrievability, plan-lane nonexistent, architect-fork stub-mode). This is a stronger misread vector than the two-ladder collision stated in the commission.

### 2.3 Decision — plan-lane-native mnemonic labels, census numbers as provenance only

**Chosen:** the folded rules are named `R-RET` (retrievability) and `R-PROV` (provenance-vs-precedence) everywhere in the operative contract. The census numbers `R6`/`R7` appear **only** in this amendment's provenance/mapping text (§1, §2.1) and in fold-ledger provenance, always written "census R6 → R-RET" / "census R7 → R-PROV".

**Rationale:**
1. **Cannot be misread across ladders.** `R-RET`/`R-PROV` are self-describing at the point of use; a developer opening `phase7-plan.md` sees "R-RET (retrievability)" and needs no mapping to know what it is. Bare `R6` would be read as either census-retrievability or architect-fork stub-mode depending on which artifact the developer opens first — the exact REJECT-grade ambiguity the commission warns against.
2. **No false continuation of the plan ladder.** Calling the folded rules "plan-lane R6/R7" would imply they continue plan-lane R5 (no-home escape); they do not — the plan ladder had no R6/R7.
3. **Audit chain preserved.** The census numbers survive verbatim in the mapping table and fold ledger, so the Emperor disposition (`wi-f0a9418c` / `0a188127`) and relay lane remain traceable.
4. **Chosen over the alternative** ("keep census numbers with an explicit mapping table") because a mapping table only helps a reader who knows to look for it; the plan set has three ladders and a developer builds from whichever artifact opens first.

### 2.4 Where "R0–R5" is the operative contract

The label "R0–R5 parser" names the **rule set the judge applies**, not the parser's token closed set. The parser's closed set is `verdict ∈ {ACCEPT,MERGE,ASK,REJECT}` (authoritative source: `design-amendments-v2.md` §8 "Outcome set", `:78-82`) × `family ∈ {boundary,reason,durability,redundancy,scope}` (`technical-analysis.md:136`, family set only — that line's verdict token is the SUPERSEDED 2-value `accept|reject` contract; see the dated self-supersession banner there) and is **UNCHANGED** by this rider (§3.2 design choice: no new verdict token, no new family, no digest-budget change). Therefore the operative wording becomes **"gate-rule parser (R0–R5 + R-RET/R-PROV; closed-set verdict+family unchanged)"** — the rule set grows by two, the token closed set does not.

---

## 3. Per-rule design

Both rules are **SEMANTIC, Wave 2, shadow default OFF**. The mechanical-now set remains exactly `reason` REQUIRED + R0 named fence (design-amendments §3:41). Neither rule adds a mechanical pre-filter, so neither needs a measured agreement rate to ship — but R-RET's *lexical* leg is nonetheless advisory-only (§3.2) because SC#29's supporting figure is itself unbacked (approver B2).

### 3.1 R-PROV (census R7) — admit-side protective clause

**Nature.** A PROTECTIVE / admit-side criterion, the same shape as the standing `R1-beats-R4` precedent (design-amendments §1:25, §1:28; phase7 T12). It does NOT add a rejection ground; it prevents a false R2-rolling rejection when a date/SHA is provenance/evidence for a durable lesson. It protects the **63-row C1 durable-with-provenance mass** (cutlist §7 pass) from over-eager rolling rejection.

**Judge-question wording** (modeled on R2's "does the rule survive the date/version it cites?" and the R1-beats-R4 precedence phrasing):

> *"Is the date/SHA in this body EVIDENCE FOR an operational claim, or IS the claim itself merely 'as of date X' / 'at commit SHA Y'? A date or SHA admissible as evidence does not make the row rolling — the row survives if the lesson survives the date. This protective clause beats a rolling rejection the way R1 beats R4."*

**Outcome mapping** (refined from the commission's "R7 → admit-side protective clause"):
- Verdict effect: suppresses a would-be `REJECT(durability/rolling)` → `ACCEPT` when the judge finds the date/SHA is evidence for a lesson that survives it; → `ASK` if confidence ≤2/3 (existing confidence→ASK rule, T7.7), never auto-reject.
- No new verdict token, no new family, no new home-routing. The clause is a rubric instruction in the judge prompt, co-located with `R1-beats-R4`.
- Ledger: the verdict row records `rule_applied=R-PROV` in the explanation trace so shadow evidence can measure how often the clause fires (feeds the P1 merge-policy/shadow-mix decisions).

**Enforcement class.** SEMANTIC — Wave 2 — shadow default OFF. Justification: it only matters when the R2 rolling judge is active (also shadow), and it is a rubric precedence rule, not a mechanical check. At enforce it rides the `pok_gate.semantic_enforcement` sibling switch (phase8 T11); it is never a mechanical-now rule.

**Wave/phase placement.**
- Phase 7: amend T7.12 in place — it already owns the `R1-beats-R4` clause and the judge question; co-locating all protective clauses under one task preserves sole ownership of the prompt string's protective section. (A separate task for R-PROV would create duplicate ownership of that prompt string — REJECT-grade.)
- Phase 11: CONTRACT test that a dated-provenance row which R2 would flag is admitted when R-PROV fires (mirror of the R1-beats-R4 CONTRACT test, phase11 T7).

**Exact plan edits implied:** design-amendments §1 table (add R-PROV row + precedence line), §3:42 (semantic set list); phase7 T12 + Exit Criterion; plan-overview T7.12 row + new SC + new Risk + fold-ledger entry. (All applied — §5.)

### 3.2 R-RET (census R6) — retrievability test

**Nature.** A judge criterion that catches mis-slugged rows that can never surface (6 mis-slugged + 41 NOFAM∧ZA rows at the cutlist §7 pass; the C4 class). It is a **detection signal**, not a standalone archive basis.

**Judge-question wording** (modeled on R1/R2 phrasing, with an explicit advisory caveat):

> *"Would someone searching with the body's own operative terms find this row by its slug — does the slug share vocabulary with the terms the body actually teaches, or is it mis-slugged so it can never surface?"*
> Advisory caveat embedded in the rubric: lexical slug↔body token overlap is an **advisory signal only**; the semantic judgment is primary. No mechanical pre-filter may act on overlap alone (SC#29; approver B2 flags its only supporting rate as unbacked).

**Outcome mapping** (refined from the commission's two suggested starting points):
- **NEW writes** — an R-RET failure → **`ASK` with a rename-slug proposal** (remedy in the `alternative` field / optional `proposed_slug` on the typed outcome), NOT auto-reject. Rationale: a mis-slugged but operationally valuable body should be saved by re-slug, not rejected; this matches the "gate maintains the library" doctrine and the MERGE/re-key remedy pattern (Phase 2 T2.3/T2.4). `REJECT`-routable (route → re-slug) is the fallback only when the body is unsalvageable under a ruled basis. In SHADOW: advisory flag only (logged, no pending mint) — consistent with phase8 T8 shadow-ASK advisory-only.
  - **De-scope 2026-11-24:** T2.3/T2.4 moved to the parallel lane (leader dispatch); the re-slug vehicle remains the supersede_and_insert machinery (repository.py:286-311) via the surviving Phase-2 carry-over/coordination rows. (Those rows were REMOVED in the 2026-11-24 de-scope; no live Phase-2 row bears 3 or 4 — the live IDs are T2.COORD + T2.5–T2.8.)
- **Librarian maintenance over the EXISTING 6 mis-slugged + 41 NOFAM∧ZA rows** — R-RET is a **DETECTION signal** fed by per-entry metrics (never-retrieved / no-family ∧ zero-access) into the T10.7 ruled-bases consolidator → (a) if a well-slugged active row already carries the fact → `MERGE` (count-preserving supersede + archive the mis-slugged duplicate under `covered-by-surviving-row`); (b) if unique content but bad slug → re-slug proposal via the librarian pass. It is **NOT a standalone archive basis**; never age, never access_count alone (T10.7, SC#15, SC#17).
- **Parser impact: none.** R-RET reuses existing `verdict=ASK` and the nearest existing `family=boundary` token (a mis-slug is a findability/boundary defect); a dedicated `retrievability` family would require a closed-set expansion + a 6th template + approver re-review and is explicitly DEFERRED as a follow-up, NOT in this rider. No digest-budget change.

**Enforcement class.** SEMANTIC — Wave 2 — shadow default OFF. Its lexical leg is **advisory-only** (no measured agreement rate backs mechanical use; SC#29's 32/64 is flagged unbacked by approver B2). Justification against the mechanical-now set: reason REQUIRED + R0 fence are format/mechanical; R-RET is a semantic findability judgment and must ship shadow like R1/R2/R4/R5. (figure "32/64" DROPPED 2026-11-24 in the B1/B2 evidence fold — see the dated provenance note in §1.3)

**Wave/phase placement.**
- Phase 7: NEW T7.15 — R-RET judge question + advisory-only lexical leg (sole owner of the retrievability criterion and the optional `proposed_slug` outcome field; T7.1 retains the base typed-outcome fields).
- Phase 8: NEW T8.15 — R-RET outcome mapping (ASK-with-rename remedy in shadow; TOOL executes re-slug via the supersede/re-key path, body preserved, count-neutral).
- Phase 10: NEW T10.12 — R-RET librarian detection signal into the ruled-bases consolidator (metrics-fed; never a standalone basis).

**Exact plan edits implied:** design-amendments §1 table (add R-RET row), §3:42 (semantic set list); phase7 T7.3 wording + new T15 + Exit Criterion; phase8 new T15; phase10 new T12; plan-overview scope/phase/task-map rows + new SCs + new Risk + fold-ledger entry. (All applied — §5.)

---

## 4. Co-resident artifact sweep (grep evidence — "a developer builds from whichever artifact opens first")

Whole-dir grep for `R0[–-]R5`, `closed.set`, `R[0-7]` and gate-contract phrases across all `.md` files (evidence TSVs excluded from R-token noise). Every hit was classified EDIT or EXAMINED-NO-EDIT:

| Artifact : line | Hit | Disposition |
|---|---|---|
| plan-overview.md:17 | "R0–R5 parser" (Wave 2 scope) | **EDIT** → "gate-rule parser (R0–R5 + R-RET/R-PROV; closed-set verdict+family unchanged)" |
| plan-overview.md:47 | "R0–R5" (Phase 7 table) | **EDIT** → "R0–R5 + R-RET/R-PROV"; task count 14→15 |
| plan-overview.md:242 | T7.3 "R0–R5 parser (closed-set…)" | **EDIT** → new rule-set label; token closed set unchanged |
| plan-overview.md:251 | T7.12 R1-beats-R4 clause | **EDIT** → add R-PROV protective clause |
| plan-overview.md (task map) | — | **EDIT** → add T7.15 / T8.15 / T10.12 rows; phase counts 14→15, 14→15, 11→12 |
| plan-overview.md (risks/SC/fold ledger) | — | **EDIT** → new risk #15, SC#31/#32, fold-ledger rider row |
| design-amendments-v2.md:12 | "R0–R5" in addendum-2 provenance | **EDIT** → append rider pointer (historical line kept accurate, not falsified) |
| design-amendments-v2.md:15 | §1 header "R0–R5 gate contract" | **EDIT** → "R0–R5 + folded R-RET/R-PROV" |
| design-amendments-v2.md:21-28 | §1 rule table + precedence | **EDIT** → add R-RET/R-PROV rows + precedence note |
| design-amendments-v2.md:42 | "SEMANTIC — SHADOW default OFF: R1/R2/R4/R5" | **EDIT** → "+ R-RET/R-PROV" |
| phase7-plan.md:11 | T3 "R0–R5 parser (closed-set…)" | **EDIT** → new rule-set label |
| phase7-plan.md:20 | T12 R1-beats-R4 clause | **EDIT** → add R-PROV |
| phase7-plan.md:34 | Exit Criterion "parser closed-set" | **EDIT** → add R-RET/R-PROV presence |
| phase8-plan.md (task/exit) | — | **EDIT** → add T15 (R-RET ASK/re-slug) |
| phase10-plan.md (task/exit) | — | **EDIT** → add T12 (R-RET detection) |
| technical-analysis.md:31,:136,:173 | "closed-set" | **CERTIFICATION WITHDRAWN / SUPERSEDED (2-value verdict contract)** — this rider previously certified these lines "still accurate" for the verdict+family token closed set; that certification is FALSE for `:136`, which asserts the SUPERSEDED 2-value `accept|reject` verdict contract. The authoritative 4-value set (ACCEPT/MERGE/ASK/REJECT) is `design-amendments-v2.md` §8 "Outcome set" (`:78-82`); the §2.4 cite is repointed accordingly. The `family` set at `:136` stands; a dated self-supersession banner now marks that line. Explicitly NOT an "R0–R5 closed set" claim. |
| technical-analysis.md:230-235 / architecture-recommendation.md:104-107 / design-amendments-v2.md:6,:137,:160,:161 | architect-fork R1–R6 | **EXAMINED, NO EDIT** — third ladder (architect forks, R6=stub-mode); unrelated to gate rules; the mnemonic labels avoid collision. |
| design-amendments-v2.md:41 | "MECHANICAL — enforce NOW: reason REQUIRED + R0 named fence" | **EXAMINED, NO EDIT** — mechanical-now set is unchanged and must stay exactly this. |
| phase3-plan.md:4/:12/:20/:28, phase6-plan.md:9, plan-review-1.md:61/:92, plan-overview.md:38/:72/:104/:203 | "R0 named fence" (mechanical) | **EXAMINED, NO EDIT** — mechanical R0 fence, unchanged by this rider. |
| phase11-plan.md:6 | "semantic R1/R2 core" + 32/64 | **EXAMINED, NO EDIT** — not a closed-set claim; names the semantic core (still true) and is part of approver B2's fix scope owned by the leader lane. (figure "32/64" DROPPED 2026-11-24 in the B1/B2 evidence fold — see the dated provenance note in §1.3) |
| approver-pok-write-gate.md | no R-ladder / closed-set hit | **EXAMINED, NO EDIT** — verdict record; iteration-001 REJECTED (B1/B2) is leader fix-round scope; this rider introduces no new unbacked count. |

No `## PREDICATE LEDGER` / closing `ATTESTATION` / `DELETION RECORD` pattern exists in this plan set (grep confirmed), so there is no char-for-char predicate table to update; the fold ledger (§5) is the consistency surface used instead.

---

## 5. Exact plan edits applied (this worker, file writes only)

1. **plan-overview.md**: scope bullet (`:17`); Phase 7/8/10 table rows (`:47/:48/:50`) with task-count deltas; Wave 2 task map T7.3 (`:242`), T7.12 (`:251`), new T7.15/T8.15/T10.12; Risk #15; Success Criteria #31/#32; fold-ledger rider row (after `:327`).
2. **design-amendments-v2.md**: `:12` rider pointer; §1 header (`:15`); §1 table R-RET/R-PROV rows + precedence (`:21-28`); §3 semantic set (`:42`).
3. **phase7-plan.md**: T3 (`:11`), T12 (`:20`), new T15, Exit Criterion (`:34`).
4. **phase8-plan.md**: new T15, Exit Criterion.
5. **phase10-plan.md**: new T12, Exit Criterion.
6. **NEW**: this file, `gate-rules-r6-r7-amendment.md`.

Task-count deltas disclosed: Phase 7 14→15, Phase 8 14→15, Phase 10 11→12; three new task-map rows (T7.15, T8.15, T10.12), all SOLE-owned (no existing task duplicates their wiring). The pre-existing 131-vs-132 grand-total discrepancy between plan-review-3 and the approver is NOT touched — this rider reports only its own +3 delta.

---

## 6. Risks / success criteria / fold ledger entries added

- **Risk #15** — R-RET lexical over-rejection (mechanical slug-similarity gate would condemn retrievable-but-unusually-slugged rows) and R-PROV re-litigation (judge re-applies rolling rejection to dated-provenance rows). Mitigation: R-RET lexical leg advisory-only; R-PROV protective clause verbatim in rubric; both shadow-default-OFF; shadow mix ≈ census mix before P1.
- **SC#31** — R-RET emits ASK-with-rename-proposal on NEW writes (never auto-reject); lexical leg advisory-only; librarian use is detection-into-ruled-bases only (no age/access-alone archive).
- **SC#32** — R-PROV admits a dated-provenance row that R2 would flag (R1-beats-R4-shaped CONTRACT test); `rule_applied=R-PROV` traceable in the ledger.
- **Fold ledger rider row** — provenance: relay `pok-disposition-exec-r1`, board `wi-f0a9418c`, history `0a188127`, `2026-11-24`; disposition: FOLDED (semantic, Wave 2, shadow OFF); landed in the files listed in §5.

---

## 7. Scope discipline / non-claims

- No code, no DB/LLM contact, no live-store write; no commit/stage/push (leader lane owns commit + approver re-review).
- Nothing touched outside `.agents/shared/planning/pok-write-gate/` in the worktree; main-checkout cutlist read only.
- No new verdict token, no new family, no digest-budget change, no mechanical-now change — the parser token closed set and all approver pins on those surfaces are untouched.
- No new bare/unbacked count introduced; every census figure carries a snapshot time or pass source (§1.3), and approver B2's unbacked 32/64 is disclosed as such. (figure "32/64" DROPPED 2026-11-24 in the B1/B2 evidence fold — see the dated provenance note in §1.3)

## 8. Open questions for the leader / approver (none block the build)

1. **R-RET family token** — this rider maps R-RET ASK to existing `family=boundary` and defers a dedicated `retrievability` family (would expand the token closed set + add a 6th template + need approver re-review). Approve the deferral, or commission the family expansion in a separate rider?
2. **Re-slug execution vehicle** — R-RET re-slug maps to the Phase 2 supersede/re-key path (T2.3/T2.4) but no dedicated re-slug tool exists. Confirm the supersede/re-key path is the intended executor, or assign a new tool (would need a new sole-owned task in Phase 8/13).
   **De-scope 2026-11-24:** T2.3/T2.4 moved to the parallel lane (leader dispatch); the re-slug vehicle remains the supersede_and_insert machinery (repository.py:286-311) via the surviving Phase-2 carry-over/coordination rows. (Those rows were REMOVED in the 2026-11-24 de-scope; no live Phase-2 row bears 3 or 4 — the live IDs are T2.COORD + T2.5–T2.8.)
3. **Approver iteration state** — the on-disk approver doc shows iteration-001 REJECTED (B1/B2). This rider is written against the CLOSED plan set per the commission; if B1/B2 are still open in the leader fix round, this rider's new SCs should ride that round's re-review. Confirm the current iteration so the leader sequences the re-review correctly.

## 9. References

- `cutlist-gap-2026-11-23.md` §7 (main checkout, READ-ONLY) — verbatim R6/R7 proposals + census failure modes, measured 2026-11-23, store snapshot ~18:57:56Z.
- `design-amendments-v2.md` §1/:15-28 (R0–R5 contract), §3/:39-44 (shadow split) — the operative gate-rule contract this rider extends.
- `phase7-plan.md:11/:20/:34`, `phase8-plan.md`, `phase10-plan.md` — placement targets.
- `plan-overview.md` §Task Map / Risks / Success Criteria / Fold ledger — consistency surfaces updated.
- POK critical note (semantic 512/512 pass: O=448/G=26/R=38; lexical 106 flagged / 15 truly rolling; misses 23/38) — the "judge, not filter" ruling.
- Approver `approver-pok-write-gate.md` iteration-001 B2 — the unbacked 32/64 figure disclosed in §1.3. (figure "32/64" DROPPED 2026-11-24 in the B1/B2 evidence fold — see the dated provenance note in §1.3)
