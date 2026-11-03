# Phase 4 evidence — renderer consolidation (wi-32a99618)

> The phase's own evidence. Every number here was MEASURED in this
> worktree (`/home/nea/ensemble-src-wt-fe-metrics-dashboard`, branch
> `feature/fe-metrics-dashboard`); nothing is inherited from prose.

## 0. What landed

| Task | What | Where |
|---|---|---|
| 4.1 | The consolidated house SVG engine — the port of BOTH former engines' exported symbols + the single geometry | `frontend/src/app/components/charts/house-svg-renderer/house-svg-renderer.ts` (+ `.component.ts`, `.renderer.ts`) |
| 4.2 | The six capabilities of the older engine carried forward (window ladder, series toggles, anomaly markers, drill-down, UTC formatting, counter STEP) | the engine + the timeline tab + `timeline-series.ts` / `timeline-drilldown.ts` |
| 4.3 | The ruled-in mini extensions (the CLOSED set) + the absence pins | `house-svg-renderer.component.ts` + `house-svg-renderer.spec.ts` |
| 4.4 | The Chart.js panel wrapper at the CANONICAL path, implementing the four contract clauses | `frontend/src/app/pages/metrics/charts/chart-panel.component.ts` |
| 4.5 | `jest-canvas-mock` wired into the jest setup | `frontend/setup-jest.ts` |
| 4.6 | The panel's house rendering rules (the unit axis, the index snap, the legend toggle, the shared y, `stepped`, the click hand-off, the UTC tick callback) | `chart-panel.component.ts` (`buildChartConfig`) |
| 4.7 | The HOUSE anomaly overlay plugin (+ the house crosshair plugin) | `frontend/src/app/pages/metrics/charts/anomaly-overlay-plugin.ts`, `crosshair-plugin.ts` |
| 4.8 | The intent → renderer registry with the three-layer fallback | `frontend/src/app/components/charts/renderer-registry.ts` + the two registrations |
| 4.9 | The scoped type-check's include list re-pointed at the live spec paths | `frontend/tsconfig.metrics-fe.json` + `metrics.tsconfig.spec.ts` |
| 4.10 | The four canvas-fatal e2e assertions re-specified in three leg-classes | `frontend/e2e/metrics-gate.spec.ts` + `metrics.e2e-retirement.spec.ts` |
| 4.11 | The per-component `anyComponentStyle` figures | §6 below |
| 4.12 | BOTH superseded engines deleted; the retired out-of-tree wrapper path swept | §5 below |
| 4.13 | The consumers re-pointed (the charts tab, the central tab, the resource timeline) + the `3600` residue cleared | §4 below |
| 4.14 | The bundle guard re-run (GREEN) + both planted variants RED + the B4 lazy-only decomposition | §7 below |
| 4.15 | The per-kind+unit PANEL COMPOSITION (the kind from the wire) | `frontend/src/app/pages/metrics/tabs/central-tab.component.ts` + `metrics-units.ts` |

## 1. The per-assertion disposition table (AM-26) — the EXIT EVIDENCE

The two former specs carried **42 assertions** (29 in the newer engine's
spec, 13 in the older engine's). EVERY one is classified below. The
count is derived from the files, not declared.

### 1.1 The newer engine's spec — `components/charts/svg-line-chart/svg-line-chart.component.spec.ts` (29)

| # | line | the assertion | disposition | where it lives now |
|---|---|---|---|---|
| L1 | 36 | the x scale maps the window start/end to the plot edges | **port** | `house-svg-renderer.spec.ts` — "the x scale" |
| L2 | 41 | the x scale maps the midpoint to the middle of the plot | **port** | idem |
| L3 | 46 | the x scale clamps an out-of-window sample to the edge | **port** | idem |
| L4 | 51 | the x scale degenerates to the midpoint for an empty window | **port** | idem |
| L5 | 58 | the x scale falls back to the plot left for a non-finite sample | **port** | idem |
| L6 | 64 | the y scale maps the series min to the floor and the max to the ceiling | **port** | "the y scale (P5-M6: the per-series normalisation)" |
| L7 | 69 | the y scale maps the range midpoint to the plot middle | **port** | idem |
| L8 | 74 | the y scale NORMALISES against the range (P5-M6) | **port** | idem |
| L9 | 82 | the y scale clamps an out-of-range value to the edge | **port** | idem |
| L10 | 87 | the y scale degenerates to the plot middle for an empty range | **port** | idem |
| L11 | 94 | the y scale falls back to the floor for a non-finite value | **port** | idem |
| L12 | 100 | the series domain reads the observed min/max | **port** | "the series domain" |
| L13 | 110 | the series domain ignores the non-finite samples | **port** | idem |
| L14 | 119 | the series domain returns a zero range for an empty series | **port** | idem |
| L15 | 131 | the linear path carries M + L and neither H nor V | **port** | "the paths" |
| L16 | 139 | the STEP path carries H/V (the counter-honesty geometry) | **port** | idem |
| L17 | 148 | the empty path for an empty series | **port** | idem |
| L18 | 154 | the path dispatches by the rendering mode | **port** | idem |
| L19 | 160 | the area path closes to the plot floor | **port** | idem |
| L20 | 166 | the STEP area path closes with the step geometry | **port** | idem |
| L21 | 174 | the coordinates project the samples onto the plot | **port** | "the coordinates and the axis" |
| L22 | 190 | the projection drops the non-finite samples | **port** | idem |
| L23 | 205 | the ticks lay out across the window | **port** | idem |
| L24 | 212 | the tick labels are UTC (never local) | **port** (+ the day-disambiguation extension) | idem + "the UTC tick disambiguates days" |
| L25 | 219 | no ticks for an empty window | **port** | idem |
| L26 | 226 | an integer stays an integer | **port** | "the value labels" |
| L27 | 230 | a fractional value rounds to two decimals | **port** | idem |
| L28 | 234 | a non-finite value renders the em-dash placeholder | **port** | idem |
| L29 | 238 | the tooltip text is the UTC time + the value | **port** | idem |

**29 ported / 0 dropped / 0 unaccounted.** Every one of these
assertions exercises a symbol whose NAME and BEHAVIOUR survive the
consolidation — the port was a MOVE, never a rewrite.

### 1.2 The older engine's spec — `pages/resources/svg-timeline/svg-timeline.component.spec.ts` (13)

| # | line | the assertion | disposition | where it lives now |
|---|---|---|---|---|
| T1 | 66 | `timelineXScale` maps the endpoints/midpoint to the plot edges | **re-point** (the name survives as an alias; the expected constants are now the HOUSE ones — the single geometry) | `house-svg-renderer.spec.ts` — "the older engine's ported helpers (re-pointed)" |
| T2 | 78 | `timelineXScale` degenerates to the midpoint | **re-point** (idem) | idem |
| T3 | 86 | `timelineYScale` maps min to the floor and max to the ceiling | **re-point** (idem) | idem |
| T4 | 91 | `buildLinePath` / `buildAreaPath` emit non-empty path data | **re-point** (the names survive as aliases; the area closes in the HOUSE form) | idem |
| T5 | 109 | `anomalyDrilldownWindow` pads by max(60 s, 10 % of the span) | **re-point — MOVED**: it is drill-down POLICY, not geometry, so it moved OUT of the chart into the tab's own module | `frontend/src/app/pages/resources/timeline/timeline-drilldown.ts` (+ its own spec) |
| T6 | 127 | `formatUtcClock` formats in UTC | **re-point** (the name survives as an alias onto the house (guarded) formatter) | `house-svg-renderer.spec.ts` — the re-pointed block |
| T7 | 148 | the component renders a non-empty line + area per enabled series | **rewrite** (the two component APIs differ — the house renderer's series shape) | `house-svg-renderer.spec.ts` — "renders a non-empty line + area per given series" |
| T8 | 165 | the component renders only the series named by its input | **rewrite** (the toggle is the PARENT's; the renderer draws what it is given) | "renders only the series it is given (the toggle lives in the parent)" |
| T9 | 175 | the anomaly markers are positioned by `t` | **rewrite** (the house renderer's marker DOM) | "positions the anomaly markers by their timestamp" |
| T10 | 190 | the UTC time-axis tick labels render | **rewrite** (the house renderer's tick DOM; the TREND variant only) | "renders UTC time-axis tick labels (the trend variant)" |
| T11 | 204 | the marker click emits `{anomaly, t0, t1}` | **rewrite — SPLIT**: the renderer hands over the MARKER; the tab owns the padded window (T5's policy) | "hands the anomaly over on a marker click" + `timeline-tab.component.ts` (`onHouseMarkerClick`) |
| T12 | 221 | the empty label shows when there are no samples | **rewrite** (the house renderer's empty label) | "shows the empty label when there are no samples" |
| T13 | 227 | the viewBox is intact (never distorted by `preserveAspectRatio=none`) | **rewrite** (the viewBox is now RESPONSIVE — the mini extension; the `preserveAspectRatio` assertion is preserved) | "keeps the viewBox intact (never distorted by preserveAspectRatio=none)" |

**13 accounted: 6 re-point + 7 rewrite / 0 dropped / 0 unaccounted.**

### 1.3 The totals

| Disposition | Count |
|---|---|
| port | **29** |
| re-point | **6** |
| rewrite (the component-level assertions) | **7** |
| drop | **0** |
| **unaccounted** | **0** |
| **TOTAL** | **42** (29 + 13) |

## 2. The capability checklist — 6/6 (task 4.2)

| # | the capability | where it lives | the pin |
|---|---|---|---|
| 1 | window ladder | the resource tab (`TIMELINE_WINDOWS` — 15m/1h/24h/7d) | `house-svg-renderer.spec.ts` — "(1) the window ladder is present" |
| 2 | series toggles | the PARENT owns the toggle; the renderer draws exactly the series it is given | "(2) the series toggle capability is present" |
| 3 | anomaly markers (the severity colours VERBATIM) | the house engine (`CHART_SEVERITY_COLORS` — low `#38bdf8` / medium `#f59e0b` / high `#ef4444`) | "(3) the anomaly markers are present, with the house severity colours VERBATIM" |
| 4 | drill-down | the renderer hands the marker over; the tab computes the padded window | "(4) the drill-down capability is present" |
| 5 | UTC formatting | `formatChartUtc` / `formatUtcClock` (epoch-ms derived, never the process clock) | "(5) the UTC formatting is present" |
| 6 | counter STEP rendering | `buildChartStepPath` (the `H`/`V` geometry) | "(6) the counter STEP rendering is present" |

## 3. The mini's closed set + the absence pins (task 4.3)

The extensions are EXACTLY the five ADJ-ii ruled in: `devicePixelRatio`
crispness, the nearest-point hover (overlay + bisect), the last-value
marker, the optional baseline line, the responsive `viewBox`.

The pins (`house-svg-renderer.spec.ts`):

- "(1) the devicePixelRatio crispness divides the stroke by the ratio" + "(1b) the component applies the crispness rule";
- "(2) the nearest-point bisect finds the closest sample" + "(2b) the hover overlay reports the nearest sample";
- "(3) the last-value marker is drawn per series";
- "(4) the optional baseline line is drawn when a baseline is given";
- "(5) the viewBox follows the measured container";
- **the absence pin**: "the MINI role renders no axes grid, no ticks and no legend";
- **the absence pin**: "NEITHER role renders a zoom, a brush, an export or a legend toggle" (DOM queries + a comment-stripped source scan for `zoom`/`brush`/`drag`/`exportTo`/`download`/`onPan`);
- **R-2**: "NO T1 page surface renders a mini" (a source scan of `src/app/pages/**` for `intent: 'sparkline'` and `variant="mini"`).

## 4. The consumers (task 4.13) + the `3600` residue

### 4.1 The pinned residue grep

```
grep -rnE "\b3600\b" frontend/src/app/pages/metrics/ frontend/src/app/components/charts/
```

The output at the phase-4 freeze (the allowlist row is
`charts-tab.component.spec.ts`):

```
frontend/src/app/pages/metrics/charts/charts-tab.component.spec.ts:5://   * the window request — `window_s=3600` ⇒ 240 points at the door's
frontend/src/app/pages/metrics/charts/charts-tab.component.spec.ts:118:  it('declares the named poll interval (15 s) and the FRAME-owned window', () => {
frontend/src/app/pages/metrics/charts/charts-tab.component.spec.ts:123:    expect(component.windowS()).toBe(3600);
```

**Three hits, all inside the allowlist row.** The three hits that
phase 3 reported in `charts-tab.component.ts` (the `WINDOW_S` constant
and its two comments) and the one hit in `metrics-door.ts` (a comment)
are GONE — the hardcoded window was replaced by the frame-owned
`windowS` input, whose default is derived from the central ladder
(`METRICS_CENTRAL_WINDOW_MINUTES['1h'] * 60`), and the `metrics-door.ts`
comment no longer names the literal.

### 4.2 The consumers

| Consumer | Before | After |
|---|---|---|
| `pages/metrics/charts/charts-tab.component.ts` | `<app-svg-line-chart>` + a hardcoded `WINDOW_S = 3600` | the REGISTRY's plan mounted with `NgComponentOutlet`; the window is the frame's `windowS` input |
| `pages/metrics/tabs/central-tab.component.ts` | one `<app-svg-line-chart>` per series | the REGISTRY's plan per KIND+UNIT group, with the mount-failure fallback + the badge |
| `pages/resources/timeline/timeline-tab.component.ts` | `<app-svg-timeline>` | the STATIC registry's plan (the house SVG only — the resources route never reaches the interactive vendor) |

## 5. The engine deletion (task 4.12) — the ORDERING (A-7)

**The ordering this lane took: BOTH engines were deleted in phase 4.**

- `frontend/src/app/components/charts/svg-line-chart/` — deleted (the
  component + its 241 L spec);
- `frontend/src/app/pages/resources/svg-timeline/` — deleted (the
  component + its 237 L spec);
- the retired out-of-tree wrapper path
  `frontend/src/app/components/charts/chart/` — **0 orphan files**
  (the path was never created; the sweep is a no-op that still asserts
  the count — `chart-panel.component.spec.ts`, "has ZERO files under
  frontend/src/app/components/charts/chart/").

**Why this ordering and not the deferred one:** the phase-4 exit
criterion demands "exactly ONE house SVG engine remains in the tree", so
the timeline engine could not survive phase 4. The resource timeline
was therefore re-pointed to the **static registry** in phase 4 (the
house SVG's fallback role, which keeps its time axis, its legend and its
anomaly markers), and phase 5 re-points it again to the **panel role**
(Chart.js) — the double re-point AM-27 states, now explicit.

**The proof that one engine remains:** `house-svg-renderer.spec.ts` —
"the house engine is the ONLY SVG chart engine in the tree" (a scan of
`src/app/**` for `svg-(line-chart|timeline)`), green at the freeze.

## 6. The inline-style budget (task 4.11)

The `anyComponentStyle` budget is 8 kB warn / 24 kB error. The
production build's warning list is UNCHANGED (the guard's B6 set
difference: 0 added, 0 removed) and contains **none** of the new
components.

The per-component figures (the compiled CSS of each component's inline
`styles` array, whitespace stripped):

| Component | figure |
|---|---|
| `components/charts/house-svg-renderer/house-svg-renderer.component.ts` | **1,111 B (1.08 kB)** |
| `pages/metrics/charts/chart-panel.component.ts` | **235 B (0.23 kB)** |
| `pages/metrics/tabs/central-tab.component.ts` | **1,586 B (1.55 kB)** |
| `pages/metrics/charts/charts-tab.component.ts` | **769 B (0.75 kB)** |

## 7. The bundle guard (task 4.14)

### 7.1 The guard at the phase-4 freeze — GREEN

```
cd frontend && node ../.agents/shared/planning/metrics-unification/fe-t1-build/bundle-guard.js dist/frontend/browser /tmp/fe-t1-p4-prod.log
```

```
(a) B1 — the numeric initial-total delta
    initial_total_bytes = 5909624
    baseline_bytes      = 5909018
    delta               = 606 B (limit |2048| B)
    initial_file_count  = 37
    measurement_tool    = 5909624 (cross-check)

(b) B2 — the chart.js marker scan over the initial set
    initial_chunks_scanned = 37
    marker_hits            = 0

(c) B3 — the import shape + import site
    chart_js_import_sites  = 1
    static_sites           = 0
    outside_metrics_tree   = 0
      src/app/pages/metrics/charts/chart-panel.component.ts: dynamic=2 static=0

(d) B6 — the anyComponentStyle warning SET (not a count)
    baseline_set_size = 9
    measured_set_size = 9
    added   = 0
    removed = 0

RESULT: GREEN (B1 + B2 + B3 + B6)
```

### 7.2 The B4 lazy-only decomposition (decisions.md IMPL-21)

```
cd frontend && node ../.agents/shared/planning/metrics-unification/fe-t1-build/bundle-baseline.measure.js dist/frontend/browser
```

| Figure | Lane base (the frozen artifact) | Phase-3 freeze | **Phase-4 freeze** |
|---|---|---|---|
| `initial_total_bytes` | 5,909,018 | — | **5,909,624** (+606 B) |
| `metrics_route_chunk_bytes` | 27,672 | 50,872 | **53,509** |
| `metrics_route_fetch_set_bytes` (total) | 496,338 | 603,420 | **621,323** |
| `metrics_route_fetch_set_lazy_only_bytes` | **72,708** | 96,539 | **114,290** |
| growth vs the recorded base (≤ 65,536 B) | — | +23,831 B | **+41,582 B** ✅ |

### 7.3 The new lazy chart chunk (the Phase-9 placeholder)

The vendor lands in its own lazy chunk: `chunk-TXUCW6LI.js` —
**206,857 B minified / 71,179 B gzipped**. It is a DYNAMIC dependency
of the route chunk, so it is NOT part of the route fetch set (the
fetch set's lazy-only growth is +41,582 B, well inside the 65,536 B
threshold).

**DISCLOSURE — the phase-9 threshold:** the plan's B4 row states "the
new chart chunk's real min + gz ≤ 65,536 B (64 KiB)". The measured
figures are 206,857 B minified and 71,179 B gzipped — **both readings
are over the stated 64 KiB** (the gzipped size by 5,643 B). Phase 4
only RECORDS the figure (the threshold is phase 9's gate, 9.8d), but
the number is stated here plainly rather than left for phase 9 to
discover. The chart.js v4 main entry is ~200 kB minified; the
import-shape rule (`await import('chart.js')`) forbids splitting it
into per-controller imports, so the figure is what the frozen import
shape costs.

### 7.4 BOTH planted variants turn the guard RED

**Variant (b) — a STATIC import inside the LAZY metrics tree** (the case
that slips through B1/B2): `import { Chart } from 'chart.js';` planted
in `pages/metrics/charts/chart-panel.component.ts`.

```
RESULT: RED (1 violation(s))
  - B3 1 STATIC chart.js import site(s): src/app/pages/metrics/charts/chart-panel.component.ts
```

with **B1 still green** (delta 606 B) and **B2 still green** (0
markers) — the shape check is the ONLY thing that catches it, exactly
as C-1 states.

**Variant (a) — an EAGER import in the INITIAL tree**:
`import { Chart } from 'chart.js';` planted in `src/app/app.ts`.

```
RESULT: RED (2 violation(s))
  - B3 1 STATIC chart.js import site(s): src/app/app.ts
  - B3 1 chart.js import site(s) outside src/app/pages/metrics/**: src/app/app.ts
```

## 8. The e2e re-specification (task 4.10) — FOUR assertions, THREE leg-classes

| # | the retired assertion | the replacement | the leg-class |
|---|---|---|---|
| 1 | the count on `[data-test="svg-line-chart"]` (`:277`) | the per-series PROXY count — `[data-test="chart-panel-proxy"]` | 1 |
| 2 | the STEP path's `d` geometry (`:281-288`) | the proxy's `data-render-mode="step"` | 2 |
| 3 | the LINE path's `d` mirror (`:290-296`) | the proxy's `data-render-mode="line"` | 2 |
| 4 | the `[data-test="chart-tick"]` SVG text (`:298`) | the wrapper's UTC tick text — `[data-test="chart-panel-tick"]` | 3 |

**THE `d`-ATTRIBUTE ASSERTIONS ARE RETIRED — NOT PRESERVED.** The
geometric truth moved to the renderer spec: "renders a COUNTER as STEP
and a GAUGE as LINEAR (the geometric truth the e2e retired)".

The retirement is PINNED (`metrics.e2e-retirement.spec.ts`): the e2e
source (comments stripped) carries no `getAttribute('d')`, no
`[data-test="svg-line-chart"]` and no `[data-test="chart-tick"]`, and
carries all three replacements.

## 9. The scoped type-check (task 4.9)

`frontend/tsconfig.metrics-fe.json` now includes the CONSOLIDATED
renderer's spec and the registry's spec, and the stale
`svg-line-chart` entry is gone. `metrics.tsconfig.spec.ts` asserts that
every listed file EXISTS (no stale entry) and that the two new paths
are inside the scoped check.

```
cd frontend && ./node_modules/.bin/tsc -p tsconfig.metrics-fe.json --noEmit
(exit 0 — no output)
```

## 10. The three-layer fallback (task 4.8)

| Layer | The proof | The spec |
|---|---|---|
| 1 — build/test-time, fail-loud | `assertChartRegistryIsWellFormed` refuses an empty registry, an uncovered intent, a registry with no static renderer, and a renderer whose `supports()` contradicts its declared capabilities; `resolve()` throws `UnsupportedChartIntentError` | `renderer-registry.spec.ts` — "the build/test-time validation (layer 1)" (6 assertions) |
| 2 — runtime mount-fallback, surfaced + badged | `chartMountFallback` returns the fallback plan AND a non-empty badge; the central tab renders both (the fallback plan + `[data-test="panel-fallback-badge"]`) | `renderer-registry.spec.ts` — "the runtime mount-fallback (layer 2)" (2) + `central-tab.component.spec.ts` — "a MOUNT FAILURE falls back to the house SVG and BADGES the downgrade" |
| 3 — hard error | with no fallback renderer registered, `fallbackPlan`/`chartMountFallback` THROW `ChartRegistryError` ("a mount failure would be a silent downgrade") | `renderer-registry.spec.ts` — "the hard error (layer 3)" (1) |

**A SILENT downgrade is impossible:** every fallback carries a badge,
and a missing fallback throws.

## 11. The dependency set — NO second vendor package

The anomaly overlay is a HOUSE plugin (`anomaly-overlay-plugin.ts`),
not `chartjs-plugin-annotation`. The lockfile carries exactly
`chart.js` (the vendor) and `jest-canvas-mock` (the devDependency) —
asserted by `anomaly-overlay-plugin.spec.ts` ("the dependency set — NO
second vendor package"), which scans `package.json` and
`package-lock.json`.

## 12. The deviations

| # | The deviation | The reason |
|---|---|---|
| D1 | The e2e's TAB-SHAPE expectations were NOT re-pointed (only the four canvas-fatal assertions were) | phase 7's task 7.9 owns the e2e's deep-link/tab-shape update; the phase-2 shell rewiring (the 7-tab manifest) is what made them stale, and this phase's scope is the canvas migration |
| D2 | The unit vocabulary ships EMPTY (`metrics-units.ts`) | the vocabulary's CONTENT is phase 6's task 6.3; phase 4 needed the grouping MECHANISM, and IMPL-10 (iii) blesses the `unit unknown` rendering for a metric with no entry. A wrong unit is worse than no unit |
| D3 | The engine's geometry is ONE geometry (the older engine's `TIMELINE_*` constants are aliases onto the house ones) | the consolidation IS one engine; the older engine's assertions were re-pointed to the house constants (the disposition table) |
| D4 | The drill-down policy (`anomalyDrilldownWindow`) moved to `pages/resources/timeline/timeline-drilldown.ts` | task 4.2: "it is drill-down policy, not geometry". It stays exported and unit-asserted — there, not in the chart |
| D5 | The renderers hand over their callbacks as INPUTS, not outputs | this Angular version's `NgComponentOutlet` has no outputs binding (`ngComponentOutletOutputs` does not exist in the installed `@angular/common`); the callback input is the only hand-off the outlet supports |
| D6 | The plan's exit criterion carries a stale "all three canvas-fatal e2e legs" line | W-3's correction governs — FOUR assertions in THREE leg-classes; the stale line is recorded here |
| D7 | The chart chunk's gzipped size (71,179 B) is over the plan's 64 KiB B4 threshold | the threshold is phase 9's gate; phase 4 records the figure (§7.3) |
| D8 | The `metrics-door.ts` comment that named the `3600` literal was re-worded (one phrase) | the pinned residue grep demands zero hits outside the allowlist row |
