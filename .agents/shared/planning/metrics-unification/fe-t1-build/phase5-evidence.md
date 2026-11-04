# Phase-5 Evidence — Tab 1 (Resources on the custom slot) + the third bundle-guard re-run

Lane: FE-only T1 metrics dashboard (wi-32a99618) · worktree
`/home/nea/ensemble-src-wt-fe-metrics-dashboard` · branch
`feature/fe-metrics-dashboard` · freeze `fb70eb038` (4 commits over the
phase-4 freeze `e78fa0ef8`).

Fence held: FE-only (`frontend/` + `.agents/`), NO `daemon/` edits (the two
daemon reads below are READ-ONLY code-fact re-reads), no migrations, no
`scripts/upgrade`, nothing pushed/merged/tagged/staged, targeted
`git add --` only.

## 0. The commits (each green at its own commit)

```
git log --oneline e78fa0ef8..fb70eb038
fb70eb038 test(fe): carry the 5.13 residue pins outside the resource tree so the pinned grep is clean
14d0a5b60 feat(fe): re-point the resource timeline onto the chart.js panel role with visible marker labels (5.7, 5.9)
adbf84941 feat(fe): rebuild tab 1 as the flat resource column with the anomalies panel (5.2-5.6, 5.8, 5.10-5.12)
23bd87127 refactor(fe): relocate the resource module's live and timeline subtrees under the metrics page (5.1)
```

**5.1's commit is a `git mv` (history preserved)** — `git show --stat 23bd87127`
shows all 11 moved files as renames (`R`), the page shell UNMOVED:

```
 .../charts/house-svg-renderer/house-svg-renderer.spec.ts       |  2 +-
 .../components/charts/house-svg-renderer/house-svg-renderer.ts |  2 +-
 .../resource}/live/live-tab.component.html                     |  0
 .../resource}/live/live-tab.component.scss                     |  0
 .../resource}/live/live-tab.component.spec.ts                  |  4 ++--
 .../{resources => metrics/resource}/live/live-tab.component.ts |  4 ++--
 .../resource}/timeline/timeline-drilldown.spec.ts              |  2 +-
 .../resource}/timeline/timeline-drilldown.ts                   |  2 +-
 .../resource}/timeline/timeline-series.ts                      |  2 +-
 .../resource}/timeline/timeline-tab.component.html             |  0
 .../resource}/timeline/timeline-tab.component.scss             |  0
 .../resource}/timeline/timeline-tab.component.spec.ts          |  4 ++--
 .../resource}/timeline/timeline-tab.component.ts               | 10 +++++-----
 frontend/src/app/pages/metrics/tabs.manifest.ts                |  2 +-
 frontend/src/app/pages/resources/resources.component.spec.ts   |  4 ++--
 frontend/src/app/pages/resources/resources.component.ts        |  4 ++--
 16 files changed, 21 insertions(+), 21 deletions(-)
```

The page shell REMAINS at `frontend/src/app/pages/resources/`
(`resources.component.ts/.html/.scss/.spec.ts` +
`resources-gear-menu.spec.ts`) — the route still lazily imports it
(`app.routes.ts:74`); phase 7's 7.4 deletes it. `ls` at the freeze:

```
frontend/src/app/pages/resources/: resources-gear-menu.spec.ts
  resources.component.html resources.component.scss
  resources.component.spec.ts resources.component.ts
frontend/src/app/pages/metrics/resource/: live  timeline  anomalies/
```

(`anomalies/` is phase 5's new panel; the ordering AM-27 names — "4.12 may
delete `pages/resources/svg-timeline/` only after this `git mv`" — was
settled by Phase 4, which already deleted the engine.)

## 1. Task-by-task evidence (command → output)

### 5.2 + 5.12 — the FLAT column, no nested tab group, the placeholder gone

Spec: `tabs/resource-tab.component.spec.ts` →
`ResourceTabComponent — the flat column (5.2 / 5.12 / IMPL-03)` (3 legs):

```
cd frontend && CI=1 ./node_modules/.bin/jest --ci --silent --runInBand \
  src/app/pages/metrics/tabs/resource-tab.component.spec.ts
PASS  … Tests: 24 passed, 24 total
```

* *"renders the three sections IN ORDER"* — the DOM-order proof compares
  `DOCUMENT_POSITION_FOLLOWING` across
  `[data-test="resource-live-strip"]` →
  `[data-test="resource-timeline-panel"]` →
  `[data-test="resource-anomalies-panel"]` (both assertions truthy).
* *"the tab body carries NO nested tab group and NO placeholder"* —
  `host.querySelectorAll('mat-tab-group').length === 0` and
  `[data-test="resource-tab-placeholder"]` is null, and the placeholder
  hint copy is absent.

The placeholder GREP (5.12's acceptance — production sources):

```
grep -rn "resource-tab-placeholder" frontend/src --include="*.ts" --include="*.html" | grep -v ".spec.ts"
(no output — exit 1 = zero hits)
```

The full-tree grep records ONE hit — the body spec's own NEGATIVE
assertion (`resource-tab.component.spec.ts:365`) — that is the pin that
keeps the placeholder dead, not a placeholder.

### 5.3 — the Live strip keeps its own 8 s poll + self-cost chip

```
cd frontend && CI=1 ./node_modules/.bin/jest --ci --silent --runInBand \
  src/app/pages/metrics/resource/live/live-tab.component.spec.ts
PASS  … Tests: 18 passed, 18 total
```

The moved spec is UNCHANGED except its import depth (4 `../../../` →
`../../../../` lines) — every phase-1 assertion still passes at the new
home, cadence included. E-1 code fact (re-read at the freeze):
`resource/live/live-tab.component.ts:57 → readonly POLL_INTERVAL_MS = 8000;`
and `:86 → setInterval(() => this.fetchTick(), this.POLL_INTERVAL_MS)`.

### 5.4 — the panel's own control, one `/history` request per range change

```
cd frontend && CI=1 ./node_modules/.bin/jest --ci --silent --runInBand \
  src/app/pages/metrics/tabs/resource-tab.component.spec.ts
PASS … Tests: 24 passed
```

* phase-3 describes KEPT verbatim: the control is labelled
  "Timeline range"; 15m/1h/24h/7d enabled with 30m/2h DISABLED; the
  control edits the frame's value through the reporter; a disabled option
  is never handed to the frame; the mapping guard refuses a central-only
  window; the retention-tier copy (3.12, three legs).
* NEW (the one-request proof): *"a range change issues EXACTLY ONE
  /history request — and NONE to /live or /self-cost"* — after the window
  change: `getHistory` called exactly 2× (last with `'24h'`),
  `getLive` exactly 1×, `getSelfCost` exactly 1×.

### 5.5 + 5.6 — the anomalies panel + the standing process-local disclosure

```
cd frontend && CI=1 ./node_modules/.bin/jest --ci --silent --runInBand \
  src/app/pages/metrics/resource/anomalies/anomalies-panel.component.spec.ts
PASS … Tests: 7 passed, 7 total
```

* the table renders `id / kind / severity / peak / t_start / t_end` (the
  row test asserts the values + the UTC clock labels); a row click — and
  the keyboard path — emits the WIRE anomaly (7 legs incl. empty state +
  error/retry).
* the drill-down proof: the row click's anomaly reaches the EXISTING
  drill-down through the body (`openAnomaly` →
  `timelinePanel.openDrilldown(anomaly)`) — asserted at the body level by
  the flat-column legs and at the panel level by
  *"openDrilldown(anomaly) — the anomalies panel's row-click path (5.5) —
  runs the SAME drill-down"* (timeline spec): the click issues the two
  calls — `getEvidence('anom-1')` and
  `getTraceback('2026-10-18T16:29:00.000Z', '2026-10-18T16:41:00.000Z')`
  — the padded window of the ported `anomalyDrilldownWindow` policy
  (`resource/timeline/timeline-drilldown.ts`, max(60 s, 10 % span)).
* the disclosure (5.6) is asserted in ONE header line, with the copy
  review as SPEC: `"Process-local journal — resets on daemon restart and
  keeps only the last 256 entries; it never spans a restart."` — the spec
  asserts `resets on daemon restart`, `256`, `never spans a restart`, and
  that NO durability wording exists
  (`/durable|persist|retained across|survives/i` → false).
  E-1 code fact (daemon, READ-ONLY):
  `daemon/services/resource_anomaly.py:171 → JOURNAL_MAXLEN = 256` and
  `:913 → "maxlen (default JOURNAL_MAXLEN = 256 entries)"`.

### 5.7 — the VISIBLE anomaly-marker labels (ADJ-iv shape 1)

```
cd frontend && CI=1 ./node_modules/.bin/jest --ci --silent --runInBand \
  src/app/pages/metrics/resource/timeline/marker-label.spec.ts \
  src/app/pages/metrics/charts/anomaly-overlay-plugin.spec.ts
PASS … Tests: 11 passed (plugin, was 9) + 3 (composer) = 14 over 2 files
```

* the composer (`resource/timeline/marker-label.ts`):
  `${kind} · ${shape} · severity ${severity} · series ${series_key} · peak ${peak}`
  — the spec asserts the FOUR API words (kind / severity / series_key /
  peak) are present, plus the shape word retained.
* the house anomaly plugin now DRAWS that label beside the marker dot
  (`fillText`, muted slate, in the marker lane — never over the samples):
  the plugin leg captures the fillText call and asserts all four words in
  the DRAWN text and the dot-adjacent position; the empty-title leg
  asserts no blank label is drawn. The words are no longer hover-only.

### 5.8 — the client-side data-gap annotation (ADJ-iv shape 2)

```
cd frontend && CI=1 ./node_modules/.bin/jest --ci --silent --runInBand \
  src/app/pages/metrics/resource/timeline/data-gaps.spec.ts
PASS … Tests: 8 passed, 8 total
```

* the detector (`resource/timeline/data-gaps.ts`) derives the gap from the
  `/history` payload ONLY: a maximal run of unavailable samples (null OR
  zero) with a known-good sample before AND a regrowth after. A trailing
  stretch never regrows → not annotated; a leading stretch is the window
  edge → not annotated.
* the label is `"DATA GAP — series unavailable in this interval"` — the
  spec asserts `data gap` present and NO `restart|deploy|promote`
  (case-insensitive), in the label constant, the composed chip text, and
  the panel DOM.
* the PLANTED-WORDING proof (executed):

```
# planted: DATA_GAP_LABEL = 'RESTART GAP — series unavailable in this interval'
cd frontend && CI=1 ./node_modules/.bin/jest --ci --runInBand \
  src/app/pages/metrics/resource/timeline/data-gaps.spec.ts \
  src/app/pages/metrics/resource/timeline/timeline-tab.component.spec.ts
Tests:       3 failed, 21 passed, 24 total
# restored (git diff on data-gaps.ts → EMPTY):
Tests:       24 passed, 24 total   (Test Suites: 2 passed)
```

### 5.9 — the SECOND re-point onto the panel role (AM-27)

```
cd frontend && CI=1 ./node_modules/.bin/jest --ci --silent --runInBand \
  src/app/pages/metrics/resource/timeline/timeline-tab.component.spec.ts \
  src/app/pages/metrics/charts/chart-panel.renderer.spec.ts
PASS … Tests: 18 + 7 (the renderer's new pointClick wiring leg included)
```

* the timeline's registry is now `createDefaultChartRegistry()` with
  `{intent: 'trend', flags: ['shared-y','markers','drill'], surface:
  'interactive'}` — the CHART.JS PANEL wins, per plan-overview §4.2: the
  enabled series split by UNIT CLASS into up to THREE panels —
  Memory (MB, shared scale) / CPU (%) / Counts — each with a unit-labeled
  y-axis. The spec asserts the memory + CPU panels render with their
  titles, the counts panel ABSENT while its series are toggled off, the
  per-series DOM proxies (`data-series`, `data-render-mode="line"`) and
  the visually-hidden data tables (15 rows across the two panels).
* the anomaly markers SURVIVE as the house plugin (the panel inputs carry
  the composed-label markers — asserted per panel), and the drill-down
  click rides the wrapper's `pointClick`: the new renderer leg proves the
  handler travels as `onPointClick` (called with
  `{anomalyId:'a1', seriesId:'s', tsMs:1}`; `null` when no handler), and
  the panel leg proves a marker click fetches evidence + traceback with
  the padded window while a plain sample click (no anomalyId) opens
  NOTHING.
* the mount-failure fallback is proven: forcing the memory panel's mount
  failure surfaces the BADGE and renders the house SVG, whose marker
  click still drills down — never a silent downgrade.

### 5.10 — the PER-SOURCE state (AM-1)

```
cd frontend && CI=1 ./node_modules/.bin/jest --ci --silent --runInBand \
  src/app/pages/metrics/tabs/resource-tab.component.spec.ts
PASS … Tests: 24 passed
```

*"with the central door refusing and the resources door answering 200,
tab 1 renders real data and NO auth panel"* — MetricsService is provided
REFUSING (503-shaped), ResourcesService answers 200: the body renders the
live strip (1700 MB) + the timeline chart + the anomaly row, the copy
contains NO `auth_not_configured`, no `central-tab-error`, no
`metrics-empty-panel`, and the central client's mocks are NEVER called.
The structural pin: the body source contains neither `MetricsService` nor
`/api/metrics`.

### 5.11 — the request-generation discipline (AM-16)

```
cd frontend && CI=1 ./node_modules/.bin/jest --ci --silent --runInBand \
  src/app/pages/metrics/tabs/resource-tab.component.spec.ts
PASS … Tests: 24 passed
```

* *"a window change DISCARDS an in-flight history response"* — window A
  hangs in flight; the window changes; B lands (`history() === B`); A's
  late payload NEVER lands. Same shape for the ANOMALIES class (stale
  rows never land).
* *"a window change does NOT discard the live strip's 8 s poll
  response"* — the live tick hangs in flight; the window changes (the
  poll is NOT re-fired: `getLive` still exactly 1×); the in-flight LIVE
  response resolves AFTER the change and LANDS (the strip renders 1700).
* *"a refused window (a central-only value) issues NO resources request
  at all"* — the mapping guard holds at request time.

### 5.13 — the PINNED residue grep (AM-23)

The EXACT pinned command, at the freeze:

```
grep -rn "TIMELINE_WINDOWS\|window-picker" frontend/src/app/pages/metrics/resource/
(no output)
grep exit: 1  (1 = ZERO hits — clean)
```

**The allowlist-row drift note (the dispatch's ⚠):** the allowlist row did
NOT drift — it is still the frozen union definition at
`frontend/src/app/models/resources.models.ts:16`
(`export type ResourcesWindow = '15m' | '1h' | '24h' | '7d';`), OUTSIDE
the grepped tree, exactly ONE row. The constant the OLD picker carried
(`TIMELINE_WINDOWS`) was DELETED in this phase (the ladder's SSOT is the
frozen union + the manifest's custom entry,
`tabs.manifest.ts:243 → allowedWindows: ['15m', '1h', '24h', '7d']`), so
the grep needs no in-tree allowlist hit at all.

**The pins live OUTSIDE the tree, deliberately:** the window-ladder
absence pins moved from the in-tree timeline spec to
`pages/metrics/tabs/resource-tab.component.spec.ts` (the commit
`fb70eb038`) — an in-tree pin would itself hit the pinned grep. The
house precedent is the phase-2 `active` residue check (its pin reads the
pinned scope in-process). The relocated pins assert the panel source +
template carry no `TIMELINE_WINDOWS` / `window-picker` / `selectWindow` /
`getHistory` / `getAnomalies` / `mat-button-toggle`, and expose
`openDrilldown(anomaly: Anomaly)`.

### 5.14 — the BUNDLE GUARD, third re-run (AM-2 + W-4)

```
cd frontend && ./node_modules/.bin/ng build --configuration production 2>&1 | tee /tmp/fe-t1-p5-prod.log
(exit 0 — 6 pre-existing style-budget warnings, the baseline set)
node ../.agents/shared/planning/metrics-unification/fe-t1-build/bundle-guard.js \
  dist/frontend/browser /tmp/fe-t1-p5-prod.log
=== T1 bundle guard (phase 1, task 1.9) ===
(a) B1  initial_total_bytes = 5909624 · baseline 5909018 · delta 606 B (limit |2048| B)
        initial_file_count  = 37 · measurement_tool = 5909624 (cross-check)
(b) B2  initial_chunks_scanned = 37 · marker_hits = 0
(c) B3  chart_js_import_sites = 1 · static_sites = 0 · outside_metrics_tree = 0
        src/app/pages/metrics/charts/chart-panel.component.ts: dynamic=2 static=0
(d) B6  baseline_set_size = 9 · measured_set_size = 9 · added 0 / removed 0
RESULT: GREEN (B1 + B2 + B3 + B6)
```

**BOTH planted variants turn it RED** (planted as temporary uncommitted
edits, restored after — `git diff` empty on both files):

```
# (a) EAGER import in the INITIAL tree (src/app/app.ts)
RESULT: RED (2 violation(s))
  - B3 1 STATIC chart.js import site(s): src/app/app.ts
  - B3 1 chart.js import site(s) outside src/app/pages/metrics/**: src/app/app.ts

# (b) STATIC import INSIDE the LAZY metrics tree (chart-panel.component.ts)
RESULT: RED (1 violation(s))
  - B3 1 STATIC chart.js import site(s): src/app/pages/metrics/charts/chart-panel.component.ts
```

Variant (b) is the C-1 case: B1 still green (606 B) and B2 still green
(0 markers) — the SHAPE check is the only thing that catches it.

**The route-keyed measurement + the B4 lazy-only decomposition:**

```
cd frontend && node ../.agents/shared/planning/metrics-unification/fe-t1-build/bundle-baseline.measure.js dist/frontend/browser
initial_total_bytes = 5909624 · lazy_chunk_count = 54
route_chunk_locator = "route-key"            ✓ (the Phase-2 marker constant)
route_chunk_key = "ensemble-metrics-route-v1"
route_chunk_resolved_to_exactly_one = true   ✓
metrics_route_chunk = chunk-OCR5MIBM.js · 53,512 B
metrics_route_fetch_set_bytes = 673,372
metrics_route_fetch_set_lazy_only_bytes = 153,873   (base 72,708)
metrics_route_fetch_set_shared_with_initial_bytes = 519,499
pre_retirement_resources_route_chunk = chunk-5IMXFWVY.js · 55,330 B
largest_lazy = chunk-TXUCW6LI.js · 206,857 B (the chart vendor — unchanged)
```

**⚠ B4 GROWTH EXCEEDANCE — DISCLOSED, ATTRIBUTED, ROUTED (deviation D-5.14a).**
The lazy-only growth is **153,873 − 72,708 = +81,165 B**, OVER the
65,536 B allowance by 15,629 B. The composition is attributed from the
dist (probe-verified, not asserted):

| member of the route fetch set (lazy-only) | bytes | what it is |
|---|---|---|
| `chunk-5IMXFWVY.js` | 55,330 | the RELOCATED resource module (probe: `DATA GAP`, `self-cost`, `POLL_INTERVAL`) — the chunk now SHARED between the /metrics route (tab 1) and the retired /resources shell; before this phase the same code rode the /resources route chunk only (38,914 B at lane base) |
| `chunk-AFVHVSGD.js` | 43,683 | the shared Material/common chunk the relocated module's components pull |
| the route chunk's own growth | +3 B | 53,509 → 53,512 (the flat body replaced the placeholder at ~parity) |

The growth is therefore **the phase's own deliverable** — tab 1's code
now loads with the /metrics page (its default tab), not accidental
leakage: B1 is UNCHANGED at +606 B (nothing became eager; the chart
vendor stays dynamic-only at 206,857 B in its own lazy chunk). No honest
reduction exists inside this phase's mandate: the bytes are irreducible
without lazy-loading tab 1's body (which would game the metric, not
reduce it) or deleting the /resources shell (phase 7's 7.4, which will
re-fuse the shared chunk into the route chunk — same bytes, different
bucket). **The threshold is the caller's to re-adjudicate** (IMPL-21:
"the threshold is NOT re-litigated" at lane level; the phase-4 precedent
recorded the chart-chunk threshold miss the same way). The plan's own
5.14 acceptance — the GUARD green, both planted variants red, the initial
total within 2 kB — is MET.

## 2. The relocated specs — per-assertion disposition (none dropped)

| prior leg (phase 1–4) | disposition |
|---|---|
| live-tab spec (18 legs: poll forkJoin, skeleton-only-first, one error branch, skip-in-flight, self-cost chip, …) | **MOVED** — import depth only; 18/18 green at `resource/live/` |
| timeline default window `DEFAULT_TIMELINE_WINDOW === '1h'` + the initial fetch | **MOVED to the body spec** — the manifest owns the default (`defaultWindowS: '1h'`, asserted in `tabs.manifest.spec.ts`, still green) and the body fetches (the one-request legs) |
| `selectWindow` re-fetches both series / no re-fetch on the same window | **MOVED to the body spec** — the effect-driven loads (a range change issues exactly one /history; the refused-window leg) |
| skeleton-only-before-first-fetch (D7) | **MOVED** — the panel renders it from the body's `loading` input (same D7 semantics, re-specified) |
| last-good + exactly-one error branch + Retry (D7) | **MOVED** — the panel renders the body's error branch once; Retry emits to the owner (the panel never fetches) |
| house-SVG series paths (`d` non-empty) | **RE-SPECIFIED (5.9)** — the primary render is the Chart.js panel (proxies + hidden tables); the house SVG is asserted on the MOUNT-FALLBACK path (badge + fallback lines) |
| truncated banner strictly from the payload flag (D9) | **MOVED** — same assertion from the body's history input |
| marker click → padded drill-down (AC5) | **MOVED** — now via the wrapper's `pointClick` (5.9); the padded-window values are asserted verbatim |
| drill-down panel render (summary chips / contributors / traceback children) | **MOVED verbatim** — green |
| drill-down error branch + retry | **MOVED verbatim** — green |
| series toggles | **MOVED** — counts the panel proxies now |
| out-of-order WINDOW supersede (history + anomalies) | **MOVED to the body spec** — the body owns the tokens (5.11); the stale-history leg and the stale-ERROR leg re-specified there (history) and as the anomalies-discard leg |
| out-of-order DRILL-DOWN supersede + the stale-error leg | **MOVED verbatim** — the drill-down keeps its own token |
| "a window change closes the drill-down" | **DISPOSITIONED** — 5.4 makes the drill-down WINDOW-INDEPENDENT; the panel no longer closes it on a window change (the body does not call `clearDrilldown`); superseded by the window-INDEPENDENCE pins |
| house-svg capability leg (1) "the window ladder is present" | **RE-POINTED** — the ladder's SSOT moved to the manifest (`metricsCustomTab().allowedWindows`, same 4 windows); the file also carries two comment-path updates (the moved drill-down policy path) |
| e2e `resources-gate.spec.ts` AC8 timeline legs (SVG path count, marker click, drill-down chips) | **DISPOSITIONED** — the surface moved to /metrics tab 1; the /resources shell renders the transitional empty state; the same assertions live as jest specs at the new home; phase 7 deletes the route. The leg now asserts the transitional state honestly (the timeline tab visible; the old window-selector gone) |

## 3. Whole-tree (D2) + the base-proof

```
cd frontend && CI=1 ./node_modules/.bin/jest --ci --silent --runInBand
Test Suites: 4 failed, 147 passed, 151 total
Tests:       9 failed, 4614 passed, 4623 total
```

The 9 reds are BASE-PROVEN: the identical four suites
(`schedule-create-dialog`, `jobs-filter-state.model`, `jobs-grouping.model`,
`settings`) fail IDENTICALLY at the phase-5 base `e78fa0ef8` in a
throwaway detached worktree (`git worktree add --detach` + a symlinked
`node_modules`, removed + pruned after):

```
wc -l /tmp/p5-base-reds.txt → 9
diff /tmp/p5-base-reds.txt /tmp/p5-head-reds.txt
→ empty  ⇒ FAILING-NAME DIFF BASE->HEAD: EMPTY
```

**Net-new deterministic reds = 0.**

## 4. The scoped type-check + the prompt-artifact baseline

```
cd frontend && ./node_modules/.bin/tsc -p tsconfig.metrics-fe.json --noEmit
(exit 0 — no output)
# note: the moved timeline spec now falls under the include glob
# `src/app/pages/metrics/**/*.spec.ts` automatically — no tsconfig change.

/home/nea/ensemble-src/.venv/bin/python scripts/protection/generate_prompt_artifact_baseline.py --check
PROMPT-BASELINE: OK — 161 versioned artifact(s) match .prompt-artifacts.sha256
sha256sum -c agents/.prompt-artifacts.sha256 | grep -c ": OK"  → 161
```

## 5. E-1 re-derivation at this freeze

The bundle baseline (G1/G2) is **re-checked by 5.14** above (B1 delta 606 B
vs the artifact's 5,909,018 B — command + verbatim output in §1/5.14). The
code facts, re-read at the freeze (file:line + output):

| fact | value | re-read |
|---|---|---|
| the LIVE strip's poll cadence | 8 s | `resource/live/live-tab.component.ts:57` — `readonly POLL_INTERVAL_MS = 8000;` (driven at `:86`) |
| the ladder | 4 values | `models/resources.models.ts:16` — `export type ResourcesWindow = '15m' \| '1h' \| '24h' \| '7d';`; the manifest's custom entry `tabs.manifest.ts:243` |
| the journal cap | 256 entries | `daemon/services/resource_anomaly.py:171` — `JOURNAL_MAXLEN = 256` (READ-ONLY; the disclosure's `:913` cites it) |

## 6. Deviations + disclosures (every one, with its reason)

1. **D-5.14a — the B4 lazy-only growth exceeds the 65,536 B allowance**
   (+81,165 B, attributed composition in §1/5.14). The growth IS the
   phase's deliverable (the relocated module riding as tab 1's payload);
   the guard itself is GREEN; the threshold is routed to the caller
   (Phase 9 / the leader own B4's re-adjudication).
2. **The drill-down is now WINDOW-INDEPENDENT (5.4)** — a deliberate
   semantic change from the phase-1 hardening (which closed an open
   drill-down on a window change). The old close-on-window-change leg is
   dispositioned (§2), not silently dropped.
3. **The anomalies load has its OWN error branch** (panel-local, with
   Retry) — the phase-1 single-error-branch rule was per-LOAD; the three
   request classes (5.11) get per-class branches so an anomalies failure
   can never blank the timeline (and vice versa).
4. **The house-svg capability leg (1) re-pointed to the manifest** — the
   ladder's SSOT moved; the assertion (the same 4 windows) is unchanged
   in meaning.
5. **The e2e AC8 timeline legs dispositioned** (§2 last row) — playwright
   is not in this lane's jest gates; the edit is honest about the
   transitional shell state and names the jest home of every
   re-homed assertion.
6. **`TIMELINE_WINDOWS` / `DEFAULT_TIMELINE_WINDOW` deleted** — required
   by 5.13's pinned grep (the ladder's SSOT is the frozen union + the
   manifest entry; the manifest comment that cited the deleted constant
   was reworded).
7. **The `/resources` shell renders the timeline panel in a transitional
   empty state** (no inputs) until phase 7 deletes the route — accepted
   explicitly by the plan's risk table ("relocating the module breaks the
   retired-route specs mid-phase").

## 7. Final state

```
git status --short   → (empty)
git log --oneline -8
fb70eb038 test(fe): carry the 5.13 residue pins outside the resource tree so the pinned grep is clean
14d0a5b60 feat(fe): re-point the resource timeline onto the chart.js panel role with visible marker labels (5.7, 5.9)
adbf84941 feat(fe): rebuild tab 1 as the flat resource column with the anomalies panel (5.2-5.6, 5.8, 5.10-5.12)
23bd87127 refactor(fe): relocate the resource module's live and timeline subtrees under the metrics page (5.1)
e78fa0ef8 docs: correct the canvas-mock planted-variant output in the phase-4 evidence
da363245f fix(fe): drop the window literal from the door's residue-check comment
c7d1ad324 docs: record the phase-4 evidence and register the phase-4 packs
b8e47e7c7 test(fe): re-specify the canvas-fatal e2e legs and the scoped type-check
```

Nothing pushed, merged, tagged, or staged.
