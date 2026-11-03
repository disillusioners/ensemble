# Phase 1: Charting dependency + same-commit governance amendment (D-1)

## Objective

Land **Chart.js v4** as the sole vendor engine (interactive-panel role only)
and **one** jest canvas-mock devDependency — together, in ONE commit, with the
governance amendment that makes the dependency legal. D-1 requires the
amendment and the dependency in the **same commit**; the "zero FE deps" posture
is a **deliberate, recorded product change**, not an oversight.

Also extract the chart theme (`chart-theme.ts`) in this phase: the house
palette is currently copied across 3+ files (`charts-tab.component.ts:37-45`,
`svg-timeline.component.ts:169-173`, and the minis) and the charting decision
explicitly calls for the extraction **before any engine lands**
(`charting-engine-decision.md` §UX note).

**This phase also carries the FIRST bundle guard (AM-2).** A Chart.js leak is
possible from this commit onward, so the guard that detects it must live here —
not in Phase 9, after eight phases of unguarded exposure.

## Scope note

`frontend/` only. The `agents/` tree is untouched. No `daemon/` change.
`.agents/tester/PACKS.md` IS in scope (it is `.agents/`, not `agents/`).

## Tasks

| # | Task | Depends On | Acceptance |
|---|---|---|---|
| 1.1 | Add `chart.js` (`^4.6.1`) to `dependencies` and `jest-canvas-mock` (`^2.5.8`) to `devDependencies` in `frontend/package.json` — **exactly these two direct entries**, no more | Phase 0 | `npm ls --depth=0` shows exactly the two additions; the lockfile closure carries `@kurkle/color`, `cssfontparser`, `moo-color` (the transitive deps, measured via `npm view` 2026-11-03) |
| 1.2 | **Same-commit** amend the pins spec `frontend/src/app/pages/metrics/metrics.bindings.pins.spec.ts`: (a) the banned list at `:94` becomes `['d3-', "from 'd3'", 'ngx-charts', 'moment', 'dayjs']` (dropping the `chart.js` entry) and the check is **scoped to the house-SVG file only** so the wrapper's Chart.js import does not trip it; (b) the spec's **disk read at `:76-88`** is **re-pointed** to the consolidated renderer's path — it currently `readFileSync`s `svg-line-chart.component.ts`, which Phase 4 **deletes**, so the spec would crash at merge (AM-10); (c) a NEW **positive** pin: every `from 'chart.js'` / `import('chart.js')` occurrence must live inside `src/app/pages/metrics/**` | 1.1, 1.7 | The spec red before the code and green after; **each** of the three clauses has a planted-violation test (a static import outside the metrics tree fails; a stale path fails; the banned-list scope is exact) |
| 1.3 | **Same-commit** amend pack Leg 7 in `test/packs/metrics_fe_dual_side_test.sh:275-286` — flip the zero-diff guard to an **expected-dependency-set** guard: the `frontend/package.json` diff must contain exactly `chart.js` (dependencies) and `jest-canvas-mock` (devDependencies); the assertion is **by name**, never by a raw line count (a transitive bump must not break it). The existing `DEPDIFF_LOG` becomes the evidence artifact | 1.1 | The amended leg passes on the branch; the same leg **fails** when a third direct dependency is added (planted-violation check) |
| 1.4 | **Same-commit** amend every live "zero FE deps" site — **six**, not the two the plan originally listed (AM-24): (i) `.agents/shared/planning/metrics-agent-tools/stage-prep-checklist.md:28` (the `frontend/package.json` EMPTY row) — amend or **record-historical**; (ii) `.agents/shared/planning/central-metrics-v1/stage-prep-checklist.md:102` (the "the three EMPTY deltas … are diff-based" paragraph) — record-historical; (iii) `.agents/shared/planning/central-metrics-v1/phase6-plan.md:23` (the ceremony checklist's "npm dependency delta is empty") — record-historical; (iv) `docs/architecture/metrics-fe-charts.md:127` (the "the page adds no new frontend dependency" claim); (v) `docs/architecture/metrics-promote-disclosure.md:122` item #2 ("Zero FE deps"); (vi) `docs/architecture/metrics-fe-charts.md:125-131` — **the path reference AND the feature-frozen sentence** ("Zoom, brush, crosshair and gradients are explicitly OUT"), which Phase 4.6 (crosshair) and ADJ-ii (nearest-point hover) **falsify**. `CHANGELOG.md:141` stays **unamended** — historical record | 1.1 | A repo-wide grep for the zero-dep claim returns only the historical CHANGELOG line (recorded as allowed) **and** the three sites explicitly marked historical; a reviewer confirms the feature-frozen sentence no longer says crosshair is OUT |
| 1.5 | Create `frontend/src/app/components/charts/chart-theme.ts` — the single palette + the unit/format vocabulary extracted from the 3+ duplicated sites (`charts-tab.component.ts:37-45`, `svg-timeline.component.ts:169-173`). It is imported by the existing consumers so the extraction is **behaviour-neutral at this phase** | none | The palette exists in exactly one module; the consumers import it; the existing specs stay green (no behavioural change) |
| 1.6 | Record the measured npm facts in the lane's evidence (the tree-shaken controller set's real min+gz is measured in Phase 9 — this phase records the **metadata**: version, unpacked size, dep closure, license) | 1.1 | The evidence file carries the `npm view` output verbatim |
| 1.7 | Write the **wrapper contract** as a doc comment in a NEW stub `frontend/src/app/components/charts/chart/chart-panel.component.ts` (the contract Phase 4 implements): the per-series **DOM proxy** (preserving `data-series` / `data-render-mode`), the **visually-hidden data table** (a11y + a live DOM-assertion gate), **`chart.destroy()` in `ngOnDestroy`** (the shell destroys tab bodies via `NgComponentOutlet`; a missing destroy leaks a canvas + a ResizeObserver per tab switch), and the **zoneless rule** ("no Angular-rendered overlay may depend on a Chart.js redraw" — the app is zoneless: `zone.js` is declared but never loaded). **The import shape is fixed here (AM-15):** the wrapper's `chart.js` import lives **inside `src/app/pages/metrics/**`** (the wrapper's home after Phase 5) **or** is a dynamic `await import('chart.js')` so the vendor is its own lazy chunk — never a static import in `components/` (a shared static import would defeat the path allowlist and carry chart.js into the initial set) | 1.5 | The stub compiles, exports the contract in its doc comment, and its spec asserts the four contract clauses **and** the import shape are present in the source (a source-grep pin, the house pattern) |
| 1.8 | Register the amended pack in `.agents/tester/PACKS.md` (keep-both, newest-first) with the node count **re-derived mechanically at gate time** (house convention: `pytest --collect-only -q` / the pack's own census — never a declared number) | 1.3 | The row exists, newest-first, and its count is a measured value |
| 1.9 | **BUNDLE GUARD (AM-2), co-located here.** Add the lane's bundle-guard script/spec and run it **in this phase**: (a) **numeric** `\|Δ initial-total\| ≤ 2 kB` against the **frozen lane-base baseline of 5,909,018 B** (the builder's own "Initial total" basis, 36 chunk files — derived in `research-findings.md` §14); (b) **no `chart.js` marker in ANY initial chunk** (a string scan of the initial set); (c) the import-graph assertion of 1.2(c). The guard runs against a **production build** (`./node_modules/.bin/ng build --configuration production`) in this worktree — no daemon, no live contact | 1.1 | The guard is green at this commit; the numbers it prints are the frozen baseline; a planted "import chart.js eagerly" variant makes (b) and (c) **red** (the guard is proven non-vacuous) |

## Coupling

- **Tight with:** Phase 0 (§8's "same commit" clause is this phase's contract) and
  Phase 4 (the wrapper contract written here is implemented there; the bundle
  guard of 1.9 is re-run there).
- **Loose with:** Phase 9 (the final bundle measurement + the chunk-size record).
- **Independent of:** Phases 2, 3, 5, 6, 7, 8.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| The dependency leaks into the **initial** bundle | High | 1.2(c)'s import-graph pin + 1.9(b)'s initial-chunk scan. **The corrected mechanism (AM-2):** a **lazy** leak trips **no** budget (lazy chunks are counted by no budget type) — so the build would stay green and both FE packs would stay green. The guards are therefore numeric + import-graph, and they live **here** (Phases 1–8 are the exposure window). An **eager** leak does trip the `initial` error budget: the headroom is **90,982 B** and chart.js's raw minified size is ~250–400 kB |
| The pins spec crashes at merge because it reads a file Phase 4 deletes | High | 1.2(b) re-points the `readFileSync` path **in this phase** (AM-10) |
| The governance amendment misses a site | Medium | 1.4 enumerates **six** sites (AM-24 corrected the count from two) and its acceptance is a repo-wide grep, not a checklist |
| The pins spec becomes vacuous (a ban list with no positive pin) | Medium | 1.2 requires a planted-violation test that must FAIL on a static import outside the metrics tree |
| The canvas mock is added as a devDependency but the jest environment is not configured for it | Medium | 1.1's acceptance requires `npm ls` to resolve it; the jest setup wiring is Phase 4's task (4.3) and must land before any canvas-rendering spec exists |

## Exit Criterion

- **One commit** carries: the two dependency entries + the lockfile, the
  pins-spec amendment (banned list + **re-pointed disk read** + the positive
  import-graph pin), the pack Leg-7 amendment, the **six** doc/checklist site
  amendments, `chart-theme.ts` (+ its extraction to the existing consumers), the
  wrapper stub with its contract + import shape + spec, and the bundle guard.
- Commit message shape: `feat(fe): adopt chart.js v4 for interactive panels — governance amendment in the same commit (D-1)`.
- `frontend/package.json` diff = **exactly two added direct entries**
  (`chart.js`, `jest-canvas-mock`) — asserted **by name**, not by a line count
  (AM-25: the original "exactly 2 added lines" criterion contradicted the
  by-name rule this phase exists to install).
- The pins spec and the amended pack leg both pass, and both have a proven
  **planted-violation** leg (a red→green proof recorded in the lane evidence).
- The existing FE specs are **green** (no behavioural change from the theme
  extraction).
- **The bundle guard (1.9) is green and non-vacuous**: it prints the initial
  total against the frozen **5,909,018 B** baseline with `|Δ| ≤ 2 kB`, the
  initial-chunk scan finds **no** `chart.js` marker, and the planted eager-import
  variant turns it **red**.
- `agents/.prompt-artifacts.sha256` still verifies **161/161**; the `agents/`
  tree has zero diffs. **`.agents/tester/PACKS.md` IS touched by 1.8 and is
  exempt from that clause** (AM-25 — it is `.agents/`, not `agents/`).
- No file under `daemon/`, `agents/`, `scripts/`, or `test/packs/` other than
  `test/packs/metrics_fe_dual_side_test.sh` is touched.
- **E-1 re-derivation:** the only MEASURED figure this phase gates on is the
  **initial-total baseline** — **re-derived at lane base** by this fold
  (2026-11-03, `research-findings.md` §14: two independent derivations
  agreeing at 5,909,018 B, cross-checked against the shipped v0.28.9 dist which
  measures byte-identically). No other MEASURED figure gates this phase.
