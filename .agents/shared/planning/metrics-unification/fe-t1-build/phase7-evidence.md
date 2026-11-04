# Phase 7 evidence — Route fate (D-3): retire `/resources` + param-preserving redirect

Lane: **FE-only T1 metrics dashboard** (`feature/fe-metrics-dashboard`) ·
Base: `a4f58db50` (Phases 0–6 + 8 landed) · Freeze: the phase-7 commits below.

Plan: `phase7-plan.md` (tasks 7.1–7.9 + the Exit Criterion).

---

## 0. What shipped (the diff)

```
$ git diff --stat HEAD
 docs/runbooks/resource-cost-control.md             |  42 ++++---
 .../resource-hygiene-operator-checklist.md         |  18 ++-
 frontend/e2e/README.md                             |   2 +-
 frontend/e2e/metrics-gate.spec.ts                  |  99 +++++++++++-----
 frontend/e2e/resources-gate.spec.ts                | 130 +++++++++++++------
 frontend/playwright.resources-gate.config.ts       |   9 +-
 frontend/src/app/app.routes.ts                     | 137 +++++++++++++++++++--
 frontend/src/app/app.ts                            |  21 +--
 frontend/src/app/pages/mcps-gateway/mcps-gateway.component.spec.ts | 2 +-
 frontend/src/app/pages/mcps-gateway/mcps-gateway.pins.spec.ts      | 21 +--
 frontend/src/app/pages/metrics/metrics-gear-menu.spec.ts |  44 ++++---
 frontend/src/app/pages/resources/resources-gear-menu.spec.ts      |  94 --------------
 frontend/src/app/pages/resources/resources.component.html          |  18 ---
 frontend/src/app/pages/resources/resources.component.scss          |  90 -----
 frontend/src/app/pages/resources/resources.component.spec.ts       | 135 -----------
 frontend/src/app/pages/resources/resources.component.ts           |  49 ---
 frontend/tsconfig.metrics-fe.json                  |   1 +
 17 files changed, 393 insertions(+), 519 deletions(-)
```

**ONE RECORDED DEVIATION** (the plan's literal mechanism is not expressible in
Angular — see §1.0) and **NO** other deviation.

---

## 1.0 THE RECORDED DEVIATION — Angular forbids a `canMatch`-only route

The plan (task 7.1) says: *"Replace the `/resources` route … with a **`canMatch`
guard returning a `UrlTree`**"*. Angular's route validator rejects that shape:

```
$ grep -n "redirectTo and\|One of the following must be provided" \
    frontend/node_modules/@angular/router/fesm2022/_router-chunk.mjs
2705:        throw new _RuntimeError(4014, `Invalid configuration of route '${fullPath}': redirectTo and ${route.canMatch ? 'canMatch' : 'canActivate'} cannot be used together.` + `Redirects happen before guards are executed.`);
2714:    if (route.redirectTo === void 0 && !route.component && !route.loadComponent && !route.children && !route.loadChildren) {
2715:      throw new _RuntimeError(4014, `Invalid configuration of route '${fullPath}'. One of the following must be provided: component, loadComponent, redirectTo, children or loadChildren`);
```

A route MUST name something to activate, and `redirectTo` + `canMatch` is
EXPLICITLY forbidden ("Redirects happen before guards are executed"). The
first implementation (`{ path: 'resources', canMatch: [resourcesRedirectGuard] }`)
died at router construction:

```
NG04014: Invalid configuration of route 'resources'. One of the following must be provided: component, loadComponent, redirectTo, children or loadChildren
```

**The adaptation (minimal, and it keeps the plan's load-bearing mechanism):**
the route keeps the `canMatch` guard — the redirect's MECHANISM (AM-3) is
unchanged — and its `loadComponent` fallback points at **the redirect's own
target**, the metrics page:

```ts
{
  path: 'resources',
  canMatch: [resourcesRedirectGuard],
  loadComponent: () => import('./pages/metrics/metrics.component').then(m => m.MetricsComponent),
  title: 'Metrics',
},
```

Why this is safe:

* the guard ALWAYS returns a `UrlTree`, so the route never activates;
* Angular resolves `loadComponent` in the **PREACTIVATION** phase — *after* the
  guards (`_router-chunk.mjs:3845-3863` sits in the preactivation `switchTap`;
  the canMatch runs during RECOGNITION) — so a retired URL never loads the
  chunk twice, and the only load is the one the redirect target needs anyway;
* the fallback is the REAL page, so even a hypothetical `true` from the guard
  would render the metrics page, not a blank.

**The plan's acceptance is unaffected** — the redirect's mechanism, its
param preservation, its precedence rule, its null-fallback and its
end-to-end legs are exactly as specified.

---

## 1. Task 7.1 — the redirect

`frontend/src/app/app.routes.ts` (the guard + the route):

* `RESOURCES_REDIRECT_TARGET = '/metrics'`;
* `RESOURCES_REDIRECT_TAB_ID = 'resource'` — **a literal on purpose** (task 7.2);
* `resourcesRedirectGuard: CanMatchFn` — reads
  `router.getCurrentNavigation()?.extractedUrl.queryParams` (the NAMED mechanism,
  AM-3), builds the `UrlTree` with every incoming param EXCEPT `tab`, and
  writes its own `tab=resource`;
* the EXPLICIT NULL-FALLBACK: with no current navigation it LOGS
  (`console.warn`) and degrades to a param-less redirect — never a crash, never
  a silent param loss.

**Command + output:**

```
$ cd $WT/frontend && CI=1 ./node_modules/.bin/jest --ci --silent --runInBand \
    src/app/app.resources-retirement.spec.ts
PASS src/app/app.resources-retirement.spec.ts
Test Suites: 1 passed, 1 total
Tests:       22 passed, 22 total
```

The two END-TO-END legs (W-2) assert the LANDED URL's **PARSED**
`queryParams` on the PRODUCTION route table (no hand-built `UrlTree`, no
raw-string comparison):

| leg | input | landed (parsed) |
|---|---|---|
| (i) | `/resources?window=24h&x=1` | path `/metrics`, `{window:'24h', x:'1', tab:'resource'}` |
| (ii) | `/resources?tab=charts` | path `/metrics`, `tab === 'resource'` (the incoming `tab` is OVERRIDDEN) |
| (iii) | `/resources` | path `/metrics`, `{tab:'resource'}` |

**"A SINGLE navigation settles"** is asserted: the spec counts the
`NavigationEnd` events and expects exactly **1** (`settled[0].urlAfterRedirects`
=== the landed URL) — the redirect does not loop (the target path differs).

**The null-fallback proof** (the guard invoked outside any navigation):

```ts
const warn = jest.spyOn(console, 'warn').mockImplementation(() => undefined);
TestBed.runInInjectionContext(() => {
  const result: unknown = resourcesRedirectGuard({ path: 'resources' } as never, []);
  tree = result instanceof UrlTree ? result : undefined;
});
expect(warn).toHaveBeenCalled();
expect(String(warn.mock.calls[0]?.[0])).toMatch(/no current navigation/i);
expect(tree).toBeInstanceOf(UrlTree);
expect(tree?.queryParams).toEqual({ tab: 'resource' });
```

GREEN: the guard logged, degraded to `{tab:'resource'}`, and did not throw.

---

## 2. Task 7.2 — the redirect's target id vs the manifest

* `RESOURCES_REDIRECT_TAB_ID === METRICS_TABS[0].id` → `'resource'` ✓
* `metricsTabById(RESOURCES_REDIRECT_TAB_ID)` is not null ✓
* **NON-VACUITY**: the redirect's id is a LITERAL — `app.routes.ts` does NOT
  import the metrics manifest and does NOT reference
  `METRICS_DEFAULT_TAB_ID`; the spec pins
  `export const RESOURCES_REDIRECT_TAB_ID = 'resource';` literally. Without this
  the first assertion would be vacuously true (a manifest rename would follow
  the redirect instead of failing the spec).

---

## 3. Task 7.3 — the gear menu

`frontend/src/app/app.ts` — the `Resources` entry (and its comment block) is
REMOVED; `Metrics` remains the single entry for these surfaces.

```
$ grep -n "Resources" frontend/src/app/app.ts
593:    // (D-3 — T1 phase 7, task 7.3): the standalone `/resources` route
594:    // is retired and the resource module is tab 1 of this page. This
595:    // entry is therefore the SINGLE gear-menu entry for those surfaces.
```

(the only remaining mention is the comment that explains the removal — there is
no `{ label: 'Resources' … }` entry and no `route: '/resources'`.)

## 4. Task 7.4 — the retired page's assets

```
$ git rm -- frontend/src/app/pages/resources/resources.component.ts \
    frontend/src/app/pages/resources/resources.component.html \
    frontend/src/app/pages/resources/resources.component.scss \
    frontend/src/app/pages/resources/resources.component.spec.ts \
    frontend/src/app/pages/resources/resources-gear-menu.spec.ts
rm 'frontend/src/app/pages/resources/resources-gear-menu.spec.ts'
rm 'frontend/src/app/pages/resources/resources.component.html'
rm 'frontend/src/app/pages/resources/resources.component.scss'
rm 'frontend/src/app/pages/resources/resources.component.spec.ts'
rm 'frontend/src/app/pages/resources/resources.component.ts'

$ ls frontend/src/app/pages/resources
ls: cannot access 'frontend/src/app/pages/resources': No such file or directory
```

**The directory is GONE.** The retired shell's per-instance-attribution
subtitle (*"Daemon memory, CPU and per-instance attribution — live snapshot and
history"*) died with it, and the RELOCATED live card still carries the honest
label — both are pinned:

* the subtitle appears NOWHERE under `frontend/` (the acceptance's own needle
  excluded);
* `pages/metrics/resource/live/live-tab.component.html` still contains
  `per-instance attribution, which it does not deliver` (the honest note that
  replaces the retired promise).

**The source-grep pins at `resources.component.spec.ts:23-25` retired WITH the
file they pin** (the file is deleted).

## 5. Task 7.5 — `metrics-gear-menu.spec.ts`

TWO assertions removed, each with its reason recorded IN the file's header
(no assertion silently deleted):

| the removed assertion | the reason | the replacement |
|---|---|---|
| `resourcesIdx` (the `Resources` entry EXISTS) | the entry no longer exists — the surface is tab 1 of Metrics | `has NO Resources gear entry` (asserts the ABSENCE) |
| "Resources still before the appends" (the ordering pin) | the entry is gone, so the pin has no subject | the same absence assertion + the surviving `Metrics` ordering pin (`MCP Gateway → Metrics → appends`), UNCHANGED |

## 6. Task 7.6 — the widened repo sweep (AM-22)

**The command (paste-executed, never prose):**

```
$ cd $WT && grep -rn "/resources" frontend/ docs/ --exclude-dir=node_modules --exclude-dir=dist \
    --include="*.ts" --include="*.html" --include="*.scss" --include="*.json" --include="*.mjs" --include="*.md"
```

**141 hits** (the base sweep was 139; the +2 net are the metrics gate's four
new `/api/resources/*` stubs and the removed resources-page hits). **EVERY hit
is classified** — the full per-hit table is `/tmp/p7-sweep-classified.txt`
(141 lines, one per hit, each with its class):

| class | count | what it is |
|---|---|---|
| (a) the intended redirect target | 30 | the redirect itself (its route + its doc, 5) · the e2e REDIRECT PROBE (1) · the ACCEPTANCE's own probes + needles (20) · a NEGATIVE guard whose string IS the absence assertion (1) · prose notices about the retirement (3) |
| (b) the `/api/resources/*` endpoints (which STAY) | 108 | the API surface incl. the backend router path `daemon/routers/resources.py` (77) · the relocated module's own file names (`models/resources.models`, `services/resources.service`) (28) · the gate's own IDENTITY (its `testMatch` + its `/tmp/resources-gate-evidence` output dir) (3) |
| (c) a stale reference to the retired route | **0** | — |
| (d) an operator doc still pointing at the retired route | 3 | the RETIREMENT NOTICES in the two runbooks — each one points at the NEW route and announces the retirement (`RETIRED` / `redirect` within the notice block) |

**ZERO class-(c) hits remain.** The two pins that enforce it are mechanical
and live in the acceptance:

* the FE sweep: every quote-delimited `/resources` occurrence in `frontend/`
  (code with the comments stripped) must be a negative guard, the redirect
  probe or the acceptance itself — everything else is a class-(c) offender;
* the docs sweep: every quote-delimited `/resources` occurrence in `docs/`
  must sit inside a RETIREMENT-notice block (the word `RETIRED` or `redirect`
  within the preceding 5 lines) — an operator instruction pointing at the
  retired route is a class-(c) offender.

The class-(c)/(d) boundary is what the plan drew: the runbooks' three hits are
NOT operator instructions pointing at the retired route — they are the notices
that say the route is retired and name the NEW one.

## 7. Task 7.7 — the Playwright gate

**RE-POINTED** (not retired): the gate still gates the resource module, at its
new home.

* `frontend/playwright.resources-gate.config.ts` — identity kept (the routed-out
  register's item (n) names this artifact); its header now says the gate drives
  the resource MODULE at `/metrics?tab=resource`.
* `frontend/e2e/resources-gate.spec.ts` — the three legs are re-pointed:
  * the gear-menu leg clicks **`Metrics`** (and asserts the retired
    `Resources` gear entry is ABSENT), then asserts the 7 manifest tabs and
    the resource module's three sections;
  * the deep-link leg opens **`/metrics?tab=resource`**;
  * the degraded-200 leg opens **`/metrics?tab=resource`**;
  * the three metrics-door routes are now stubbed too (the page that hosts the
    module needs its own door) with the frozen envelope shapes and an empty
    family list — the central door stays quiet while the resource tab stays lit.

**The acceptance covers the SPEC, not only the config:**

* **NO Playwright CONFIG targets the retired route** — every
  `frontend/playwright*.config.ts` is scanned (comments stripped) and must not
  contain a quote-delimited `/resources` ✓;
* **NO Playwright SPEC drives the retired route AS A PAGE** — no e2e spec
  asserts the retired route as a landing URL (`toHaveURL(.../resources...)` ⇒
  **0** hits) ✓;
* the ONE e2e spec that names the retired route is `metrics-gate.spec.ts` — the
  **redirect probe** (task 7.9), which names it as the redirect's INPUT and
  asserts the `/metrics` landing ✓.

> **The plan's own tension, resolved and disclosed.** Task 7.9 REQUIRES the
> redirect leg in an e2e spec, and a redirect leg must name the retired route.
> The exit bullet ("no Playwright config OR spec drives the retired route") is
> therefore read as **"drives it AS A PAGE"** — the probe drives the REDIRECT,
> not the page. The pins above encode exactly that reading, and the probe is
> pinned to assert the `/metrics` landing. Recorded here rather than guessed.

## 8. Task 7.8 — the operator runbooks

Both runbooks now name the NEW route, and the retired route appears in them
ONLY inside the retirement notice:

* `docs/runbooks/resource-cost-control.md` — §1's heading is now *"Reading the
  resource surface"*, with a ROUTE-CHANGE notice (the old URL redirects and
  carries its query params over), the page described as `/metrics?tab=resource`
  (tab 1 of the metrics page, the gear menu's `Metrics` entry), the live strip
  / timeline panel / anomalies panel re-described as the flat column, and the
  `/api/resources/*` endpoints marked UNCHANGED. The (a)-item's "tunable once
  real headroom is observed" now names the resource tab.
* `docs/runbooks/resource-hygiene-operator-checklist.md` — a ROUTE-CHANGE
  notice at the top ("the `/api/resources/*` endpoints are UNCHANGED — every
  `curl` below still works exactly as written"), and the two operator
  instructions that pointed at the API surface now name the resource tab
  (`/metrics?tab=resource`) WITH the API endpoint beside it.

**A doc grep finds no operator instruction pointing at `/resources`** — the
three remaining mentions are the notices, and each names the new route.

## 9. Task 7.9 — the e2e deep-link expectations + the redirect leg

`frontend/e2e/metrics-gate.spec.ts` — the shell is manifest-driven since
phase 2 (the retired Overview/Charts/Families trio is GONE), so the tab-shape
expectations were re-pointed:

* the 3 retired tabs → the manifest's **7** tabs (Resources, Runtime, DB Pool,
  Jobs / Queue, Instances, Checkpoint, MCP), each asserted visible;
* the resource module is tab 1 and is the DEFAULT selection;
* the "Charts" leg → the **DB Pool** central tab (the stub's F4 is live and
  bound to it): one live family, two panels;
* the "Families" tab → the **family drawer** (`metrics-catalog-drawer-toggle`
  → `families-table`);
* the deep-link leg → `/metrics?tab=resource`;
* **THE REDIRECT LEG (new):** `page.goto('/resources?window=24h&x=1')` ⇒ the
  landed URL is asserted on the **PARSED** params (`pathname === '/metrics'`,
  `tab === 'resource'`, `window === '24h'`, `x === '1'`) and the resource
  module is visible.

### 9.1 TWO defects the REAL run exposed (both FIXED here)

The Playwright gates were **executed** in this phase (they are Phase 9's
*release* run, but running them here is what proved the re-point). The first
metrics-gate run was **3 passed / 1 failed** and the failure was REAL, not
cosmetic:

1. **The metrics gate's stub set was missing the RESOURCE module's own
   endpoints.** The resource module is tab 1 — the page's DEFAULT tab — so the
   page renders it on every visit, and the catch-all's `{}` body (not a shape
   the resources door ever serves) made the live strip throw
   `TypeError: Cannot read properties of undefined (reading 'pid')` during
   change detection. **It did not fail an assertion** — which is exactly why
   the stub set has to be right. **FIX:** the four resource request classes
   (`/live` + `/self-cost`, `/history`, `/anomalies`) are now stubbed with
   contract-shaped bodies. The re-run logs **0** browser-console ERRORs, **0**
   pageerrors, **0** requestfailed.
2. **The tick count was a PER-GRID figure in the retired Charts tab and a
   PER-PANEL figure in a central tab.** The first run measured **10** (2
   panels × 5 ticks) where the retired grid asserted 5. **FIX:** the
   assertion now states the STRUCTURE (one `[data-test="chart-panel-ticks"]`
   group per panel ⇒ 2) and the total (10), so the number is explained rather
   than merely moved.
3. A third fix fell out of the same run: the drawer's `family-live` /
   `family-dark` / `data-reason` assertions were **unscoped** and hit the
   central tab BEHIND the drawer (a strict-mode violation on the reason
   locator). **FIX:** they are scoped to the drawer's own table — stronger, and
   no longer able to pass on the wrong surface.

**Disclosed for the lane's owner (NOT a phase-7 fix):** the live strip's
template reads `payload.daemon.pid` UNGUARDED, while its sibling block
(`payload.cgroup?.memory?…`) IS optional-chained. The §2.8 contract always
serves the `daemon` block (with nullable SCALARS), so this is not reachable
through the real door — but it is a robustness asymmetry worth the owner's
attention. It was NOT changed here (out of this phase's scope; a drive-by
product edit is not this lane's business).

### 9.2 THE GATES, RUN

```
$ cd $WT/frontend && ./node_modules/.bin/playwright test --config=playwright.resources-gate.config.ts
Running 3 tests using 1 worker
  3 passed (35.2s)
PW_RC=0

$ cd $WT/frontend && ./node_modules/.bin/playwright test --config=playwright.metrics-gate.config.ts
Running 4 tests using 1 worker
  4 passed (38.1s)
PW_RC=0
```

Real Chromium, the FE dev server started/stopped by Playwright's own
`webServer` lifecycle, **every** API call stubbed by `page.route` — **zero
daemon, zero DB, zero live port** (the dev server's proxy target `:8079` was
never reached; the live daemon on `:9797` was never contacted).

---

## 10. The Exit Criterion, bullet by bullet

| bullet | evidence |
|---|---|
| `/resources` → `/metrics?tab=resource` with every param preserved (except `tab`), on the END-TO-END **parsed** `queryParams` (both legs) | §1 — the acceptance's legs (i) + (ii) — GREEN (22/22) |
| the null-fallback path (no current navigation) — logs, degrades to a param-less redirect, does not crash | §1 — the null-fallback proof — GREEN |
| `frontend/src/app/pages/resources/` is GONE | §4 — `ls` ⇒ *No such file or directory*; pinned by the acceptance |
| the gear menu has no `Resources` entry; the metrics entry remains | §3 — pinned by the acceptance AND by `metrics-gear-menu.spec.ts` |
| the repo-wide sweep recorded with a per-hit classification; ZERO stale-route references and ZERO stale operator instructions | §6 — 137 hits, all classified, **0** class-(c) |
| no Playwright config OR spec drives the retired route; the gear-menu leg re-pointed | §7 — the config scan (0 hits), the `toHaveURL` scan (0 hits), the gear-menu leg re-pointed to `Metrics` |
| the e2e specs are green against the stubbed server; the stubbed-server gate still passes | §9.2 — **BOTH** Playwright gates RUN and PASS: the resources gate **3 passed (35.2s)** and the metrics gate **4 passed (38.1s)** (real Chromium, every API call stubbed, zero daemon/DB/live port) |
| the bundle guard re-runs GREEN at the freeze | §12 |
| E-1 — no MEASURED figure gates this phase; the structural inputs re-verified at lane base and re-read at the freeze | §13 |
| `agents/` 161/161; branch clean; nothing pushed/merged/tagged/staged | §14 |

---

## 11. The test runs

### 11.1 The phase's own pack (the acceptance)

```
$ cd $WT/frontend && CI=1 ./node_modules/.bin/jest --ci --silent --runInBand \
    src/app/app.resources-retirement.spec.ts
PASS src/app/app.resources-retirement.spec.ts
Test Suites: 1 passed, 1 total
Tests:       22 passed, 22 total
```

### 11.2 The AFFECTED existing specs

```
$ cd $WT/frontend && CI=1 ./node_modules/.bin/jest --ci --silent --runInBand \
    src/app/app.routes.spec.ts src/app/app.component.spec.ts src/app/app.persona-nav.spec.ts \
    src/app/app.resources-retirement.spec.ts \
    src/app/pages/metrics/metrics-gear-menu.spec.ts \
    src/app/pages/mcps-gateway/mcps-gateway.pins.spec.ts \
    src/app/pages/mcps-gateway/mcps-gateway.component.spec.ts \
    src/app/pages/metrics/metrics.e2e-retirement.spec.ts \
    src/app/pages/metrics/metrics.bindings.pins.spec.ts
Test Suites: 9 passed, 9 total
Tests:       170 passed, 170 total
```

### 11.3 The whole tree (D2 leg)

```
$ cd $WT/frontend && CI=1 ./node_modules/.bin/jest --ci --silent --runInBand
Test Suites: 4 failed, 149 passed, 153 total
Tests:       9 failed, 4701 passed, 4710 total
```

**The failing suites (verbatim) — the SAME four the phase-8 freeze recorded:**

```
FAIL src/app/components/schedule-create-dialog/schedule-create-dialog.component.spec.ts
FAIL src/app/models/jobs-filter-state.model.spec.ts
FAIL src/app/models/jobs-grouping.model.spec.ts
FAIL src/app/pages/settings/settings.component.spec.ts
```

**The 9 reds are BASE-PROVEN.** The phase-8 freeze (the lane's immediately
preceding freeze, same branch) recorded *"4702 collected / 4693 passed / 9
failed — the 9 reds are BASE-PROVEN: the identical four specs
(`settings.component`, `jobs-filter-state.model`, `jobs-grouping.model`,
`schedule-create-dialog`) fail with the IDENTICAL 9 failing NAMES at the
phase-8 base `4e0c82b8b` … The failing-NAME diff base→head is EMPTY in BOTH
directions ⇒ net-new deterministic reds = 0"* (`.agents/tester/PACKS.md`, the
`t1_fe_phase8_whole_tree` row). This phase touches NONE of those four files,
the failing-suite count is the same **4** and the failing-test count the same
**9** ⇒ **net-new deterministic reds = 0** and none fixed.

**The collected count moved 4702 → 4710**: `+22` (this phase's acceptance) and
`−14` (the two deleted resources specs: `resources.component.spec.ts` +
`resources-gear-menu.spec.ts`).

### 11.4 The type-checks

```
$ cd $WT/frontend && ./node_modules/.bin/tsc --noEmit -p tsconfig.app.json
(0 errors)

$ cd $WT/frontend && ./node_modules/.bin/tsc -p tsconfig.metrics-fe.json --noEmit
(0 errors)
```

`tsconfig.metrics-fe.json` gained ONE include line
(`src/app/app.resources-retirement.spec.ts`) so this phase's acceptance is
inside the lane's scoped type-check — the file lives at `src/app/`, outside the
`src/app/pages/metrics/**` glob. (The whole-spec-tree config
`tsconfig.spec.json` is NOT usable as a gate: `src/app/app.component.spec.ts`
has a PRE-EXISTING stray `});` at its line 2127 — present at the phase-8 base
`4e0c82b8b` too, verified with `git show 4e0c82b8b:frontend/src/app/app.component.spec.ts`.)

---

## 12. The bundle guard at the phase-7 freeze

```
$ cd $WT/frontend && ./node_modules/.bin/ng build --configuration production 2>&1 | tee /tmp/fe-t1-p7-prod.log
…
Output location: /home/nea/ensemble-src-wt-fe-metrics-dashboard/frontend/dist/frontend
BUILD_RC=0

$ cd $WT/frontend && node ../.agents/shared/planning/metrics-unification/fe-t1-build/bundle-guard.js \
    dist/frontend/browser /tmp/fe-t1-p7-prod.log
=== T1 bundle guard (phase 1, task 1.9) ===
baseline_artifact: .agents/shared/planning/metrics-unification/fe-t1-build/bundle-baseline.json

(a) B1 — the numeric initial-total delta
    initial_total_bytes = 5907717
    baseline_bytes      = 5909018
    delta               = -1301 B (limit |2048| B)
    initial_file_count  = 36
    measurement_tool    = 5907717 (cross-check)

(b) B2 — the chart.js marker scan over the initial set
    initial_chunks_scanned = 36
    marker_hits            = 0

(c) B3 — the import shape + import site
    chart_js_import_sites  = 1
    static_sites           = 0
    outside_metrics_tree   = 0
      src/app/pages/metrics/charts/chart-panel.component.ts: dynamic=2 static=0

(d) B6 — the anyComponentStyle warning SET (not a count)
    baseline_set_size = 9
    measured_set_size = 9
    added   = 0
    removed = 0

RESULT: GREEN (B1 + B2 + B3 + B6)
GUARD_RC=0
```

**GREEN.** B1 `|Δ| = 1,301 B` BELOW the frozen 5,909,018 B (limit 2 kB) — the
phase-8 freeze sat 1,707 B below, so the **+406 B** is this phase's own
eager delta: the redirect guard + its constants live in `app.routes.ts`, which
is in the INITIAL set (the deleted page shell was a LAZY chunk and cost the
initial set nothing). B2 0 markers in the 36 initial chunks · B3 1 import site
(dynamic only, 0 static, 0 outside `src/app/pages/metrics/**`) · B6 the warning
SET 9 == 9 (0 added / 0 removed).

### 12.1 The measure tool

```
$ cd $WT/frontend && node ../.agents/shared/planning/metrics-unification/fe-t1-build/bundle-baseline.measure.js \
    dist/frontend/browser
{
  "initial_total_bytes": 5907717,
  "initial_file_count": 36,
  "error_budget_bytes": 6000000,
  "headroom_bytes": 92283,
  "index_html_bytes": 9257,
  "whole_initial_payload_bytes": 5916974,
  "lazy_chunk_count": 52,
  "lazy_bytes_total": 1963784,
  "route_chunk_locator": "route-key",
  "route_chunk_key": "ensemble-metrics-route-v1",
  "route_chunk_resolved_to_exactly_one": true,
  "metrics_route_chunk": "chunk-4ECAPKNS.js",
  "metrics_route_chunk_bytes": 120154,
  "metrics_route_fetch_set_bytes": 682399,
  "metrics_route_fetch_set_lazy_only_bytes": 165185,
  "metrics_route_fetch_set_shared_with_initial_bytes": 517214,
  "largest_lazy": "chunk-TXUCW6LI.js",
  "largest_lazy_bytes": 206857,
  "second_largest_lazy": "chunk-UZSIQFDK.js",
  "second_largest_lazy_bytes": 157173,
  "pre_retirement_resources_route_chunk": "chunk-4ECAPKNS.js",
  "pre_retirement_resources_route_chunk_bytes": 120154
}
```

* **The route-key resolution** ✓ — `route_chunk_locator: "route-key"`,
  `route_chunk_resolved_to_exactly_one: true`; the metrics route chunk is
  `chunk-4ECAPKNS.js` (120,154 B — it now carries the RESOURCE module too,
  since the retired shell's content merged into it).
* **The B4 lazy-only figure (for the record)** — `165,185 B` (the phase-5
  freeze: 166,022 B; the phase-4 freeze: 114,290 B). It is **over** the
  65,536 B allowance, exactly as it was at the phase-5 freeze. **NOT a gate
  here** — the threshold is a Phase-9 caller adjudication, and no code was
  contorted to move it.
* ⚠️ **`pre_retirement_resources_route_chunk` is NOT null — it reports the
  METRICS chunk.** The plan expected "NULL/absent after the retirement (the
  tool is absent-tolerant)". Reality: the field resolves by the LEGACY
  CONTENT MARKER `anomaly-marker`, and that string moved into the metrics
  chunk when the anomaly panel moved into the metrics tree in phase 5. The
  tool's own header warns about exactly this ("it must NEVER be mis-resolved
  as the metrics chunk"). **The retired route chunk itself DOES NOT SURVIVE
  the retirement** — proved directly on the built output:

  ```
  $ cd $WT/frontend/dist/frontend/browser
  $ grep -l "anomaly-marker" *.js
  chunk-4ECAPKNS.js
  $ grep -l "ensemble-metrics-route-v1" *.js
  chunk-4ECAPKNS.js
  $ grep -l "resources-subtitle\|resources-container" *.js || echo "(NONE — the retired shell's markers are gone)"
  (NONE — the retired shell's markers are gone)
  ```

  So the FIELD's value is a mis-resolution of the metrics chunk under a
  pre-retirement name; the underlying FACT it was meant to report (the retired
  route chunk) is correctly absent. **The field is NOT a gate** (A6's
  scope-note: "that field is a PRE-RETIREMENT baseline ONLY — it is EXCLUDED
  from the Phase-9 gate"), the guard is GREEN, and **the measure tool was NOT
  modified** — it is Phase 9's artifact and the instruction for this phase was
  to run it and REPORT. **ROUTED to the Phase-9 caller** as a disclosed
  finding: either the tool's legacy-marker locator should exclude the
  route-keyed chunk, or the field should be renamed/re-scoped.


## 13. E-1 — this phase gates on NO measured figure

The phase's inputs are STRUCTURAL facts, re-verified at lane base and re-read
at this freeze:

| the structural input | lane base (`a4f58db50`) | at this freeze |
|---|---|---|
| the `/resources` route | a lazy `loadComponent` route over `pages/resources/resources.component` | a `canMatch` guard over the redirect + the metrics-page fallback |
| the gear-menu entries | `Resources` present, `Metrics` present | `Resources` GONE, `Metrics` present |
| the e2e specs | `resources-gate.spec.ts` drove `/resources`; `metrics-gate.spec.ts` asserted the retired 3-tab shape | both re-pointed; the redirect probe added |
| the retired page directory | `frontend/src/app/pages/resources/` — 5 files | GONE |

## 14. The fences

```
$ cd $WT && /home/nea/ensemble-src/.venv/bin/python scripts/protection/generate_prompt_artifact_baseline.py \
    --check --tree $WT
PROMPT-BASELINE: OK — 161 versioned artifact(s) match .prompt-artifacts.sha256

$ git diff --stat HEAD -- agents/
(empty — the agents/ tree is byte-identical)
```

* **No daemon, no HTTP, no database, no live port** — this phase is FE-only
  and ran no server.
* **NO `daemon/` edit, NO migration, NO `scripts/upgrade` edit.** The two
  `docs/runbooks/` edits are the plan's own task 7.8 scope.
* **No new npm package**; `frontend/package.json` untouched; the `chart.js`
  import form untouched.
* **Nothing pushed, merged, tagged or staged** — the phase's commits sit on
  `feature/fe-metrics-dashboard`.

---

## 15. Disclosures

1. **The recorded deviation** (§1.0) — Angular's route validator forbids a
   `canMatch`-only route and forbids `redirectTo` + `canMatch`. The redirect's
   MECHANISM (AM-3) is exactly as planned; the route additionally carries the
   redirect's own target as its (never-reached) fallback.
2. **The exit-bullet reading** (§7) — task 7.9 requires a redirect leg in an
   e2e spec; the exit bullet's "no Playwright spec drives the retired route"
   is read as "drives it AS A PAGE". The probe is pinned to assert the
   `/metrics` landing, and the `toHaveURL` scan is 0.
3. **The Playwright gates WERE executed in this phase** (§9.2) — they are
   Phase 9's *release* run, but running them here is what proved the
   re-point, and it exposed THREE real defects in the metrics gate's stub
   set and assertions (§9.1), all FIXED here. The first run was 3 passed / 1
   failed; the final run is 4 passed. The live strip's unguarded
   `payload.daemon.*` reads are DISCLOSED to the lane's owner (not reachable
   through the real door — the §2.8 contract always serves the block).
4. **Two assertions were removed** from `metrics-gear-menu.spec.ts` and
   **one** from `mcps-gateway.pins.spec.ts` — each with its reason recorded in
   the file's own header (§5, and the gateway's re-point in the same table).
   The gateway's ordering pin was re-pointed from the deleted `Resources`
   anchor to the nearest surviving one (`Snapshots`), NOT weakened.
5. **`tsconfig.metrics-fe.json` gained one include line** (§11.4) so this
   phase's acceptance is inside the lane's scoped type-check.
