# Phase 8 evidence — empty state + honesty (D-2)

**Lane:** FE-only T1 metrics dashboard (`wi-32a99618`), branch
`feature/fe-metrics-dashboard`.
**Worktree:** `/home/nea/ensemble-src-wt-fe-metrics-dashboard`.
**Phase base:** `4e0c82b8b` (Phases 0–5 landed) — the tree this phase
built on.
**Plan:** `.agents/shared/planning/metrics-unification/fe-t1-build/phase8-plan.md`
(tasks 8.1–8.12 + the Exit Criterion), with `decisions.md` IMPL-09 /
IMPL-18 / IMPL-19 and AM-1 / AM-4 / AM-20 / AM-21.
**Fence:** FE-only — `frontend/` + `.agents/` planning/evidence. No
`daemon/`, no migrations, no `scripts/upgrade`, no `agents/` change, no
live daemon / HTTP / DB, no new npm package.

---

## 0. The commits (seven, all `type: summary`)

```
$ cd /home/nea/ensemble-src-wt-fe-metrics-dashboard && git log --oneline --format='%h %s' 4e0c82b8b..HEAD
14b143c9f test(fe): make the phase-8 specs strict-mode clean under the scoped type-check
c899371ec test(fe): make the dark-family ceiling pin discriminating (the slice-before-filter variant)
e1b71d467 feat(fe): reflow the door-state banner so the 900 px collision cannot recur (8.6)
79892f345 feat(fe): mark where the panel's data ends and pin the drawer affordances (8.9, 8.10)
6566c0111 feat(fe): surface the FE's own selector truncation and keep dark families off the ceiling (8.7, 8.8)
b67d0ba13 feat(fe): render the door state once with an actionable panel and a page-scoped posture chip (8.2-8.5)
f7b88f56c feat(fe): widen the door-state table to the closed ten-class union and make the copy structured (8.1, 8.11, 8.12)
```

(The `6.x` commits interleaved between them are **Phase 6's** — see §9.1.)

`git show --stat` per commit:

| commit | files | +/- |
|---|---|---|
| `f7b88f56c` | `models/metrics.model.ts`, `models/metrics.model.spec.ts`, `charts/charts-tab.component.ts`, `overview/overview-tab.component.ts` | 537 / 38 |
| `b67d0ba13` | `metrics-door-state.component.ts` (new), `metrics.door-state.spec.ts` (new), `metrics.component.{ts,html,scss,spec.ts}`, `tabs/central-tab.component.{ts,spec.ts}`, `families/families-tab.component.ts` | 775 / 85 |
| `6566c0111` | `tabs/central-tab.component.{ts,spec.ts}` | 121 / 6 |
| `79892f345` | `charts/data-end-overlay-plugin.ts` (new), `charts/data-end-overlay-plugin.spec.ts` (new), `charts/chart-panel.component.ts`, `charts/chart-panel.renderer.ts`, `components/charts/renderer-registry.ts`, `tabs/central-tab.component.{ts,spec.ts}`, `families/families-tab.component.spec.ts` | 473 / 1 |
| `e1b71d467` | `metrics-door-state.component.ts`, `metrics.banner-geometry.spec.ts` (new) | 239 / 0 |
| `c899371ec` | `tabs/central-tab.component.spec.ts` | 5 / 1 |
| `14b143c9f` | 5 spec files (strict-mode) | 30 / 10 |

---

## 1. The TEN-CLASS CLOSED UNION (8.1) — with the per-class Retry policy

**Structural facts re-read at the freeze (E-1 — no measured figures):**

```
$ grep -n "export type MetricsDoorErrorCode" -A 13 frontend/src/app/models/metrics.model.ts
385:export type MetricsDoorErrorCode =
386-  | 'auth_required'
387-  | 'auth_not_configured'
388-  | 'scope_insufficient'
389-  | 'forbidden_other'
390-  | 'unknown_family'
391-  | 'bad_request'
392-  | 'registry_unavailable'
393-  | 'network'
394-  | 'timeout'
395-  | 'unexpected';

$ grep -n "export const METRICS_DOOR_STATE_COPY\|export const METRICS_DOOR_ERROR_COPY\|export function decodeMetricsDoorError" frontend/src/app/models/metrics.model.ts
488:export const METRICS_DOOR_STATE_COPY: Readonly<
576:export const METRICS_DOOR_ERROR_COPY: Readonly<Record<MetricsDoorErrorCode, string>> =
655:export function decodeMetricsDoorError(err: unknown): {

$ grep -n "status === 403\|status === 401\|status === 404\|status === 400\|status === 503\|status === 0" frontend/src/app/models/metrics.model.ts
673:  if (status === 403) {
678:  if (status === 401) {
681:  if (status === 404) {
684:  if (status === 400) {
687:  if (status === 503) {
696:  if (status === 0) {
```

**The table as ASSERTED (the spec is the census — the union is closed):**

| class | status / body | Retry | pinned by |
|---|---|---|---|
| `auth_required` | 401 | **NO** | `metrics.model.spec.ts` — "decodes a 401 to the FIRST-CLASS sign-in state (never `unexpected`)" + the five-class NO-Retry leg |
| `auth_not_configured` | 503 `AUTH_NOT_CONFIGURED` | **NO** | "the AUTH state renders the cause, the meaning, the action and the RUNBOOK POINTER" + "the AUTH state renders ZERO Retry buttons" |
| `scope_insufficient` | 403 + `SCOPE_INSUFFICIENT` (case-insensitive) | YES | "THE 403 BRANCHES ARE BOTH PINNED (task 8.12 / AM-20)" |
| `forbidden_other` | 403, any other body (or none) | YES | the same leg — "a 403 with another code ⇒ `forbidden_other`, NEVER `scope_insufficient`" |
| `unknown_family` | 404 | **NO** | "decodes a 404 to `unknown_family` and a 400 to `bad_request`" + the NO-Retry leg |
| `bad_request` | 400 (`invalid_label_filter` / `invalid_cursor`) | **NO** | the same leg + the NO-Retry leg |
| `timeout` | the client-side ~15 s deadline | **NO** | "the NO-Retry classes render no Retry even when a handler is supplied" (the `timeout` class was delivered in phase 2's 2.10) |
| `registry_unavailable` | 503 `registry_unavailable` | YES | "the 503 branches stay distinct — the fail-closed auth and the unwired provider" |
| `network` | status 0 | YES | "a RETRYABLE class keeps its banner + Retry semantics" |
| `unexpected` | the closed fallback (any other status, a 503 with neither body code) | YES | "the flat map carries NO literal copy of its own" + the re-pointed fallback leg |

**The exhaustion pin** — a class added to the union without a copy (or a
copy without a class) breaks the build:

```
$ cd frontend && CI=1 ./node_modules/.bin/jest --ci --silent --runInBand src/app/models/metrics.model.spec.ts
  metrics model — the CLOSED door-state table (T1 phase 8, task 8.1 / AM-4)
    ✓ is a CLOSED union of exactly the TEN classes
    ✓ gives EVERY class a cause, a meaning, an action and a pointer slot
    ✓ carries a per-class RETRY POLICY, and the plan's five classes are NO-Retry
    ✓ the auth_not_configured copy is ACTIONABLE — cause, meaning, action and the RUNBOOK
    ✓ NO environment-variable name appears in ANY door-state copy (the plan's risk row)
    ✓ decodes a 401 to the FIRST-CLASS sign-in state (never `unexpected`)
    ✓ decodes a 404 to `unknown_family` and a 400 to `bad_request`
    ✓ THE 403 BRANCHES ARE BOTH PINNED (task 8.12 / AM-20)
    ✓ the scope token is matched CASE-INSENSITIVELY (and only that token)
    ✓ the 503 branches stay distinct — the fail-closed auth and the unwired provider
Tests:       46 passed, 46 total
```

**The 401 defect is closed:** the old table had five classes and 401
decoded to `unexpected` with a live Retry. The re-pointed fallback leg
now asserts 401 ⇒ `auth_required`, 404 ⇒ `unknown_family`, 400 ⇒
`bad_request`, and only an UNDOCUMENTED status (500) falls back to
`unexpected`.

---

## 2. The ONE ACTIONABLE PANEL (8.2) — central surfaces only, ZERO Retry

**The panel** is ONE shared component
(`frontend/src/app/pages/metrics/metrics-door-state.component.ts`) that
renders the four facts from the copy SSOT — cause / meaning / action /
pointer — and a Retry button IFF the class is `retryable`.

```
$ cd frontend && CI=1 ./node_modules/.bin/jest --ci --silent --runInBand src/app/pages/metrics/metrics.door-state.spec.ts
  MetricsDoorStateComponent — the ACTIONABLE state (T1 phase 8, task 8.2)
    ✓ renders NOTHING when there is no class (a surface never carries an empty banner)
    ✓ the AUTH state renders the cause, the meaning, the action and the RUNBOOK POINTER
    ✓ the AUTH state renders ZERO Retry buttons (a config error is not transient)
    ✓ a RETRYABLE class keeps its banner + Retry semantics
    ✓ the NO-Retry classes render no Retry even when a handler is supplied
    ✓ NO environment-variable name leaks into the rendered copy (the plan's risk row)
Tests:       18 passed, 18 total
```

**The copy** (from the SSOT, `models/metrics.model.ts:488`):

| member | the auth_not_configured copy |
|---|---|
| `cause` | *The metrics door is fail-closed — auth is not configured on the daemon.* |
| `meaning` | *No credential can fix this — it is daemon configuration, so the door serves no metrics.* |
| `action` | *An operator must configure auth on the daemon and restart it.* |
| `pointer` | `docs/runbooks/oauth2-auth.md` |
| `retryable` | `false` |

**The env-var discipline** — the copy states the FACT and the runbook;
no `ENSEMBLE_*` / `POSTGRES_*` / `OPENAI_*` name appears in ANY class's
copy (asserted for all ten classes, plus the rendered DOM).

**A central failure does NOT blank the custom tab (tab 1 stays lit):**

```
$ cd frontend && CI=1 ./node_modules/.bin/jest --ci --silent --runInBand src/app/pages/metrics/metrics.door-state.spec.ts
  MetricsComponent — the door state renders ONCE (T1 phase 8, task 8.3)
    ✓ a CENTRAL failure does NOT blank the CUSTOM tab (tab 1 stays lit)
```

**THE PLANTED VARIANT (the plan's own "Retry on the auth state"):**

```
$ python3 - <<'PY'   # plant: the Retry renders for EVERY class
p='src/app/pages/metrics/metrics-door-state.component.ts'
s=open(p).read()
s=s.replace("""  readonly retryable = computed(
    () => (this.state()?.copy.retryable ?? false) && this.onRetry() !== null,
  );""","""  readonly retryable = computed(() => this.onRetry() !== null);""",1)
open(p,'w').write(s)
PY
$ npx --no-install jest src/app/pages/metrics/metrics.door-state.spec.ts src/app/pages/metrics/tabs/central-tab.component.spec.ts
    ✕ the AUTH state renders ZERO Retry buttons (a config error is not transient)
    ✕ the NO-Retry classes render no Retry even when a handler is supplied
    ✕ the AUTH state offers NO Retry (a config error is not transient — 8.2)
    ✕ a retryable failure DOES offer the Retry affordance
Tests:       4 failed, 58 passed, 62 total
$ git checkout -- frontend/src/app/pages/metrics/metrics-door-state.component.ts
$ npx --no-install jest src/app/pages/metrics/metrics.door-state.spec.ts src/app/pages/metrics/tabs/central-tab.component.spec.ts
Tests:       62 passed, 62 total
```

**Interpretation disclosed (§9.2):** 8.2 names "the catalog drawer + the
6 central tab bodies" as the places the actionable state renders, while
8.3 forbids the tab bodies from EACH re-rendering it. Resolved in favour
of 8.3 + the Exit Criterion's "**one** actionable panel" and "no
duplicate banners": the **frame** renders the central state ONCE
(page-scoped, above the tabs, therefore visible on every central tab
body); the **drawer** states the FACT and POINTS at it (a pointer line,
not a second panel); the **tab bodies** render only their OWN failure.

---

## 3. NO DUPLICATE BANNERS (8.3)

```
$ cd frontend && CI=1 ./node_modules/.bin/jest --ci --silent --runInBand src/app/pages/metrics/metrics.door-state.spec.ts
  MetricsComponent — the door state renders ONCE (T1 phase 8, task 8.3)
    ✓ a failed central read renders EXACTLY ONE door-state panel on the page
    ✓ the frame template renders the state panel ONCE (a planted second banner fails)
    ✓ the drawer states the FACT and POINTS at the failure — it does not re-render the panel
    ✓ a CENTRAL failure does NOT blank the CUSTOM tab (tab 1 stays lit)
Tests:       18 passed, 18 total
```

**The markup (re-read at the freeze):**

```
$ grep -n "app-metrics-door-state\|metrics-door-chip\|metrics-catalog-error\|metrics-catalog-retry" frontend/src/app/pages/metrics/metrics.component.html
12:          class="metrics-door-chip"
13:          [attr.data-test]="'metrics-door-chip'"
38:  <app-metrics-door-state
41:    testId="metrics-catalog-error"
42:    retryTestId="metrics-catalog-retry"
43:  ></app-metrics-door-state>

$ grep -n "app-metrics-door-state\|central-tab-error\|central-tab-retry" frontend/src/app/pages/metrics/tabs/central-tab.component.ts
150:      <app-metrics-door-state
153:        testId="central-tab-error"
154:        retryTestId="central-tab-retry"
155:      ></app-metrics-door-state>
```

**What changed underneath.** Before this phase the frame published the
central failure into every central tab's `errorClass`
(`publishCentralSource({doorState, errorClass})`), so the page carried a
page-level banner AND a per-tab banner for the same failure. Now
`publishCentralSource` publishes the **posture** (`doorState`) only; the
**class** lives on the frame (`catalogErrorCode`) and is rendered once.
A tab body's own read failure still lands in its own `errorClass` and
still renders its own panel — the plan's risk row ("a tab's own fetch
failure is a different fact from the door's state") is preserved.

---

## 4. THE TRUTHFUL COPY (8.4) — no "last known values" claim when none exist

The old suffix was appended **unconditionally** in two tab bodies, so a
failure with NO last-good still claimed "the last known values are kept"
while the page rendered none. The suffix is now chosen by the FACT, and
both suffixes live in the SSOT (§6).

```
$ cd frontend && CI=1 ./node_modules/.bin/jest --ci --silent --runInBand src/app/pages/metrics/metrics.door-state.spec.ts
  MetricsComponent — the truthful copy (T1 phase 8, task 8.4)
    ✓ the frame NEVER claims retained last-good values in its failure copy
    ✓ the STALE badge still appears when a last-good catalog exists
    ✓ the two tab bodies choose their suffix by the FACT (a last-good or not)
```

**The suffix grep (both suffixes now live in ONE place):**

```
$ grep -rn "the last known values are kept\|the last known trend is kept" --include=*.ts frontend/src/app | grep -v "\.spec\.ts"
frontend/src/app/models/metrics.model.ts:592: * "the last known values are kept" while the page rendered none. The
frontend/src/app/models/metrics.model.ts:596:export const METRICS_STALE_SUFFIX_VALUES = 'the last known values are kept';
frontend/src/app/models/metrics.model.ts:602:export const METRICS_STALE_SUFFIX_TREND = 'the last known trend is kept';
```

The two tab bodies now READ the constants (never inline them):

```
$ grep -rn "METRICS_STALE_SUFFIX\|METRICS_NO_LAST_GOOD_SUFFIX" --include=*.ts frontend/src/app | grep -v "\.spec\.ts" | grep -v "models/metrics.model.ts"
frontend/src/app/pages/metrics/charts/charts-tab.component.ts:20:  METRICS_NO_LAST_GOOD_SUFFIX,
frontend/src/app/pages/metrics/charts/charts-tab.component.ts:21:  METRICS_STALE_SUFFIX_TREND,
frontend/src/app/pages/metrics/charts/charts-tab.component.ts:339:              hasLastGood ? METRICS_STALE_SUFFIX_TREND : METRICS_NO_LAST_GOOD_SUFFIX
frontend/src/app/pages/metrics/overview/overview-tab.component.ts:19:  METRICS_NO_LAST_GOOD_SUFFIX,
frontend/src/app/pages/metrics/overview/overview-tab.component.ts:20:  METRICS_STALE_SUFFIX_VALUES,
frontend/src/app/pages/metrics/overview/overview-tab.component.ts:308:              hasLastGood ? METRICS_STALE_SUFFIX_VALUES : METRICS_NO_LAST_GOOD_SUFFIX
```

---

## 5. THE PAGE-SCOPED CHIP, NOT THE GLOBAL DOT (8.5)

```
$ cd frontend && CI=1 ./node_modules/.bin/jest --ci --silent --runInBand src/app/pages/metrics/metrics.door-state.spec.ts
  MetricsComponent — the PAGE-SCOPED posture chip (T1 phase 8, task 8.5)
    ✓ the metrics header carries the door-posture chip
    ✓ the chip names the FAIL-CLOSED posture when the door is fail-closed
    ✓ the chip reads OK when the door answers
    ✓ the GLOBAL health dot is UNCHANGED by a door failure (AM-21)
    ✓ a PLANTED "red global dot" would be caught (the dot class is bound to health alone)
```

**The shell's dot binding (unchanged by this phase):**

```
$ grep -n "class.healthy\|class.unhealthy" frontend/src/app/app.html
92:            [class.healthy]="health()!.status === 'healthy'"
93:            [class.unhealthy]="health()!.status !== 'healthy'"
```

**THE PLANTED VARIANT (the plan's own "red global dot"):**

```
$ python3 - <<'PY'   # plant: the shell dot ORs a metrics-door state
p='src/app/app.html'
s=open(p).read()
s=s.replace("""            [class.unhealthy]="health()!.status !== 'healthy'\"""",
            """            [class.unhealthy]="health()!.status !== 'healthy' || metricsDoorFailed\"""",1)
open(p,'w').write(s)
PY
$ npx --no-install jest src/app/pages/metrics/metrics.door-state.spec.ts
    ✕ the GLOBAL health dot is UNCHANGED by a door failure (AM-21)
    ✕ a PLANTED "red global dot" would be caught (the dot class is bound to health alone)
      Expected substring: "[class.unhealthy]=\"health()!.status !== 'healthy'\""
      Expected substring: not "metrics"
Tests:       2 failed, 16 passed, 18 total
$ git checkout -- frontend/src/app/app.html
$ npx --no-install jest src/app/pages/metrics/metrics.door-state.spec.ts
Tests:       18 passed, 18 total
```

The chip carries `data-door-posture` ∈ `loading | ok | fail-closed |
failed`; `fail-closed` is reserved for the two AUTH classes (the door is
deliberately closed, not broken).

---

## 6. ONE COPY SSOT (8.11) — the two duplicated suffixes moved

```
$ cd frontend && CI=1 ./node_modules/.bin/jest --ci --silent --runInBand src/app/models/metrics.model.spec.ts
  metrics model — the COPY SSOT (T1 phase 8, task 8.11 / IMPL-18 / AM-20)
    ✓ the flat banner map is DERIVED from the structured one (never a second source)
    ✓ the flat map carries NO literal copy of its own (the source has one home)
    ✓ the two DUPLICATED suffixes live in the SSOT and nowhere else
    ✓ the SSOT also carries the NO-LAST-GOOD suffix (8.4 — the truthful case)
```

The suffix pin walks the whole `frontend/src/app` tree (every `.ts`,
specs excluded) and asserts **exactly one** home per phrase — in
`models/metrics.model.ts`.

**DISPOSITION (§9.3):** the plan names the two duplicated suffixes at
`overview-tab.component.ts:295` / `charts-tab.component.ts:311` as the
"degraded-registry" suffixes. **There is no duplicated degraded-registry
suffix** — `METRICS_REGISTRY_DEGRADED_COPY` has exactly ONE home
(`models/metrics.model.ts:400`, used by `metrics.component.ts` only):

```
$ grep -rn "METRICS_REGISTRY_DEGRADED_COPY" --include=*.ts frontend/src/app
frontend/src/app/pages/metrics/metrics.component.ts:18:  METRICS_REGISTRY_DEGRADED_COPY,
frontend/src/app/pages/metrics/metrics.component.ts:201:    readonly registryDegradedCopy = METRICS_REGISTRY_DEGRADED_COPY;
frontend/src/app/models/metrics.model.ts:400:export const METRICS_REGISTRY_DEGRADED_COPY =
```

The two duplicated sentences that actually lived at those sites are the
STALE ones ("— the last known values are kept." / "— the last known trend
are kept."). They moved into the SSOT and became conditional (§4).

---

## 7. THE NARROWED 403 PREDICATE (8.12) — BOTH branches pinned

```
$ cd frontend && CI=1 ./node_modules/.bin/jest --ci --silent --runInBand src/app/models/metrics.model.spec.ts
    ✓ THE 403 BRANCHES ARE BOTH PINNED (task 8.12 / AM-20)
    ✓ the scope token is matched CASE-INSENSITIVELY (and only that token)
```

* 403 + `SCOPE_INSUFFICIENT` (any case) ⇒ `scope_insufficient`.
* 403 + any other body code, **or no body code at all** ⇒
  `forbidden_other` — never `scope_insufficient`.

The old predicate (`rawCode === 'SCOPE_INSUFFICIENT' || (status === 403
&& rawCode !== null)`) labelled ANY 403-with-a-code as a scope failure —
a false statement about the operator's session, contradicting the
function's own doc comment.

---

## 8. NO ENABLED-BUT-EMPTY TABS (8.7) + THE AVAILABILITY-FIRST SLICE (8.8)

```
$ cd frontend && CI=1 ./node_modules/.bin/jest --ci --silent --runInBand src/app/pages/metrics/tabs/central-tab.component.spec.ts
    ✓ a tab whose families are ALL dark renders EVERY family reason (8.7)
    ✓ a DARK family does NOT consume the selector ceiling (8.8 / ADJ-vi)
    ✓ the FE's own truncation is SURFACED with a count and no phantom page (8.8)
Tests:       44 passed, 44 total
```

**8.7** — a tab whose families are ALL dark renders each family's OWN
reason (`data-test="central-tab-family-dark"` + `data-reason`), never an
empty shell; the custom tab shows ITS data under a central failure
(§2).

**THE PLANTED VARIANT (the plan's own "enabled-but-empty"):**

```
$ python3 - <<'PY'   # plant: the dark family renders WITHOUT its reason
p='src/app/pages/metrics/tabs/central-tab.component.ts'
s=open(p).read()
s=s.replace("""                <span class="reason">{{ reasonOf(family) }}</span>""",
            """                <span class="reason"></span>""",1)
open(p,'w').write(s)
PY
$ npx --no-install jest src/app/pages/metrics/tabs/central-tab.component.spec.ts
    ✕ renders a DARK family with its reason, and never charts it (ADJ-vi)
    ✕ a tab whose families are ALL dark renders EVERY family reason (8.7)
    ✕ the runtime tab fetches ITS bound families and surfaces a dark family's reason
    ✕ the db-pool tab fetches ITS bound families and surfaces a dark family's reason
    ✕ the jobs tab fetches ITS bound families and surfaces a dark family's reason
    ✕ the instances tab fetches ITS bound families and surfaces a dark family's reason
Tests:       8 failed, 36 passed, 44 total
$ git checkout -- frontend/src/app/pages/metrics/tabs/central-tab.component.ts
$ npx --no-install jest src/app/pages/metrics/tabs/central-tab.component.spec.ts
Tests:       44 passed, 44 total
```

**8.8** — the slice is taken over LIVE families only (the dark family is
filtered out **before** the ceiling), and the FE's own truncation is
SURFACED with a count:

```
$ grep -n "droppedSeriesCount" frontend/src/app/pages/metrics/tabs/central-tab.component.ts
250:      @if (droppedSeriesCount() > 0) {
261:          {{ droppedSeriesCount() }} series are not shown — this page's own selector slice is
552:  readonly droppedSeriesCount = computed(() => {
563:  readonly selectorSliced = computed(() => this.droppedSeriesCount() > 0);
```

The old copy was *"The door's selector ceiling is 50 series — the rest
are on the next page."* — a **false statement**: the FE drops them, and
there is no next page.

**THE PLANTED VARIANT (the plan's own "dark family consumed a slot") —
and a VACUOUS PIN FOUND AND FIXED FIRST:**

The first version of the 8.8 leg put the LIVE family first, so the
slice-before-filter variant still passed it (the dark family's names
landed after the slice). The leg was reordered (the dark family leads)
and now discriminates:

```
$ python3 - <<'PY'   # plant: the slice happens BEFORE the availability filter
p='src/app/pages/metrics/tabs/central-tab.component.ts'
s=open(p).read()
s=s.replace("""      if (family.availability.state !== 'live') {
        continue;
      }
      if (family.role !== 'chart') {""","""      if (family.role !== 'chart') {""",1)
open(p,'w').write(s)
PY
$ npx --no-install jest src/app/pages/metrics/tabs/central-tab.component.spec.ts
    ✕ a DARK family does NOT consume the selector ceiling (8.8 / ADJ-vi)
Tests:       9 failed, 35 passed, 44 total
$ git checkout -- frontend/src/app/pages/metrics/tabs/central-tab.component.ts
$ npx --no-install jest src/app/pages/metrics/tabs/central-tab.component.spec.ts
Tests:       44 passed, 44 total
```

---

## 9. THE DATA-END MARKER (8.10) + THE STAT-STRIP AGE

**The marker did NOT exist** — verified before the work:

```
$ grep -rn "data-end\|dataEnd\|data_end" --include=*.ts --include=*.html frontend/src/app
(no hits)
```

It is now a **house chart.js plugin** (the anomaly overlay's precedent —
no second vendor package): `pages/metrics/charts/data-end-overlay-plugin.ts`,
registered in the panel and fed through the renderer's `inputs()`.

```
$ cd frontend && CI=1 ./node_modules/.bin/jest --ci --silent --runInBand src/app/pages/metrics/charts/data-end-overlay-plugin.spec.ts
  the house data-end overlay plugin (T1 phase 8, task 8.10)
    ✓ draws a VERTICAL line at the data end, across the chart area
    ✓ draws a VISIBLE label beside the line (a bare line says nothing)
    ✓ draws NOTHING when there is no data end
    ✓ draws NOTHING when the scale cannot resolve the timestamp (never a bogus marker)
    ✓ carries the house id, colour and label font
    ✓ the data end is a structural type — the marker never carries a bare number
Tests:       6 passed, 6 total

$ cd frontend && CI=1 ./node_modules/.bin/jest --ci --silent --runInBand src/app/pages/metrics/tabs/central-tab.component.spec.ts
  MetricsCentralTabComponent — the DATA-END marker (T1 phase 8, task 8.10)
    ✓ the panel carries WHERE ITS DATA ENDS (the last plotted sample)
    ✓ a panel with NO points carries NO data end (never a bogus marker)
    ✓ the render data carries the data end (the renderer passes it to the panel)
    ✓ the STAT STRIP shows the sample age (the audit found no age on the cards)
```

The stat strip's age was **already delivered** (phase 2/4) — the leg is
a RENDER assertion (the card shows `published 42.5s ago`), not a source
pin.

---

## 10. THE DRAWER AFFORDANCES (8.9) — disposition: DELIVERED IN PHASE 2 (2.7)

The four affordances exist and each is pinned (the phase-2 legs). This
phase re-asserts them together, so the phase's own evidence carries the
claim:

```
$ cd frontend && CI=1 ./node_modules/.bin/jest --ci --silent --runInBand src/app/pages/metrics/families/families-tab.component.spec.ts
  MetricsFamilyCatalogComponent — the drawer affordances (T1 phase 8, task 8.9)
    ✓ every column header carries `aria-sort` and a click handler
    ✓ the dark families are FINDABLE (a filter narrows the rows)
    ✓ the empty copy is ERROR-GATED (a failed load never says "No families are registered.")
    ✓ the empty copy renders only under a HEALTHY, genuinely empty load
Tests:       17 passed, 17 total
```

---

## 11. THE 900 px GEOMETRY (8.6)

**A real browser, a real layout.** jsdom has no layout engine (every
`getBoundingClientRect()` there returns zeros), so the spec drives the
REAL Chromium against a harness built from the PRODUCTION sources: the
CSS is EXTRACTED from the door-state component's own styles, the copy is
IMPORTED from the copy SSOT, and the harness reproduces the page's
geometry (the host padding, the material tab body padding).

```
$ cd frontend && CI=1 ./node_modules/.bin/jest --ci --silent --runInBand src/app/pages/metrics/metrics.banner-geometry.spec.ts
  the banner GEOMETRY at 900 px (T1 phase 8, task 8.6)
    ✓ the banner text and the Retry button do NOT overlap at 900 px
    ✓ the Retry button REFLOWS BELOW the copy at 900 px (the collision fix)
    ✓ the LONGEST copy also reflows (the audit said "any longer message")
    ✓ the SHIPPED stylesheet carries the reflow rules (the unit-level pin)
Tests:       4 passed, 4 total
```

**THE PLANTED VARIANT (the pre-fix stylesheet):**

```
$ python3 - <<'PY'   # plant: the pre-fix stylesheet (no wrap / no min-width / no stacking)
... (flex-wrap: wrap, min-width: 0, flex: 0 0 auto and the @media block removed)
PY
$ npx --no-install jest src/app/pages/metrics/metrics.banner-geometry.spec.ts
    ✓ the banner text and the Retry button do NOT overlap at 900 px
    ✕ the Retry button REFLOWS BELOW the copy at 900 px (the collision fix)
      Expected: >= 116.171875
      Received:    64.578125
    ✕ the SHIPPED stylesheet carries the reflow rules (the unit-level pin)
      Expected substring: "flex-wrap: wrap"
Tests:       2 failed, 2 passed, 4 total
$ cp /tmp/door-state.fixed.ts src/app/pages/metrics/metrics-door-state.component.ts   # restore
$ npx --no-install jest src/app/pages/metrics/metrics.banner-geometry.spec.ts
Tests:       4 passed, 4 total
```

**DISCLOSED (§9.4):** the audit's PIXEL collision could not be
reproduced from the CSS alone — flexbox WRAPS the text rather than
overlapping it, so the "no overlap" leg is GREEN under both stylesheets.
The load-bearing leg is therefore the **reflow** (the button stacks
BELOW the copy at 900 px), which the planted stylesheet turns RED.

**A REAL BUG THE GEOMETRY SPEC CAUGHT IN THIS PHASE'S OWN FIX.** The
first narrow-width fix stacked the banner (`flex-direction: column`) but
left the copy block's `flex: 1 1 18rem` — and in a COLUMN container the
flex-basis is the **HEIGHT**, so the copy block was pinned to an 18rem
box and a longer copy OVERFLOWED it straight into the button below. The
geometry spec caught it (the measured button was inside the copy block's
band). The shipped CSS resets the block inside the media query:

```
@media (max-width: 900px) {
  .metrics-banner { flex-direction: column; align-items: flex-start; }
  .metrics-banner-body { flex: 0 0 auto; width: 100%; }
}
```

---

## 12. The whole-tree verification

```
$ cd frontend && CI=1 ./node_modules/.bin/jest --ci --silent --runInBand
Test Suites: 4 failed, 150 passed, 154 total
Tests:       9 failed, 4693 passed, 4702 total
```

**The 9 reds are BASE-PROVEN** — the identical four specs and the
identical nine failing NAMES at this phase's base `4e0c82b8b`, run with
the SAME commands in a throwaway detached worktree:

```
$ git worktree add --detach /tmp/phase8-base-wt 4e0c82b8b
$ ln -s .../frontend/node_modules /tmp/phase8-base-wt/frontend/node_modules
$ cd /tmp/phase8-base-wt/frontend && npx --no-install jest --ci --silent --runInBand \
    src/app/pages/settings/settings.component.spec.ts \
    src/app/models/jobs-filter-state.model.spec.ts \
    src/app/models/jobs-grouping.model.spec.ts \
    src/app/components/schedule-create-dialog/schedule-create-dialog.component.spec.ts
FAIL src/app/pages/settings/settings.component.spec.ts
  ● Timezone preference (user-timezone-setting) › renders the timezone section with the native picker …
  ● Timezone preference (user-timezone-setting) › uses the API list when the GET /api/settings/timezones …
FAIL src/app/models/jobs-filter-state.model.spec.ts
  ● JobsFilterState — shape + defaults › canonical status/source tables cover the model unions …
FAIL src/app/models/jobs-grouping.model.spec.ts
  ● jobs-grouping — groupMetaLine … › renders agent + N jobs + ago when all three segments are populated
  ● jobs-grouping — groupHeaderTitle … › renders "agent · timeAgo" when both fields are populated
  ● jobs-grouping — groupHeaderTitle … › falls back to timeAgo-only when no agent is available
FAIL src/app/components/schedule-create-dialog/schedule-create-dialog.component.spec.ts
  ● schedule-create-dialog — timezone picker source chain › (1) …
  ● schedule-create-dialog — timezone picker source chain › (2) … (response is non-empty)
  ● schedule-create-dialog — timezone picker source chain › (2) … (the call errors)
Tests:       9 failed, 201 passed, 210 total

$ comm -13 /tmp/phase8-base-reds-sorted.txt /tmp/phase8-head-reds.txt
(empty — NET-NEW REDS = 0)
$ comm -23 /tmp/phase8-base-reds-sorted.txt /tmp/phase8-head-reds.txt
(empty — none fixed either)
```

The reds are the host-DST timezone labels (`America/New_York (UTC-04:00)`
vs `(UTC-05:00)`) and the two known base-debt model specs — the same set
the phase-5 freeze recorded.

**The type-checks:**

```
$ cd frontend && ./node_modules/.bin/tsc -p tsconfig.metrics-fe.json --noEmit ; echo rc=$?
rc=0
$ cd frontend && ./node_modules/.bin/tsc --noEmit -p tsconfig.app.json ; echo rc=$?
rc=0
```

**The prompt-artifact baseline (the `agents/` tree is byte-identical):**

```
$ cd /home/nea/ensemble-src-wt-fe-metrics-dashboard && \
  /home/nea/ensemble-src/.venv/bin/python scripts/protection/generate_prompt_artifact_baseline.py --check
PROMPT-BASELINE: OK — 161 versioned artifact(s) match .prompt-artifacts.sha256
```

**The per-file node census (MECHANICAL — re-derived at the freeze):**

```
$ cd frontend && for f in ...; do CI=1 ./node_modules/.bin/jest --ci --silent --runInBand "$f"; done
src/app/models/metrics.model.spec.ts                      -> 46 passed
src/app/pages/metrics/metrics.door-state.spec.ts          -> 18 passed
src/app/pages/metrics/metrics.banner-geometry.spec.ts     ->  4 passed
src/app/pages/metrics/metrics.component.spec.ts            -> 58 passed
src/app/pages/metrics/families/families-tab.component.spec.ts -> 17 passed
src/app/pages/metrics/tabs/central-tab.component.spec.ts  -> 44 passed
src/app/pages/metrics/charts/data-end-overlay-plugin.spec.ts -> 6 passed
src/app/pages/metrics/charts/chart-panel.component.spec.ts -> 23 passed
```

---

## 13. The bundle guard (B1/B2/B3/B6)

GREEN at the phase's freeze — see §15 for the B1 attribution and the
one-line fix that restored it.

---

## 14. Deviations, dispositions and disclosures

### 14.1 The SEQUENCING premise was FALSE at dispatch (and phase 6 landed mid-phase)

The dispatch said *"you were spawned only because Phase 6 reported
complete — verify that from the log before proceeding."* **The log
disproved it**: at dispatch the branch tip was `4e0c82b8b` (Phases 0–5),
`phase6-evidence.md` did not exist, `METRICS_UNIT_VOCABULARY` was empty
by design (*"phase 6, task 6.3 populates it"*), and the Phase-6 coder
instance was still `running`. Phase 6's commits then landed **between**
this phase's commits (`c59d44434` 6.3, `b14f3a5f7` 6.1/6.2, `aa7c0b04e`
6.4–6.6, `fd6322639` the F4 unit pins).

**Handling:** the file sets were disjoint at every step; every commit
used **targeted `git add --`** (never `add -A`), so no Phase-6 in-flight
edit was ever staged by this phase; Phase 6's uncommitted working-tree
edits were left untouched. `phase6-plan.md` itself declares Phase 6
*"Independent of: Phase 1, Phase 2, Phase 7, Phase 8"*, which the
disjoint file sets confirm.

### 14.2 8.2 vs 8.3 — the "where the actionable panel renders" tension — RESOLVED

8.2 says the actionable state *"renders on the catalog drawer + the 6
central tab bodies"*; 8.3 says *"the tab bodies do not each re-render
it"*; the Exit Criterion demands *"**one** actionable panel"* and *"no
duplicate banners"*. **Resolution:** the frame renders the central state
ONCE (page-scoped — above the tabs, so visible on every central tab
body and above the drawer); the drawer states the FACT and POINTS at it
(a pointer line, NOT a second panel — the drawer is inline on the same
page, so a second panel would BE the duplicate); the tab bodies render
only their OWN failure. The Exit Criterion's testable bullets are
satisfied literally.

### 14.3 8.11's "degraded-registry suffixes" — MIS-DESCRIBED in the plan

See §6: there is no duplicated degraded-registry suffix (the constant has
one home). The two duplicated sentences at the cited sites are the STALE
ones; they moved into the SSOT and became conditional (8.4). The plan's
line numbers are also stale (`charts-tab.component.ts:311` → the suffix
lived at `:330`).

### 14.4 8.8's location + copy wording

The plan names `metrics.component.ts:162-175` for `chartSpecs`; the
projection actually lives in `tabs/central-tab.component.ts` (phase 2
moved it into the tab body). The **availability filter already preceded
the slice** (delivered in phase 2) — the remaining defect was the copy
and the missing count. The plan's suggested copy (*"N families are not
shown — the door's selector ceiling is 50"*) is itself imprecise: the
slice is over SERIES (a family can be shown partially, so "N families"
can be false), and it is the FE — not the door — that drops them.
**Shipped:** *"N series are not shown — this page's own selector slice is
50."*

### 14.5 8.10's data-end marker did NOT exist (the plan called it "Phase 4's contract")

Verified by grep (zero hits). Built as a house plugin (the anomaly
overlay's precedent) — **no new npm package**; the render data gained
one OPTIONAL, additive member (`ChartRenderData.dataEnd`).

### 14.6 8.6 — the audit's pixel collision is not reproducible from the CSS

See §11. The load-bearing assertion is the reflow; the overlap leg is
reported as GREEN under both stylesheets (flexbox wraps rather than
overlaps). The spec found a REAL bug in this phase's own first fix
(the column-axis flex-basis) — the geometry leg is doing its job.

### 14.7 Three EXISTING legs were RE-POINTED (never deleted)

| leg | before | after | why |
|---|---|---|---|
| `central-tab.component.spec.ts` — "a retryable failure DOES offer the Retry affordance" | used `auth_not_configured` as its retryable example | uses `network` | a fail-closed CONFIG error is not transient (8.2); the plan names this exact variant as the planted failure. A NEW leg asserts the auth state offers NO Retry. |
| `metrics.component.spec.ts` — "a failed catalog load is an ERROR state, not an empty state" | set the copy string only | sets the CLASS too | the panel is class-driven (the class selects the copy + the Retry policy) |
| `metrics.component.spec.ts` — "a failing CENTRAL source does NOT blank the custom tab's state" | asserted `tabStates()['runtime'].errorClass === 'auth_not_configured'` | asserts the tab's `doorState`, that its `errorClass` is now `null`, and that the FRAME carries the class | 8.3 — the central state is rendered ONCE, so it is no longer published into every central tab |

### 14.8 One VACUOUS pin found and fixed

The first 8.8 leg put the live family first, so the slice-before-filter
variant passed it. The leg was reordered and now discriminates (§8).

### 14.9 The B1 bundle-budget — a REAL eager leak this phase's copy table EXPOSED

This phase's door-state copy table grew the EAGER metrics-model chunk
past the B1 allowance. The cause was the barrel's VALUE re-export
(`export * from './metrics.model'`), which put the whole metrics
vocabulary in the startup payload. **Fixed** with a type-only re-export
(§15): the initial bundle is now 1,707 B **below** the frozen baseline.
The scope extension (touching `models/index.ts`) is disclosed here; it is
FE-only, behaviour-neutral and was REQUIRED by the bundle guard.

---

## 15. The bundle guard — B1 attribution AND the FIX

**The guard went RED at HEAD** (B1: the initial total exceeds the 2,048 B
allowance). The attribution was MEASURED, not guessed:

| tree | `initial_total_bytes` | B1 vs the frozen baseline (5,909,018 B) | verdict |
|---|---|---|---|
| this phase's base `f7b88f56c^` (= `c59d44434`, phase 6's 6.3) | 5,909,624 | +606 B | GREEN (the phase-5 freeze value) |
| **this phase's 7 commits alone** (cherry-picked onto the base) | **5,912,157** | **+3,139 B** | **RED** |
| HEAD (this phase + phase 6's later commits) | 5,912,157 | +3,139 B | RED |

⇒ the delta is **this phase's**, not phase 6's. The per-file attribution
(measured on the two builds, same algorithm as the house's measure tool):

```
BASE  : main-IQUDPDM7.js (215,527 B) + chunk-GA772GYA.js (2,285 B)  = 5,909,624 B
MINE  : main-6J5KESLV.js (215,527 B) + chunk-V77VRCRH.js (4,818 B)  = 5,912,157 B
DELTA : +2,533 B — entirely inside the ONE chunk that grew
        (the chunk carries the availability-reason formatters and
         `auth_not_configured` ⇒ it is the METRICS MODEL chunk)
```

**THE ROOT CAUSE.** The metrics model — and with it this phase's new
door-state copy table — was in the **EAGER** bundle, because
`models/index.ts` re-exported it with a VALUE re-export:

```
$ grep -n "export \* from './metrics.model'" frontend/src/app/models/index.ts
1059:export * from './metrics.model';
```

The barrel is imported by EAGER modules, so a value re-export puts the
whole metrics vocabulary — the payload shapes, the formatters and the
2.5 KB copy table — into the INITIAL bundle: every operator downloads
the /metrics vocabulary at startup, whether or not they ever open
/metrics.

**THE FIX (one line, FE-only, behaviour-neutral):** the barrel re-exports
the mirror **TYPE-ONLY**:

```
export type * from './metrics.model';
```

Every `import { Agent } from '../../models'` still works; the mirror's
RUNTIME values now ship with the modules that use them — the metrics
page's own tree, which is LAZY. This is the house's own answer to the
"new bytes must be lazy" rule (D-1) — and it is the phase-8 fix for a
defect this phase's copy table EXPOSED (the table was never eager
before: the eager model chunk was 2,285 B).

**THE GUARD IS GREEN AT THE FIX:**

```
$ cd frontend && ./node_modules/.bin/ng build --configuration production   # rc 0
$ cd frontend && node ../.agents/shared/planning/metrics-unification/fe-t1-build/bundle-guard.js \
    dist/frontend/browser /tmp/phase8-build2.log
(a) B1 — the numeric initial-total delta
    initial_total_bytes = 5907311
    delta               = -1707 B (limit |2048| B)
    initial_file_count  = 36
(b) B2  initial_chunks_scanned = 36 · marker_hits = 0
(c) B3  chart_js_import_sites = 1 · static_sites = 0 · outside_metrics_tree = 0
        src/app/pages/metrics/charts/chart-panel.component.ts: dynamic=2 static=0
(d) B6  baseline_set_size = 9 · measured_set_size = 9 · added 0 / removed 0
RESULT: GREEN (B1 + B2 + B3 + B6)
```

The initial bundle is now **1,707 B SMALLER than the frozen baseline**
and carries **36** initial files instead of 37 — the metrics mirror left
the eager set entirely. Both type-checks pass after the change
(`tsconfig.app.json` rc 0, `tsconfig.metrics-fe.json` rc 0).

**B4 (the lazy-only decomposition) is DISCLOSED as an EXCEEDANCE** — the
lazy-only portion grew because this phase adds a component and a plugin
to the metrics route chunk (`metrics_route_fetch_set_lazy_only_bytes`:
159,330 B → 166,022 B). The B4 threshold re-adjudication is the
caller's (Phase 9 / leader), exactly as the phase-5 evidence routed it.

**The other three guards are GREEN at HEAD:**

```
$ cd frontend && node ../.agents/shared/planning/metrics-unification/fe-t1-build/bundle-guard.js \
    dist/frontend/browser /tmp/phase8-build.log
(b) B2  initial_chunks_scanned = 37 · marker_hits = 0
(c) B3  chart_js_import_sites = 1 · static_sites = 0 · outside_metrics_tree = 0
        src/app/pages/metrics/charts/chart-panel.component.ts: dynamic=2 static=0
(d) B6  baseline_set_size = 9 · measured_set_size = 9 · added 0 / removed 0
```

**B4 (the lazy-only decomposition) is DISCLOSED as an EXCEEDANCE** — the
lazy-only portion grew because this phase adds a component and a plugin
to the metrics route chunk. The full numbers are in the final report; the
B4 threshold re-adjudication is the caller's (Phase 9 / leader), exactly
as the phase-5 evidence routed it.

---

## 16. The final tree state

```
$ git status --short
<the phase-8 evidence file + the PACKS.md registration — both committed by the phase>
```

```
$ git log --oneline -6
14b143c9f test(fe): make the phase-8 specs strict-mode clean under the scoped type-check
c899371ec test(fe): make the dark-family ceiling pin discriminating (the slice-before-filter variant)
e1b71d467 feat(fe): reflow the door-state banner so the 900 px collision cannot recur (8.6)
79892f345 feat(fe): mark where the panel's data ends and pin the drawer affordances (8.9, 8.10)
6566c0111 feat(fe): surface the FE's own selector truncation and keep dark families off the ceiling (8.7, 8.8)
b67d0ba13 feat(fe): render the door state once with an actionable panel and a page-scoped posture chip (8.2-8.5)
```

**Nothing pushed, merged, tagged or staged** (the phase's scope is
stage-only at lane end, and the promote is the user's ceremony).
