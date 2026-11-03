# FE /metrics source audit — what actually shipped

> **Deliverable 1(a)** of the metrics-UI audit commission. Read-only source audit of the
> Angular `/metrics` page (the `central-metrics-v1` lane, release v0.28.6; FE deltas in
> v0.28.9). Companion excerpts: [`fe-metrics-source-audit.excerpts.md`](fe-metrics-source-audit.excerpts.md).
>
> **Audited by:** wanderer (fe-metrics-source-audit). **Date:** 2026-11-03.

## 0. PROVENANCE — where the source actually lives (read this first)

**The main checkout `/home/nea/ensemble-src` does NOT contain the `/metrics` page.** Its HEAD
is `013895287` (a v0.27.8-era commit, ~300 commits behind `origin/latest` @ `c4161335b`).
`frontend/src/app/pages/metrics/` does not exist there, and `app.routes.ts` at that HEAD has
no `metrics` route. Any audit run against the main checkout alone would wrongly conclude
"the page does not exist."

Audited tree: **`/home/nea/ensemble-src-wt-stage-v0289/frontend/`** (HEAD `7fcf1bd71` = the
v0.28.9 tag target). Verified for the five core metrics files by sha256 `MATCH` against
`git show origin/latest:<path>`, and by the census worker for the whole `frontend/` tree
(`git diff --stat 7fcf1bd71 origin/latest -- frontend/` → empty).

Chronology (from git objects at `origin/latest`):
- `/metrics` FE added in commit `1e633cb8d` — *"feat(metrics): the FE charts page — the
  model mirror, the service, the SVG primitive and the three tabs (P5 5.1-5.5)"*, 2026-11-02
  — the `central-metrics-v1` lane, shipped in **v0.28.6** (`CHANGELOG.md:134-141`, which also
  states **"FE `package.json` empty"** at `:141`).
- The chart primitive `svg-line-chart.component.ts` is **unchanged since `1e633cb8d`**.
- The only post-v0.28.6 metrics-FE delta is the v0.28.9 registry-degraded mirror:
  `git diff fa1817efa..7fcf1bd71 -- frontend/src/app/{pages/metrics,components/charts,…}`
  → `metrics.component.{ts,html,scss}` + `metrics.model.ts` (+213/−5); **the chart primitive
  and all three tab bodies are untouched.**

All `file:line` citations below refer to the audited tree (paths relative to `frontend/src/app/`).

---

## 1. ROUTE + PAGE STRUCTURE

### Route
- `app.routes.ts:58` — `{ path: 'metrics', loadComponent: () => import('./pages/metrics/metrics.component').then(m => m.MetricsComponent), title: 'Metrics' }`. Lazy `loadComponent`, static path, no guard. Placed deliberately before the `skills/:id` family (`:55-57` comment).

### Page shell
- `pages/metrics/metrics.component.ts` (256 lines) — the shell; `metrics.component.html` (60), `.scss` (90).
- Local section registry, `metrics.component.ts:83-88`:
  - `overview` → **Overview** → `overview/overview-tab.component.ts` (300 lines)
  - `charts` → **Charts** → `charts/charts-tab.component.ts` (316 lines)
  - `families` → **Families** → `families/families-tab.component.ts` (249 lines)
- Tab bodies are mounted lazily via Material `mat-tab-group` + `NgComponentOutlet`
  (`metrics.component.html:50-58`); the shell passes each body an `active` flag and a
  non-selected body **stops its poll** (`metrics.component.ts:60-62`, `:177-197`).
- Shell owns the family catalog — **one load, not a poll** (`metrics.component.ts:199-201`,
  `:212-250`; doc `docs/architecture/metrics-fe-charts.md:52-53`).

### What each tab renders
- **Overview** — stat cards of the *current* values (`GET /api/metrics/snapshot`, **10 s**
  poll): name + labels + value + `kind · published Ns ago` per card, in an auto-fill grid
  (`overview-tab.component.ts:74-90`; styles `:153-160`). Envelope meta line prints schema /
  vocabulary / restart epoch (`:67-72`). **The snapshot request carries NO query parameters
  at all** — `getSnapshot()` takes no options (`services/metrics.service.ts:55-60`), call site
  `overview-tab.component.ts:276`. There is no pagination, no filter, no selector on the
  snapshot route from the FE side.
- **Charts** — the windowed trend (`GET /api/metrics/series`, **15 s** poll, **window
  hardcoded to 3600 s**): one small-multiple SVG chart per returned series
  (`charts-tab.component.ts:94-121`).
- **Families** — the catalog **as a table** (`GET /api/metrics/families`, on demand): Family /
  Role / Availability (live|dark pill + reason) / Series count / Names chips with declared
  label vocabularies (`families-tab.component.ts:45-90`). **This tab owns NO network** — it
  holds no injected service and makes no `MetricsService` call; the catalog arrives as an
  input (`readonly families = input<readonly MetricsFamily[]>([])`, `families-tab.component.ts:205`)
  and the shell owns the fetch (`metrics.component.ts:214-215`). Its only interaction is a
  **Reload** button that calls back into the shell (`families-tab.component.ts:42,:217,:220-225`).
  No chart renders here (nor in Overview — only the Charts tab renders SVG).

### Navigation
- **Gear-menu only.** `app.ts:597` — `{ label: 'Metrics', icon: 'query_stats', route: '/metrics' }`.
- It is the **LAST static** entry of `settingsMenuItems` (`app.ts:566-598`). Immediately
  before: `MCP Gateway` (`app.ts:589`). Immediately after: the **conditionally-appended**
  `Database` (`app.ts:798-801`, gated on `postgres_env_set`) and `Maintenance`
  (`app.ts:842-846`, gated on `state === 'ready'`).
- Unconditional: the entry is static because *"the query door is never switch-gated"* (`app.ts:595-596`).
- **No header-nav link** — pinned by `pages/metrics/metrics-gear-menu.spec.ts:55-58`
  (asserts `app.html` contains no `'/metrics'`). Menu rendered at `app.html:78-87`.
- Ordering pinned by `metrics-gear-menu.spec.ts:30-45` (Resources → MCP Gateway → Metrics → conditional appends).
- For contrast: `/resources` is `app.ts:580` — `{ label: 'Resources', icon: 'insights', route: '/resources' }`, three positions earlier, also static, also gear-menu-only.

---

## 2. CHART PRIMITIVES — the real implementation

### Where + how big
- **`components/charts/svg-line-chart/svg-line-chart.component.ts` — 523 lines**, standalone,
  OnPush, zero imports beyond `@angular/core` + the metrics model (`:1-7`). One file, no
  template URL (inline template `:279-371`, inline styles `:372-439`).
- Consumer: `pages/metrics/charts/charts-tab.component.ts:316` in total. **Chart code = 839
  lines** (523 primitive + 316 tab). The whole metrics FE surface (non-spec) is **2,324
  lines** (page shell 256 + 3 tabs 865 + html 60 + scss 90 + primitive 523 + service 88 + model 442).

### Props / type signature
Two **inputs**, **zero outputs** (`:443-446`):
```ts
readonly series = input<readonly ChartSeries[]>([]);
readonly ariaLabel = input<string>('Metric trend (UTC)');
```
Input shape (`:52-65`): `ChartSeries { id, label, color, mode: 'line'|'step', points: readonly ChartPoint[] }`
where `ChartPoint { t: epoch-ms, v: number }`. **There is no input for** time range, thresholds,
comparison series, visibility toggles, units, or y-domain; **no output for** click/hover/select.

### Rendering approach — hand-rolled `<path>`, no scale lib
- **Hand-rolled `<path d="…">` strings built by exported pure functions** (`:176-229`):
  `buildChartLinePath` → `M x,y L x,y …`; `buildChartStepPath` → `M x,y H x V y …`
  (**step-after** semantics — the observed value holds until the next sample);
  `buildChartAreaPath` → line/step path closed to the plot floor (`L…,PLOT_BOTTOM L…,PLOT_BOTTOM Z`).
- **No `<polyline>`, no `<rect>`**, no d3 / d3-scale / any scale library. Scales are two
  ~20-line pure functions: `chartXScale` (`:108-116`, time→x) and `chartYScale` (`:124-132`, value→y).
- **Axis/gridlines:** `chartUtcTicks` (`:232-247`) produces 5 evenly-spaced vertical
  gridlines (`<line>` at `:303-310`) + `<text>` tick labels (`:311-319`). **Vertical-only** —
  there are **no horizontal gridlines and no y-axis value labels anywhere in the SVG.** The
  only vertical reference is the legend's observed min–max range (`:286-288`).
- **Legend** (`:280-292`): static per-series row — color swatch, label, and
  `min–max (sampleCount)`. **Not clickable, no toggle.**
- **Tooltips:** native SVG `<title>` only. Per sample: `<circle r=2.2>` + `<title>` whose text
  is `chartPointTooltip` = `"HH:MM UTC · value"` (`:269-271`, rendered `:341-353`). The line
  path itself carries only the series *label* as `<title>` (`:339`). No crosshair, no shared
  hover readout, no units.
- **Area fill** at opacity 0.16 (`:430-433`); line `stroke-width: 1.6` (`:425-428`).
- **Per-series y-normalisation** — the load-bearing design choice (`:32-35`, `:118-123`):
  every series is scaled to **its own observed [min,max]**, so the chart is explicitly a
  *"SHAPE view — the numeric truth lives in the Overview tab's stat cards."*
- **Feature-frozen by declaration** (`:24-26`): *"FEATURE-FROZEN: zoom / brush / crosshair /
  gradients are OUT"* — restated in `docs/architecture/metrics-fe-charts.md:130-133`.
- **Empty state** inside the SVG: `"No samples in window"` (`:356-366`).

### Shared or bespoke?
- The primitive lives under `components/charts/` (shared **placement**) but has **exactly ONE
  consumer** — `charts-tab.component.ts:27,:69,:111-114` (grep-verified: no other import of
  `SvgLineChartComponent` / `app-svg-line-chart` in the app).
- **A second, separate hand-rolled SVG chart engine already exists**:
  `pages/resources/svg-timeline/svg-timeline.component.ts` (**538 lines**, added in commit
  `40c252150`, v0.25.0-era, last modified `dae97a500` 2026-10-19). It ships **its own
  duplicated scale/path helpers** (`timelineXScale`/`timelineYScale` `:95-113`,
  `buildLinePath`/`buildAreaPath` `:122-152`) with **no shared abstraction** between the two.
  So: *within* the metrics page the primitive is shared; *across the app* there are **two
  divergent zero-dep chart engines, ~1,061 lines total, with duplicated helpers.**

---

## 3. CHART CAPABILITY AUDIT

| Capability | Verdict | Evidence |
|---|---|---|
| **Time-range selection** | **ABSENT** | Window is a hardcoded constant: `charts-tab.component.ts:195` `readonly WINDOW_S = 3600;`, passed at `:291`. No selector anywhere (grep `range/select/option/preset/1h/6h/24h/7d` over `pages/metrics/` + `components/charts/` → only that constant and unrelated identifiers). The user cannot choose 15m/1h/24h/7d/custom. **The FE never asks for more than 60 minutes of history.** Contrast: the older resources timeline ships `TIMELINE_WINDOWS = ['15m','1h','24h','7d']` + `selectWindow()` (`pages/resources/timeline/timeline-tab.component.ts:60`, `:125`). |
| **Comparison-to-baseline** | **ABSENT** | grep `threshold\|anomal\|baseline\|compare\|deviation\|delta_change\|alert` over `pages/metrics/` + `components/charts/` + `models/metrics.model.ts` → **0 hits**. The service exposes no compare parameter (`services/metrics.service.ts:70-87` — only `names`, `windowS`, `maxPoints`). The BE's `delta_change` exists on the series door but the FE never surfaces it. |
| **Multi-series overlay** | **PARTIAL** | The primitive's input *is* an array (`:443`) and the legend renders every entry (`:280-292`), so a multi-series chart is technically renderable. But: (a) the only consumer passes exactly one — `charts-tab.component.ts:112` `[series]="[chart]"`, one small multiple per returned series; (b) y is normalised **per series** (`:124-132`), so two overlaid series would sit on incomparable vertical scales — the design *forces* small multiples; (c) no legend toggle / per-series visibility input exists anywhere. |
| **Drill-down (chart → entity)** | **ABSENT** | grep `routerLink\|navigate\|href=\|window.open` over `pages/metrics/` + `components/charts/` → **0 hits**. The chart component has **zero click/hover handlers** (grep `(click)\|(mouseenter)\|(mousemove)\|@HostListener` → the only 4 hits are the tabs' Retry/Reload buttons). A point cannot lead to an instance, job, or event. Contrast: the older resources chart has `(click)="onMarkerClick(…)"` (`svg-timeline.component.ts:240`) → `markerClick = output<TimelineMarkerClick>()` (`:392`) → `timeline-tab.component.ts:185-199` `forkJoin({evidence, traceback})` drill-down. |
| **Anomaly spotting** | **ABSENT** | No marker/threshold/band/deviation concept in the metrics FE (same 0-hit grep as baseline). `ChartMarker` (`:78-83`) is a per-sample tooltip dot, not an anomaly marker. The `/resources` page's anomalies (`/api/resources/anomalies`) have no `/metrics` counterpart. |
| **Export (CSV/JSON/PNG/clipboard)** | **ABSENT** | grep `csv\|clipboard\|download\|toBlob\|createObjectURL` over the metrics surface → only TypeScript `export` keyword hits. No data export of any kind; the raw JSON is reachable only via devtools. |
| **Zoom / pan / brush** | **ABSENT** | Explicitly frozen out: `svg-line-chart.component.ts:24-26` *"zoom / brush / crosshair / gradients are OUT"*; `docs/architecture/metrics-fe-charts.md:130-133`. No wheel/brush/pointer handlers exist. |
| **Tooltips with values** | **PARTIAL** | Native `<title>` per sample point = `"HH:MM UTC · value"` (`:269-271`, `:351`). Deficits: native browser tooltip (not a styled readout); **no units** (`formatChartValue` `:258-266` is a bare number — no ms, bytes, %, count); no aggregation/rollup readout; tooltip exists only on the point circles, not along the line; **no y-axis labels**, so reading a value off the shape is impossible — you must consult the legend range or switch to the Overview tab. |

---

## 4. DEPENDENCY / POSTURE CENSUS

### `frontend/package.json` — complete list (33 deps, 12 devDeps)
**dependencies** (`package.json:27-61`): `@angular/{animations ^21.1.5, cdk ~21.2.5, common ^21.2.5, compiler ^21.2.5, core ^21.2.5, forms ^21.2.5, material ~21.2.5, platform-browser ^21.2.5, platform-browser-dynamic ^21.2.5, router ^21.1.0}` · `@codemirror/{commands, lang-css, lang-html, lang-javascript, lang-json, lang-markdown, lang-python, lang-sql, lang-yaml, language, legacy-modes, merge, state, theme-one-dark, view}` · `marked ^17.0.4` · **`mermaid ^11.4.0`** · `ng-zorro-antd ^21.2.1` · `ngx-markdown ^21.1.0` · `playwright ^1.60.0` · `rxjs ~7.8.0` · `tslib ^2.3.0` · `zone.js ~0.16.1`.

**devDependencies** (`package.json:62-75`): `@angular/build ^21.2.5` · `@angular/cli ^21.2.5` · `@angular/compiler-cli ^21.2.5` · `@angular/material ^21.2.5` · `@axe-core/playwright ^4.13.0` · `@playwright/test ^1.60.0` · `@types/jest ^30.0.0` · `jest ^30.3.0` · `jest-environment-jsdom ^30.3.0` · `jest-preset-angular ^16.1.2` · `ts-jest ^29.4.9` · `typescript ~5.9.2`.

### Charting-library verdict: **NONE as a direct dependency**
- **Absent** (exact-string grep of `package.json` + lockfile `node_modules/*` keys): `chart.js`, `ng2-charts`, `recharts`, `echarts`, `@visx/*`, `plotly`, `uplot`, `lightweight-charts`, `victory`, `@nivo/*`, `apexcharts`, `highcharts`, `chartist`, `frappe-charts`, `ngx-charts`, `svg-charts`, `billboard.js`, `rickshaw`, `dygraphs`.
- **`d3` present only TRANSITIVELY, via mermaid** (`package-lock.json:10884-10885` mermaid → `d3 ^7.9.0`, `d3-sankey`; also dagre-d3-es → d3). Never imported by app code.
- **`mermaid ^11.4.0`** (`package.json:54`) is a *diagram* renderer (bundled as a global script, `angular.json:59`), not a charting library.
- **The zero-dep posture is ENFORCED BY A TEST**, not just convention:
  `pages/metrics/metrics.bindings.pins.spec.ts:70-98` — *"the D2 zero-dependency guard"* —
  asserts the metrics FE sources contain none of `chart.js`, `d3-`, `from 'd3'`,
  `ngx-charts`, `moment`, `dayjs`. **Consequence: adopting a charting library for /metrics is
  a test change first, a package change second.**
- Release-gate corroboration: `CHANGELOG.md:141` (v0.28.6) *"FE `package.json` empty"*;
  `docs/architecture/metrics-promote-disclosure.md:122` *"the FE adds no npm dependency — the
  charts are a hand-rolled SVG primitive"*.

### Bundle-size budget
- **Declaration (the only one):** `angular.json:64-75`, production config only —
  `initial`: `maximumWarning "1MB"`, `maximumError "6MB"` (`:65-69`);
  `anyComponentStyle`: `maximumWarning "8kB"`, `maximumError "24kB"` (`:70-74`).
  The development config (`:78-82`) has **no budgets**; `defaultConfiguration: "production"` (`:84`).
- **Enforcement:** the Angular CLI **at build time** (`ng build --configuration production`)
  — `maximumError` fails the build, warnings do not. **There is no per-chunk / per-file byte
  budget and no jest spec or script that measures built chunk bytes** (searched: whole
  `frontend/` excluding `node_modules|dist|.angular|.vite` for
  `budget|maxSize|size-budget|bundle-size|8192|8000|8kB|24kB|chunk|statSync|readFileSync`;
  the only budget-ish hits are e2e *wall-clock* budgets and Playwright timeouts;
  `frontend/scripts/` holds 8 files, none size-related).
- Repo-level (outside `frontend/`), **recorded not gated**: `test/packs/metrics_fe_dual_side_test.sh:249-268`
  runs the production build and prints `BUDGET_WARNINGS` as *"RECORDED FINDINGS, not
  auto-fixed"*; only a non-zero build exit fails. `test/packs/fe_static_typecheck_build_test.sh:11-13`
  states warnings are *"ADJUDICATION DATA … NOT a pass/fail gate."*
- Scripts (`package.json:4-12`): `ng`, `start`, **`build: ng build`**, `watch`, `test: jest`,
  `e2e`, `e2e:headed`. **No `analyze` script / bundle-analyzer dep / analyze target.**

---

## 5. DATA FETCHING + DEGRADATION

### Fetching
- One typed service, three relative URLs (`services/metrics.service.ts:37-39`):
  `FAMILIES_URL = '/api/metrics/families'`, `SNAPSHOT_URL = '/api/metrics/snapshot'`,
  `SERIES_URL = '/api/metrics/series'`. Same-origin only; **the page never polls the raw
  exposition listener `:9798`** (pinned: `metrics.bindings.pins.spec.ts:64-68`; e2e
  `e2e/metrics-gate.spec.ts:322`).
- `JSON_HEADERS = { Accept: 'application/json' }` (`:45`) is load-bearing: the auth
  middleware **302-redirects a wildcard-Accept request** to an HTML login page (`:25-29`).
- Query params (`getSeries`, `:70-87`): `names` (repeated param), `window_s`, `max_points`.
  The Charts tab passes `names = all chart-primary names` and `window_s = 3600`
  (`charts-tab.component.ts:289-292`) — `max_points` is left at the door default (240).
- **Poll intervals:** Overview **10 s** (`POLL_INTERVAL_MS = 10_000`, pinned
  `metrics.bindings.pins.spec.ts:60`); Charts **15 s** (`charts-tab.component.ts:192`, pinned
  `:61`); Families **on demand** (shell loads once at `ngOnInit`, `metrics.component.ts:199-201`).
- **Polling discipline (genuinely good):** tab-not-selected gate (`charts-tab:264`,
  `overview-tab:251`), browser `visibilitychange` gate (`charts-tab:207,:213`), skip-if-in-flight
  (`:208,:283`), **monotonic supersede token** so a slow earlier response can never overwrite a
  newer one (`:209,:287,:297`), `clearInterval` teardown (`:275-280`).

### Errors (401/403/503/network)
- `decodeMetricsDoorError` (`models/metrics.model.ts:416-442`) decodes the **body before the
  status** (`:428-441`) into 5 classes with distinct copy (`METRICS_DOOR_ERROR_COPY`, `:384-393`):
  - 403 + `SCOPE_INSUFFICIENT` → *"…the signed-in token lacks the maintenance:read scope."*
  - 503 + `AUTH_NOT_CONFIGURED` → *"The metrics door is fail-closed — auth is not configured on the daemon…"*
  - 503 + `registry_unavailable` → *"no provider wired…"*
  - `status === 0` → *"The metrics door is unreachable."*
  - anything else (401/400/404/proxied HTML) → honest *"unexpected error"* fallback.
- Every error branch **retains the last-good view**: charts sets `error` and
  `stale.set(payload !== null)` while keeping `payload` (`charts-tab.component.ts:304-313`);
  the shell does the same for the catalog (`metrics.component.ts:238-248`). Retry CTA on every
  banner (`charts-tab:76-78`, `overview-tab:54-56`, `metrics.component.html:12-19`).

### Stale / dark / empty states
- **STALE badges** (three levels): catalog `"STALE — last known catalog"`
  (`metrics.component.html:23-25`), values `"STALE — last known values"`
  (`overview-tab.component.ts:60-62`), trend `"STALE — last known trend"`
  (`charts-tab.component.ts:82-84`).
- **Age is printed on the Charts meta line**: `window from→to · interval Ns (source) · newest
  sample Ns old` (`charts-tab.component.ts:95-105`) — so chart staleness *is* quantified there.
- **DARK family** → surfaced **only in the Families tab** as a `dark` pill + humanised reason
  (`families-tab.component.ts:64-69`; `formatAvailabilityReason` `models/metrics.model.ts:294-312`).
  In the Charts tab a dark family simply returns no series ⇒ **no chart cell and no
  explanation** — the chart grid is driven by returned series (`charts-tab.component.ts:223`).
- **Empty panel** `"No metrics available yet"` (`METRICS_EMPTY_PANEL_COPY`,
  `metrics.component.ts:50`; panel `metrics.component.html:39-47`) fires exactly when
  `catalogLoaded && !catalogError && !catalogRegistryDegraded && !anyFamilyLive`
  (`metrics.component.ts:146-155`) — availability-driven, deliberately never "disabled"
  (D-8/AM-19(d), doc `metrics-fe-charts.md:104-120`).
- **Tab-level empty states are NOT error-gated** — a real inconsistency: the Families tab's
  empty branch keys on `families().length === 0` **only**
  (`families-tab.component.ts:90-92` → *"No families are registered."*), so an **errored**
  catalog load with an empty last-good catalog renders that copy **alongside** the shell's
  error banner. The shell deliberately gates its own panel on `catalogError() === null`
  (`metrics.component.ts:149`); the tab's copy is not gated the same way.
  The Overview tab's empty state is inside a successful `snapshot()` frame
  (`overview-tab.component.ts:90-92` → *"No series published yet."*) and is therefore
  implicitly gated.
- **Truncation surfaced**: catalog (`metrics.component.html:33-37`), snapshot
  (`overview-tab:69-71`), series (`charts-tab:102-104`).
- **Registry-degraded badge** (the v0.28.9 addition): a failed registry read answers
  **200 with an empty body**, distinguishable only via `activation.registry_read_ok === false`
  (`metrics.component.ts:225-229`) → badge `metrics.component.html:27-31` with copy
  `METRICS_REGISTRY_DEGRADED_COPY` (`models/metrics.model.ts:380-381`).
- **No last-good timestamp for the catalog.** The Families tab surfaces a live/dark count and
  a Reload button (`families-tab.component.ts:38-44`) but never renders *when* the catalog was
  fetched. The only quantified age on the page is the Charts meta line's sample age.

### "Activation" UI — **ABSENT**
The envelope's `activation` block has 4 members (`models/metrics.model.ts:112-133`:
`listener_enabled`, `census_enabled`, `query_api`, `registry_read_ok`). Grep across the whole
FE shows `listener_enabled` / `census_enabled` / `query_api` appear **only in the model type
and in spec fixtures — never in any template**. The only activation-derived surface is the
degraded-registry badge. **The user cannot see from the UI whether the exposition listener is
bound, whether the census is gated, or that the door is auth-gated-always-on.** There is no
toggle and no posture panel — consistent with the lane's design (the door is *never*
switch-gated, `metrics-fe-charts.md:143-147`), but it means the "activation concept" the lane
shipped is backend-only as far as the UI is concerned.

---

## 6. THE OTHER PAGES — route inventory (context)

`app.routes.ts` (105 lines at the audited tree) — **24 route entries, 18 top-level segments
(16 named non-wildcard + root + wildcard)**, every non-redirect route lazy `loadComponent`:

| # | line | path | title | component |
|---|---|---|---|---|
| 1 | 32 | `''` | — | HomeComponent |
| 2 | 33 | `instances` | — | InstancesComponent |
| 3 | 35 | `instances/:instanceId` | — | **redirect** → `projects/all/instances/:instanceId` |
| 4 | 42 | `projects/:projectId/instances/:instanceId` | — | InstanceDetailComponent |
| 5 | 43 | `projects/:projectId/workspace` | Workspace Viewer | WorkspaceComponent |
| 6 | 47 | `projects/:projectId/blueprints` | Project Blueprints | BlueprintComponent |
| 7 | 48 | `sources` | — | SourceListComponent |
| 8 | 49 | `jobs` | — | JobsComponent |
| 9 | 50 | `settings` | — | SettingsComponent |
| 10 | **58** | **`metrics`** | **Metrics** | **MetricsComponent** |
| 11 | 59 | `skills` | — | SkillsComponent |
| 12 | 61 | `skills/bank` | — | SkillBankComponent |
| 13 | 63 | `skills/triggers` | Skill Triggers | SkillTriggersPageComponent |
| 14 | 64 | `skills/:id` | — | SkillDetailComponent |
| 15 | 68 | `persona` | Persona | PersonaComponent |
| 16 | 69 | `schedules` | — | SchedulesComponent |
| 17 | 70 | `snapshots` | Snapshots | SnapshotsComponent |
| 18 | 74 | `resources` | Resources | ResourcesComponent |
| 19 | 75 | `mcp-servers` | — | McpServerListComponent |
| 20 | 76 | `migration` | — | MigrationComponent |
| 21 | 82 | `plan` | — | PlanComponent (invisible stub; Plane iframe overlay) |
| 22 | 93-98 | `maintenance/checkpoint-cleanup` | Maintenance · Checkpoint Cleanup | MaintenanceComponent — **guarded** (`canMatch: maintenanceAvailabilityGuard`, `:22-29,:96`) |
| 23 | 103 | `mcps-gateway` | MCP Gateway | McpsGatewayComponent |
| 24 | 104 | `**` | — | **redirect** → `''` |

Header nav (`app.html:11-39`) carries only: Instances, Sources, Schedules, Jobs, Skills,
Persona, Plan (gated). Everything else — including Metrics and Resources — is gear-menu only.

---

## 7. HONEST CRITIQUE

### 7.1 The headline finding: the NEWER chart is strictly LESS capable than the OLDER one in the same app
The codebase now contains **two hand-rolled zero-dependency SVG chart engines**:
`svg-line-chart` (523 lines, added 2026-11-02, `/metrics`) and `svg-timeline` (538 lines,
v0.25.0-era, `/resources`) — with **duplicated scale/path helpers and no shared abstraction**.
The older one ships capabilities the newer one lacks:

| Capability | `svg-timeline` (/resources, older) | `svg-line-chart` (/metrics, newer) |
|---|---|---|
| Time-range picker | ✅ `TIMELINE_WINDOWS = ['15m','1h','24h','7d']` (`timeline-tab.component.ts:60`, `:125`) | ❌ hardcoded `WINDOW_S = 3600` (`charts-tab.component.ts:195`) |
| Series toggle | ✅ `toggleSeries()` + `MatButtonToggleModule` (`timeline-tab.component.ts:13,:176`; `svg-timeline:388` "parent owns the toggle state") | ❌ none |
| Anomaly markers | ✅ severity-coloured dots + lane (`svg-timeline:169`, `:235-265`) | ❌ none |
| Click → drill-down | ✅ `(click)` → `markerClick` output → evidence + traceback fetch (`svg-timeline:240,:392`; `timeline-tab:185-199`) | ❌ zero click handlers |
| Hover styling | ✅ `.anomaly-marker:hover` (`svg-timeline:372`) | ❌ none |

This is the strongest argument for the refactor the user suspects: **the zero-new-deps
constraint did not merely forgo library features — it produced a functional regression
against the app's own prior art**, and it doubled the maintenance surface (two engines to
keep honest instead of one abstraction or one library).

### 7.2 What a developer-user cannot do here (the "why is this instance slow?" test)
- **Cannot see more than 60 minutes.** `WINDOW_S = 3600` is the only window the FE ever
  requests (`charts-tab.component.ts:195,:291`) — *"what changed overnight?"* is unanswerable
  from the Charts tab by construction.
- **Cannot read values off the chart.** No y-axis labels, no horizontal gridlines
  (`svg-line-chart:302-320` is vertical-only); y is per-series normalised (`:124-132`) so the
  chart is a *shape* view by explicit design (`:32-35`). To get a number you go to the
  Overview stat cards or hover individual 2.2px dots for a native tooltip with **no units**.
- **Cannot click anything.** No drill-down, no entity link, no series selection — the chart
  is a dead-end image (`charts-tab` grep: zero `(click)` outside Retry buttons).
- **Cannot compare.** No baseline/previous-window/deviation surface at all (0-hit grep), and
  the door's own `delta_change` is never requested.
- **Cannot export.** No CSV/JSON/PNG/clipboard anywhere in the metrics surface.
- **Cannot tell why a chart is missing.** A dark family yields no chart cell and no
  explanation on the Charts tab — the reason lives one tab away in a table
  (`families-tab.component.ts:64-69`).

### 7.3 Concrete costs of the zero-new-npm-deps constraint
Hand-rolled as a direct consequence (each is bespoke code the team now owns):
1. **Two scale/axis implementations** (`chartXScale`/`chartYScale` vs
   `timelineXScale`/`timelineYScale`) — duplicated, diverging.
2. **Two path builders** per engine (`buildChartLinePath/StepPath/AreaPath` vs
   `buildLinePath/buildAreaPath`).
3. **Native `<title>` tooltips** instead of a hover readout — no units, no crosshair, no
   nearest-point snapping, no touch support.
4. **No time-axis formatting library** — the code rolls `toISOString().slice(11,16)`
   (`svg-line-chart:250-255`), which shows only `HH:MM` and therefore **cannot distinguish
   days** on a >24h window (moot today only because the window is fixed at 1h).
5. **No zoom/brush/pan** — frozen out by declaration (`:24-26`).
6. **The constraint is now load-bearing test debt**: `metrics.bindings.pins.spec.ts:70-98`
   bans `chart.js`/`d3`/`ngx-charts`/`moment`/`dayjs` by string match in the metrics sources.
   Any future "adopt a library" decision must rewrite that pin — the guard is honest but it
   converts a product decision into a test change.

### 7.4 What breaks or degrades when data is dark or stale
- **Dark family + Charts tab** → silent absence (no cell, no reason). See §5.
- **>50 chart-primary families** → **silent omission**: `chartSpecs` filters only on
  `role === 'chart'`, **not** on `availability.state`, then `slice(0, METRICS_SELECTOR_CEILING)`
  with ceiling **50** (`metrics.component.ts:40,:162-175`). Dark families' names consume the
  budget, and if the roster exceeds 50, live families later in catalog order are cut — the FE
  surfaces the *door's* page bound (`payload.truncated`) but **not its own slice**. This is a
  genuine honesty gap in an otherwise unusually honest page.
- **Stale** → last-good view + STALE badge (never blanks — good), but there is **no marker of
  where the data ends** on the chart itself, and no last-good timestamp on the Overview cards
  (only the Charts meta line prints sample age, `charts-tab:98`).
- **Door 503 fail-closed / 403 scope miss** → correct, distinct, honest copy (§5) — but the
  user has no in-page remediation path (no "sign in with scope", no link).
- **Registry-degraded read** → a 200-empty body that *looks* like "no metrics"; the v0.28.9
  badge closes the confusion (`metrics.component.ts:225-229`) — the best piece of degradation
  design on the page.

### 7.5 Small defects found during the audit (cheap wins for the refactor)
- **Dead input:** `families-tab.component.ts:210` — `readonly active = input(false);` is
  declared, documented, and passed by the shell (`metrics.component.ts:188`) but **never
  referenced** anywhere in the tab (grep: single hit, the declaration). Harmless, but it
  implies a poll gate that does not exist — this tab owns no network at all.
- **Families empty copy not error-gated** (see §5) — *"No families are registered."* can
  render under a failed load.
- **Families table is inert** — `<th>` cells (`families-tab.component.ts:50-55`) have no sort,
  no click, no `aria-sort`; a 26-family catalog (16 live / 6 dark per the v0.28.6 census) is
  fine, but there is no way to find the dark ones without reading every row.
- **`chartUtcTicks`' `HH:MM`-only label** (`svg-line-chart.component.ts:250-255`) cannot
  disambiguate days on any window > 24 h — latent, because the window is fixed at 1 h today.

### 7.6 What is genuinely good (credit where due)
- **Polling hygiene is excellent** — named intervals, visibility gating, skip-if-in-flight,
  monotonic supersede tokens, teardown, all pinned by tests (`metrics.bindings.pins.spec.ts`).
- **Error taxonomy is honest and specific** — body-before-status decoding, five distinct
  classes, never-blank degradation (`metrics.model.ts:416-442`).
- **STEP-vs-LINEAR data honesty** — counters render step-after because a linear interpolation
  would assert unobserved values (`charts-tab.component.ts:56-59`; `svg-line-chart:186-205`);
  pinned in e2e (`e2e/metrics-gate.spec.ts:283-295`).
- **UTC-everywhere**, accessibility (`role="img"` + `ariaLabel`), and a typed verbatim wire
  mirror (`metrics.model.ts` header) are all above average.
- The primitive's **pure helpers are exported and unit-asserted** (`svg-line-chart:28-30`),
  which is exactly why this audit could cite them precisely.

### 7.7 Test coverage note
1,645 lines of specs cover the FE metrics surface (`svg-line-chart.component.spec.ts` 241,
`metrics.component.spec.ts` 519, `charts-tab` 213, `overview-tab` 274, `families-tab` 173,
`metrics-gear-menu` 98, `metrics.bindings.pins` 127) plus a Playwright gate
(`e2e/metrics-gate.spec.ts`, 3 scenarios incl. deep-link and the degraded-registry badge).
The shipped behaviour is well-pinned — a refactor has a strong safety net.

---

## Gaps / confidence
- 🟢 All findings above are multi-source (source + spec + e2e + doc `docs/architecture/metrics-fe-charts.md`).
- 🟢 The Overview/Families tab anatomy was read directly by the auditor **and** independently
  by a dedicated census worker; the two reports agree on every claim, and the worker's three
  additions (snapshot takes no query params; Families owns no network; dead `active` input)
  were re-verified directly at `services/metrics.service.ts:55-60`,
  `families-tab.component.ts:205,:210,:90-92` before being folded in above.
- **Not covered here (out of scope):** live-page rendering/screenshots (a sibling tester
  instance owns live capture); the `/resources` page's own full anatomy (a sibling audit owns
  it); backend door behaviour beyond what the FE consumes.
