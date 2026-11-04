# FE T1 Metrics Dashboard — Independent Functional Gate (wi-32a99618)

Date: 2026-11-04 · Tester functional gate · Executor: tester lead + 5 worker instances
(`fe-t1-gate-recon` 2e492e89 · `fe-t1-gate-protocol` 0a3b4416 · `fe-t1-gate-baseprep` 4db106ce ·
`fe-t1-gate-pack` 6f11ad25 · `fe-t1-gate-census` dc521087 · `fe-t1-gate-guard` 89a29835)

Lane: branch `feature/fe-metrics-dashboard` @ `2c63a70a1ec8ad10703acf36b7fbdd31b91d89b1`
(base `c4161335b406b884d1de6c8e7bf4b3d1f7fe2abe`, **79 commits** — commission said 55; tip
includes post-phase-9 folds: tidy pass, pack re-point `a6d720191`, FIX-ORDER#2 docs `2c63a70a1`).
Drift vs `origin/latest` = 20 commits (`51d77f019`), ZERO overlap with `frontend/` or the pack;
`.agents/tester/PACKS.md` IS in the drift → keep-both at lane merge.

## VERDICT: **FAIL** — one commission item (C-1 real-pointer) FAILS on a proven defect; all other commissioned items PASS.

| # | Commission verification route | Verdict |
|---|---|---|
| 1 | Fenced dev daemon P1–P9 + three falsification checks (a)/(b)/(c) | **PASS** — all executed green |
| 2 | Scoped token mint, both-inputs proof, live bearer-count fence | **PASS** — live 1/1/1, label-scoped 0 at every observation, zero delta |
| 3 | Pixel items (i)–(iv) | **PASS** — (i) warm PASS (cold-start caveat N3), (ii) PASS, (iii) PASS both legs, (iv) PASS |
| 4 | C-1 real-pointer leg at the tip | **FAIL — BLOCKER B-1** (vocabulary break; canvas marker path dead) |
| 5 | Pack both legs, expected posture, d1 full set, existence-assert mutation | **PASS (expected posture confirmed)** — FAIL(1 leg) = exactly the 2 blob-identical base-debt reds; d1 578/578 over 30/30 files |
| 6 | Whole-FE regression census base↔HEAD | **PASS — net-new deterministic reds = 0** |
| 7 | The guard (B1/B4a/B4b/vendor invariant/warnings) | **PASS** — all thresholds green; invariant mutation exit-4→0 proven |
| 8 | Fences throughout | **HELD** — live untouched (sanctioned SELECTs + /livez only); ensemble_dev never dropped; teardown DELETE 1 |

---

## 1. Boot + falsification (commission route 1) — PASS

P1–P9 executed per plan-overview §6 (lines 421–453), phase9-plan 9.1/9.2/9.3a, at the TIP (the
dev's C-4 closure was code-path-only; this run pays the executed-boot debt):

- Scrub per variable (NEVER `env -u POSTGRES_*`): POSTGRES_{DB,HOST,PASSWORD,PORT,USER},
  PG{PASSWORD,USER,HOST,PORT,DATABASE,SSLMODE,CONNECT_TIMEOUT,APPNAME}, BLUEPRINT_EMBEDDING_API_KEY,
  EMBEDDING/LIGHTRAG/OPENAI/PLANE_MCP/SKILL_EVOLUTION_EMBEDDING *_API_KEY, PORT, ENSEMBLE_SELF_ENV,
  **RAG_IS_REQUIRED** (ambient poison confirmed present pre-scrub: `POSTGRES_DB=ensemble_prod`,
  `POSTGRES_HOST=10.44.0.2`, `PORT=9797`, `ENSEMBLE_SELF_ENV=live`, `RAG_IS_REQUIRED=true`).
- **Falsification (a)** `env | grep POSTGRES` post-scrub → **0 hits**.
- Boot order: scrub → `set -a; . ./.env; set +a` → P2 assert `POSTGRES_DB=ensemble_dev` →
  `PORT=8079` re-exported UNCONDITIONALLY after source → 5 auth vars → `ENSEMBLE_DATA_DIR=/tmp/fe-t1-dev-data`
  (P4 exact pre-seed, jq P3 pin true) → `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pinned →
  `exec /home/nea/ensemble-src/.venv/bin/python -m daemon` (pinned interpreter, `__main__` path).
- Boot result: banner `06:31:06 Starting Ensemble v0.28.9`; `/livez` 200 on **8079** (`ss` → pid 3524703);
  readyz 200 all-components-true, `leak_signal.detected=false`.
- **Falsification (b)** `grep -c "No PostgreSQL ENV vars detected, defaulting to sqlite"` → **0**.
- **Falsification (c)** `$ENSEMBLE_DATA_DIR/ensemble.json` → `database=postgres`,
  `.postgres.db=ensemble_dev`; P3 jq re-run true.
- **P8b** startup-log read-back verbatim: `Loaded ensemble config: database=postgres` +
  `Creating PostgreSQL engine: 127.0.0.1:5432/ensemble_dev`.

## 2. Scoped mint + live fence (route 2) — PASS

- Mint (scrubbed shell, scratch data dir): `/home/nea/ensemble-src/.venv/bin/python -m daemon.auth.cli
  issue-token --label fe-t1-operator --scopes "metrics:read"` → rc 0; token 44 B (first8 `3cWpCN4v`)
  to `/tmp/fe-t1-token.out` 0600, never in a repo file. Resolved-DSN line byte-identical to the dev's
  (`ensemble_dev_app:***@127.0.0.1:5432/ensemble_dev`).
- Both-inputs proof: 0 POSTGRES hits AND scratch-JSON pin true, asserted pre-mint.
- Dev read-back: exactly 1 row `fe-t1-operator | metrics:read | active`.
- **Live fence (W-1 sourced-credential shape — creds from install `.env` in a subshell, never inlined):**

| observation | ensemble_prod total | label-scoped | /livez 9797 | current | state.json sha256 |
|---|---|---|---|---|---|
| BEFORE (06:30) | 1 (rc0) | 0 (rc0) | 200 · uptime 53137.3 | releases/v0.28.9 | e7c5db34…0dd98 |
| AFTER-MINT | 1 (rc0) | 0 (rc0) | 200 · 53222.1 | same | same |
| TEARDOWN (07:01) | 1 (rc0) | 0 (rc0) | 200 · 55055.0 monotone | same | same |

- Teardown: SIGTERM both daemons (port-verified PIDs 3524703 / 3534521); dev DB
  `DELETE FROM auth_bearer_tokens WHERE label='fe-t1-operator'` → **DELETE 1**;
  `current_database()=ensemble_dev`; table intact; **ensemble_dev never dropped**.

## 3. Pixel verification (route 3) — PASS on all four items

Executed with the committed drivers unmodified (`capture-9.7a.js` auth-ON, `capture-9.7b.js` auth-OFF;
`timeout 420`; Playwright from `frontend/node_modules`; bearer via `extraHTTPHeaders`; token re-minted
to `/tmp/fe-t1-token.out` which the dev's sweep had deleted).

**(i) 7 tabs WITH DATA** — warm run **16/16 PASS** (`ITEMS: 16/16 PASS`, exit 0):
PX-1 tab1 resource `live_strip=1 timeline=1 anomalies_panel=1 anomaly_rows≈2 disclosure_text=true`;
PX-2 runtime panels=3 chips=3; PX-3 db-pool panels=2; PX-4 jobs panels=2 strip=1; PX-5 instances
panels=6 chips=2; PX-6 checkpoint panel=1 chip=1; PX-7 mcp strip=1 chip=1 (stat-primary tab).
Resources door 6 endpoints (bearer): `/live` 200 (rss_mb 301.03), `/self-cost` 200,
`/history?window=15m` 200 (3 cold → 30 warm points), `/anomalies?window=15m&limit=10` 200 (0 cold → 2 warm),
`/anomalies/anom-000002/evidence` 200 (real id, warm), `/traceback` 200 **with ISO-8601 t0/t1**
(epoch-seconds shape → 422, see N4). L1 thresholds: 34 registered families, 12/12 tab-bound present,
F9 snapshot 13 series, rss non-zero. API census both pixel runs: non-GET **0**, `:9797` hits **0**.
**(ii) AUTH_NOT_CONFIGURED empty-state** — 503 body byte-identical to dev's; PX-8@1600/@900 PASS
(cause_panels=1, runbook pointer visible, retry_buttons=0, chip not green, no 900px collision);
L2/L4-tab1-lit@1600/@900 PASS — **AM-1 CONFIRMED: tab 1 stays lit under the 503 central door.**
**(iii) /resources redirect BOTH legs** — PX-9 `/resources?window=24h&x=1` → `/metrics?window=24h&x=1&tab=resource`;
leg 2 `/resources?tab=charts` → `/metrics?tab=resource`; `?tab=charts&window=24h&x=1` → all params preserved.
**(iv) mixed-unit y-axis** — PX-10/L3: three unit-class panels (memory/cpu/count), own canvas 1/1/1
(no shared axis); default-on = memory+cpu (the two labelled panels), count via `series-toggle-threads`.
L1b: 401 AUTH_REQUIRED + `www-authenticate: Bearer realm="ensemble"`; rendered sign-in panel, 0 Retry.

**Cold-start caveat (N3):** the FIRST 9.7a run (daemon ~4 min old) failed PX-1 `anomaly_rows≈0`
(anomaly engine first pass not yet fired; `anomaly_interval_s=600`; replay logged 06:41:18). Warm re-run
(daemon ~19 min) 16/16. **Gate-protocol amendment adopted: warm the daemon ≥ one anomaly interval
before pixel runs** (the dev's run was 21-min warm — the trap was latent).

## 4. C-1 real-pointer leg (route 4) — **FAIL (BLOCKER B-1)**

- `[data-test="house-anomaly-marker"]` = 0, `panel-fallback-badge` = 0, canvases = 2 → **the healthy
  Chart.js path was active, not the house-SVG fallback** (the house SVG is the mount-FAILURE fallback —
  commission premise N2). The leg was re-derived against the real DOM contract.
- Real pointer (`page.mouse` at rendered geometry + `document.elementFromPoint`): target =
  `CANVAS[data-test="chart-panel-canvas"]` under `DIV[data-test="timeline-chart"]`;
  **148 real move+click probes → 0 drilldown openings.**
- Row-click control: `TR[data-test="anomaly-row-anom-000002"]` click → drilldown OPENS
  (`kind memory · shape ramp · severity medium · series daemon.rss_kb · observed 345996 · baseline 306028`).
- **Root cause (proven in-page, not inferred):** `/api/resources/history` series keys
  `["daemon_cpu","daemon_rss_mb","cgroup_mem_mb","threads","instances_active"]` vs
  `/api/resources/anomalies` `series_key` `["daemon.rss_kb","cgroup.anon_kb"]` → **intersection `[]`**
  (daemon synthesizes FE-facing names at `daemon/routers/resources.py:675` but passes engine-internal
  keys verbatim at `:820`). FE `unitClassOf(anomaly.series_key)` resolves against the history vocabulary
  (`TIMELINE_SERIES_DEFS`) → null → "the marker joins NO panel" (`timeline-tab.component.ts:206-213`)
  → the Chart.js anomaly overlay receives an empty marker list → **no marker is ever drawn and the
  chart→drilldown click path is dead. The lane-delivered timeline-marker feature is dead-on-arrival.**
  Drill-down remains reachable ONLY via the anomaly list row. Fix ownership: cross-layer —
  daemon-side key normalization OR FE-side mapping; NOT fixable inside verification (FIX NOTHING held).

## 5. Pack (route 5) — expected posture CONFIRMED

Registered invocation (PACKS.md:214 amended form; dual-side: one invocation per side):

```bash
EXPECTED_BRANCH=feature/fe-metrics-dashboard BASE_REF=c4161335b… METRICS_FE_LOG_DIR=/tmp/fe-t1-gate/head \
  timeout 900 bash test/packs/metrics_fe_dual_side_test.sh          # HEAD leg
EXPECTED_BRANCH=… BASE_REF=… ALLOW_DETACHED=1 \
  METRICS_FE_PROJECT_DIR=/home/nea/ensemble-src-wt-fe-gate-base METRICS_FE_LOG_DIR=/tmp/fe-t1-gate/base \
  timeout 900 bash test/packs/metrics_fe_dual_side_test.sh          # BASE leg
```

- HEAD: `RESULT: FAIL (failed legs: 1)`, PACK_EXIT=1. d1 **578/578 over 30/30 declared files** (the
  commission's 578 = current composition; PACKS.md's 174/11 and the dev's 224 are stale earlier
  compositions — registration corrected below). d2_declared 131/131. d2_derived
  `2 failed, 1832 passed, 1834 total` (39 files) — the ONLY reds are the settings timezone pair.
- BASE: same RESULT shape; d2_derived `2 failed, 1799 passed, 1801 total` — **the same two tests**,
  same names, same lines (1506/1741), same `UTC-05:00 vs UTC-04:00` cause; failing-NAME diff empty
  both directions. typecheck/build legs SKIPPED-BY-CONSTRUCTION at base (correct).
- Blob-identity (census sha-normalized): both settings failureMessages hash-identical base↔HEAD
  (`458e4f72…99d5`, `14f0198e…38c`) — **byte-identical, base-proven, zero lane attribution.**
- Leg-7 dependency set `ok: true` by name (`chart.js` / `jest-canvas-mock` exactly).
- Mutation proof: `app.routes.spec.ts` moved aside → pack failed LOUD naming the file, BEFORE the
  d2_declared jest leg (`MISSING DECLARED SPEC src/app/app.routes.spec.ts`, RESULT: FAIL (failed legs: 2),
  elapsed 143 s) → restored byte-exact (sha256 `46cae19b…e17a5a` match) → porcelain empty both trees.
  **The silent-drop guard is alive.**

## 6. Whole-FE census (route 6) — PASS, net-new deterministic reds = 0

Identical invocation both sides (`jest --ci --silent --runInBand --cacheDirectory=/tmp/fe-t1-gate/jest-cache
--json`, jest.config.js proven identical):

| side | suites | tests | exit |
|---|---|---|---|
| BASE c4161335b | 4F/130P = 134 | 9F/4309P = 4318 | 1 |
| HEAD 2c63a70a1 | 4F/148P = 152* | 9F/4685P = 4694* | 1 |
| HEAD2 (determinism) | 4F/149P = 153 | 9F/4702P = 4711 | 1 |

*The 152/4694 HEAD count is a **cross-wave artifact, not a jest anomaly**: the pack worker's mutation
plant (app.routes.spec.ts aside, 07:11:00Z — also observed by the guard worker as a transient deletion)
overlapped the census HEAD leg. HEAD2 (file restored) reports the full 153/4711 with an IDENTICAL
failed set. Failed-set diff: BASE-only **0** · HEAD-only **0** · both **9** — exact reconciliation with
the phase-1 expected set (settings ×2, schedule-create-dialog ×3, jobs-grouping ×3, jobs-filter-state ×1).
All 9 are path-normalized **blob-identical** across every leg; the four base-debt spec files are
byte-identical base↔HEAD. The jobs-queue-indicator flake never fired (passed on all legs). November ⇒
`America/New_York` standard time ⇒ `UTC-05:00` vs specs' hard-coded summer `UTC-04:00` — the known
date-dependent base debt, zero lane attribution.

## 7. The guard (route 7) — ALL PASS

Isolated build (`--output-path /tmp/fe-t1-gate-dist`, default dist untouched, rc 0/30 s, porcelain 0):

| guard | measured | threshold (artifact-quoted) | verdict |
|---|---|---|---|
| B1 initial total | 5,907,717 B (Δ **−1,301**) | \|Δ\| ≤ 2,048 vs 5,909,018 | PASS |
| B2 chart.js in initial | 0 hits / 36 chunks | 0 | PASS |
| B3 import shape/site | 0 static statements; sole site `chart-panel.component.ts` dynamic ×2; all under `src/app/pages/metrics/**` | 0 static | PASS |
| B4a chart chunk | `chunk-TXUCW6LI.js` 206,857 min / **71,054 gz** (independent `gzip -9` byte-equal to artifact) | ≤ 82,000 gz | PASS (headroom 10,946) |
| B4b route fetch set | total 683,269 · **lazy-only 166,055** | ≤ 185,000 lazy-only | PASS (headroom 18,945) |
| B6 warning set | 9 == baseline 9, added 0 / removed 0 (set-diff, no count ceiling) | set-equality | PASS |
| vendor invariant (clean) | `vendor_static_edge_count: 0`, `vendor_in_initial_set: false` | 0/false | PASS |
| guard run | `RESULT: GREEN (B1+B2+B3+B6)` exit 0 | — | PASS |

Route-field drift vs phase-9 records: +870 B across route chunk/fetch-set (120,154→121,024;
165,185→166,055; 682,399→683,269) — inside margins; the commission's 166,055 figure matches this
fresh HEAD measurement. Pre-retirement `/resources` chunk correctly null.

**Vendor-invariant mutation (once, /tmp copies only):** dist-level static-edge plant → measure
stderr verbatim `VENDOR-INVARIANT RED — vendor chunk(s) [chunk-OLWODC2S.js] carry 1 static-import
edge(s)` → **exit 4**; restore → `vendor_invariant_ok: true` → **exit 0**. **Caveat (load-bearing
note N5):** SOURCE-level static `import 'chart.js'` triggers esbuild facade re-splitting
(both same-file and sibling plants tested) which evades the STRUCTURAL invariant — but every planted
shape was caught by the numeric layers (B3 static-scan exit 1; B4b 166,055→372,485; B1 +206,077).
Defense-in-depth held; artifact amendment recommended (owner: lane).

## 8. Fences

Live out-of-bounds held: only the sanctioned SELECTs (8/8 rc=0 numeric), /livez reads, readlink +
state.json sha (byte-identical pre/mid/post), uptime monotone (no restart). ensemble_dev never
dropped; only the minted row deleted. Both pixel censuses: non-GET 0, `:9797` 0. Worktrees porcelain
clean at every checkpoint. `/home/nea/ensemble-src` never modified.

## Findings register (classified)

| id | severity | finding | owner / routing |
|---|---|---|---|
| **B-1** | **BLOCKER** | C-1 real-pointer dead: anomaly `series_key` ↔ history vocabulary intersection `[]` → markers join no panel → chart→drilldown path dead (148 clicks, 0 openings); row-click path only. Lane-delivered marker feature dead-on-arrival. | **Leader decision: daemon-side key normalization (`resources.py:820`) vs FE-side mapping** — cross-layer; not fixable in-gate |
| N1 | note | Commission said 55 commits over base; actual **79** (post-phase-9 folds). Drift 20 (not +6), still zero overlap with frontend/+pack; PACKS.md in drift → keep-both merge | disclosure |
| N2 | note | Commission premise: house-SVG marker hooks — architecturally the house SVG is the mount-FAILURE fallback; healthy path = Chart.js canvas. Real-pointer leg re-derived to the real DOM contract | disclosure |
| N3 | note | Cold-start false-RED on PX-1 (anomaly pass unfired < `anomaly_interval_s=600`) — **protocol amendment adopted: warm ≥600 s before pixel runs** | gate protocol |
| N4 | note | `/api/resources/traceback` requires ISO-8601 t0/t1 (epoch → 422) — protocol amendment adopted | gate protocol |
| N5 | note (load-bearing) | Vendor-invariant facade evasion on source-level static imports (esbuild re-split); numeric layers caught all shapes; `bundle-guard.js` also does not enforce the invariant and swallows the measure tool's non-zero exit (its `measurement_tool=(unavailable)` path) | lane artifact amendment |
| N6 | note | Pack `${leg}_ERROR_SUMMARY` grep shape (`^(FAILED|ERROR) `) never matches jest's `FAIL` summary — observability only | pack owner |
| N7 | note | PACKS.md:214 census rows stale (D1 174/11 → **578/30**; D2-declared 138 → **131**) — corrected in this gate's registration row (below) | tester (done) |
| N8 | note | Cross-wave interference (explained, benign): pack mutation plant (app.routes.spec.ts, 07:11Z) overlapped census HEAD leg (152 vs 153 suites) + guard's transient-deletion observation. HEAD2 + final porcelain prove zero residue. **Protocol: serialize mutation runs against concurrent census/guard legs** | gate protocol |
| N9 | note | jest failed-set diffs MUST key on worktree-relative paths (absolute-path keying produces an absurd 9/9/0 split) — recorded; skill_fix raised | tester tooling |
| N10 | note | Census environment asymmetry: `setup-jest.ts` differs by lane (canvas mock + ResizeObserver stub at HEAD) — legitimate lane content, disclosed not equivalence-claimed | disclosure |
| N11 | note | Boot banner logged twice (`__main__` + api import); RAG/plane/:9798/OAuth-discovery warnings expected under scrub+placeholder posture; readyz all-true | pre-existing |
| N12 | note | Driver PX-13 locator union matches the table header row before data rows (`drilldown=false` in its verdict) — real row click opens; driver-side nit | pixels driver |

## Pixel capture inventory (this gate's run — /tmp, swept post-harvest)

`/tmp/fe-t1-pixels/` 27 files / 2,663,829 B: px01–px07 (7 tabs), px08-{1600,900}x1000 +
l2l4-tab1-still-lit-{1600,900} (auth-OFF), px09 + redirect-charts{,-B} (both redirect legs),
px10-mixed-units, px11a{,-on}/px11b, px12a/b, px13, px14, l1b-signin-panel, c1-pointer-drilldown
(row-click control), capture-9.7a/9.7b evidence JSONs. The dev's committed 25-file set remains the
durable in-repo evidence (`.agents/shared/planning/metrics-unification/fe-t1-build/pixels/`).

## Reproduction anchors

Boot/mint/teardown scripts: `/tmp/fe-t1-gate/boot-auth-{on,off}.sh` (swept; shapes quoted §1–§2).
Pack: §5 commands. Census: §6 command. Guard: §7 + `/tmp/fe-t1-gate/{guard-build.log,measure.json}`.
Base worktree `/home/ensemble-src-wt-fe-gate-base` removed post-gate (`git worktree remove`).

## Overall

- Commission items: 7/8 PASS; item 4 (C-1) **FAIL — blocker B-1**.
- **Verdict: FAIL** — the lane is not ceremony-ready until B-1 is adjudicated (the marker feature is
  a headline deliverable of the timeline work and it cannot function against the real daemon payload).
- All fences held; live untouched; no fixes applied (FIX NOTHING held); evidence committed on-branch.


---

# SCOPED RE-VERIFICATION (2026-11-04, tip `cf4e4fd14`) — supersedes the first-gate verdict

Fold under test: `52e7a9dfb` (B-1 compat shim) · `0798d6f3e` (real-shape specs + e2e fixture) ·
`33abc3ae6` (N5 guard enforcement) · `cf4e4fd14` (docs), on top of `ff064658c` (= this gate's own
first-gate evidence commit; `2c63a70a1` is its parent — 5 commits, linear, no merges). Only the
routes the fix touched were re-verified; routes 1/2/3/5/6/7/8 evidence from the first gate stands at
`2c63a70a1`. The developer's claims were treated as INPUT, not evidence; every worker drove its own
runs. Four independent workers: route-4 (`058faa90`, e2e-test) · guard (`2719d705`) · suites+fence
(`1cb4bd35`) · pack (`563c79f6`, test-pack-execution).

## LANE VERDICT: **PASS-WITH-NOTES** — ceremony-ready; the first-gate FAIL on B-1 is superseded.

| re-verified route | verdict | one-line basis |
|---|---|---|
| Route 4 (C-1 real pointer) | **PASS 3/3** | all three engine keys re-proven live, independently, with real pointer clicks |
| Guard at tip (B1/B4a/B4b/warnings + the 2 new enforcement legs) | **PASS** | numbers re-measured; one mutation re-proved RED→GREEN; the old false-green seam proven real |
| Suites + fence | **PASS** | 151/151 reconciled; fence = 8 `frontend/` + 6 `.agents/`, **zero `daemon/`**; baseline 161/161 |
| Pack at tip (dual-side) | **PASS (expected posture)** | FAIL(1 leg) = the same 2 settings TZ reds; d1 585/585 over 30 files; both-revs rule honored |

## Route 4 re-proof — PASS 3/3 (worker `058faa90`)

**Fix read first** (`52e7a9dfb`): `timeline-series.ts` = typed `ENGINE_SERIES_COMPAT` (7 exact engine
keys + an instance pattern) with `anchorKey` / `unitClass` / `engineUnit` / `factorToAnchorUnit`
(documented, never consumed) + `resolveTimelineSeriesJoin()` (FE pass-through → engine table →
instance pattern → **null**, fail-closed) + `markerAnchorKey()` (enabled-aware anchor).
`daemon.rss_kb`→`daemon_rss_mb` (memory, dataset 0) · `cgroup.anon_kb`→`cgroup_mem_mb` (memory,
**dataset 1**) · `daemon.cpu_pct`→`daemon_cpu` (cpu, dataset 0). The two dead seams:
**DRAW** = `anomaly-overlay-plugin.ts::beforeDatasetsDraw`→`houseDatasetIndex` (marker.seriesId now
the FE anchor id, not the raw engine key — pre-fold it matched no dataset ⇒ nothing drawn);
**CLICK** = `chart-panel.component.ts::onChartClick` (chart is `interaction:{mode:'index',
intersect:false}` ⇒ `elements` carries one entry PER DATASET; pre-fold it read `elements[0]` only ⇒
a dataset-1 anchor was unreachable; post-fold it scans every element and the first marker-bearing
one wins, else the first element's plain payload).

**Boot fences (verbatim, boot script's own stdout capture — NOT a re-boot):**
```
BOOT_TS=2026-11-04T08:57:48Z
FENCE1 postgres_hits_after_scrub=0
FENCE1b ambient PORT=<unset> ENSEMBLE_SELF_ENV=<unset> RAG_IS_REQUIRED=<unset>
FENCE2 sourced POSTGRES_DB=ensemble_dev POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5432
FENCE3 ensemble_json_pin=true
BOOT-EXEC port=8079 datadir=/tmp/fe-t1-rere-dev-data sweep=0
```
Boot-time daemon captures (08:57–08:58Z): banner `Starting Ensemble v0.28.9` (×2) ·
`Creating PostgreSQL engine: 127.0.0.1:5432/ensemble_dev` · `No PostgreSQL ENV vars detected,
defaulting to sqlite` → **count 0** · `maintenance boot sweep: 0 interrupted run(s)`.
Live re-capture (09:10Z, no restart): `/livez` uptime 746.15 v0.28.9 · `/readyz` all components true,
reasons [] · `ss` → :8079 owned by pid 3574404 · jq ensemble.json pin true · `/proc/3574404/environ`
→ `POSTGRES_DB=ensemble_dev`, `PORT=8079`, `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0`,
`ENSEMBLE_DATA_DIR=/tmp/fe-t1-rere-dev-data`, no `OPENAI_API_KEY`/`PLANE_MCP_API_KEY`/`RAG_IS_REQUIRED`.
**Prod fence:** zero contact with :9797 / ensemble_prod / the live daemon (no mint — auth-OFF
posture; no SELECTs). **GET-only census: 0 non-GET**; the only anomalies were the house fail-closed
`503 AUTH_NOT_CONFIGURED` on `/api/metrics/*` and one CSP-blocked external iframe (nothing sent).

**Live anomaly coverage (real engine, real payload; warm-up honoured — first read at t+10 min,
`anomaly_interval_s=600`, `anomaly_min_baseline_points=12`):**

| engine key | anomaly | sev/shape | peak / baseline | first seen | induced by |
|---|---|---|---|---|---|
| `daemon.rss_kb` | anom-000003 | high/ramp | 345996.0 / 304172.0 KB | 09:08:22Z | boot churn |
| `cgroup.anon_kb` | anom-000001 | high/ramp | 10359808.0 / 1585664.0 KB | 09:08:22Z | boot churn |
| `daemon.cpu_pct` | anom-000007 | high/spike | 58.2 % / 6.2417 % | 09:39:17Z | burst 2+3 (GET-only) |
| `daemon.rss_kb` | anom-000012 | low/leak | 317932 / 307952 KB | 09:39:17Z | leak-shaped drift |

Bursts (GET-only, localhost, PID-killed): burst 2 (09:15–09:17Z, 8×3 endpoints) → daemon CPU 6 %→46–48 %
(**not** enough to clear the robust-z bar); burst 3 (09:28–09:32Z, 24×4 endpoints) → CPU ~116 % → the
CPU key emitted by the 09:38 pass. Coverage window 41.5 min from boot.

**Per-key pointer verdicts (real `page.mouse`, real payload).** Geometry method (no DOM marker
exists — markers are canvas arcs): rendered bitmap read via `getImageData`, marker discs located by
EXACT severity-palette colour (`low #38bdf8`, `medium #f59e0b`, `high #ef4444`), classified by shape
(filled disc ≈10–12 px vs 1–3 px series lines), confined to the marker lane (rows 28–48 of 150).

| key | marker | elementFromPoint chain | click (viewport) | clicks→open | drilldown `series` |
|---|---|---|---|---|---|
| `daemon.rss_kb` (low) | memory, disc x=505.24 y=37.5 | canvas[chart-panel-canvas] > div[chart-panel] > app-metrics-chart-panel > div[timeline-chart] > div[timeline-panel-memory] > … | (533.37, 339.5) | **1** | `daemon.rss_kb` (observed 317932) |
| `daemon.cpu_pct` (high) | cpu, disc x=412.54 y=37.5 | … > div[timeline-panel-cpu] > … | (440.64, 546.41) | **1** | `daemon.cpu_pct` (observed 58.2) |
| `cgroup.anon_kb` (high) | memory **dataset 1**, disc x=89.24 y=37.5 | … > div[timeline-panel-memory] > … | (117.26, 887.5) | **1** | `cgroup.anon_kb` (observed 10359808) |

Every drilldown's series field equals the WIRE engine key (never an FE-style rename).

**Seam confirmations.** (i) OVERLAY DRAW — pixel census of the marker lane: memory 89 severity px in
a 10–11 px band vs **0 px** in the adjacent marker-free band (x511–1188); cpu 89 px vs 0 px
(x534–1188) ⇒ markers are genuinely drawn at the probe coordinates. (ii) WRAPPER CLICK — proven by
the 3/3 first-click drilldowns; code anchors `onChartClick`→`houseNearestAnomaly`→
`onPanelPointClick`→`openDrilldown` match live behaviour.
**Second-dataset / widening case exercised:** `cgroup.anon_kb` anchors to the memory panel's SECOND
dataset — pre-fold (elements[0]-only) this click would have yielded a plain payload with no
drilldown; post-fold it opens on the FIRST click. Strongest single evidence for the widening.

**Teardown:** SIGTERM (port-verified pid 3574404) → exited 1 s → `/livez` refused → :8079 empty → no
inducer orphans; swept its own `/tmp/fe-t1-rere4/` + `/tmp/fe-t1-rere-dev-data/`; live :9797 untouched
(pid 3547471). Worktree porcelain 0, HEAD `cf4e4fd14`.

## Guard at tip — PASS (worker `2719d705`)

`33abc3ae6` = `bundle-guard.js` only (+98/−5); thresholds unchanged. New legs: the vendor invariant is
now ENFORCED from the measure tool's JSON (`vendor_static_edge_count > 0` → RED naming the leak
sites; `vendor_in_initial_set === true` → RED; fail-closed when the tool never answered) and the
measure tool's exit status PROPAGATES (the old `catch { measureTotal = null }` → `measurement_tool =
(unavailable)` on a green run — the false-green seam). GREEN banner now
`GREEN (B1 + B2 + B3 + B6 + vendor-invariant + measure-propagation)`.

| guard | measured | threshold | verdict |
|---|---|---|---|
| B1 initial total | 5,907,717 B (Δ **−1,301**) | ±2,048 vs 5,909,018 | PASS |
| B2 | 0 hits / 36 chunks | 0 | PASS |
| B3 | 1 site / **0 static** / 0 outside metrics | 0 static | PASS |
| B4a | `chunk-TXUCW6LI.js` 206,857 min / **71,054 gz** (independent gzip-9 equal) | ≤82,000 | PASS (headroom 10,946) |
| B4b | route chunk 125,063 · fetch set 687,308 · **lazy-only 170,094** | ≤185,000 | PASS (headroom 14,906) |
| B6 warnings | 9 == baseline 9, 0 added / 0 removed | set-diff | PASS |
| vendor invariant | 0 / false / tool rc 0 | — | CLEAN |
| guard exit | GREEN (both new legs present) | — | **0** |

**Mutation re-proof (independent):** /tmp copy of the dist + tooling (5-level depth; a REAL
`frontend/src` copy — symlinks are not traversed by the B3 walk, else the guard reports VACUOUS):
append a static import of the route's dynamic target to an initial chunk ⇒ measure tool **rc 4**
(`TOOL: VENDOR-INVARIANT RED — vendor chunk(s) [chunk-TXUCW6LI.js] carry 1 static-import edge(s)`)
and the guard copy **rc 1 with 4 named violations** (B1 delta + `MEASURE the measurement tool exited
4 …` + both VENDOR-INVARIANT RED lines) ⇒ restore ⇒ **exit 0 GREEN**. Supplementary legs prove the
OLD seam was real: a lazy-chunk plant made the OLD guard exit **0 GREEN** with
`measurement_tool = (unavailable)` on a dist carrying a genuine static vendor edge, while the NEW
guard exits **1 RED** naming both legs.

## Suites + fence — PASS (worker `1cb4bd35`)

| suite | kind | nodes |
|---|---|---|
| `timeline-series-compat.spec.ts` (NEW) | vocabulary/shim | 21 ✅ |
| `timeline-tab.component.spec.ts` | timeline | 26 ✅ |
| `chart-panel.component.spec.ts` | renderer | 25 ✅ |
| `anomalies-panel.component.spec.ts` | anomalies | 8 ✅ |
| `house-svg-renderer.spec.ts` | renderer | 71 ✅ |

**151 = 80 (the 4 changed/added) + 71 (renderer) — the claim reconciles exactly; 151/151 PASS.**
`frontend/e2e/resources-gate.spec.ts` is the **corrected FIXTURE** (Playwright; jest ignores
`<rootDir>/e2e/`) carrying the real engine shape (`series_key:'daemon.rss_kb'`, KB-scale
peak/baseline) — it is NOT a jest suite and its 4/4 was not re-run here (disclosed). Whole-tree
census: 154 suites / 4739 tests, the same **9 base-proven reds** (4 suites, blob-identical base↔HEAD,
zero lane attribution). **Fence:** `git diff --name-only 2c63a70a1..cf4e4fd14` = 14 files = **8
`frontend/` + 6 `.agents/`** — `daemon/` 0, `scripts/` 0, `tests/` 0, `test/` 0, `agents/` 0,
migrations 0; 5 commits, linear; `PROMPT-BASELINE: OK — 161 versioned artifact(s) match` (rc 0),
`agents/` diff empty.

## Pack at tip — expected posture CONFIRMED (worker `563c79f6`)

Base worktree re-created (`git worktree add --detach … c4161335b`, `npm ci` rc 0/29 s; `chart.js` +
`jest-canvas-mock` ABSENT at base) and removed after (`git worktree remove --force`).

- HEAD leg: `RESULT: FAIL (failed legs: 1)` exit 1 — d1 **585/585 over 30 declared files** (first gate
  578: +7 from the fold's three MODIFIED D1 members) · d2_declared 131/131 · d2_derived
  `2 failed, 1832 passed, 1834 total` (39 files) · typecheck_scoped/dev build/prod build exit 0.
- BASE leg: `RESULT: FAIL (failed legs: 1)` exit 1 — d1 SKIPPED-BY-CONSTRUCTION · d2_declared 131/131 ·
  d2_derived `2 failed, 1799 passed, 1801 total` (38 files).
- The single failing leg carries the **same two** reds on both sides:
  `src/app/pages/settings/settings.component.spec.ts` lines **1506** / **1741**, DST cause
  (`Expected "America/New_York (UTC-04:00)" / Received "… (UTC-05:00)"`), file untouched by the fold.
  **No new failing legs, no new reds.**
- **Membership rule HONORED:** the fold's ONE new spec (`timeline-series-compat.spec.ts`) is in
  **none** of D1_FILES / D2_DECLARED / the d2_derived grep; every D2_DECLARED entry exists at BOTH
  revs (base-verified + asserted by the pack on both legs) ⇒ no HEAD-only file sits in a both-revs
  set. The pack script itself was NOT modified by the fold.

## Residual notes (non-blocking) — with owners

| id | note | owner |
|---|---|---|
| **R1** | **Latent race, code-reading only:** the wrapper's rebuild effect assigns `chart.data` but NEVER `chart.options` ⇒ the overlay plugin's marker list is frozen at chart-mount time. If the chart mounts before `/api/resources/anomalies` resolves, markers are silently absent — the first gate's symptom shape. Not reproduced (charts mounted after the anomalies here). | lane (chart-panel rebuild effect; assign `chart.options.plugins[HOUSE_ANOMALY_PLUGIN_ID].markers` too) |
| **R2** | One leading-edge instance (`anom-000003`, `daemon.rss_kb`, 08:55→09:05) rendered a marker but **25 real clicks at/around its exact centre opened no drilldown**, while a neighbouring marker 25 px away opened fine and the same key is click-through on a later instance. Mechanism not isolated (production build, no chart introspection). The vocabulary/shim join is proven; this instance's click path is not. | lane (follow-up leg) |
| **R3** | Count-class engine keys (`daemon.fds`, `daemon.threads`) resolve to a unit class with **no enabled series** ⇒ no panel is rendered ⇒ no marker can exist. Scope fact, not a shim defect. | metrics lane (series enablement) |
| **R4** | The fold's new compat spec is in NO declared set ⇒ the gate pack will not catch a future regression in it (covered today only by the scoped suite run). | lane/pack owner (add to `D1_FILES`) |
| **R5** | `${leg}_ERROR_SUMMARY` greps `^(FAILED\|ERROR) ` — jest prints `FAIL <path>` ⇒ the summary reads `(none)` on a failing leg (verdict unaffected: exit code + `Tests:` counts are correct). | pack owner (one-line grep) |
| **R6** | Fold-evidence count drift: its "12 suites / 133 tests" scope measures **149** at the tip (133 matches no natural scope). Documentation only. | lane (re-derive the row) |
| **R7** | B4b arithmetic: the brief's "+4,039" uses the first-gate 166,055; the artifact's own prior baseline is 165,185 ⇒ measured Δ-vs-artifact **+4,909** (both under 185,000). | lane (artifact/evidence alignment) |
| **R8** | The amended guard discards the measure tool's stderr ⇒ the operator-facing reason line lacks the tool's own `TOOL: VENDOR-INVARIANT RED — …` tail (the invariant is still enforced from stdout JSON). | lane tooling |
| **R9** | Route-4 chart-theme note (dev): `CHART_SERIES_COLORS[0]` is byte-identical to `CHART_SEVERITY_COLORS.low` ⇒ a colour-based marker probe can confuse a low-severity marker with the series-0 line (it bit the independent prober twice). | chart-theme owning lane |

## Tester-process disclosure (mine, not the lane's)

**Orchestration error:** I gave two parallel workers the same `/tmp/fe-t1-rere` prefix; the pack
worker's sweep (~09:02Z) deleted the route-4 worker's `boot.sh`, `daemon.log` and `induce.sh`
(collateral), and the suites worker's census artifacts (it re-ran them). Consequences handled without
falsification: the route-4 daemon was **not** restarted (warm-up + detected anomalies preserved), its
boot-fence evidence was recovered from the boot script's own stdout capture plus a live re-capture
(banner/engine-line/sqlite-line were captured at 08:57–08:58Z before the deletion), and inducer burst
1 never ran (recorded). Lesson: **unique /tmp prefixes per parallel worker** — added to
`LESSONS/2026-11-04-fe-t1-gate-protocol-lessons.md`.

## Ceremony readiness

**PASS-WITH-NOTES.** B-1 is closed as re-verified (both dead seams now resolve engine keys against
the real payload; the second-dataset widening case proven on the first click); N5 is closed (both
enforcement legs live; the old false-green seam proven real and now closed); the guard, suites,
fence and pack posture all re-verified green. Residual items R1–R9 are non-blocking with owners —
**R1 (frozen plugin options) is the one to route first**, since it is the only residual that could
reproduce the original symptom shape under a different arrival order.


---

# SECOND SCOPED RE-VERIFICATION (2026-11-04, tip `1af481a78`) — R1/R2 fold; FINAL LANE VERDICT

Fold under test: `8c344a6da` (R1 `chart.options` refresh on rebuild + R2 clicked-sample local
tolerance + pack D1 expansion + specs) · `1af481a78` (live before/after evidence), on top of
`7c36835c0` (this gate's re-verification #1 evidence). Six workers, each with a UNIQUE /tmp prefix
and its own worktree/port where it touched the FE (the prior round's shared-prefix lesson applied;
the pack ran in its own tip worktree because the lane worktree's `frontend/dist` was being served
live by the R1 leg). Every developer claim was treated as INPUT; all legs re-run independently.

## FINAL LANE VERDICT: **PASS-WITH-NOTES** — ceremony-ready.

| leg | verdict | one-line basis |
|---|---|---|
| R1 arrival-order | **PASS** | pre-fix shape ⇒ 0 marker arcs / no disc; tip ⇒ 2 marker-lane arcs + visible disc when the chart mounts BEFORE the anomalies payload; source-level served-bytes patch reproduces the freeze end-to-end |
| R2 geometry/tolerance | **PASS** | hole pin RED@pre-fix-copy → GREEN@tip; OWN hole (1197 s row gap) ⇒ marker 73.0 s from nearest sample vs 38.1 s mean ⇒ drills on the FIRST click; both on-grid controls unchanged (1 click each) |
| Guard at tip | **PASS** | B1 −1,301 · B4a 71,054 gz · B4b lazy-only 170,177 (≤185,000) · warnings 9==9 · invariant clean · **R8 closed** (tool stderr surfaced in RED, both mutation legs) |
| Pack at tip | **PASS (expected posture)** | both legs FAIL(1 leg) = the same 2 settings TZ reds byte-identical; **D1 = 32 files / 610 tests** (**R4 closed**); error-summary now names the failing suite (**R5 closed**); membership rule honored |
| Suites + census | **PASS** | **153/153** (13-suite scope; the correct arithmetic is 149+4, not 151+2) · whole-FE BASE 134/4318 · HEAD 155/4743 · failed diff base-only 0 / head-only 0 / shared 9 (all blob-identical) · **net-new deterministic reds = 0** |
| Fence + live baseline | **PASS** | live moved v0.28.9→**v0.28.10** by ANOTHER lane's ceremony (run_id `r-20261104-073151-e436`, committed 07:35:57Z); our lane made no live change (journal + git-diff proof); one /livez request total |

## R1 — PASS (worker `b847074c`)

Fix read: the rebuild effect now does `chart.data = config.data; chart.options = config.options;
chart.update();` (`chart-panel.component.ts` ~L391) — pre-fix, the options bag (and the plugin's
marker list inside it) was never refreshed. Mechanism: the marker list lives at
`options.plugins['house-anomaly-overlay'].markers`; the overlay paints
`ctx.arc(x, chartArea.top + 6, 6, …)`.

- Arrival-order scenario (anomalies response delayed +25 s ⇒ chart mounts first): at mount
  `markers=[0,0]`, **arcs 0**; after the payload lands: marker list **2**, cumulative **2 arcs at
  y=38 = chartArea.top(32)+6, r=6**; disc centre pixel **#38bdf8**, 127 palette pixels in 14×14.
- Mechanism table (same live chart instance): TIP `options=freshBag; update(); resize(); draw()` →
  **2 arcs** · PRE-FIX `options=mountBag; data=…; update(); resize(); draw()` → **0 arcs** ·
  RESTORED control → **2 arcs**. Pre-fix at the same coordinates: centre **#000000**, 0 palette
  pixels.
- Source-level cross-check (served bytes only: `c.data=d.data,c.options=d.options,` →
  `c.data=d.data,`): marker lists frozen at 0 and arcs 0 for the whole session — the defect
  reproduced in the real product.
- Dev's jest pin `chart-panel.anomaly-order.spec.ts` → 3/3 PASS. Boot fences: scrub hits 0,
  sqlite-line 0, engine DSN `127.0.0.1:5432/ensemble_dev`, jq pin true, sweep pin 0, PORT=8079
  re-exported post-source. API census 0 non-GET. Live :9797 uptime continuous, zero prod contact.

## R2 — PASS (worker `df96922a`)

Fix read: `clickToleranceMs(series, element.index)` — the tolerance is now the clicked sample's
LOCAL neighbourhood (`max(dist to prev, dist to next)`; "the sample that borders a hole OWNS the
hole"; on a uniform grid this equals the old mean). The pinned `houseNearestAnomaly` is unchanged.

- **(a) Hole pin mutation-proven:** at tip 26/26 (the pin = exactly 1 node); in a /tmp pre-fix copy
  (diff = ONLY the R2 revert, verified before running; node_modules symlinked): the pin RED verbatim
  (`Expected: "anom-000003" / Received: undefined` at `:369`) while the other 25 legs stayed green.
- **(b) OWN hole case:** fenced daemon on **:8081** (own worktree/dist; five boot fences green both
  boots); warm + first engine pass 11:56:58Z; port-verified SIGTERM at **12:22:06Z**; idle
  **18.7 min**; restart **12:40:59Z** ⇒ raw-grid **GAP 12:21:16→12:41:13 = 1197 s**. The
  post-restart pass emitted **`anom-000005 daemon.rss_kb low/ramp t_start=12:40:00` — INSIDE the
  hole; nearestSampleDelta 73.0 s vs meanSpacing 38.1 s ⇒ unreachable by the old mean rule** (same
  shape as the dev's 129.0/39.4). Real pointer at the pixel-located disc (elementFromPoint =
  CANVAS[chart-panel-canvas]): **drilldown opened on the FIRST click**, `series daemon.rss_kb`,
  window 12:40–12:50, observed 324068 / baseline 304352.
- **(c) Normal path unchanged:** `anom-000002 daemon.anon_kb` (delta 14.0 s) and `anom-000003
  daemon.cpu_pct` (13.0 s) — both **clicks→open = 1**, correct series chips. A control click on a
  steep series-line run (the R9 colour collision) correctly opened **nothing** (no fabricated id).
- **(d) Refutations adjudicated — both stand:** z-order is structurally impossible
  (`interaction:{mode:'index',intersect:false}` ⇒ nearest-x lookup ignores y; nothing painted can
  block the hit) and key handling resolves by FE dataset-id string equality (never the engine key) —
  the same engine keys drill when geometry allows. Correctly refuted.

## Guard — PASS (worker `9f5cc6a9`)

Numbers byte-identical to the fold's row: **B1 5,907,717 (Δ −1,301)** · B2 0/36 · B3 non-vacuous
(dynamic 2 / static 0 / 0 outside metrics) · **B4a `chunk-TXUCW6LI.js` 206,857 min / 71,054 gz**
(independent gzip-9 equal; headroom 10,946) · **B4b lazy-only 170,177** (route chunk 125,146; fetch
set 687,391; headroom 14,823) · B6 **9==9, 0 added/0 removed** · vendor invariant 0/false ·
measure-propagation rc 0 · guard **exit 0 GREEN** with the new `measurement_tool_stderr` row.
**R8 closed:** both mutation legs (route-chunk + initial-set static edges) produce RED naming the
leak site **with the tool's own `TOOL: VENDOR-INVARIANT RED — …` stderr tail** in the guard output;
restores return exit 0. Thresholds unchanged by the fold (read from the artifact; `bundle-guard.js`
diff = stderr plumbing only). Build isolated (`--output-path /tmp/fe-t1-v2-guard-dist`); worktree
dist digests byte-identical pre/post.

## Pack — PASS, expected posture (worker `6393dbd5`, own tip+base worktrees)

- HEAD leg: `RESULT: FAIL (failed legs: 1)` — **d1 = 32 declared files / 610 passed, 610 total**
  (both new entries running: `chart-panel.anomaly-order` + `timeline-series-compat` — **R4
  closed**); d2_declared 131/131; d2_derived `2 failed, 1832 passed, 1834` (39 files);
  typecheck/dev/prod builds exit 0; `package_json_dependency_set ok:true`.
- BASE leg: `RESULT: FAIL (failed legs: 1)` — d1 SKIPPED-BY-CONSTRUCTION; d2_declared 131/131;
  d2_derived `2 failed, 1799 passed, 1801` (38 files).
- The 2 reds identical both sides: `settings.component.spec.ts:1506/:1741`, DST cause,
  byte-identical Expected/Received ⇒ base-proven, zero lane attribution. **R5 closed:** the
  error-summary now prints `FAIL src/app/pages/settings/settings.component.spec.ts`.
- Membership HONORED on all three sets (24/32 D1 files literally HEAD-only incl. both new ones; all
  4 D2_DECLARED exist at both revs; d2_derived re-derived 38/39 matched the pack's own counts).
- **Base-only 3rd red in base observation 1** = the known `job-queue-indicator` millisecond-tie
  flake (mock `created_at` straddling a ms boundary): NOT lane-attributable (spec + helpers
  blob-identical base↔tip; the only lane change in the import chain is a TYPE-ONLY re-export) and
  **did not reproduce** on the confirming base re-run (obs 2 = exactly 2 reds). HEAD side showed no
  extra reds in two runs.

## Suites + census — PASS (worker `5aea5b18`, own base worktree `-v2`)

- **13-suite scope = the four `pages/metrics/` sub-dirs ⇒ 153/153, rc 0.** Correct reconciliation:
  **149 + 4 = 153** (+1 suite/3 nodes `chart-panel.anomaly-order`; +1 node = the R2 gap leg in
  `chart-panel.component.spec` 25→26) — the prior round's "151" was a different 5-suite scope
  (incl. `house-svg-renderer`, 71 nodes, outside these dirs).
- Whole-FE: BASE **134 suites/4318 tests** · HEAD **155/4743** (+21/+425 = the lane's new FE
  surface). Path-normalized FAILED diff: **base-only 0 · HEAD-only 0 · shared 9** — the exact
  expected family (settings ×2, schedule-create-dialog ×3, jobs-grouping ×3, jobs-filter-state ×1),
  **all 9 blob-identical** (sha over worktree-root-normalized failureMessages); the four failing
  spec files AND their sources are 0-changed across the lane. The jq-indicator flake did not fire
  (3× isolated green). **Net-new deterministic reds = 0.**

## Fence + live baseline — PASS (worker `3342f5cd`)

**The live baseline a staged artifact is measured against (captured 11:39–11:40Z):**

| field | value |
|---|---|
| version / http | **0.28.10** / 200 · `{"status":"alive","uptime_seconds":14701.6,…}` |
| pid / cwd / exe | 3547471 · `/home/nea/agents-ensemble` · `releases/v0.28.10/ensemble-prod` |
| epoch | **2026-11-04T07:34:23Z** (uptime RESET — the first gate's 55,055 s baseline is not comparable) |
| symlink / state.json sha | `releases/v0.28.10` / **`d0ddfb363db46ad2209f7a894f7e1945fc3140e34ffd4ccb89a0afbe9c71f6be`** |
| live binary sha256 | `50d8610c1233f05cb896bb7ed6ac8970b059c946e64702171c29dbc713ab5342` |

The v0.28.10 move is **another lane's operator-initiated scripted promote** (journal:
`run_id r-20261104-073151-e436`, nonce 07:32:29Z → commit 07:35:57Z "gate+soak green;
previous=v0.28.9"). **Our lane made no live change**: the branch diff touches only
`frontend/src/**`, `test/packs/**`, `.agents/**` (no daemon/, scripts/, .env); exactly ONE live
request (GET /livez); zero writes to the install; dev plane clean (only the foreign demo :7979).

## Residual notes (non-blocking, with owners) — this round

| id | note | owner |
|---|---|---|
| **W1** | **Product (small):** `onChartClick` iterates datasets IN ORDER and returns the FIRST dataset with a matching marker — not the marker nearest the click. Observed live: a click on the `cgroup.anon_kb` marker opened a drill-down naming `daemon.rss_kb` (a sibling marker within the same clicked sample's local tolerance). Reachability is restored (R2 works) but the payload can name a sibling anomaly; the B-1 comment's "scans the marker-bearing element first" is not what the code does. | metrics/FE lane |
| **W2** | `bundle-baseline.json`'s `reproduces_at_tip_note` is STALE (claims every commit after `7961bcb21` is docs-only and `git diff 7961bcb21..HEAD -- frontend/` must be empty; at the tip that diff is 105 files, +19,014/−3,299 — the R1/R2 fold is code-bearing). The artifact's NUMBERS remain valid (re-measured green; the fold is lazy-only); only the note is stale, and it could mislead a future worker into overwriting a frozen artifact. | FE T1 lane |
| **W3** | The R1 fix's correctness depends on `buildChartConfig()` returning a NEW options object each rebuild (the vendor's option-scope cache is keyed by object identity; an in-place-mutated cached bag would silently reinstate the freeze). True today (fresh object literal); worth a code comment + pin. | FE T1 lane |
| **W4** | The dev's claim that the R1 closure also fixes the data-end marker's freeze is INFERENCE (same options bag), not evidence — untested this round. | FE T1 lane / next gate |
| **W5** | `job-queue-indicator` equal-timestamp tie-break flake: did not fire here (3× isolated green; the pack's base obs-1 firing did not reproduce in obs-2), but the fixture still stamps `new Date().toISOString()` per mock — a standing routed item. | job-queue-indicator owning lane |
| **W6** | Doc nits: the pack's comment "the base tree does not carry the script" is inaccurate (the script exists at base via `a33943bd7`); the dev's R1 mechanism wording ("resize+draw repaints") is imprecise (only `update()` invalidates the descriptor cache — `resize()/draw()` alone do not re-resolve plugin options). | FE T1 lane |
| **W7** | Probe-side (recorded for future pointer gates): two markers sharing `t_start` draw at the same pixel (2 arcs, 1 disc — do not read arc-count as disc-count); the R9 colour collision (`CHART_SERIES_COLORS[0]` ≡ `CHART_SEVERITY_COLORS.low`) still admits steep-line blobs into colour scans (shape filtering required); the `data-gap-chip` DOM row can occlude a marker under viewport-top scroll (use `scrollIntoView({block:'center'})`). | tester tooling |
| **W8** | Prior-round residuals still open: R3 (count-class engine keys have no enabled series ⇒ no panel ⇒ no marker — scope) and R9 (the chart-theme colour collision — product-side). | metrics lane / chart-theme lane |

## Ceremony readiness

**PASS-WITH-NOTES.** R1 and R2 are independently re-proven closed at the product level (mechanism +
live behavior, both directions); R4/R5/R8 also closed this round; guard, pack posture, suites,
census, fence all green; the live baseline for any staged-artifact measurement is recorded above.
The residual register (W1–W8, plus the carried R3/R9) is non-blocking with owners — **W1
(sibling-anomaly payload naming) and W2 (stale artifact provenance note) are the two to route
first.**
