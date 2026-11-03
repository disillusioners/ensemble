# Phase 6: Chart correctness — units, shared scale, attribution math

## Objective

Close the three arithmetic/label defects the audit caught in the pixels:

1. **No y-axis at all**, and three series with two units (MB and %) on one
   unlabeled axis, each independently min-max normalized — so the area fills
   visually read as a stacked-area chart (a 105 % CPU peak draws no higher than
   an RSS plateau). Nonsense across units.
2. **Attribution arithmetic that does not cohere** (71 MB attributed + 1,119 MB
   unattributed ⇒ 1,190 MB implied total; daemon RSS 633 MB; cgroup current
   6,178 MB; coverage 0 % — which is incompatible with 71/1,190 ≈ 6 % under any
   reading).
3. **A coverage figure inconsistent with its own numbers.**

These are **false statements**, not styling nits. The fix is a split, a basis,
and a stated non-reconciliation.

## Tasks

| # | Task | Depends On | Acceptance |
|---|---|---|---|
| 6.1 | **Split the resource timeline into per-unit panels** (IMPL-10): a **memory panel** (MB — RSS + cgroup on ONE shared scale) and a **CPU panel** (%, its own scale). A single mixed-unit axis is **rejected** — it is the defect | Phase 5, Phase 4 | The correctness spec asserts: 2 panels; each carries a labeled axis with its unit; the memory series share one scale; the CPU series do **not** share the memory scale. A planted "one mixed axis" variant fails the spec |
| 6.2 | Give **every** rendered chart a labeled value axis with a unit suffix — the Chart.js panels (unit suffix on the axis, from the family's unit vocabulary) and the house SVG panels (a labelled axis where the role permits it — the mini role does not, by the ADJ-ii scope guard) | Phase 4 | The spec asserts each panel's axis carries a unit; the mini role is explicitly exempt and the exemption is asserted |
| 6.3 | Define the **unit vocabulary** as an explicit mapping (family → unit), not an inference from the series name. The door's families carry `labels` and `role`, not units; a wrong unit is worse than no unit — an unlabelled axis with a stated "unit unknown" is honest, a mislabelled one is not | Phase 4 | The mapping is a typed table with a spec; a family with no known unit renders "unit unknown" (never a guess) |
| 6.4 | Fix the **attribution card**: state the basis the coverage figure is computed on, and when the displayed components do not reconcile with the stated total, say so **in words** ("these figures do not reconcile against the daemon RSS reading — the daemon process is not the whole cgroup"). The FE **cannot invent a basis** — the payload's fields are what they are | Phase 5 | The spec asserts: the basis string is rendered; when the numbers do not reconcile, the non-reconciliation sentence is present; a planted "0 % next to 71/1,190" variant fails the spec |
| 6.5 | Make the **coverage figure consistent with its own numbers** — the coverage is computed from the same basis as the components shown next to it; if the API's `coverage_pct` and the displayed components disagree, the FE renders the disagreement rather than the lone percentage | Phase 5, 6.4 | The spec asserts the coverage renders with the components it is derived from, and that a disagreeing payload produces an explicit "cannot reconcile" state instead of a bare percentage |
| 6.6 | **Do not** add the per-instance top-N list in this lane — routed (m). Instead, the Live strip's attribution card must **stop promising** what it does not deliver: the subtitle "per-instance attribution" is replaced by the truth (the attribution figures are the **aggregate** split) | Phase 5 | A copy assertion: the attribution card's subtitle names the aggregate split; the per-instance promise is gone |

## Coupling

- **Tight with:** Phase 4 (the renderers own the axes), Phase 5 (the panels
  being corrected live here).
- **Loose with:** Phase 9 (PX-10, PX-1).
- **Independent of:** Phase 1, Phase 2, Phase 3, Phase 7, Phase 8.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| A unit is **guessed** from a series name and is wrong | High — a wrong unit is a false statement | 6.3: an explicit mapping; "unit unknown" when unmapped; the spec forbids inference |
| The split panels change the resource tab's layout so much that the drill-down affordances are lost | Medium | 6.1's acceptance keeps the anomaly markers + drill-down in the panels; the Phase-5 drill-down spec stays green |
| The attribution fix is judged as "the data is wrong, fix the data" | Medium — the data IS odd, but it is the daemon's | 6.4: the FE states the basis and the non-reconciliation; the underlying payload question is routed to the daemon owner as a note (not a code change in this lane) |

## Exit Criterion

- The resource timeline renders **2 per-unit panels** with labeled axes; no
  mixed-unit unlabeled axis survives (a planted variant fails the spec).
- Every panel's axis carries a unit, or "unit unknown" — never a guess.
- The attribution card states its basis; the coverage figure is consistent with
  the components shown beside it, or the card says it cannot reconcile.
- The Live strip's attribution subtitle no longer promises per-instance
  attribution.
- The correctness spec is mutation-proven: each of the four fixes has a
  planted-violation leg that goes red → restored → green.
