# Phase 4: Renderer consolidation — house SVG + Chart.js panel wrapper + the intent registry

## Objective

Collapse the two hand-rolled engines into **ONE** house SVG renderer (the
mini + fallback role), add the **Chart.js** interactive-panel wrapper, and put
both behind the **intent → renderer registry** so adding an engine later is
one file + one registry line and the manifest never changes.

The house rule this phase exists to honour: *"ONE engine; the feature-frozen
list is a spec, not a default"* — and to stop the house's own observed drift
(the newer engine was strictly **less** capable than the older one it
duplicated).

**This phase also carries the SECOND bundle guard** (AM-2) — the engine is
where a chunk-graph mistake is most likely, so the guard is co-located here.

## Tasks

| # | Task | Depends On | Acceptance |
|---|---|---|---|
| 4.1 | Create `frontend/src/app/components/charts/house-svg-renderer/` — the consolidated house primitive. It **ports** (never rewrites) the exported symbols of BOTH engines. **The inventory is enumerated by name (AM-26) — 12 exported functions from the newer engine alone:** `chartXScale`, `chartYScale`, `chartSeriesDomain`, `chartCoords`, `buildChartLinePath`, **`buildChartStepPath`** (the geometric half of counter-honesty, spec-pinned at `svg-line-chart.component.spec.ts:139-171`), `buildChartPath`, `buildChartAreaPath`, `chartUtcTicks`, `formatChartUtc`, `formatChartValue`, `chartPointTooltip` (`svg-line-chart.component.ts:108-271`) — plus the older engine's `timelineXScale`, `timelineYScale`, `buildLinePath`, `buildAreaPath`, `anomalyDrilldownWindow`, `formatUtcClock` (`svg-timeline.component.ts:95-167`) and the geometry constants of both. The helpers stay **exported and unit-asserted** — that property is why the audit could cite them precisely. **A per-assertion disposition table is required (AM-26):** every assertion of the two existing specs (241 L + 237 L) is classified **port / re-point / drop-with-reason** against the consolidated component's API — the two engines' component APIs differ, so component-level assertions (8 timeline assertions + the path/coord/tick/value-label assertions) are **REWRITTEN** specs, not relocated ones | Phase 1 | The disposition table covers **every** assertion of both specs (the count is derived from the files, not declared); the ported helper specs are green; no assertion is unaccounted for |
| 4.2 | Keep the **older engine's capabilities** in the consolidated renderer: the window ladder, series toggles, anomaly markers (with the severity colours **verbatim** from `svg-timeline.component.ts:169-173`), click → drill-down, the UTC clock formatter, and the `anomalyDrilldownWindow` drill-down policy (which moves **OUT of the chart** into the tab — it is drill-down policy, not geometry). **The capability checklist gains "counter STEP rendering" (AM-26)** — the older engine's checklist omitted it while the port must carry `buildChartStepPath` | 4.1 | The **capability checklist** spec asserts all **six** are present: window ladder, series toggles, anomaly markers, drill-down, UTC formatting, **counter STEP rendering** |
| 4.3 | Add the ruled-in **mini-tier extensions** (ADJ-ii, closed set): `devicePixelRatio` crispness, nearest-point hover via overlay + bisect, last-value marker, optional baseline line, `viewBox` responsiveness. **Nothing outside the closed set** — no axes grid, no zoom/brush, no legend-toggle language, no export. **Per R-2 the mini role has NO page surface in T1**: the extensions are exercised through (a) the **mount-failure fallback** path and (b) **renderer-level specs** | 4.1, Phase 3 (the baseline toggle) | Each extension has a spec; a spec asserts the **absence** of the out-of-set capabilities on the mini role (the scope guard is pinned, not just documented); a spec asserts no T1 surface renders a mini |
| 4.4 | Create `frontend/src/app/pages/metrics/charts/chart-panel.component.ts` — the Chart.js wrapper implementing the Phase 1 contract, at its **phase-1 stub's path** (1.7 created it there; it is the wrapper's **final home**, under the `src/app/pages/metrics/**` tree the import-site rule covers — C-1). The per-series **DOM proxy** (preserving `data-series` / `data-render-mode`), the **visually-hidden data table**, **`chart.destroy()` in `ngOnDestroy`**, and the zoneless rule (no Angular-rendered overlay depends on a Chart.js redraw). **The import SHAPE is fixed** (Phase 1's 1.7): the `chart.js` import is the **dynamic** `await import('chart.js')` form — **never** a static `from 'chart.js'` (a static import inside the lazy metrics tree satisfies the initial-total delta, the initial-chunk marker scan and a location-only rule, because the vendor is extracted into a lazy-only sibling chunk) | Phase 1 | The wrapper's spec asserts all four contract clauses **and** the **dynamic** import shape (a static form must fail it); the DOM proxy is asserted by a **DOM test** (the `data-render-mode="step"` / `"line"` distinction the current e2e pins at `frontend/e2e/metrics-gate.spec.ts:283-295` must survive the canvas migration) |
| 4.5 | Configure the jest **canvas environment** (`jest-canvas-mock` — wired in the jest setup) so canvas-rendering specs run under jsdom | Phase 1 | A canvas-rendering spec runs green under jest; a planted "no canvas-mock" variant fails (proving the mock is load-bearing) |
| 4.6 | Implement the Chart.js features the panels need: value axis **with a unit suffix**, crosshair + nearest-point snap (`interaction: {mode:'index', intersect:false}`), legend with click-toggle, multi-dataset **shared-y**, `stepped: true` for counter series (the house counter-honesty rule), `onClick → (datasetIndex, index) → (seriesId, tsMs)`, and a **linear x-axis with a custom UTC tick callback** (so NO date-adapter dependency is needed) | 4.4 | Each feature has a spec; the UTC-never-local property is pinned (the current `chartUtcTicks` `HH:MM`-only label must become a UTC-aware tick that can also disambiguate **days** beyond 24 h — `svg-line-chart.component.ts:250-255` today cannot) |
| 4.7 | Implement the **anomaly overlay as a house plugin** (NOT `chartjs-plugin-annotation` — that would be a second dependency): the severity colours verbatim, the marker click → the wrapper's `pointClick` payload `{anomalyId?, seriesId, tsMs}` | 4.4, 4.6 | The plugin is a **house** file with its own spec; the lockfile gains **no** second vendor package |
| 4.8 | Create `frontend/src/app/components/charts/renderer-registry.ts` — the intent vocabulary (`trend` / `sparkline` / `stat`) plus flags (shared-y, markers, bands, drill) mapped to `ChartRenderer { capabilities, supports(), render(), teardown() }`. The **three-layer fallback**: build/test-time validation **first** (fail-loud, like `assert_roster_is_well_formed`), runtime mount-fallback **second** (surfaced, badged), hard error **third** — **never a silent downgrade** | 4.3, 4.4 | The registry spec asserts: an intent with no supporting renderer fails **loudly at build/test time**; a runtime mount failure **surfaces a badge and falls back** to the house SVG; a planted "no fallback" case is a hard error. A **silent** downgrade is the failure mode the spec forbids |
| 4.9 | **Scoped type-check correctness (AM-10).** `frontend/tsconfig.metrics-fe.json:11` includes the **OLD** spec path (`src/app/components/charts/svg-line-chart/svg-line-chart.component.spec.ts`) — tsc **silently ignores** stale includes, so the relocated specs would fall **OUT** of the scoped type-check (the P5-M9 leg) with **no signal**. Update the include to the new spec path(s) (the consolidated renderer's spec **and** the relocated resource specs), and remove the stale entry. **Note the asymmetry:** the 241 L spec falls out (it is an explicit include of a deleted path); the 237 L timeline spec was never included at all (it lives under `pages/resources/`, outside the `pages/metrics/**` glob) and only **enters** the scoped check after Phase 5's relocation | 4.1 | The scoped type-check's collected set includes the consolidated renderer's spec; a spec asserts the tsconfig include list matches the live spec paths (no stale entry survives) |
| 4.10 | **Re-specify the canvas-fatal e2e legs (AM-11 + W-3).** `frontend/e2e/metrics-gate.spec.ts` has **four** canvas-fatal assertions in **three** leg-classes: `:277` (a count assertion on `[data-test="svg-line-chart"]` — the component and the selector are **deleted** by 4.12), `:281-288` (the step path's `d`-attribute geometry: `H`/`V` present, no diagonal), `:290-296` (the `line` path's `d`-attribute mirror: `L` present, no `H`/`V`), `:298` (`chart-tick` SVG text visible). Under a canvas renderer there is no `d` and no `chart-tick`. Enumerate **all four** with their replacements: the count → a **per-series proxy count**; the two `d`-geometry assertions → **one** renderer-level step-vs-linear assertion (the geometric truth moves from the DOM to the renderer spec); the `chart-tick` → the wrapper's **UTC tick text in the hidden data table / DOM proxy**. **State explicitly that the `d`-attribute assertions are RETIRED, not preserved** | 4.4 | **All four** assertions are re-specified with their replacements; the e2e spec passes against the canvas panel; a "the `d` assertion survived" check fails |
| 4.11 | **Inline-style budget (AM-28).** The consolidated renderer (one primitive + five mini extensions + toggles + markers + legend + overlay + baseline + last-value), the Chart.js wrapper, the house anomaly plugin and the hidden data table are all new/expanded inline-style surfaces; the `anyComponentStyle` budget is **8 kB warn / 24 kB ERROR** (`frontend/angular.json`) and the 24 kB cap **hard-fails** the production build. Keep the consolidated renderer's inline styles **under the 8 kB warning**, and record the per-component figure | 4.3, 4.4 | The build's `anyComponentStyle` figure for the new components is **< 8 kB** (no new warning line); the figure is recorded in the lane evidence |
| 4.12 | Delete the superseded engine: `frontend/src/app/components/charts/svg-line-chart/` (523 L + its 241 L spec) and `frontend/src/app/pages/resources/svg-timeline/` (538 L + its 237 L spec) — **after** the port (4.1) is proven green. **The two-step migration is stated (AM-27):** in **Phase 4** the resource timeline is re-pointed to the **registry** (4.13); in **Phase 5** it is re-pointed again to the **panel role** (Chart.js) — the double re-point is explicit, not accidental. **Alternatively** the deletion of `pages/resources/svg-timeline/` is deferred until after Phase 5's `git mv` — the lane picks the ordering that keeps every commit green, and states which one it took | 4.1, 4.3, 4.9 | A repo-wide grep finds exactly **one** house SVG engine; the deleted files' specs are provably relocated or dispositioned (the 4.1 table) |
| 4.13 | Update the consumers: `frontend/src/app/pages/metrics/charts/charts-tab.component.ts` (316 L) and the resource timeline component use the registry instead of the deleted primitive. The **hardcoded `WINDOW_S = 3600`** (`:195`) is removed — the frame owns the window (Phase 3). **The residue check is pinned (AM-23 + A-4):** the exact command is `grep -rnE "\b3600\b" frontend/src/app/pages/metrics/ frontend/src/app/components/charts/` — **word-bounded** (the bare `"3600"` over-matches `36000` and `3.600`) — with the allowlist row `charts-tab.component.spec.ts` (a fixture asserting the old value) — every other hit must be zero | 4.8, 4.12 | No consumer imports the deleted primitive (grep: 0); the hardcoded window is gone (the pinned grep returns zero hits outside its allowlist row) |
| 4.14 | **BUNDLE GUARD (AM-2 + C-1), re-run here.** Re-run the Phase-1 guard after the engine lands, reading the **named baseline artifact** `bundle-baseline.json`: numeric `\|Δ initial-total\| ≤ 2 kB` vs the frozen 5,909,018 B baseline; no `chart.js` marker in ANY initial chunk; the **import-shape + import-site** assertion (**0** static `from 'chart.js'` sites; every site inside `src/app/pages/metrics/**`; the wrapper's dynamic `await import('chart.js')` form). This is the phase where the chunk graph actually changes, so the guard is **co-located** | 4.12, 4.13 | The guard is green at this commit; the initial total is unchanged within 2 kB; **BOTH** planted variants (the eager import and the static-inside-the-lazy-tree import) turn it red; the new lazy chunk's size is recorded (the Phase-9 measurement) |
| 4.15 | **COMPOSE THE PER-TAB PANELS (C-2 — the moved half of Phase 2's task 2.4).** Each central tab now **hands its data to the renderer registry** and **composes its PANELS per kind+unit group** (R-1/AM-9/AM-12): the kind is read from the **wire** (`chart_role` per metric — `frontend/src/app/models/metrics.model.ts:164`, resolved per series at `charts-tab.component.ts:222`), never from an FE table, so a family registering both a gauge and a counter (F4 `w1.py:194-228`; F9 `w2.py:120-150`) yields **two** panels — a mixed-kind panel breaks counter-honesty and falsifies the unit label. This task is where 2.4's two-panels assertion lives; Phase 4's exit re-asserts it | 4.8, 4.9 | A spec asserts a mixed-kind family produces **two** panels (one per kind) and that the kind came from the wire; the per-tab bodies render through the registry, not around it |

## Coupling

- **Tight with:** Phase 1 (the dependency + the wrapper contract + the first
  bundle guard), Phase 3 (the baseline capability; the frame drives it), Phase 5
  (the timeline's second re-point to the panel role), Phase 6 (the correctness
  fixes land on these renderers).
- **Loose with:** Phase 9 (the final bundle measurement + the chunk-size record).
- **Independent of:** Phase 7, Phase 8. (Phase 2 is now **tight** — 2.4's
  panel-composition half moved here as task 4.15 — C-2.)

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| The consolidation **loses capability** (the house's observed drift) | High | 4.2's capability checklist is an exit criterion (**6/6** with STEP rendering added); 4.1's per-assertion disposition table accounts for every assertion |
| A silent downgrade (a panel falls back with no badge) | High — the house forbids it | 4.8's three-layer fallback with a planted-violation test for the silent case |
| The canvas redraw fights the zoneless runtime | High | The rule is in the contract (Phase 1, 4.4) and pinned by a spec; the Angular-owned DOM (legend, proxy, hidden table) never depends on a library redraw |
| The DOM-assertion gates die with the canvas | Medium | 4.10 re-specifies **all three** canvas-fatal legs, not one; the DOM proxy + hidden data table keep `data-series` / `data-render-mode` assertable |
| The house SVG grows into a second full engine | Medium | The closed extension set (4.3) with a negative pin for the out-of-set capabilities; **no page surface** (R-2) |
| The scoped type-check silently loses the relocated specs | Medium | 4.9 updates the tsconfig include and asserts no stale entry survives |
| The new inline styles breach the 24 kB `anyComponentStyle` error cap | Medium (a hard build failure) | 4.11's < 8 kB bound + the recorded per-component figure |
| A chart.js import lands statically in `components/` and defeats the path allowlist | High | 1.7's fixed import shape + 1.2(c)'s import-graph pin + 4.14's initial-chunk scan |

## Exit Criterion

- Exactly **one** house SVG engine remains in the tree; both old engines are
  gone with their specs **relocated or dispositioned** — and the 4.1
  per-assertion disposition table accounts for **every** assertion of both
  original specs (that table IS the exit evidence, not a line count).
- The capability checklist passes **6/6** (window ladder, series toggles,
  anomaly markers, drill-down, UTC formatting, **counter STEP rendering**).
- The mini role's extensions are exactly the closed set (a spec proves the
  absence of axes-grid / zoom / brush / legend-toggle / export) and **no T1
  surface renders a mini** (R-2).
- The Chart.js wrapper satisfies all four contract clauses **and** the **dynamic**
  import shape (a static `from 'chart.js'` must fail the spec); it lives at
  `frontend/src/app/pages/metrics/charts/chart-panel.component.ts` — **inside**
  the `src/app/pages/metrics/**` tree the import-site rule covers; the DOM proxy
  keeps the `data-render-mode` step/line distinction assertable.
- **All FOUR** canvas-fatal e2e assertions (`:277`, `:281-288`, `:290-296`, `:298`)
  are re-specified with their replacements, in **three** leg-classes, and the
  `d`-attribute assertions are explicitly **retired** (W-3).
- **The mixed-kind family yields two panels in the per-tab bodies** — 2.4's
  assertion, re-asserted here (C-2).
- All **three** canvas-fatal e2e legs are re-specified with their replacements,
  and the `d`-attribute assertion is **explicitly retired**.
- The scoped type-check's include list matches the live spec paths — the
  consolidated renderer's spec and the relocated resource specs are **inside**
  the scoped check (no stale entry survives).
- The new components' inline styles are **< 8 kB** (no new
  `anyComponentStyle` warning) and the figure is recorded.
- The registry fails **loudly** for an unsupported intent and **surfaces** a
  mount-failure badge — a silent downgrade is impossible (planted-violation
  proven).
- The lockfile gains **no** second vendor package (the anomaly overlay is a
  house plugin).
- **The Phase-1 bundle guard is re-run and green**: `\|Δ initial-total\| ≤ 2 kB`
  vs 5,909,018 B, no `chart.js` marker in any initial chunk, the import-shape
  assertion satisfied (**0** static sites) and the import-site assertion
  satisfied; **both** planted variants turn it red.
- **The engine-deletion ordering is recorded here (A-7):** which of the two
  orderings Phase 4/5 took — delete `pages/resources/svg-timeline/` **after**
  Phase 5's `git mv`, or keep it until then — is stated in this exit criterion,
  because Phase 5's risk row points here.
- **E-1 re-derivation:** the MEASURED figures this phase gates on are (i) the
  **initial-total baseline** — re-derived at lane base by this fold
  (`research-findings.md` §14) and re-run by the guard in this phase; (ii) the
  **per-component `anyComponentStyle` figures** — **re-derived at this phase's
  freeze** by a production build in this worktree (the task records them; they
  are NOT inherited from the digest); (iii) the **new lazy chunk's size** — a
  Phase-9 measurement, recorded here as a placeholder and filled in Phase 9.
  No other MEASURED figure gates this phase.
