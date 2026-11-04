# Resource Tab Forensics — the pre-central `/resources` page

**Date:** 2026-11-03 · **Commissioner:** leader (metrics-UI audit + refactor proposal) · **Author:** wanderer (read-only forensics; 3 delegated investigators + controller cross-verification)
**Question:** locate the pre-existing resource page precisely, map its data dependencies, and determine exactly why it needs a refactor.

## Citation basis (read first)

- Working tree `/home/nea/ensemble-src` = `013895287` (2026-10-26, **v0.27.8-era**). The central-metrics system **does not exist in this tree** (`daemon/services/metrics_query/`, `daemon/routers/metrics.py` absent; `daemon/checkpoint_metrics.py` there is the pre-generalisation 355-line registry). Anyone grepping the working tree for central metrics gets a false "does not exist".
- Central-metrics code is cited from **`origin/latest` = `c4161335b`** (2026-11-03, v0.28.9 tip) via read-only `git show`, cited as `origin/latest:<path>:<line>`.
- **Drift measured** (`git diff --stat 013895287 origin/latest`): `daemon/routers/resources.py` (+87/−8) and `daemon/services/resource_anomaly.py` (+24/−1) drifted → cited from `origin/latest`. All other resource-side files (`daemon/services/resource_traceback.py`, `daemon/models/resource_monitor.py`, `daemon/services/resource_monitor/*`, the four `.sql` migrations, all `/resources` FE files except one spec) are **byte-identical** → cited as plain `path:line`. The only FE drift is `resources-gear-menu.spec.ts` (+20).
- Timeline anchors: `/resources` shipped **v0.25.0, 2026-10-19** (`origin/latest:CHANGELOG.md:309` heading, `:328` FE page line; first commit `aa95fc438` 2026-10-18 adds `daemon/routers/resources.py`; FE shell commit `5a0380444`). Central metrics shipped **v0.28.6, 2026-11-02** (`origin/latest:CHANGELOG.md:134`; tag `v0.28.6 → 2026-11-02`). Agent-tool lane **v0.28.9, 2026-11-03** (`CHANGELOG.md:96`). **Gap = 14 days.** The naming/cardinality catalog that governs the split is dated **2026-10-29** — 10 days *after* `/resources` shipped.

---

## PART A — Locate the resource page precisely

### A1. Route + nav entry

| Item | Value | Evidence |
|---|---|---|
| Route path | `resources` → `/resources` | `frontend/src/app/app.routes.ts:66` |
| Route title | `Resources` | `frontend/src/app/app.routes.ts:66` |
| Loading | lazy `loadComponent` (bundle-cost off initial load) | `frontend/src/app/app.routes.ts:66` |
| Nav label / icon / route | `Resources` / `insights` / `/resources` | `frontend/src/app/app.ts:580` |
| Menu surface | **Gear/settings menu only** — no top-level header link (pinned by spec) | `frontend/src/app/app.ts:566`; `frontend/src/app/pages/resources/resources-gear-menu.spec.ts:43-48` |
| Ordering neighbours | `Snapshots` (`app.ts:575`) → **Resources** (`:580`) → `MCP Gateway` (`:589`); on `origin/latest` `Metrics` was appended after MCP Gateway (`origin/latest:frontend/src/app/app.ts:597`) | `app.ts:575-589` |
| Ordering contract | spec-pinned "Settings → Snapshots → Resources → (conditional appends)" — a nav reorder breaks the pin | `resources-gear-menu.spec.ts:26-41` |

### A2. Component tree

```
ResourcesComponent                    frontend/src/app/pages/resources/resources.component.ts:42
├─ template resources.component.html:1-18 (header h1+subtitle; <mat-tab-group>)
├─ LOCAL section registry (hardcoded, NOT shared): resources.component.ts:45-48
│    { id:'live', label:'Live', LiveTabComponent }
│    { id:'timeline', label:'Timeline', TimelineTabComponent }
├─ LiveTabComponent                   pages/resources/live/live-tab.component.ts:52 (selector :45; html 181L)
└─ TimelineTabComponent               pages/resources/timeline/timeline-tab.component.ts:80 (selector :66; html 211L)
   └─ SvgTimelineComponent            pages/resources/svg-timeline/svg-timeline.component.ts:383
        (selector app-svg-timeline :176; INLINE template :180-282; INLINE styles :283-381; consumed timeline-tab.component.html:69-74)
```

- Registry is explicitly local: *"Local section registry — no shared cross-page registry"* (`resources.component.ts:43-44`). The shell pattern itself is the house pattern (also used by `maintenance.component.ts:63` and `origin/latest:frontend/src/app/pages/metrics/metrics.component.ts:84-87`, which cites `resources.component.ts:35-49` as its precedent).
- `SvgTimelineComponent` is **page-local**: referenced nowhere outside `pages/resources/` (grep-verified).

### A3. What it renders — 36 distinct visual elements

Live tab (`live/live-tab.component.html`): skeleton shimmer cards `:3-14`; error banner + Retry `:18-30`; "Sampled {ts} UTC" `:35`; "level {sample_level}" `:37`; **self-cost chip** (6 values: cpu %, cpu total, MB buffers, threads, samples, ticks skipped) `:46-63`; **stat cards**: Daemon process `:71-97` (pid/cpu/rss/threads/fds/uptime), Cgroup memory `:107-142` (current/peak/anon/file/slab MB + cgroup cpu usage/throttled/nr_throttled `:129-141`), Attribution `[estimated]` honesty-tag card `:144-163` (attributed/unattributed/coverage %), Self-cost overruns `:165-177`.

Timeline tab (`timeline/timeline-tab.component.html`): window toggle 15m/1h/24h/7d `:3-13`; 5 series toggle buttons `:15-29`; truncated/rollup banner `:33-41`; skeleton `:44-49`; error + retry `:53-65`; the **SVG chart** `:67-77`; inline drill-down panel (NOT a modal) `:79-210` with 7 anomaly summary chips `:94-110`, loading/error+retry `:112-125`, contributors list `:129-144`, traceback window kv `:151-155`, alive-instances list `:157-167`, in-flight tasks list `:169-179`, spawned-children list with `chart_render` hot-flag `:181-198`, structural counters kv `:200-206`.

SVG chart (`svg-timeline.component.ts`): legend with per-series swatch + label + **min–max range** `:181-191`; `<svg role="img" aria-label>` `:193-198`; per-series **area fill** `<path>` `:203-208` + **line** `<path>` `:209-215`; vertical gridlines `:219-225`; UTC `HH:MM` x-tick labels `:226-232`; "UTC" caption `:280`; anomaly markers (stem + dot + 12r hit-target + click emit) `:235-267`; native `<title>` tooltip on markers only `:257`; "No samples in window" empty state `:269-277`.

**Explicit absences (stated, not assumed):**
- **No HTML tables anywhere** on the page (zero `<table>/<th>/<tr>` matches) — the brief's hypothesis "numbers-only tables" is factually wrong; presentation is stat-cards + div rows.
- No `<canvas>`, no sparklines, no KPI meter tiles, no progress bars, no modals (no MatDialog).
- **Exactly one chart** for the whole page; hand-rolled, **zero charting libraries** (`frontend/package.json` contains no chart/d3/echarts/plotly dep; only `mermaid ^11.4.0` — diagrams-in-markdown, and `ngx-markdown`).
- Chart affordances absent: y-axis line/labels/numeric scale (deliberate — 5 series with different units each normalised to its own `[min,max]`, `svg-timeline.component.ts:29-33`), crosshair, brush, zoom, per-point line tooltips, gradients — declared **out of scope** by the primitive itself (`:26-27`).
- **Unrendered payload (honesty/promise gap):** the page subtitle promises *"per-instance attribution"* (`resources.component.html:5`) but `LivePayload.instances[]` (`resources.models.ts:77-86`: `instance_id, agent, estimated, charged_mb, child_rss_mb, children_n, in_flight_tasks`) and `children[]` (`:69-75`) are **fetched every 8 s and never rendered**; same for `self_cost` embedded copy (`:147`), `rss_budget_mb`/`flush`/`profiling` (`:122,:127-128`), evidence `samples/top_pids_per_bucket/children_delta` (`:230-232`), traceback `findings` (`:281`).

### A4 (B7). Polling / refresh behaviour

| Aspect | Answer | Evidence |
|---|---|---|
| Auto-poll | **8000 ms**, Live tab only | `live-tab.component.ts:57` `POLL_INTERVAL_MS = 8000`; set `:84-87`; cleared `ngOnDestroy` `:89-94` |
| Tab-switch teardown | verified against framework source (`preserveContent=false` default → portal detach destroys the component) — the 8 s poll stops when the Timeline tab is selected | `node_modules/@angular/material/fesm2022/tabs.mjs:1375,1046-1049`; `@angular/cdk/fesm2022/portal.mjs:133-139,:345-348` |
| Manual refresh button | **none** (only error-branch Retry buttons) | `live-tab.component.html:25`; `timeline-tab.component.html:60,121` |
| SSE / websocket | **none** (zero EventSource/WebSocket in `pages/resources/**`) | — |
| Timeline refresh | loads once on mount; reloads only on window change or Retry — a user parked on Timeline sees **frozen data with no timestamp** | `timeline-tab.component.ts:121-137` |
| Stale-response guard | Live: skip-if-in-flight (`live-tab.component.ts:64,:102-104`); Timeline: monotonic sequence-token supersede (`:108-109,:151-153,:158-160,:200-210`) | — |
| "Last updated" | Live only — and it is the **server sample ts**, not fetch time (`live-tab.component.html:35`) | — |

---

## PART B — Data dependency map

### B4. Endpoints → producer → table → central-or-not

All eight routes in `origin/latest:daemon/routers/resources.py` (1405 lines). FE consumes exactly six GETs (`frontend/src/app/services/resources.service.ts:35-39` URL constants, methods `:42-88`; evidence route derived `:74-75`); `/control-capabilities` + `POST /profile` are deliberately excluded from the FE (`resources.service.ts:26`).

| Endpoint | Router (origin/latest) | Producer | Table(s) read | Central-metrics? |
|---|---|---|---|---|
| GET `/api/resources/live` | `resources.py:276` | `monitor.ring.snapshot()` + sampler RAM views (`:247,:434,:459-470`) | **none — 100 % in-process** | **own path** |
| GET `/api/resources/history?window=&resolution=` | `:458` | `_read_history_points` `:611` / `_read_instances_active` `:557` (own SQL + own tier/resolution ladder `:506-554`) | `resource_daemon_bucket` (`:636-642`), `resource_instance_sample` (`:592-596`) | **own path** |
| GET `/api/resources/self-cost` | `:694` | `_self_cost_from_monitor` `:423-452` | **none** — counters only; `buffers_bytes` estimated `len(rows)*250` (`:431`) | **own path** |
| GET `/api/resources/traceback?t0=&t1=` | `:983` | `resource_traceback.build_window_view` + in-router structural providers `:916-977` | **9 tables**: `instances` (`resource_traceback.py:241-244`), `event` (`:251-256`), `task` (`:404-410`), `message_metadata` (`:412-415`), `resource_instance_sample` (`:508-515`), `resource_pid_map` (`:571-591,:689-692`), `service_tracking` (`:694-698`), + router-side `message_queue` (`resources.py:930-936`) and `checkpoints` (`:961`), + `gc.get_stats()` + in-RAM journal | **own path** (the `message_queue` inline SQL duplicates central **F10** job-queue depth) |
| GET `/api/resources/anomalies?window=&limit=` | `:1080` | `AnomalyEngine.list_anomalies` `:1110` (engine = `monitor.anomaly`, `:1074-1077`) | **none at route level** — in-RAM `FindingJournal` deque (`resource_anomaly.py:907-932`) | **own path** |
| GET `/api/resources/anomalies/{id}/evidence` | `:1121` | `AnomalyEngine.build_evidence` `:1144` | journal + `resource_instance_sample` (`resource_anomaly.py:1013-1017`), `resource_daemon_bucket` minute tier (`:1491-1494`), T2/T3 via join layer | **own path** |
| GET `/api/resources/control-capabilities` | `:1199` | `_count_policy_rows` `:1182-1196` | `resource_limit_policy` (COUNT, `:1193`) — **no FE consumer** | **own path** |
| POST `/api/resources/profile` | `:1325` | in-router `_ProfileSeamState` `:1244-1295` — seam only, no profiler, no table | **none** — RAM reservation | **own path** |

**Crux answer:** zero coupling to central metrics in **both directions** — `git show origin/latest:daemon/routers/resources.py | grep metrics` → 0 hits; the entire `daemon/services/resource_monitor/` package + `resource_traceback.py` + `models/resource_monitor.py` contain zero `metrics_query|checkpoint_metrics` references. The **only** interaction runs the opposite way: central family **F0.3** *pulls* from the resource subsystem (below).

### B5. Tables + writers (confirmed names)

| Table | Model (`daemon/models/resource_monitor.py`) | Writer | Started at | Retention |
|---|---|---|---|---|
| `resource_daemon_bucket` (T1) | `ResourceDaemonBucket` `:84` (`__tablename__` `:93`) | `Flusher._write_raw/_write_minute/_write_hour` (`resource_monitor/flusher.py:447-501,:503-537,:539-568`), idempotent `INSERT … ON CONFLICT DO NOTHING` (`:925-933`) | `ResourceMonitor.start()` (`facade.py:81-146`; sampler `:104`, flusher `:121`), wired `daemon/api.py:2239-2268` (`app.state.resource_monitor` `:2268`) | raw 48 h / minute 14 d / hour 180 d (`flusher.py:360-368`; `daemon/config.py:811-827`) |
| `resource_instance_sample` (T2) | `ResourceInstanceSample` `:163` (`:172`) | `Flusher._write_instance_rows` (`flusher.py:733-785`), plain INSERT drain-once (no unique constraint, `:747-751`); `estimated` written `bool(r.get("estimated", True))` `:770` | same | swept at raw cutoff (`flusher.py:369-371`) |
| `resource_pid_map` (T3) | `ResourcePidMap` `:244` (`:256`) | `Flusher._write_pid_map` (`flusher.py:673-731`), drained from in-RAM `PidRegistry` (`:382`) — stamps enter at `daemon/tools/bash.py:376/:848` and `daemon/mcp/stdio_wrapper.py:525` | same | exited-row sweep, `_PID_MAP_HISTORY_DAYS` (`flusher.py:140,:384-391`) |
| `resource_limit_policy` (T4) | `ResourceLimitPolicy` `:321` (`:330`) | **NO WRITER EXISTS** — "stays EMPTY in v1 (no seed rows, no CRUD endpoints)" (`resources.py:1169-1175`); control actions all `available:false` (`:1223-1230`) | n/a | n/a |

Sampler class: `Sampler` (`sampler.py:167`, counters `SamplerCounters` `:125`); flusher `Flusher` (`flusher.py:177`); facade `ResourceMonitor` (`facade.py:35`). **Sampler never touches the DB** (ring only; "NO psutil. Zero new deps", `proc_readers.py:15`). Anomaly findings live in a **bounded in-RAM journal** (`resource_anomaly.py:14-27`, `JOURNAL_MAXLEN` `:170`), cold-boot recomputed from the minute tier (`:1468-1530`).

### B6. Access-pattern classification (central registry vs own path)

**Every one of the 8 endpoints is class (b) — its OWN per-module query path.** None reads the central metrics registry/series via the query provider. The overlap that *does* exist is central→resource (one-way):

- **F0.3 "resource spine"** — the only central family touching the resource subsystem: `ensemble_resource_spine_tables_accepting_rows` (gauge) + `ensemble_resource_spine_rows_written_total` (counter), both `label_keys=("table",)` (`origin/latest:daemon/services/metrics_query/wiring/w1.py:56-92`; table list `wiring/vocab.py:31-36` = the same 4 tables). Its adapter runs its **own second SQL** over the same tables (`_SPINE_COUNT_SQL`, `w1.py:347-352`) and reads the **same in-process counters** (`w1.py:364-380`) that `/api/resources/self-cost` reads (`resources.py:425-426`). It publishes **spine health + row counts only — never a resource measurement**; grep for `daemon_cpu|cpu_pct|rss_mb|rss_kb|threads|instances_active|attributed_mb` across the whole metrics tree = **0 hits**. `/api/resources/live` is **unreconstructible** from `/api/metrics/*`.
- The thread-CPU family **F2 was dropped** by value ruling AM-25/LR-2 (`origin/latest:.../roster.py:29-31`, `DROPPED_FAMILIES=("F2",)` `:204`); F22 (`ensemble_process_cpu_seconds`) is OUT (`adjudication.py:77`).
- Per-instance attribution is central-**excluded by rule**: "per-instance attribution = PG-only (`/api/resources/*`); no daemon-origin `/metrics` family carries a per-instance label" (`origin/latest:.agents/shared/planning/metrics-collection/metric-catalog.md:1168-1170`).

### B7. Polling — see A4 above (8 s Live-tab poll; Timeline manual; no SSE).

---

## PART C — Diagnosis: why does it need a refactor?

### (a) Reads from pre-central endpoints / duplicates central metrics logic — **YES** (with an important design nuance)

Evidence: the page's entire data path predates and bypasses the central door — 8 own-path endpoints, zero provider references (B4/B6). Strongest single evidence: `git grep` both directions = 0 coupling, while F0.3 runs a **second independent SQL reader over the same four resource tables** (`w1.py:347-352` vs `resources.py:636-642,:592-596`) and a **second reader of the same counters** (`w1.py:366-367` vs `resources.py:425-426`). Additional concrete duplications: the `/traceback` inline `message_queue` admission SQL (`resources.py:930-936`) duplicates central **F10** `ensemble_job_queue_depth` (flagged by the project's own inventory, `.agents/shared/planning/metrics-unification/metrics-inventory-backend-producers.md:75,:125`); and at FE level **two hand-rolled SVG chart primitives** now exist (below).

**Nuance that must travel with the verdict:** the split is *ratified design*, not accident — the leader-ratified three-tier architecture (Part D9). The refactor problem is therefore not "the page should simply call `/api/metrics/*`" (the central registry **cannot serve** per-instance or measurement data today, and its 2 h in-RAM window cannot back a 7 d timeline). The problem is the **unmanaged residue**: two SQL readers over the same tables, two counter readers, one duplicated admission tally, two chart codebases, and two vocabularies — none shared, none cross-referenced.

### (b) Missing charts / visualization — **PARTIAL**

It is **not** "numbers-only tables with no time-series" — there is exactly **one** real chart: a hand-rolled dependency-free SVG timeline (line+area, 5 series, legend with ranges, anomaly markers, click→drill-down). Strongest evidence set:
1. **Zero charting libraries** in `frontend/package.json` (both at working tree and `origin/latest`); the page imports only `@angular/*` + `rxjs`.
2. The primitive is deliberately minimal: no y-axis/numeric scale, no zoom/brush/crosshair, tooltip only on anomaly dots (`svg-timeline.component.ts:26-27,:29-33`).
3. **The decisive new fact:** the central-metrics lane shipped a *second, shared, strictly more capable* primitive (`origin/latest:frontend/src/app/components/charts/svg-line-chart/svg-line-chart.component.ts` — LINE **and STEP** modes, **per-sample** tooltips, sample counts) whose docstring names the resources primitive as its precedent and declares **"ONE shared primitive — never a per-page copy"** (`:10-16`) — while the resources page-local copy persists un-extracted. Neither is a drop-in for the other (resources' carries anomaly-marker/drill-down behaviour the shared one lacks).
4. The **Live tab has no chart at all** (stat cards + chip) and **no sparkline**, while `instances[]`/`children[]` time-varying data is fetched and dropped (A3).

### (c) Missing metrics — **YES for the resource page; the central system also lacks most of them**

The "Tier-3 gap list" exists under that tiering label (not that name) at `.agents/shared/planning/metrics-unification/metrics-inventory-backend-producers.md:144` — "TIER-3 (name-only/absent — new instrumentation): LLM tokens (F14 …); LLM request/latency (F15) + stream stalls (F16); … sources circuit-breakers/token buckets (uncatalogued, zero exposure) …":

| Gap item | On `/resources`? | In central registry? | Notes / evidence |
|---|---|---|---|
| LLM token usage | **NO** | **NO — F14 catalogued, verdict OUT** (`ensemble_llm_tokens_total`, `ensemble_llm_token_usage_missing_total`; `adjudication.py:69,:110-111`) | `usage_metadata` is captured on every streamed call then **dropped — "zero consumers ref-wide"**, "biggest single hole" (`metrics-inventory-backend-producers.md:81,:129`). No source exists anywhere for the page to consume. |
| LLM request latency | **NO** | **NO — F15 OUT** (`ensemble_llm_request_total/_seconds`; `adjudication.py:70,:112-113`) | Only observable today: WARNING log lines (`[LLM-HA]`, `failover_summary()`). Badge/HTTP request latency: **uncatalogued, no family id** (`:75`). |
| Circuit breakers (source adapters: Discord/Slack/Telegram `CircuitBreaker`/`TokenBucketLimiter`) | **NO** | **NO — uncatalogued, "zero exposure, zero consumers outside daemon/sources/"** (`metrics-inventory-backend-producers.md:86,:144`) | Do not conflate with **F17 MCP server health** (`ensemble_mcp_server_state`, `init_guard.py:145-147`) which **IS registered and LIVE** — a different subject. |
| *(context)* Event-loop lag | NO | F1 registered, **DARK · `capture_off`** — false-dark (9 live series publish simultaneously) | `capture_active` never wired (`daemon/api.py:2528-2533`); production defect disclosed at `origin/latest:docs/architecture/metrics-promote-disclosure.md:82-98`. |

**Central families LIVE vs DARK (16 registered = 10 IN + 6 PARTIAL of a 26-family catalog):** **10 LIVE / 6 DARK** (`origin/latest:.agents/shared/planning/central-metrics-v1/evidence/c1-runtime-observation.md:119-142`; `adjudication.py:45,:84-101`). DARK: F1, F3 (wedge counters, `capture_off`), F6 (PG census, `census_gated` — reason polarity inverted), F7 (checkpoint op bytes, `series_absent`), F18 (gateway sessions, `series_absent`), F21 (journal twin, `series_absent` — needs an install dir). LIVE: F0.3, F4, F5, F8, F9, F10, F11, F12, F13, F17.

**Families NOT surfaced on `/resources`:** **all 16** — the resource page surfaces zero central families (it predates the registry and reads none of it). The nearest overlaps it *could* show but doesn't: F10 job-queue depth (it re-derives inline at `resources.py:930-936` instead), F11 worker pool, F13 instance status, F9 readiness.

### (d) Stale / inconsistent with the new `/metrics` vocabulary — **YES**

Strongest evidence (each independently verified):

| Axis | `/resources` | `/metrics` (central) | Divergence evidence |
|---|---|---|---|
| Window vocabulary | `15m\|1h\|24h\|7d` (`resources.py:107-112`) | single clamped `window_s ≤ 7200` (2 h in-RAM) | `origin/latest:daemon/services/metrics_query/constants.py:33` vs `resources.py:107-112` — the FE Resources page can show 7 d; the Metrics page can never exceed 2 h |
| Availability model | **none** (degrade to empty shape, never 503; `resources.py:55-56` D11) | `'live'\|'dark'` + 5 closed reasons | `origin/latest:frontend/src/app/models/metrics.model.ts:58-66`; truth table `docs/architecture/metrics-query-contract.md:313-366` |
| Degradation/auth posture | **open, fail-open-but-empty**; scope manifest `"/api/resources": "*"` (`tests/unit/test_router_scope_manifest.py:113,:106-112`) | **auth-gated, fail-closed** `503 AUTH_NOT_CONFIGURED` | `origin/latest:daemon/routers/metrics.py:106-115,:121,:136-152,:581-705` vs `resources.py:101` (no `dependencies=`) |
| Staleness concept | **none** — `/history` hard-codes `truncated: False` (`resources.py:495`); `/live` only a "NOT A LIVENESS PROBE" disclosure (`:310-317`) | explicit `stale = age > 3× effective interval` (`constants.py:92`; `envelope.py:232`) | implemented once, centrally only |
| Units | concrete fields (`rss_mb`, `cpu_pct`, KB→MB rounding at `resources.py:88,:136-140,:676-680`) | **no `unit` field on the wire at all** — units are name-suffix convention only (`ensemble_*_seconds/_bytes/_total`) | `metric-catalog.md:36-38`; zero `unit` matches across `routers/metrics.py`, `provider.py`, `envelope.py`, `registry.py` |
| Naming | resource-family REST `/api/resources/*`, payload-field names | `ensemble_*` family/metric ids; **no display-name field** (Families tab renders raw `family.id`, `origin/latest:.../families/families-tab.component.ts:60`) | "Naming scatter across five conventions" — the project's own inventory (`metrics-inventory-surfaces-fe-deps.md:171`) |
| Docs | runbooks contain **zero** mentions of metrics/central/`/api/metrics` (explicit 0-hit grep over both runbooks) | contract + disclosure docs never reference `/resources` | `docs/runbooks/resource-cost-control.md`, `resource-hygiene-operator-checklist.md` |

Duplicated concepts between the two pages: the SVG chart primitive (two codebases), the tab-shell pattern (fine — acknowledged house pattern), and the resource spine facts (F0.3 row-counts vs the page's own reads).

### (e) Verdict

**NOT a clean "all of the above".** The honest verdict is: **(a) YES** (own path end-to-end; genuine duplication at F0.3-SQL, counters, admission-tally, and chart-primitive level — but the *split itself* is ratified design), **(b) PARTIAL** (one good-faith hand-rolled chart exists; the real problems are chart-primitive duplication, the chart-less Live tab, and unrendered fetched payload), **(c) YES** (the page shows none of the tier-3 items; the central system also lacks LLM tokens/latency/source-breakers entirely — so no quick re-point fixes this), **(d) YES** (vocabularies, availability models, auth postures, and unit conventions all diverge, with zero cross-references). **The single strongest refactor driver is (d)+(a)'s residue: two parallel observability stacks with two chart codebases and two vocabularies, neither able to serve the other's data.**

---

## PART D — Context

### D8. Data-source seam quantification (the "scattered per-module sources" complaint, quantified)

- **8 REST endpoints** (6 FE-consumed, 2 deliberately FE-orphaned).
- **11 distinct tables** read across the surface: the 4 resource tables (`resource_daemon_bucket`, `resource_instance_sample`, `resource_pid_map`, `resource_limit_policy`) **plus 7 platform tables reached through the traceback join** (`instances`, `event`, `task`, `message_metadata`, `message_queue`, `checkpoints`, `service_tracking`) — `grep -ho "FROM [a-z_]*"` over `resources.py` + `resource_traceback.py` + `resource_anomaly.py`.
- **3 in-process seams** (sampler `TierRing` ring; `sampler_counters`/`flusher_counters`; in-RAM `FindingJournal`).
- **Backend modules spanned:** `daemon/routers/resources.py` (1405 L), `daemon/services/resource_monitor/` (10 files), `daemon/services/resource_traceback.py` (910 L), `daemon/services/resource_anomaly.py` (~1.8 k L), `daemon/models/resource_monitor.py`, wiring in `daemon/api.py:2239-2268` + `daemon/manager.py:822-832`.
- **FE:** 1 service (6 methods) + 4 components + 1 models file (305 L, hand-mirrored).
- Contrast: the central metrics door is **one** provider over **one** in-RAM ring with **zero** table reads (except F0.3's one 4-table COUNT and the census).

### D9. Documented reason the page was NOT routed through central metrics

**There is no "deferred/out-of-scope" line — instead the split is ratified architecture.** Decisive citations:

1. `origin/latest:.agents/shared/planning/central-metrics-v1/architecture-recommendation.md:33-38` — "The house architecture split (leader-ratified decision 4) already assigns durable time-series to the resource_monitor PG tier and alerting to Prometheus (deferred item #3); this lane adds the third leg — the in-process quick-read/query tier — **without disturbing the split**."
2. `origin/latest:.agents/shared/planning/central-metrics-v1/decisions.md:211-214` — "resource_monitor is already the durable time-series tier (leader-ratified decision 4 …)"; ruling at `:237-242` — "the buffer is the quick-view tier ONLY; durable long history stays where decision 4 put it … resource_monitor PG tier for the per-instance views it already owns."
3. `origin/latest:.agents/shared/planning/metrics-collection/metric-catalog.md:1168-1170` — cardinality rule A10/A18: per-instance attribution = PG-only; no daemon-origin family carries a per-instance label.
4. `metrics-inventory-surfaces-fe-deps.md:189` — resource monitor rated **TIER-1** for central onboarding: "durable tiered time-series … REST `/api/resources/history` already time-series shaped, FE chart exists; **read-only poller/adapter suffices**" — i.e. the central system would *poll* the resource tier; replacing its UI was never in the v0.28.6 scope.
5. The resource lane's own planning (`/home/nea/ensemble-src/.agents/shared/planning/resource-cost-control/`) contains **zero** mentions of central metrics/unification (grep-verified); its FE phase was built "parallel with P1–P4 against the frozen §2.8 contract" (`plan-overview.md:50`) — its OWN contract, with "No auth (same-origin /api, house posture); every endpoint degrades to an empty shape, never 503" (`plan-overview.md:97`) — the exact opposite of the central door's fail-closed posture.

**Timeline makes the sequencing explicit:** `/resources` = v0.25.0 (2026-10-19) → central metrics = v0.28.6 (2026-11-02) → agent tools = v0.28.9 (2026-11-03). The page could not have used a door that did not exist; the door then deliberately did not absorb it.

---

## Incidental findings (out of this lane's scope — route to owners)

- 🔴 **F0.3's spine-health leg is structurally broken in production** (independently verified by the controller): the facade exposes `sampler_counters`/`flusher_counters` as `@property` (`daemon/services/resource_monitor/facade.py:71-77`; neither counters class defines `__call__` — grep 0 hits), but the central adapter calls them **with parens** (`origin/latest:daemon/services/metrics_query/wiring/w1.py:366-367`) → `TypeError` swallowed by `except Exception: return False` (`:368-369`) → `_spine_healthy` **always False against the real facade** → `ensemble_resource_spine_tables_accepting_rows` renders 0 ("not accepting") for the three data tables while healthy. The unit test masks it: `_FakeMonitor` defines them as **methods** (`tests/unit/metrics/test_metrics_family_w1.py:78-85`; every f0_3 test passes a fake or `None`, `:208-270`). Observability-only (the page + `spine_rows_written_total` are unaffected). *Type-inferred, not runtime-observed (no daemon contact per constraints).* → owner: central-metrics lane.
- 🔴 **Three of sixteen families report DARK while publishing LIVE series** (F1: 9 series, F3, F6: 15) — `capture_active` never wired (`daemon/api.py:2528-2533`) + census `gated` polarity inverted (`daemon/services/metrics_census.py:323-335`); disclosed at `metrics-promote-disclosure.md:82-98`, routed R-i/R-ii, **not fixed as of `origin/latest`**. Any consumer treating `dark` as "no data" is wrong for these three.
- 🟡 `/api/resources/*` serves **live daemon internals unauthenticated whenever auth is off** (pids, per-instance memory, thread counts) — fail-open by design (D9 posture); the central door fails closed. A refactor that merges surfaces must pick one posture consciously.
- 🟡 Frozen-doc version drift: `metrics-promote-disclosure.md:61-62` and `metric-catalog.md:1172-1174` say `VOCABULARY_VERSION` 1→2; code is **3** (`origin/latest:daemon/checkpoint_metrics.py:218`, bumped by the v0.28.9 lane).
- 🟡 `.agents/shared/planning/metrics-unification/` (the inventories) is **untracked on disk**; committed vendored copies live at `origin/latest:.agents/shared/planning/central-metrics-v1/inventory-vendor/` with different line offsets (+~50).
- 🟡 `/live`'s daemon block is built from the sampler ring which the flusher **drains** — between flush windows the block can be all-NULL ("NOT A LIVENESS PROBE", `resources.py:310-317`). A refactor should surface this on the FE rather than render dashes without explanation.
- 🟢 The tab-shell registry pattern is healthy and shared idiom (do NOT refactor it); the chart primitive is the duplication, not the shell.

## Gaps / unverified

- No live-daemon or DB observation (per constraints): all LIVE/DARK counts are the documented 2026-11-02 observation (`c1-runtime-observation.md`), not a fresh census; live binary version not verified.
- The F0.3 🔴 defect is proven from the type contract + both call sites + the masking fixture, but not executed against a real monitor.
- F7's second registered metric's exact line inside its family block was not re-printed (family membership + histogram verified).
- Runtime behaviour of the FE (actual painted pixels) is owned by the sibling live-capture lane, not this report.

*Method: 3 delegated investigators (FE anatomy; BE data path; central-metrics comparison) + controller cross-verification of every headline claim against both trees. Read-only throughout; this file is the only write.*
