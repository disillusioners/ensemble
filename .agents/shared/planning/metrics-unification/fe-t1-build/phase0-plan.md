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

---

## Implementation note (2026-11-03) — the placement contradiction, recorded not silently resolved

**The exit criterion's two-sided placement check is UNSATISFIABLE as written, and
it was RECORDED rather than silently redesigned.**

- **The contradiction.** The exit criterion above asks for a §8 section that sits
  **"after §7 and before the evidence index"**, and gives the machine check
  "`grep -n '^## 8\. What changed and why' <file>` returns a line number greater
  than §7's and less than the evidence index's". In this document the **evidence
  index is §5** (line 292), which sits **BEFORE** §6 (line 328) and §7 (line 346)
  — §7 is the **last** section. **No line number can be simultaneously greater
  than §7's and less than §5's**, so the two-sided check can never be satisfied.
- **The implemented placement.** **"After §7, as the document's last `##` section"**
  — i.e. §8 is appended at the end of the document. This satisfies the plan's
  **PRIMARY placement clause ("after §7")** and makes §8 the last `##` section.
- **The "before the evidence index" clause is unsatisfiable as written** and was
  **recorded, not silently resolved**. The clause appears to carry a mistaken
  assumption that the evidence index is the LAST section of the document; in
  fact §5 is the evidence index and §6/§7 follow it. The placement that
  satisfies the PRIMARY clause was implemented and this note is the disclosure.
- **The machine-checkable check that was actually run** (and its output):

  ```
  $ grep -n '^## 8\. What changed and why' .agents/shared/planning/metrics-unification/fe-ui-audit-and-resource-refactor.md
  350:## 8. What changed and why (2026-11-03 — post-audit rulings)
  $ grep -n '^## 7\.' .agents/shared/planning/metrics-unification/fe-ui-audit-and-resource-refactor.md
  346:## 7. Suggested next step
  $ grep -c '^## ' .agents/shared/planning/metrics-unification/fe-ui-audit-and-resource-refactor.md
  9
  ```

  **350 > 346** ⇒ §8 is after §7 ✔ · **§8 is the last `##` section** (9 `##`
  sections, the 9th being §8) ✔ · the evidence index (§5) is at line 292, so the
  "less than the evidence index's" half of the original check is **unsatisfiable
  by construction** — recorded here, not silently dropped.
- **What this commit (A) changed:** exactly ONE file —
  `fe-ui-audit-and-resource-refactor.md` (§8 added; §6's heading/lead pointed at
  §8; §0's "decisions needed" framing line re-worded to "ANSWERED"). §6's three
  questions and every other substantive claim are **verbatim unchanged**. Zero
  changes under `frontend/`, `daemon/`, `agents/`, `scripts/`, `test/`.

### Implementation note (2026-11-03) — commit B: the approver's A1–A6 + the §9 clarification

**Commit B** (`docs(fe-t1-build): fold approver advisories A1-A6 +
operator-recipe clarification (wi-32a99618)`) landed the approver's **six
advisories** and the commission's operator-recipe clarification, **docs-only**
(plan dir only — `plan-overview.md`, `phase0-plan.md`, `phase1-plan.md`,
`phase9-plan.md`, `fold-audit.md`):

- **A1** — §0.2's quoted "verbatim output" JSON block replaced with the
  artifact's `reproduce.output` **byte-identical** (field names only; every BYTE
  figure unchanged).
- **A2** — the live-count fence now **names its exact SELECTs**
  (`SELECT count(*) FROM auth_bearer_tokens;` and the **label-scoped**
  `SELECT count(*) FROM auth_bearer_tokens WHERE label = 'fe-t1-operator';`),
  is **label-scoped** (a concurrent unrelated mint cannot cause a spurious RED),
  and asserts that **the read itself succeeded** — a raised SELECT **FAILS LOUD
  (STOP)**; a failed read is never "0 rows". Applied in **both** places:
  `plan-overview.md` §6.1 Leg L1 and `phase9-plan.md` task 9.3a legs (d) + (f).
- **A3** — the **B4 runbook gap** (`docs/runbooks/oauth2-auth.md` §1b omits
  `ENSEMBLE_AUTH_ISSUER`) is now a **§7 register row (`o`)** with an **owner**
  (the auth-docs owner) and a reason; the register grows **14 → 15** and §10
  criterion 13 becomes **15/15**. `phase9-plan.md` task 9.11 and the Phase-9 exit
  criterion were updated to match.
- **A4** — `fold-audit.md`'s **AM-20** row's stale `phase2 2.13` pointer corrected
  to **2.14** (2.13 is the B3 marker-constant task). Nothing else in that file
  changed.
- **A5** — `phase1-plan.md` task 1.2(c)'s acceptance list's **second "(iv)" leg
  renumbered to "(v)"**. Cosmetic only.
- **A6** — an explicit **SCOPE-NOTE** on `pre_retirement_resources_route_chunk`
  (PRE-RETIREMENT baseline ONLY; EXCLUDED from the Phase-9 gate; must NEVER be
  mis-resolved as the metrics chunk) in `phase9-plan.md` task 9.8 **and** in
  `plan-overview.md` §0.2's G5 row. The artifact's own
  `pre_retirement_resources_chunk_note` agrees; **neither
  `bundle-baseline.json` nor `bundle-baseline.measure.js` was changed.**
- **The operator-recipe clarification** — `plan-overview.md` §9 gains a new
  **§9.0** carrying the runbook's **§1d / §1c / §5** semantics (the one-shot
  bootstrap lane, the integer-seconds `--ttl` + range, the mandatory
  `ENSEMBLE_AUTH_ALLOWED_ORIGINS`, the cold Host-recorder trap, and the
  merge-with-auth-OFF posture), each **re-verified against the tree** with its
  code sites. §9 **remains the canonical operator-recipe source**, and the
  runbook's env-incompleteness (B4) stays a routed documentation item owned by
  the auth-docs owner. §9 step 3's mint command keeps the narrower
  `metrics:read`; §6.1's Leg-L1 mint line was **aligned** to `metrics:read`
  (prose-consistency only — the door's ANY-OF set is unchanged and the
  least-privilege reason stays stated).

**No threshold, rule, ruling, the per-instance top-N routing (register item `m`),
or the AM-14 narrowing was changed. The ONLY count that changed is the §7
register count 14 → 15 (A3, explicitly instructed).**
