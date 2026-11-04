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
