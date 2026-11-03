# Phase 0: Housekeeping — the audit-doc what-changed-and-why amendment

## Objective

Make the record honest **before** any code moves. The audit document
(`.agents/shared/planning/metrics-unification/fe-ui-audit-and-resource-refactor.md`)
ends with §6 "Decisions needed from the owner" — three open questions that the
owner has now answered. This phase amends that document so it records **what
changed and why**: the 4-engine proposal as it was written, the measured
reality that rejected it, the D-1 supersession chain, and — kept deliberately
on the record — the **not-chosen zero-dep counter-option**.

**This is the lane's FIRST housekeeping commit.** It touches documentation
only: no `frontend/`, no `daemon/`, no dependency, no test.

## Tasks

| # | Task | Depends On | Acceptance |
|---|---|---|---|
| 0.1 | Add a new section `## 8. What changed and why (2026-11-03 — post-audit rulings)` to `fe-ui-audit-and-resource-refactor.md`, placed **after** §7 and before the evidence index, with a header line stating: the document's §6 decisions were answered by the owner on 2026-11-03 and §8 supersedes §6 | none | The section exists, is unambiguously dated, and names the three rulings it records |
| 0.2 | Record the **supersession chain** as an explicit chain, not a summary: (i) the original 4-engine proposal (ECharts + ApexCharts + Gridster2 + Chart.js, per §3e) → (ii) the two independent measured evaluations (`charting-engine-decision.md` + `charting-engine-decision-independent.md`) that found the ECharts tier EMPTY at our volumes, no ApexCharts surface, and Gridster2 as a different product → (iii) the adoption of Chart.js v4 as the SOLE vendor for the interactive-panel role + ONE consolidated house SVG for minis/fallback → (iv) the owner's D-1 ruling, which (a) fixes the vendor set, (b) **voids** the "drop Chart.js if interaction is deferred" flip-condition because interaction IS in scope, and (c) requires the governance amendment in the SAME commit as the dependency | 0.1 | The chain is legible as a chain; the voided flip-condition is named as void, with the reason |
| 0.3 | Record the **not-chosen zero-dep counter-option** verbatim in substance: ratify zero-npm-deps as a standing FE product position ⇒ the panel role goes to the extended house-SVG renderer at ~+400–700 L (crosshair + nearest-snap is the expensive item), the `metrics.bindings.pins.spec.ts:94` ban and the pack Leg-7 guard stay untouched, and the test-enforced zero-dep posture is preserved | 0.1 | The counter-option is stated with its cost and its preserved benefit, and marked **NOT CHOSEN** with the date — a reader must be able to see it was considered and rejected, not overlooked |
| 0.4 | Record the two **other owner answers** in the same section: D-2 (the central door's fail-closed posture STAYS; the FE gains a distinct actionable `AUTH_NOT_CONFIGURED` empty state; enabling auth is a USER MORNING ITEM with the operator recipe handed over, not executed) and D-3 (retire `/resources` with a SAME-RELEASE param-preserving redirect to `/metrics?tab=resource`; the predefined-tab direction stands; the Gridster2 door stays closed) | 0.1 | Both rulings are recorded with their consequences |
| 0.5 | Amend the document's §6 heading/lead so a reader who arrives at §6 first is not left with three unanswered questions — a one-line pointer to §8 (the rulings are recorded there) | 0.1 | §6 carries the pointer; §6's own text is otherwise unchanged (the record of what was asked stays intact) |
| 0.6 | Amend the document's §0 TL;DR so it does not read as "two decisions needed" — the rulings are answered. Keep every substantive claim; change only the "Decisions needed" framing and point to §8 | 0.1 | §0 no longer presents open questions as open; no factual claim is altered |

## Coupling

- **Tight with:** Phase 1 (the dependency + governance amendment) — §8's
  "same commit" clause is the contract Phase 1 executes.
- **Loose with:** Phase 9 (the evidence trail cites §8).
- **Independent of:** Phases 2–8 (no code moves in this phase).

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Rewriting the audit's substance (it is an evidence document, not a blog) | Medium — the audit's provenance value is destroyed | §0's rule: **add** §8, **amend** the framing lines only, never rewrite a finding. §6's questions stay verbatim. |
| The zero-dep counter-option is recorded so faintly that a future reader thinks it was never considered | Medium — the next lane would re-litigate | 0.3 requires the cost AND the preserved benefit, marked NOT CHOSEN with the date |

## Exit Criterion

- The commit exists with the message `docs: record the post-audit rulings — D-1 charting, D-2 auth, D-3 route fate (wi-32a99618)`.
- `git show --stat` shows **exactly one file changed**:
  `.agents/shared/planning/metrics-unification/fe-ui-audit-and-resource-refactor.md`.
- Zero changes under `frontend/`, `daemon/`, `agents/`, `test/`.
- `agents/.prompt-artifacts.sha256` still verifies **161/161** (this phase touches
  the `agents/` tree not at all — the check is the guard against an accidental
  broad `git add`).
- The document contains the words "NOT CHOSEN" next to the zero-dep
  counter-option, and the three ruling ids D-1/D-2/D-3.
- **The architect review's placement check** (the one item the review recorded
  as missing for phase 0): a machine-checkable assertion that the new §8
  section exists and sits **after §7** and **before the evidence index** —
  `grep -n '^## 8\. What changed and why' <file>` returns a line number
  greater than §7's and less than the evidence index's.
- **E-1 re-derivation (standing lane rule):** this phase gates on **no**
  MEASURED figure, so nothing is re-derived here. (The rule and the
  gated-figures table live in `plan-overview.md` §0.1; every phase's exit
  section carries its own E-1 line.)
