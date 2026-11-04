# Research Findings — FE-only T1 metrics dashboard (wi-32a99618)

Date: 2026-11-03
Author: plan author (giter lane) for commission wi-32a99618
Status: Evidence digest — the plan set's primary evidence base.

## 0. Provenance and a disclosure about this file

The dispatch for this commission carried an attached explorer research digest
("plan-explorer-sources", HIGH confidence, **34,996 chars**) whose body was
**truncated in transit** — the dispatch preserved only its section headers, the
first six sections' summaries, and the tail of the key tables. The remaining
~35 kB did not reach the plan author.

This file therefore **reconstructs the digest from the in-tree corpus**, which
is the same evidence at full fidelity (file:line cited, MEASURED-vs-ESTIMATED
ledger intact) and is the authoritative source for every claim below. Nothing
here is inferred from the truncated dispatch text; every fact was re-read from
the tree at the commit below.

**Tree provenance (this file's facts were read here):**
- Worktree: `/home/nea/ensemble-src-wt-fe-metrics-dashboard`
- Branch: `feature/fe-metrics-dashboard` @ `c32029d1c` (parent `c4161335b` = `origin/latest`)
- Tree carries the **v0.28.9 lineage** (`7fcf1bd71` content), i.e. it DOES contain
  `frontend/src/app/pages/metrics/`.

**Provenance trap (binding for every FE fact in this plan set):** the shared
checkout `/home/nea/ensemble-src` is v0.27.8-era and has **no `/metrics` page
at all**. Every FE fact in this plan set was read from the worktree listed
above. Any audit run against the main checkout alone would wrongly conclude
"the page does not exist"
(`fe-metrics-source-audit.md` §0; `live-capture-log.md` §1;
`fe-ui-audit-and-resource-refactor.md` §1.1).

**Live-release correction (carried):** live is **v0.28.9** (livez 200, epoch
2026-11-03T15:43:16Z). The `staged_release_v0289_composition.promote` field
still reads "NOT performed" — that record is **STALE**; the authoritative key is
`promote_state_2026_11_03`.

---

## 1. Producer census — 16 registered families, 10 LIVE / 6 DARK

Registry SSOT `daemon/services/metrics_query/wiring/registry.py:64`
(`FAMILY_SPECS`); verdicts `wiring/adjudication.py:51-78` (registered set
`:84-101`); chart roster `wiring/roster.py:67-202` (F2 dropped `:204-206`);
availability model `availability.py:65-98`
(`resource-refactor-architecture.md` §2.1).

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

OUT by verdict (`adjudication.py:105+`): F2 (thread CPU/RSS, `:57`), F14 (LLM
tokens, `:69`), F15 (LLM latency, `:70`), F16 (`:71`), F19 (`:74`), F20 (`:75`),
F22 (`:77`) — names force-absent.

**Read the LIVE/DARK column as PRODUCER liveness, not door reachability**
(`fe-ui-audit-and-resource-refactor.md` §4). Live auth is OFF ⇒ every CENTRAL
family is unreachable in production today.

---

## 2. The central query door — facts the FE plan depends on

- **Routes (exactly three):** `/api/metrics/families`, `/api/metrics/snapshot`,
  `/api/metrics/series` — frozen by the `query-routes` fence
  (`docs/architecture/metrics-query-contract.md:225-229`, machine-pinned by
  `tests/unit/metrics/test_central_metrics_contract_doc.py:146-147`). **A new
  route is forbidden.**
- **Scope:** the three query routes carry the ANY-OF set `maintenance:read` OR
  `metrics:read` (`daemon/routers/metrics.py:57-63`). `metrics:read` was minted
  by the v0.28.9 metrics-agent-tools lane; `maintenance:read` also confers the
  destructive checkpoint-cleanup capability.
- **Auth-OFF posture:** the inline `_require_auth_on` gate returns
  **503 `AUTH_NOT_CONFIGURED`** before any metric is read
  (`daemon/routers/metrics.py:70-76`, `:121`, `:150`); the metrics door is
  **never switch-gated** — it serves live data in BOTH switch states once auth
  is on.
- **Window clamp:** `window_s` is silently clamped to `WINDOW_SECONDS = 7200`
  (`daemon/services/metrics_query/constants.py:33`; enforced `routers/metrics.py:778`,
  re-clamped `envelope.py:201-202`, `provider.py:350-351`). The clamp is a
  *substrate* ceiling: the ring retains exactly 480 × 15 s = **2 h**
  (`ring.py:72-101`; `POINT_BUDGET = 750 000` @ `constants.py:29`, 16 B/pt
  `ring.py:64` ⇒ 11.4 MiB worst case), with age eviction enforcing the same
  horizon (`sampler.py:254`). Effective interval = `max(sampler 10 s,
  publisher 15 s) = 15 s` (`config.py:825-826`, `checkpoint_metrics.py:128`).
  Raising central tabs to 7200 s costs **zero** backend work (resolution halves
  to 30 s at the default `max_points` 240).
- **Anchor:** `window_s` anchors at the server's `generated_at_utc`
  (`envelope.py:203,226-227`); there is **no absolute-time param** on the REST
  series door.
- **`delta_change` is NOT on the REST door.** It lives on the **agent tool**
  `metrics_history` (`daemon/tools/metrics_read.py:1151-1158`, compare leg
  `:1124-1132`, `end_offset_s` clamp `:1028,1100`). The FE audit's attribution
  (`fe-metrics-source-audit.md:152`) would have commissioned the wrong backend
  work — record corrected in `fe-ui-audit-and-resource-refactor.md` §4.
- **Snapshot scoping (FE-usable, verified):** `GET /api/metrics/snapshot` reads
  `?family=` and `?name_prefix=` from `request.query_params`
  (`daemon/routers/metrics.py:661-668`) and is BOUNDED (`offset`/`limit`, ceiling
  `MAX_SNAPSHOT_SERIES`) with `truncated` + `next_cursor` surfaced. **The FE
  service currently sends no params** (`frontend/src/app/services/metrics.service.ts:55-60`).
- **The `D3` string at `daemon/routers/metrics.py:71` is an unrelated older
  contract note** — it is NOT the corpus' redirect ruling (which is
  `fe-ui-audit-and-resource-refactor.md` §6 decision 3, `:340-342`). Do not
  conflate.

---

## 3. The six `/api/resources/*` support endpoints (tab 1's custom slot)

8 routes exist under `/api/resources/*`; the FE consumes 6; all are own-path,
zero coupling in either direction to the central door (grep-proven;
`resource-tab-forensics.md` Part B).

| Endpoint | In FE today | Tab-1 role | Notes |
|---|---|---|---|
| `GET /api/resources/live` (`resources.py:276`) | yes | Live strip | Reads the in-process `ResourceMonitor.ring` — **unreconstructible** from the central door |
| `GET /api/resources/history` (`:458`) | yes | Timeline panel | Tier ladder: raw 30 s/48 h, minute 60 s/14 d, hour 3600 s/180 d — the tier knobs live in `daemon/config.py:925-931` (the docstring listing `retention_raw_minutes` / `retention_minute_hours` / `retention_hour_days`) and `:980-1001` (the Field definitions: 2880 min = 48 h, 336 h = 14 d, 180 d). **CITATION CORRECTION (fold, AM-17):** the original cite `daemon/services/flusher.py:360-368` named a file that does not exist — the flusher is `daemon/services/resource_monitor/flusher.py` and the lines cited there (`:355-372`) are the **sweeper's** row-drain body, not tier configuration; and `daemon/config.py:811-827` holds the **metrics** sampler intervals, not the resource tiers. The corrected cites are the two above. |
| `GET /api/resources/anomalies` (`:1080`) | yes | Anomalies panel | **Process-local** in-RAM `deque(maxlen=256)`, reset per restart, ~3.2 h cold-boot replay cap — can never span overnight BY CONSTRUCTION |
| `GET /api/resources/anomalies/{id}/evidence` (`:1121`) | yes | Anomaly drill-down | |
| `GET /api/resources/traceback` (`:983`) | yes | Anomaly drill-down | Joins 9 tables |
| `GET /api/resources/self-cost` (`:694`) | yes | Live strip chip | |
| `GET /api/resources/control-capabilities` (`:1199`) | **no** | **retire (backend)** | Reads `resource_limit_policy`, which has **NO WRITER** ("stays EMPTY in v1", `:1169-1175`) — FE-orphaned |
| `POST /api/resources/profile` (`:1325`) | **no** | **retire (backend)** | Seam-only, never invokes a profiler (`:1239-1240`) — FE-orphaned |

**Retention tiers vs picker windows (the correction that must survive into the
UI):** 48 h / 14 d / 180 d are **PG RETENTION TIERS** (the backing tiers of the
resource ladder), **NOT** picker options. The **picker** vocabulary is
`15m / 1h / 24h / 7d` (`frontend/src/app/pages/resources/timeline/timeline-tab.component.ts:60`,
`TIMELINE_WINDOWS`; `frontend/src/app/models/resources.models.ts:16`).

---

## 4. Routes + the redirect surface (D-3)

- `/metrics` — `frontend/src/app/app.routes.ts:58` (lazy `loadComponent`).
- `/resources` — `frontend/src/app/app.routes.ts:74` (lazy `loadComponent`).
- The app's existing `redirectTo` idiom (`app.routes.ts:35,:104`) **does NOT
  preserve query params** → a `canMatch → UrlTree` redirect or a 3-line redirect
  component is required (`resource-refactor-architecture.md` §7).
- **`?tab=` deep links DO NOT exist anywhere today.** All `queryParamMap` usage
  is jobs/snapshots filter sync (`frontend/src/app/pages/jobs/jobs.component.ts:606-628`,
  `frontend/src/app/pages/snapshots/snapshots.component.ts:110-111`); no mat-tab
  page reads params. New work: read `queryParamMap` → set the tab signal
  (`frontend/src/app/pages/metrics/metrics.component.ts:118` `activeTabId`),
  write back on `onTabChange` (`:204-209`).
- Gear menu: `Resources` is 5th of 9 (`frontend/src/app/app.ts:580`); `Metrics`
  is 7th of 9 (`:597`).
- Spec surface the retirement must edit: `resources-gear-menu.spec.ts:19-24,26-41,43-49,72-93`,
  `metrics-gear-menu.spec.ts:30-45,47-53`,
  `resources.component.spec.ts:23-25` (production source-grep pins over
  `resources.component.ts` / `.html`). The audit's sweep
  (`resource-refactor-architecture.md` §7) was **NOT exhaustive** — a repo-wide
  `grep -r "/resources"` over all `*.spec.ts` is an owed task.

---

## 5. Chart engines — the two hand-rolled engines and their divergence

**Both existing engines** (`fe-metrics-source-audit.md` §7.1;
`resource-refactor-architecture.md` §5.1):

| Asset | Size | Capabilities |
|---|---|---|
| `frontend/src/app/components/charts/svg-line-chart/svg-line-chart.component.ts` | 523 L + 241 L spec | STEP/LINEAR + area, shape-only by design; **declared FEATURE-FROZEN at authoring time** (`:24-26` "zoom / brush / crosshair / gradients are OUT"); UTC `HH:MM` ticks only (`:250-255` — cannot disambiguate days beyond 24 h) |
| `frontend/src/app/pages/resources/svg-timeline/svg-timeline.component.ts` | 538 L + 237 L spec | **the app's most capable engine**: window ladder, series toggles, anomaly markers + severity colors, click → drill-down window; also FEATURE-FROZEN (`:26-27`); exports pure helpers `timelineXScale`/`timelineYScale`/`buildLinePath`/`buildAreaPath`/`anomalyDrilldownWindow`/`formatUtcClock` |

- **~1,061 lines, ~106 lines of identical duplicated helpers** (6 pairs: scale
  fns ×2, path builders ×2, UTC `HH:MM` formatters ×2, `clamp01` ×2 —
  `svg-line-chart.component.ts:108-271` vs `svg-timeline.component.ts:90-167`).
- **The newer engine is strictly LESS capable than the older one** — no window
  picker (`charts-tab.component.ts:195` hardcodes 3600), no series toggles, no
  anomaly markers, no drill-down, no y-axis labels/units.
- The anomaly marker `<title>` already composes the API words:
  `markerTitle()` = `` `${kind} · ${shape} · ${severity} · peak ${peak}` ``
  (`svg-timeline.component.ts:530-532`) — but it is a hover-only native
  `<title>`, never a visible label.

**Charting capability table as audited** (`fe-metrics-source-audit.md` §3):

| Capability | State | Note |
|---|---|---|
| Time-range selection | ABSENT | window hardcoded 3600 s (`charts-tab.component.ts:195`) |
| Comparison-to-baseline | ABSENT | — |
| Multi-series overlay | PARTIAL | primitive takes an array but only consumer passes `[chart]`; per-series y-normalization makes overlay meaningless |
| Drill-down | ABSENT on `/metrics` | zero click handlers (present on `/resources`) |
| Anomaly spotting | ABSENT on `/metrics` | present on `/resources` |
| Export | ABSENT | — |
| Zoom/pan | ABSENT | — |
| Tooltips | PARTIAL | native `<title>` "HH:MM UTC · value" — no units, no y-axis labels |

---

## 6. Bundle + dependency facts (MEASURED — re-derived at lane base, 2026-11-03)

> **E-1 STANDING LANE RULE (folded 2026-11-03, tightened by the plan-review's
> C-3):** every MEASURED figure a phase exit criterion depends on must be
> **re-derived at lane base** (this worktree's tree) before that phase freezes —
> **and a label without a COMMAND is not a re-derivation**. The figures in this
> table were re-derived by a fresh production build at the lane base — **see §14
> for the full method, the exact commands, their verbatim output, and the
> comparison against the architect's AM-2 figures.** The machine-readable
> single source is **`bundle-baseline.json`** (+ the re-derivation tool
> `bundle-baseline.measure.js`), both in this plan directory.

| Fact | Value | Status |
|---|---|---|
| Initial budget semantics | `initial` = entry points + static imports only; **lazy chunks counted by NO budget** (only `initial` + `anyComponentStyle` configured; 5 more types unused; zero per-chunk assertions repo-wide) | MEASURED (builder source: `node_modules/@angular/build/src/utils/bundle-calculator.js:155-167` — `InitialCalculator` sums the chunks flagged `initial`; `index.html` is an **asset**, not a chunk, so it is NOT in the budget total) |
| Initial total today | **5,909,018 B** (36 chunk files) vs the 6,000,000 B decimal error budget ⇒ **90,982 B headroom**; the 1 MB warning budget is exceeded by 4,909,018 B (pre-existing). `index.html` (9,257 B) is outside the budget ⇒ whole initial payload 5,918,275 B / 81,725 B headroom | **RE-DERIVED at lane base 2026-11-03** (§14). The digest's original `89,982 B` headroom was an **arithmetic slip** (6,000,000 − 5,909,018 = **90,982**); the 5,909,018 B total itself was **correct** |
| Lazy baseline | **52** lazy chunks / **1,702,633 B** (≈1.70 MB); **`/metrics` chunk 27,672 B**; **`/resources` chunk 38,914 B**; largest lazy **157,141 B** | **RE-DERIVED at lane base 2026-11-03** (§14). The digest's "52 chunks / 1.70 MB / metrics 27,672 B" all reproduce **exactly**; its "largest lazy 135,690 B" is now the **second** largest (a drift of one chunk position) |
| `chart.js` 4.6.1 | 6.2 MB unpacked; **1 dep** (`@kurkle/color` ^0.3.0); MIT; last publish 2026-10-22 | MEASURED (re-verified 2026-11-03 via `npm view`) |
| `jest-canvas-mock` 2.5.8 | deps `cssfontparser` ^1.2.1 + `moo-color` ^1.0.2 | MEASURED (re-verified 2026-11-03 via `npm view`) |
| `echarts` 6.1.0 / `apexcharts` 8.7.0 / `angular-gridster2` | 60.3 MB / 30 MB / 480 kB; gridster2 peers `@angular/core ^22` — **incompatible with our 21.2.5**; its `angular2-d3` premise is **FALSE** (package 404s) | MEASURED |
| Governance | pack Leg 7 hard-fails on ANY `frontend/package.json` diff (`test/packs/metrics_fe_dual_side_test.sh:275-286`); the "zero FE deps" claim lives at **six** live sites (see AM-24) | MEASURED (AM-24 corrected the count from five) |
| FE deps today | 33 deps + 12 devDeps; `mermaid ^11.4.0` (pulls d3 7.9.0 transitively); **zero charting libraries** | MEASURED (`frontend/package.json`) |
| The app is ZONELESS | `zone.js` declared but never loaded (no `polyfills` entry, no provider in `app.config.ts`); Angular v21 defaults zoneless ⇒ canvas redraw is unentangled, **provided** the rule "no Angular-rendered overlay may depend on a library redraw" is written down | MEASURED |
| Test surface | 1,645 L of metrics specs (svg-line-chart 241, metrics.component 519, charts-tab 213, overview-tab 274, families-tab 173, metrics-gear-menu 98, metrics.bindings.pins 127) + a Playwright gate (`frontend/e2e/metrics-gate.spec.ts`, 3 scenarios) | MEASURED — **informational only**: no exit criterion in the plan set gates on a spec **line count** (AM-26 replaces the line-count framing with a per-assertion disposition table) |
| The pins that ban charts | `frontend/src/app/pages/metrics/metrics.bindings.pins.spec.ts:94` bans `['chart.js','d3-',"from 'd3'",'ngx-charts','moment','dayjs']` over the svg-line-chart source + metrics.service.ts; the spec ALSO **reads the engine file from disk** at `:76-88` — a path that Phase 4 deletes (AM-10) | MEASURED |

---

## 7. FE verification-environment facts (the live capture method)

`live-capture-log.md` §3 (canonical, reproduced verbatim):

```js
const { chromium } = require('/home/nea/ensemble-src/frontend/node_modules/playwright');
const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
const context = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
const page = await context.newPage();
const api = [];
page.on('response', r => { if (r.url().includes('/api/'))
  api.push({ method: r.request().method(), status: r.status(), url: r.url() }); });
await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
try { await page.waitForLoadState('networkidle', { timeout: 15000 }); } catch {}
await page.waitForTimeout(3000);
await page.screenshot({ path: out, fullPage: true });   // viewport-only where noted
```

- Driver: **Node Playwright 1.60.0** from `frontend/node_modules/playwright`
  (python Playwright absent on this host; raw chromium FATALs without
  `--no-sandbox`).
- **Timing contract:** `domcontentloaded` (30 s) → best-effort `networkidle`
  (15 s cap, ALWAYS times out — the SSE stream `/api/notifications/stream` +
  8 s `/api/resources/*` polls never settle) → **fixed 3 s settle** → screenshot.
- Viewports: 1600×1000 (desktop) and 900×1000 (narrow probe of `/metrics`).
- Instrumentation: every `/api/` response logged `{method, status, url}`
  (GET-only proof), console errors, pageerrors; each run wrapped in
  `timeout 120`.
- The in-repo Playwright configs already exist and are stubbed
  (`frontend/playwright.metrics-gate.config.ts`: `testMatch:
  /metrics-gate\.spec\.ts/`, workers 1, retries 0, port 14203, no daemon, all
  API calls stubbed via `page.route`) and
  `frontend/playwright.resources-gate.config.ts` (port 14202).

**Live-capture safety posture (the standard this lane must match):** capture was
100 % GET (566 + 81 requests swept, 0 non-GET); livez uptime monotone
throughout. The live daemon was read-only and is **out of bounds** for this
lane's verification (the lane runs against a fenced DEV daemon).

---

## 8. Live-capture findings the plan set must close (the pixel list's source)

`live-capture-log.md` §7 + `fe-ui-audit-and-resource-refactor.md` §1.2.

**The `/metrics` empty state — honest about the cause, dishonest about the
consequence:**

1. Two identical banners, one self-contradictory ("serves no metrics. — the
   last known values are kept", no values rendered).
2. TWO Retry buttons that can never succeed (this is daemon config, not a
   transient).
3. Three tabs left enabled that all fail identically.
4. No HTTP status surfaced, no runbook link, no names-only family catalog.
5. Header shows a **green health dot** while the body reports hard failure.
6. 900 px CSS collision (banner text vs Retry button).
7. Metrics is not in the top nav at all (gear-menu only, 7th of 9).

**The `/resources` Timeline with real data** — it CONTAINS the answer to "what
changed overnight" (the ~15:42Z V-collapse aligns with the v0.28.9 boot epoch)
but WITHHOLDS the interpretation. Pixel defects:

- NO y-axis at all; three series with two units (MB and %) on one unlabeled
  axis, each independently min-max normalized, so the area fills visually read
  as a stacked-area chart — nonsense across units.
- legend advertises 5 series, plot draws 3.
- four event dots unlabeled, unmoored (the blue dot does NOT align with the
  actual collapse), evenly spaced so they read as tick marks.
- no date; 1 h default window so "overnight" is unframeable despite 24 h/7 d
  chips existing.
- no last-updated stamp; the chart occupies a thin band with the bottom ~40 %
  of the viewport empty.

**The `/resources` Live tab** — subtitle promises "per-instance attribution"
and NOTHING names a single instance; the arithmetic does not cohere:

| Displayed | Value | Coherence problem |
|---|---|---|
| attributed / unattributed | 71 MB / 1,119 MB | sum = 1,190 MB implied total |
| daemon RSS | 633 MB | matches NO relation to 1,190 MB |
| cgroup current | 6,178 MB | best-fit basis (anon 901 + slab 291 = 1,192 MB) never stated |
| Coverage | 0 % | incompatible with 71/1,190 ≈ 6 % under any reading |

And the sharpest inference the page never makes: daemon RSS (633) < cgroup anon
(901) → 268+ MB of anonymous memory belongs to something the page cannot
identify — exactly the question it exists to answer.

**Anomaly markers are process-local** (in-RAM `deque(maxlen=256)`, reset per
restart, ~3.2 h cold-boot replay cap) — they can never span overnight BY
CONSTRUCTION. The 24 h/7 d history is real (2,723 raw-30 s points / 13,387
minute-tier points probed live), but the marker layer cannot annotate it.

**Ranked top-5 developer-user frictions:**

1. 🔴 `/metrics` dark by install posture — auth OFF means the surface built to
   answer these questions serves nothing.
2. 🔴 Attribution panel: cannot name a culprit + non-reconciling math.
3. 🔴 Timeline has no y-axis — shape, never levels.
4. 🟠 Events anonymous and mortal — the journal dies at restart; the loudest
   event (the promote itself) carries no annotation.
5. 🟠 Zero-state UX + discoverability — Retry offered for a config error;
   gear-menu 7th of 9; 900 px CSS collision; enabled-but-empty chips; em-dash
   ring-drain transients.

**Anomaly rows exist server-side and the FE renders no anomalies list on either
page** — they surface only as the anonymous Timeline dots, unexplained.

---

## 9. The honesty gap this lane must fix in the FE's own code

`fe-metrics-source-audit.md` §7.4 — **silent omission**:

> `chartSpecs` filters only on `role === 'chart'`, **not** on
> `availability.state`, then `slice(0, METRICS_SELECTOR_CEILING)` with ceiling
> **50** (`frontend/src/app/pages/metrics/metrics.component.ts:40,:162-175`).
> Dark families' names consume the budget, and if the roster exceeds 50, live
> families later in catalog order are cut — the FE surfaces the *door's* page
> bound (`payload.truncated`) but **not its own slice**.

Verified in the tree at this commit: `METRICS_SELECTOR_CEILING = 50`
(`metrics.component.ts:40`), `chartSpecs` computed at `:162-175`, slice at
`:174` — **no availability filter**.

Other small defects in the same class (`fe-metrics-source-audit.md` §7.5):

- **Dead input:** `families-tab.component.ts:210` `readonly active = input(false);`
  is declared, documented and passed by the shell
  (`metrics.component.ts:188`) but **never referenced** — it implies a poll gate
  that does not exist (the tab owns no network).
- **Families empty copy not error-gated** — "No families are registered." can
  render under a failed load.
- **Families table is inert** — `<th>` cells (`families-tab.component.ts:50-55`)
  have no sort, no click, no `aria-sort`.
- **`chartUtcTicks`' `HH:MM`-only label** cannot disambiguate days beyond 24 h.

---

## 10. What is genuinely good — the safety net this lane inherits

(`fe-metrics-source-audit.md` §7.6; `fe-ui-audit-and-resource-refactor.md` §1.5)

- Polling hygiene with supersede tokens + tab-hide stop.
- Never-blank stale-degradation (last-good kept, STALE surfaced).
- STEP rendering for counters — data honesty (pinned in e2e,
  `frontend/e2e/metrics-gate.spec.ts:283-295`).
- UTC-everywhere; strong test pinning.
- The resource svg-timeline's window ladder is REAL and the `/history` API
  serves 24 h/7 d truthfully (probe-proven).
- The fail-closed banner copy names the real root cause — the best dark-state
  copy on either page.
- The v0.28.9 badge closes the "registry-degraded reads as empty" confusion
  (`metrics.component.ts:225-229`).
- The SVG primitive's **pure helpers are exported and unit-asserted**
  (`svg-line-chart.component.ts:28-30`) — which is why the audit could cite them
  precisely. The consolidated renderer must keep this property.

---

## 11. The three binding rulings this plan set folds (given, not re-litigated)

**D-1 CHARTING.** Chart.js v4 SOLE vendor for interactive panels,
LAZY-CHUNK-ONLY (~65 kB gz, ZERO first-paint growth); ONE consolidated house SVG
renderer for minis/fallback. Supersedes the earlier ECharts settlement;
ApexCharts/Gridster2 dropped. **Interaction (hover readout, baseline, ranges) IS
in scope** — the "drop Chart.js if interaction deferred" flip-condition does
NOT apply. SAME-COMMIT pins-spec/pack governance amendment in the same commit
that adds the dependency. The lane's FIRST housekeeping commit = the audit-doc
what-changed-and-why amendment.

**D-2 AUTH POSTURE.** The central door's fail-closed posture STAYS (no
weakening). T1 ships as designed. The FE MUST distinguish 503
`AUTH_NOT_CONFIGURED` from other dark causes and render an ACTIONABLE
empty-state ("metrics door is fail-closed pending auth configuration — see
runbook" → `docs/runbooks/oauth2-auth.md`). Enabling auth is a USER MORNING
ITEM — the lane must NOT touch live auth config; the exact operator recipe
(steps + restart implication) goes in the plan's close-out section.

**D-3 ROUTE FATE.** Retire `/resources` with a SAME-RELEASE redirect to
`/metrics?tab=resource`. Predefined tabs stand; Gridster2 door closed.

---

## 12. Verification-environment facts for the DEV (data-bearing) route

Read in the tree at this commit:

- The worktree `.env` is mode `600` and carries the fence block:
  `ENSEMBLE_SELF_ENV=dev`, `POSTGRES_HOST=127.0.0.1`, `POSTGRES_PORT=5432`,
  `POSTGRES_DB=ensemble_dev`, `POSTGRES_USER=ensemble_dev_app`,
  `POSTGRES_PASSWORD=…`, `DAEMON_PORT=8079`.
- **No `data/` directory exists in the worktree** ⇒ no `data/ensemble.json`
  exists ⇒ the daemon would AUTO-CREATE one on first boot
  (`daemon/ensemble_config.py:56-89`). Auto-detect: `POSTGRES_HOST` AND
  `POSTGRES_DB` set AND no SQLite DBs ⇒ `database="postgres"`. The env vars
  override the file values at runtime (`daemon/ensemble_config.py` docstring:
  "Environment variables take precedence over file values at runtime").
- **The `ensemble.json` pin OUTRANKS the env** for the *engine* choice, and
  because it is persisted on first boot, a stale file silently pins every later
  boot from that tree. It must be asserted before every dev boot (see
  `phase9-plan.md`).
- **Auth ON requires**: `client_id` + `client_secret` + `issuer` (https unless
  `allow_insecure_cookie=true`) + non-empty `allowed_origins` — else the config
  validator raises and the daemon refuses to boot (exit 78)
  (`daemon/config.py:3930-3965`, `:4009`; runbook `docs/runbooks/oauth2-auth.md`
  §1b).
- **The bearer lane never contacts the IdP**: `AuthMiddleware` verifies a
  static bearer locally (`daemon/middleware/auth.py:358-377`,
  `_verify_bearer` → `service.lookup_bearer`, `:440-447`); no OIDC discovery
  fetch happens at boot. A DEV bearer-only leg therefore needs placeholder IdP
  *config* values (to satisfy the validator) but no live IdP.
- The bearer row is written to the DEV database by
  `daemon/auth/cli.py issue-token` (`--scopes` is **required** for a
  non-bootstrap mint; the v1 scope taxonomy is closed —
  `daemon/auth/scopes.py:66-120`; the query door accepts
  `maintenance:read` OR `metrics:read`).
- The metrics door's `Accept: application/json` requirement is already carried
  by the FE service (`metrics.service.ts:38`, `JSON_HEADERS` — the auth
  middleware 302-redirects a wildcard-Accept request).

---

## 13. House conventions binding on this lane

- Commit style `type: summary`.
- `.agents/tester/PACKS.md` registration for any new pack (keep-both,
  newest-first). The plan SPECIFIES pack additions; it does not create them.
- TypeScript only via `frontend/`'s local devDependency — never bare `tsc` /
  `npx tsc` outside `frontend/` (the `tsc` npm package ≠ TypeScript; bare
  `npx` outside a package dir = remote fetch + execute). Safe idiom:
  `cd frontend && ./node_modules/.bin/tsc --noEmit -p tsconfig.app.json`.
- The `agents/` prompt-artifact tree stays untouched (baseline
  `agents/.prompt-artifacts.sha256` = **161** entries, verified 161 lines at
  this commit). `.agents/` is project-shared and in scope; `agents/`
  definitions tree is NOT.
- Targeted `git add` of new paths only — never `git add -A`.
- Frozen-binary boot smokes from daemon-child shells MUST pin
  `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` explicitly; `env -u` on it strips
  the ambient guard and the sweep kills live processes.
- `env -u POSTGRES_*` is a **silent NO-OP** (bash does not glob env names) —
  scrub per-variable, one by one.
- Never resolve live-shaped paths (HOME-derived install dirs) in test scripts.

---

## 14. LANE-BASE MEASUREMENT (2026-11-03) — the bundle baseline re-derived

**Why this section exists.** The architect's review (AM-2) flagged the bundle
baseline in §6 above as carrying **provenance doubt** (the digest's figures may
have been measured on the main checkout's v0.27.8-era FE) and reported its own
dist-derived numbers as **5,115,820 B initial / 884,180 B headroom**. The
leader ordered a re-derivation **at lane base** before the fold. This section is
that re-derivation, in full.

### 14.0 The single-source artifact (fold #3, A-8)

The baseline lives in **`bundle-baseline.json`** — machine-readable, the single
source the T1 bundle guards read — with **`bundle-baseline.measure.js`** as the
re-derivation tool. A number duplicated only in plan prose can drift; a file
cannot. Reproduce with:

```
cd frontend && node ../.agents/shared/planning/metrics-unification/fe-t1-build/bundle-baseline.measure.js dist/frontend/browser
```

Its verbatim output is §14.4's table, row by row.

### 14.1 Method (the build)

- Tree: `/home/nea/ensemble-src-wt-fe-metrics-dashboard` @ `c26cec612`
  (the lane's three commits — `c32029d1c`, `3a6bf7ba5`, `c26cec612` — are
  **docs-only**, so HEAD's `frontend/` tree **is** the lane base's `frontend/`
  tree; a build at HEAD IS a lane-base measurement).
- `frontend/node_modules` was **absent** (fresh worktree) → `npm ci` inside
  `frontend/` (lockfile `package-lock.json`, 506,083 B; node v22.23.2, npm
  10.9.8). `npm ci` touches only untracked `node_modules/`.
- Build: `cd frontend && ./node_modules/.bin/ng build --configuration production`
  — the **local** toolchain only (never a bare `tsc`/`npx tsc` outside
  `frontend/`). Output: `frontend/dist/frontend/browser/` (93 files, untracked).
- **No daemon was booted** and nothing was written to any tracked file.

### 14.2 What the BUILDER itself reported

From the build log (`/tmp/fe-t1-build-prod.log`):

```
Initial chunk files | Names | Raw size | Estimated transfer size
scripts-DN54Y2NQ.js | scripts | 3.56 MB | 737.45 kB
styles-S2UMLXTA.css  | styles  | 655.43 kB | 58.41 kB
… 36 chunk rows …
| Initial total | 5.91 MB | 1.28 MB |

▲ [WARNING] bundle initial exceeded maximum budget.
   Budget 1.00 MB was not met by 4.91 MB with a total of 5.91 MB.
```

The warning's own arithmetic (5.91 − 4.91 = 1.00 MB) is consistent with a
total of **5,909,018 B** (5.909 → truncated "5.91"; 4.909 → "4.91").

**Builder semantics** (`node_modules/@angular/build/src/utils/bundle-calculator.js:155-167`):
`InitialCalculator` sums the chunks flagged `initial` — entry points plus their
transitive **static** imports. `index.html` is an **asset**, not a chunk, so it
is **not** in the budget total. Lazy chunks are counted by **no** budget (only
`initial` + `anyComponentStyle` are configured).

### 14.3 Two independent derivations of the same number

| Method | Files | Bytes |
|---|---|---|
| **A** — the builder's own "Initial total" table (36 chunk rows), summed from disk by exact byte size | 36 | **5,909,018** |
| **B** — an independent closure: `index.html`'s referenced files + the transitive **static**-import closure of every referenced JS (`from "…"`, `import "…"`), excluding dynamic `import(…)` | 36 | **5,909,018** |
| **C** — the whole initial payload (A + `index.html`, 9,257 B) | 37 | 5,918,275 |

Methods A and B agree **byte-for-byte** and the file sets are identical (set
difference empty in both directions) — two independent derivations, one number.

### 14.4 The measured baseline (frozen for the lane)

| Figure | Value |
|---|---|
| **Initial total (builder basis — the budget's own number)** | **5,909,018 B** |
| Error budget (`initial`, decimal) | 6,000,000 B |
| **Headroom** | **90,982 B** |
| Warning budget (`initial`, decimal) | 1,000,000 B — exceeded by 4,909,018 B (**pre-existing**) |
| `index.html` (outside the budget) | 9,257 B → whole initial payload 5,918,275 B, headroom 81,725 B |
| Lazy chunk count | **52** |
| Lazy bytes total | **1,702,633 B** (≈1.70 MB) |
| `/metrics` lazy chunk | **27,672 B** (`chunk-ZA3RV4HY.js`) |
| **`/metrics` route FETCH SET** (the route chunk + its **direct** static deps) | **496,338 B** — route chunk 27,672 + `chunk-6Y6F4JKW.js` 43,688 (**LAZY-ONLY**) + `chunk-AOZCSBSC.js` 1,348 (**LAZY-ONLY**) + 6 initial deps. **Newly measured (fold #3, C-1):** this is the threshold that actually catches a **static** `chart.js` import inside the lazy metrics tree — the route chunk alone does **not** grow (the vendor lands in a lazy-only sibling chunk) |
| `/resources` lazy chunk | **38,914 B** (`chunk-FOLCEMN3.js`) |
| Largest lazy chunk | **157,141 B** (`chunk-QS2K3YLH.js`); second 135,690 B |
| `anyComponentStyle` warnings | **9** — `agent-selector` 8.77 kB · `snapshots` 12.29 · `jobs` 14.77 · `add-source-modal` 8.32 · `snapshot-detail-drawer` 8.70 · **`blueprint` 18.32 (the LARGEST)** · `checkpoint-cleanup` 8.70 · `instance-list` 10.17 · `chat-interface` 16.15 — all in **other** components' SCSS, **pre-existing**; the `initial` budget warning is pre-existing too. **CORRECTION (fold #3, C-3): the original figure was 8 — `blueprint.component.scss` had been omitted.** The guard is a **set difference** against this set, **never a count ceiling** |

### 14.5 Comparison against the architect's AM-2 figures — **MISMATCH, disclosed**

| Source | Initial total | Headroom | Verdict |
|---|---|---|---|
| Architect AM-2 (shipped `releases/v0.28.9` dist) | 5,115,820 B | 884,180 B | **undercounts by 793,198 B** |
| **This fold (fresh lane-base build)** | **5,909,018 B** | **90,982 B** | **authoritative** |
| Research digest §6 (original) | 5,909,018 B | *89,982 B* | the **total was correct**; the headroom had a **1,000 B digit slip** (6,000,000 − 5,909,018 = 90,982) |

**The delta is fully explained, not a mystery.** AM-2's number is exactly the
sum of the files `index.html` **references** (`styles` + `scripts` + `main` +
the 10 `modulepreload` chunks) **without** the builder's transitive
static-import closure — 5,125,077 − 9,257 (`index.html`) = **5,115,820**.
The builder counts **36** initial chunk files; the index.html-referenced subset
is **13** of them. The remaining **23** chunks (≈793 kB) are reachable from the
entry points by **static** import and are counted by the builder — and are
printed in the builder's own "Initial chunk files" table, which AM-2 did not
reproduce.

**Cross-check (the strongest one available).** The **shipped** `releases/v0.28.9`
dist on disk was measured with the same script: **identical output** — 37 files,
5,918,275 B, 81,725 B headroom, 52 lazy chunks, `/metrics` chunk 27,672 B,
`/resources` chunk 38,914 B, and the **same chunk filenames and byte sizes**
(`chunk-K3LECV3O.js` 316,339 B, `chunk-QU4CTU7Q.js` 263,458 B, `main-SAUX6EY3.js`
215,495 B, `scripts-DN54Y2NQ.js` 3,559,319 B). The lane base and the shipped
v0.28.9 artifact are the **same FE tree**, byte-for-byte — which is the
expected result of a docs-only lane and independently confirms the build is
reproducible.

### 14.6 What this changes for the plan (AM-2 folded)

1. **The baseline is 5,909,018 B / 90,982 B headroom** — NOT AM-2's
   5,115,820 / 884,180, and NOT the digest's 89,982.
2. **AM-2's MECHANISM findings stand** — they are the part that matters:
   - (a) **lazy chunks are counted by no budget** ⇒ a **lazy** Chart.js leak
     trips **nothing** (no build failure, no pack failure). The plan's original
     claim ("a static-import leak hard-fails the production build … and both FE
     packs") was **false** for the lazy case.
   - (b) an **eager** Chart.js leak **does** break the build against the
     correct baseline: the headroom is **90,982 B** and chart.js's raw
     minified size is ~250–400 kB ⇒ 5,909,018 + 250 kB > 6,000,000 ⇒ the
     `initial` **error** budget trips. (Against AM-2's undercounted baseline
     the same leak would have fit — which is exactly why the baseline had to be
     re-derived.)
   - (c) B1's "byte-identical file list" is unsatisfiable under
     `outputHashing: "all"` (any change renames chunks) ⇒ B1 is redefined
     numerically (§6.3 of the plan).
3. **The guard is co-located** (Phases **1, 4, 5 and 9** exit criteria), not a
   Phase-9-only gate — the leak window spans Phases 1–8.
4. **C-1 (fold #3):** a **static** `from 'chart.js'` inside the lazily-loaded
   metrics tree satisfies the initial-total delta, the initial-chunk marker scan
   **and** a location-only import rule — the vendor is extracted into a
   **lazy-only sibling chunk**. Hence the guard set gains (a) the import **SHAPE**
   gate (**every** `chart.js` import is the dynamic `await import('chart.js')`
   form), (b) the **route fetch set** as the measurement that catches it
   (496,338 B baseline), and (c) a **second planted variant** (the static case,
   not only the eager one).
5. **C-3 (fold #3):** the `anyComponentStyle` baseline is a **SET of 9 paths**,
   captured mechanically from the Phase-1 build log, and compared as a **set
   difference** — a count ceiling is forbidden.
