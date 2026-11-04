# Council fold record — FE-only T1 metrics dashboard (wi-32a99618)

**Authority:** `code-review.md` (agentic councilor — APPROVE-WITH-ADVISORIES,
0C/0W/4A) and `code-review-coding-model.md` (coding councilor —
APPROVE-WITH-ADVISORIES, **1 CRITICAL**/1W/6A). Both read in full before any
edit; both were re-read against the tree at every disposition below.

**Tree:** worktree `/home/nea/ensemble-src-wt-fe-metrics-dashboard`, branch
`feature/fe-metrics-dashboard`. Head at fold start `7d50e4a73` (the agentic
review commit on top of `aa3f5c15f`). **FE-only; no daemon, DB, migration or
live-write contact. NO threshold changed** (`bundle-baseline.json` is
byte-identical to the pre-fold file — `git diff` empty for it).

**Verdict carried:** the fold closes the one CRITICAL, remediates the WARNING,
and disposes of every advisory — 8 rows covering all 10 advisory IDs:
**6 folded** (agentic-1, coding-A-2, coding-A-3, agentic-2, coding-A-6,
coding-A-1/agentic-4) · **1 routed** (coding-A-5 → tidier) · **1 noted**
(agentic-3 + coding-A-4 are the same finding). Nothing in the D-1 contract,
the D-2 posture, or D-3's redirect moved.

---

## 1. C-1 — the overlay shadowed the anomaly markers (FOLDED)

### The defect (confirmed, then reproduced under a REAL pointer)

`house-svg-renderer.component.ts` painted the full-plot hover overlay
(`<rect class="house-overlay">`, `fill="transparent"`, CSS
`pointer-events: all`) **after** the anomaly-marker group. SVG hit-testing
delivers the pointer to the **topmost** hit-target, and `pointer-events: all`
captures regardless of paint — so the overlay was the topmost target across
the whole plot, and the marker `<g>` (a **sibling**) never received a real
pointer. The unit leg passed anyway because jsdom performs no SVG hit-testing
and the leg dispatches the click straight at the element.

Reproduced at the pre-fix ordering by the NEW browser leg (see §1.3): at the
marker's rendered centre, `document.elementFromPoint` answered
**`rect.house-overlay`** — the exact predicted signature.

### 1.1 The fix — paint order, not pointer plumbing

```
BEFORE (pre-fold, template order)            AFTER (folded)
──────────────────────────────────           ──────────────────────────────────
<path house-series-area>  × N                <path house-series-area>  × N
<path house-series-line>  × N                <path house-series-line>  × N
<circle house-point>      × N                <circle house-point>      × N
<circle house-last-value> × N                <circle house-last-value> × N
<g house-anomaly-marker>  × N   ← clickable  <rect house-overlay>       ← hover target
<circle house-hover-point> (hover)           <g house-anomaly-marker>  × N ← CLICKABLE
<rect house-overlay>            ← TOPMOST    <circle house-hover-point> (hover)
<text house-empty>                           <text house-empty>
```

**Chosen remedy: (A) move the `<rect>` before the marker loop** — the
council's first option. Why not (B) `pointer-events: none` + listeners on
the plot `<g>`/`<svg>`: with the overlay non-hit-testable, the empty plot
area has no painted descendant, so a `pointermove` over blank plot would
never reach an ancestor listener — the nearest-point hover (ADJ-ii's
extension, which the council required to keep working) would degrade to
"only over drawn geometry". Re-ordering keeps **both** behaviours: the
overlay still sits above the series (a pointer over a line reaches it) and
below the markers (a pointer over a marker reaches the marker).

**CSS diff: NONE.** `.house-overlay { pointer-events: all }` and
`.house-marker-hit { pointer-events: all }` are unchanged; the fix is purely
document order. A template comment now states the invariant and warns that
the template is a TS template literal (no backticks in template comments —
the first edit attempt broke the build on exactly that, caught by `tsc`).

### 1.2 The unit pin (jsdom can assert paint order)

New leg in `house-svg-renderer.spec.ts`: *"paints the hover overlay BENEATH
the anomaly markers (a real pointer must reach them)"*. It asserts the
document-order relation **both ways**:

- the overlay **follows** the series line (`HOUSE_SERIES_LINE` precedes it) —
  else the hover listener is unreachable over a line;
- the overlay **precedes** the marker group — else the marker is shadowed.

**Mutation-verified (executed, then restored):** with the overlay moved back
after the markers, the leg is **RED** at the predicted assertion
(`compareDocumentPosition … DOCUMENT_POSITION_PRECEDING`); restored → green.
Renderer suite `3 suites / 84 tests`, all green (+1 node vs the review's
record — the new leg).

### 1.3 The Playwright REAL-POINTER leg (green-at-fix MANDATORY — green)

New leg in `frontend/e2e/resources-gate.spec.ts` (the resources gate config,
`playwright.resources-gate.config.ts`, port 14202 — the module's own gate):

```
[C-1] a REAL pointer reaches an anomaly marker on the house-SVG fallback
      and opens the drill-down
```

How it works:

1. **The fallback is entered for real** — `page.addInitScript` makes
   `HTMLCanvasElement.prototype.getContext('2d')` throw. Chart.js 4.6.1 calls
   `platform.acquireContext` **inside** the constructor, so the throw
   propagates out of `new Chart(...)` → the panel's `mount()` catch →
   `onMountFailed` → the house SVG **with its badge**. Nothing is stubbed in
   the chart layer (a `null` context, by contrast, does NOT throw in 4.6.1 —
   it `console.error`s and returns — which is why the first attempt never
   reached the fallback; recorded so a future author does not repeat it).
2. **Viewport 1600×1200 + `scrollIntoViewIfNeeded`** — raw mouse coordinates
   are viewport coordinates; below the fold both `page.mouse.*` and
   `elementFromPoint` no-op (the second thing the first run taught us).
3. **(a) the hover extension still works** — a real `page.mouse.move` into
   an empty patch of the plot publishes `[data-test="house-hover-point"]`.
4. **(b) a REAL pointer at the marker's rendered centre** — the
   `.house-marker-hit` circle's bounding-box centre, then
   `elementFromPoint` must answer a `house-marker*` element and **never**
   `house-overlay`, then `page.mouse.click(x, y)`.
5. **The drill-down opens** — `[data-test="drilldown-panel"]` visible, kind
   `memory`, severity `high`, and the evidence round-trip renders
   (`[data-test="contributors"]`), i.e. the click travelled the whole
   marker → consumer → request path.

**Results (executed):**

| run | ordering | outcome |
|---|---|---|
| green-at-fix (mandatory) | folded | **PASS** — full gate `4 passed (38.3s)` |
| red-at-base-class (optional, taken) | overlay restored to after-markers | **FAIL** at `expect(topmost).not.toContain('house-overlay')` — received `"rect.house-overlay"` |

The gate's other three legs (AC8 gear-menu/live strip, AC8 deep-link, AC8
degraded-200) run alongside and stay green; `metrics-gate` (4 legs) also
re-ran green (unchanged).

---

## 2. W-1 — the prod credential at rest in `/tmp` (REMEDIATED)

Full record with the corrected fence pattern, the pre-deletion inventory,
the per-class disposition table, the credential-scan hits and the relocation
proof: **`phase9-evidence.md` §9.10** (added by this fold). Summary:

- **(a) corrected pattern**: the fence helper SOURCES the five `POSTGRES_*`
  values from the install's own `.env` (verified mode `0600`, exactly one
  occurrence of each key — so it *is* the five values the old helper inlined)
  inside a subshell, maps them to libpq names for ONE `psql` command, and
  never puts the secret on argv or in any lane-written file.
  `<the prod password>` appears in no artifact of this fold.
- **(b) sweep — inventory first, then remove.** 50 top-level entries / 74
  files / ~3.1 MB. **25 files RELOCATED** (`pixels/`, committed — explicit
  leader override of the "binary evidence stays out" convention, because both
  reviews consume it), **49 files REMOVED**, **0 kept**. `lsof` was empty
  before the sweep (no standing process), and nothing needed keeping.
- **(c) verification**: `ls -d /tmp/fe-t1-*` → no match; a `/tmp`-wide
  credential scan (`PROD_PW=` / `PGPASSWORD=<value>` / `postgres://user:pass@`
  / `Bearer <token>`) → zero hits from this lane; the only other `Bearer`
  strings were the `www-authenticate: Bearer` scheme word and a runtime
  template in a capture driver (no literal token).
- **No credential rotated** — user-owned, untouched.

---

## 3. Advisory dispositions — every one recorded

| ID | Doc | Disposition | Where |
|---|---|---|---|
| **agentic-1** (uniqueness pin is path-scoped) | agentic | **FOLDED — widened** | `house-svg-engine-uniqueness.spec.ts`: the offender pattern is now BASENAME-anchored (`^svg-line-chart(.|$)` / `^svg-timeline(.|$)`) over the whole `src/app` walk — extension-agnostic, directory-agnostic — exactly the reviewer's preferred form. Mutation-proven: a probe engine planted at `src/app/pages/__mutation-probe/svg-line-chart.component.ts` (no `charts` segment) is **RED** under the widened pin and would have been **green** under the old one; probe removed, `2/2` green. |
| **coding-A-2** (`statFamilyIds` optional array) | coding | **FOLDED** | `tabs.manifest.ts`: `statFamilyIds?: NonEmpty<MetricsRegisteredFamilyId>` (the file's own helper name), matching `familyIds`; the empty-array sentinel is now unrepresentable. Consumers (`?? []` at two call sites, one spec projection) type-check unchanged; `tsc -p tsconfig.metrics-fe.json` rc 0. |
| **coding-A-3** (137 vs 141) | coding | **FOLDED** | `phase7-evidence.md:542` summary row → **141** (matching §6, §6.1 and both reviewers' re-runs). |
| **agentic-2** (35-JS wording) | agentic | **FOLDED** | `phase9-evidence.md` §9.8 B2 row → "0 hits across the **36-file** initial set (35 JS + 1 CSS)". |
| **coding-A-6** (duplicated B4 fragment) | coding | **FOLDED** | `plan-overview.md` §6.3 row B4: the second copy dropped (phrase now occurs once in the file). |
| **coding-A-1 / agentic-4** (`payload.daemon.pid` unguarded) | both | **FOLDED (optional-taken)** | `live-tab.component.html:75` → `payload.daemon?.pid ?? '—'` — one-character symmetry, pre-existing and unreachable through the real door; zero behavioural change, asymmetry gone. |
| **coding-A-5** (duplicated banner colours) | coding | **ROUTED — owner: the tidier** | `charts-tab.component.ts:138` and `overview-tab.component.ts:118` still carry inline `#2a1414/#7f1d1d/#fca5a5`; they should collapse onto `MetricsDoorStateComponent`'s CSS custom properties. Not folded here (both reviewers classed it tidier work; it touches two retired surfaces). **Owner: tidier lane (this lane's tidier pass, next commission).** |
| **agentic-3 / coding-A-4** (brief count labels) | both | **NOTED — no repo change** | The drift is in the dispatch brief's metadata (file/commit counts, suite-count labels), not in the tree or the evidence's own recorded runs. Noted for future briefs: cite suite PATHS, and re-derive counts at brief-freeze time. |

---

## 4. Record correction — the phase-6/phase-8 concurrency incident

**Corrected in BOTH documents** (`phase6-evidence.md` §"Consequences" and
`phase8-evidence.md` §14.1). The earlier wording — *"the file sets were
disjoint"* — was **imprecise**: `frontend/src/app/pages/metrics/tabs/
central-tab.component.spec.ts` was touched by **both** phases.

Re-derived mechanically at the fold (not from prose):

```
phase-6 commits (c59d44434,b14f3a5f7,aa7c0b04e,fd6322639) → 13 files
phase-8 commits (b67d0ba13,6566c0111,79892f345,f7b88f56c,14b143c9f,c899371ec) → 20 files
intersection → exactly ONE:
  frontend/src/app/pages/metrics/tabs/central-tab.component.spec.ts
```

**Nothing was lost.** Each phase added its own `it` blocks to that file; both
sets survive at the tip and the spec is green — `44 passed / 44 total`,
including phase 6's unit-vocabulary legs (6.3) *and* phase 8's 8.2–8.5 /
8.7 / 8.8 / 8.9 / 8.10 legs.

---

## 5. Post-fold re-runs (all executed in this worktree)

| check | command | result |
|---|---|---|
| production build | `cd frontend && ./node_modules/.bin/ng build --configuration production` | **rc 0** |
| measurement tool | `node …/bundle-baseline.measure.js dist/frontend/browser` | `initial_total_bytes=5907717`, `initial_file_count=36`, `vendor_static_edge_count=0`, `vendor_in_initial_set=false`, `vendor_invariant_ok=true`, route key resolves to exactly one chunk |
| bundle guard | `node …/bundle-guard.js dist/frontend/browser <build log>` | **GREEN** — B1 Δ **−1,301 B** (limit ±2,048 vs 5,909,018); B2 **0/36**; B3 **0 static**, 1 dynamic site (2 dynamic forms) inside `src/app/pages/metrics/**`; B6 set diff **0/0** |
| B4a (unchanged threshold) | `gzip -9 -c chunk-TXUCW6LI.js \| wc -c` | **71,054 B gz** ≤ 82,000 |
| B4b (unchanged threshold) | measurement tool | lazy-only **165,207 B** ≤ 185,000 (the +22 B vs the phase-9 record is this fold's own route-chunk source delta — the template comment plus the re-ordering; still ~19.8 kB under the pin) |
| `tsc` app + metrics-fe | `--noEmit -p tsconfig.app.json` / `-p tsconfig.metrics-fe.json` | **rc 0 / rc 0** |
| renderer suites | `jest src/app/components/charts` | **3 suites / 84 tests** green |
| resource suites | `jest src/app/pages/metrics/resource` | **6 suites / 65 tests** green |
| metrics tree | `jest src/app/pages/metrics` | **27 suites / 434 tests** green |
| central-tab spec (shared file) | `jest …/tabs/central-tab.component.spec.ts` | **44/44** green |
| whole FE suite | `jest --ci --silent --runInBand` | **4 failed suites / 153**, **9 failed / 4,711** — the **SAME 9 base-proven names** (settings timezone ×2, schedule-create-dialog ×3, jobs-grouping ×3, jobs-filter-state ×1) ⇒ **net-new = 0** |
| NEW Playwright leg | `playwright test -c playwright.resources-gate.config.ts` | **4 passed (38.3 s)** — incl. `[C-1]` |
| metrics gate | `playwright test -c playwright.metrics-gate.config.ts` | **4 passed (37.3 s)** |
| prompt-artifact baseline | `python scripts/protection/generate_prompt_artifact_baseline.py --check` | **OK — 161/161** |

**Thresholds: unchanged** (B1 ±2,048 vs 5,909,018; B2 0; B3 0 static; B4a
82,000; B4b 185,000). `bundle-baseline.json` has no diff. D-1/D-2/D-3 are
untouched by this fold.
---

## 6. FIX ORDER #2 routed register

This pass folded the tidier's **H-2** escalation (the false-green pack class)
plus one coupled correctness correction it uncovered, in ONE pack commit:

- **H-2** — `D1_FILES` re-pointed to the lane's ACTUAL current specs (the
  `svg-line-chart` entry was a Phase-4 deletion; 20 replacement suites
  entered the list), `D2_DECLARED` trimmed of the Phase-7
  `resources-gear-menu` deletion, and a **per-declared-file existence
  assertion** added — D1 on the HEAD leg, D2 on BOTH legs (that list's
  membership invariant). The pack header (`:12`/`:22`) was refreshed in the
  same commit, and the L-13 `set +e` no-ops were swept (errexit is now
  asserted explicitly OFF at the top of the script, which is what makes
  those no-ops provably dead).
- **Coupled correction (found by running the BASE leg, not by reading):**
  the pack's `D1_MARKER` was `src/app/models/metrics.model.spec.ts` — a
  file the **v0.28.6 central-metrics-v1 lane had already shipped**, so it
  EXISTS at this lane's base (`c4161335b`). The BASE leg therefore
  mis-discriminated as HEAD: it ran `d1` against a MIXED set (8 inherited
  files present, the lane's own absent → jest silently dropped the absent
  ones) and forced Leg 7's expected dependency set to the HEAD's
  `["chart.js"]`/`["jest-canvas-mock"]` while the base tree's
  `package.json` delta against `BASE_REF` is EMPTY → a false
  "D-1 DEPENDENCY-SET BREACH". The marker is re-pointed to
  `src/app/app.resources-retirement.spec.ts` (this lane's Phase-7 artifact
  — PROVEN absent at the base, present at the head).

**Product code was NOT changed by this pass** — the pack file and this
record only.

The items below are **routed, not folded**. Each carries its owner.

### 6.1 — `fetchStatStrip` `console.warn` + the `ResourcesService` missing deadline

| Item | Source | Why it is routed, not folded | Owner |
|---|---|---|---|
| `fetchStatStrip` empty catch with no console output (the snapshot-path failure is invisible; the banner only fires on the catalog path — `central-tab.component.ts:901-908`) | tidier **M-9** (SPLIT: the FALSE comment was folded zero-behavior in `c003d6e3e`; the `console.warn` addition was ESCALATED) | observable console output is a **behavior change** — out of this lane's pre-stage window | **the metrics-lane follow-up** |
| The Resources reads carry **no client deadline** — the hung-read class AM-4 eliminated for the central tabs survives in tab 1 (`live-tab.component.ts:101-105`; the service side is pre-existing and outside the delta) | tidier **M-10** | a deadline is behavior by definition and spans service + consumers | **the metrics-lane follow-up** |

### 6.2 — `central-tab` 918-line pure-move extraction

| Item | Source | Why it is routed, not folded | Owner |
|---|---|---|---|
| `central-tab.component.ts` is 918 lines with three concerns (fetch loop / pure projection / renderer plumbing) | tidier **M-11** | within the acceptable band (<1000); a pure-move extraction is recorded (sibling `central-tab.panels.ts`; precedent = the house renderer engine/component split) but was NOT executed, to avoid late-lane byte churn against the frozen guard numbers | **the next FE lane** |

### 6.3 — The tidier's frozen / left items (as the tidier recorded them)

| ID | Item | Disposition as recorded | Owner |
|---|---|---|---|
| L-5 | `CHART_FLAGS` vocabulary const unreferenced (`renderer-registry.ts:61`) | **LEFT** — documented vocabulary for the next engine; the flags-coverage assert is **ROUTED** | semantics lane |
| L-7 | `'unit unknown'` literals at the `components/charts` sites (`house-svg-renderer.component.ts:439`, `.renderer.ts:66`) | **LEFT** — the documented `components ↛ metrics` decoupling per `chart-theme.ts`; the mirror class is accepted there | — (accepted, no owner) |
| L-8 | e2e gate stub-bootstrap scaffolding duplicated between the two Playwright gates | **LEFT** — the weakest finding; adjacent to the frozen D-guards; a third gate is the trigger to dedup | — (accepted, no owner) |
| L-9 | "showing the last known window/list" copy is unconditional on first-load failure (`resource-tab.component.ts:293-296,324-327`; the string is spec-pinned) | **ESCALATED** — branch changes render copy = behavior | future copy fold |
| L-10 | 503 branch exact-match vs 403's case-insensitive body match (`metrics.model.ts:667-670`) | **ESCALATED** — widening reclassifies lower-cased bodies | semantics lane |
| L-11 | `EXPECTED_BRANCH` default names the parent lane's branch (`metrics_fe_dual_side_test.sh:63`) | **ESCALATED** — it flips unexported-run verdicts; **NOT changed by this pass** (out of the H-2 scope: it is a drift-gate semantic, not a declared-composition defect) | pack maintenance |
| L-12 | The D-1 node guard swallows the git failure and misattributes the verdict (`:316-324`) | **ESCALATED (report-only)** — frozen guard surface | pack maintenance |
| L-13 | Orphaned `set +e` no-ops after the L/M-8 fold | **LEFT by scope → FOLDED in THIS pass** (swept, with errexit asserted explicitly OFF at the top of the script) | — (closed here) |
| §5 | The tidier's "Deferred to Reviewer" observations (source-scanning retirement assertions; the deliberate `RESOURCES_REDIRECT_TAB_ID` literal; the `central-tab.fetchTrend` skip-vs-replace window; the `chart-panel.renderer.ts` self-registration side-effect; the `live-tab` error-surface visual language; the `export type *` barrel watch; the jest-canvas-mock + ResizeObserver stub adequacy; `check_cap` at 4 checkpoints vs the 900 s budget) | **frozen** — observed, out of the tidier's scope | reviewer / future FE lane |

Also frozen and untouched by this pass: the **D-1 / D-2 / D-3** guard
surfaces, the **bundle-guard thresholds** (B1 ±2,048 vs 5,909,018; B2 0;
B3 0 static; B4a 82,000; B4b 185,000) and `bundle-baseline.json`.

### 6.4 — The 2 `d2_derived` settings / DST season-rot fails

| Item | Source | Why it is routed, not folded | Owner |
|---|---|---|---|
| `src/app/pages/settings/settings.component.spec.ts › Timezone preference (user-timezone-setting)` — **2** fails: *"renders the timezone section with the native picker when Intl.supportedValuesOf is available"* + *"uses the API list when the GET /api/settings/timezones response is non-empty"* | tidier's **new base-proven evidence** (`tidier-report.md` §2, "Pack (full hermetic run)": `d2_derived 39 files, 1,832 pass / 2 fail` — "2 fails **proven pre-existing at base** (re-run @21075e1cf: identical 2F/117P — `settings.component.spec.ts` season-dependent DST fixture; new evidence, routed)"), itself riding the phase-1 base proof (`phase1-evidence.md` §"BASE-PROVEN" — the identical failures occur at the lane base `c4161335b` in a throwaway detached worktree) | the cause is the **calendar**: in November `America/New_York` is on STANDARD time (UTC−05:00) while the specs hard-code the summer offset (UTC−04:00). The FE convention "use relative dates" already documents the class | **the settings/timezone debt lane** |

**This pass's independent base proof** (re-run in the throwaway detached base
worktree at `c4161335b`, this lane's changes absent): the base leg's
`d2_derived` sweep is **38 files / 1,801 tests / 2 failed / 1,799 passed**
with the **same two** `settings.component.spec.ts` names, and
`frontend/src/app/pages/settings/{settings.component.spec.ts,settings.component.ts}`
are **blob-identical** base↔HEAD (`80576cbe…` / `fb6d2e8e…`). The failure is
therefore structural base debt, not this lane's.

### 6.5 — NEW in this pass: the `job-queue-indicator` equal-timestamp tie-break flake

A **third** `d2_derived` failure appeared in one of the five HEAD-leg sweeps
of this pass, and did not appear in the other four:

```
● JobQueueIndicatorComponent Logic › recentJobs (public computed) ›
  should filter out pending, processing, and paused jobs (terminal only via isTerminalStatus)
  expect(ids).toEqual(['c1','f1','d1','x1','s1'])   →  received ['s1','c1','f1','d1','x1']
```

**Classification — a load-sensitive, base-proven flake; NOT a lane
regression and NOT a deterministic break:**

| Evidence | Result |
|---|---|
| Same tree, same composition, 5 HEAD sweeps | **4× `2 failed`, 1× `3 failed`** ⇒ nondeterministic |
| The spec in isolation, 5 runs at HEAD | **5/5 green** (134/134) |
| The spec in isolation, 5 runs at the base worktree | **5/5 green** (134/134) |
| `frontend/src/app/components/job-queue-indicator/job-queue-indicator.component.spec.ts` base↔HEAD | **blob-identical** |
| `frontend/src/app/testing/job-test-helpers.ts` (the mock factory) base↔HEAD | **blob-identical**; `git diff base..HEAD -- frontend/src/app/testing/` and `-- .../job-queue-indicator/` are both **empty** (this lane touches neither) |
| Mechanism | `createMockJob` stamps `created_at`/`completed_at` with `new Date().toISOString()` — **millisecond** resolution. The assertion is an **equal-timestamp stable-sort tie-break**: if the 8 mock creations land inside one millisecond the order is `c1,f1,d1,x1,s1` (pass); if the clock ticks between them, `s1` carries the newest stamp and a desc sort puts it **first** (fail). Under the full 39-file sweep the tick is likelier than in an isolated run |

**Owner: the `job-queue-indicator` spec's owning lane.** The fix is a
timestamp-independent fixture (inject a fixed clock or distinct stamps) —
out of this lane's scope, and product code was not touched here.

---

## 7. FIX ORDER #3 routed register (the B-1 + N5 fold, 2026-11-04)

This pass folded the tester gate's **BLOCKER B-1** (FE-side dual-vocabulary
mapping — the leader ruling FE-only stands) and **N5** (guard enforcement).
Full evidence: `b1-n5-fold-evidence.md` beside this record. The items below
are **routed, not folded**; each carries its owner.

| Item | Source | Why it is routed, not folded | Owner |
|---|---|---|---|
| **Daemon-side one-vocabulary normalization** — `/api/resources/anomalies` serves the ENGINE `series_key` verbatim (`daemon/routers/resources.py:820`, `ResourceAnomaly.series_key`) while `/api/resources/history` synthesizes FE-style keys (`:675`); the intersection is `[]`. The durable fix normalizes the anomaly door's keys at the `:820` serialization site (or derives both doors from one vocabulary), with the KB→MB unit question settled in the same change (`daemon.rss_kb` peak 345996 KB ≈ `daemon_rss_mb` ÷1024 — the scale table in `timeline-series.ts` carries the derivation). | tester gate **B-1** + this fold | daemon code is READ-ONLY in this lane (FE-only ruling); the FE shim (`ENGINE_SERIES_COMPAT` + the instance pattern in `timeline-series.ts`) is the documented COMPATIBILITY layer until the daemon speaks one vocabulary — when that lands, the shim becomes deletable in the same lane | **the metrics lane** |
| `tsconfig.spec.json` fails `tsc --noEmit` at `src/app/app.component.spec.ts:2127` (a duplicated `});` — 2× TS1128) | this fold's `tsc` run | the file is **byte-identical to the lane tip received** (`git status`/`git diff` empty; the tester's evidence commit `ff064658c`) — pre-existing tip debt outside this fold's scope; the pack's scoped tsconfig (`tsconfig.metrics-fe.json`) does not include the file, which is why the gate's typecheck leg stayed green | **the app-shell spec's owning lane** |
| `CHART_SERIES_COLORS[0]` (`#38bdf8`) is byte-identical to `CHART_SEVERITY_COLORS.low` — a canvas-pixel probe cannot distinguish a `low` marker dot from the first series' line/legend colour | this fold's C-1 driver (first probe iteration matched the RSS LINE, not the marker) | widening either palette is a design change with cross-stack parity implications (`chart-theme.ts` is the shared token); the driver now probes only severities present in the payload | **the chart-theme owning lane** (next palette touch) |
