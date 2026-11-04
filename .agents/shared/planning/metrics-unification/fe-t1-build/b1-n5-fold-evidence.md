# B-1 + N5 fold evidence — the FE dual-vocabulary mapping + guard enforcement (2026-11-04)

Fold of the tester functional gate's **BLOCKER B-1** (the C-1 real-pointer
leg dead on the dual vocabulary) and **N5** (the guard's false-green seams),
per the FIX ORDER #3 ruling (FE-only stands; daemon READ-ONLY). Worktree
`/home/nea/ensemble-src-wt-fe-metrics-dashboard`, branch
`feature/fe-metrics-dashboard`, base tip `ff064658c`.

## 1. The fix (B-1), file by file

| File | Change |
|---|---|
| `frontend/src/app/pages/metrics/resource/timeline/timeline-series.ts` | **THE COMPATIBILITY SHIM** — typed table `ENGINE_SERIES_COMPAT` (7 exact engine keys) + `INSTANCE_SERIES_PATTERN` (2 instance suffixes), each entry carrying `anchorKey` (the FE dataset the marker joins), `unitClass`, `engineUnit`, `factorToAnchorUnit` (DOCUMENTED, never consumed — see §3) and per-entry `provenance`; `resolveTimelineSeriesJoin()` resolves BOTH vocabularies (FE pass-through → engine table → instance pattern → **null**, fail-closed); `markerAnchorKey()` resolves the enabled-aware dataset anchor. The table's comment block states the dual vocabulary, both daemon source sites, that this is a COMPATIBILITY SHIM, and that the durable fix is daemon-side (routed — council-fold-record §7). |
| `frontend/src/app/pages/metrics/resource/timeline/timeline-tab.component.ts` | `unitClassOf()` delegates to the shim; `panelAnomalies()` anchors each marker to the panel's dataset via `markerAnchorKey` — the overlay's draw join (`houseDatasetIndex`) and click join (`houseNearestAnomaly`) both match on the anchor. The LABEL is untouched: `anomalyMarkerLabel` reads the anomaly, so the marker still carries the API's engine words verbatim (5.7). |
| `frontend/src/app/pages/metrics/charts/chart-panel.component.ts` | `onChartClick` scans the mode-'index' elements list for the MARKER-BEARING element (an anchor may be any dataset of the panel) and falls back to the first element's plain payload — plain-sample clicks are byte-identical to before. |
| `frontend/e2e/resources-gate.spec.ts` | The anomaly fixture is the REAL dual-vocabulary shape: `series_key: 'daemon.rss_kb'` + KB-scale peak/baseline (1,945,600 / 1,740,800 = 1900/1700 MB × 1024), verbatim, no conversion. |
| Specs (jest) | `timeline-series-compat.spec.ts` NEW (21 tests: table consistency, the exact enumerated-keyspace pin, scale-shape pin, provenance pin, instance pattern, resolver pass-through/engine/unknown, anchor fallback); `timeline-tab.component.spec.ts` +4 B-1 tests (engine-key marker joins the memory panel with the anchored seriesId + verbatim label; the FULL 9-key engine vocabulary; unknown-key fail-closed; the anchored end-to-end drill-down); `chart-panel.component.spec.ts` +2 (non-first-dataset anchor resolves; plain-payload fallback unchanged); `anomalies-panel.component.spec.ts` +1 engine-key row (verbatim render + wire emit, the gate's real receipt values). |

## 2. The real payload — the mapping's derivation sources

**The fenced dev daemon** (Phase-9 fence §9.1/§9.2 shape: per-variable scrub
→ worktree `.env` → `POSTGRES_DB=ensemble_dev` asserted → `PORT=8079`
re-exported → scratch `ENSEMBLE_DATA_DIR=/tmp/b1-fold-data` with the exact
pre-seeded `ensemble.json` (jq pin true) →
`ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pinned → the pinned interpreter
`python -m daemon`, auth OFF). Falsification: `env | grep POSTGRES` → 0 hits
pre-source; `No PostgreSQL ENV vars detected, defaulting to sqlite` → **0
hits**; `Loaded ensemble config: database=postgres` + `Creating PostgreSQL
engine: 127.0.0.1:5432/ensemble_dev` verbatim; `/proc/<pid>/environ`
read-back: `POSTGRES_DB=ensemble_dev`, `PORT=8079`, `ENSEMBLE_DATA_DIR`,
sweep pin all present.

- `/api/resources/history?window=24h` (REAL): 155 points, keys
  `["cgroup_mem_mb","daemon_cpu","daemon_rss_mb","instances_active","t","threads"]`
  — the FE vocabulary, exactly as the gate reported.
- `/api/resources/anomalies?window=24h` (REAL, journal fired 08:1xZ after the
  `restart-recompute replayed 151 minute-tier rows` receipt at 08:10:29):
  **three engine keys** —
  | id | series_key | peak | baseline | severity |
  |---|---|---|---|---|
  | anom-000004 | `daemon.rss_kb` | 345996.0 | 303936.0 | high |
  | anom-000003 | `daemon.cpu_pct` | 10.2833 | 6.4667 | high |
  | anom-000001 | `cgroup.anon_kb` | 10359808.0 | 1576960.0 | high |

  This is a STRICTER receipt than the gate's (two keys): this fold's live
  journal additionally emitted `daemon.cpu_pct` — the table entry upgraded
  from PRODUCER-SOURCE-only to REAL-PAYLOAD provenance.
- The remaining four exact keys (`daemon.anon_kb`, `daemon.threads`,
  `daemon.fds`, `cgroup.current_kb`) were NOT in the journals observed
  (in-RAM `deque(maxlen=256)`, reset each restart); they enter the table
  from the PRODUCER SOURCE, READ-ONLY: the observe sites
  `daemon/services/resource_anomaly.py:1252/1255/1258/1269` and the kind map
  `:816-839` (`cgroup.current_kb` is mapped there with NO live observe site —
  a defensive entry so the key can never strand a marker if emitted).
  Every entry states which source fed it; the spec pins the table to EXACTLY
  the enumerated keyspace (the drift alarm if the producer grows a key).

## 3. The unit scale — derived empirically, then NOT applied

**Positioning is time-only.** The overlay draws markers in the top-of-plot
marker lane (`anomaly-overlay-plugin.ts`: `laneY = chartArea.top +
ANOMALY_MARKER_RADIUS`) — the series VALUE never touches a marker's y. No
scale conversion is applied anywhere; `factorToAnchorUnit` is documentation
for the durable daemon-side fix (and any future value-positioned rendering).

The empirical derivation (real payload vs real payload):

| pair | derivation | verdict |
|---|---|---|
| `daemon.rss_kb` → `daemon_rss_mb` | anomaly baseline 303936.0 KB ÷ 1024 = **296.81 MB** vs `/history daemon_rss_mb` **298.66 MB** at t_start (08:00:27) — ratio **1.0062** | clean ×1024 (KB→MB). The peak (337.89 MB ÷1024) exceeds the 30 s-resolution history max (298.87) because the anomaly tier catches sub-sample spikes — disclosed, factor unaffected |
| `daemon.cpu_pct` → `daemon_cpu` | baseline **6.4667 %** vs in-window `/history daemon_cpu` **6.33–6.87 %** | the SAME measure and unit — factor 1 |
| `cgroup.anon_kb` → `cgroup_mem_mb` | the UNIT factor is clean ×1024 (baseline 1576960 KB ÷ 1024 = 1540.0 MB), but the SERIES is the cgroup anon **COMPONENT** while the anchored series is the cgroup **TOTAL** (anon = **22.5 %** of total at the same bucket) | same unit class (memory/MB), DIFFERENT measure — the anchor is the panel join only; recorded in the entry's provenance |

## 4. The C-1 real-pointer leg — LIVE daemon, no stubs

**Driving mode: the LIVE fenced daemon through the real HTTP stack** — real
Chromium → `ng serve :4199` (proxy.conf.json → :8079) → the fenced daemon.
NO `page.route` stubs anywhere; the journal was NON-empty at leg time, so
the order's primary route applies (the HTTP-layer-stub fallback was NOT
needed). Driver: `/tmp/b1-fold-c1-driver.js` (swept post-harvest; the
verbatim output is preserved below and in this file).

```
C1-VERDICT: PASS — 3/3 real engine-keyed markers pointer-drilled (anom-000004, anom-000001, anom-000003)
C1-DRILLDOWNS: [
 {"panel":"memory","engineKey":"daemon.rss_kb","anomalyId":"anom-000004","cluster":{"xCss":62,"yCss":38,"sevs":["high"],"pixels":110},"topmost":"CANVAS[chart-panel-canvas].chart-panel-canvas","drilldownSummaryHas":["series daemon.rss_kb","observed 345996"]},
 {"panel":"memory","engineKey":"cgroup.anon_kb","anomalyId":"anom-000001","cluster":{"xCss":432,...},"topmost":"CANVAS[chart-panel-canvas]...","drilldownSummaryHas":["series cgroup.anon_kb","observed 10359808"]},
 {"panel":"cpu","engineKey":"daemon.cpu_pct","anomalyId":"anom-000003","cluster":{...},"topmost":"CANVAS[chart-panel-canvas]...","drilldownSummaryHas":["series daemon.cpu_pct","observed 10.2833"]}]
receipt: healthy-path canvasCount=2 fallbackBadges=0; real payload engineKeys
["daemon.rss_kb","daemon.cpu_pct","cgroup.anon_kb"], feStyleKeys []
```

Every leg: `page.mouse.move` + `down`/`up` at the marker's RENDERED geometry
(pixel-probed severity-coloured dot, `document.elementFromPoint` naming the
CANVAS before each click — hit-testing is the thing under test), the
drill-down opening, and the summary carrying the API's verbatim words
(`series daemon.rss_kb · observed 345996`, etc.). The
`cgroup.anon_kb` marker is anchored to the memory panel's SECOND dataset —
its successful pointer drill-down is the live proof of the wrapper widening.
The prior leg's gap is closed at the root: the fixture previously used the
FE key for the anomaly (the synthesized shape the gate called out); the
committed fixture, the unit specs and this leg now exercise the REAL engine
shape.

Driver probes only the severities present in the payload: `CHART_SERIES_COLORS[0]`
(`#38bdf8`) is byte-identical to `CHART_SEVERITY_COLORS.low` — a blind probe
matches the first series' LINE (the first probe iteration did exactly that).
Routed to the chart-theme owning lane (council-fold-record §7).

## 5. N5 — the guard now enforces + propagates

`bundle-guard.js` amendments (thresholds untouched, no existing check
weakened):

- **(a) the vendor invariant is enforced**: `vendor_static_edge_count > 0`
  OR `vendor_in_initial_set == true` is RED (the invariant fields are read
  from the measurement tool's JSON — including its exit-4 stdout — and the
  failure names the leak sites).
- **(b) the measure tool's exit status propagates**: any non-zero tool exit
  is a `MEASURE` failure (RED) — the old code swallowed it into
  `measurement_tool = (unavailable)` on a green run. The tool's JSON is
  recovered from stdout even on failure so (a) still applies. The GREEN
  line now reads `RESULT: GREEN (B1 + B2 + B3 + B6 + vendor-invariant +
  measure-propagation)`; the report carries a new `(e)` block.

**Mutation (i) — planted static vendor edge** (a /tmp COPY of the dist; the
route chunk appended `from"chunk-TXUCW6LI.js"`): the guard went **RED,
exit 1, TWO violations** —
`MEASURE the measurement tool exited 4 — a swallowed non-zero measure-tool exit is a false-green seam (N5)` +
`VENDOR-INVARIANT RED — vendor_static_edge_count 1 > 0: chunk-LKN62IVK.js -> chunk-TXUCW6LI.js`
(both mechanisms: the propagated tool exit AND the guard's own invariant,
the leak site named).

**Mutation (ii) — measure tool forced non-zero** (a /tmp copy of the guard
whose `MEASURE_PATH` points at a stub exiting 7; `REPO_ROOT` re-pinned so
B3 scans the real tree and the ONLY delta is the propagation): **RED,
exit 1** —
`MEASURE the measurement tool exited 7 — a swallowed non-zero measure-tool exit is a false-green seam (N5)`.

**Restored green**: the real dist with the amended guard →
`RESULT: GREEN (B1 + B2 + B3 + B6 + vendor-invariant + measure-propagation)`,
exit 0.

## 6. Re-run + disclosure (the full gate at the tip)

| check | result |
|---|---|
| Unit-vocabulary + renderer suites (jest, scoped) | **13 suites / 153 tests / 0 failed** — the scope is the FOUR metrics sub-directories (`charts/`, `resource/timeline/`, `resource/anomalies/`, `resource/live/`), re-derived on 2026-11-04 because the row's original "12 suites / 133 tests" matched NO natural scope (the tester gate measured 12 suites / **149** tests at the fold's tip `cf4e4fd14`; the R1 order spec (+1 suite / 3 tests) and the R2 gap leg (+1 test) take it to 13 / 153). The fold's own additions at the time were 21 compat + 4 timeline-tab + 2 chart-panel + 1 anomalies-panel = 28 tests |
| The committed e2e gate (`resources-gate.spec.ts`, engine-key fixture) | **4/4 passed** (12.9 s) — including the C-1 house-SVG fallback leg now exercising the REAL `daemon.rss_kb` shape |
| The C-1 LIVE leg | **PASS 3/3** (§4) |
| `tsc --noEmit -p tsconfig.app.json` | **rc 0** |
| `tsc --noEmit -p tsconfig.metrics-fe.json` (the pack's scoped gate) | **rc 0** |
| `tsc --noEmit -p tsconfig.spec.json` | 2 errors, BOTH pre-existing at the tip in `src/app/app.component.spec.ts:2127` (a duplicated `});`; the file is byte-identical to the received tip — `git status`/`git diff` empty). Outside this fold's scope; routed (council-fold-record §7) |
| The guard (byte delta DISCLOSED) | B1 initial total **5,907,717 → 5,907,717 (+0; delta −1,301 vs baseline 5,909,018, limit ±2,048) — the fold is lazy-only**; B4a chart chunk **71,054 → 71,054 gz (+0; ≤82,000)**; B4b route fetch set lazy-only **166,055 → 170,094 (+4,039 vs the FIRST GATE's own 166,055 measurement; +4,909 vs the ARTIFACT's own post-relocation baseline of 165,185 — the artifact's number is the baseline of record, and BOTH are under the 185,000 limit, headroom 14,906)**; vendor invariant 0/false ENFORCED → **GREEN exit 0** |
| Prompt baseline | **PROMPT-BASELINE: OK — 161 versioned artifact(s) match** |

## 7. Teardown

SIGTERM the fenced dev daemon (port-verified pid) + the `ng serve` dev
server; remove the scratch dirs (`/tmp/b1-fold-data`, /tmp dist copies);
sweep every /tmp artifact this fold created (boot script, driver, mutation
copies, captures) — the W-1 discipline: no credentials inline anywhere (the
boot script SOURCES the worktree `.env`; the scratch seed was written via
jq from the sourced values, mode 600 in a 700 dir; the daemon ran auth-OFF
so no bearer was minted at all).
