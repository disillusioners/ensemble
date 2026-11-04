# Charting-Engine Decision — Independent (Blind) Evaluation

**Date:** 2026-11-03 · **Author:** architect (independent second opinion; no prior-context file read — `resource-refactor-architecture.md` deliberately NOT opened)
**Evidence basis:** `origin/latest` tree at `/home/nea/ensemble-src-wt-stage-v0289/` (v0.28.9 tag target, READ-ONLY), the four evidence files in this directory, `npm view` registry metadata (no install performed), and the actual built `dist/` in the v0.28.9 worktree.

---

## VERDICT (up front)

**Adopt a REDUCED SET: zero new chart engines now. Build the tab manifest as an engine-agnostic chart-INTENT spec (shape B) with the consolidated SVG primitives as the single registered renderer. Each proposed engine gets a named, written trigger; when a trigger fires, that engine registers behind the same seam — additive, lazy-chunk-confined, no core rewrite.** The 4-engine proposal as-is is rejected on measured evidence: the ECharts workload tier is **empty at our data volume** (hard server ceilings of 240 points/series and 12,000 points/response on the central door; ≤7,200 points on the largest current resources panel), the Chart.js mini tier **does not exist as a surface** (Overview/Live are stat cards, not charts), and Gridster2's core value is structurally unused under the settled predefined-tabs direction.

The user's core instinct — *per-panel engine selection through a seam* — is **right and is preserved**: the seam is the manifest + renderer interface, not the engine count. Engines are plugins; today exactly one plugin is registered.

---

## 1. BUNDLE + BUILD REALITY (measured, not guessed)

### 1.1 Build system and budget semantics (MEASURED from `angular.json` + audit)

- Builder: **`@angular/build:application`** (esbuild-based application builder), Angular **21.1.5**, `outputHashing: all`, production default (`angular.json:52-86`). No webpack.
- Budgets exist ONLY in the production config: `initial` warn **1MB** / error **6MB**; `anyComponentStyle` warn 8kB / error 24kB (`angular.json:64-74`). **There is no `anyScript`/`allScript`/total budget.**
- **"initial" = the initial bundle only** — the files index.html loads eagerly (modulepreload chunks + `main` + global `scripts`). Lazy route chunks are **NOT** counted. Confirmed from the built `dist/frontend/browser/index.html`: 10 `<link rel="modulepreload">` chunks + `scripts-DN54Y2NQ.js` + `main-SAUX6EY3.js` form the initial set; the metrics and resources chunks are absent from it.
- Enforcement is **CLI build-time only**: `maximumError` fails `ng build`; warnings do not. **No per-chunk size assertion exists anywhere in CI** — verified by this audit's own search (§4 of `fe-metrics-source-audit.md`): `test/packs/metrics_fe_dual_side_test.sh:249-268` runs the production build and prints `BUDGET_WARNINGS` as *"RECORDED FINDINGS, not auto-fixed"*; `test/packs/fe_static_typecheck_build_test.sh:11-13` states warnings are *"ADJUDICATION DATA … NOT a pass/fail gate."* No jest spec or script measures built chunk bytes. No `analyze` script exists.
- Therefore: **any chart engine imported only by the lazy `/metrics` or `/resources` page lands in a lazy chunk and escapes BOTH budgets** — measured proof: the metrics lazy chunk today is 27,672 B raw / 8,139 B gz; resources 38,914 B / 10,883 B gz; neither appears in the initial set.

### 1.2 Our current bundle (MEASURED from `dist/frontend/browser/` of the v0.28.9 build)

| Quantity | Raw bytes | gzipped |
|---|---|---|
| **Initial set** (12 files: 10 preloads + main + global scripts) | **4,460,391 (~4.46 MB)** | **1,294,925 (~1.29 MB)** |
| …of which **mermaid global script** (`scripts-DN54Y2NQ.js`, `angular.json:59`) | **3,559,319 (~3.56 MB)** | **972,142 (~972 kB)** |
| `/metrics` lazy chunk | 27,672 | 8,139 |
| `/resources` lazy chunk | 38,914 | 10,883 |
| ALL JS in dist (87 files) | 6,956,222 (~6.96 MB) | 1,930,297 (~1.93 MB) |

Two consequences:

1. **The initial budget is already dominated by mermaid** — a *diagram* renderer loaded as a global script on **every page** (80% of the initial set, 972 kB gz). Against the 1 MB warning we are at ~4.4× (warning fires, non-blocking); against the 6 MB error we have ~1.5 MB raw of headroom. **If bundle weight is a concern, the lever is making mermaid lazy — worth ~972 kB gz — which is larger than the entire four-engine proposal combined.** The chart-engine decision is not the bundle problem; mermaid is.
2. Adding even ALL FOUR engines behind lazy chunks **fails no gate** — there is no total budget and no CI per-chunk assertion. The cost is real but user-visible only on the metrics route (first-visit latency) and in `node_modules` weight.

### 1.3 Engine-by-engine cost (MEASURED = npm registry metadata via `npm view`, 2026-11-03; ESTIMATE = published bundle figures — **no install was performed**)

| Engine | Latest | MEASURED unpackedSize | MEASURED files | MEASURED runtime deps | Last publish | ESTIMATED min+gz (tree-shaken where applicable) |
|---|---|---|---|---|---|---|
| **Apache ECharts** | 6.1.0 | **60.3 MB** | 1,347 | 2 (`tslib`, `zrender` 6.1.0 @ 4.3 MB) | 2026-05-19 | full ~1.0 MB / ~330 kB gz; line+bar tree-shaken ~350–500 kB / **~120–160 kB gz** (ESTIMATE) |
| **ApexCharts** | 8.7.0 | **30.0 MB** | 618 | 1 (`apex-commons` @ 192 kB) | 2026-10-31 | ~500–560 kB / **~150–160 kB gz** (ESTIMATE, v3/v4 class) |
| **Chart.js** | 4.6.1 | **6.2 MB** | 117 | 1 (`@kurkle/color`) | 2026-10-22 | ~200 kB / **~65–68 kB gz** (ESTIMATE) |
| **angular-gridster2** | 22.0.0 | **480 kB** | 6 | 0 (+`tslib`) | 2026-06-26 | ~60–90 kB / ~20 kB gz (ESTIMATE) |
| *Wrapper (Angular glue)* | | | | | | |
| `ngx-echarts` | 22.0.0 (**21.0.0** for our NG21) | 77 kB | — | peers `echarts >=5`, `@angular/core >=21` (at 21.0.0) | 21.0.0: 2025-11-24 | negligible |
| `ng-apexcharts` | 3.2.0 | 121 kB | — | peers `apexcharts ^6‖^7‖^8`, `@angular/core >=20` | 2026-10-09 | negligible |
| `ng2-charts` | 11.0.0 (**10.0.0** for our NG21) | 57 kB | — | peers `chart.js ^3‖^4`, `@angular/core >=21` (at 10.0.0) | 2026-09-29 | negligible |

**Angular-major compatibility (MEASURED from peer ranges):** we are on Angular **21.1.5**. Compatible pins: `ngx-echarts@21.0.0`, `ng-apexcharts@3.2.0`, `ng2-charts@10.0.0`, `angular-gridster2@21.0.1`. The LATEST majors of `angular-gridster2` (22.0.0), `ngx-echarts` (22.0.0) and `ng2-charts` (11.0.0) all peer-require **Angular ≥22** — every one of these libs couples its major to Angular's major, so each adds a **lockstep-upgrade obligation** at every Angular major. Chart.js/ECharts/ApexCharts cores are framework-agnostic (wrappers are the coupled part; all three cores can also be driven without a wrapper).

**⚠ Factual correction to the proposal's stated premise:** `angular2-d3` **does not exist in the npm registry** (`npm view angular2-d3` → E404, verified 2026-11-03). `angular-gridster2` has **no d3 dependency of any kind** — its package metadata lists `dependencies: { tslib }` and `peerDependencies: { @angular/common, @angular/core, rxjs }`; it is pure Angular components. There is no "d3 v7 ESM interop" question for Gridster2. (The only d3 in our tree is *transitive via mermaid*, never imported by app code — audit §4.) The Gridster2 maintenance question stands on its real merits: **single maintainer** (tiberiuzuld), MIT, majors track Angular majors, last publish 2026-06-26 — maintained but **bus-factor 1**.

**Posture guard:** the zero-chart-dependency posture is **enforced by a test**, not convention — `pages/metrics/metrics.bindings.pins.spec.ts:70-98` (the "D2 zero-dependency guard") bans `chart.js`, `d3-`, `from 'd3'`, `ngx-charts`, `moment`, `dayjs` from the metrics FE sources. **Adopting any charting library is a test change first, a package change second.**

---

## 2. WORKLOAD FIT — profiled from the registry + contract with citations

### 2.1 Cadence and cardinality (MEASURED)

- Central sampler tick: `limits.metrics_sampler_interval_s`, default **10 s**, boot-applied (`daemon/services/metrics_query/sampler.py:72`; publisher snapshot cadence ≤15 s, `checkpoint_metrics.py:128` per backend inventory).
- Central **window: hard-capped at 2 h** — `WINDOW_SECONDS: int = 7200` and the request path clamps `window = max(1, min(window, WINDOW_SECONDS))` (`metrics_query/constants.py:33`, `envelope.py:201-202`). A client cannot ask for more.
- Central **points per series: hard-capped at 240** — `DEFAULT_MAX_POINTS = 240` and `MAX_POINTS_CEILING = 240` ("a request ABOVE the ceiling is CLAMPED down to it — never honoured", `constants.py:39-56`).
- Central **per-response ceilings:** `MAX_NAMES = 50` selectors, `MAX_SERIES = 500`, `MAX_TOTAL_POINTS = 12_000`, `RESPONSE_BYTE_CEILING = 1 MiB` (`constants.py:59-91`).
- Registry-wide series cardinality: 16 families in the v1 catalog, 10 live / 6 dark, **99 live series / 112 snapshot series** observed; catalog budget ≤2,000 series (MA-10).
- Resources tab (tab 1): sampler tick **30 s** (clamp 15–60), flush 60 s; **retention ladder raw 48 h / minute 14 d / hour 180 d** (`resource_monitor/flusher.py:360-368`; `daemon/config.py:811-827`); FE window picker exposes **15m/1h/24h/7d** with tier predicate raw(30 s)/minute(60 s)/hour(3600 s) (`routers/resources.py:503-541`); FE polls live at 8 s.
- Central FE: Overview 10 s / Charts 15 s polls, window hardcoded `WINDOW_S = 3600`, `max_points` left at the 240 default (`charts-tab.component.ts:192-195,:289-292`).

### 2.2 Actual point counts (derived from the measured cadences/ceilings)

| Panel | Window | Resolution | Points/series | Series | **Points/panel** |
|---|---|---|---|---|---|
| Central Charts tab (any family) | 1 h (2 h max) | 10–15 s, clamped to **240** | ≤240 | 1 per small-multiple | **≤240** |
| Central Charts tab, worst legal request | 2 h | clamped | 240 | ≤50 | **≤12,000 (server ceiling)** |
| Resources timeline | 15 m | raw 30 s | 30 | 5 | **150** |
| Resources timeline | 1 h | raw 30 s | 120 | 5 | **600** |
| Resources timeline (largest today) | 24 h | minute 60 s | 1,440 | 5 | **7,200** |
| Resources timeline | 7 d | hour 3600 s | 168 | 5 | **840** |
| Resources ladder, theoretical max (not exposed by picker) | 14 d / 180 d | minute / hour | 20,160 / 4,320 | 5 | ≤21,600 (14 d) / 21,600 (180 d) |

### 2.3 Verdict — is the ECharts tier empty?

**Yes. Empty at our volume.** Canvas-class engines (ECharts/zrender) earn their cost at ~10⁵–10⁶ points (progressive rendering, large-series dataZoom, brushing). Our **hard server ceilings** make those numbers unreachable: the central door can never emit more than 240 points/series or 12,000 points/response or a 2 h window (`constants.py:29-91` — frozen constants, deliberately "NOT long history — the Prometheus TSDB owns that" at `:31-33`); the resources door's largest offered panel is 7,200 points and even the unexposed far end of the ladder is ~21,600. That is **1–2 orders of magnitude below** canvas territory. SVG renders 20k path points without measurable strain; we already ship proof — the 24 h minute-tier panel works today on the hand-rolled 538-line engine.

Two semantic facts further shrink what a heavy engine would buy:

1. **Small multiples, not multi-axis overlays, is our rendering shape.** Both house primitives deliberately normalize each series to its own observed [min,max] — a stated data-honesty decision ("a linear interpolation between counter samples would assert values that were never observed", `svg-line-chart.component.ts:22-47`; same note in `svg-timeline.component.ts:29-33`). ECharts/Apex/Chart.js all default to a shared y-axis per chart; reproducing our semantics means one chart instance per series — exactly what the Charts tab already does. The engine would be configured INTO our existing shape, not the reverse.
2. **STEP mode is already solved** (`buildChartStepPath`, counter honesty per contract §4.3 `chart_role`) — and natively supported by ECharts/Chart.js too, so it is not a differentiator either way.

**Interactions — required vs free:**

| Interaction | Required by current/planned tabs? | Library gives free? | Notes |
|---|---|---|---|
| Window picker (discrete) | **Yes — tab 1** (15m/1h/24h/7d; ladder to 180 d) | No — tab-level concern in any engine | Already built; the backend tier ladder IS the zoom model |
| Series toggles | **Yes — tab 1** (5 series) | Partial (legend click) | Already built |
| Legend + per-series min–max | Yes (both primitives) | Partial | Our legend prints observed range — custom in every lib |
| Native `<title>` tooltips | Yes (both) | Superseded by crosshair | Crosshair is the one genuinely missing polish item |
| Crosshair + numeric y-scale | **Nice-to-have** (both primitives are deliberate "shape views") | **Yes** | The ONLY interaction a library buys us today |
| Anomaly markers + click→drill-down | **Yes — tab 1** (exists: `svg-timeline.component.ts:235-267`) | No (markPoint/annotation plugins — Chart.js needs an extra plugin dep) | Already built; custom in every engine |
| Anomaly bands | Plausible (tab 1) | Partial (markArea) | Small SVG addition |
| dataZoom / brush | **No current surface** (only the 180 d ladder makes it interesting, and that is 4,320 pts/series — trivial) | Yes (ECharts/Apex) | Solves a problem our ceilings forbid |
| Very large point counts | **Structurally impossible** (240/12 k/2 h hard clamps) | Yes | — |

**Do minis need Chart.js?** No — the mini tier has **no surface today**: the Overview tab renders stat cards, not charts (`overview-tab.component.ts:74-90`), the Live tab renders stat cards, and a sparkline is ~30 lines of SVG using the primitive's already-exported pure helpers. Chart.js would add a third interaction language and a second tooltip system to render fewer pixels than the CSS we'd write for it.

---

## 3. TAB-CONTRACT IMPACT — manifest shape

Today both pages use an explicitly **local section registry** (`resources.component.ts:43-48` "Local section registry — no shared cross-page registry"; `metrics.component.ts:83-88`, which cites the resources pattern as its precedent). The refactor's shared TAB MANIFEST replaces these. Two shapes:

### (A) Per-engine config schemas — tab declares `{ engine: 'echarts', options: EChartsOption } | { engine: 'svg', ... } | …`

- **Schema sketch:** `interface TabDef { id: string; label: string; panels: Array<{ engine: 'svg'|'echarts'|'apex'|'chartjs'; config: any /* engine-specific */ }>; }` — a discriminated union keyed on engine.
- **Adding a later engine:** additive at runtime, **breaking at the type level** — the union grows, every manifest consumer (validation, defaults, tests, docs) must learn the new variant; tabs written against a dead engine become un-renderable.
- **Per-renderer translation cost:** zero (config passes through).
- **Fallback when an intent isn't expressible:** **none** — an entry IS an engine config; there is no neutral form to re-render with another renderer. If the engine is absent (lazy chunk failed to load, engine removed), the tab shows an error, not a chart.
- **Lock-in:** tabs are coupled to engines forever; replacing an engine rewrites every tab entry.

### (B) Engine-agnostic chart-INTENT spec + pluggable renderers (RECOMMENDED)

- **Schema sketch:**
  ```ts
  interface PanelIntent {
    kind: 'timeseries';                       // additive union — 'stat-cards', 'table', … later
    series: Array<{ id: string; label: string; color: string; mode: 'line'|'step' }>;
    source: { endpoint: 'metrics-series'|'resources-history'; window: number; maxPoints?: number };
    markers?: Array<{ t: number; severity: 'critical'|'warning'; onClick: 'drill-down' }>;
    axes: 'normalized-per-series' | 'shared'; // our honesty semantics as DATA
    interactions: Array<'series-toggle'|'window-picker'|'tooltip-native'|'crosshair'>;
  }
  interface ChartRenderer { id: string; priority: number; canRender(intent): boolean; render(...) }
  ```
  The tab manifest declares **what the panel means**; the shell resolves the highest-priority registered renderer whose `canRender(intent)` is true. The SVG primitive registers as renderer #1 (priority 0, always present, eager — it is already in the chunk).
- **Adding a later engine:** **purely additive** — a new `ChartRenderer` class + registration (behind a dynamic `import()` so the engine's bytes land in its own lazy chunk). Tabs never change. If the new engine unlocks new intent kinds, the intent union extends additively.
- **Per-renderer translation cost:** each engine pays its own intent→options translator (unavoidable in ANY multi-engine world; with one renderer today, the cost is paid once, trivially).
- **Fallback rule (must be explicit, never silent):** if no registered renderer fully supports an intent, degrade to the baseline SVG renderer AND disclose — render with a visible badge/note listing the dropped interactions (e.g. "crosshair unavailable"). If even the baseline cannot render the intent kind, render an explicit "unsupported panel" card. Never a blank area, never a silent capability drop.

**Recommendation: (B).** It is the only shape that makes the user's "each chart/layout/module may pick one or many" true without coupling tab definitions to engine names, and it is the only one with a defined fallback. (A) is cheaper on day one by exactly the cost of one translator — and pays that saving back as a rewrite the first time any engine changes.

---

## 4. UX CONSISTENCY

- **One interaction language today:** both primitives share a coherent model — UTC-only axes, native `<title>` tooltips, per-series legend with observed range, click-marker→drill-down. A developer moving between tabs learns ONE language.
- **Adding engines fragments it:** ECharts, ApexCharts and Chart.js each bring their own tooltip component, legend behavior, gesture set, animation defaults and accessibility story. Three engines on one page's tabs = three languages; the 4-engine proposal would put up to **four** (SVG + 3) on a single page. If a second engine EVER registers, confine it to the tab that needs it (tab 1 long-range) so the page never carries more than two.
- **Theming:** our charts are hand-rolled SVG styled with the FE's design system (Material 3 `--mat-sys-*` tokens, dark scheme, ng-zorro-antd coexistence — visible in the built `index.html` inline token block). SVG binds to CSS variables directly. All three libraries theme through **JavaScript option objects** (ECharts theme objects, Apex `theme` options, Chart.js plugin config) — each would need a generated theme kept in sync with the token set, a standing drift surface. This cost is per-engine and permanent.
- **The genuinely missing polish** (crosshair, numeric y-scale) is implementable in the SVG primitive in ~100–150 lines using the already-exported pure helpers — without importing an interaction language to get two features.

---

## 5. GRIDSTER2 — roadmap question, not a library pick

The settled direction is **predefined tabs with fixed layout; NO composable drag/drop dashboard in the roadmap**. Gridster2's entire value is user drag/resize/swap grid composition.

**Either/or with costs:**

- **Predefined tabs now (our direction) → DROP Gridster2.** Its core value is unused by construction; the zero-dep alternative is a fixed CSS-grid layout, and we already have a zero-dep SVG precedent plus an existing `@angular/cdk` dependency (whose `DragDrop`+grid covers any future escape). Adoption cost is small in bytes (480 kB unpacked / ~20 kB gz) but real in obligations: `angular-gridster2@21.0.1` peer-pins Angular ^21 while latest (22.0.0) needs Angular 22 — a **lockstep major-coupling to a single-maintainer (bus-factor 1) library**, for a feature we would not turn on.
- **Composable dashboards someday → defer; cost of deferring ~zero.** The library is maintained (publishes per Angular major, last 2026-06-26) and tiny; a future composable-dashboard lane would integrate it fresh against the then-current Angular at the same effort as today. Deferral risks: (i) the lib goes unmaintained — hedged by `@angular/cdk` (already a dep) as the substitute; (ii) the tab manifest would need a layout-composition extension — but that is true whenever the feature starts, and intent-shape (B) absorbs it as an additive `layout: 'fixed-grid' | 'composable'` field.
- **Crisp answer: drop it now; revisit only when a composable dashboard is a committed roadmap item. Deferring a year costs approximately nothing and keeps a bus-factor-1 dep off our upgrade critical path.**

---

## 6. THE RECOMMENDATION (one)

**Reduced set — build the chart-INTENT tab manifest (shape B) with the consolidated SVG primitives (the existing 523-line `svg-line-chart` + 538-line `svg-timeline`, unified into one renderer) as the ONLY registered renderer today; add the crosshair + numeric y-axis to that renderer (~150 lines); register additional engines later, each behind the same seam and confined to its lazy chunk, exactly when its trigger fires:**

| Engine | Disposition | Trigger (when an X surface actually exists) |
|---|---|---|
| SVG primitive (consolidated) | **STAYS** — renderer #1, priority 0, eager | — (always registered; the fallback) |
| **Apache ECharts** | **DEFER** | A committed panel class needing canvas-scale interaction at volumes our ceilings allow to grow (≥ ~50 k points/panel after a ring-growth lane lifts `WINDOW_SECONDS`/`MAX_POINTS_CEILING`), or a product decision that built-in crosshair/dataZoom polish across many series is wanted NOW — then register for tab 1 only, via `ngx-echarts@21`, dynamic-import, tree-shaken line+step (~120–160 kB gz lazy) |
| **ApexCharts** | **DEFER** | A committed aesthetic/report/export surface (shareable rendered report pages) — none exists in the roadmap |
| **Chart.js** | **DEFER — likely never** | A committed mini/sparkline tier — today that tier has no surface (Overview/Live are stat cards), and sparklines are ~30 lines of SVG with the existing exported helpers |
| **angular-gridster2** | **DROP** | Only if a composable drag/drop dashboard becomes a committed roadmap item (currently explicitly not); `@angular/cdk` is the hedged fallback |

This preserves the user's seam instinct (the manifest + `ChartRenderer` interface IS the seam; adding an engine is an additive registration, never a rewrite), costs ~0 new bytes, keeps one interaction language, keeps CSS-var theming, and leaves every door open with written triggers instead of speculative weight.

**If the user weights built-in interaction polish (crosshair/zoom/legend animations) for tab 1 above minimalism:** the acceptable variant is *SVG minis + ECharts for tab-1 panels only* (the second row's trigger fired early) — measured cost ~120–160 kB gz in tab 1's lazy chunk, no budget-gate impact, one added interaction language confined to one tab. That variant is defensible; the 4-engine version is not.

---

## 7. THE SINGLE DECISION NEEDED FROM THE USER

**Do you accept engine count = 1 (consolidated SVG) behind the intent-manifest seam with written triggers for the other three — or do you want ECharts registered for tab 1 now as interaction polish, accepting ~120–160 kB gz lazy + one JS-theme layer + the D2 guard test change?** (Everything else — Gridster2 drop, ApexCharts/Chart.js deferral, manifest shape B — follows from the evidence and does not need a ruling.)

## 8. WHAT I COULD NOT VERIFY

1. **min+gzip bundle figures are ESTIMATES** from published bundle-size classes (echarts v5/v6, chart.js v4, apexcharts v3/v4); no `npm install`/build was permitted. Registry `unpackedSize`/`fileCount`/`peerDependencies`/publish dates are MEASURED via `npm view` (2026-11-03). ECharts 6 / ApexCharts 8 exact shipped-tree-shaken sizes could differ by ±30%.
2. **Angular budget raw-vs-gz semantics**: I verified the budget declaration and which files land in the initial set (from the built `index.html`); the CLI's internal size comparator (raw file bytes) was not re-derived from Angular 21 source. The measured raw totals (4.46 MB initial) sit between the 1 MB warning and 6 MB error either way, so the conclusion is insensitive to this.
3. **The prompt's `angular2-d3` premise** could not be verified because it is **false** — the package 404s in the registry and Gridster2 has no d3 dependency (verified via registry metadata). I flag this rather than evaluate a phantom.
4. **Runtime rendering performance of the 24 h minute-tier panel (7,200 pts)** is inferred from its shipped, working status plus SVG norms — no profiling run was performed (build/run outside this commission's read-only scope).
5. **The tab-manifest refactor's own planned shape** — deliberately not read (`resource-refactor-architecture.md` excluded to stay independent); the A/B comparison above is derived from the existing local-registry pattern (`resources.component.ts:43-48`, `metrics.component.ts:83-88`) and the settled product context.
