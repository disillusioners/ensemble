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

## Part II — Adjudication owed (six decisions, all made)

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
hour 3600 s/180 d (`daemon/services/flusher.py:360-368`, `daemon/config.py:811-827`;
`resource-refactor-architecture.md` §6.1 `:233`) — the tiers **backing** the
ladder, **NOT** picker options. The picker never offers 48h/14d/180d.

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

### IMPL-04 — Renderer roles: house SVG = mini + fallback; Chart.js = interactive panels

- **House SVG renderer** (ONE consolidated engine, merged from
  `svg-line-chart.component.ts` + `svg-timeline.component.ts`): serves the
  **mini** role (sparkline-scale, no axes grid, no zoom/brush) and the
  **never-silent fallback** role (a Chart.js mount failure surfaces a badge
  and falls back to SVG — never a silent downgrade,
  `resource-refactor-architecture.md` §10.6 three-layer fallback).
- **Chart.js wrapper**: serves the **interactive panel** role — value axis with
  units, crosshair + nearest-point snap, legend toggles, shared-y multi-series,
  `stepped: true` (the house counter-honesty rule), click → drill-down
  (`resource-refactor-architecture.md` §5.2). The **resource timeline migrates
  to it** (its anomaly markers become a house plugin — the severity colors
  verbatim from `svg-timeline.component.ts:169-173` — and its marker click
  becomes the wrapper's `pointClick`).
- **The intent + renderer registry** (shape B) is the contract:
  `trend | sparkline | stat` intents → a registry mapping intents to
  `ChartRenderer { capabilities, supports(), render(), teardown() }`
  (`resource-refactor-architecture.md` §10.3). Adding an engine later = one
  renderer file + one registry line; the manifest never changes.
- **The consolidation deletes one engine.** The 241 L + 237 L pure-helper
  specs are **ported** (not re-invented): the exported helpers stay exported
  and unit-asserted — that property is why the audit could cite them precisely
  (`fe-metrics-source-audit.md` §7.6).
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

### IMPL-06 — The redirect is a `canMatch → UrlTree` guard

The app's `redirectTo` idiom does not preserve query params
(`app.routes.ts:35,:104`); `canMatch` returning a `UrlTree` does
(`resource-refactor-architecture.md` §7). A 3-line redirect component was the
alternative; the guard is preferred because it keeps the route table as the
single source of route truth and needs no new component.

### IMPL-07 — The frame owns range + baseline; the baseline is an FE-only client split

Baseline = **2× window fetch + client-side split at the midpoint** (points
carry epoch seconds, `envelope.py:386-388`), working for **W ≤ 1 h** (2W ≤
7200). Disclosed fidelity limits, stated in the UI, not in a comment:
`bucket_max` downsampling runs over the whole fetched window so a bucket can
straddle the split; the older half sits at the ring's eviction edge; the
resolution halves. **Forged cursors are forbidden** (the cursor is unsigned
base64 JSON — out-of-contract and brittle; `resource-refactor-architecture.md`
§3.2). The first-class `end_offset_s`/`compare` backend param is **routed out**
(ADJ list, item f).

### IMPL-08 — Stat cards read the snapshot scoped by family, with truncation honesty

The frame fetches `/api/metrics/snapshot` once per poll cycle and each tab
filters its own stat-role families **client-side by the bound family names**
(the snapshot route also accepts `?family=` — `daemon/routers/metrics.py:661-668`
— and the FE service gains an optional `family` param, FE-only). If a bound
family is absent from a **bounded** snapshot page (`truncated` + `next_cursor`),
the card renders "not on this page" + the truncation hint — **never a fake
zero** and never a silent absence.

### IMPL-09 — The `AUTH_NOT_CONFIGURED` empty state is a distinct panel, not a banner variant

D-2 requires the FE to distinguish it from other dark causes. The current copy
(`metrics.model.ts:387-388`) is a one-line banner that names the cause but
offers no remedy, and the page renders TWO of them (page-level + tab-level)
plus two Retry buttons that can never succeed. Ship: **one** frame-level
actionable panel for `auth_not_configured` — cause, what it means ("no
credential can fix this — it is daemon configuration"), the runbook pointer
(`docs/runbooks/oauth2-auth.md`), and **no Retry button** (a config error is
not transient). The other four classes keep their banner + Retry semantics
(network/unexpected are transient; scope_insufficient is actionable but not a
config fix).

### IMPL-10 — The resource timeline's mixed-unit defect is fixed by SPLITTING, not by normalizing

The timeline plots RSS (MB), cgroup (MB) and CPU (%) on one unlabeled axis
with each series independently min-max normalized, so the area fills read as a
stacked-area chart (nonsense across units). Ship: **per-unit panels** — a
memory panel (MB, one shared scale, unit-labeled y-axis) and a CPU panel (%, its
own axis) — each with a labeled y-axis and a unit suffix. A single mixed-unit
axis with an unlabeled normalization is **rejected**: it is the defect.

### IMPL-11 — The attribution card states its basis, or says it cannot reconcile

The displayed numbers do not cohere (71 MB attributed + 1,119 MB unattributed
⇒ 1,190 MB implied total; daemon RSS 633 MB; cgroup current 6,178 MB; coverage
0 %). The FE **cannot invent a basis** — the payload's fields are what they
are. Ship: the coverage figure renders with the **basis it is computed on**,
and when the displayed components do not reconcile with the stated total, the
card **says so explicitly** ("these figures do not reconcile against the
daemon RSS reading — the daemon process is not the whole cgroup") instead of
presenting a 0 % next to numbers that imply ~6 %. The **per-instance top-N list**
(the `instances[]`/`children[]` payload that is fetched every 8 s and never
rendered — `resource-tab-forensics.md` A3) is **deferred to a follow-up
commission**: it is a NEW capability (top-N? sort? click-through?), the
commission's scope item 6 named the math and the coverage figure specifically,
and the payload is already present so the deferral is cheap.

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

### IMPL-14 — The verification route is a fenced DEV daemon on :8079, with two auth legs

See `plan-overview.md` §Verification and `phase9-plan.md`. Two legs:
**L1 (data-bearing)** — dev daemon, auth ON with a minted operator bearer
(`maintenance:read`) presented by the browser context's
`extraHTTPHeaders` (**no production FE change**); **L2 (auth-absent)** — the
same dev env with auth OFF → 503 `AUTH_NOT_CONFIGURED` → the actionable
empty-state. **Never** the live daemon; never `:9797`; live config out of
bounds.

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

---

## Part IV — Explicitly NOT decided here (and why)

| Item | Why not decided in this plan |
|---|---|
| Whether the family catalog is a drawer vs a sub-tab of a "more" tab | Settled in ADJ-i (frame-level drawer) — the alternative was considered and rejected for the 8th-tab contradiction. |
| Whether Chart.js covers the **central** tabs' trend panels | Settled in IMPL-04: the central panels ARE interactive panels (y-axis+units, crosshair, legend toggles, shared-y, drill) — the whole point of D-1. |
| Whether the anomalies panel belongs on the central tabs | Out of scope: the anomalies journal is a **resource-module** artifact (`/api/resources/anomalies`); no central family carries anomaly data. |
| Whether the mini tier gets a y-axis | **No** (ADJ-ii scope guard): a mini is sparkline-scale; axes belong to panels. |
| The enable-auth operator decision | **User-owned** (D-2; the lane writes the recipe, it does not execute it on live). |
