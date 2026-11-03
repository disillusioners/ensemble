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

## Tasks

| # | Task | Depends On | Acceptance |
|---|---|---|---|
| 4.1 | Create `frontend/src/app/components/charts/house-svg-renderer/` — the consolidated house primitive. It **ports** (never rewrites) the exported pure helpers of BOTH engines: `timelineXScale`, `timelineYScale`, `buildLinePath`, `buildAreaPath`, `anomalyDrilldownWindow`, `formatUtcClock` (`frontend/src/app/pages/resources/svg-timeline/svg-timeline.component.ts:95-167`) and the `svg-line-chart` equivalents (`svg-line-chart.component.ts:108-271`). The helpers stay **exported and unit-asserted** — that property is why the audit could cite them precisely | Phase 1 | The ported helper specs (241 L + 237 L) are **moved** into the new location and stay green; a diff review shows no assertion lost |
| 4.2 | Keep the **older engine's capabilities** in the consolidated renderer: the window ladder, series toggles, anomaly markers (with the severity colours **verbatim** from `svg-timeline.component.ts:169-173`), click → drill-down, the UTC clock formatter, and the `anomalyDrilldownWindow` drill-down policy (which moves **OUT of the chart** into the tab — it is drill-down policy, not geometry) | 4.1 | A **capability checklist** spec asserts all five are present: window ladder, series toggles, anomaly markers, drill-down, UTC formatting |
| 4.3 | Add the ruled-in **mini-tier extensions** (ADJ-ii, closed set): `devicePixelRatio` crispness, nearest-point hover via overlay + bisect, last-value marker, optional baseline line, `viewBox` responsiveness. **Nothing outside the closed set** — no axes grid, no zoom/brush, no legend-toggle language, no export | 4.1, Phase 3 (the baseline toggle) | Each extension has a spec; a spec asserts the **absence** of the out-of-set capabilities on the mini role (the scope guard is pinned, not just documented) |
| 4.4 | Create `frontend/src/app/components/charts/chart/chart-panel.component.ts` — the Chart.js wrapper implementing the Phase 1 contract: the per-series **DOM proxy** (preserving `data-series` / `data-render-mode`), the **visually-hidden data table**, **`chart.destroy()` in `ngOnDestroy`**, and the zoneless rule (no Angular-rendered overlay depends on a Chart.js redraw) | Phase 1 | The wrapper's spec asserts all four contract clauses; the DOM proxy is asserted by a **DOM test** (the `data-render-mode="step"` / `"line"` distinction the current e2e pins at `frontend/e2e/metrics-gate.spec.ts:283-295` must survive the canvas migration) |
| 4.5 | Configure the jest **canvas environment** (`jest-canvas-mock` — wired in the jest setup) so canvas-rendering specs run under jsdom | Phase 1 | A canvas-rendering spec runs green under jest; a planted "no canvas-mock" variant fails (proving the mock is load-bearing) |
| 4.6 | Implement the Chart.js features the panels need: value axis **with a unit suffix**, crosshair + nearest-point snap (`interaction: {mode:'index', intersect:false}`), legend with click-toggle, multi-dataset **shared-y**, `stepped: true` for counter series (the house counter-honesty rule), `onClick → (datasetIndex, index) → (seriesId, tsMs)`, and a **linear x-axis with a custom UTC tick callback** (so NO date-adapter dependency is needed) | 4.4 | Each feature has a spec; the UTC-never-local property is pinned (the current `chartUtcTicks` `HH:MM`-only label must become a UTC-aware tick that can also disambiguate **days** beyond 24 h — `svg-line-chart.component.ts:250-255` today cannot) |
| 4.7 | Implement the **anomaly overlay as a house plugin** (NOT `chartjs-plugin-annotation` — that would be a second dependency): the severity colours verbatim, the marker click → the wrapper's `pointClick` payload `{anomalyId?, seriesId, tsMs}` | 4.4, 4.6 | The plugin is a **house** file with its own spec; the lockfile gains **no** second vendor package |
| 4.8 | Create `frontend/src/app/components/charts/renderer-registry.ts` — the intent vocabulary (`trend | sparkline | stat`) plus flags (shared-y, markers, bands, drill) mapped to `ChartRenderer { capabilities, supports(), render(), teardown() }`. The **three-layer fallback**: build/test-time validation **first** (fail-loud, like `assert_roster_is_well_formed`), runtime mount-fallback **second** (surfaced, badged), hard error **third** — **never a silent downgrade** | 4.3, 4.4 | The registry spec asserts: an intent with no supporting renderer fails **loudly at build/test time**; a runtime mount failure **surfaces a badge and falls back** to the house SVG; a planted "no fallback" case is a hard error. A **silent** downgrade is the failure mode the spec forbids |
| 4.9 | Delete the superseded engine: `frontend/src/app/components/charts/svg-line-chart/` (523 L + its 241 L spec) and `frontend/src/app/pages/resources/svg-timeline/` (538 L + its 237 L spec) — **after** the port (4.1) is proven green. The consolidation must leave **one** house SVG engine in the tree | 4.1, 4.3 | A repo-wide grep finds exactly one house SVG engine; the deleted files' specs are provably relocated (not deleted) |
| 4.10 | Update the consumers: `frontend/src/app/pages/metrics/charts/charts-tab.component.ts` (316 L) and the resource timeline component use the registry instead of the deleted primitive. The **hardcoded `WINDOW_S = 3600`** (`:195`) is removed — the frame owns the window (Phase 3) | 4.8, 4.9 | No consumer imports the deleted primitive (grep: 0); the hardcoded window is gone (grep: 0 hits for a literal 3600 window in the metrics pages) |

## Coupling

- **Tight with:** Phase 1 (the dependency + the wrapper contract), Phase 3 (the
  baseline capability), Phase 5 (the resource timeline migrates to the panel
  role), Phase 6 (the correctness fixes land on these renderers).
- **Loose with:** Phase 9 (the bundle measurement of the new lazy chunk).
- **Independent of:** Phase 2, Phase 7, Phase 8.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| The consolidation **loses capability** (the house's observed drift) | High | 4.2's capability checklist is an exit criterion; 4.1 ports the specs rather than rewriting them |
| A silent downgrade (a panel falls back with no badge) | High — the house forbids it | 4.8's three-layer fallback with a planted-violation test for the silent case |
| The canvas redraw fights the zoneless runtime | High | The rule is in the contract (Phase 1, 4.4) and pinned by a spec; the Angular-owned DOM (legend, proxy, hidden table) never depends on a library redraw |
| The DOM-assertion gates die with the canvas | Medium | The DOM proxy + hidden data table keep `data-series` / `data-render-mode` assertable; the e2e step/line pin is re-specified at renderer level (4.4) |
| The house SVG grows into a second full engine | Medium | The closed extension set (4.3) with a negative pin for the out-of-set capabilities |

## Exit Criterion

- Exactly **one** house SVG engine remains in the tree; both old engines are
  gone with their specs **relocated**, not dropped.
- The capability checklist passes **5/5**.
- The mini role's extensions are exactly the closed set (a spec proves the
  absence of axes-grid / zoom / brush / legend-toggle / export).
- The Chart.js wrapper satisfies all four contract clauses; the DOM proxy keeps
  the `data-render-mode` step/line distinction assertable.
- The registry fails **loudly** for an unsupported intent and **surfaces** a
  mount-failure badge — a silent downgrade is impossible (planted-violation
  proven).
- The lockfile gains **no** second vendor package (the anomaly overlay is a
  house plugin).
- All relocated helper specs are green; no assertion was lost in the port.
