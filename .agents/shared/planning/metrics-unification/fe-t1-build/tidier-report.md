# Tidier Report — FE-only T1 Metrics Dashboard (wi-32a99618)

- **Pass:** Iteration 001 · dispatcher + 3 skill workers (readable `65d2b0b8`, hygiene `a1e9c224`, robustness `f567fd6f`) + apply phase reusing the readable worker (revived, context intact)
- **Review range:** `c4161335b..21075e1cf` — 103 files, +18,053/−3,215 (`frontend/` + `test/packs/metrics_fe_dual_side_test.sh`)
- **Fold range:** `21075e1cf..770528084` — 7 commits, 23 files, +164/−199
- **Verdict: PASS with escalations** — craftsmanship surface folded clean and byte-green; **1 High escalated** (pack gate composition — owner decision, below); **zero behavior change** (confirmation §6)

---

## 1. Findings by severity and disposition

### 🔴 High (2)

| # | Finding | Disposition |
|---|---|---|
| H-1 | **A-5 banner/badge style forks across 6 sites** — door-state SSOT inline vs byte-identical `.tab-banner`/`.stale-badge`/`.tab-hint`/`.tab-meta` copies in charts-tab `:129-162`, overview-tab `:109-142`, families-tab `:193-196`, shell `metrics.component.scss:22-43,156-167`; palette reappears in the registry-degraded badge + door-chip failure states | **FOLDED** `59e79f9f6` — new `pages/metrics/shared/tab-surface.scss` partial (byte-identical blocks, authority header naming `metrics-door-state.component.ts`); tabs consume via `styleUrls`; shell `@use` + `$banner-error-*`/`$stale-warn-*` vars; door-state inline untouched. Verified: banner-geometry spec, guard GREEN, initial_total **identical** |
| H-2 | **Pack declared composition is silently false** — `D1_FILES[136]` (svg-line-chart spec) and `D2_DECLARED[150]` (resources-gear-menu spec) name files THIS lane deleted; jest silently drops unmatched positionals (empirically proven via `--listTests`); replacement suites (`house-svg-renderer*`, `renderer-registry`, tabs/central-tab/resource/door-state/units specs) run in **no leg**; D2's BASE side silently loses a file → dual-side "identical command shape" erodes asymmetrically | **ESCALATED — owner: lane owner / tester-gate lane.** Re-pointing lists + adding a per-declared-file existence assert are coupled assertion changes (the assert alone flips current HEAD runs to FAIL); assertions frozen for this pass per commission. Stale headers `:12`/`:22` ride the same decision |

### 🟡 Medium (11)

| # | Finding | Disposition |
|---|---|---|
| M-1 | Dead phase-3 helpers `metricsBaselineDataset` + `MetricsBaselineSeries` (`metrics-baseline.ts:31-38,70-88`) — zero refs; superseded by phase-4 frame shape | **FOLDED** `0f7d5609e` (`METRICS_BASELINE_CONTRACT` kept — spec-pinned) |
| M-2 | 8 zero-ref TIMELINE_* compat aliases (`house-svg-renderer.ts:90,94,605-619`) — half the "ported alias" block was never consumed | **FOLDED** `0f7d5609e` (kept `PLOT_LEFT/RIGHT`, `TICK_COUNT`, `MARKER_RADIUS` — spec-referenced; engine-uniqueness guard strings untouched) |
| M-3 | Unused named import `HOUSE_CROSSHAIR_PLUGIN_ID` (`chart-panel.component.ts:30`) | **FOLDED** `0f7d5609e` (export kept — frozen-surface discipline) |
| M-4 | `'unit unknown'` literal mirrors at `chart-panel.component.ts:256` + `chart-panel.renderer.ts:58` vs SSOT `METRICS_UNIT_UNKNOWN` | **FOLDED** `f512dcbd3` (both sites import the SSOT; `components/charts` copies left — documented decoupling) |
| M-5 | Hand-copied string keys in `METRICS_TAB_COMPONENTS` (`tabs.components.ts:23`) — no compiler link to manifest ids | **FOLDED** `f512dcbd3` — derived `MetricsTabId = (typeof METRICS_TABS)[number]['id']`; record re-keyed; tsc proves the 7 keys exact |
| M-6 | Retired-page selector prefixes on the moved panels (`app-resources-live/timeline`, `app-resource-anomalies`) — 3 prefixes among siblings | **FOLDED** `e379b3dd4` → `app-metrics-*`; fresh whole-tree grep found exactly 6 refs (3 selectors + 3 template usages); **plus a dev-AOT build pass** covering the NG8001/NG8002 blind spot for a template-touching change |
| M-7 | `models/index.ts` barrel (1,075 lines) over the 1000-line threshold with no top-of-file size rationale | **FOLDED (docs-only)** `c003d6e3e` — ~5-line header (purpose, type-only re-export rationale, tail-note pointer). Pre-existing at base (1,059); lane delta 16 lines — disclosed |
| M-8 | Pack errexit asymmetry — header `set -uo pipefail` (no `-e`) but four `set -e` "restores" flip errexit ON mid-script (`:178,217,241,261`) | **FOLDED** `770528084` — dead restores deleted after command-by-command proof of observation-identity on pass AND fail legs; one header comment notes errexit deliberately OFF |
| M-9 | `fetchStatStrip` empty catch + FALSE comment ("the door's own banner carries the fact") — banner fires only on the catalog path; snapshot-path failures invisible (`central-tab.component.ts:901-908`) | **SPLIT:** comment fixed (zero-behavior, `c003d6e3e`); `console.warn` addition **ESCALATED — owner: caller** (observable console output = behavior change) |
| M-10 | Resources reads carry no client deadline — the hung-read class AM-4 eliminated for central tabs survives in tab 1 (`live-tab.component.ts:101-105`; service pre-existing outside delta) | **ESCALATED — owner: caller / behavior lane** (a deadline is behavior by definition; spans service + new consumers) |
| M-11 | `central-tab.component.ts` 918-line file, three concerns (fetch loop / pure projection / renderer plumbing) | **ROUTED — owner: future FE lane.** Within the acceptable band (<1000); pure-move extraction plan recorded (sibling `central-tab.panels.ts`, precedent = house renderer engine/component split); not executed to avoid late-lane byte churn against frozen guard numbers |

### 🟢 Low (13)

| # | Finding | Disposition |
|---|---|---|
| L-1 | Needless exports: `CHART_SERIES_COLORS` (internal-only), `MetricsSourceSlot` (zero refs; manifest narrowing is better typing) | **FOLDED** `0f7d5609e` (export dropped / type deleted) |
| L-2 | Dead const `HOUSE_SVG_RENDERER_ID` (`house-svg-renderer.renderer.ts:32-33`) — registrations use the MINI/TREND ids | **FOLDED** `0f7d5609e` |
| L-3 | Anomaly-marker radius `6` duplicated across both chart stacks, coupling undocumented | **FOLDED** `78ca46fc6` — `ANOMALY_MARKER_RADIUS` in `chart-theme.ts`; **deviation adjudicated:** `HOUSE_ANOMALY_MARKER_RADIUS` kept as `= ANOMALY_MARKER_RADIUS` alias (its spec imports the name — deleting would redden a passing spec, banned); value single-sourced |
| L-4 | Pack `sed "s#^#$FRONTEND_DIR/#"` metachar-fragile (`:161`) | **FOLDED** `770528084` — while-read process-substitution (in-family at `:157`); side-by-side equivalence proof: 39/39 entries identical |
| L-5 | `CHART_FLAGS` vocabulary const unreferenced (`renderer-registry.ts:61`) | **LEFT** (documented vocabulary for the next engine) + **ROUTED:** flags-coverage assert → semantics lane |
| L-6 | `crosshair-plugin.ts` has no colocated spec (both sibling plugins have one) | **ROUTED — owner: test-semantics lane** (adding spec content exceeds zero-behavior hygiene) |
| L-7 | `'unit unknown'` literals at `components/charts` sites (`house-svg-renderer.component.ts:439`, `.renderer.ts:66`) | **LEFT** — documented `components ↛ metrics` decoupling per `chart-theme.ts`; mirror class accepted there |
| L-8 | e2e gate stub-bootstrap scaffolding duplicated between the two Playwright gates | **LEFT** — weakest finding; adjacent to frozen D-guards; a third gate is the trigger to dedup |
| L-9 | "showing the last known window/list" copy unconditional on first-load failure (`resource-tab.component.ts:293-296,324-327`; string spec-pinned) | **ESCALATED — owner: future copy fold** (branch changes rendered copy = behavior) |
| L-10 | 503 branch exact-match vs 403's case-insensitive body match (`metrics.model.ts:667-670`) | **ESCALATED — owner: semantics lane** (widening reclassifies lower-cased bodies) |
| L-11 | `EXPECTED_BRANCH` default names the parent lane's branch (`:63`) | **ESCALATED — owner: pack maintenance** (flips unexported-run verdicts) |
| L-12 | D-1 node guard swallows the git failure and misattributes the verdict (`:316-324`) | **ESCALATED (report-only) — frozen guard surface**; fold into future D-1 maintenance |
| L-13 | Orphaned `set +e` no-ops remain after L/M-8 fold | **LEFT by scope** — follow-up observation handed to the pack-composition owner (H-2) |

**Escalation closed by adjudication:** `package.json` adds `chart.js ^4.6.1` + `jest-canvas-mock ^2.5.8` — **pre-adjudicated** by the user-ratified fe-ui-audit ruling (Chart.js sole vendor behind the intent+renderer seam; jest-canvas-mock the documented jsdom-canvas enabler, `setup-jest.ts:1-10` with a planted no-mock control). No action; disclosed for the record. Pack D-1 dependency-set re-verified `ok:true` at HEAD.

## 2. Verification — before vs after (baseline @21075e1cf → final @770528084)

| Gate | Before | After | Status |
|---|---|---|---|
| tsc ×3 (`tsconfig.json` / `app` / `metrics-fe`) | exit 0 | exit 0 | identical |
| jest recorded set | 12 suites / 266 pass | 12 suites / 266 pass | identical |
| Renderer suites + engine-uniqueness guard | green | green | identical |
| Door-state / units / banner-geometry / baseline-pins / range / tabs.manifest / central-tab / resource-tab | green | green | identical |
| Bundle guard | GREEN — B1 5,907,717 (frozen 5,909,018, limit ±2,048) · B2 0 · B3 0 · B6 0/0 | GREEN — B1 **5,907,717 identical** · B2 0 · B3 0 · B6 0/0 | identical |
| Measure (disclosed shift) | lazy 1,963,806 · route chunk 120,176 · lazy-only fetch 165,207 | lazy 1,964,654 · route chunk 121,024 · lazy-only fetch 166,055 | **+848 net lazy bytes** (A-5 partial compiles per-component; threshold 185,000) — disclosed + justified |
| Dev AOT build (`ng build -c development`) | — | exit 0, 0 template errors (batch-4 guard) | added coverage |
| Pack (full hermetic run) | not run pre-fold | d1 224/224 · d2_declared 131/131 · typecheck/dev/prod ✓ · D-1 ok:true · d2_derived 39 files, 1,832 pass / **2 fail** | 2 fails **proven pre-existing at base** (re-run @21075e1cf: identical 2F/117P — `settings.component.spec.ts` season-dependent DST fixture; new evidence, routed) |
| Prompt-artifact baseline | 161/161 OK | 161/161 OK | identical (agents/ untouched) |
| Worktree | clean | clean, no push | ✓ |

## 3. Deviations adjudicated by dispatcher

1. **B6 alias kept** (`HOUSE_ANOMALY_MARKER_RADIUS = ANOMALY_MARKER_RADIUS`) — spec imports the name; deletion would redden a passing spec. Accepted: value single-sourced, body uses the SSOT.
2. **Pack headers `:12`/`:22` left stale** — refreshing them while the lists stay stale (H-2 escalated) would make comments contradict the arrays; both ride the owner's one coupled decision.
3. **`set +e` no-ops left** — fold list scoped only the `set -e` restores; noted as L-13 for the pack owner.

## 4. Zero-behavior-change confirmation

Every fold is style/dead-code/type-level/comment-only. All suites byte-green before and after with identical counts; bundle guard GREEN with the initial total byte-identical; the only runtime-observable deltas are the disclosed +848 net lazy bytes (guard-sanctioned, within thresholds) and three renamed DOM tags (grep-proven zero external references). No new dependency, no threshold change, D-1/D-2/D-3 untouched, FE-only, agents/ untouched.

## 5. Deferred to Reviewer (observed, out of Tidier scope)

- Retirement specs assert absence by grepping e2e/ts SOURCE text — robustness of source-scanning assertions is correctness territory.
- `RESOURCES_REDIRECT_TAB_ID` deliberate non-derived literal (`app.routes.ts:70-77`) — intentional-design judgment.
- `central-tab.fetchTrend` skip-vs-replace in-flight window logic — correctness reasoning.
- `decodeMetricsDoorError` maps any non-HTTP error object to `network` (unreachable via current pipe; future throwing operator would misclassify).
- `chart-panel.renderer.ts` self-registers into the shared registry via import side-effect — architectural wiring.
- `live-tab` error surface uses a different visual language (icon + headline card) than the door-state/tab banners — design consistency.
- Barrel `export type *` value-import watch (compile-time safe today, verified).
- jest-canvas-mock + ResizeObserver stub adequacy as a test environment — semantics.
- Pack `check_cap` fires at 4 checkpoints only; worst-case wall (≈3,300 s) can exceed the declared 900 s budget — exit-124 contract is checkpoint-based.

## 6. Fold commits (all local, no push)

```
59e79f9f6 refactor(fe): consolidate the tab-surface banner/badge styles into one shared partial (A-5)
0f7d5609e refactor(fe): delete the zero-reference dead exports left by the consolidation
f512dcbd3 refactor(fe): wire the unit placeholder + tab-id vocabulary to their SSOTs (type-level)
e379b3dd4 refactor(fe): rename the resource panels' selectors to the app-metrics- prefix
c003d6e3e docs(fe): barrel header + truthful stat-error comment (comment-only)
78ca46fc6 refactor(fe): single-source the anomaly-marker radius across both chart stacks
770528084 test(fe): pack shell hygiene — drop dead set -e restores, while-read D2_DERIVED (observation-identical)
```

**Recommendations (priority):** (1) owner decision on H-2 pack composition — re-point lists + add the existence-assert in one commit, refresh headers `:12`/`:22` and sweep the `set +e` no-ops in the same pass; (2) caller call on M-9 `console.warn` + M-10 ResourcesService deadline (both small behavior lanes); (3) route `settings.component.spec.ts` season-rot to the settings/timezone debt lane (base-proven, `America/New_Y York` DST fixture renders UTC-05:00 in November) — the FE convention "use relative dates" already documents the class.
