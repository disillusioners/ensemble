# Phase 0 — Adjudication + plan gates

**Lane:** `plane-pm-issue-tools` · base `941cb6321` · **Tier:** plan gates (no code, no test legs)
**Depends on:** — · **Blocks:** Phases 1–6 (no code may start before G2 closes)

---

## 1. Objective

Close the leader's **first** gate (lean #1): the architect must adjudicate **Path A vs Path B vs hybrid** on the 5-axis trade-off matrix plus the write-verb reality check, with the flip conditions embedded. Then the plan goes through the house **reviewer/plan council** and a **fresh approver**, and the design is frozen for implementation.

**This phase produces no code.** Its output is a frozen design plus an amended `decisions.md`.

---

## 2. File-level scope

| File | Action | Detail |
|------|--------|--------|
| `.agents/shared/planning/plane-pm-issue-tools/plan-overview.md` | READ | the plan under review |
| `.agents/shared/planning/plane-pm-issue-tools/decisions.md` | **AMEND** | fold the architect's ruling as a new `W-`/`ADJ-` entry + resolve any ADJ the architect closes; **append only** — never renumber §1's leans or §2's `W-` entries |
| `.agents/shared/planning/plane-pm-issue-tools/phaseN-plan.md` | **AMEND** | only if the ruling changes a phase's file-level scope; append, never rewrite |
| `.agents/shared/planning/plane-pm-issue-tools/investigation-digest.md` · `technical-analysis.md` · `tools-list-inventory.md` | READ-ONLY | the three wave-1 artifacts are **never modified** (C8) |

**No repo source file is touched in this phase.** The commit discipline for Phase 0: the plan set is committed **as planning artifacts only** (or left uncommitted per the leader's convention — the leader decides; this lane does not commit on its own).

---

## 3. G0 — Architect adjudication (the FIRST gate after plan delivery)

**Input the architect must adjudicate (all three artifacts are already produced):**

1. **The 5-axis matrix** — `technical-analysis.md` §Comparison. A wins Complexity / Risk / Cost; B wins Scalability (prompt surface) / Maintainability (stable contract). The plan's frozen recommendation is **Path A**.
2. **The write-verb reality check** — `tools-list-inventory.md` §3/§3e: work-item CRUD (8 verbs), comments (5), cycles (10), `create_work_item(is_draft=…)`, **116/177** write-capable, **zero** `*_issue` verbs. ⇒ lean #1's Path-B trigger is **discharged**.
   - **> (G2 fold, approver iter-001, 2026-11-02 — non-blocking item 7: the "116/177" figure above is the WITHDRAWN one.)** It is retained here because this table records **the input the architect adjudicated** (historical record). The figure was **withdrawn by the architect's own adjudication** (W-13 / P1-B) and replaced by the **three-number disclosure**: **104/73** by the inventory's stated method, and **48 read / 82 write / 47 unclassified** by the daemon's actual classifier. The **Path-B discharge is unaffected** — it rests on the **verb coverage** (work-item CRUD + 5 comment + 10 cycle + `is_draft`), not on the write-percentage. See `phase1-plan.md` §3 (P1-B) and `plan-overview.md` §6 (R18).
3. **FC1–FC6** — the flip conditions, embedded in the plan (W-11, ADJ-5).
4. **The D-1 tightening** — the design change the wave-1 analysis forced (W-3). The architect must accept or reject the **lane-local** live predicate (ADJ-2 default: lane-local).
5. **ADJ-1 … ADJ-5** — the defaults are stated in `decisions.md` §3; the architect may close any of them and **must** explicitly accept or override the ADJ-1 default (live-exempt) and the ADJ-5 default (full category, A′ deferred to measurement).

**The architect must also rule on two escalation questions carried forward:**

- **R11 / lean #6** — confirm **no privileged-category change**. If the architect forces one, the **full same-commit pin ceremony** (16 assertion sites / 10 files + 5 count pins + the ari trailing-entry tuple + a new registration pin) becomes a Phase-2 sub-task and the Phase-2 tier plan is re-cut.
- **ADJ-3** — the severity escalation on AD2 (a live credential readable via `GET /api/mcp-servers`). The architect must either accept the out-of-scope default **with the escalation on the record**, or rule it in-lane.

**Adjudication record (to be appended to `decisions.md` §3/§2):** the ruling, the rejected alternatives, the rationale, and the source (who ratified, when) — the house ruling format.

---

## 4. G1 / G2 — Plan council + fresh approver

| Gate | Body | What it checks | Failure handling |
|------|------|-----------------|------------------|
| **G1** | reviewer / plan council | the phase decomposition; the file-level scope completeness; the coupling map; the gate map against `ensure.md`; that every commission hard requirement is traceable in a phase | fold findings into the phase files (append-only) and re-run |
| **G2** | fresh approver | the design as a whole: is it implementable, safe, and complete? | each iteration's blockers are folded; the cap on iterations is the leader's call |

**Known items to pre-empt at G1 (self-identified in this plan):**

- **The ADJ-1 / policy-table reconciliation** — the wave-1 policy table's live row lists a `"workspace 'e2e' is not permitted in the production environment"` reason, which belongs to the **strict** variant, while the ADJ-1 default is **live-exempt**. The implementer must reconcile these two artifacts. Flagged, tracked, not silently decided.
- **R15** — a refusal produces *no Plane tools at all*, which resembles the original bug. Phase 3's typed reasons + Phase 5's assertions are the mitigation; G1 must confirm that is sufficient or require more.
- **Gate (ii)'s authorisation dependency** — the `list_projects` probe needs an operator authorisation that does not exist today (ADJ-4). G1 must confirm the plan's treatment (Phase 1 G4 is a hard blocker with an explicit "if unauthorised, the pin does not ship" branch).

---

## 5. Tier gating

**No regression leg runs in this phase** — there is no diff to gate. The plan-gate chain itself follows the house commission pattern (plan → architect → council → approver → implementation).

**What the reviewer/approver will read first:** `.agents/tester/rules/ensure.md` (Gate Tiers), this phase set, and the three wave-1 artifacts. The Tier-1/Tier-2 **recipes** are loaded from `.agents/tester/skills/` at execution time (Phase 2 onward) — deterministic path, never `skill_search` discovery.

---

## 6. Pin / test obligations

None (no code). **Obligation carried forward:** the Phase-1 gate (i) re-capture is the **drift detector** for the charter vocabulary — so the charter rewrite in Phase 4 depends on the re-capture, not on the current snapshot. This dependency is explicit in the phase map (`plan-overview.md` §4).

---

## 7. Exit criteria

| # | Criterion |
|---|-----------|
| **X0.1** | The architect has issued an explicit A-vs-B-vs-hybrid ruling, recorded in `decisions.md` with rejected alternatives + rationale + source. |
| **X0.2** | FC1–FC6 are either ratified verbatim or amended (with the amendment recorded). |
| **X0.3** | ADJ-1 and ADJ-5 have an explicit accept/override (the two with a shipped-behaviour consequence). ADJ-2, ADJ-3, ADJ-4 are acknowledged with their defaults recorded. |
| **X0.4** | R11 is settled: privileged-category change = no (or the ceremony is scheduled into Phase 2). |
| **X0.5** | Plan council (G1) closed with findings folded. |
| **X0.6** | Fresh approver (G2) APPROVED. |
| **X0.7** | The plan set is internally consistent after folding: every phase's file-level scope still matches the frozen design; the gate map still matches `ensure.md`; the ADJ defaults in `decisions.md` match the policy tables inside the phase files. |

**If G0 rules Path B (or hybrid):** this phase does **not** re-plan. It records the ruling, and the leader commissions the re-plan — Path B changes the shipped surface (new module + factory wiring + a `PlaneHttpClient` contract that does not exist + a second credential class), so the phase set would need a genuine re-cut, not a fold.

---

## 8. CLOSURE — G0 is COMPLETE (folded 2026-11-02)

**G0 outcome: APPROVED-WITH-AMENDMENTS.** The architect adjudicated on 2026-11-02 (record: `architecture-recommendation.md`, **read-only, never modified**). The phase structure was **CONFIRMED** — *"phase structure stands"*, *"Phase structure 0–6 STANDS"* (§0 row 7, §9) — so this phase set is **not** re-planned and **no phase was renumbered**.

**What G0 closed:**

| Exit criterion | Result |
|---|---|
| **X0.1** | ✅ the architect issued the A-vs-B-vs-hybrid ruling: **PATH A CONFIRMED** (matrix re-scored; the Path-B discharge **stands for the surface** with two riders) |
| **X0.2** | ✅ FC1–FC6 **AMENDED** (detectability / owners / thresholds) and **FC7–FC11 ADOPTED** — folded into `plan-overview.md` §10 |
| **X0.3** | ✅ ADJ-1 **live-exempt CONFIRMED**; ADJ-5 **amended to the third option** (full at ship + FC9 ceiling + A′ default-on-breach); ADJ-2 **lane-local** + predicate hardening; ADJ-3 **out-of-scope with the escalation on record**; ADJ-4 **open / user-owned** (the disclosed live-efficacy precondition) |
| **X0.4** | ✅ **R11 settled: no privileged-category change** (§5) — the privileged 16-set is untouched; no pin ceremony |
| **X0.5** | ⏳→✅ **plan council (G1) closed with findings folded — 2026-11-02.** *(G1 fold W-A: the earlier ✅ on this row was a **G0/G1 conflation** — it recorded the architect's 27-amendment fold, which is G0 work, not the reviewer council. The G1 council (2 councilors, plan-review skill) has now run; its findings — B1–B7, W-A/B/C, A-a…A-i — are folded into the phase files and registered at `decisions.md` §8. X0.5 closes on THIS fold; G2 (X0.6) is the next gate.)* |
| **X0.6** | ⏳ **fresh approver (G2) — still pending.** The next gate after this fold. |
| **X0.7** | ✅ internal consistency verified by the fold: every phase's file-level scope matches the frozen design; the gate map matches `ensure.md`; the ADJ defaults in `decisions.md` §3 match the policy tables inside the phase files |

**Fold disposition:** the **27** amendment IDs the architect's §7 enumerates were folded append-only into the phases the architect mapped them to — see `phase1-plan.md` §9, `phase2-plan.md` §7, `phase3-plan.md` §7, `phase4-plan.md` §7, `phase5-plan.md` §10, `phase6-plan.md` §9, plus `decisions.md` §5/§6/§7 and `plan-overview.md` §3/§6/§10/§11/§12. **Amendment-count reconciliation** (architect's stated "30" vs the **27** IDs the document actually enumerates) is recorded in `decisions.md` §7. **G1 reviewer-council folds (2026-11-02): `decisions.md` §8.**

---

## G2 fold (approver iter-001, 2026-11-02) — item disposition in this file

**Source:** `.agents/approver/plane-pm-issue-tools-tracking.md` — iteration 001 **REJECTED** (3 blockers + 9 non-blocking). **Architecture NOT re-opened.** **Mode:** append-only; no phase/gate/pin/exit ID renumbered.

| Item | Disposition | Where |
|------|-------------|-------|
| **NB7** | **FOLDED** — the "116/177" figure in the §3 G0-input table is **annotated as WITHDRAWN** (historical record retained; it records what the architect adjudicated). The replacement is the **three-number disclosure** (104/73 stated-method; 48/82/47 daemon classifier). **The Path-B discharge is unaffected** — it rests on the verb coverage, not the write-percentage. | §3 (the G2 fold NB7 annotation) |
| — | **No blocker applies to Phase 0** — the architect adjudication recorded here stands unchanged; the G0 exit criteria are already met (the fresh approver, G2, is the next gate and is **the very body that produced this fold**). | §8 (the closure block) |
