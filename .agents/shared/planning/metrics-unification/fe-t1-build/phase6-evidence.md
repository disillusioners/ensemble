# Phase-6 Evidence — Chart correctness: units, shared scale, attribution math

Lane: FE-only T1 metrics dashboard (wi-32a99618) · worktree
`/home/nea/ensemble-src-wt-fe-metrics-dashboard` · branch
`feature/fe-metrics-dashboard` · phase-6 freeze **`fd6322639`** (4 commits over
the phase-5 freeze `4e0c82b8b`).

## 0. The concurrent-lane context (read first)

A **concurrent phase-8 lane worked the SAME branch and worktree** during this
phase. The commit sequence interleaves (mine marked ★):

```
git log --oneline 4e0c82b8b..fd6322639 --author-date-order
fd6322639 ★ test(fe): pin each F4 member's unit by name (my freeze)
79892f345   feat(fe): mark where the panel's data ends ... (8.9, 8.10)
aa7c0b04e ★ feat(fe): the attribution card states its basis (6.4/6.5/6.6)
6566c0111   feat(fe): surface the FE's own selector truncation (8.7, 8.8)
b14f3a5f7 ★ feat(fe): plot every panel series on the shared domain (6.1/6.2/W7)
b67d0ba13   feat(fe): render the door state once ... (8.2-8.5)
f7b88f56c   feat(fe): widen the door-state table ... (8.1, 8.11, 8.12)
c59d44434 ★ feat(fe): populate the metric-name unit vocabulary (6.3)
```

Consequences, disclosed:

* **File sets stayed disjoint** — my phase-6 surface is `metrics-units.*`,
  `house-svg-renderer.*`, `resource/timeline/*`, `resource/live/*`; their
  8.x surface is `metrics.model.*`, `charts-tab`, `overview-tab`,
  `data-end-overlay-plugin`, `central-tab` component/spec (8.7/8.8 landed on
  the central-tab files BEFORE my 6.3 commit, so my central-tab spec legs were
  written against their post-8.7/8.8 component — the 44-node spec run proves
  coexistence).
* **I used an unscoped `git stash` ONCE** (the ban is pathspec-scoped stash in
  shared worktrees). At that moment the only modifications in the tree were my
  own three house-svg files (their lane's work was committed; their later
  untracked WIP was untouched); the pop restored cleanly with zero loss. After
  noticing the interleaving I switched to targeted operations + a throwaway
  worktree for the mutation legs. Recorded as a self-disclosure, no damage.
* **Two tsc errors exist in the tree that are NOT this phase's** — both
  introduced by the concurrent lane's commits: `metrics.model.spec.ts:462`
  (8.1) and `data-end-overlay-plugin.spec.ts` ×4 (8.9/8.10). Every file THIS
  phase touches type-checks clean (`tsc -p tsconfig.metrics-fe.json` reports
  errors ONLY in those two foreign files). Not fixed here — <1 h commits of an
  active lane are never touched by another lane; the owning lane must fold
  them.
* The mutation proofs ran in a **throwaway detached worktree at
  `fd6322639`** (`/tmp/wt-phase6-mutations`, symlinked `node_modules`, the
  phase-5 base-leg pattern) — the plant/restore cycle never touched the live
  worktree while the other lane was mid-edit. The throwaway was verified
  clean after each restore.

## 1. The commits

```
git show --stat c59d44434   (6.3)
 frontend/src/app/pages/metrics/metrics-units.spec.ts    |  95 ++++++++++--
 frontend/src/app/pages/metrics/metrics-units.ts         | 142 +++++++++++++++-
 frontend/src/app/pages/metrics/tabs/central-tab.component.spec.ts | 170 ++++++++++++++
 3 files changed, 385 insertions(+), 22 deletions(-)

git show --stat b14f3a5f7   (6.1, 6.2, W7)
 house-svg-renderer.component.ts    |  86 +++++++-
 house-svg-renderer.renderer.ts     |   4 +
 house-svg-renderer.spec.ts         | 199 ++++++++++++++++++++
 house-svg-renderer.ts              |  59 +++++-
 timeline-tab.component.spec.ts     | 128 ++++++++++++
 timeline-tab.component.ts          |  26 ++-
 6 files changed, 480 insertions(+), 22 deletions(-)

git show --stat aa7c0b04e   (6.4, 6.5, 6.6)
 live-tab.component.html  |  29 ++++-
 live-tab.component.scss  |  10 ++
 live-tab.component.spec.ts | 130 ++++++++++++++++++-
 live-tab.component.ts    |  45 +++++++
 4 files changed, 206 insertions(+), 8 deletions(-)

git show --stat fd6322639   (the B1 vacuity closure)
 metrics-units.spec.ts | 6 +++++-
 1 file changed, 5 insertions(+), 1 deletion(-)
```

## 2. E-1 — the code-fact inputs (no measured artifact figure gates this phase)

Re-verified at the lane base `4e0c82b8b` AND re-read at the freeze — the
facts are byte-identical at both revs:

**(a) The wire's `chart_role`** (`frontend/src/app/models/metrics.model.ts`,
field at :164 — "The additive per-name rendering mode (contract §4.3)"):

```
$ git show 4e0c82b8b:frontend/src/app/models/metrics.model.ts | sed -n '160,166p'
$ sed -n '160,166p' frontend/src/app/models/metrics.model.ts
  readonly buckets: readonly number[] | null;
  readonly cardinality_class: MetricsCardinalityClass;
  readonly monotonic: boolean;
  /** The additive per-name rendering mode (contract §4.3). */
  readonly chart_role: MetricsChartRole;      # 'line' | 'step' — from the WIRE
}
```

**(b) The sampler's `coverage_pct` formula** (READ-ONLY;
`daemon/services/resource_monitor/sampler.py:759` docstring, computed at
:841/:849-851; the wire round at `daemon/routers/resources.py:369` — a 0–1
fraction rounded to 4 dp):

```
coverage_pct = min(1.0, attributed / anon_bytes)     # :759 (docstring), :850
anon_bytes   = max(0, snap.daemon.anon_kb or 0) * 1024   # :841
"coverage_pct": round(float(attrib.get("coverage_pct") or 0.0), 4)  # resources.py:369
```

`anon_bytes` is the ANON POOL — NOT the sum of the displayed components —
so the original 6.5 wording ("computed from the same basis as the
components") is unsatisfiable and was withdrawn per AM-13; the FE displays
the value verbatim and computes nothing.

**(c) The timeline's series defs** (`timeline-series.ts:41-72` at the
freeze): 5 series (`daemon_rss_mb`, `cgroup_mem_mb`, `daemon_cpu`,
`threads`, `instances_active`) across 3 unit classes (`memory`/`cpu`/`count`),
3 default-enabled (`:35-39`).

**(d) The mixed-kind families**: F4 registers a gauge AND a counter —
`w1.py:194-228` (`ensemble_db_pool_native` kind="gauge" at :196-198;
`ensemble_db_pool_requests_errors_total` kind="counter" at :219-221); F9 a
counter + a 0/1 gauge — `w2.py:120-150` (`ensemble_readiness_probe_total`
kind="counter"; `ensemble_readiness_grace_active` kind="gauge").

**(e) 6.7's door value vs the derived spacing**:
`central-tab.component.ts:474` — `metaIntervalS = computed(() =>
metricsDerivedIntervalS(this.state().lastGood))` — the meta line at :164-171
prints `effective spacing {{ interval | number: '1.0-1' }}s (derived from the
payload)`. The DERIVED function is `metrics-range.ts:257`
(`metricsDerivedIntervalS` = span ÷ densest point count). The resource
timeline prints **no** interval/cadence claim at all (`grep -rn
"interval_s|cadence|effective spacing" frontend/src/app/pages/metrics/resource
--include=*.html --include=*.ts` → no hits beyond poll-loop comments) —
there is no false statement to correct there, so no code change was needed;
the spec + mutation leg pin the central meta (below).

## 3. The unit vocabulary (6.3) — the table and where every unit came from

`frontend/src/app/pages/metrics/metrics-units.ts` — 43 entries, keyed on the
METRIC NAME, each derived from a READ-ONLY registration fact (never inferred):

| Unit | Metrics (source: wiring file) |
|---|---|
| `s` | `ensemble_loop_lag_seconds`, `ensemble_pg_idle_in_txn_oldest_seconds`, `ensemble_pg_census_last_success_timestamp_seconds` (w1), `ensemble_task_heartbeat_age_seconds`, `ensemble_task_heartbeat_cadence_seconds` (w2) |
| `count` | `ensemble_loop_lag_samples`, `ensemble_loop_lag_overflow_total`, `ensemble_wedge_capture_total`, `ensemble_wedge_capture_write_total`, `ensemble_db_pool_native`, `ensemble_db_pool_requests_errors_total`, `ensemble_db_pool_checked_out`, `ensemble_db_pool_waiting_clients`, `ensemble_pg_backends`, `ensemble_pg_leaked_txn`, `ensemble_readiness_probe_total`, `ensemble_readiness_failure_streak`, `ensemble_job_queue_depth`, `ensemble_dead_letter_items`, `ensemble_job_queue_items_created_total`, `ensemble_workers_total`, `ensemble_workers_busy`, `ensemble_worker_pool_events_total`, `ensemble_sweep_cycles_total`, `ensemble_sweep_errors_total`, `ensemble_sweep_rows_total`, `ensemble_instance_status_count`, `ensemble_instance_liveness_count`, `ensemble_waiting_children_count`, `ensemble_mcp_failures_total` (w1/w2/w3) |
| `flag` | `ensemble_loop_lag_probe_state` ("1 current, 0 others"), `ensemble_readiness_grace_active` (0/1), `ensemble_readiness_component_state` ("1 healthy, 0 degraded") |
| `state` | `ensemble_mcp_server_state` (an enumerated health-state code — no physical unit claimed) |
| `ratio` | `ensemble_worker_pool_wakeup_efficiency` ("notifications / (notifications + empty claims)") |
| `bytes` | `ensemble_checkpoint_blobs_table_bytes`, `ensemble_checkpoint_table_bytes` (w2) |
| `MB` | `daemon_rss_mb`, `cgroup_mem_mb` (the resources API's own names) |
| `%` | `daemon_cpu` (the resources API's own name) |
| `count` | `threads`, `instances_active` (the resources API's own names) |

The timeline panels now DERIVE their axis unit from this table
(`timelinePanelUnit()` in `timeline-tab.component.ts` — the class's first
series def is the lookup key), so the vocabulary is load-bearing on tab 1 as
well as on the central tabs (`metricsUnitFor` at central-tab's
`groupsByFamily`). Unmapped names render `unit unknown` — the central-tab
spec leg proves it with `ensemble_metric_the_lane_never_mapped`.

## 4. The mutation proofs (A-9) — 8 planted legs at the freeze tree `fd6322639`

All runs: `cd /tmp/wt-phase6-mutations/frontend && CI=1
./node_modules/.bin/jest --ci --runInBand <spec>`; plant via exact-string
replace; restore via `git checkout -- frontend/<file>`; the throwaway
verified clean after every leg.

| # | Planted defect (file) | RED | Restored GREEN |
|---|---|---|---|
| A1 | **one mixed axis** — `panelSeries` drops the `unitClass` filter (`timeline-tab.component.ts`) → every panel carries every enabled series | `Tests: 3 failed, 19 passed, 22 total` | `Tests: 22 passed, 22 total` |
| A2 | **count series on the memory panel** — `threads`' `unitClass: 'count'` → `'memory'` (`timeline-series.ts`) → the counts panel never appears; `threads` rides the MB panel | `Tests: 3 failed, 19 passed, 22 total` | `Tests: 22 passed, 22 total` |
| A3 | **one series rescaled inside the panel** — `rendered` reverts to `chartSeriesDomain(entry.points)` per series (`house-svg-renderer.component.ts`) — the W7 revert | `Tests: 22 failed, 48 passed, 70 total` | `Tests: 70 passed, 70 total` |
| B1 | **a wrong unit** — the vocabulary entry `ensemble_db_pool_checked_out: 'count'` → `'MB'` (`metrics-units.ts`) | `Tests: 1 failed, 7 passed, 8 total` | `Tests: 8 passed, 8 total` |
| B2 | **the mixed-kind panel** — `metricsPanelGroupKey` returns a constant (`metrics-units.ts`) → a family's gauge + counter merge into ONE panel | `Tests: 4 failed, 40 passed, 44 total` | `Tests: 44 passed, 44 total` |
| C1 | **silent non-reconciliation** — the discrepancy predicate returns `false` unconditionally (`live-tab.component.ts`) → the audit-shaped payload renders no sentence | `Tests: 1 failed, 23 passed, 24 total` | `Tests: 24 passed, 24 total` |
| D1 | **coverage rounding** — the verbatim passthrough becomes `fmt(coverage, 0) + '%'` (`live-tab.component.ts`) — the audit's false "0 %" pixel | `Tests: 3 failed, 21 passed, 24 total` | `Tests: 24 passed, 24 total` |
| E1 | **the door cadence in the meta** — `metaIntervalS` reads `payload.window.interval_s` instead of the derived spacing (`central-tab.component.ts`) → the 2 h leg sees 15 s, not 30 s | `Tests: 1 failed, 43 passed, 44 total` | `Tests: 44 passed, 44 total` |

**B1's first run was GREEN — a vacuous plant** (the F4 leg named
`db_pool_native` + `requests_errors_total` but not `checked_out`). The gap
was closed by `fd6322639` (each F4 member pinned by name), and B1 was re-run
RED at the freeze tree. The mutation process caught its own gap — recorded
as required by A-9.

## 5. What the specs now pin (the green side)

* `metrics-units.spec.ts` (8): `unit unknown` fallback (no inference — even a
  `_mb`-shaped name); every entry non-empty and round-tripping; the metric-name
  keying; the F4 members' units EACH by name (B1's leg); the F9 counter+0/1
  gauge yielding two different units with distinct group keys; the timeline
  series' units; the closed unit-string census; the role-comes-from-the-wire
  division.
* `central-tab.component.spec.ts` (44, incl. the 8.7/8.8 legs of the other
  lane): +3 phase-6 legs — the F9 mixed-kind family yields TWO panels with
  DIFFERENT `data-unit`s (`count` vs `flag`); each group hands its unit to the
  renderer (never `unit unknown` for a mapped metric); an unmapped metric
  renders `unit unknown` on its panel.
* `timeline-tab.component.spec.ts` (22): +4 phase-6 legs — each panel's unit
  from the vocabulary (MB / %); the TOGGLE path lands `threads` on the counts
  panel with `unit: 'count'` and NEVER in the memory/cpu series lists; the
  rendered Chart.js config suffixes the axis ticks (`1800 MB` / `3 %`) with
  ONE shared y-scale; the vocabulary agrees WITHIN each unit class.
* `house-svg-renderer.spec.ts` (70): +8 phase-6 legs — `chartSharedDomain`
  (joint range, empty/NaN-safe); the W7 NUMERIC assertion (a 10–20 series
  inside a 10–2000 panel uses < 5% of the plot height; the max series still
  reaches > 95%); the hover projection uses the SAME shared domain; the
  baseline rides the same domain; the TREND role renders the unit-labeled
  value axis (`4 MB`); an unmapped unit renders `unit unknown`; the MINI role
  renders NO axis (R-2 — both halves of the 6.2 sentence asserted).
* `live-tab.component.spec.ts` (24): +6 phase-6 legs — the AUDIT-shaped
  payload (71 + 1,119 against RSS 633, coverage 0.06) renders `0.06` verbatim,
  no `%` glyph, AND the sentence "do not reconcile … the daemon process is not
  the whole cgroup"; a reconciling payload renders no claim; a missing RSS
  makes no claim; a non-finite coverage renders `—`; the card names the
  AGGREGATE split + `estimated` and carries NO "Per-instance" promise; THE
  ABSENCE PIN — the component source reads `coverage_pct` once and
  `String()`s it, with no `× 100`, no `toFixed`, no division, and the template
  never `fmt()`s the coverage.

## 6. The runs

Per-file census at the freeze tree (jest `--ci --silent --runInBand`, the
`Tests:` line):

```
metrics-units.spec.ts            → Tests:  8 passed, 8 total   (was 4)
central-tab.component.spec.ts    → Tests: 44 passed, 44 total  (was 33 at phase 5; +3 mine, +8 theirs 8.7/8.8)
timeline-tab.component.spec.ts   → Tests: 22 passed, 22 total  (was 18)
timeline-drilldown.spec.ts       → Tests:  1 passed, 1 total
data-gaps.spec.ts                → Tests:  8 passed, 8 total
marker-label.spec.ts             → Tests:  3 passed, 3 total
live-tab.component.spec.ts       → Tests: 24 passed, 24 total  (was 18)
anomalies-panel.component.spec.ts→ Tests:  7 passed, 7 total
resource-tab.component.spec.ts   → Tests: 24 passed, 24 total
house-svg-renderer.spec.ts       → Tests: 70 passed, 70 total  (was 62)
chart-panel.component.spec.ts    → Tests: 23 passed, 23 total
chart-panel.renderer.spec.ts     → Tests:  7 passed, 7 total
charts-tab.component.spec.ts     → Tests:  9 passed, 9 total
anomaly-overlay-plugin.spec.ts   → Tests: 11 passed, 11 total
```

The phase-5 pack set (the 12 registered specs) + the phase-6 specs, run
together at the current branch tip: **`Tests: 275 passed, 275 total`**.

The whole-tree (D2) leg: see §7.

## 7. The whole-tree leg

Run in the throwaway worktree at the freeze `fd6322639` (detached — carries
exactly the branch history to my freeze, NO foreign uncommitted WIP;
`node_modules` symlinked per the phase-5 base-leg pattern):

```
cd /tmp/wt-phase6-mutations/frontend && CI=1 ./node_modules/.bin/jest --ci --silent --runInBand
```

**Result: 4698 collected / 4689 passed / 9 failed (140.98 s).** The 9 reds
are the SAME FOUR suites phase 5 base-proved (`schedule-create-dialog`,
`jobs-filter-state.model`, `jobs-grouping.model`, `settings` — the
timezone-picker family + the jobs grouping/filter model legs, 9 failing
names). The four suites' sources are UNTOUCHED between the phase-5 base
`e78fa0ef8` and the freeze (`git diff --stat 4e0c82b8b..fd6322639 -- <the
four paths>` is EMPTY), so phase 5's base-proof (the identical 9 failing
names at `e78fa0ef8`) carries over verbatim. **Net-new deterministic reds
= 0.** (+75 collected nodes over phase 5's 4623 — this phase's legs plus the
8.x lane's committed legs.)

## 8. agents/ 161/161

```
$ /home/nea/ensemble-src/.venv/bin/python scripts/protection/generate_prompt_artifact_baseline.py --check
PROMPT-BASELINE: OK — 161 versioned artifact(s) match .prompt-artifacts.sha256
$ sha256sum -c agents/.prompt-artifacts.sha256 | grep -c ": OK"
161
```

## 9. Honest-unit design decisions (disclosed)

* `bytes` — NOT `MB`: the FE plots values as served (no conversion), so a
  `_bytes` metric axis says `bytes`; an `MB` label would be a false unit.
* `flag` for 0/1 gauges, `state` for enumerated codes, `ratio` for the 0–1
  share — the honest label of a value with no physical unit; `unit unknown`
  would be a lie in the other direction for values the wire documents.
* The discrepancy predicate compares payload fields it DISPLAYS (the
  components' sum vs the daemon RSS reading, +2 MB render-rounding slack) and
  fires only on the impossible direction (sum > RSS) — the audit's exact
  shape. A sum below RSS (the anon pool inside the cgroup) is the honest
  steady state and stays silent.
* The house renderer's capability metadata (`axis: 'time'`) is left as-is:
  the single-value enum cannot say "time AND unit"; the 6.2 assertion lives
  on the rendered DOM (`house-y-tick`), not on the descriptive enum.

## 10. Residuals / routed

* The two foreign tsc errors (§0) — the phase-8 lane's to fold.
* The FE-metrics dashboard lane continues in phases 7-9 (the shell's
  per-instance subtitle dies with the shell in 7.4; the per-instance top-N
  list stays routed (m)).
* `central-tab.component.ts`'s `metaIntervalS` and the meta line are shared
  surface with the 8.x lane's file — future edits there need a fresh
  coexistence run of the 44-node spec.
