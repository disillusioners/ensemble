# R1 + R2 fold evidence — the marker arrival order + the unreachable-marker path (2026-11-04)

Fold of the tester functional gate's **FIX ORDER #4** residuals **R1** (the
frozen plugin options) and **R2** (the one instance whose marker rendered
but never drilled down), plus the small folds **R4 / R5 / R6 / R7 / R8**.

Worktree `/home/nea/ensemble-src-wt-fe-metrics-dashboard`, branch
`feature/fe-metrics-dashboard`, base tip `7c36835c0` (the tester's
re-verification commit). FE-only product code; the daemon is READ-ONLY.

The tester's evidence: `.agents/tester/RESULTS/2026-11-04-fe-t1-metrics-functional-gate.md`
(the SCOPED RE-VERIFICATION section, residual notes R1–R9).

---

## 1. R1 — the frozen plugin options (REPRODUCED, then fixed)

### 1.1 The hypothesis

The tester's code-reading: the wrapper's rebuild effect assigns `chart.data`
but NEVER `chart.options`, so the overlay plugin's marker list
(`options.plugins[HOUSE_ANOMALY_PLUGIN_ID].markers`) is frozen at
chart-mount time. A payload that resolves AFTER the mount is never drawn.

### 1.2 The reproduction — the ORDER, driven in jest

**Driving mode: the jest seam** (the component + the REAL vendor). Why: the
defect is an ORDER between two inputs to the same component, and the jest
seam drives that order deterministically (chart first, payload second) —
the browser's arrival order is a race the seam removes. The PRODUCT
symptom was then reproduced at the PIXEL level (the vendor's own draw
path), not merely asserted on an options bag.

`frontend/src/app/pages/metrics/charts/chart-panel.anomaly-order.spec.ts`
(new, 3 legs):

| leg | BEFORE the fix | AFTER the fix |
|---|---|---|
| the ORDER — the markers reach the chart's plugin options when the anomalies arrive AFTER the mount | **RED** — `markers.length` 0 (the list stayed frozen at the mount-time snapshot) | **GREEN** — 1 marker, fields read back through the vendor's resolved proxy |
| the NORMAL path — the mount-time markers survive a rebuild | GREEN (unchanged) | GREEN (unchanged) |
| the VENDOR's draw path paints the late marker (`chart.resize` + `chart.draw`, the canvas mock's `arc` events) | **RED** — **0 arcs** (the marker was NEVER painted — the exact symptom) | **GREEN** — 1 arc, at the marker's own scale pixel |

The RED state was captured by reverting ONLY the component change
(`git checkout -- frontend/src/app/pages/metrics/charts/chart-panel.component.ts`)
and re-running the same file: **2 failed, 1 passed** (the normal-path leg
is unaffected by the fix, as it must be).

The vendor's own resolver was read to make the pin faithful:
`chart.options` is a PROXY over the option scopes and every nested object it
returns is a sub-resolver proxy — so `chart.options.plugins[id].markers[0]`
is a proxy, not the plain marker (jest's deep equality throws on it;
`name.startsWith is not a function`). The pin therefore reads the marker
FIELDS through the resolved proxy and asserts the deep equality against the
RAW bag (`chart.config.options`) — which is what the vendor's plugin
resolver itself reads.

### 1.3 The fix

`chart-panel.component.ts` — the rebuild effect now refreshes the OPTIONS
bag as well as the data:

```ts
chart.data = config.data;
chart.options = config.options;   // ← R1
chart.update();
```

The assignment is the vendor's own update shape (`data` + `options` +
`update()`): the plugin resolves its options from the config the chart was
constructed with and only rebuilds them on `update()`, so the options bag
must be replaced BEFORE the update. This also closes the SAME freeze for the
data-end marker (`HOUSE_DATA_END_PLUGIN_ID.dataEnd`), which travels in the
same options bag.

---

## 2. R2 — the instance that rendered but never drilled down

### 2.1 The isolation — the mechanism, from the CODE and the REAL payload

The tester's observation: `anom-000003` (`daemon.rss_kb`, 08:55→09:05)
rendered a marker, but 25 real clicks at/around its exact centre opened NO
drill-down, while a neighbouring marker 25 px away opened fine and the SAME
KEY was click-through on a later instance.

**The three candidate classes, decided by evidence:**

| class | verdict | evidence |
|---|---|---|
| (i) z-order / hit-target | **REFUTED** | the tester's own `document.elementFromPoint` chain reached the CANVAS at the click point; the disc was a real painted disc |
| (iii) key handling | **REFUTED** | the vocabulary/shim join is proven, and the SAME engine key (`daemon.rss_kb`) is click-through on a later instance (`anom-000012`) |
| (ii) geometry — the marker is drawn at a position whose hit area is elsewhere | **CONFIRMED** | below |

**The mechanism (the two clocks do not agree):**

1. **The anomaly engine buckets on a 5-minute stride.** `daemon/services/resource_anomaly.py`
   — `STATE_BUCKET_S = 300`, `STATE_WINDOW_S = 4 * 3600`; every observed
   timestamp is floored onto that stride (`_floor_to_stride`). The
   detection bucket becomes the finding's `time_bucket` →
   `lean_row()["t_start"]` → the FE's `t_start` → the marker's `tsMs`
   (`timeline-tab.component.ts`: `tsMs: parseNaiveUtc(anomaly.t_start)`).
   The FE's marker is therefore placed on a **5-minute** clock.
2. **The timeline's grid is the raw 30-second tier** — the `/history`
   endpoint's `resolution_used: "raw"` (measured live: 30 s and 31 s
   spacings), with the daemon's own samples.
3. **A daemon restart leaves a HOLE in that grid** — the last pre-restart
   sample, then the first post-restart one. Measured live on the dev DB:
   `resource_daemon_bucket` rows stop at `08:39:34` and resume at
   `08:58:02` (18.5 minutes with no sample at all).
4. **The detection bucket lands INSIDE that hole.** The first live sample
   after a restart is floored onto the 5-minute stride, so the detection
   bucket PRECEDES the first sample by up to 5 minutes — and the hole
   contains no sample to match it. Measured live on the fenced dev daemon:
   the daemon booted at `10:51:37`, its first row is `10:52:00`, and the
   engine's findings carry `t_start = 2026-11-04 10:50:00` — the 5-minute
   bucket, 2 minutes before the first sample.
5. **The click's time resolution could not reach it.** The wrapper matched a
   marker against the clicked sample with a tolerance of **one MEAN sample
   spacing** (`clickToleranceMs` = total span / (n − 1)). Over a grid with a
   hole the mean is the DENSE region's spacing (measured live: **57.4 s**
   over a 1-hour grid whose spacings are 30 s, 31 s and one 1722 s hole),
   while the marker's distance to the nearest sample is the HOLE's width
   (minutes). The marker is DRAWN (its time is inside the plotted window)
   but the click resolves the nearest sample and the tolerance check fails
   ⇒ a plain payload ⇒ **no drill-down**. That is exactly the reported
   symptom.

**⇒ CLASSIFICATION: (ii) GEOMETRY — the marker is placed by TIME and the
click is resolved by SAMPLE, and the two agree only when the marker's time
is within one MEAN spacing of a sample. In a restart hole (or before the
first sample) they never agree.**

### 2.2 The fix — the tolerance becomes LOCAL

`chart-panel.component.ts`:

```ts
private clickToleranceMs(series: ChartPanelSeries, index: number): number {
  // … the clicked sample's OWN neighbourhood: the distance to its farther
  // neighbour. On a uniform grid this is exactly the old mean (one
  // spacing), so the normal path is unchanged; the sample that borders a
  // hole now OWNS the hole.
}
```

`houseNearestAnomaly` (the pinned pure helper) is UNCHANGED — only the
tolerance's SOURCE moved from the series-wide mean to the clicked sample's
local neighbourhood.

### 2.3 The pin — the real shape

`chart-panel.component.spec.ts` (new leg): a grid of 30 dense pre-restart
samples (30 s), an **18.5-minute hole**, then 30 dense post-restart
samples; the anomaly's detection bucket lands 10 minutes INTO the hole; the
pointer resolves the nearest sample (the first post-restart one).

| | BEFORE the fix | AFTER the fix |
|---|---|---|
| the leg | **RED** — no marker matched (the mean tolerance ≈ 48 s vs the marker's 510 s) | **GREEN** — `anomalyId = anom-000003` |

### 2.4 The LIVE proof — the real instance, real pointer clicks

The fenced dev daemon was **stopped and restarted across a deliberate
hole** so that the engine's detection bucket lands inside it: the previous
daemon's last row is `11:10:30`, the new daemon's first row is `11:17:09`
(a 6.6-minute hole), and the engine's first live bucket — the detection
bucket — is **11:15:00**, which has no sample at all.

The REAL payload (GET only, `window=1h`):

```
GRID gaps(>120s)= [{from:11:10:30,to:11:17:09,seconds:399}]
ANOMALY anom-000003 key=daemon.rss_kb  sev=low  shape=ramp t_start=11:15:00 nearestSampleDelta=129.0s meanSpacing=39.4s reachable_by_mean_rule=false
ANOMALY anom-000002 key=daemon.cpu_pct sev=high shape=ramp t_start=11:15:00 nearestSampleDelta=129.0s meanSpacing=39.4s reachable_by_mean_rule=false
ANOMALY anom-000001 key=daemon.anon_kb sev=high shape=ramp t_start=11:25:00 nearestSampleDelta=10.0s  meanSpacing=39.4s reachable_by_mean_rule=true
ANOMALY anom-000004 key=daemon.threads sev=high shape=ramp t_start=11:25:00 nearestSampleDelta=10.0s  meanSpacing=39.4s reachable_by_mean_rule=true
```

`anom-000003` is the SAME instance the gate reported (the engine key
`daemon.rss_kb`, a ramp, the 11:15 detection bucket) — and the two
`reachable_by_mean_rule=true` rows are the CONTROL group (markers that sit
on a sample).

**The real-pointer probe** (chromium → `ng serve :4199` → the fenced dev
daemon; the marker discs located by PIXEL-PROBING the canvas for the house
severity palette — a disc is a ≥ 6 px run, a series line is 1–3 px, which is
also what dodges R9's colour collision; `document.elementFromPoint` names the
hit target before every click; the verdict is the drill-down's OWN summary):

| instance | PRE-FIX | POST-FIX |
|---|---|---|
| **`daemon.rss_kb` (anom-000003 — the gate's instance)** | **NO-DRILLDOWN** | **DRILLDOWN-OPEN** — `series daemon.rss_kb · window 11:15–11:25 UTC · observed 317676` |
| `daemon.cpu_pct` (anom-000002 — the other hole instance) | (the probe's page scroll shifted; the pre-fix run's verdict is on the rss instance) | **DRILLDOWN-OPEN** — `series daemon.cpu_pct · window 11:15–11:25 UTC · observed 116.5667` |
| `daemon.threads` (anom-000004 — the CONTROL, on a sample) | **DRILLDOWN-OPEN** | **DRILLDOWN-OPEN** |
| `elementFromPoint` at every click | **the CANVAS** (`CANVAS[chart-panel-canvas]`) — the hit target was never the issue | the CANVAS |
| the API census | 42 calls, **0 non-GET** | 42 calls, **0 non-GET** |

The two `NO-DRILLDOWN` clicks in the post-fix run are the R9 false
positives (the series-0 LINE, painted in `CHART_SEVERITY_COLORS.low`'s
byte-identical colour) — they are not markers, and the probe's shape test
separates them from the real discs (a 12-row vertical line run vs the
10-row disc in the marker lane).

**⇒ The gate's instance now drills down on a real pointer click, and the
control instance is unchanged.**

### 2.5 The driver's VERBATIM output (the post-fix run)

```
PAYLOAD anomalies=4 history_points=86
GRID gaps(>120s)=[{"from":1793790169000,"to":1793790570000,"seconds":401},{"from":1793790630000,"to":1793791029000,"seconds":399}]
ANOMALY anom-000002 key=daemon.cpu_pct sev=high shape=ramp t_start=2026-11-04 11:15:00 nearestSampleDelta=129.0s meanSpacing=39.0s reachable_by_mean_rule=false
ANOMALY anom-000003 key=daemon.rss_kb sev=low shape=ramp t_start=2026-11-04 11:15:00 nearestSampleDelta=129.0s meanSpacing=39.0s reachable_by_mean_rule=false
ANOMALY anom-000001 key=daemon.anon_kb sev=high shape=ramp t_start=2026-11-04 11:25:00 nearestSampleDelta=10.0s meanSpacing=39.0s reachable_by_mean_rule=true
ANOMALY anom-000004 key=daemon.threads sev=high shape=ramp t_start=2026-11-04 11:25:00 nearestSampleDelta=10.0s meanSpacing=39.0s reachable_by_mean_rule=true
CANVASES=2
CANVAS[0] discs=3 [{"severity":"low","x":575.5,"y":10,"rows":12},{"severity":"low","x":990.814453125,"y":33,"rows":10},{"severity":"high","x":1221.4375,"y":33,"rows":10}]
CLICK canvas=0 severity=low at=(604,951) topmost=CANVAS[chart-panel-canvas] -> NO-DRILLDOWN
CLICK canvas=0 severity=low at=(1019,974) topmost=CANVAS[chart-panel-canvas] -> DRILLDOWN-OPEN summary="Anomaly drill-down close kind memory shape ramp severity low series daemon.rss_kb window 11:15–11:25 UTC observed 317676 baseline 304204 CONTRIBUTORS unattributed 100% Attribution is a sample-time estimate; the unattribu"
CLICK canvas=0 severity=high at=(1249,974) topmost=CANVAS[chart-panel-canvas] -> DRILLDOWN-OPEN summary="Anomaly drill-down close kind threads shape ramp severity high series daemon.threads window 11:25–11:25 UTC observed 62 baseline 39.5 CONTRIBUTORS unattributed 100% Attribution is a sample-time estimate; the unattributed"
CANVAS[1] discs=2 [{"severity":"low","x":664.5,"y":10,"rows":12},{"severity":"high","x":982.185546875,"y":33,"rows":10}]
CLICK canvas=1 severity=low at=(693,640) topmost=CANVAS[chart-panel-canvas] -> NO-DRILLDOWN
CLICK canvas=1 severity=high at=(1010,663) topmost=CANVAS[chart-panel-canvas] -> DRILLDOWN-OPEN summary="Anomaly drill-down close kind cpu shape ramp severity high series daemon.cpu_pct window 11:15–11:25 UTC observed 116.5667 baseline 6.5833 CONTRIBUTORS unattributed 100% Attribution is a sample-time estimate; the unattrib"
API-CENSUS calls=42 nonGET=0
C1-VERDICT: at least one marker drilled
```

---

## 3. The small folds

| id | fold |
|---|---|
| **R4** | **CONFIRMED A REAL GATE GAP and CLOSED.** The compat spec `timeline-series-compat.spec.ts` was in NO declared set: not in `D1_FILES`, not in `D2_DECLARED`, and the `D2_DERIVED` greps (`from '[^']*/models'`, `app\.routes\|app\.ts'\|app\.html'`) do not match it (it imports only the sibling `./timeline-series`) — so the pack never ran it. It is now in `D1_FILES` (the HEAD-only lane-owned set), together with the new `chart-panel.anomaly-order.spec.ts`. `D1_FILES` is now **32 declared** (was 30). |
| **R5** | The pack's `${leg}_ERROR_SUMMARY` grep was `^(FAILED\|ERROR) ` — jest prints a failing suite as `FAIL <path>`, so the summary read `(none)` on a failing leg (the verdict was never affected: the exit code and the `Tests:` counts are correct). The grep now matches BOTH forms. |
| **R6** | The fold-evidence row's "12 suites / 133 tests" matched NO natural scope. Re-derived on the four metrics sub-directories (`charts/`, `resource/timeline/`, `resource/anomalies/`, `resource/live/`): **12 suites / 149 tests at the fold's tip `cf4e4fd14`** (the tester's measurement) and **13 suites / 153 tests** at this tip (the R1 order spec +1 suite / 3 tests, the R2 gap leg +1 test). The row is corrected to state both. |
| **R7** | The B4b arithmetic now names BOTH baselines: the fold-evidence's "+4,039" was measured against the FIRST GATE's own 166,055, while the ARTIFACT's post-relocation baseline of record is **165,185** ⇒ the Δ vs the artifact is **+4,909** (both under the 185,000 limit). |
| **R8** | The guard DISCARDED the measure tool's stderr (`stdio: […, 'ignore']`), so the operator-facing reason line could never carry the tool's own `TOOL: VENDOR-INVARIANT RED — …` tail (the invariant itself was enforced from the stdout JSON). The stderr is now PIPED and surfaced — in the MEASURE failure line AND as a `measurement_tool_stderr` report row. |

---

## 4. Kept as ROUTED / DISCLOSED (not folded)

| id | disposition |
|---|---|
| **R3** | Count-class engine keys (`daemon.fds`, `daemon.threads`) resolve to a unit class with NO enabled series ⇒ no panel ⇒ no marker can exist. A SCOPE FACT, not a shim defect — recorded here (the resource tab has no copy surface for it; no copy was invented). Owner: the metrics lane (series enablement). |
| **R9** | `CHART_SERIES_COLORS[0]` is byte-identical to `CHART_SEVERITY_COLORS.low` — already routed to the chart-theme owning lane; this fold records it as a DUPLICATE of that entry (no second entry created). It constrained the live probe: the marker-disc probe discriminates a DISC (≥ 6 px run) from a series LINE (1–3 px), so the collision cannot confuse the probe. |
| **V1 (new, disclosed)** | The OTHER variant of the same clock mismatch: a detection bucket that precedes the FIRST sample of the plotted window (the daemon's first sample after a restart, floored onto the 5-minute stride) is drawn OUTSIDE the chart area — the vendor does not clamp `getPixelForValue`, so the disc lands off-plot and is INVISIBLE (the drill-down stays reachable through the anomalies-panel ROW click). This fold fixes the REACHABILITY of a drawn marker (R2); it does NOT clamp the draw, so the off-plot variant remains invisible — disclosed, not folded. |

---

## 5. The re-run (the whole tree, at this tip)

| check | result |
|---|---|
| The scoped metrics suites (`charts/`, `resource/timeline/`, `resource/anomalies/`, `resource/live/`) | **13 suites / 153 tests / 0 failed** |
| The whole FE census | **155 suites / 4743 tests — 4 failed suites / 9 failed tests, the SAME 9 base-proven reds** (settings ×2, schedule-create-dialog ×3, jobs-grouping ×3, jobs-filter-state ×1 — the date-dependent DST / stale-expectation debt the gate documented), **net-new deterministic reds = 0** (the gate's HEAD2 census was 154 suites / 4739 tests; this tip adds the R1 order spec +3 and the R2 gap leg +1) |
| The pack's D1 leg (the 32 declared files, with the R4 closure) | **32/32 declared files present; 32 suites / 610 tests / 0 failed** |
| `tsc --noEmit -p tsconfig.app.json` | **rc 0** |
| `tsc --noEmit -p tsconfig.metrics-fe.json` (the pack's scoped gate) | **rc 0** |
| The guard (B1 / B4a / B4b / the vendor invariant / the warning set) | **GREEN, exit 0** — B1 5,907,717 (Δ −1,301 vs 5,909,018, limit ±2,048); B4a 206,857 min / **71,054 gz** (≤ 82,000); B4b lazy-only **170,177** (≤ 185,000; +83 B vs the gate's 170,094 — the R1/R2 code, lazy-only); vendor 0 / false; the warning set 9 == 9; `measurement_tool_stderr = (none)` (the R8 row) |
| Prompt baseline | **PROMPT-BASELINE: OK — 161 versioned artifact(s) match** |

## 6. The fence + the teardown

| fence | evidence |
|---|---|
| The dev boot | per-variable scrub (never `env -u POSTGRES_*`) → the worktree `.env` → `POSTGRES_DB=ensemble_dev` asserted → `PORT=8079` re-exported unconditionally → `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` PINNED (the frozen binary's path derivation resolves the LIVE install root, so the sweep must not run) → the scratch `ENSEMBLE_DATA_DIR` with the exact pre-seeded `ensemble.json` (the pin re-asserted `true`) → the pinned interpreter (`/home/nea/ensemble-src/.venv/bin/python -m daemon`) |
| The boot's own receipts | `Starting Ensemble v0.28.9`; `Loaded ensemble config: database=postgres`; `Creating PostgreSQL engine: 127.0.0.1:5432/ensemble_dev`; the "No PostgreSQL ENV vars detected, defaulting to sqlite" line **0 hits** |
| The prod fence | **ZERO live contact.** The live daemon answered `/livez` 200 `version 0.28.10` with an uptime continuous since ~07:31Z (BEFORE this lane); the live `state.json` (mtime 07:36Z, current `v0.28.10`, previous `v0.28.9`, `in_flight: null`, `pending_op: null`) predates the lane; the live symlink and the journal were never written by this lane. The `anom-*` ids are process-local (the daemon's own contract) |
| The dev DB | `ensemble_dev` only; the dev daemon's own resource rows were written by the dev daemon; no DROP, no schema change; the sweep guard pinned off |
| The teardown | the dev daemon SIGTERM'd (port-verified PID; :8079 free), the `ng serve` dev server stopped (:4199 free), and **every `/tmp` artifact this fold created swept** (the boot script, the daemon logs, the probe driver + its output, the isolated dist, the build/guard/measure logs, the jest cache + JSON, the payload captures, the scratch data dir, the fix patches). The worktree's own `frontend/dist` (08:57, the lane's earlier build) was NOT touched — this fold's production build went to the isolated `/tmp` path |
