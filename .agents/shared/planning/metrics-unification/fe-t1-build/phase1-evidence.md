# Phase 1 evidence — Chart.js v4 + the same-commit governance amendment (D-1)

**Lane:** `feature/fe-metrics-dashboard` · **Task:** wi-32a99618 · **Phase:** 1 of 9
**Worktree:** `/home/nea/ensemble-src-wt-fe-metrics-dashboard`
**Authority:** `.agents/shared/planning/metrics-unification/fe-t1-build/phase1-plan.md`
(tasks 1.1–1.9 + the Exit Criterion)

Every figure below was produced by the named command, and the command's
**actual output** is reproduced verbatim. A label without a command is not a
measurement (E-1).

---

## 1.6 — The measured npm facts

### The two direct entries (task 1.1)

`frontend/package.json` gained **exactly two** direct entries — the diff is
reproduced in §"the pack leg" below (the pack asserts it BY NAME).

### `npm view` — VERBATIM

```
$ npm view chart.js@^4.6.1 version dist.unpackedSize license dependencies
version = '4.6.1'
dist.unpackedSize = 6227928
license = 'MIT'
dependencies = { '@kurkle/color': '^0.3.0' }

$ npm view jest-canvas-mock@^2.5.8 version dist.unpackedSize license dependencies
version = '2.5.8'
dist.unpackedSize = 114094
license = 'MIT'
dependencies = { cssfontparser: '^1.2.1', 'moo-color': '^1.0.2' }

$ npm view @kurkle/color version dist.unpackedSize license
version = '0.6.5'
dist.unpackedSize = 200996
license = 'MIT'

$ npm view cssfontparser version dist.unpackedSize license
version = '1.2.1'
license = 'MIT'

$ npm view moo-color version dist.unpackedSize license
version = '2.0.0'
dist.unpackedSize = 255519
license = 'MIT'
```

### The RESOLVED closure (`npm ls`)

```
$ npm ls --depth=0 | grep -E "chart\.js|jest-canvas-mock|frontend@"
frontend@0.0.0 /home/nea/ensemble-src-wt-fe-metrics-dashboard/frontend
├── chart.js@4.6.1
├── jest-canvas-mock@2.5.8
```

### The LOCKFILE closure (the three transitive packages)

```
$ node -e "…package-lock.json packages[]…"
node_modules/chart.js => 4.6.1 license=MIT
node_modules/jest-canvas-mock => 2.5.8 license=MIT
node_modules/@kurkle/color => 0.3.4 license=MIT
node_modules/cssfontparser => 1.2.1 license=MIT
node_modules/moo-color => 1.0.3 license=MIT
```

**The tree-shaken controller set's real min+gz is NOT measured here** — it is
Phase 9's task 9.8(d). This phase records the **metadata** only (version,
unpacked size, dep closure, license), as the task requires.

---

## E-1 re-derivation — the two MEASURED figures this phase gates on

### G1/G2 — the initial-total baseline (the guard's B1)

```
$ cd frontend
$ ./node_modules/.bin/ng build --configuration production      # exit 0
$ node ../.agents/shared/planning/metrics-unification/fe-t1-build/bundle-baseline.measure.js dist/frontend/browser
{
  "initial_total_bytes": 5909018,
  "initial_file_count": 36,
  "error_budget_bytes": 6000000,
  "headroom_bytes": 90982,
  "index_html_bytes": 9257,
  "whole_initial_payload_bytes": 5918275,
  "lazy_chunk_count": 53,
  "lazy_bytes_total": 1702774,
  "route_chunk_locator": "legacy-content-marker",
  "route_chunk_key": "ensemble-metrics-route-v1",
  "route_chunk_resolved_to_exactly_one": true,
  "metrics_route_chunk": "chunk-TLUSWWAH.js",
  "metrics_route_chunk_bytes": 27614,
  "metrics_route_fetch_set_bytes": 496498,
  "largest_lazy": "chunk-QS2K3YLH.js",
  "largest_lazy_bytes": 157141,
  "second_largest_lazy": "chunk-HNKUMJNH.js",
  "second_largest_lazy_bytes": 135690,
  "pre_retirement_resources_route_chunk": "chunk-WDASKBPV.js",
  "pre_retirement_resources_route_chunk_bytes": 38895
}
```

**`initial_total_bytes = 5,909,018` — byte-identical to the frozen lane-base
baseline.** The gate figure (B1's delta) is therefore **0 B**.

**Two DRIFTS vs the frozen artifact** (both expected; neither is a Phase-1
gate — G4/G4b are Phase 9's):

| Figure | Frozen | Measured here | Why |
|---|---|---|---|
| `lazy_chunk_count` | 52 | **53** | the extracted `chart-theme.ts` is a new shared lazy module |
| `lazy_bytes_total` | 1,702,633 | **1,702,774** | the same (+141 B) |
| `metrics_route_chunk_bytes` | 27,672 | 27,614 | the route chunk's content changed (the theme import) ⇒ the hash and the minified size shift |
| `metrics_route_fetch_set_bytes` | 496,338 | 496,498 | the same shift (+160 B) |

### G10 — the `anyComponentStyle` warning SET (the guard's B6)

```
$ grep -E "scss exceeded maximum budget" /tmp/fe-t1-build-prod.log | sed 's/^▲ \[WARNING\] //'
src/app/components/instance-list/instance-list.scss exceeded maximum budget. Budget 8.00 kB was not met by 2.17 kB with a total of 10.17 kB.
src/app/pages/jobs/jobs.component.scss exceeded maximum budget. Budget 8.00 kB was not met by 6.77 kB with a total of 14.77 kB.
src/app/components/agent-selector/agent-selector.scss exceeded maximum budget. Budget 8.00 kB was not met by 765 bytes with a total of 8.77 kB.
src/app/pages/snapshots/snapshots.component.scss exceeded maximum budget. Budget 8.00 kB was not met by 4.29 kB with a total of 12.29 kB.
src/app/pages/blueprint/blueprint.component.scss exceeded maximum budget. Budget 8.00 kB was not met by 10.32 kB with a total of 18.32 kB.
src/app/pages/maintenance/checkpoint-cleanup/checkpoint-cleanup.component.scss exceeded maximum budget. Budget 8.00 kB was not met by 701 bytes with a total of 8.70 kB.
src/app/components/snapshot-detail-drawer/snapshot-detail-drawer.component.scss exceeded maximum budget. Budget 8.00 kB was not met by 697 bytes with a total of 8.70 kB.
src/app/components/add-source-modal/add-source-modal.scss exceeded maximum budget. Budget 8.00 kB was not met by 318 bytes with a total of 8.32 kB.
src/app/components/chat-interface/chat-interface.scss exceeded maximum budget. Budget 8.00 kB was not met by 8.15 kB with a total of 16.15 kB.
```

**NINE paths** (C-3) — the census matches `bundle-baseline.json` EXACTLY
(0 added, 0 removed). `blueprint.component.scss` (18.32 kB, the largest
offender) is in the set.

---

## 1.9 — The bundle guard

The guard is a **node tool in the lane's plan dir** (a production build is
not a jest leg):
`.agents/shared/planning/metrics-unification/fe-t1-build/bundle-guard.js`.
It reads the **named baseline artifact** `bundle-baseline.json` (B8) and
hard-codes **no** numeric baseline of its own.

```
$ node .agents/shared/planning/metrics-unification/fe-t1-build/bundle-guard.js \
      frontend/dist/frontend/browser /tmp/fe-t1-build-prod.log
```

### GREEN at this commit

```
(a) B1 — the numeric initial-total delta
    initial_total_bytes = 5909018
    baseline_bytes      = 5909018
    delta               = 0 B (limit |2048| B)
    initial_file_count  = 36
    measurement_tool    = 5909018 (cross-check)

(b) B2 — the chart.js marker scan over the initial set
    initial_chunks_scanned = 36
    marker_hits            = 0

(c) B3 — the import shape + import site
    chart_js_import_sites  = 1
    static_sites           = 0
    outside_metrics_tree   = 0
      src/app/pages/metrics/charts/chart-panel.component.ts: dynamic=1 static=0

(d) B6 — the anyComponentStyle warning SET (not a count)
    baseline_set_size = 9
    measured_set_size = 9
    added   = 0
    removed = 0

RESULT: GREEN (B1 + B2 + B3 + B6)      # exit 0
```

### Planted variant (i) — an EAGER import (in the initial set)

Planted: `import { Chart } from 'chart.js';` in `frontend/src/main.ts` (the
entry point — the import is in the builder's `initial` closure).

**The build ITSELF breaks** — the `initial` **error** budget trips, exactly as
the plan predicts (the headroom is 90,982 B and the vendor is far larger):

```
$ ./node_modules/.bin/ng build --configuration production
✘ [ERROR] bundle initial exceeded maximum budget. Budget 6.00 MB was not met by 31.21 kB with a total of 6.03 MB.
BUILD_EXIT=1
```

(A failed build leaves no dist, so the guard was re-run against a dist built
with the `initial` error budget temporarily raised to 10MB — a TRANSIENT
`angular.json` edit, restored before the commit — so the numeric leg could be
measured rather than merely inferred.)

```
(a) B1 — the numeric initial-total delta
    initial_total_bytes = 6031213
    baseline_bytes      = 5909018
    delta               = 122195 B (limit |2048| B)
    measurement_tool    = 6031213 (cross-check)
(b) B2 — the chart.js marker scan over the initial set
    marker_hits            = 0
(c) B3 — the import shape + import site
    chart_js_import_sites  = 2
    static_sites           = 1
    outside_metrics_tree   = 1
      src/app/pages/metrics/charts/chart-panel.component.ts: dynamic=1 static=0
      src/main.ts: dynamic=0 static=1
RESULT: RED (3 violation(s))
  - B1 initial-total delta 122195 B exceeds |2048| B (measured 6031213, baseline 5909018)
  - B3 1 STATIC chart.js import site(s): src/main.ts
  - B3 1 chart.js import site(s) outside src/app/pages/metrics/**: src/main.ts
```

**FINDING (for the reviewer — B2 is a WEAK guard):** the eager leak was
caught by **B1 (the numeric delta) and B3 (the import shape)**, but **B2's
marker scan reported 0 hits** — the MINIFIED production output carries **no
literal `chart.js` string** (esbuild strips module paths, and the vendor's
`sideEffects` list excludes `dist/chart.js`). B2 is therefore NOT a reliable
detector on its own; B1 + B3 are the effective guards. B2 is kept exactly as
the artifact specifies it (the method is pinned by the plan), and this finding
is routed to the reviewer rather than silently "fixed".

### Planted variant (ii) — a STATIC import inside the LAZY metrics tree (C-1)

Planted: `import { Chart } from 'chart.js';` in
`frontend/src/app/pages/metrics/charts/charts-tab.component.ts` (a file in the
lazily-loaded `/metrics` route tree).

```
$ ./node_modules/.bin/ng build --configuration production      # exit 0 — the build STAYS GREEN
```

```
(a) B1 — the numeric initial-total delta
    initial_total_bytes = 5909018
    baseline_bytes      = 5909018
    delta               = 0 B (limit |2048| B)
    measurement_tool    = 5909018 (cross-check)
(b) B2 — the chart.js marker scan over the initial set
    marker_hits            = 0
(c) B3 — the import shape + import site
    chart_js_import_sites  = 2
    static_sites           = 1
    outside_metrics_tree   = 0
      src/app/pages/metrics/charts/chart-panel.component.ts: dynamic=1 static=0
      src/app/pages/metrics/charts/charts-tab.component.ts: dynamic=0 static=1
RESULT: RED (1 violation(s))
  - B3 1 STATIC chart.js import site(s): src/app/pages/metrics/charts/charts-tab.component.ts
```

**This is the C-1 case exactly:** the lazy leak slips through **B1** (the
initial total does not move — lazy chunks are counted by no budget) **and
through B2** (no marker in any initial chunk), and is caught **only by B3**.
The build itself stays green, which is why the guard has to exist.

Both planted variants were **restored**; the final state is the GREEN run
above (re-built and re-measured after the restore).

---

## 1.2 — The pins spec's planted-violation legs

Command (each leg): `cd frontend && ./node_modules/.bin/jest --silent
src/app/pages/metrics/metrics.bindings.pins.spec.ts` → **22 nodes**.

| # | Planted violation | Result | Restored |
|---|---|---|---|
| (i) | `import { Chart } from 'chart.js';` in `charts-tab.component.ts` | **RED** — 2 failed / 20 passed (`has NO static chart.js import statement anywhere in the FE tree` + `reaches the vendor only through the DYNAMIC import form`) | GREEN 22/22 |
| (ii) | `import dayjs from 'dayjs';` in `charts-tab.component.ts` | **RED** — 1 failed / 21 passed (`the D2 zero-dependency guard` — the per-ban scope survives the withdrawal of the `chart.js` ban) | GREEN 22/22 |
| (iii) | the read re-pointed at the Phase-4 consolidated renderer (`components/charts/house-chart/house-chart.component.ts` — a path that does not exist yet) | **RED** — 1 failed / 21 passed, `ENOENT` on the stale read path | GREEN 22/22 |
| (iv) | the stub's OWN doc comment naming ``from 'chart.js'`` (the state at the freeze) | **GREEN** — 22/22. A naive `not.toContain('chart.js')` scan would have gone RED on the same source (`naive_not_toContain_chart.js => RED (would fail)`) — the pin is statement-anchored, so prose cannot redden it | n/a (the shipped state) |
| (v) | `import { scaleLinear } from 'd3-scale';` in `metrics.service.ts` | **RED** — 1 failed / 21 passed (the D2 ban list) | GREEN 22/22 |

---

## 1.3 — The pack leg (the expected-dependency-set guard)

### The amended pack, run on the branch

```
$ EXPECTED_BRANCH=feature/fe-metrics-dashboard BASE_REF=c4161335b406b884d1de6c8e7bf4b3d1f7fe2abe \
  METRICS_FE_LOG_DIR=/tmp/metrics-fe-gate-t1 \
  timeout 1500 bash test/packs/metrics_fe_dual_side_test.sh

d1_EXIT=0
d1_COUNTS: Tests:       174 passed, 174 total
d2_declared_EXIT=0
d2_declared_COUNTS: Tests:       138 passed, 138 total
d2_derived_EXIT=1
d2_derived_COUNTS: Tests:       2 failed, 1799 passed, 1801 total
typecheck_scoped_EXIT=0
build_development_EXIT=0
build_production_EXIT=0

--- package_json_dependency_diff (D-1 expected-dependency-set guard) ---
expected_dependencies=["chart.js"]
expected_devDependencies=["jest-canvas-mock"]
package_json_dependency_set={
  "dependencies":    { "added": ["chart.js"],        "removed": [], "changed": [] },
  "devDependencies": { "added": ["jest-canvas-mock"], "removed": [], "changed": [] },
  "expected": { "dependencies": ["chart.js"], "devDependencies": ["jest-canvas-mock"] },
  "ok": true
}

RESULT: FAIL (failed legs: 1)
PACK_EXIT=1
```

**The ONE failing leg is `d2_derived`, and both of its failures are
BASE-PROVEN — zero lane attribution.** They are
`src/app/pages/settings/settings.component.spec.ts`'s **date-dependent**
timezone labels: `America/New_York` is on STANDARD time in November ⇒
`UTC-05:00`, while the spec hard-codes the summer `UTC-04:00`.

```
$ git worktree add --detach /tmp/fe-t1-base-check c4161335b   # the lane base
$ cd /tmp/fe-t1-base-check/frontend && ./node_modules/.bin/jest --silent \
      src/app/pages/settings/settings.component.spec.ts
  ● Timezone preference (user-timezone-setting) › renders the timezone section …
    Expected value: "America/New_York (UTC-04:00)"
    Received array: ["Auto / not set", "Asia/Bangkok (UTC+07:00)", "America/New_York (UTC-05:00)", "UTC (UTC+00:00)"]
  ● Timezone preference (user-timezone-setting) › uses the API list …
Test Suites: 1 failed, 1 total
Tests:       2 failed, 117 passed, 119 total
```

The same 2 failures, at the lane base, with the lane's changes ABSENT. The
file is not touched by this lane (`git status --short frontend/src/app/pages/settings/`
is empty). **A base-debt item, routed — NOT fixed in this phase** (the
phase is FE-only and the fix belongs to the settings lane).

### The planted-violation proof (a THIRD direct dependency)

Planted: `"zod": "^3.23.8"` added to `frontend/package.json`'s `dependencies`
(a transient edit — restored afterwards).

```
--- package_json_dependency_diff (D-1 expected-dependency-set guard) ---
package_json_dependency_set={
  "dependencies":    { "added": ["chart.js", "zod"], "removed": [], "changed": [] },
  "devDependencies": { "added": ["jest-canvas-mock"], "removed": [], "changed": [] },
  "expected": { "dependencies": ["chart.js"], "devDependencies": ["jest-canvas-mock"] },
  "ok": false
}
RESULT: FAIL (D-1 DEPENDENCY-SET BREACH — the FE dependency delta is not exactly the expected set)

RESULT: FAIL (failed legs: 2)
PACK_EXIT=1
```

The **assertion is by NAME** (the set is compared as a set of names, never
as a line count) — so a transitive bump cannot break it, and a third direct
dependency does.

### The D1 census (mechanical, per file — the number registered in PACKS.md)

```
$ ./node_modules/.bin/jest --ci --silent --runInBand --verbose <the 11 D1 files>
  30  src/app/models/metrics.model.spec.ts
   9  src/app/services/metrics.service.spec.ts
  29  src/app/components/charts/svg-line-chart/svg-line-chart.component.spec.ts
  26  src/app/pages/metrics/metrics.component.spec.ts
   9  src/app/pages/metrics/metrics-gear-menu.spec.ts
  22  src/app/pages/metrics/metrics.bindings.pins.spec.ts
  11  src/app/pages/metrics/metrics.bundle-guard.spec.ts        (NEW)
  13  src/app/pages/metrics/overview/overview-tab.component.spec.ts
   9  src/app/pages/metrics/charts/charts-tab.component.spec.ts
   9  src/app/pages/metrics/charts/chart-panel.component.spec.ts  (NEW)
   7  src/app/pages/metrics/families/families-tab.component.spec.ts
TOTAL 174
```

---

## 1.4 — The governance amendment (the zero-dep claim)

### The repo-wide grep — the ACTUAL output

```
$ grep -rnE "zero (new )?(FE|frontend|npm)[ -]?dep|no new (frontend|npm) dependency|npm dependency delta is (EMPTY|empty)|adds no new frontend dependency|Zero FE deps|no npm dependency|FE .package.json. empty|does \*\*not\*\* add a charting dependency" --include=*.md . | grep -v node_modules
```

The plan's acceptance ("the grep returns only the historical CHANGELOG line
+ the three sites explicitly marked historical") is **NOT satisfiable by a
naive repo-wide grep**: the planning corpus legitimately QUOTES the claim
(the decision documents that ASKED for the amendment quote the phrase they
amended). The output, with every hit classified:

| Class | Hits | Treatment |
|---|---|---|
| **the plan's three "record-historical" sites** | `metrics-agent-tools/stage-prep-checklist.md:28` · `central-metrics-v1/stage-prep-checklist.md:102` · `central-metrics-v1/phase6-plan.md:25` | **marked HISTORICAL** (the plan's sites (i)(ii)(iii)) |
| **the CHANGELOG line** | `CHANGELOG.md:141` | **left UNAMENDED** — the historical record (the plan's instruction) |
| **the shipped docs' claim family** | `metrics-promote-disclosure.md:114` · `:122` · `:158` · `metrics-query-contract.md:413` | **marked HISTORICAL** (site (v) + the three sibling claims the plan's census missed) |
| **the shipped FE doc's claim** | `metrics-fe-charts.md:128` · `:167` | **AMENDED** (sites (iv) + (vi) + the sibling claim the census missed) |
| **past-lane planning records** (what those lanes did) | `central-metrics-v1/plan-overview.md:130` · `:569` · `phase5-plan.md:11` · `fold-ledger.md:220` · `metrics-agent-tools/evidence-pack.md:170` · `phase6-plan.md:23` | **left as they are** — they are the record of a lane that genuinely added no npm dependency; rewriting them would falsify that record (the same principle the plan applies to `CHANGELOG.md:141`) |
| **the T1 lane's own decision / analysis documents** | `charting-engine-decision.md:20` · `:41` · `resource-refactor-architecture.md:345` · `:390` · `fe-metrics-source-audit.md:179` · `fe-t1-build/{decisions.md:30, phase1-plan.md:8, phase1-plan.md:33, architecture-review.md:426, plan-review.md:84, fold-audit.md:99, research-findings.md:230}` | **left as they are** — they DESCRIBE the amendment ("these sites must be amended"), they do not assert the claim |
| **an unrelated lane's plan** | `agent-prompt-immutability/plan-overview.md:39` ("zero new npm deps") | a false positive of the pattern — a different lane's plan, untouched |

### The machine-checkable acceptance (the LIVE surfaces)

Every surface that can be read as a LIVE claim now carries a HISTORICAL
marker (or is the CHANGELOG):

```
$ for f in <the six live surfaces + CHANGELOG.md>; do
    n=$(grep -cE "<the same pattern>" "$f"); h=$(grep -c "HISTORICAL" "$f")
    echo "$f  hits=$n  historical_markers=$h"; done
docs/architecture/metrics-fe-charts.md  hits=0  historical_markers=1
docs/architecture/metrics-promote-disclosure.md  hits=3  historical_markers=3
docs/architecture/metrics-query-contract.md  hits=1  historical_markers=1
.agents/shared/planning/metrics-agent-tools/stage-prep-checklist.md  hits=1  historical_markers=2
.agents/shared/planning/central-metrics-v1/stage-prep-checklist.md  hits=1  historical_markers=1
.agents/shared/planning/central-metrics-v1/phase6-plan.md  hits=1  historical_markers=1
CHANGELOG.md  hits=1  historical_markers=0
```

**No live surface asserts the zero-dep claim any more** — the only surface
without a HISTORICAL marker is `CHANGELOG.md`, which the plan leaves
unamended on purpose.

---

## The whole FE suite — net-new failures = 0 (BASE-PROVEN)

```
$ cd frontend && ./node_modules/.bin/tsc --noEmit -p tsconfig.app.json
TSC_APP_EXIT=0

$ ./node_modules/.bin/jest --ci --silent --runInBand
Test Suites: 4 failed, 132 passed, 136 total
Tests:       9 failed, 4334 passed, 4343 total
```

The **9 failures in 4 suites** are **BASE-PROVEN** — the identical 9 failures
occur at the lane base `c4161335b` in a throwaway detached worktree with this
lane's changes ABSENT:

```
$ git worktree add --detach /tmp/fe-t1-base-check c4161335b
$ cd /tmp/fe-t1-base-check/frontend && ./node_modules/.bin/jest --ci --silent --runInBand \
    src/app/pages/settings/settings.component.spec.ts \
    src/app/components/schedule-create-dialog/schedule-create-dialog.component.spec.ts \
    src/app/models/jobs-grouping.model.spec.ts \
    src/app/models/jobs-filter-state.model.spec.ts
  ● settings.component.spec.ts  › … the native picker …   (2 — the date-dependent timezone label)
  ● schedule-create-dialog.component.spec.ts › timezone picker source chain …  (3 — the same cause)
  ● jobs-grouping.model.spec.ts › … (3)
  ● jobs-filter-state.model.spec.ts › … (1)
Test Suites: 4 failed, 4 total
Tests:       9 failed, 201 passed, 210 total
```

**The lane's net-new FE failures = 0.** The cause of the timezone family is
the calendar (November ⇒ `America/New_York` is on STANDARD time ⇒
`UTC-05:00`, while the specs hard-code the summer `UTC-04:00`); the
jobs-grouping / jobs-filter-state failures are the same base debt family.
**Not fixed here** (the phase is FE-only and the fix belongs to the owning
lanes) — routed.

---

## The deviations (each with its reason)

1. **The plan's task 1.2(a) PROSE says "the four remaining bans"; the ARRAY it
   specifies has FIVE entries.** Implemented the ARRAY
   (`['d3-', "from 'd3'", 'ngx-charts', 'moment', 'dayjs']`) and the
   **five-source** scope. Reported as the plan's prose slip.

2. **The house SVG engine LEAVES the ban-scan set at this phase.** Task
   1.2(b) re-points the spec's fourth read at the wrapper stub, because the
   house SVG engine is consolidated away in Phase 4 (the read would crash the
   spec at merge). The engine is still covered by the **positive import-shape
   pin**, which scans the whole FE tree — only the `d3-` / `ngx-charts` /
   `moment` / `dayjs` bans no longer cover it until Phase 4 deletes it.

3. **`chart-theme.ts` carries the PALETTE + the `chartSeriesColor` helper +
   the no-value token — not a unit/format vocabulary.** The two sites the plan
   cites (`charts-tab.component.ts:37-45`, `svg-timeline.component.ts:169-173`)
   contain **palette data only**; the chart sites' formatters are NOT
   byte-identical (`formatChartUtc` guards a non-finite input with `—`,
   `formatUtcClock` does not), so merging them would be a behaviour change,
   which this phase forbids. The metric-kind → render-mode / unit-group
   vocabulary is Phase-4 material (IMPL-10) and does not exist yet.

4. **THREE shipped-doc zero-dep claims beyond the plan's six sites** were
   marked historical as well: `docs/architecture/metrics-promote-disclosure.md:114`
   and `:158`, and `docs/architecture/metrics-query-contract.md:413` — plus
   `docs/architecture/metrics-fe-charts.md:167` ("It does **not** add a
   charting dependency…"), which the plan's site list missed. Leaving them
   would have left FALSE statements in shipped documentation. The plan's
   census (six, AM-24) is under-counting the shipped docs by four.

5. **The bundle guard's home is the lane's PLAN DIR**, next to the
   measurement tool (`bundle-baseline.measure.js`), and it is a **node tool**,
   not a jest leg — a production build is not a jest fixture. The constraint
   "no file under `test/packs/` other than `metrics_fe_dual_side_test.sh`"
   rules out a new pack script, and the shipped tree must not carry a
   dev-only tool. Its **contract** is pinned by a jest spec
   (`frontend/src/app/pages/metrics/metrics.bundle-guard.spec.ts`, 11 nodes),
   so the guard cannot rot silently.

6. **The guard hardens one thing beyond the plan's text:** a build that FAILED
   (the eager-leak case) leaves no dist, and the guard now reports that as a
   VIOLATION instead of crashing with an uncaught `ENOENT`.
