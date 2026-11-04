# Decision Record — FE-only T1 metrics dashboard (wi-32a99618)

Date: 2026-11-03
Author: plan author (giter lane) for commission wi-32a99618
Status: Binding + implementation decisions for the T1 build lane.

Every decision below carries a rationale and a citation. The three binding
rulings (D-1/D-2/D-3) are reproduced **verbatim** from the commission and are
NOT re-litigated here; the implementation-level risks they expose are recorded
in `plan-overview.md` §Risks and, where actionable, as scoped mitigations.

---

## Part I — The three binding rulings (verbatim, folded as decided)

### D-1 CHARTING — FINAL

> Chart.js v4 SOLE vendor for interactive panels, LAZY-CHUNK-ONLY (~65 kB gz,
> ZERO first-paint growth); ONE consolidated house SVG renderer for minis/fallback.
> Supersedes the earlier ECharts settlement; ApexCharts/Gridster2 dropped.
> Interaction (hover readout, baseline, ranges) IS in scope — the "drop Chart.js
> if interaction deferred" flip-condition does NOT apply. SAME-COMMIT
> pins-spec/pack governance amendment in the same commit that adds the
> dependency. Lane's FIRST housekeeping commit = the audit-doc
> what-changed-and-why amendment.

**Consequences accepted by this plan:** the Chart.js flip-condition (f2 in
`resource-refactor-architecture.md` §10.7) is **void** for this lane; the
posture amendment (`metrics.bindings.pins.spec.ts:94`, pack Leg 7, and the
"zero FE deps" doc/checklist sites) lands in the SAME commit as the dependency
(Phase 1); the audit's "4-engine proposal" is superseded, not extended.

### D-2 AUTH POSTURE — FINAL

> central door fail-closed posture STAYS (no weakening). T1 ships as designed.
> FE MUST distinguish 503 AUTH_NOT_CONFIGURED from other dark causes and render
> an ACTIONABLE empty-state ("metrics door is fail-closed pending auth
> configuration — see runbook" → docs/runbooks/oauth2-auth.md). Enabling auth is
> a USER MORNING ITEM — the lane must NOT touch live auth config; the exact
> operator recipe (steps + restart implication) goes in the plan's close-out
> section.

**Consequences accepted by this plan:** the FE distinguishes **five** door
failure classes (`MetricsDoorErrorCode` in
`frontend/src/app/models/metrics.model.ts:359-364`), and
`auth_not_configured` gets its OWN actionable state — not a banner variant, a
distinct empty-state panel with the runbook pointer. The operator recipe lives
in `plan-overview.md` §Close-out and is **not executed** by the lane.

### D-3 ROUTE FATE — FINAL

> retire /resources with a SAME-RELEASE redirect to /metrics?tab=resource.
> Predefined tabs stand; Gridster2 door closed.

**Consequences accepted by this plan:** the redirect preserves query params
(the app's `redirectTo` idiom does not — `app.routes.ts:35,:104`), `?tab=`
deep links are NEW FE work, and the gear-menu `Resources` entry is removed.

---

## Part II — The two accepted analyst rulings (R-1, R-2) + the six adjudications owed

### R-1 — ACCEPTED (binding analyst ruling, architect review 2026-11-03)

> **R-1 — Panel granularity (central tabs): one panel per metric-kind + unit
> group, kind read from the WIRE.** Registered families mix kinds (F4 registers
> a gauge *and* a counter — `daemon/services/metrics_query/wiring/w1.py:194-228`;
> F9 likewise — `w2.py:120-150`), and the wire already carries `chart_role` per
> metric (`metrics.model.ts:164`, resolved per series at
> `charts-tab.component.ts:222`). Per-family panels would break counter-honesty
> and falsify unit labels; per-raw-series panels lose the family grouping the
> manifest promises. Granularity therefore derives at panel-composition time
> from wire kinds; the manifest stays registration-only on the family axis.
> (Lands in AM-9/AM-12.)

**Accepted as binding.** Folded into IMPL-04 (the renderer roles + the
granularity rule), IMPL-10 (the unit vocabulary keys on the **metric name**, not
the family), and the new surface→intent→renderer→granularity table in
`plan-overview.md` §4. **Effect on the plan:** the manifest binds FAMILIES; the
PANELS are composed at render time from the wire's per-metric kinds, so a
family that registers both a gauge and a counter yields **two** panels (one per
kind+unit group) — never one mixed axis.

### R-2 — ACCEPTED (binding analyst ruling, architect review 2026-11-03)

> **R-2 — House-SVG mini role in T1: capability-retained, NO page surface.**
> No mini consumer exists anywhere in the plan's consumer map (grep
> "sparkline" = 0 hits in `frontend/src`; phases 4–5 name exactly two renderer
> consumers, both panel/fallback). D-1/ADJ-ii's five mini-tier extensions stay
> IN the consolidated renderer and are exercised via (a) the mount-failure
> fallback path and (b) renderer-level specs — not via a live page surface.
> Phase 3.7 must name the renderer that actually draws the baseline in T1 (the
> Chart.js panel is the normal path; the house SVG carries the baseline as
> fallback capability). (Lands in AM-9.)

**Accepted as binding.** Folded into IMPL-04 (the house SVG's role is
**capability-retained** — mini + fallback — with **no page surface in T1**),
ADJ-ii (the scope guard's third bullet is restated: the five extensions are
exercised via the **mount-failure fallback** and the **renderer specs**, not a
live mini), and Phase 3's baseline task (the renderer that draws the T1
baseline is named explicitly).

### ADJ-i — Fate of the old three tabs (Overview / Charts / Families)

**DECIDED:** none of the three survives as a TAB. Their content is re-homed:

| Old tab | Fate | Where it lands |
|---|---|---|
| **Overview** (snapshot stat cards, `/api/metrics/snapshot`, 10 s poll) | **Re-homed, per-tab** | Each of the 7 tabs renders a **stat strip** for its own bound stat-role families, fed by a frame-scoped snapshot fetch scoped with `?family=` (`daemon/routers/metrics.py:661-668` reads `family`/`name_prefix` from `request.query_params`). The frame fetches the snapshot ONCE and shares it across tabs (one poll, not seven). |
| **Charts** (series viewer, `/api/metrics/series`, 15 s poll, window HARDCODED 3600 s at `frontend/src/app/pages/metrics/charts/charts-tab.component.ts:195`) | **Re-homed, per-tab** | Each tab renders **its own** trend panels for its bound chart-role families; the frame's range selector + baseline apply to all of them uniformly. The global "browse every chart family at once" view is **retired** — it is replaced by the per-tab views plus the frame-level family catalog (below). |
| **Families** (catalog of every registered family with live/dark + reason) | **Re-homed, frame-level** | Becomes a **catalog drawer** opened from the page frame — NOT a tab. Rationale: it is a cross-cutting registry view (it lists families of ALL modules, including ones with no tab), not a module view; keeping it a tab would make it an 8th tab and contradict the ratified 7-tab set. The drawer's content is unchanged in substance (the `families-tab.component.ts` row rendering + the `formatAvailabilityReason` vocabulary at `metrics.model.ts:294-313`). |

**Rationale:** the commission fixes T1 at 7 module tabs. A module tab is a
*module* view; Overview/Charts/Families are *facets* of all modules, so they
belong to the frame, not to a tab. The one capability genuinely lost — a
cross-module "all series at once" surface — is recoverable in one manifest
line later, and its per-tab replacement answers the same question more
directly.

**Citations:** `fe-ui-audit-and-resource-refactor.md` §3b (7-tab table);
`resource-refactor-architecture.md` §1.1 (`CentralDataTab.statFamilyIds`);
`fe-metrics-source-audit.md` §1.2 (the three old tabs and their polling).

### ADJ-ii — Mini-tier extension tension (corpus says feature-frozen; the commission rules the extensions IN)

**DECIDED:** the **commission wins**. The mini-tier extensions
(devicePixelRatio crispness, nearest-point hover via overlay+bisect, last-value
marker, optional baseline line, viewBox responsiveness) are **ruled IN** for
this lane, superseding `resource-refactor-architecture.md` §10.6 `:390`
("the mini renderer is feature-frozen *as a mini*, no axes/interaction ever").

**Rationale:** (i) the commission is the overnight-authorized decision owner for
v1 and explicitly rules the extensions in; (ii) the corpus prescription was
written against a defect picture that the audit has since sharpened — the
extensions are exactly the pixels the audit condemned (no hover readout, no
last-value, no baseline, no crispness); (iii) the extensions are *bounded
additions to one consolidated primitive*, not a second engine.

**Scope guard (binding on the implementation):**

1. The extensions live in **one** house SVG primitive (the consolidated
   renderer) — no per-surface copies.
2. **Chart.js stays interactive-panels-only.** The mini/fallback role never
   acquires a vendor engine, a zoom/brush gesture, a legend-toggle language, or
   an export path.
3. The mini role stays a MINI: sparkline-scale, no axes grid, no zoom/brush.
   The extension set is closed: `{devicePixelRatio, hover+bisect, last-value
   marker, optional baseline line, viewBox}` — an extension outside this set
   requires a new decision record entry.
4. The consolidation **deletes** one of the two hand-rolled engines, so the net
   surface grows by the extensions and shrinks by the duplication.

**Citations:** `resource-refactor-architecture.md` §10.6 `:390`, §5.1 (both
engines "pre-declared closed at authoring time"), §5.4 (the migration table);
`fe-metrics-source-audit.md` §3 (the capability table the extensions close).

### ADJ-iii — Range vocabulary per tab

**DECIDED (shipped sets):**

| Tab | Shipped range options | Default | Why |
|---|---|---|---|
| Tabs 2–7 (central) | **15m / 30m / 1h / 2h** | 1h | 2 h IS the substrate ceiling (`WINDOW_SECONDS = 7200`, `daemon/services/metrics_query/constants.py:33`) and raising the FE to ask for it costs **zero** backend work (resolution halves to 30 s at `max_points` 240 — `resource-refactor-architecture.md` §3.1). Shipping the ceiling makes it **visible**; today a 2 h request silently returns a 2 h window with no marker (`daemon/routers/metrics.py:778`), which is the silent-clamp dishonesty class the audit condemned. 30m is on the corpus ladder (`resource-refactor-architecture.md` §3.1) and costs nothing. |
| Tab 1 (Resources, custom slot) | **15m / 1h / 24h / 7d** | 1h | Its own ladder, already shipped: `TIMELINE_WINDOWS` (`frontend/src/app/pages/resources/timeline/timeline-tab.component.ts:60`), `frontend/src/app/models/resources.models.ts:16`. |

**Rationale for reading the commission's "universal 15m/1h (central clamps 2h)"
against the corpus's "15m / 30m / 1h / 2h":** the commission's parenthetical
names 2h as the clamp; the corpus ladder is the four-step set ending at that
clamp. The commission asked the plan to *pick the shipped set per tab and
record it*; this record is the pick. The universal frame control renders the
**union** of options and **disables** the ones the active tab cannot serve
(visible enforcement, deliberately contrasting with the door's silent clamp).

**The retention-tier correction (must appear in the UI copy, precisely):**
48 h / 14 d / 180 d are **PG RETENTION TIERS** — raw 30 s/48 h, minute 60 s/14 d,
hour 3600 s/180 d — the tier knobs live in `daemon/config.py:925-931` (the
docstring listing `retention_raw_minutes` / `retention_minute_hours` /
`retention_hour_days`) and `:980-1001` (the Field definitions: 2880 min = 48 h,
336 h = 14 d, 180 d); also `resource-refactor-architecture.md` §6.1 `:233` — the
tiers **backing** the ladder, **NOT** picker options. The picker never offers
48h/14d/180d. **CITATION CORRECTION (AM-17):** the previously cited
`daemon/services/flusher.py:360-368` names a file that does not exist (the
flusher is `daemon/services/resource_monitor/flusher.py`, and those lines are
the **sweeper's** row-drain body, not tier configuration), and
`daemon/config.py:811-827` holds the **metrics** sampler intervals, not the
resource tiers.

### ADJ-iv — Event overlay (scope 7): ship-vs-route

**DECIDED: SHIP both FE-only shapes, ROUTE the durable event work.**

**Shape 1 — SHIP (visible anomaly-marker labels).** The API words are already
in the FE: `markerTitle()` composes `` `${kind} · ${shape} · ${severity} · peak ${peak}` ``
(`frontend/src/app/pages/resources/svg-timeline/svg-timeline.component.ts:530-532`)
— but only as a hover-only native `<title>`. The audit's "four event dots
unlabeled" is a VISIBILITY defect, not a data defect. Ship: a visible label +
tooltip carrying `kind`, `severity`, `series_key`, `peak` — every word the API
already serves — plus the anomaly list (ADJ-v) as the non-hover path to the
same facts.

**Shape 2 — SHIP (honest data-gap annotation), client-side.** No restart or
deploy seam exists in ANY endpoint (`resource-tab-forensics.md` Part B: 8
endpoints, zero coupling to any event source), and the anomalies journal is
process-local (`deque(maxlen=256)`, reset per restart). So the ONLY honest
FE-only annotation is a **data-gap** annotation derived from the `/history`
discontinuity itself: a series that drops to zero (or to a missing sample) and
regrows ⇒ a data gap. It is labelled **"DATA GAP — series unavailable in this
interval"** and **never** "restart" / "deploy" / "promote". If the FE derives
it, the FE says only what it derived.

**ROUTE (not built):** durable anomaly/restart/deploy events spanning restarts —
own small commission (the commission's routed-out item (d)). The lane records
it; it does not build it and does not pretend the data-gap shape is it.

**Citations:** `resource-tab-forensics.md` Part B (endpoint inventory) and
Part C (d); `fe-ui-audit-and-resource-refactor.md` §1.2 ("Anomaly markers are
process-local"); `live-capture-log.md` §7.2.

### ADJ-v — Anomaly table on the resource tab

**DECIDED: SHIP — tab 1 gains an anomalies panel.**

Rationale: `/api/resources/anomalies` returns rows (live probe: `anom-000003`
cpu peak 75.55 severity medium, `anom-000004` fds, … — `live-capture-log.md`
§4) and the FE service already has `getAnomalies(window)` /
`getEvidence(id)`
(`frontend/src/app/services/resources.service.ts:67-76`), but **no FE surface
renders them** — they exist only as anonymous Timeline dots. The panel is a
small table (`id`, `kind`, `severity`, `peak`, `t_start`, `t_end`) whose row
click opens the **existing** drill-down (`evidence` + `traceback`), which
already exists (`timeline-tab.component.ts:185-205`).

**Honesty requirement on the panel (binding):** it carries the standing
disclosure that the anomaly journal is **process-local** — it resets on daemon
restart and retains only the last 256 entries — so the panel can never be read
as the durable event history. The panel's own header states this in one line.

### ADJ-vi — Family availability handling + the `chartSpecs` honesty gap

**DECIDED (two coupled changes):**

1. **Rendering filters by per-family availability.** A tab renders a chart for
   a bound family **only while that family's `availability.state === 'live'`
   and its reason is not a false-dark one already fixed**. A bound family the
   door reports dark is listed in the tab's **family line** with its reason
   (`formatAvailabilityReason` — `metrics.model.ts:294-313`) — never charted,
   never silently dropped. Consequences: F1/F3 (Runtime) and F6 (DB Pool) join
   their tabs **with ZERO FE change** the moment R-ii / R-i land, because the
   manifest already binds them and the availability projection drives rendering.
2. **The FE's own slice is surfaced.** The `chartSpecs` computed at
   `frontend/src/app/pages/metrics/metrics.component.ts:162-175` filters only on
   `role === 'chart'` and then `slice(0, METRICS_SELECTOR_CEILING /* 50 */)`
   at `:174` — dark families consume the budget and later live families are
   cut **silently** (`fe-metrics-source-audit.md` §7.4). Fix: filter by
   availability **before** the ceiling, and when the FE's own slice actually
   truncates, **say so** ("N families are not shown — the door's selector
   ceiling is 50") — the door's own `payload.truncated` is already surfaced and
   the FE's slice must be surfaced with the same discipline.

**Citations:** `fe-metrics-source-audit.md` §7.4; `metrics.component.ts:40,:162-175`;
`resource-refactor-architecture.md` §2.2 ("the manifest already lists them; the
per-family availability display handles the rest"); R-i/R-ii in the v0.28.6
disclosure.

---

## Part III — Implementation decisions (mine, with rationale + citation)

### IMPL-01 — The tab manifest is FE-local and typed; the backend serves no manifest

`frontend/src/app/pages/metrics/tabs.manifest.ts` (NEW), typed
`MetricsTabManifest` with the discriminated `central | custom` slots, per
`resource-refactor-architecture.md` §1.1. **A BE-served manifest is refused**:
shape (B) (a new route) breaks the frozen `query-routes` fence
(`docs/architecture/metrics-query-contract.md:225-229`); shape (A) would change
the exact-field-set pin at
`tests/unit/metrics/test_metrics_query_provider.py:137-147` — and the lane is
FE-only, so a backend change is out of scope by construction.

### IMPL-02 — Tab 1 is a `custom` slot; its module RELOCATES under the metrics page

The resource module moves from `frontend/src/app/pages/resources/` to
`frontend/src/app/pages/metrics/resource/` (`live/`, `timeline/`, the
consolidated renderer consumer, the anomalies panel). Rationale: D-3 retires the
route, and a directory named after a retired route is a lie the next reader
will trust. `pages/resources/` is deleted wholesale (shell + spec +
gear-menu spec + the superseded engine).

### IMPL-03 — Tab 1's body is FLAT (Live strip → Timeline panel → anomalies panel), not nested tabs

The existing `ResourcesComponent` shell hosts a `mat-tab-group` with Live and
Timeline sections (`frontend/src/app/pages/resources/resources.component.ts:47-51`).
Nested tab groups inside a tab are a known collision surface (the audit already
found a 900 px banner/button collision). The tab-1 body is instead one
scrollable column: **Live stat strip → Timeline panel → anomalies panel**,
with the **frame's** range selector driving the Timeline's window (the
Timeline's own window-picker UI is removed — one range control for the page).
The Live strip keeps its own 8 s poll cadence (a different question, a
different cadence).

### IMPL-04 — Renderer roles: house SVG = mini + fallback (capability-retained, NO T1 page surface); Chart.js = interactive panels

- **House SVG renderer** (ONE consolidated engine, merged from
  `svg-line-chart.component.ts` + `svg-timeline.component.ts`): serves the
  **mini** role (sparkline-scale, no axes grid, no zoom/brush) and the
  **never-silent fallback** role (a Chart.js mount failure surfaces a badge
  and falls back to SVG — never a silent downgrade,
  `resource-refactor-architecture.md` §10.6 three-layer fallback).
  **Per R-2, the mini role has NO page surface in T1**: the five mini-tier
  extensions (ADJ-ii) are **capability-retained** and are exercised through
  (a) the **mount-failure fallback** path and (b) **renderer-level specs** —
  never by a live mini on any of the 7 tabs. The completion sentence is
  therefore "every **panel** carries a unit-labeled y-axis; the mini role
  renders no axis" (AM-9).
- **Chart.js wrapper**: serves the **interactive panel** role — value axis with
  units, crosshair + nearest-point snap, legend toggles, shared-y multi-series,
  `stepped: true` (the house counter-honesty rule), click → drill-down
  (`resource-refactor-architecture.md` §5.2). The **resource timeline migrates
  to it** (its anomaly markers become a house plugin — the severity colors
  verbatim from `svg-timeline.component.ts:169-173` — and its marker click
  becomes the wrapper's `pointClick`).
- **Panel granularity (per R-1):** the manifest binds **families**; a **panel**
  is composed at render time as **one metric-kind + unit group**, with the kind
  read from the **wire** (`chart_role` per metric — `metrics.model.ts:164`;
  resolved per series at `charts-tab.component.ts:222`) and **never** guessed
  from an FE table. A family that registers both a gauge and a counter (F4:
  `w1.py:194-228`; F9: `w2.py:120-150`) yields **two** panels — a mixed-kind
  panel would break counter-honesty and falsify the unit label.
- **The intent + renderer registry** (shape B) is the contract:
  `trend | sparkline | stat` intents → a registry mapping intents to
  `ChartRenderer { capabilities, supports(), render(), teardown() }`
  (`resource-refactor-architecture.md` §10.3). Adding an engine later = one
  renderer file + one registry line; the manifest never changes.
- **The consolidation deletes one engine.** The 241 L + 237 L pure-helper
  specs are **ported** (not re-invented): the exported helpers stay exported
  and unit-asserted — that property is why the audit could cite them precisely
  (`fe-metrics-source-audit.md` §7.6). **Per AM-26 the port is a 12-symbol
  enumeration plus a per-assertion disposition table**, not a line count.
- **The wrapper MUST** emit a per-series DOM proxy (preserving the
  `data-series` / `data-render-mode` hooks) **and** a visually-hidden data
  table (restoring the a11y surface the canvas removes and keeping the
  DOM-assertion gate alive), and MUST call `chart.destroy()` in
  `ngOnDestroy` (the shell destroys tab bodies via `NgComponentOutlet`; a
  missing destroy leaks canvas + ResizeObserver per tab switch)
  (`resource-refactor-architecture.md` §5.3 (i)-(iii)).
- **The canvas-redraw rule is written down:** the app is zoneless
  (`zone.js` declared but never loaded — `resource-refactor-architecture.md`
  §5.1), so no Angular-rendered overlay may depend on a Chart.js redraw.

### IMPL-05 — The dependency set is exactly two direct entries

`chart.js` ^4.6.1 (dependency) + `jest-canvas-mock` ^2.5.8 (devDependency).
`@kurkle/color` arrives transitively; the lockfile closure additionally carries
`cssfontparser` + `moo-color` (jest-canvas-mock's two deps — measured via
`npm view` 2026-11-03). The governance amendment asserts the **EXPECTED SET by
name**, not a raw entry count (a count assertion breaks on an unrelated
transitive bump).

### IMPL-06 — The redirect is a `canMatch → UrlTree` guard, with a named param-reading mechanism (AM-3)

The app's `redirectTo` idiom does not preserve query params
(`app.routes.ts:35,:104`); `canMatch` returning a `UrlTree` does
(`resource-refactor-architecture.md` §7). A 3-line redirect component was the
alternative; the guard is preferred because it keeps the route table as the
single source of route truth and needs no new component.

**The mechanism is named (AM-3) — a `canMatch` guard receives `(route,
segments)`, NOT query params.** The guard therefore reads
`Router.getCurrentNavigation()?.extractedUrl.queryParams` with an **explicit
null-fallback** (log + degrade to a param-less redirect, **never** crash and
never a silent param loss). **Precedence:** the redirect's own `tab=resource`
**overrides** an incoming `tab` — the preservation claim is therefore narrowed
to **"all params except `tab`"**. The acceptance is an **end-to-end**
`router.navigateByUrl('/resources?window=24h&x=1')` asserting the **landed
URL's parsed `queryParams`** (not a raw string — string comparison is
order-brittle), plus the leg `/resources?tab=charts` ⇒ lands on `?tab=resource`.
A unit assertion on a hand-built UrlTree is explicitly **not** accepted.

### IMPL-07 — The frame owns range + baseline; the baseline is an FE-only client split, **scoped to the central tabs** (AM-14)

Baseline = **2× window fetch + client-side split at the midpoint**, working for
**W ≤ 1 h** (2W ≤ 7200). **The split is anchored on the RESPONSE's
`window.from_utc/to_utc` midpoint** (parsed with the house `parseNaiveUtc`),
**never the client clock** (AM-17: a 60 s client-clock skew would misassign
~8 % of a 2 h window; the epoch points themselves live at
`daemon/services/metrics_query/provider.py:384-387` and the window anchor at
`envelope.py:201-203,:226-228` — the previously cited `envelope.py:386-388`
does not exist, the file is 316 lines).

**The baseline is scoped to the CENTRAL tabs only** (AM-14): the 2W overfetch is
defined against the central door's `window_s`. The **custom slot renders the
toggle disabled with its reason** ("the resources door serves a fixed
15m/1h/24h/7d vocabulary with no previous-window offset") — the resources door
accepts exactly those four values (`daemon/routers/resources.py:120-130`, 400
otherwise) and `/api/resources/history` takes only `window` + `resolution`
(`frontend/src/app/services/resources.service.ts:54-56`), so a "previous
window" is **not fetchable** there; a client-side split of one payload would
yield two HALF-length windows mislabeled "previous window".

**The guard is request-time, not UI-only** (defence in depth): the toggle
auto-clears with its reason when the range crosses 1 h; the request builder
**never emits `window_s > 7200`**; and the FE compares the response's window
span against 2W and **refuses to render a baseline on a clamped payload** — the
central door silently clamps to 7200 s and returns 200
(`daemon/routers/metrics.py:778`), which would otherwise let the FE split a
2 h payload at a fake midpoint while the UI claims two 2 h windows.

Disclosed fidelity limits, stated in the UI, not in a comment: `bucket_max`
downsampling runs over the whole fetched window so a bucket can straddle the
split; the older half sits at the ring's eviction edge; the resolution halves.
**Forged cursors are forbidden** (the cursor is unsigned base64 JSON —
out-of-contract and brittle; `resource-refactor-architecture.md` §3.2). The
first-class `end_offset_s`/`compare` backend param is **routed out** (ADJ
list, item f).

### IMPL-08 — Stat cards read the snapshot scoped by family, with truncation honesty

The frame fetches `/api/metrics/snapshot` once per poll cycle and each tab
filters its own stat-role families **client-side by the bound family names**
(the snapshot route also accepts `?family=` — `daemon/routers/metrics.py:661-668`
— and the FE service gains an optional `family` param, FE-only). If a bound
family is absent from a **bounded** snapshot page (`truncated` + `next_cursor`),
the card renders "not on this page" + the truncation hint — **never a fake
zero** and never a silent absence.

### IMPL-09 — The door state is PER-SOURCE, not frame-level (AM-1)

D-2 requires the FE to distinguish 503 `AUTH_NOT_CONFIGURED` from every other
dark cause. **The state model is per source, not per frame** (AM-1): the
manifest's `central | custom` slot is the seam. The `auth_not_configured`
actionable panel renders on **central-door surfaces only** (the catalog drawer
+ the 6 central tab bodies); **each custom slot renders its own source's
state** — so under the default install posture (auth OFF ⇒ central door 503,
`/api/resources/*` 200 open) **tab 1 stays lit** with its live strip and
timeline while the central tabs carry the actionable state. The frame holds
**per-tab state**, not one global state.

The class table is **widened** (AM-4, a consequence beneath D-2 — the ruling
itself is untouched): `auth_required` (401 — cause + "sign in / obtain a
scoped token"; **no** Retry), `auth_not_configured` (503 — actionable, runbook
pointer, **no** Retry), `scope_insufficient` (403 with the
`SCOPE_INSUFFICIENT` token), `forbidden_other` (a 403 without that token —
"rejected before the scope check"), `unknown_family` (404 — version skew; no
Retry), `bad_request` (400 — an FE-bug class; no Retry), `timeout` (a
client-side ~15 s deadline — no Retry), `registry_unavailable` (503 with that
body code), `network` (status 0), `unexpected`. **401 is the FIRST state every
real user meets** once auth is enabled (the FE sends
`Accept: application/json`, so the middleware's **401** branch applies, not the
browser 302) — today it decodes to `unexpected` + a dead Retry
(`metrics.model.ts:437-441`).

### IMPL-10 — The unit vocabulary keys on the METRIC name; panels are per kind+unit group (AM-12, R-1)

The door's families carry `labels` and `role`, **not** units, and a family
**mixes kinds** (F4 registers a gauge *and* a counter — `w1.py:194-228`; F9 a
counter + a 0/1 gauge — `w2.py:120-150`). Therefore: (i) the unit vocabulary
keys on the **metric (series) name**, never on the family; (ii) **panels are
composed per kind+unit group** with the kind read from the **wire**
(`chart_role`, `metrics.model.ts:164`) and **never guessed** from an FE table;
(iii) a metric with no known unit renders "unit unknown" — a wrong unit is
worse than no unit; (iv) the resource timeline's panels are **three** unit
classes (memory MB, CPU %, **counts** — `threads`, `instances_active`,
`svg-timeline.component.ts:56-63`), and the count class must survive the
**series-toggle path** (5 series, 3 unit classes) — toggling a count series on
must never land it on an MB/% panel.

### IMPL-11 — The attribution card states its basis, or says it cannot reconcile (AM-13)

The displayed numbers do not cohere (71 MB attributed + 1,119 MB unattributed
⇒ 1,190 MB implied total; daemon RSS 633 MB; cgroup current 6,178 MB; coverage
0 %). The FE **cannot invent a basis** — and, per AM-13, it **cannot compute
the coverage either**: the daemon computes `coverage_pct = min(1.0, attributed
/ anon_bytes)` (`daemon/services/resource_monitor/sampler.py:759`, computed at
`:841,:849-851`) and `anon_bytes` comes from `snap.daemon.anon_kb` — it is
**NOT** the sum of the displayed components. **Therefore the FE displays the
payload's `coverage_pct` verbatim** beside the components, and renders an
explicit non-reconciliation sentence when the payload's own fields disagree.
**Pinned: "the FE computes NO coverage percentage."**

The **per-instance top-N list** (the `instances[]`/`children[]` payload that is
fetched every 8 s and never rendered — `resource-tab-forensics.md` A3) is
**deferred to a follow-up commission** (routed (m)): it is a NEW capability
(top-N? sort? click-through?), the commission's scope item 6 named the math and
the coverage figure specifically, and the payload is already present so the
deferral is cheap. The Live strip's attribution subtitle stops promising what
it does not deliver.

### IMPL-12 — The family catalog drawer replaces the Families tab, and fixes the inert table

The drawer keeps the catalog's substance (every registered family, live/dark +
reason) and adds what the audit found missing: the dark families become
**findable** (sort/filter), the header cells gain `aria-sort` + click
(`families-tab.component.ts:50-55` is inert today), the empty copy is
**error-gated** ("No families are registered." must not render under a failed
load), and the dead `active` input (`:210`) is removed with its dead shell
plumbing (`metrics.component.ts:188`).

### IMPL-13 — The `?tab=` deep link is written back on tab change

Read `queryParamMap` → set `activeTabId` (`metrics.component.ts:118`) on init;
write `?tab=<id>` on `onTabChange` (`:204-209`). Unknown/legacy tab ids fall
back to the default tab (tab 1, Resources) **without** a redirect loop. The
manifest's `id` is the slug, and `?tab=resource` is the D-3 redirect target —
so the redirect's target id must equal the manifest entry's id.

### IMPL-14 — The verification route is a fenced DEV daemon on :8079, with FOUR legs

See `plan-overview.md` §Verification and `phase9-plan.md`. The legs:
**L1 (data-bearing)** — dev daemon, auth ON with a minted operator bearer
(`maintenance:read`) presented by the browser context's
`extraHTTPHeaders` (**no production FE change**); **L1b (auth required, no
bearer)** — auth ON, **no** credential → **401** → the sign-in panel (AM-4 —
this is the FIRST state a real user meets); **L2 (auth-absent)** — the same dev
env with auth OFF → 503 `AUTH_NOT_CONFIGURED` → the actionable empty state
**on the central surfaces only**, while **tab 1 stays lit** (AM-1); **L4** (the
AM-1 capture leg: tab 1 renders real data while the central tabs carry the
actionable state). **Never** the live daemon; never `:9797`; live config out of
bounds. The dev daemon binds **`PORT=8079`** — `DAEMON_PORT` is **inert**
(`config.yaml:112-113` reads `port: ${PORT:-8088}`; `DAEMON_PORT` appears
nowhere under `daemon/`), so the fence sets `PORT` **after** the scrub and
asserts the **effective** port from the running daemon (AM-5).

### IMPL-15 — The backend endpoint retirements are ROUTED OUT of this lane

`/api/resources/control-capabilities` and `POST /api/resources/profile` are
dead code, but retiring them rewrites the scope-manifest census pin
(`tests/unit/test_router_scope_manifest.py:106-113,:288-292`) — a **daemon**
change, which this FE-only lane may not make. Routed to a backend hygiene
commission with the pin-rewrite mechanics recorded.

### IMPL-16 — The `agents/` tree is untouched; `agents/.prompt-artifacts.sha256` stays 161/161

`.agents/` is project-shared and in scope (planning + evidence). `agents/` is
the agent-definitions tree and is **out of scope** — the baseline
`agents/.prompt-artifacts.sha256` (161 entries, verified at this commit) must
be byte-identical at the lane's end.

### IMPL-17 — TypeScript is invoked only through `frontend/`'s local devDependency

`cd frontend && ./node_modules/.bin/tsc --noEmit -p tsconfig.app.json`.
Never bare `tsc` / `npx tsc` outside `frontend/` (the `tsc` npm package is not
TypeScript; bare `npx` outside a package dir is a remote fetch + execute).
The existing pack `test/packs/fe_static_typecheck_build_test.sh` Stage 1 uses
`npx tsc --noEmit` — the lane **verifies its working directory** is `frontend/`
before invoking it, and does not extend that idiom.

### IMPL-18 — One copy SSOT: every door-state class carries a structured copy (AM-20)

The door-state copy moves to **one structured const per class** in
`frontend/src/app/models/metrics.model.ts` — each with **cause / meaning /
action / pointer** — replacing the flat `Record<MetricsDoorErrorCode, string>`
(`metrics.model.ts:384-392`) that cannot hold a 3-part actionable copy. The
**two duplicated degraded-registry suffixes** (`overview-tab.component.ts:295`
and `charts-tab.component.ts:311`) move into that SSOT as well.

The **403 predicate is narrowed** (AM-20): `scope_insufficient` is
`rawCode === 'SCOPE_INSUFFICIENT'` (case-insensitive) **only**; any other 403
becomes the new **`forbidden_other`** class ("rejected before the scope
check"). The current predicate labels *any* 403-with-a-code as
`scope_insufficient` (`metrics.model.ts:426-428`) — a false statement about the
operator's session, contradicting the function's own doc comment. **Both 403
branches are pinned by a spec.**

### IMPL-19 — The manifest carries per-tab VARIANTS of the frame states, never a parallel copy source (AM-20)

The frame owns the **state**; the manifest may carry a per-tab **variant** of
a state's copy (e.g. "the MCP families are dark — reason X") but never a
second, parallel copy source. The state model is **per source** (IMPL-09): the
frame holds per-tab state; the `central | custom` slot is the seam.

### IMPL-20 — The bundle guard is numeric + import-graph based, and lives in the phase that can break it (AM-2)

The D-1 safety claim is restated: **a LAZY Chart.js leak trips no budget** —
lazy chunks are counted by no budget type (only `initial` + `anyComponentStyle`
are configured), so the production build stays green and both FE packs stay
green. The guards are therefore: **(a) a numeric `|Δ initial-total| ≤ 2 kB`
against the frozen lane-base baseline** (5,909,018 B — the builder's own
"Initial total" basis, 36 chunk files; the full derivation is
`research-findings.md` §14), **(b) no `chart.js` marker in ANY initial chunk**,
and **(c) an import-graph assertion over resolved import sites** (AM-15). The
**eager** leak case *does* break the build against the correct baseline: the
headroom is **90,982 B** and chart.js's raw minified size is ~250–400 kB ⇒ the
`initial` **error** budget trips. The guard is **co-located** in Phases 1 and 4
(the leak window spans Phases 1–8), not deferred to Phase 9.

---

## Part IV — The LEADER RULINGS of fold #3 (binding; folded, not re-litigated)

### LEADER RULING 1 (C-1f) — "first paint" = THE APP SHELL

> **"first paint" = APP SHELL** (normative — matches §10 criterion 8's
> operationalisation and the builder's `initial` budget basis). The
> page-scoped import-graph marker assertion is RETAINED anyway, so the D-1
> guarantee holds under BOTH readings. Pin + B4 threshold + G4 orphan-row fix
> land per the review's required changes.

**Folded as:** the D-1 row in `plan-overview.md` §3; §6.3's guard set (B1–B8);
Phase 1's tasks 1.2(c)/1.7/1.9; Phase 4's 4.4/4.14; Phase 5's 5.14; Phase 9's
9.8. **Concretely:** the wrapper's **final home** is
`frontend/src/app/pages/metrics/charts/chart-panel.component.ts` — **inside** the
`src/app/pages/metrics/**` tree the import-site rule covers — so the location
rule holds by construction (C-1a); the **shape** is gated too: **every**
`chart.js` import is the **dynamic** `await import('chart.js')` form and there
are **0** static `from 'chart.js'` sites (C-1b); B4 now has a **threshold**
(≤ 64 KiB min+gz) **and** a **baseline** (G4's 27,672 B route chunk + G4b's
496,338 B route fetch set) (C-1c); the **route fetch set** — not the route
chunk — is the measurement that catches a static-shape leak (C-1d); and the
guard carries **both** planted variants, eager **and** static-inside-the-lazy-
tree (C-1e).

### LEADER RULING 2 (C-2) — severity accepted as 🔴; the fix set is identical under both readings

**Folded as:** Phase 2's task **2.4 is SPLIT** (the tab bodies + the family
binding + the fetch + the availability surfacing stay in Phase 2; the registry
hand-off and the per-kind+unit-group **panel composition** MOVE to Phase 4's
new task **4.15**); the coupling map's 2↔4 cells read **tight** in both
directions, and Phase 2's + Phase 4's Coupling blocks say so; Phase 2's exit
criterion gains *"each of the 6 central tabs fetches its bound families and
surfaces their availability … no central tab is an empty shell"* (W-10); and
Phase 4's exit **re-asserts** 2.4's two-panels property.

### LEADER RULING 3 (C-4) — the fence is independently confirmed safe; the PORT re-export must be unconditional and POST-source

> the fence is independently confirmed safe (worktree .env, mode 600, carries
> POSTGRES_HOST=127.0.0.1, POSTGRES_PORT=5432, POSTGRES_DB=ensemble_dev,
> POSTGRES_USER=ensemble_dev_app, POSTGRES_PASSWORD (dev credential),
> DAEMON_PORT=8079 (INERT per AM-5), and NO `PORT` at all → no live-port
> binding path exists). STILL REQUIRED: the PORT=8079 re-export must be
> UNCONDITIONAL and POST-source (never inherited from a sourced env), and the
> effective port must be asserted FROM /livez, not from the intent.

**Folded as:** `plan-overview.md` §6.1's P7 + the ordered boot sequence;
Phase 9's task 9.2. **The order is the fix:** scrub → **source the worktree
`.env`** → assert the loaded `POSTGRES_DB == ensemble_dev` → **re-export
`PORT=8079` unconditionally, AFTER the source** → boot `python -m daemon` →
assert the effective port **from `/livez`** + the DB read-back + the
`ensemble.json` assertion. **C-4 was closed on CODE-PATH evidence, not on an
executed boot**; the three cheap falsification checks are named as
implementation-time assertions in Phase 9's task 9.2.

### THE FOLD-QUALITY RULE (the reviewer's systemic finding — binding on every landing)

> the prose landed; the task-graph entry, acceptance fixture, or numeric
> threshold did not — 4 of 7 weak landings were 🔴

**Folded as:** the `fold-audit.md` artifact — the machine-checkable landing
ledger carrying, per amendment and per finding: **task-graph entry · fixture/leg
· threshold · plan section · status**. Every one of the 29 prior amendments is
re-stated in that triple, and each of the 7 weak/partial landings (AM-3 → W-2,
AM-6 → W-13, AM-7 → C-2, AM-10 → W-1, AM-11 → W-3, AM-15 → C-1, AM-29 → W-7)
carries a **REPAIR** row. **Prose-only landings are not acceptable; a grep for
"AM-n present" does not count as a fold.**

## Part V — The APPROVER's verdict of fold #3 (iteration 001) and the fold #4 dispositions

### The verdict

**REJECTED (iteration 001)** — the approver's fresh-eyes independent review
(`approval.md` @ `e343cb5f6`, cross-check addendum @ `fefa938c5`): **5 BLOCKERS
(B1–B5) + 11 non-blocking warnings (W1–W11)**.

**What the verdict did NOT change (the approver's own words, §9.6):** *"The
architectural shape of the plan, the requirement coverage (1–8), the rulings
(D-1/D-2/D-3) and the FE-only discipline are **not** in question; every blocking
item is mechanical."* Requirement coverage 1–8 is **DELIVERED** (approval §1);
the rulings are **RESPECTED** (approval §2); the FE-only discipline, the
staging-only posture and the routed-out register (14/14) are all confirmed
(approval §3).

### The five blockers — what each turned out to be

| # | The blocker | The disposition (fold #4) | Lands in |
|---|---|---|---|
| **B1** | The bearer-token mint is the lane's only database **WRITE**, and it was **not fenced**: the mint resolves its own engine (`daemon/auth/cli.py:127 resolve_engine()` → `EnsembleConfig.load_or_create`) and `daemon/repositories/factory.py:346-350` resolves host/port/db/user/password from `os.environ` **over** the file's block — so the P4 pre-seed did **not** protect it while `POSTGRES_DB=ensemble_prod` was ambient, and with no worktree `data/` dir `load_or_create` **persists** a prod pin. The lane's live fence (symlink / `state.json` / uptime / "0 daemon requests") **cannot detect a DB write** ⇒ false green | The mint runs **inside the scrubbed shell** with `ENSEMBLE_DATA_DIR=/tmp/fe-t1-dev-data`; its **resolved** target is proved from **both** resolution inputs (no `POSTGRES_*` in the scrubbed env **and** `$.postgres.db == "ensemble_dev"` in the pre-seed) — never from the env var; the token is read back **from `ensemble_dev`**; and a **write-detecting live fence** records the live `ensemble_prod` `auth_bearer_tokens` row count **before and after** and requires **0 delta**. The scope is narrowed to `--scopes "metrics:read"` (least privilege; the door's ANY-OF accepts it) | `phase9` 9.3a (new task) + 9.1 + the exit criterion; `plan-overview` §6.1's L1 leg |
| **B2** | Phase 9's task graph contradicted its own prose: 9.3's `Depends On` read `9.2`, yet 9.2's boot step had to come **after** the auth exports (auth has no live toggle) | The order is now **in the graph**: 9.1 → **9.3a** (export + fenced mint) → **9.2** (boot) → **9.3b** (Leg L1's assertions); 9.11's self-dependency is gone | `phase9` (task table + exit criterion) |
| **B3** | The measurement tool located the route chunks by **content markers** (`'metrics-empty-panel'`, `'anomaly-marker'`) that Phases 7 and 8 invalidate ⇒ `read(undefined)` ⇒ the whole JSON is lost ⇒ B1–B4 become **unmeasurable mid-lane** | The tool is **route-keyed** (the lane-controlled marker constant Phase 2 exports) with a legacy fallback used only to reproduce the lane-base baseline, and the whole route section is **absent-tolerant** (null + `resolved_to_exactly_one: false`, never a throw, never a mis-resolution) — **verified** by re-running it against a marker-stripped dist | `bundle-baseline.measure.js` (rewritten) + `bundle-baseline.json`; `phase2` 2.13 (the marker constant); `phase9` 9.8 + the E-1 line ("the surviving route chunk(s)"); `plan-overview` §0.2 G5 + §6.3's B4 row |
| **B4** | §9's operator recipe **omitted `ENSEMBLE_AUTH_ISSUER`** — `AuthConfig.issuer` has no default and is not derived from `discovery_url` (`daemon/config.py:3736-3748`; `:3953-3954` adds `"issuer"` to `missing`) ⇒ the daemon **refuses to boot** (exit 78). §6.1 stated it correctly; §9 contradicted it | §9 step 2 now lists **six** vars including `ENSEMBLE_AUTH_ISSUER=https://<idp>/` with the **origin rule** (`daemon/config.py:3984-3988`); the cited runbook's env-incompleteness is **disclosed** (it lives outside the plan dir, so the gap is recorded + routed, not edited); **§9 is now the canonical operator-recipe source** | `plan-overview` §9 step 2 (+ §0.4's disclosure) |
| **B5** | Phase 5's exit ("the old directory is gone") contradicted Phase 7's 7.4 — `pages/resources/` holds `live/`, `timeline/`, `svg-timeline/` **and the page shell**, and the route still lazily imports that shell (`app.routes.ts:74`) | Phase 5's exit now says the **`live/` and `timeline/` subtrees are relocated and the page shell REMAINS until Phase 7**; **7.4 keeps the single statement of the directory's removal** | `phase5` exit; `phase7` 7.4 |

### The eleven warnings — dispositions

| # | Disposition |
|---|---|
| **W1** | **Folded** — P8b's mechanism is named: the **STARTUP-LOG read-back** (`daemon/ensemble_config.py:75` logs `Loaded ensemble config: database=postgres`; the daemon prints its resolved DSN) with **no new endpoint** (`daemon/routers/database.py` exposes only `POST /api/database/switch`) — a daemon change would break FE-only. `plan-overview` §6.1 P8b + `phase9` 9.1/9.2 |
| **W2** | **Folded as a DISCLOSURE** — the mini role has **no T1 page surface** (R-2): the five extensions are capability-retained, exercised via the mount-failure fallback and the renderer specs. `plan-overview` §9.1 + §0.4 |
| **W3** | **Folded as a DISCLOSURE, stated ONCE** — the baseline comparison is **central-tabs-only**; on tab 1 the toggle is disabled with a stated reason. It goes into the close-out as a disclosed narrowing of commission scope item 3. `plan-overview` §9.1 + §0.4 |
| **W4** | **Folded, stated ONCE next to the manifest** — "registration-only" means exactly two FE-local typed-table edits: the **family binding** + a **metric→unit vocabulary entry**. `plan-overview` §2 (and removed as a restatement from §4.2) |
| **W5** | **Folded with B2** — the `9.11` self-dependency is fixed |
| **W6** | **Folded** — the new import pin is **statement-anchored** (it matches import statements, not prose), so the wrapper's own doc comment — which must name the forbidden static form — cannot redden the phase-1 freeze; a planted leg asserts the doc comment stays GREEN. `phase1` 1.2(c) + its acceptance, `phase4` 4.14, `phase5` 5.14 |
| **W7** | **Folded — the defect is actually REMOVED** — the per-series min-max normalization is now **removed**, not merely named: every series in a panel is plotted against that panel's single shared domain, with the **numeric threshold 0 independently-rescaled series per panel** and a third planted variant that fails if any series is rescaled inside the memory panel. `phase6` 6.1 |
| **W8** | **Folded — re-targeted** — 6.6 no longer targets the page shell's subtitle (deleted in Phase 7); it targets the **Live strip's attribution card**, which survives the relocation (and already carries the honest *"attribution is partial by physics"* label); the shell's own per-instance promise dies with the shell in 7.4. `phase6` 6.6, `phase7` 7.4 |
| **W9** | **Folded — the interpreter is PINNED** — every python command uses `/home/nea/ensemble-src/.venv/bin/python` (Python 3.13.15, uv-managed), run from the worktree cwd: the worktree has **no** `.venv`/`venv` and **bare `python` does not exist on this host**, so `dev.sh`'s fallback chain would resolve to the uncertified system interpreter. `phase9` scope note + 9.2/9.3a + `plan-overview` §9 step 3 |
| **W10** | **Folded — the teardown is specified** — **DO NOT drop `ensemble_dev`** (it is shared); the teardown deletes **ONLY** the minted bearer row (scoped by the mint's own label) and nothing else, and the live `ensemble_prod` row count is asserted unchanged at teardown too. `phase9` 9.11 + the exit criterion |
| **W11** | **Folded — the two taxonomies are disambiguated** — LIVE/DARK (availability, the axis the tabs bind): LIVE = F0.3, F4, F5, F8, F9, F10, F11, F12, F13, F17; DARK = F1, F3, F6, F7, F18, F21. FULL/PARTIAL (adjudication coverage, a **different** partition): PARTIAL = F7, F8, F11, F13, F18, F21. **"6 partial" is NOT "6 dark"** — F8/F11/F13 are PARTIAL yet **LIVE**, which is why tabs 5 and 6 must render. `plan-overview` §0.2 G11 |

### What fold #4 did NOT touch (binding)

**No threshold moved and no rule changed.** D-1, D-2, D-3, the per-instance
top-N routing (routed row (m)) and the AM-14 narrowing are untouched; every
pre-existing numeric threshold (5,909,018 B · 90,982 B · 496,338 B · ≤ 2 kB ·
≤ 64 KiB · 27,672 B · 9 paths · 10 door classes · 7 tabs · 3 unit classes ·
0 files) is byte-identical. **The only numbers ADDED are the ones the
approver required**: B1(d)'s **0-delta live bearer-row count** and B1's
resolution proof, B3's **`route_chunk_resolved_to_exactly_one`** flag, W6's
statement-anchored pin, W7's **0 independently-rescaled series per panel**, and
W1's startup-log read-back.

## Part VI — Explicitly NOT decided here (and why)

| Item | Why not decided in this plan |
|---|---|
| Whether the family catalog is a drawer vs a sub-tab of a "more" tab | Settled in ADJ-i (frame-level drawer) — the alternative was considered and rejected for the 8th-tab contradiction. |
| Whether Chart.js covers the **central** tabs' trend panels | Settled in IMPL-04: the central panels ARE interactive panels (y-axis+units, crosshair, legend toggles, shared-y, drill) — the whole point of D-1. |
| Whether the anomalies panel belongs on the central tabs | Out of scope: the anomalies journal is a **resource-module** artifact (`/api/resources/anomalies`); no central family carries anomaly data. |
| Whether the mini tier gets a y-axis | **No** (ADJ-ii scope guard): a mini is sparkline-scale; axes belong to panels. Per **R-2** the mini role has **no page surface in T1** at all — it is exercised via the mount-failure fallback and the renderer specs. |
| The enable-auth operator decision | **User-owned** (D-2; the lane writes the recipe, it does not execute it on live). |
| The 7-tab family binding's currency (the 16-family census) | Code-verified at lane base (the registry SSOT). Per **E-1** the binding is re-derived at Phase 2's freeze against the registry file, so a daemon-side drift cannot silently change the tab table. |
| **Which "initial set" definition a promotion uses** (the builder's 36-chunk basis vs the whole payload incl. `index.html`) | Both readings are defensible; the **budget's own basis is the former** (`InitialCalculator` sums chunks; `index.html` is an asset). The plan uses the **builder's basis** (5,909,018 B) for every guard and states the whole-payload figure (5,918,275 B) alongside it. A house wording decision would be nice-to-have, not a blocker. |
| **The plan-review council's model mapping** (`decision` → `coding`) | Carried as a **disclosure** (`plan-overview.md` §0.4) and on the user's morning list — the findings stand (all 4 criticals independently re-verified by direct commands; 2 models converged over 2 rounds), but the **D7 proxy-side repair remains user-owned**. The fold author re-verified 9 of the findings independently (`fold-audit.md` §G). |

## Implementation-time rulings (2026-11-03, lane B4)

> These entries are **implementation-time rulings** taken during the lane's execution, on measured
> evidence. They do NOT amend the approved D-1 / D-2 / D-3 or any approved decision text above —
> they are recorded here, in their own section, so the approved record stays byte-identical.

### IMPL-21 — B4 part (2) gates on the LAZY-ONLY portion of the `/metrics` route fetch set

**The ruling (the leader's, on evidence — recorded verbatim, the threshold is NOT re-litigated):**

> B4 part (2) — "the /metrics route fetch set grows ≤ 65,536 B vs the 496,338 B baseline" —
> measures the growth of the **LAZY-ONLY portion** of the route fetch set (the route chunk + its
> direct static deps, RESTRICTED to members NOT in the initial set). The threshold (65,536 B) is
> UNCHANGED. The total remains reported for transparency but is NOT the gate. Rationale: D-1
> protects NEW bytes the /metrics page fetches; initial-set members are downloaded at startup
> regardless; the plan's own G4b reasoning targets the lazy-only sibling class; the eager case
> remains caught by B1 + the initial error budget.

**The measured composition evidence (re-derived from the real trees, not asserted):**

| Tree | route chunk | route fetch set (total) | SHARED with the initial set | LAZY-ONLY |
|---|---|---|---|---|
| the lane base (`7961bcb21`, the artifact's `measured_at_commit`) | 27,672 B | **496,338 B** | 423,630 B | **72,708 B** |
| the phase-2 freeze (`1474952fe`) | 39,332 B | **591,467 B** | **506,881 B** | **84,586 B** |
| the phase-3 freeze (`b0b1a1bcb`) | 50,872 B | 603,420 B | 506,881 B | **96,539 B** |

The phase-2 row reproduces the rider's numbers EXACTLY (591,467 / 506,881 / 84,586). The lane-base
row reproduces the frozen artifact's figures EXACTLY (`initial_total_bytes` 5,909,018 · route chunk
27,672 B · route fetch set 496,338 B · pre-retirement `/resources` chunk 38,914 B) — so the
lazy-only baseline is a MEASURED figure, not an estimate.

**Why the old wording would have failed.** On the TOTAL basis the phase-2 freeze already showed
591,467 − 496,338 = **+95,129 B**, a FALSE breach of the 65,536 B threshold — while the genuine
new payload of the route was only the route chunk's +11,718 B. On the LAZY-ONLY basis the same
tree shows 84,586 − 72,708 = **+11,878 B**, and the phase-3 freeze shows
96,539 − 72,708 = **+23,831 B** — both inside the unchanged 65,536 B threshold.

**The tool change (additive).** `bundle-baseline.measure.js` gained two fields:
`metrics_route_fetch_set_lazy_only_bytes` and `metrics_route_fetch_set_shared_with_initial_bytes`.
No existing field's semantics changed; the locator stays route-keyed and absent-tolerant; both
new fields are `null` exactly when the route chunk is unresolvable. The recorded baseline lives in
`bundle-baseline.json` under `guards.B4_new_chart_chunk_size` as
`baseline_metrics_route_fetch_set_lazy_only_bytes: 72708` with the derivation in its
`provenance` note (B8 — the artifact stays the single machine-readable source).

**Derivation path taken:** the REAL BUILD path (a throwaway detached worktree at the artifact's
`measured_at_commit`, `node_modules` symlinked from the lane worktree, the worktree removed +
`git worktree prune`d afterwards, the shared checkout `/home/nea/ensemble-src` never touched).
The plan's own analytic figure (27,672 + 43,688 + 1,348 = 72,708 B) was NOT needed as a
fallback — the real build agrees with it digit for digit.

### IMPL-22 — (AM-14, ruling) the baseline toggle renders ONCE, in the frame

**The ruling:** the baseline toggle renders **ONCE**, in the FRAME. When the custom slot
(the resources module) is active, the frame-level toggle is **disabled with its stated
reason**. A panel-level duplicate would be a SECOND copy source, which the copy SSOT
(IMPL-18 / AM-20) forbids.

**Why this satisfies PX-11:** PX-11 asks that "the custom tab's toggle is disabled with its
reason". The frame-level toggle IS the custom tab's toggle — it is the single owner of the
baseline state — and it carries the disabled state and the reason. The panel therefore does
NOT render a second toggle: a duplicate would let two controls edit one value, which is the
exact failure the single-copy rule exists to prevent.

**Scope:** this ruling is about the FRAME's baseline toggle. The central tabs' panels read
the frame's `baseline` input (the client-side split of the 2W payload) and never own the
toggle themselves.

### IMPL-23 — (AM-19, ruling) the frame's range control is INERT on the custom slot

**The ruling:** the frame's range control is **INERT** when the custom slot is active. The
custom panel's own labelled **"Timeline range"** control is the single LIVE control there.

**Why this satisfies PX-12:** AM-19 makes the range control's scope **per-panel**: the
central tabs' panels are governed by the frame's control, and the custom slot's panel is
governed by its own labelled control. Two simultaneously-live controls for ONE value would
violate single ownership — the operator could set the range in one place and read it in
another, and the two could disagree.

**Consequence for the phase-4 implementation:** the custom slot's panel carries its own
labelled control (`resource-range-select`, the "Timeline range" select) and the frame's
central-sourced control is disabled on that slot. The phase-4 renderer work does not change
either control; it only composes the panels the controls govern.

### IMPL-24 — (LEADER RULING, 2026-11-04) B4 re-baseline to the measured reality — an AUTHORIZED threshold change, not a silent weakening

**The ruling:** the Phase-9 measurement proved both B4 sub-thresholds were written against
estimates that reality outruns. The leader re-baselined them on the MEASURED numbers,
with the supersession documented in every ledger the numbers live in
(`bundle-baseline.json` guards.B4, `plan-overview.md` §0.2 rows G4b + G9, §6.3 row B4,
and this section). **The code council and the tester will both see this change in the
open and re-verify it independently.**

**(1) D-1 IS UNCHANGED AND GREEN.** D-1's binding contract is first paint = the app shell
(the builder's initial set); **B1 (Δ −1,301 B vs the ±2,048 bound) + B2 (0 `chart.js`
markers in the initial set) + B3 (0 static import sites, statement-anchored,
mutation-proven) remain THE D-1 enforcement — B4 was always second-order.** Nothing in
this ruling touches the initial set.

**(2) B4a (chart chunk) 65,536 → 82,000 B min+gz.** The 65,536 figure was G9's ESTIMATE
of chart.js alone. The MEASURED barrel-mandatory composition is **71,054 B gz**
(206,857 B min, `chunk-TXUCW6LI.js`). **Tree-shaking is structurally impossible under
the mandated dynamic-barrel form:** the D-1-mandated `await import('chart.js')`
materializes the WHOLE namespace object, so all controllers ship in the chunk —
Bar/Doughnut/Pie/Radar/Bubble/Scatter are all present despite the explicit
`Chart.register(...)` of only the used set (verified on the dist in Phase 4, re-verified
at Phase 9). The new pin is measured + ~15% margin for wrapper/plugin/theme growth.

**(3) B4b (route fetch set, lazy-only) 65,536-growth → 185,000 B on the POST-RELOCATION
basis.** Phase 5/7 RELOCATED the resources module INTO the metrics route — the route
chunk grew 27,672 → 120,154 B and the lazy-only fetch set 72,708 → **165,185 B**. That
growth is the lane's OWN D-3 deliverable, NOT vendor leakage; the vendor bytes reach the
page only via dynamic import and are NOT in the fetch set. Measured 165,185 → pin
185,000. The pre-relocation 72,708 B is retained in the artifact as
`baseline_metrics_route_fetch_set_lazy_only_bytes_PRE_RELOCATION_SUPERSEDED`
(provenance, not the gate).

**(4) THE LOAD-BEARING INVARIANT (new tool assertion, fails loud — exit 4):**
**`chart.js` vendor bytes in ANY static (non-dynamic) fetch set == 0.** The vendor chunk
is identified deterministically from the ROUTE-KEYED route chunk's dynamic-import
targets (exactly one on the Phase-9 dist — `chunk-TXUCW6LI.js`; if a future build
carries several, the assertion covers ALL of them), and the tool scans EVERY chunk for
static-import edges (`from "…"` / `import "…"`) targeting it — plus the initial-set
membership check. **Plant→red→restore→green proven** on the Phase-9 dist: a planted
`import"chunk-TXUCW6LI.js";` edge reddens the tool (exit 4, RED stderr, count 1),
restoring greens it (exit 0, count 0). The byte thresholds are sized to measured
reality; the INVARIANT is the structural catch that survives any future growth.

**Ledger discipline:** every changed row carries old value → new value and why — the
E-1 discipline (a label without a command is not a re-derivation) applied in reverse
(an estimate without a supersession record is not a correction).
