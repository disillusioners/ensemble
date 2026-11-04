# Metrics-UI Refactor — Architecture Proposal (read-only pass, no implementation)

**Date:** 2026-11-03 · **Author:** architect (controller) — aggregation of 3 dispatched verification workers (data-flow-design, system-decomposition, trade-off-analysis) over the two prior forensics passes.
**Tree basis:** all `daemon/` and `frontend/` citations are from `/home/nea/ensemble-src-wt-stage-v0289/` @ `7fcf1bd71` (= v0.28.9 tag target = origin/latest), unless prefixed `origin/latest:` or explicitly said otherwise. The main checkout is stale (v0.27.8-era) for central-metrics code. `/resources` FE files are byte-identical in both trees.
**Status:** PROPOSAL ONLY. Read-only pass: zero writes to tracked source, zero builds, zero npm, zero live-daemon contact, zero git operations.
**Ratified direction (settled, not re-litigated):** one Angular page, predefined tabs (one per module); `/resources` retained as tab 1; Prometheus-text export stays an optional power-user escape hatch; no Grafana menu.

---

## §0 Executive recommendation

Build **one `/metrics` page driven by a FE-local, typed tab manifest** with a discriminated `central | custom` data-source slot: central tabs bind to family IDs already served by `/api/metrics/families` (so adding *series* to an existing tab is registration-only), while tab 1 (Resources) mounts the existing resource components over their own 6 endpoints behind the `custom` slot. Ship **7 tabs now** (Resources, Runtime, DB Pool, Jobs/Queue, Instances, Checkpoint, MCP) — every one backed by families that are verifiably LIVE — and hide not-yet-sourced modules entirely (no dark shells), with the manifest carrying their planned state for later waves. The page frame owns a **time-range selector with per-tab window vocabularies** (15m–2h for central tabs, hard-capped by a *substrate* ceiling — the ring retains exactly 480 × 15 s = 2 h — and the resource tab's own 15m/1h/24h/7d PG ladder) plus a **baseline comparison** that is FE-only in T1 via a 2× window fetch + client split (works for windows ≤ 1 h), with a small optional additive backend param (`end_offset_s` / `compare`) as the first-class follow-up. Drill-down is **FE-only and generic**: clicks emit `(series, full-labels, timestamp)` and the manifest maps family → route/target; no central family carries per-entity labels (by ratified rule), so instance-level drill-down stays on tab 1's existing traceback path. Retire the `/resources` standalone route in the **same release** that mounts tab 1, redirecting to `/metrics?tab=resource`.

**The (e) answer, stated flat out: adopt ONE small charting dependency — Chart.js v4 (MIT, tree-shaken, lazy chunk) — behind a single Angular wrapper, and retire both hand-rolled SVG engines.** The two-engine divergence precedent (the newer engine is strictly less capable than the older one it duplicated) plus the T1→T3 tab/series scaling requirement makes hand-rolling the wrong default; ~60–70 % of the needed capability is chart-library-shaped and Chart.js ships it natively (value axis, crosshair + nearest-point snap, legend + toggles, multi-series scales, click events, `stepped` mode preserving the house counter-honesty rule), while the app-domain gaps (units vocabulary, anomaly source, drill-down destinations, CSV) cost the same under either option. One flip condition is carried to §9: **if T1 ships single-series shape views with interaction deferred to T2+, the recommendation flips to Option A** (merged hand-rolled engine, ~130 LOC marginal).

---

## §1 (a) Tab contract — the shared tab manifest

### 1.1 Manifest shape (TypeScript sketch)

Lives at `frontend/src/app/pages/metrics/tabs.manifest.ts` — FE-local, typed, compiled, lazy-loaded with the page. Shape:

```ts
/** FE-local, typed, versioned. NOT served by the backend. */
export interface MetricsTabManifest {
  readonly version: 1;                     // FE-only manifest schema axis
  readonly tabs: readonly MetricsTabDef[];
}

export type MetricsTabDef = CentralDataTab | CustomDataTab;

/** The CONFIG promise: a central tab is a binding, not a component. */
export interface CentralDataTab {
  readonly kind: 'central';
  readonly id: string;                     // stable slug for ?tab= deep links ('db-pool', …)
  readonly title: string;                  // gear/tab label
  readonly order: number;                  // resource tab = 0 (retained-first, per ratified direction)

  // --- data binding (by family id; new families in a wired module appear WITHOUT an FE commit) ---
  readonly familyIds: readonly string[];   // e.g. ['F4', 'F5', 'F6'] — F6 joins when R-i lands
  readonly seriesFilter?: readonly string[];          // optional name-level filters feeding ?names=
  readonly statFamilyIds?: readonly string[];         // stat-role families rendered as cards, not charts

  // --- presentation ---
  readonly chartType?: Partial<Record<string, 'line' | 'step'>>;  // default derived from chart_role on the wire
  readonly defaultWindowS: WindowPreset;               // 900 | 1800 | 3600 | 7200
  readonly allowedWindowsS: readonly WindowPreset[];   // central tabs: ceiling 7200 (see §3)

  // --- cross-cutting ---
  readonly baseline?: { enabled: boolean };            // page-frame comparison toggle (T1: client-split)
  readonly drillDown?: readonly DrillDownMapping[];    // see §4
  readonly emptyState: { title: string; body: string };

  // --- ship-only-live gate (see §2) ---
  readonly availability: 'live' | 'planned';           // 'planned' tabs are NOT rendered
  readonly liveWhen?: { minLiveFamilies: number };     // rendered only while ≥N bound families are live
  readonly trackingNote?: string;                      // planned tabs only: gap + owner (module onboarding record)
}

/** The escape hatch: tab 1 (resource) and any future non-central module. */
export interface CustomDataTab {
  readonly kind: 'custom';
  readonly id: string;                     // 'resource'
  readonly title: string;
  readonly order: number;                  // 0 — first tab
  readonly loadComponent: () => Promise<unknown>;   // lazy Angular component (the existing resource tab bodies)
  readonly allowedWindows: readonly ('15m' | '1h' | '24h' | '7d')[];   // the resource page's OWN ladder vocabulary
  readonly emptyState: { title: string; body: string };
}

export type DrillDownMapping =
  | { familyId: string; on: 'point' | 'marker';
      target:
        | { type: 'route'; route: string; paramsFromLabels: Record<string, string> } // label key → route param
        | { type: 'tab'; tabId: string }
        | { type: 'window-probe'; endpoint: '/api/resources/traceback' | '/api/resources/anomalies'; }; }
```

Deliberately minimal: everything derivable from the wire (`chart_role`, `role`, labels vocabulary, availability, buckets) **is read from the wire at runtime** — the manifest never duplicates it. The manifest adds only what the backend cannot know: tab grouping, titles, ordering, window defaults, drill-down destinations, empty-state copy, and the planned/live onboarding state.

### 1.2 Where the manifest lives — FE-local (recommended) vs BE-served

| | FE-local typed config (recommended) | BE-served (from family registration metadata) |
|---|---|---|
| Onboarding a module's *series* into an existing tab | registration-only (manifest binds by family id; families payload already carries `role`/`chart_role`/labels — `provider.py:220-260`) | same |
| Onboarding a *new tab* | FE commit (append one manifest entry) | registration-only **after** backend work |
| Backend surface added | **none** | shape (A): one optional field on the families envelope + registration kwarg + exact-field-set pin update (`tests/unit/metrics/test_metrics_query_provider.py:137-147` asserts `set(family) == {…}` exactly — it reds until re-pinned). Shape (B) — a new route — breaks the frozen `query-routes` fence (`docs/architecture/metrics-query-contract.md:225-229`, machine-pinned by `tests/unit/metrics/test_central_metrics_contract_doc.py:146-147`): forbidden. |
| Frozen-contract impact | zero | small but real: AM-7 additive policy applies (new optional field ≠ `schema_version` bump, `constants.py:25` = 1); must NOT bump `VOCABULARY_VERSION` (that axis tracks closed *label values* only — `checkpoint_metrics.py:218` = 3). The §4.3 family-shape block is *not* machine-pinned, so the contract doc itself needs no edit under shape (A). |
| Versioning concern | none (compiled with the FE; changes ride the FE release) | a second versioned surface to keep aligned with the FE |
| Fit for tab 1's custom source | natural (`custom` slot) | awkward — the backend would have to describe a page it does not serve |

**Recommendation: FE-local for T1–T2.** A new tab is a deliberate UI decision at current cadence (a handful of modules per wave); an FE commit is the honest cost. The door already serves everything a *binding* needs, so the "config, not code" promise holds where it matters: **adding series to a wired module is registration-only today.** Flip to BE-served (shape A) only when module onboarding cadence makes FE commits a real bottleneck — the path is designed and small (one optional field + one pin update), and is flagged backend-touching in §8.

### 1.3 The custom-source slot and its cost

Tab 1 (and only tab 1 for the foreseeable future) uses `kind: 'custom'`: it lazy-mounts the existing Live/Timeline components over `/api/resources/*` (§6.2). Cost, stated plainly: **the "config not code" promise covers `central` tabs only.** A `custom` tab is code-backed by definition — its data source, window vocabulary, and drill-down are component-owned; the manifest contributes placement, title, and deep-link id only. This is the cheapest honest boundary: pretending the in-process ring were central would require the rule+budget work in §6.1 and buy nothing for T1. The slot is typed and closed (`CentralDataTab | CustomDataTab`) so future non-central modules (none planned) have a sanctioned shape rather than a hack.

---

## §2 (b) Ship-only-live-tabs — census, T1 list, no-source policy

### 2.1 The 16 registered families (verified in-tree; LIVE/DARK = documented 2026-11-02 production observation, mechanisms code-verified)

Registry SSOT: `daemon/services/metrics_query/wiring/registry.py:64` (`FAMILY_SPECS`); verdicts `wiring/adjudication.py:51-78` (registered set `:84-101`); chart roster `wiring/roster.py:67-202` (F2 dropped `:204-206`); availability model `availability.py:65-98`.

| F-id | Family | Module | LIVE/DARK (reason) | Labels | Role |
|---|---|---|---|---|---|
| F0.3 | resource spine (`…spine_tables_accepting_rows`, `…spine_rows_written_total`) | resource_monitor (`w1.py:56-92`) | **LIVE** (spine-health leg broken — routed defect, `w1.py:366-367` calls `@property` counters with parens → always "not accepting") | `table` | stat |
| F1 | loop lag | wedge_capture (`w1.py:94-152`) | DARK `capture_off` — **FALSE-dark** (9 live series publish; `capture_active` never wired, `api.py:2528-2533` absent) | `stat`,`state` | chart |
| F3 | wedge captures | wedge_capture (`w1.py:157-189`) | DARK `capture_off` — **FALSE-dark** | `reason`,`outcome` | stat |
| F4 | db pool native | readiness pool sampling (`w1.py:194-228`) | **LIVE** | `pool`,`quantity` | chart |
| F5 | db pool derived | wedge collector (`w1.py:238-268`) | **LIVE** | `pool` | chart |
| F6 | PostgreSQL census | metrics_census (`w1.py:275-334`) | DARK `census_gated` — **FALSE-dark** (polarity inverted, `metrics_census.py:323-335` vs `provider.py:145`) | `state`,`site` | chart |
| F7 | checkpoint op bytes | persistence call site (`w2.py:70-88`) | DARK `series_absent` | `op` | chart |
| F8 | checkpoint table bytes | maintenance gauges (`w2.py:89-114`) | **LIVE** | `table` | chart |
| F9 | readiness | readiness composite (`w2.py:120-182`) | **LIVE** | `component`,`outcome` | chart |
| F10 | job queue depth | job_queue admission (`w2.py:188-229`) | **LIVE** | `admission_state` | chart |
| F11 | worker pool | worker_pool get_stats (`w2.py`) | **LIVE** (PARTIAL registration) | `pool`,`event` | chart |
| F12 | sweeps | sweep counters (`w2.py`) | **LIVE** | `lane`,`outcome` | stat |
| F13 | instances census | instances + waiting-children (`w2.py:392-414`) | **LIVE** (aggregate by status/liveness — NOT per-instance) | `status`,`liveness` | chart |
| F17 | MCP server health | mcp/init_guard (`w3.py:51-88`) | **LIVE** | `server`,`state` | stat |
| F18 | mcps gateway sessions | gateway + audit (`w3.py:93-135`) | DARK `series_absent` | `outcome` | chart |
| F21 | upgrade journal twin | upgrade_journal gauges (`w3.py:139-175`) | DARK `series_absent` (needs an install dir) | `version` | chart |

OUT by verdict (`adjudication.py:105+`): F2 (thread CPU/RSS, `:57`), F14 (LLM tokens, `:69`), F15 (LLM latency, `:70`), F16 (`:71`), F19 (`:74`), F20 (`:75`), F22 (`:77`) — names force-absent.

### 2.2 T1 tab list (ships NOW with real data — 7 tabs)

| # | Tab | Feeds | Primary user question |
|---|---|---|---|
| 1 | **Resources** (`custom`) | own 6 endpoints (§6.2) | "what is the daemon costing right now, and what spiked?" |
| 2 | **Runtime** | F9 (+F1/F3 when R-ii lands) | "is the daemon serving green — and why not when it isn't?" |
| 3 | **DB Pool** | F4 + F5 (+F6 when R-i lands) | "are pool waits or PG backends degrading the daemon?" |
| 4 | **Jobs / Queue** | F10 (+F12 sweeps) | "is work queuing up or dying in the DLQ?" |
| 5 | **Instances** | F13 (+F11 worker pool) | "what is the fleet's status mix; are workers saturated?" |
| 6 | **Checkpoint** | F8 | "how fast is the checkpoint blob store growing?" |
| 7 | **MCP** | F17 | "which MCP servers are degraded/quarantined right now?" |

**Blocked-by-defect (do NOT ship against `dark`):** the Loop/Wedge families (F1/F3) and the PG census (F6) publish live series while the door reports dark — any consumer treating `dark` as "no data" is wrong for these three (routed R-i/R-ii, still present in this tree). T1.5: once R-i/R-ii land, F6 joins the DB Pool tab and F1/F3 join Runtime **with zero FE change** — the manifest already lists them; the per-family availability display handles the rest.

### 2.3 Modules with NO live source — hide entirely (recommended)

**Recommendation: hide the tab entirely; do NOT ship a one-line placeholder row.** Rationale: (i) the user has signaled "no dark shells" — a placeholder row *is* a dark shell with extra steps; (ii) the page already owns an honest availability surface — the Families tab lists every registered family with live/dark + reason (`families-tab.component.ts:64-69`), and planned-but-unregistered modules belong to the roadmap, not the runtime; (iii) the manifest keeps the memory: a `planned` entry (id, title, `trackingNote`) renders nothing but documents the module for the onboarding wave, so "adding the tab later" is flipping `availability: 'planned' → 'live'` plus the family binding.

Gaps and their class (all metrics-lane, not FE — §6.3): LLM token usage (F14 OUT — `usage_metadata` captured then dropped, "zero consumers ref-wide"), LLM request latency (F15 OUT — only WARNING log lines today), source circuit-breakers/token-buckets (uncatalogued; readable state exists — `daemon/sources/circuit_breaker.py:110-112` `get_state()`, `daemon/sources/rate_limiter.py:77-90` `available_tokens()` — but denials are uncounted and both are process-local; a new family is Tier-3 instrumentation in the same class as F14/F15).

---

## §3 (c) Page-level cross-cutting — time range + baseline

### 3.1 Time-range selector: page frame ABOVE the tabs, with per-tab window vocabularies

**Substrate ceiling, verified:** the central door clamps `window_s` silently to `WINDOW_SECONDS = 7200` (`daemon/services/metrics_query/constants.py:33`; enforced `routers/metrics.py:778`, re-clamped `envelope.py:201-202`, `provider.py:350-351`). The clamp is a *substrate* ceiling, not just policy: the ring's depth rule computes `depth = max(60, min(POINT_BUDGET÷divisor, window_s÷effective_interval))` (`ring.py:72-101`; `POINT_BUDGET = 750 000` @ `constants.py:29`, 16 B/pt `ring.py:64` ⇒ 11.4 MiB worst case) = **480 points × 15 s effective interval = exactly 2 h retained**, with age eviction enforcing the same horizon (`sampler.py:254`). Effective interval = `max(sampler 10 s, publisher 15 s) = 15 s` (`config.py:825-826`, `checkpoint_metrics.py:128`). **The FE's hardcoded 3600 s is a UI choice** (`charts-tab.component.ts:195`) — raising central tabs to 7200 s costs zero backend work (resolution halves to 30 s at the default `max_points` 240).

**Design:** the page frame hosts the selector; the manifest declares each tab's `allowedWindows`. Central tabs: **15m / 30m / 1h / 2h** (2 h = ceiling). Tab 1 (Resources): its own existing ladder **15m / 1h / 24h / 7d** served by the PG tier (`resources.py:108`, `models/resources.models.ts:16`) — the selector swaps vocabulary when the active tab changes, disabling options beyond the active tab's ceiling. This is *visible* enforcement — deliberately contrasting with the door's silent clamp (a 7 d request on a central tab today returns a 200 with a 2 h window and no marker; `routers/metrics.py:778`). Windows beyond 2 h on central tabs would require `WINDOW_SECONDS` growth (ring depth + `POINT_BUDGET` + eviction + cursor-expiry semantics all move together) or a PG-backed tier — **flagged, not recommended for T1** (§8).

### 3.2 Baseline / previous-window comparison

**Not expressible on the REST door today.** `window_s` anchors at the server's `generated_at_utc` (`envelope.py:203,226-227`; contract `:200,:305-308`); there is no absolute-time param and no `from`/`to`/`at` anywhere in the router's query-param reads (`routers/metrics.py:287,318,456,667,668,769,780`). **Correction to the prior audit:** `delta_change` is NOT on the series door — it lives on the *agent tool* `metrics_history` (`daemon/tools/metrics_read.py:1151-1158`, compare leg `:1124-1132`, `end_offset_s` clamp `:1028,1100`); the FE audit's attribution (`fe-metrics-source-audit.md:152`) would have commissioned the wrong backend work.

- **T1 (FE-only, recommended): 2× window fetch + client split.** Fetch `window_s = 2W`, split at the midpoint (points carry epoch seconds, `envelope.py:386-388`). Works for **W ≤ 1 h** (2W ≤ 7200). Disclosed fidelity limits: `bucket_max` downsampling runs over the whole fetched window (`downsample.py:31-50`) so a 30 s bucket can straddle the split; the older half sits at the ring's eviction edge; resolution halves.
- **Do NOT forge cursors.** The cursor is unsigned base64 JSON (`envelope.py:270-289`; validation only of `t`/`o`, `routers/metrics.py:299-307`) so a client *could* mint `{"t": now-3600, "o":0}` to shift the window — out-of-contract (the contract calls it opaque, `:291-294`) and brittle against any future signing change. Explicitly rejected.
- **T1.5 (optional, small, additive backend):** one optional `end_offset_s` param on `/series` (clamped `[0, 7200 − window_s]`, mirroring `metrics_read.py:1028`) and/or `compare=1`, emitting `compare_window` + per-series `delta`/`prev_delta`/`delta_change`/`delta_reason` mirroring the tool's contract (`metrics_read.py:1145-1160`). The provider already accepts an arbitrary `now` (`provider.py:352,378`) ⇒ **no ring growth, no provider change, no `schema_version` bump, no `VOCABULARY_VERSION` bump**. Supports baselines up to W = 2 h. Flagged backend-touching in §8.

---

## §4 (d) Drill-down — chart → instance/job detail

**Series identity model (verified):** a series is `(name, labels)`, labels the complete discriminator, canonical `_label_key` sorted pairs (`checkpoint_metrics.py:492-494`; contract §4.2/AM-1). All 16 families carry **closed** label vocabularies (the registration helper *rejects* a label key without `allowed_values`, `wiring/registry.py:102-114`). **No family carries `instance_id`/`job_id` or any per-entity label** — per-instance attribution is central-excluded by ratified rule A10/A18 (`.agents/shared/planning/metrics-collection/metric-catalog.md:1168-1169`); F13 is status *counts*, not per-instance series (`w2.py:392-414`).

**Entity filtering:** the door's label-aware selector `?series=name{k=v,…}` exists (`selectors.py:32-61`; parser → `400 invalid_label_filter` on malformed, `routers/metrics.py:763-767`) — **but matching is exact label-set equality, not subset filtering** (`selectors.py:77`). A partial selector against a multi-label family silently matches nothing. Bounds are a non-issue for drill-down fetches (one series, narrow window ⇒ ≤ 240 of the 12 000-point budget, `constants.py:78`, `envelope.py:238-257`; 1 of the 500-series cap).

**Recommendation — generic drill-down is FE-only and needs NO backend enrichment:**
1. The chart wrapper emits `pointClick = { seriesId, name, labels (complete set), tsMs }` — the complete label set is already on the FE (it fetched the series).
2. The manifest's `drillDown` mappings resolve the click: label values → existing routes (e.g. F13 `{status=…}` → `/instances`; F10 → `/jobs`; F17 `{server=…}` → `/mcp-servers`), or → another tab, or (tab 1 only) → the existing **window-probe** drill-down — `/api/resources/traceback?t0&t1` + `/anomalies/{id}/evidence` (`timeline-tab.component.ts:185-199` already does exactly this).
3. Two rules the FE must obey: **never build a `?series=` selector from a partial label set** (exact-equality trap, above); and click-on-spike on an *aggregate* series lands on the module's list page filtered by the label value, not on a specific instance — that is the honest limit of aggregate telemetry. Instance-level drill-down lives on tab 1 (the 9-table traceback join), which the refactor keeps intact.
4. Backend enrichment (entity links in series metadata) is **not needed for T1** and not recommended: without per-entity labels there is nothing to link; when/if a per-entity surface is ever ratified (§6.1), links become trivial.

---

## §5 (e) The charting decision

### 5.1 The verified ground truth

- **Two engines, ~1,061 lines, ~106 lines of identical duplicated helpers** (6 pairs: scale fns ×2, path builders ×2, UTC `HH:MM` formatters ×2, `clamp01` ×2 — `svg-line-chart.component.ts:108-271` vs `svg-timeline.component.ts:90-167`). Both **pre-declared closed** at authoring time (`svg-line-chart:24-26` "FEATURE-FROZEN: zoom / brush / crosshair / gradients are OUT"; `svg-timeline:26-27`).
- **The newer engine is strictly less capable than the older** (audit §7.1): no window picker (`charts-tab.component.ts:195` hardcoded 3600), no series toggles, no anomaly markers, no drill-down, no y-axis labels/units — while `svg-timeline` has all four (`timeline-tab.component.ts:60,:125,:176`; `svg-timeline:235-267,:392`).
- **The zero-dep posture is double-enforced:** `metrics.bindings.pins.spec.ts:94` bans `['chart.js','d3-',"from 'd3'",'ngx-charts','moment','dayjs']` in metrics sources — **and** pack Leg 7 (`test/packs/metrics_fe_dual_side_test.sh:275-286`) hard-FAILs on *any* `frontend/package.json` diff vs the base ref. Adopting ANY dependency — uPlot included — is a deliberate posture change amending both.
- **Budgets:** `angular.json:64-75` — initial 1 MB warn / 6 MB error; anyComponentStyle 8 kB/24 kB. **No `anyBundle` budget exists** ⇒ the lazy `/metrics` chunk (`app.routes.ts:58` lazy `loadComponent`) is measured by nothing configured — a governance hole to disclose, not exploit.
- **The app is ZONELESS:** `zone.js` is in `package.json:60` but vestigial — no `polyfills` entry, no polyfills.ts, `app.config.ts:10-18` registers no zone provider; Angular v21 defaults zoneless. Canvas-lib imperative redraw outside Angular CD is therefore unentangled, *provided* the rule is written down: no Angular-rendered overlay may depend on a library redraw.
- **The FE is NOT dependency-free:** `mermaid ^11.4.0` (pulls d3 7.9.0 transitively, `package-lock.json:7318`), ng-zorro-antd, codemirror suite. Only "the metrics lane added zero deps" was ever true (`CHANGELOG.md:141`).
- **Test surface:** 1,645 L of metrics specs (incl. 241 L asserting the SVG engine's exported pure functions, and e2e `metrics-gate.spec.ts:277-298` asserting `d`-attribute internals that cannot survive a canvas). Resources specs 1,375 L. `@axe-core/playwright` is used elsewhere (`e2e/maintenance-checkpoint-cleanup-wizard.spec.ts:61,:470`) but NOT gated on /metrics.

### 5.2 Option comparison (5 axes; sizes directional, not session-verified)

| | A: one shared hand-rolled engine | B: Chart.js v4 (recommended) | uPlot | lightweight-charts | ECharts | ngx-charts |
|---|---|---|---|---|---|---|
| Complexity | ~480-720 impl + ~440-705 test LOC **new** on a 550-580 L merged baseline ⇒ ~1,030-1,300 impl LOC owned forever | ~250-450 impl + ~150-300 test LOC of wrapper/plugin | hand-write the tooltip/snap layer anyway + lose STEP | wrong domain (OHLC) | disqualified (weight) | d3-based, style clash |
| Scalability (T1→T3 tabs/series) | every capability wave is bespoke geometry | later tabs are configuration | perf irrelevant at ≤240 pts/series | — | — | — |
| Maintainability | 🔴 divergence precedent: house declares charts closed at authoring time, then duplicates them | library owns the hard parts (crosshair snap = classic hand-rolled defect) | thin wrappers forever | — | — | — |
| Risk | re-declared-closed at T2; a11y strong (SVG `role="img"` + per-point `<title>`) | canvas opaque to a11y → mitigated by required DOM proxy + hidden data table; jsdom needs a canvas-mock devDep | no built-in legend/tooltip; no STEP | — | — | — |
| Cost | no dep; pins untouched | one MIT dep in the lazy chunk; posture amendment in the same commit (pins spec `:94` + pack Leg 7 + `metrics-fe-charts.md:126-128,:144` + `metrics-promote-disclosure.md:122` item #2; `CHANGELOG.md:141` stays — historical) | same amendment, less capability | — | — | — |

**Why Chart.js v4 and not uPlot:** uPlot's headline is 45 KB and performance — both irrelevant at ~240 points over ≤ 2 h — while it ships **no legend, no tooltip, no crosshair** and **no step mode**: adopting it means hand-writing exactly the nearest-point-snap layer Option A must build, *plus* paying the dependency and the posture amendment. Chart.js natively covers ~60-70 % of the T1 gap list — value axis + unit suffix, tooltip with `interaction:{mode:'index',intersect:false}` (nearest-point snap free), legend with click-toggle, multi-dataset/shared+dual y-axes, `stepped: true` (preserving the house counter-honesty rule, `svg-line-chart:17-19`), `onClick` → `(datasetIndex, index)` → `(seriesId, ts)`, and a **linear x-axis with a custom UTC tick callback so NO date-adapter dependency is needed**. The residual ~30-40 % (anomaly overlay as a house plugin ~80-120 L — *not* `chartjs-plugin-annotation`, which would be a second dep; CSV ~25-40 L; units vocabulary; drill-down destinations) is app-domain work costing the same under either option.

### 5.3 Decision

**Option B — Chart.js v4.** Dominant axis: **Maintainability**, reinforced by Complexity and Cost. The T1→T3 argument makes it decisive rather than marginal: T2/T3 is where multi-series + 10-series legends + comparison make the shared-scale/toggle/crosshair set mandatory *all at once* — the moment Option A must grow ~600 L of bespoke interaction code on top of a spec suite that pins the old shape view. Under Option B those tabs are configuration and the engine stays one wrapper. The zero-setup product goal argues FOR B, not against: the constraint is on *external services*; an embedded MIT library in a lazy chunk satisfies it exactly.

**Required in the plan, not optional:** (i) the wrapper must emit a per-series DOM proxy (preserving `data-series`/`data-render-mode` hooks) **and** a visually-hidden data table — restoring the a11y surface the canvas removes (improving on today), keeping DOM-assertion gates usable; (ii) `chart.destroy()` in `ngOnDestroy` is mandatory — the shell destroys tab bodies via `NgComponentOutlet`/portal detach, and a missing destroy leaks canvas + ResizeObserver per tab switch; (iii) a jest canvas-mock devDep for jsdom; (iv) the posture amendment lands in the SAME commit as the dependency with recorded rationale.

**Flip condition (carried to §9):** if T1 ships single-series shape views with interaction deferred to T2+, Option A wins (merged engine ≈ 550-580 L, ~130 L marginal over the merge, no amendment) — the entire recommendation rests on T1 genuinely shipping the interactive affordances. Second flip: if the user ratifies zero-npm-deps as a standing FE-level product position, B is off the table and A (with "ONE engine; the feature-frozen list is a spec, not a default" as a house rule) is the answer.

### 5.4 Migration path for the two engines

| Asset | Fate |
|---|---|
| `components/charts/svg-line-chart/` (523 L + 241 L spec) | replaced by `components/charts/chart/` wrapper (standalone, OnPush, `input()` signals — house idiom). STEP semantics via `stepped: true`; UTC-never-local via the tick callback. Pure helpers + their spec deleted; pinned behaviors re-pinned at wrapper level. |
| `pages/resources/svg-timeline/` (538 L + 237 L spec) | retired; behavior migrates in full: anomaly markers → house Chart.js plugin (severity colors `svg-timeline:169-173` verbatim); click→drill-down → wrapper `pointClick` output (payload `{anomalyId?, seriesId, tsMs}`); `anomalyDrilldownWindow` (`:153-162`, pure, tested) moves OUT of the chart into the tab (it is drill-down policy); series toggles stay parent-owned (`hiddenSeriesIds` input → `chart.setDatasetVisibility`); window presets stay tab-level. |
| Pins + docs | `metrics.bindings.pins.spec.ts:94`, pack Leg 7, `metrics-fe-charts.md:126-128,:144`, `metrics-promote-disclosure.md:122` amended same-commit; `CHANGELOG.md:141` untouched. |

Bonus leverage: the Live tab's unrendered `instances[]`/`children[]` payload (fetched every 8 s, never rendered — forensics A3) becomes a cheap small-multiple addition under B (a third bespoke path under A).

---

## §6 (f) Backend-touching flags

### 6.1 Tab 1 per-instance data through the central door — NO (for T1)

The resource family in the registry is **F0.3 spine health only** — row counts + spine health, *never a resource measurement* (`w1.py:56-92`, own SQL `:346-352`). No per-instance/per-process CPU/RSS family exists: F2 is OUT and `DROPPED_FAMILIES=("F2",)` (`adjudication.py:57`, `roster.py:204`). What exactly is missing — this is a **rule + budget + retention collision, not a wiring task**:
1. **Rule:** A10/A18 excludes per-instance labels from daemon-origin families by ratified design (`metric-catalog.md:1168-1169`).
2. **Cardinality:** per-instance = open cardinality; the AM-2 guard *forbids* registering a label key without a closed value set (`wiring/registry.py:102-114`); `GLOBAL_SERIES_CAP = 2000` (`adjudication.py:209`) vs ~6,324 prod instances (catalog `:972`) — one gauge would exceed the whole cap ~3×.
3. **Retention:** central ring = 2 h in-RAM, lost on restart; the durable per-instance tier is the resource PG ladder (raw 30 s/48 h, minute 60 s/14 d, hour 3600 s/180 d — `config.py:927-931,980-1000`); teaching the door to read PG would violate read-path purity (contract §4.7, `:215-223`).
4. **Pull seam — exists:** `ResourceMonitor.ring → TierRing.snapshot()` (`facade.py:50-79`, `ring.py:82-88`) is the pollable surface `/api/resources/live` reads; the blocker is the rule and the series model, not the seam.

**Verdict: tab 1 stays on `/api/resources/*` (zero backend work, consistent with the leader-ratified three-tier split — `central-metrics-v1/decisions.md:211-214`, `:237-242`).** Routing per-instance through the door would be a deliberate metrics-lane commission (A10/A18 amendment + AM-2 relaxation + budget re-plan + retention story) — listed in §8 as NOT recommended for T1.

### 6.2 `/api/resources/*` endpoint triage — tab-1 support set

The refactor does NOT put tab 1 on central-API-only terms (impossible: `/live` reads an in-process ring that is *unreconstructible* from the door — forensics B6; `/traceback` joins 9 tables; per-instance is rule-excluded). **Keep the 6 FE-consumed endpoints as tab-1 support endpoints:** `/live` (`resources.py:276`), `/history` (`:458`, tier ladder), `/traceback` (`:983`), `/anomalies` (`:1080`) + `/anomalies/{id}/evidence` (`:1121`), `/self-cost` (`:694`). **Retire the 2 FE-orphaned endpoints:** `/control-capabilities` (`:1199` — reads `resource_limit_policy`, which has NO WRITER: "stays EMPTY in v1", `:1169-1175`) and `POST /profile` (`:1325` — seam-only, never invokes a profiler, `:1239-1240`). Caveat: retirement rewrites the scope-manifest census pin (`tests/unit/test_router_scope_manifest.py:106-113` pins the 8-route family; `:288-292` the count) — cheap but real. The traceback's inline `message_queue` tally (`:930-936`, duplicating F10) is a future consolidation candidate, not a retirement.

### 6.3 Tabs with no source at all (Tier-3 gaps) — all metrics-lane, not FE

| Gap | Class | Evidence |
|---|---|---|
| LLM token usage | **new family + increment at usage site** (F14 currently OUT) — `usage_metadata` captured on every streamed call then dropped, "zero consumers ref-wide" | `adjudication.py:69`; inventory `:81,:129` |
| LLM request latency | **new family + observe at request sites** (F15 OUT; badge/HTTP latency uncatalogued, no family id) | `adjudication.py:70`; inventory `:75` |
| Source circuit-breakers / token buckets | **new family + counters that don't exist yet** (state is readable — `circuit_breaker.py:110-112`, `rate_limiter.py:77-90` — but denials uncounted; process-local) | uncatalogued; inventory `:86,:144` |

None of these is unblocked by FE work; the FE cost after each family lands is one manifest flip (§2.3).

### 6.4 Backend-touching items arising from (a)-(d)

- **BE-served manifest** — deferred (§1.2); if adopted: shape (A) optional field + one pin update. Shape (B) new route violates the frozen fence — forbidden.
- **Baseline first-class support** — optional additive `end_offset_s`/`compare` on `/series` (§3.2): no schema/vocab bump, no ring growth; recommended T1.5 only if the client-split proves insufficient.
- **Window > 2 h on central tabs** — `WINDOW_SECONDS` growth is multi-axis (ring depth, `POINT_BUDGET`, eviction, cursor expiry) or a PG tier breaking §4.7 purity; NOT recommended for T1; 24 h/7 d remain tab-1-only via the PG ladder.
- **F1/F3/F6 false-dark fixes** (R-i polarity, R-ii `capture_active` wiring) — already routed; prerequisite for 3 families joining Runtime/DB-Pool tabs. Not absorbed here.
- **F0.3 spine-health TypeError** (`w1.py:366-367` — `@property` called with parens, always "not accepting") — already routed; type-inferred, not runtime-observed.

---

## §7 (g) Route fate of `/resources`

**Recommendation: Option (2) — retire and redirect, executed in the SAME release that mounts tab 1.** Transitional dual-mount (option 3) is the fallback if the user wants a bookmark grace period.

Reasoning: (i) once tab 1 hosts the full component tree (Live + Timeline + drill-down), the standalone page has **no unique capability** — keeping it is pure duplication of the shell; (ii) a permanent redirect serves bookmarks forever; (iii) dual-mount defers, not avoids, the spec churn (every file the refactor touches is already open in this release); (iv) the ratified direction is ONE page. The Live tab having "no central equivalent" is irrelevant to the route question — it ships as tab 1 over its own endpoints (§6.2).

**Required FE work and spec changes (verified):**
- `app.routes.ts:74` `'resources'` → redirect to `/metrics` preserving query params (the app's existing `redirectTo` idiom — `:35,:104` — does NOT preserve params; use a `canMatch → UrlTree` redirect or a 3-line redirect component, new but tiny).
- `?tab=` deep-link support does NOT exist anywhere (all `queryParamMap` usage is jobs/snapshots filter sync — `jobs.component.ts:606-628`, `snapshots.component.ts:110-111`; no mat-tab page reads params) — new FE work in the metrics shell: read `queryParamMap` → set the tab signal (`metrics.component.ts:117` `activeTabId`), write back on `onTabChange` (`:204`).
- Gear menu: remove the `Resources` entry (`app.ts:580`); `Metrics` (`:597`) becomes the single entry.
- Spec edits (per-option enumeration verified in the registry-triage pass): `resources-gear-menu.spec.ts` — `:19-24` seed-shape → null; `:26-41` ordering (resourcesIdx → −1); `:43-49` no-header-link stays true; `:72-93` lazy-route pins → redirect pins. `metrics-gear-menu.spec.ts` — `:30-45` drop `resourcesIdx` assertion; `:47-53` Resources-still-before-appends delete/convert. `resources.component.spec.ts` source-grep pins (`:23-25`) need path/structure review. `tests/unit/test_router_scope_manifest.py:106-113,:288-292` unaffected if endpoints are kept (§6.2) — only endpoint retirement rewrites it.
- Pre-flight for the build lane: repo-wide `grep -r "/resources"` over all `*.spec.ts` (the two gear-menu specs + component spec are verified; an exhaustive sweep was not run in this pass).

---

## §8 Consolidated flag list — FE-only vs metrics-lane split

| # | Item | Class | When |
|---|---|---|---|
| 1 | Tab manifest (FE-local typed config, `central\|custom` slots) + `/metrics` shell hosting 7 tabs + `?tab=` deep links | **FE-only** | T1 |
| 2 | Chart.js v4 adoption: wrapper + anomaly plugin + DOM proxy/hidden data table + canvas-mock devDep + posture amendment (pins `:94`, pack Leg 7, 2 docs) | **FE-only** (deliberate posture change, user-ratified — §9) | T1 |
| 3 | Mount resource components as tab 1; page-frame time selector w/ per-tab vocabularies; client-split baseline (W ≤ 1 h) | **FE-only** | T1 |
| 4 | Route retirement + param-preserving redirect + gear-menu/spec edits | **FE-only** | T1 |
| 5 | Retire `/control-capabilities` + `POST /profile` (dead code) + scope-manifest pin update | **Backend (small)** — recommend same release; can defer independently | T1 or T1.5 |
| 6 | `end_offset_s`/`compare` on `/series` (first-class baseline; additive; no bumps) | **Backend (small, optional)** | T1.5 |
| 7 | F1/F3/F6 false-dark fixes (R-i/R-ii) — unblock 3 families joining tabs | **Backend (already routed)** | T1.5 |
| 8 | F0.3 spine-health TypeError fix | **Backend (already routed)** | T1.5 |
| 9 | Tier-3 families: LLM tokens (F14), LLM latency (F15), source breakers | **Backend (metrics lane: new families + instrumentation)** | T2/T3 |
| 10 | Per-instance CPU/mem through the door (A10/A18 amendment + AM-2 relaxation + budget re-plan + retention) | **Backend (major; NOT recommended — tab 1 covers it via `/api/resources/*`)** | unpaved |
| 11 | BE-served manifest (shape A optional field + pin update) | **Backend (deferred — flip when onboarding cadence demands)** | T2+ |
| 12 | Central windows > 2 h (ring growth or PG tier) | **Backend (major; not recommended)** | unpaved |

---

## §9 Open decisions for the user

1. **Confirm T1 ships the interactive affordances** (value axis + units, crosshair, legend/toggles, multi-series, drill-down). The whole (e) recommendation rests on this: if T1 is single-series shape views with interaction deferred to T2+, **flip to Option A** (merged hand-rolled engine, ~130 LOC marginal, no dependency, no posture amendment).
2. **Ratify the dependency posture change** as a deliberate product decision: the metrics zero-dep guard (`metrics.bindings.pins.spec.ts:94` + pack Leg 7) and two architecture docs are amended to "one MIT chart lib, lazy chunk, MIT-licensed, with a11y fallback" — or ratify zero-npm-deps as a standing FE-level position, which takes Option B off the table entirely.
3. **Baseline path:** accept T1's client-split (FE-only, W ≤ 1 h, disclosed fidelity limits) — or commission the small additive `end_offset_s`/`compare` param up front (backend, no schema/vocab bumps).
4. **Route fate:** confirm retire+redirect in the refactor release (recommended) vs one-release transitional dual-mount (fallback; cheapest spec churn now, one extra release of dual surface).
5. **Endpoint retirement timing:** retire the two dead resource endpoints in the same release (recommended; small scope-manifest pin churn) or leave them for a hygiene pass.
6. **Verify Chart.js v4 size before the budget claim locks:** all dependency sizes in this doc are directional from prior knowledge (no npm/network in this pass); the build lane should measure the real min+gz for the exact tree-shaken controller set.

### Could not be verified from code (stated per instructions)

- LIVE/DARK counts and live-series figures are the documented 2026-11-02 production observation (`metrics-promote-disclosure.md:55-61,:138`; `c1-runtime-observation.md:119-142`), not a fresh census — no live-daemon contact by constraint. The *mechanisms* (capture_off, census-gated polarity, series_absent) are code-verified in the v0.28.9 tree.
- Chart-library sizes (~45 KB uPlot, ~60-70 KB Chart.js, ~1 MB ECharts) — directional, not session-verified.
- The F0.3 spine-health TypeError and the forged-cursor window-shift are type-inferred/static readings, not runtime-observed.
- Chart.js-under-zoneless-Angular-21 integration is reasoned (imperative redraw outside CD + the no-Angular-overlay rule), not prototyped; jsdom canvas-mock need is standard-practice inference.
- Whether any spec file beyond the three named greps the `/resources` route literal — directory-level check only; the build lane must run the repo-wide sweep.


---

# §10 CHARTING ENGINE DECISION — the 4-engine proposal evaluated (addendum, 2026-11-03)

**Supersedes §5's engine *count* discussion; confirms §5's engine *choice* for the panel role.** Trigger: a concrete user proposal — "three charting libs behind a lazy-loading regime — (1) dashboard frame: Angular Gridster2; (2) heavy real-time/time-series panels: Apache ECharts; (3) aesthetic overview/report panels: ApexCharts; (4) mini/simple widgets: Chart.js. We can use 3 libs (with lazy load if possible), each chart/layout/module may pick one or many." (Four engines: Gridster2 = layout frame; three chart engines.) All numbers below are **MEASURED** (npm registry metadata via `npm view`, read-only; builder source read from `node_modules/@angular/build`; shipped `dist` re-hashed) or explicitly **ESTIMATED**. Three verification workers: bundle/build reality (`scalability-design`), workload fit (`trade-off-analysis`), manifest shape + UX + Gridster2 (`structural-design`).

## 10.0 Verdict

**The proposal is rejected AS-IS and accepted IN SHAPE.** The *shape* — per-panel engine selection behind a seam, engines additive later — is right and is adopted as the **chart-INTENT + renderer-registry** contract (§10.3). The *set* is reduced to **ONE vendor engine in ONE role**: **Chart.js v4 for interactive trend panels + the house SVG retained (distilled) for minis/sparklines** — both behind the seam. **ECharts: deferred** (its capability tier is **measured-empty** at our volumes, §10.2). **ApexCharts: deferred** (no aesthetic-report surface exists). **Gridster2: dropped** for the ratified fixed-tab direction (wrong product; also Angular-major peer mismatch) — revisit only on a composable-dashboards product decision (§10.5). This **confirms §5's Chart.js choice for the panel role** and refines §5 in two ways: the mini role keeps SVG (Chart.js is at its *worst* in a mini — §10.2b), and the "canvas at scale" scaling argument is retired (the BE's own ceilings bound every panel below canvas-class volume — §10.2a).

## 10.1 Bundle + build reality (all MEASURED unless noted)

**Budget semantics — the crux, read from the installed builder (`@angular/build` 21.2.6):**
- Config declares exactly two budget types: `initial` (1 MB warn / 6 MB error) and `anyComponentStyle` (8 kB/24 kB) — `angular.json:63-75`. Seven types exist (`schema.json:689`: `all|allScript|any|anyScript|anyComponentStyle|bundle|initial`); **five are unused**.
- `initial` covers **entry points + transitive static imports only; dynamic imports are never followed** (`node_modules/@angular/build/src/tools/esbuild/bundler-context.js:261` gates on `import-statement|import-rule`; summing in `bundle-calculator.js:154-167`). **⇒ an engine imported only by the lazy `/metrics` route does NOT count against 1 MB/6 MB.**
- **No budget of any kind constrains a lazy chunk today, and no per-chunk size assertion exists anywhere** (grep over `frontend/src`, `e2e`, `scripts`, `test/packs`, `scripts/upgrade` → 0 real hits; no `.github/` CI in-tree). Enforcement is CLI build-time only: budget *error* → non-zero exit (`execute-build.js:184-189`) → both FE packs fail; *warnings* are recorded findings, never gates (`metrics_fe_dual_side_test.sh:261-269`; `fe_static_typecheck_build_test.sh:11-13,:110-116`).
- 🔴 **The real cliff is the initial set itself: measured initial total = 5,909,018 B vs the 6,000,000 B error ⇒ 89,982 B (~89 KiB) of headroom** (byte math is decimal, `bundle-calculator.js:17`). The initial bundle is mermaid-dominated (`scripts-*.js` 3,559,319 B, `angular.json:58-59`). **Any engine that leaks into the initial set — e.g. a layout frame imported by a shared shell module rather than a lazy route — hard-fails the production build and both FE packs.**
- Measured lazy baseline: **52 lazy chunks, 1,702,633 B total**; `/metrics` chunk today **27,672 B**, `/resources` **38,914 B**, largest lazy (jobs) 135,690 B. An engine added to the metrics chunk is ungoverned but also invisible to first paint.

**Per-engine npm metadata (MEASURED 2026-11-03):**

| package | version (last publish) | unpackedSize | runtime deps | new lockfile entries | peers |
|---|---|---|---|---|---|
| **chart.js** | 4.6.1 (2026-10-22) | 6,227,928 B | **1**: `@kurkle/color@^0.3.0` (43,554 B, MIT) | **2** | none |
| **echarts** | 6.1.0 (2026-05-19) | 60,297,703 B (57.5 MiB) | 2: `tslib@2.3.0` (exact!), `zrender@6.1.0` (4.3 MB) | **4** (echarts pins tslib exactly 2.3.0 vs root 2.8.1 ⇒ nested copies) | none |
| **apexcharts** | 8.7.0 (2026-10-31) | 29,969,470 B | 1: `apex-commons@^0.8.3` | 2 (+1 with wrapper) | none |
| ng-apexcharts (wrapper) | 3.2.0 (2026-10-09) | 120,741 B | 1: tslib (satisfied) | +1 | `@angular/core >=20` ✅, `rxjs ^7.8.2` ✅, `apexcharts ^6‖^7‖^8` ✅ |
| angular-gridster2 | 22.0.0 (2026-06-26) | 480,299 B | 1: tslib (satisfied) | +1 | 🔴 **`@angular/core ^22.0.0` — mismatches our 21.2.5; the 21-line pin is 21.0.1** |

- **Browser-bundle sizes (ESTIMATED, not measured — registry metadata carries no bundle bytes):** echarts full ≈ 1.0–1.1 MB min / ≈330–360 kB gz; `echarts/core` + 2–4 charts ≈ 150–400 kB min / ≈60–120 kB gz. Chart.js tree-shaken ≈ 60–70 kB min+gz-class (round-1 directional). ApexCharts: SVG-default renderer, canvas = separate opt-in feature module, per-shape subpath exports (v8) — size unmeasured.
- **Premise corrections to the proposal:** (i) **`angular2-d3` does not exist (E404)** — angular-gridster2 has carried no d3 peer since v7; the "d3 v7 ESM interop" question is moot. (ii) Chart.js has **one** runtime dep, not zero. (iii) ApexCharts is **SVG by default** (canvas opt-in) — so it is *not* the canvas-class engine in this stack; ECharts is.
- **Lockfile impact is a non-constraint:** current lockfile 506,083 B / 14,293 lines / 1,015 package entries; **all five packages = +10 entries ≈ +5 kB (ESTIMATED bytes)**. The constraint is the **built artifact + governance**, not the lockfile: `stage.sh:444-449,:598,:601` hash the FE tree + per-file manifest (v0.28.9 manifest: 95 FE files), and **pack Leg 7 hard-fails on ANY `frontend/package.json` diff** (`metrics_fe_dual_side_test.sh:276-286`, verbatim logic verified). Five documented "zero FE deps" sites must be amended in the same commit as any dependency (pack Leg 7; `CHANGELOG.md:141` historical — do not rewrite; `docs/architecture/metrics-promote-disclosure.md:122` item #2; `metrics-agent-tools/stage-prep-checklist.md:28`; `central-metrics-v1/phase6-plan.md:23` + `stage-prep-checklist.md:102`).

## 10.2 Workload fit (MEASURED from code)

**Cadence, proven:** sampler tick 10 s (`config.py:825-834`), publisher cadence 15 s (`checkpoint_metrics.py:128`; rate-limit `:1045`), **effective spacing = max(10,15) = 15 s** (`provider.py:125-131`; contract `:196`) — the 10 s tick that re-reads an unchanged published snapshot appends nothing (identity-dedupe, `sampler.py:232-234`).

**Point counts per window (the deciding table):**

| Window | Central ring (15 s) | Resource PG ladder (auto-tier, `resources.py:146-160`) |
|---|---|---|
| 15 m / 1 h | 60 / 240 pts/series on the wire | 30 / 120 pts/series (raw, 30 s) |
| 2 h | **480 retained, ≤240 served** (bucket-max; `constants.py:55`) — hard ceiling (`metrics.py:778`) | 240 (raw) |
| 24 h | **impossible** | **2,880 pts/series** (raw 30 s; no LIMIT — `resources.py:611-692`) |
| 7 d | impossible | **10,080 pts/series** (minute 60 s; hour tier never auto-picked, `:157-159`) |

Response ceilings: ≤ 500 series, **≤ 12,000 points per response** (`constants.py:69,:78`; `envelope.py:238-253`). Series counts per family (static AST over the 47 registrations + `vocab.py`): closed spaces 2–104 series/family, total 314; **~42 actually published** (`ring.py:43`). Heaviest real panel today: resource timeline @7 d = 5 × 10,080 = **50,400 points** — rendered today by the 538-L SVG engine without canvas.

**(a) The ECharts-class tier is EMPTY.** dataZoom: central windows hard-clamp at 2 h/240 pts — there is nothing below the downsample to zoom into. Brush: the only long window (7 d) is a *minute rollup*; a local brush would imply sub-bucket resolution that does not exist — the honest semantic is **re-fetch a narrower window**, which the BE already implements (tier re-picks per window, `resources.py:146-160`) and the FE already teaches ("Narrow the window for finer detail", `timeline-tab.component.html:37-38`). `large`/canvas modes: max 50.4 k points on one panel — a *decimation* concern, not an engine class. Thousands of series: **cannot exist** — per-instance labels are rule-excluded (A10/A18, `metric-catalog.md:1168-1169`; 6,324 prod instances at `:972` vs the 2,000-series hard cap with rejection-not-folding, `checkpoint_metrics.py:129,:789/:820/:868`).

**(b) Minis need NO engine.** No sparkline exists today (grep: empty); the house SVG primitive already delivers line/step/area, per-point `<title>`, `role="img"` a11y, and exported pure helpers assertable in jsdom. Chart.js in a mini is strictly worse: one canvas instance + mandatory `destroy()` discipline (a leak class under OnPush/signals), no canvas semantics (a11y wrapper needed anyway), **jsdom has no canvas** (the existing Jest harness cannot render it; no `node-canvas`/mock in-tree).

**(c) Interaction mapping (condensed; full table in the worker report):** for the **central trend panel** role, crosshair/legend-toggle/shared-y are "library gives free" under Chart.js/ECharts/Apex and "build anyway" under SVG; **click→drill-down and CSV export are APP-domain under every engine** (destination mapping, anomaly source, units vocabulary — engine-agnostic); **dataZoom/brush are N/A everywhere** (§10.2a). For the **mini** role everything is N/A or already shipped in SVG. **The BE's 12,000-point/500-series/2 h ceilings cap every panel regardless of engine** — the T2/T3 "canvas at scale" scenario is bounded away by the door itself.

## 10.3 Tab-contract impact — Shape B: chart-INTENT + pluggable renderers (recommended)

**Shape A (per-engine config in the manifest)** is rejected: adding an engine widens the `EngineId` union (compile-break at every switch), adds a per-engine config field, and re-declares `sharedY`/`anomalies`/`zoom`/`mode` in 3–4 mutually incompatible vocabularies (`stepped` vs `seriesType:'step'` vs `curve:'step'`); the manifest's *shape* changes on every engine addition — the opposite of additive.

**Shape B (adopted):** the manifest carries engine-agnostic **intents** (`trend | sparkline | stat`, with flags for shared-y/markers/bands/drill); a **renderer registry** maps intents to implementations via a `ChartRenderer { id, capabilities, supports(intent), render(intent, host), teardown(host) }` interface; `supports()` is pure ⇒ unit-assertable (house pattern). Adding ECharts/ApexCharts later = **one renderer file + one registry line + one capability declaration; the manifest is untouched** (an optional per-tab `preferred` hint remains validated). Keep intent kinds ≤ 3 or B degenerates into A.

**Fallback rule (three layers):** (1) **build/test-time hard validation** — a pure `validateManifest(intents, renderers)` asserts every tab's intent is covered by some renderer's capabilities; exit non-zero on gap (mirrors `assert_roster_is_well_formed()` fail-loud); (2) **runtime mount-fallback only** (container/canvas unavailable) down a declared priority chain, surfaced via `data-renderer-fallback` + a degraded badge; (3) **hard error, never silent downgrade** — an unexpressible intent renders an explicit unsupported state, never a plausible-but-wrong chart (band silently dropped).

**Real surface (ESTIMATE, grounded in the two existing engines' measured line counts):** house-SVG renderer (merge + extend) ≈ 145–255 L + shared-domain change; Chart.js renderer ≈ 520–890 L (incl. anomaly plugin 90–160 L, theming 90–150 L, teardown); ECharts ≈ 450–755 L; ApexCharts ≈ 505–885 L. **CSV export (~60–90 L) and empty/stale/dark states are intent-level, written once** — they never multiply per renderer. Four renderers ≈ 1,620–2,785 L translation + 4 theming surfaces + 3 vendor deps + 3 lazy-chunk plumbings; **one vendor + SVG ≈ 665–1,145 L + 1 dep + 1 theming surface.** Shape B pays off precisely when ONE renderer ships now and the registry is the extension point.

## 10.4 UX consistency + theme plumbing

Four engines on one page = **2–3 interaction languages by default** (SVG native-`<title>` + exact-point click + no legend toggle; ECharts axisPointer + dataZoom + toolbox; ApexCharts no native legend toggle; Chart.js legend `onClick` toggles). Under Shape B the intent vocabulary *is* the interaction contract — one language to enforce across renderers; with one vendor renderer the problem is bounded to two (canvas panels + SVG minis), and the minis carry no interaction at all. **There is no chart token layer to build on** (palette `CHART_COLORS` duplicated in 3+ files; literal hex in `styles.scss`) — extract `chart-theme.ts` (~40–60 L) first regardless of engine count; each canvas engine then adds its own theming surface (Chart.js `Chart.defaults`+CSS ~90–150 L; ECharts theme JSON ~120–200 L, not CSS-var-aware; ApexCharts per-chart options ~100–180 L, drift-prone). Vendor tooltip/legend DOM escapes Angular encapsulation — Material dark theme does not reach it without a global CSS block per engine.

## 10.5 Gridster2 — roadmap either/or (not a library choice)

**(i) Ratified direction (predefined tabs, fixed layout): WRONG-FIT — drop it.** Its core value (user drag/resize + layout persistence) is unused; the in-tab panel grid is already a 4-line CSS grid (`charts-tab.component.ts:171-175`); adopting a dashboard frame inside a tab is "a product change wearing a library costume". Also measured: **latest 22.0.0 peers `@angular/core ^22.0.0` — incompatible with our 21.2.5** (the 21-line pin is 21.0.1); single maintainer (bus-factor-1); 480,299 B unpacked; and a layout frame is naturally imported by shell/shared code — exactly the import shape that lands in the **89-KiB-headroom initial set and hard-fails the build** (§10.1). The `angular2-d3` peer in the proposal **does not exist (E404)** — that concern is obsolete.

**(ii) If composable dashboards become a product goal someday:** the trigger is a real user requirement ("users arrange their own panels"), never a technical one. **Cost of deferring 12 months: LOW** — release cadence ≈ 2–6/yr ⇒ at most one major behind; the Angular-major-coupled peers mean adoption naturally lands on an Angular-major boundary anyway; and the layout layer is **orthogonal to the engine seam** (a Gridster widget is just a different `host` element for a renderer — the intent manifest does not change). Fallback ladder when the trigger fires: adopt Gridster2 at the next Angular major (≈1 release of catch-up, MIT) → or a maintained alternative → or hand-roll a CSS-grid drag/resize layer (~150–300 L ESTIMATE; the grid already exists).

## 10.6 The one recommendation (reduced set, behind the seam)

**KEEP:** Chart.js v4 as the **sole vendor engine**, for the interactive trend-panel role only (its best role: value axis+units, crosshair+nearest-snap, legend toggles, shared-y multi-series, `stepped` counter honesty, click→drill; ~250–450 L wrapper + plugin vs ~480–720 L hand-built + ~440–705 L tests — round-1's measured gap, unrefuted). Import it **only from the lazy `/metrics` route tree** so it never touches the 89-KiB initial headroom. **KEEP:** the house SVG, distilled into ONE minimal mini/sparkline renderer (roles disjoint from the panel renderer — the divergence precedent forbids two engines claiming the same role; the mini renderer is feature-frozen *as a mini*, no axes/interaction ever). **ADOPT:** Shape B intent+renderer registry with the three-layer fallback. **AMEND (same commit as the dep):** pack Leg 7 + the four live "zero FE deps" doc/checklist sites (`CHANGELOG.md:141` stays — historical); add `@kurkle/color` + `chart.js` (+2 lockfile entries) and a jest canvas-mock devDep; the wrapper carries the DOM-proxy + visually-hidden data table (a11y + keeps a DOM-assertion gate alive; the current e2e `d`-attribute internals at `metrics-gate.spec.ts:277-298` are re-specified at renderer level).

**DEFER — ECharts**, with measured triggers: (t1) A10/A18 per-instance relaxation (→ thousands of series), (t2) central `window_s`/`max_points` ceilings raised so zoom has range to reveal, or (t3) a panel class needing sub-bucket brushing (requires a BE tier change first). Until any fires, its tier is empty and its cost is a 4-entry lockfile closure incl. two exact-pinned nested `tslib@2.3.0` copies + a ~150–400 kB (ESTIMATED) lazy-chunk payload + a second theming surface + a second interaction language. **DEFER — ApexCharts**, trigger: an actual aesthetic report/export surface whose product goal is presentation-grade static charts (SVG-default renderer + maintained `ng-apexcharts` wrapper will both be compatible when that day comes). **DROP — Gridster2** (§10.5).

**What this changes vs §5:** §5's Chart.js-for-panels verdict STANDS (now on affordance-cost + divergence-precedent grounds alone — the scale argument is retired by §10.2a); §5's "retire both SVG engines" is refined — the panel engine is replaced by the Chart.js renderer, while a *distilled mini renderer* survives in the SVG line (§10.2b). The T1 flip condition from §5 remains live and is restated in §10.7.

## 10.7 Fact adjudications, flips, and unverified items

- **Adjudicated — zoneless dispute:** round-1 concluded the app runs zoneless; round-2's structural worker concluded not. Both agree on the underlying facts (no `polyfills` entry; no zone provider in `app.config.ts`; `zone.js ^0.16.1` declared in deps). Mechanical resolution: **zone.js is never loaded ⇒ effectively zoneless under Angular 21 defaults**; the dep entry is inert. Immaterial to the verdict — the no-Angular-overlay rule for canvas redraws applies either way. Not runtime-observed.
- **Flip conditions for §10.6:** (f1) if the user ratifies zero-npm-deps as a standing FE product position, the panel role flips to the merged/extended house-SVG renderer at ~+400–700 L (the crosshair+snap layer is the expensive item) with the house rule "ONE engine; the frozen-list is a spec, not a default"; (f2) if T1 defers the interactive affordances entirely, the same flip fires (§5's original flip, restated); (f3) any §10.6-defer trigger firing re-opens ECharts/ApexCharts as an *additive renderer*, never a rewrite.
- **MEASURED vs ESTIMATED ledger:** MEASURED — all npm metadata (versions, unpackedSize, deps, peers, publish dates), builder budget semantics + 7 budget types, initial 5,909,018 B / 89,982 B headroom, lazy-chunk inventory (52 chunks, 1.70 MB, metrics 27,672 B), lockfile shape (1,015 entries/506 kB), pack-Leg-7 verbatim logic + the 5 governance sites, cadence/ceilings/point-counts/series-vocabularies. ESTIMATED — browser bundle bytes for every engine (echarts 150–400 kB tree-shaken / ~1 MB full; Chart.js ~60–70 kB; ApexCharts unmeasured), all per-renderer LOC figures, lockfile growth bytes (~5 kB), hand-rolled CSS-grid drag/resize (~150–300 L).
- **Unverified:** echarts full-vs-core bundle bytes and Chart.js per-controller import shape (registry metadata silent — re-measure with a real build before locking any budget claim); `apexcharts` license text (metadata says "SEE LICENSE IN LICENSE.md" — verify before any future adoption); live published-series count (42 = code-declared, not observed); real-browser cost of the 50,400-command 7-d SVG (no browser run; mitigation is BE-side decimation, not an engine); whether the closed-space count is 314 (round-2 AST) or 336 (adjudication pack) — counting conventions differ over F7 histogram buckets, immaterial here.
