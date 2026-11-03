# Phase 3 evidence — the page frame (the range selector + the baseline)

Lane: `feature/fe-metrics-dashboard` · worktree `/home/nea/ensemble-src-wt-fe-metrics-dashboard`
· base `1474952fe` (Phases 0/1/2 landed) · phase-3 freeze: the commit that carries this file.

**Scope.** FE-only (`frontend/` + `.agents/` planning/evidence). No `daemon/` edit, no
migration, no `scripts/upgrade`, no live daemon, no HTTP, no DB. `agents/` is byte-identical
(161/161 at the freeze — the prompt-artifact baseline is re-verified below).

---

## 1. What landed

| File | Change |
|---|---|
| `frontend/src/app/pages/metrics/metrics-range.ts` | **NEW** — the range/baseline domain: the option set (derived from the manifest), the keep-or-snap resolution, the request builder, the server-anchored midpoint split, the derived interval, the copy |
| `frontend/src/app/pages/metrics/metrics-baseline.ts` | **NEW** — the frame→renderer baseline CONTRACT (3.7) |
| `frontend/src/app/pages/metrics/tabs.manifest.ts` | `defaultWindowS` per bound tab (the snap target) |
| `frontend/src/app/pages/metrics/metrics.component.ts` / `.html` / `.scss` | the frame's range control + baseline toggle + the snap announcement + the fidelity disclosure |
| `frontend/src/app/pages/metrics/tabs/central-tab.component.ts` | the window/baseline inputs, the 2W request, the split, the request-generation discipline, the derived-interval meta |
| `frontend/src/app/pages/metrics/tabs/resource-tab.component.ts` | the panel's own labelled "Timeline range" control + the mapping guard + the retention-tier copy |
| `frontend/src/app/pages/metrics/tabs/{runtime,db-pool,jobs,instances,checkpoint,mcp}-tab.component.ts` | the three new inputs threaded through the six thin wrappers |
| `frontend/src/app/pages/metrics/metrics-state.ts` | `baselineSeries` + `baselineRefusal` on the per-tab state |
| `frontend/src/app/pages/metrics/charts/chart-panel.component.ts` | the `baseline` input (the Chart.js panel = the NORMAL path) |
| `frontend/src/app/models/resources.models.ts` | the resources ladder's RETENTION-TIER copy (see §5 deviation D-3) |
| `frontend/src/app/pages/metrics/metrics-range.spec.ts` | **NEW** — the domain spec (30 nodes) |
| `frontend/src/app/pages/metrics/metrics.baseline.pins.spec.ts` | **NEW** — the no-forged-cursor negative pin + the renderer contract (10 nodes) |
| `frontend/src/app/pages/metrics/tabs/resource-tab.component.spec.ts` | **NEW** — the custom slot spec (11 nodes) |
| `frontend/src/app/pages/metrics/metrics.component.spec.ts` | + the frame's range/baseline legs (44 → 58 nodes) |
| `frontend/src/app/pages/metrics/tabs/central-tab.component.spec.ts` | + the range/baseline legs (18 → 29 nodes) |
| `frontend/src/app/pages/metrics/tabs.manifest.spec.ts` | + the per-tab default window (30 → 33 nodes) |

---

## 2. The task-by-task map (3.1 – 3.12)

| Task | Where | The leg that pins it |
|---|---|---|
| **3.1** the union option set + the visible enforcement | `metrics-range.ts` (`METRICS_RANGE_OPTIONS`, `metricsRangeEnabledFor`), the frame's `<select>` | `metrics-range.spec.ts` (the union, ascending) + `metrics.component.spec.ts` ("a CENTRAL tab shows 15m/30m/1h/2h with 24h/7d DISABLED", "the CUSTOM tab shows 15m/1h/24h/7d with 30m/2h disabled") + `resource-tab.component.spec.ts` (the panel's own ladder) |
| **3.2** the keep-or-snap switch + the announcement | `resolveWindowOnTabChange` + `MetricsComponent.applyWindowForTab` / `metricsSnapNote` | `metrics-range.spec.ts` (both branches) + `metrics.component.spec.ts` ("KEEPS the window … and says NOTHING", "SNAPS … and ANNOUNCES the snap") |
| **3.3** the range→request plumbing | `MetricsCentralTabComponent.windowS` → `MetricsService.getSeries({names, windowS})`; the custom panel maps onto its own ladder (`resourcesWindow()`) | `central-tab.component.spec.ts` ("the frame's window reaches the door as `window_s`") + `resource-tab.component.spec.ts` (the mapping) |
| **3.4** the 2W fetch + the server-anchored split | `splitSeriesAtMidpoint` (the RESPONSE's `from_utc`/`to_utc` via the house `parseNaiveUtc`; **no clock is read**) | `metrics-range.spec.ts` (the midpoint maths, **the faked client-clock offset leaves the split UNCHANGED**) + `central-tab.component.spec.ts` (the two halves are published) |
| **3.5** the three request-time guards | (a) `applyBaselineRangeGuard` · (b) `metricsSeriesWindowS` (the 7200 ceiling) · (c) `splitSeriesAtMidpoint` returns `null` on a short span | (a) `metrics.component.spec.ts` ("crossing 1 h AUTO-CLEARS the baseline WITH its reason") · (b) `metrics-range.spec.ts` + `central-tab.component.spec.ts` ("the request NEVER carries a window above the substrate ceiling") · (c) `central-tab.component.spec.ts` ("a CLAMPED payload publishes NO baseline and says why") |
| **3.6** the custom slot's disabled toggle + the mapping refusal | the frame's toggle is disabled on the custom slot **with the plan's exact reason text**; `metricsResourcesWindow` refuses a central-only window | `metrics.component.spec.ts` ("the CUSTOM slot renders the toggle DISABLED with its reason") + `resource-tab.component.spec.ts` ("W = 2 h with the toggle forced on ⇒ NO over-ceiling request and NO baseline line") |
| **3.7** the renderer contract | `metrics-baseline.ts` (`METRICS_BASELINE_CONTRACT`) + the Chart.js panel's `baseline` input | `metrics.baseline.pins.spec.ts` (the contract's five clauses + the panel's declared input) |
| **3.8** the fidelity disclosure in the UI | `METRICS_BASELINE_FIDELITY_DISCLOSURE`, rendered next to the toggle | `metrics.component.spec.ts` ("the FIDELITY DISCLOSURE is visible while the baseline is on") + `metrics-range.spec.ts` (the three limits are named) |
| **3.9** no forged cursors | the metrics tree constructs no cursor; the pin scans for four minting shapes | `metrics.baseline.pins.spec.ts` (the scan + two planted-violation legs + the read-is-allowed leg) — **mutation-proven at the file level, §4.4** |
| **3.10** the request-generation discipline | `MetricsCentralTabComponent.requestGeneration` (bumped on every window/baseline change) + the in-flight generation guard | `central-tab.component.spec.ts` ("a window change issues EXACTLY ONE series request", "the baseline SHARES the request", "an in-flight response from the PREVIOUS generation is DISCARDED") |
| **3.11** the derived effective interval | `metricsDerivedIntervalS` (span ÷ point count) rendered by the tab's meta line | `central-tab.component.spec.ts` ("prints the DERIVED spacing at the 2 h option — NOT the door's observed cadence") |
| **3.12** the retention-tier copy | `RESOURCES_LADDER_RETENTION_TIERS_COPY` under the panel's control | `resource-tab.component.spec.ts` (the tiers are named; the tiers are never offered as options) |

---

## 3. The Exit Criterion, bullet by bullet

| Bullet | Verdict | Evidence |
|---|---|---|
| The frame renders a range control whose option set follows the selected tab, with the unsupported options visibly disabled; the custom tab's control is separately labelled and drives only its own panel | **MET** | §2/3.1; the frame's control is inert on the custom slot **with the reason** ("the Resources panel has its own range control") and the panel carries its own labelled "Timeline range" control (AM-19). The panel's control edits the frame's ONE value; the panel maps it onto ITS ladder (`resourcesWindow()`) — the frame's control never drives the custom door |
| Switching tabs keeps the window when supported and announces the snap when not | **MET** | both branches pinned; the snap note names the new window and the tab |
| The baseline works for W ≤ 1 h (2W fetch + server-anchored midpoint split) on the central tabs, and is disabled with a stated reason on the custom tab | **MET** | the 2W request is the tab's SINGLE request; the split is anchored on the response; the custom slot's toggle is disabled with the plan's exact reason |
| The three request-time guards hold | **MET** | §2/3.5 |
| The baseline shares the tab's single series request and an in-flight old-generation response is discarded | **MET** | §2/3.10 |
| The fidelity disclosure is visible next to the baseline control | **MET** | §2/3.8 |
| The negative "no forged cursors" pin is mutation-proven | **MET** | §4.4 — planted → **RED** → restored → **GREEN** |
| The meta line reports the derived effective spacing, not the door's observed cadence | **MET** | §2/3.11 (the 2 h leg asserts `30s` and the ABSENCE of `15s`) |
| The retention-tier copy names 48 h/14 d/180 d as tiers, with the corrected cites, and the picker never offers them | **MET** | §2/3.12; the corrected cites are re-read in §5 |
| The existing polling-hygiene specs stay green | **MET** | §4.1 — the retired `charts-tab`/`overview-tab` specs (the visibility gate, the supersede token, the skip-if-in-flight guard) are untouched and GREEN |
| E-1: the constants are re-read at this phase's freeze | **MET with ONE recorded discrepancy** | §5 D-1 — the `daemon/config.py:825-826` cite reads `metrics_sampler_interval_s` with default **10**, not 15 (the two 15 s fields are at `:805` and `:815`). The phase gates on **no** measured artifact figure, and the meta line derives its spacing from the payload, so the discrepancy is documentary, not behavioural |

---

## 4. The verification

### 4.1 The jest legs (the per-file census — mechanical, re-derived at the freeze)

```
cd $WT/frontend && CI=1 ./node_modules/.bin/jest --ci --silent --runInBand <file>
```

| Spec | Nodes | vs the phase-2 freeze |
|---|---|---|
| `pages/metrics/metrics-range.spec.ts` | **30** | **NEW** — the range/baseline domain |
| `pages/metrics/metrics.baseline.pins.spec.ts` | **10** | **NEW** — the no-forged-cursor pin + the renderer contract |
| `pages/metrics/metrics.component.spec.ts` | **58** | 44 → 58 (the range control, the tab switch, the baseline toggle) |
| `pages/metrics/tabs.manifest.spec.ts` | **33** | 30 → 33 (the per-tab default window) |
| `pages/metrics/tabs/central-tab.component.spec.ts` | **29** | 18 → 29 (the range→request plumbing, the split, the generation, the meta) |
| `pages/metrics/tabs/resource-tab.component.spec.ts` | **11** | **NEW** — the custom slot |
| `services/metrics.service.spec.ts` | 16 | unchanged |
| `models/metrics.model.spec.ts` | 32 | unchanged |
| **the phase-3 pack total** | **219 over 8 specs** | the phase-2 pack was 153 over 6 |

The scoped type-check is green (§4.2), the production build is green (§4.3) and the bundle guard
is **GREEN** — the phase-3 FE code adds **0 B** to the initial total (it lives in the lazy route
chunk) and adds **0** to the `anyComponentStyle` warning set.


### 4.2 The scoped type-check

```
cd $WT/frontend && timeout 300 ./node_modules/.bin/tsc --noEmit -p tsconfig.metrics-fe.json
→ exit 0, no output
```

### 4.3 The production build + the bundle guard

```
cd $WT/frontend && ./node_modules/.bin/ng build --configuration production 2>&1 | tee /tmp/fe-t1-p3-prod.log
cd $WT && node .agents/shared/planning/metrics-unification/fe-t1-build/bundle-guard.js \
    frontend/dist/frontend/browser /tmp/fe-t1-p3-prod.log
→ RESULT: GREEN (B1 + B2 + B3 + B6)
   B1  initial_total_bytes 5,909,053 vs the frozen 5,909,018 → Δ = 35 B (limit 2 kB)
   B2  0 `chart.js` markers in the 36 initial chunks
   B3  1 import site — dynamic 1, static 0, outside the metrics tree 0
   B6  the anyComponentStyle warning SET 9 == 9 (0 added, 0 removed)
```

### 4.4 The cursor pin's mutation proof (file level)

```
PLANTED: a client-minted cursor appended to frontend/src/app/pages/metrics/metrics-range.ts
  → jest src/app/pages/metrics/metrics.baseline.pins.spec.ts
    ✕ the metrics tree NEVER constructs a cursor
    Tests: 1 failed, 9 passed, 10 total          ← RED
RESTORED: the file was restored byte-for-byte (cp from a pre-plant copy)
  → jest src/app/pages/metrics/metrics.baseline.pins.spec.ts
    Tests: 10 passed, 10 total                   ← GREEN
```

### 4.5 The whole-tree jest leg (D2)

```
cd $WT/frontend && CI=1 ./node_modules/.bin/jest --ci --silent --runInBand
→ Test Suites: 4 failed, 137 passed, 141 total
  Tests:       9 failed, 4494 passed, 4503 total
```

**The 9 reds are BASE-PROVEN — zero lane attribution.** The same four specs fail identically at
the phase-3 BASE commit (`1474952fe`) in a throwaway detached worktree
(`git worktree add --detach /tmp/p3-base-wt 1474952fe`, `node_modules` symlinked from the lane
worktree, the worktree removed + pruned afterwards):

```
cd /tmp/p3-base-wt/frontend && CI=1 jest --ci --silent --runInBand \
    src/app/pages/settings/settings.component.spec.ts \
    src/app/components/schedule-create-dialog/schedule-create-dialog.component.spec.ts \
    src/app/models/jobs-grouping.model.spec.ts \
    src/app/models/jobs-filter-state.model.spec.ts
→ Test Suites: 4 failed, 4 total
  Tests:       9 failed, 201 passed, 210 total
  → the SAME 9 node names (the timezone labels, the job-status vocabulary, the timeAgo rows)
```

**Net-new deterministic reds = 0.** None of the four files is in this phase's diff, and the two
phase-2 reds (the date-dependent `America/New_York` labels in `settings.component.spec.ts`) are
the ones the phase-2 evidence already documented as base-proven.


---

## 5. The deviations and the interpretations (recorded, not silently taken)

**D-1 — the E-1 cite `daemon/config.py:825-826` does not carry the 15 s value.**
The plan's Exit Criterion cites that line for "the 15 s effective interval". The re-read at the
freeze:

```
825:    metrics_sampler_interval_s: int = Field(
826:        default=10,
```

The two 15 s fields are `metrics_scrape_interval_hint_s` (default 15, `:805`) and
`metrics_census_interval_s` (default 15, `:815`). The door's `interval_s` is **runtime-observed**
(`effective_interval_s(provider)`), not a config constant — which is exactly why 3.11 replaces it
with the payload-derived spacing. **Nothing in this phase depends on the constant**, so the
discrepancy is documentary. The other two cites re-read clean: `constants.py:33` →
`WINDOW_SECONDS: int = 7200`; `models/resources.models.ts:16` →
`export type ResourcesWindow = '15m' | '1h' | '24h' | '7d';`.

**D-2 — the baseline toggle lives in the FRAME, and the custom slot sees it disabled there.**
The phase objective puts "the two cross-cutting controls **above** the tabs", and 3.6 says "the
custom slot renders the baseline toggle DISABLED with its reason". The implementation renders the
toggle once, in the frame's toolbar: on the central tabs it is live, and on the custom slot it is
disabled **with the plan's exact reason text** and a `data-test` hook. The state is cleared when
the operator switches to the custom slot (the baseline is a central-tabs-only capability).

**D-3 — the retention-tier copy lives in `models/resources.models.ts`, not in the metrics tree.**
The plan's copy names the hour tier's cadence as `3600 s`; the word-bounded literal `3600` is
FORBIDDEN under `frontend/src/app/pages/metrics/` by phase 4's residue check (AM-23). The copy
therefore lives in the resources wire contract's own home (where the frozen ladder's type already
lives) and is imported by the panel. The copy text is the plan's, verbatim.

**D-4 — the window VALUE is one union value; the custom slot's ladder is its own control.**
"One control, one value, two vocabularies" (the plan's scope note) + phase 5's "the frame's
central-sourced control does **not** govern it" (AM-19) are reconciled as: the frame owns ONE
value; the frame's control is central-sourced and is **inert** on the custom slot (it says why);
the custom panel's own labelled control edits the same value and the panel maps it onto ITS
ladder. The alternative — a second value — would leave the frame's keep-or-snap rule with no
production-reachable snap branch.

**D-5 — the split's point count uses the payload's DENSEST series.**
`metricsDerivedIntervalS` divides the response's span by the largest point count in the payload
(the door downsamples every series to the same point budget). A panel with fewer points is spaced
more widely, and its own axis shows that. The 2 h leg (240 points over 2 h ⇒ 30 s) is unaffected.

**D-6 — the split's midpoint is INCLUSIVE of the boundary point.**
A point exactly at the midpoint belongs to the CURRENT half (`>= midpoint`). The choice is
documented in the code and is stable under the door's second-precision anchors.

---

## 6. The coupling notes for the later phases

- **Phase 4 (the renderer):** the baseline's DATA path is live — the tab state carries
  `baselineSeries` and the Chart.js panel declares the `baseline` input. Phase 4 draws it (the
  normal path) and gives the house SVG its fallback line; the mini role stays surface-free.
  The frame's window also replaces the retired `charts-tab`'s hardcoded `WINDOW_S = 3600`
  (phase 4's task 4.13 removes the literal).
- **Phase 5 (tab 1):** the panel's control is the phase-3 placeholder; phase 5 re-points the
  timeline onto `/api/resources/history` with the panel's own ladder. The phase-3 mapping guard
  (`resourcesWindow()`) is the seam that keeps every request inside the frozen union.
- **Phase 6 (the meta):** `metricsDerivedIntervalS` is the shared helper — phase 6's task 6.7
  re-uses it on the final panels rather than re-deriving the spacing.

---

## 7. The prompt-artifact baseline + the branch state

```
cd $WT && python3 scripts/generate_prompt_artifact_baseline.py --check   # house tool
→ PROMPT-BASELINE: OK (161/161)

cd $WT && git status --short
→ (empty — the freeze is committed; nothing pushed, merged, tagged or staged)
```

---

## 8. The B4 rider — the lazy-only route-fetch-set measurement (separate commit)

**The ruling** (the leader's, on evidence; recorded verbatim in `decisions.md` §"Implementation-time
rulings (2026-11-03, lane B4)" → IMPL-21): B4 part (2) measures the growth of the **LAZY-ONLY**
portion of the `/metrics` route fetch set (the route chunk + its direct static deps, restricted to
the members NOT in the initial set). **The threshold (65,536 B) is UNCHANGED**; the total stays
reported for transparency but is not the gate.

### 8.1 The tool (additive)

`bundle-baseline.measure.js` gained two fields — `metrics_route_fetch_set_lazy_only_bytes` and
`metrics_route_fetch_set_shared_with_initial_bytes`. No existing field's semantics changed; the
locator stays route-keyed and absent-tolerant; both new fields are `null` exactly when the route
chunk is unresolvable.

**The absent-tolerance, proven on a synthetic dist** (a route chunk that does not exist):

```
$ node .../bundle-baseline.measure.js /tmp/p3-noroute-dist
  "route_chunk_locator": null,
  "route_chunk_resolved_to_exactly_one": false,
  "metrics_route_chunk": null,
  "metrics_route_fetch_set_bytes": null,
  "metrics_route_fetch_set_lazy_only_bytes": null,          ← null, not a guess
  "metrics_route_fetch_set_shared_with_initial_bytes": null,
EXIT=0
```

**A pre-existing edge, disclosed and NOT changed:** on a dist with ZERO JavaScript files the tool
throws in `largest_lazy_bytes` (`size(undefined)`) — a pre-existing behaviour, untouched by this
rider (the rider's fields are correct in that case; the throw is in an unrelated field and a real
dist always has chunks).

### 8.2 The lane-base re-derivation — the REAL BUILD path

```
cd $WT
$ git worktree add --detach /tmp/p3-b4-base-wt 7961bcb21     # the artifact's measured_at_commit
$ ln -s $WT/frontend/node_modules /tmp/p3-b4-base-wt/frontend/node_modules
$ cd /tmp/p3-b4-base-wt/frontend && ./node_modules/.bin/ng build --configuration production
$ node $WT/.agents/shared/planning/metrics-unification/fe-t1-build/bundle-baseline.measure.js \
      /tmp/p3-b4-base-wt/frontend/dist/frontend/browser
```

```json
{
  "initial_total_bytes": 5909018,          ← the artifact's frozen baseline, EXACTLY
  "metrics_route_chunk": "chunk-QUI65QT3.js",
  "metrics_route_chunk_bytes": 27672,      ← the artifact's value, EXACTLY
  "metrics_route_fetch_set_bytes": 496338, ← the artifact's value, EXACTLY
  "metrics_route_fetch_set_lazy_only_bytes": 72708,        ← THE NEW BASELINE
  "metrics_route_fetch_set_shared_with_initial_bytes": 423630,
  "pre_retirement_resources_route_chunk_bytes": 38914      ← the artifact's value, EXACTLY
}
```

**The plan's analytic figure agrees digit for digit**: the G4b text (lane-base route chunk
27,672 B + exactly two lazy-only deps 43,688 B + 1,348 B) predicts 72,708 B — the real build
measures 72,708 B. The analytic fallback was therefore NOT needed.

**The phase-2 freeze re-measured the same way** (a second throwaway worktree at `1474952fe`):
route fetch set **591,467 B** · shared **506,881 B** · lazy-only **84,586 B** — the rider's
numbers, reproduced from the real tree.

**The cleanup (proven):**

```
$ rm -f /tmp/p3-b4-*/frontend/node_modules
$ git worktree remove --force /tmp/p3-b4-base-wt && git worktree remove --force /tmp/p3-b4-p2-wt
$ git worktree prune
$ git worktree list | grep -c p3-b4        → 0
$ ls -d /tmp/p3-b4-base-wt /tmp/p3-b4-p2-wt → No such file or directory (both)
$ git status --short                      → only the rider's own files
```

The shared checkout `/home/nea/ensemble-src` was never touched (the worktrees were detached
additions to THIS repo, removed with `git worktree remove`).

### 8.3 The current tree (the phase-3 freeze)

```
$ cd $WT/frontend && node ../.agents/shared/planning/metrics-unification/fe-t1-build/bundle-baseline.measure.js dist/frontend/browser
  "metrics_route_fetch_set_bytes": 603420,
  "metrics_route_fetch_set_lazy_only_bytes": 96539,        ← the NEW field
  "metrics_route_fetch_set_shared_with_initial_bytes": 506881,  ← the NEW field
```

**B4 part (2) on the lazy-only basis: 96,539 − 72,708 = +23,831 B ≤ 65,536 B → GREEN.**
(On the old TOTAL basis the same tree would read +107,082 B — the false breach the ruling
describes.)

### 8.4 The recorded baseline

`bundle-baseline.json` → `guards.B4_new_chart_chunk_size` gained
`baseline_metrics_route_fetch_set_lazy_only_bytes: 72708` and
`baseline_metrics_route_fetch_set_shared_with_initial_bytes: 423630`, plus a `provenance` note
naming the ruling, the date and the derivation path. **No existing value was altered**, and the
bundle guard still reads the artifact and reports **GREEN** (§4.3).

---

## 9. The residue check this phase must not redden (phase 4's task 4.13)

Phase 4 pins `grep -rnE "\b3600\b" frontend/src/app/pages/metrics/ frontend/src/app/components/charts/`
and requires **zero hits** outside its allowlist row (`charts-tab.component.spec.ts`).

**This phase contributes ZERO hits.** The window seconds are DERIVED from the ladder
(`METRICS_CENTRAL_WINDOW_MINUTES['1h'] * 60`) — in the production code AND in the specs (the
phase-3 fix commit `dc58b5b6a` replaced the spec's literal with the same derived form).

```
$ grep -rnE "\b3600\b" frontend/src/app/pages/metrics/ frontend/src/app/components/charts/
  frontend/src/app/pages/metrics/charts/charts-tab.component.spec.ts:5,118,120,130,134   ← the allowlist row
  frontend/src/app/pages/metrics/charts/charts-tab.component.ts:41,183,184              ← PRE-EXISTING
  frontend/src/app/pages/metrics/metrics-door.ts:23                                      ← PRE-EXISTING
```

The four remaining non-allowlist hits are **phase-2's / pre-lane's** (the retired charts tab's
hardcoded window and its two comments, plus one comment in `metrics-door.ts`) — **not this
phase's**. Phase 4's own task 4.13 removes the hardcoded `WINDOW_S = 3600`; the three comments
ride with it. **Routed to phase 4, not fixed here** (they are outside this phase's diff).

---

## 10. The freeze state

```
$ git log --oneline -6
dc58b5b6a fix: derive the window literals so phase 4's residue check stays clean (wi-32a99618)
cfe8d9553 docs: measure the B4 route fetch set on its lazy-only portion (wi-32a99618)
451f6282f docs: record the phase-3 evidence and register the phase-3 FE packs (wi-32a99618)
b0b1a1bcb feat: add the metrics range control and the server-anchored baseline (wi-32a99618)
1474952fe test: assert every central tab fetches its bound families (wi-32a99618)

$ git status --short
→ (empty)

$ git diff --stat 1474952fe HEAD -- agents/
→ (empty — the agents/ tree is byte-identical)

$ git diff --stat 1474952fe HEAD
→ 28 files changed, 2919 insertions(+), 30 deletions(-)
```

The whole-tree jest at the freeze: **141 suites · 4503 collected · 4494 passed · 9 failed** —
the same 9 base-proven reds (§4.5), **net-new = 0**. The polling-hygiene legs of the retired
tabs are green (`charts-tab` + `overview-tab` 22/22 — the visibility gate, the supersede token,
the skip-if-in-flight guard).


