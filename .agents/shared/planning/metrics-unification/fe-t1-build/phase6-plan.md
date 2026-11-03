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
| 6.1 | **Split the resource timeline into per-unit panels** (IMPL-10, AM-12): a **memory panel** (MB — RSS + cgroup on ONE shared scale), a **CPU panel** (%, its own scale), **and a counts panel** (the third unit class — `threads`, `instances_active`, `svg-timeline.component.ts:56-63`). A single mixed-unit axis is **rejected** — it is the defect. **THE PER-SERIES MIN-MAX NORMALIZATION IS ACTUALLY REMOVED (W7) — it was named as the defect but never removed:** the old engine normalized each series independently (`svg-line-chart.component.ts:124 chartYScale` + `:135 chartSeriesDomain`), so a per-series rescale *inside* a single-unit panel would still pass a "one mixed axis" check. The consolidated renderer must plot every series in a panel against **that panel's single shared domain** — **no series may be independently rescaled within a panel**. **The acceptance covers the TOGGLE path (AM-12):** the timeline offers **5** series across **3** unit classes (`TIMELINE_SERIES_DEFS` at `:56-63`; 3 default-enabled at `:50-54`), and enabling a count series must land it on the **counts** panel — never on an MB/% panel | Phase 5, Phase 4 | The correctness spec asserts: 3 panels; each carries a labeled axis with its unit; the memory series share one scale; the CPU series do **not** share the memory scale; **and** toggling `threads` on lands it on the counts panel. **NUMERIC ASSERTION (W7): 0 series are independently rescaled (min-max normalized) within any panel** — every series in a panel is plotted against that panel's single shared domain; a planted "one mixed axis" variant, a planted "count series on the memory panel" variant **and** a planted "one series rescaled inside the memory panel" variant all fail the spec |
| 6.2 | Give **every** rendered chart a labeled value axis with a unit suffix — the Chart.js panels (unit suffix on the axis) and the house SVG panels (a labelled axis where the role permits it). **Per R-2 the mini role has no page surface in T1**, so it renders no axis at all; the completion sentence is therefore "every **panel** carries a unit-labeled y-axis; the mini role renders no axis" | Phase 4 | The spec asserts each panel's axis carries a unit; the mini's exemption is explicitly asserted (and the mini is never rendered on a T1 surface) |
| 6.3 | Define the **unit vocabulary** as an explicit mapping keyed on the **METRIC (series) name**, never on the family and never inferred (IMPL-10, AM-12): families **mix kinds** — F4 registers a gauge *and* a counter (`daemon/services/metrics_query/wiring/w1.py:194-228`), F9 a counter + a 0/1 gauge (`w2.py:120-150`) — so a family-keyed unit is false and a family-keyed panel mixes a LINEAR gauge with a STEPPED counter (counter-honesty broken). The kind comes from the **wire** (`chart_role`, `frontend/src/app/models/metrics.model.ts:164`; resolved per series at `charts-tab.component.ts:222`), never from an FE table. A metric with no known unit renders "unit unknown" — a wrong unit is worse than no unit | Phase 4 | The mapping is a typed table keyed on the metric name with a spec; a spec asserts a family registering both a gauge and a counter yields **two** panels with different units; an unmapped metric renders "unit unknown" (the spec forbids inference) |
| 6.4 | Fix the **attribution card**: when the displayed components do not reconcile with the stated total, say so **in words** ("these figures do not reconcile against the daemon RSS reading — the daemon process is not the whole cgroup"). The FE states the **fact** and never invents a basis | Phase 5 | The spec asserts: when the payload's own fields disagree, the non-reconciliation sentence is present; a planted "0 % next to 71/1,190" variant fails the spec |
| 6.5 | **The coverage figure (AM-13).** The FE **displays the payload's `coverage_pct` verbatim** beside the components and renders the explicit non-reconciliation sentence when the payload's own fields disagree. **Pinned: "the FE computes NO coverage percentage."** The daemon computes `coverage_pct = min(1.0, attributed / anon_bytes)` (`daemon/services/resource_monitor/sampler.py:759`, computed at `:841,:849-851`) and `anon_bytes` comes from `snap.daemon.anon_kb` — it is **NOT** the sum of the displayed components, so "the coverage is computed from the same basis as the components shown next to it" is **unsatisfiable** and the original 6.5 wording is withdrawn | Phase 5, 6.4 | The spec asserts: the rendered coverage is the payload's value (a spec asserts the FE performs **no** arithmetic on it — the FE-side coverage computation is absent from the code); a disagreeing payload produces the explicit "cannot reconcile" state |
| 6.6 | **Do not** add the per-instance top-N list in this lane — routed (m). Instead, the **Live strip's attribution card** must **stop promising** what it does not deliver: the card's label is the honest one (the strip already carries *"attribution is partial by physics"* + an `estimated` tag — `frontend/src/app/pages/resources/live/live-tab.component.html:144-157`), and **the card must not name a per-instance attribution it cannot deliver**. **Re-targeted (W8):** the original target was the **page shell's** subtitle (*"Daemon memory, CPU and per-instance attribution — live snapshot and history"* — `resources.component.html:4-6`), a file **Phase 7 deletes**; this task now targets the card, which **survives** the Phase-5 relocation into `pages/metrics/resource/`. The shell's own per-instance promise dies with the shell in Phase 7 (7.4) | Phase 5 | A copy assertion: the relocated attribution card names the aggregate split and carries the honesty label; **no per-instance promise** survives the relocation; the shell's subtitle is removed with the shell (7.4) |
| 6.7 | **Effective-interval honesty on the panels (AM-18).** The meta line prints the door's `interval_s` (the observed 15 s cadence) while the plotted points at `window_s = 7200` are 30 s apart. The panels' meta must report the **derived** effective spacing (span ÷ point count), or label the door value as the observed cadence | Phase 3, Phase 4 | A spec at the 2 h option asserts the printed interval equals the derived spacing |

## Coupling

- **Tight with:** Phase 4 (the renderers own the axes; the unit suffix is a
  panel capability), Phase 5 (the panels being corrected live here), Phase 3
  (the effective-interval meta).
- **Loose with:** Phase 9 (PX-10, PX-1).
- **Independent of:** Phase 1, Phase 2, Phase 7, Phase 8.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| A unit is **guessed** from a series name and is wrong | High — a wrong unit is a false statement | 6.3: an explicit metric-name mapping; "unit unknown" when unmapped; the spec forbids inference |
| A family-keyed panel mixes a LINEAR gauge with a STEPPED counter | High — counter-honesty broken | 6.3's kind-from-the-wire rule + the two-panels-per-mixed-family spec |
| The split panels change the resource tab's layout so much that the drill-down affordances are lost | Medium | 6.1's acceptance keeps the anomaly markers + drill-down in the panels; the Phase-5 drill-down spec stays green |
| The coverage fix is judged as "the data is wrong, fix the data" | Medium — the data IS odd, but it is the daemon's | 6.4/6.5: the FE states the payload's value and the disagreement; the underlying payload question is routed to the daemon owner as a note (not a code change in this lane) |
| A count series lands on an MB/% panel via the toggle path | Medium | 6.1's toggle-path acceptance (5 series, 3 unit classes) |

## Exit Criterion

- The resource timeline renders **3 per-unit panels** (memory MB, CPU %, counts)
  with labeled axes; no mixed-unit unlabeled axis survives (a planted variant
  fails the spec), and the **toggle path** is covered.
- Every panel's axis carries a unit, or "unit unknown" — never a guess; a
  mixed-kind family yields two panels with different units.
- The attribution card states the payload's `coverage_pct` **verbatim** and
  renders the non-reconciliation sentence when the payload's own fields
  disagree; **the FE computes no coverage percentage** (asserted).
- The panels' meta reports the **derived** effective spacing.
- The Live strip's attribution subtitle no longer promises per-instance
  attribution.
- The correctness spec is mutation-proven: each of the **five** substantive
  fixes (the per-unit panel split incl. the toggle path, the unit vocabulary
  keyed on the metric name, the attribution basis, the coverage displayed
  verbatim, the derived-spacing meta) has a planted-violation leg that goes
  red → restored → green (A-9).
- **E-1 re-derivation:** this phase gates on **no** MEASURED artifact figure.
  Its inputs are **code facts** (the wire's `chart_role`, the sampler's
  `coverage_pct` formula, the timeline's series defs) re-verified at lane base
  and re-read at this phase's freeze.
