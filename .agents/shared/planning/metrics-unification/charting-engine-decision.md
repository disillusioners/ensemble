# Charting Engine Decision — digest (decision-ready)

**Companion to:** `resource-refactor-architecture.md` §10 (full evidence, file:line, measured-vs-estimated ledger). **Date:** 2026-11-03. All npm numbers MEASURED 2026-11-03 via `npm view` (metadata only); browser-bundle bytes are ESTIMATES until a real build measures them.

## Verdict

**The 4-engine proposal is rejected AS-IS, adopted IN SHAPE.** Keep the user's instinct — per-panel engine selection behind a seam — as a **chart-INTENT + renderer-registry** contract, and ship **ONE vendor engine in its best role: Chart.js v4 for interactive trend panels**, with the **house SVG distilled into the mini/sparkline renderer** (a role where canvas is strictly worse). **ECharts deferred** (its capability tier is measured-empty at our volumes), **ApexCharts deferred** (no aesthetic-report surface exists), **Gridster2 dropped** for the ratified fixed-tab direction. Chart.js lands **only in the lazy `/metrics` chunk** — ungoverned by any budget, invisible to first paint.

## The numbers

| Fact | Value | Status |
|---|---|---|
| Initial budget semantics | `initial` = entry points + static imports only; **lazy chunks counted by NO budget** (only `initial`+`anyComponentStyle` configured; 5 more types unused; zero per-chunk assertions repo-wide) | MEASURED (builder source) |
| Initial total today | **5,909,018 B vs 6,000,000 B error ⇒ 89,982 B headroom** (mermaid alone = 3.56 MB) — any engine leaking into the initial set **hard-fails the build + both FE packs** | MEASURED |
| Lazy baseline | 52 chunks / 1.70 MB; **/metrics chunk 27,672 B**; largest lazy 135,690 B | MEASURED |
| chart.js 4.6.1 | 6.2 MB unpacked; **1 dep** (`@kurkle/color`, 43.5 kB); +2 lockfile entries; MIT; last publish 2026-10-22 | MEASURED |
| echarts 6.1.0 | 60.3 MB unpacked; deps `tslib@2.3.0` (exact → nested copies) + zrender; +4 lockfile entries; ~150–400 kB tree-shaken / ~1 MB full in-browser | mixed |
| apexcharts 8.7.0 | 30 MB unpacked; **SVG default, canvas opt-in module**; wrapper `ng-apexcharts` 3.2.0 active & Angular-21-compatible | MEASURED |
| angular-gridster2 | latest 22.0.0 peers `@angular/core ^22` — **incompatible with our 21.2.5** (21-line pin = 21.0.1); 480 kB; single maintainer; **`angular2-d3` = E404, the d3-peer premise is obsolete** | MEASURED |
| Governance | pack Leg 7 hard-fails on ANY `package.json` diff; **5 "zero FE deps" sites** must be amended in the same commit | MEASURED |
| Our panels | 15 s effective cadence; central ≤ 240 pts/series, ≤ 12,000 pts/response, 2 h ceiling; heaviest panel = resource 7 d = 5 × 10,080 = 50,400 pts (rendered by SVG today); **no thousands-of-series panel can exist** (A10/A18 + 2,000-series cap) | MEASURED |

## Workload-fit verdict

**The ECharts tier is EMPTY.** Central windows hard-clamp at 2 h/240 pts (nothing to zoom into); the only long window (7 d) is a minute rollup where a local brush would imply resolution that does not exist — the honest zoom is **re-fetch narrower**, already shipped ("Narrow the window for finer detail"). **Minis need no engine** (SVG: a11y-native, jsdom-testable, zero instances; Chart.js per-mini = canvas instance + destroy discipline + a11y wrapper + broken Jest). Chart.js earns its place only in the **interactive panel role**: value axis+units, crosshair+snap, legend toggles, shared-y, click→drill — ~250–450 L wrapper vs ~480–720 L hand-built (+~440–705 L tests), against a house precedent of two hand-rolled engines that both got declared feature-frozen and diverged (the newer one regressed).

## Manifest shape

**Shape B: intent + renderers** (not per-engine configs). Manifest carries `trend | sparkline | stat` intents with flags (shared-y, markers, bands, drill); a registry maps intents to `ChartRenderer { capabilities, supports(), render(), teardown() }`. **Adding ECharts/ApexCharts later = one renderer file + one registry line; the manifest never changes.** Fallback: build/test-time validation first (fail-loud, like `assert_roster_is_well_formed`), runtime mount-fallback second (surfaced, badged), hard error third — **never a silent downgrade**. Cost: 1 vendor + SVG ≈ 665–1,145 L; four renderers ≈ 1,620–2,785 L + 4 theming surfaces + 3 interaction languages (ESTIMATES).

## UX note

Four engines = 2–3 interaction languages on one page **by default** (SVG native-`<title>`/no legend toggle; ECharts axisPointer/dataZoom/toolbox; ApexCharts no native legend toggle; Chart.js legend-click). One vendor + inert minis bounds it to one interactive language; the intent vocabulary is the contract that keeps it there. No chart token layer exists yet — extract `chart-theme.ts` (~40–60 L, de-duplicates a palette copied in 3+ files) before any engine lands.

## Gridster2 either/or

**Either** adopt now: pay an Angular-major-mismatched peer (pin 21.0.1), bus-factor-1 maintenance, 480 kB, and a layout-frame import shape that risks the 89-KiB initial headroom — to obtain drag/resize the ratified fixed-tab direction does not want. **Or defer** (recommended): cost of deferring 12 months is LOW (≤1 major behind; adoption naturally lands on an Angular-major boundary; the layout layer is orthogonal to the engine seam — a Gridster widget is just a different `host`). **Trigger to revisit:** a real composable-dashboards product requirement — then adopt at the Angular-major boundary, or hand-roll CSS-grid drag/resize (~150–300 L, the grid already exists).

## The one decision needed

**Ratify the reduced set + the posture amendment:** Chart.js v4 as the sole vendor dep (panel role, lazy chunk, +2 lockfile entries, +1 canvas-mock devDep) with the same-commit amendment of pack Leg 7 + the four live "zero FE deps" doc/checklist sites — **or** ratify zero-npm-deps as a standing FE product position, which flips the panel role to the extended house-SVG renderer at ~+400–700 L (crosshair+nearest-snap is the expensive item) under a house rule that the frozen-list is a spec, not a default. Everything else (ECharts, ApexCharts, Gridster2) is deferred with the triggers above and re-enters additively through the renderer seam.
