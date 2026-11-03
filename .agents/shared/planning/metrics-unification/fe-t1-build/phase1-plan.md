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
| 1.2 | **Same-commit** amend the pins spec `frontend/src/app/pages/metrics/metrics.bindings.pins.spec.ts`: (a) the banned list at `:94` becomes `['d3-', "from 'd3'", 'ngx-charts', 'moment', 'dayjs']` (dropping the `chart.js` entry) — **the scope change is PER-BAN, not per-file (W-6)**: the four remaining bans keep scanning **all five** sources the spec reads today (`overview-tab.component.ts` `:25`, `charts-tab.component.ts` `:26`, `metrics.component.ts` `:75`, the engine file `:76-88`, `metrics.service.ts` `:89-90`); only the `chart.js` ban is withdrawn, because the wrapper's import is sanctioned; (b) the spec's **disk read at `:76-88`** is **re-pointed to a phase-1-EXISTING file that Phase 4 does not delete** — `frontend/src/app/pages/metrics/charts/chart-panel.component.ts` (the wrapper stub 1.7 creates), **not** the consolidated renderer's path (created only at 4.1 ⇒ the spec was RED at the phase-1 freeze — W-1/AM-10); (c) a NEW **positive** pin that gates the import **SHAPE**, not only the location (C-1): **every** `chart.js` import site in the tree must be the **dynamic** `await import('chart.js')` form — **0** static `from 'chart.js'` sites anywhere — and each site must live inside `src/app/pages/metrics/**` | 1.1, 1.7 | The spec is **green at the phase-1 freeze** with the re-pointed path present in the tree at this commit; **four** planted-violation legs: (i) a static `from 'chart.js'` anywhere ⇒ red; (ii) a `dayjs` import in `charts-tab.component.ts` ⇒ red (the per-ban scope survives); (iii) a stale read path ⇒ red; (iv) a `d3-` import in `metrics.service.ts` ⇒ red |
| 1.3 | **Same-commit** amend pack Leg 7 in `test/packs/metrics_fe_dual_side_test.sh:275-286` — flip the zero-diff guard to an **expected-dependency-set** guard: the `frontend/package.json` diff must contain exactly `chart.js` (dependencies) and `jest-canvas-mock` (devDependencies); the assertion is **by name**, never by a raw line count (a transitive bump must not break it). The existing `DEPDIFF_LOG` becomes the evidence artifact | 1.1 | The amended leg passes on the branch; the same leg **fails** when a third direct dependency is added (planted-violation check) |
| 1.4 | **Same-commit** amend every live "zero FE deps" site — **six**, not the two the plan originally listed (AM-24): (i) `.agents/shared/planning/metrics-agent-tools/stage-prep-checklist.md:28` (the `frontend/package.json` EMPTY row) — amend or **record-historical**; (ii) `.agents/shared/planning/central-metrics-v1/stage-prep-checklist.md:102` (the "the three EMPTY deltas … are diff-based" paragraph) — record-historical; (iii) `.agents/shared/planning/central-metrics-v1/phase6-plan.md:25` (task **6.7**'s ceremony checklist — the "npm dependency delta is empty" string; the earlier `:23` cite was the wrong row — A-5) — record-historical; (iv) `docs/architecture/metrics-fe-charts.md:127` (the "the page adds no new frontend dependency" claim); (v) `docs/architecture/metrics-promote-disclosure.md:122` item #2 ("Zero FE deps"); (vi) `docs/architecture/metrics-fe-charts.md:125-131` — **the path reference AND the feature-frozen sentence** ("Zoom, brush, crosshair and gradients are explicitly OUT"), which Phase 4.6 (crosshair) and ADJ-ii (nearest-point hover) **falsify**. `CHANGELOG.md:141` stays **unamended** — historical record | 1.1 | A repo-wide grep for the zero-dep claim returns only the historical CHANGELOG line (recorded as allowed) **and** the three sites explicitly marked historical; a reviewer confirms the feature-frozen sentence no longer says crosshair is OUT |
| 1.5 | Create `frontend/src/app/components/charts/chart-theme.ts` — the single palette + the unit/format vocabulary extracted from the 3+ duplicated sites (`charts-tab.component.ts:37-45`, `svg-timeline.component.ts:169-173`). It is imported by the existing consumers so the extraction is **behaviour-neutral at this phase** | none | The palette exists in exactly one module; the consumers import it; the existing specs stay green (no behavioural change) |
| 1.6 | Record the measured npm facts in the lane's evidence (the tree-shaken controller set's real min+gz is measured in Phase 9 — this phase records the **metadata**: version, unpacked size, dep closure, license) | 1.1 | The evidence file carries the `npm view` output verbatim |
| 1.7 | Write the **wrapper contract** as a doc comment in a NEW stub at the **canonical path** `frontend/src/app/pages/metrics/charts/chart-panel.component.ts` (the contract Phase 4 implements): the per-series **DOM proxy** (preserving `data-series` / `data-render-mode`), the **visually-hidden data table** (a11y + a live DOM-assertion gate), **`chart.destroy()` in `ngOnDestroy`** (the shell destroys tab bodies via `NgComponentOutlet`; a missing destroy leaks a canvas + a ResizeObserver per tab switch), and the **zoneless rule** ("no Angular-rendered overlay may depend on a Chart.js redraw" — the app is zoneless: `zone.js` is declared but never loaded). **The import SHAPE is fixed here (AM-15 + C-1) and it is the stricter of the two readings:** the wrapper's `chart.js` import is the **dynamic** `await import('chart.js')` form, so the vendor is its own lazy chunk — **never** a static import, in `components/` or anywhere (a static import inside the lazily-loaded metrics tree satisfies the initial-total delta, the initial-chunk marker scan **and** a location-only rule, because the vendor is extracted into a **lazy-only sibling chunk** — proven on the real dist: `chunk-6Y6F4JKW.js` 43,688 B and `chunk-AOZCSBSC.js` 1,348 B are both lazy-only). **The wrapper's final home is `frontend/src/app/pages/metrics/charts/chart-panel.component.ts` — under the `src/app/pages/metrics/**` tree** (a NAMED relocation, so 1.2(c)'s location rule holds by construction; the earlier `components/charts/chart/` home sat OUTSIDE it and made the pin unimplementable — C-1). **THE RELOCATION IS A TASK-GRAPH ENTRY, NOT A SENTENCE (W-1b):** this task CREATES the stub **at the canonical path** — the retired out-of-tree path `frontend/src/app/components/charts/chart/chart-panel.component.ts` is **never** created by this lane, and if anything ever lands there, **4.12** names its removal | 1.5 (`chart-theme.ts` — the palette the contract's rendering clauses reference) | The stub compiles at the **canonical path** `frontend/src/app/pages/metrics/charts/chart-panel.component.ts`, exports the contract in its doc comment, and its spec asserts the four contract clauses **and** the **dynamic** import shape are present in the source (a source-grep pin, the house pattern) — the static form must fail it. **NUMERIC ASSERTION (W-1b): 0 files** may exist under `frontend/src/app/components/charts/chart/` in the tree at this commit (the out-of-tree copy must not exist at all) |
| 1.8 | Register the amended pack in `.agents/tester/PACKS.md` (keep-both, newest-first) with the node count **re-derived mechanically at gate time** (house convention: `pytest --collect-only -q` / the pack's own census — never a declared number) | 1.3 | The row exists, newest-first, and its count is a measured value |
| 1.9 | **BUNDLE GUARD (AM-2 + C-1 + C-3 + A-8), co-located here.** Add the lane's bundle-guard script/spec and run it **in this phase**, reading the **named baseline artifact** `bundle-baseline.json` (A-8 — a number duplicated only in plan prose is **not** accepted as a baseline): (a) **numeric** `\|Δ initial-total\| ≤ 2 kB` against the frozen **5,909,018 B**; (b) **no `chart.js` marker in ANY initial chunk** (a string scan of the 36-file initial set); (c) the **import-shape + import-site** assertion of 1.2(c) (**0** static `from 'chart.js'` sites; every site inside `src/app/pages/metrics/**`); (d) **capture the `anyComponentStyle` warning BASELINE SET mechanically** from this phase's production build log (C-3) — the set must be the **9** paths recorded in `bundle-baseline.json`, captured by `grep -E "scss exceeded maximum budget" <build-log>`, never hand-written. The guard runs against a **production build** (`./node_modules/.bin/ng build --configuration production`) in this worktree — no daemon, no live contact | 1.1 | The guard is green at this commit; the numbers it prints are the frozen baseline; **TWO** planted-violation variants make it **red** — (i) an **eager** import (in the initial set) and (ii) a **static import inside the lazy metrics tree** (the case that slips through (a) and (b) — C-1); the captured warning set is recorded with its 9 paths and equals the `bundle-baseline.json` set |

## Coupling

- **Tight with:** Phase 0 (§8's "same commit" clause is this phase's contract),
  Phase 4 (the wrapper contract written here is implemented there; the bundle
  guard of 1.9 is re-run there) and Phase 5 (the third guard re-run — Phase 5
  moves the resource module INTO the tree the import-site rule covers, so it is
  the other chunk-graph-changing phase — W-4).
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
- **The pins spec is GREEN at the phase-1 freeze** with its re-pointed read path
  present in the tree at this commit (W-1) — the re-point target is
  `frontend/src/app/pages/metrics/charts/chart-panel.component.ts`, which Phase 1
  creates and Phase 4 does not delete.
- The existing FE specs are **green** (no behavioural change from the theme
  extraction).
- **The bundle guard (1.9) is green and non-vacuous**: it prints the initial
  total against the frozen **5,909,018 B** baseline with `|Δ| ≤ 2 kB`, the
  initial-chunk scan finds **no** `chart.js` marker, the import-shape
  assertion finds **0** static `from 'chart.js'` sites, and **BOTH** planted
  variants — an **eager** import and a **static import inside the lazy metrics
  tree** (C-1) — turn it **red**. The `anyComponentStyle` **baseline set** is
  captured from the build log and recorded: **9 paths** (C-3), matching
  `bundle-baseline.json` exactly.
- `agents/.prompt-artifacts.sha256` still verifies **161/161**; the `agents/`
  tree has zero diffs. **`.agents/tester/PACKS.md` IS touched by 1.8 and is
  exempt from that clause** (AM-25 — it is `.agents/`, not `agents/`).
- No file under `daemon/`, `agents/`, `scripts/`, or `test/packs/` other than
  `test/packs/metrics_fe_dual_side_test.sh` is touched.
- **E-1 re-derivation:** the MEASURED figures this phase gates on are the
  **initial-total baseline (G1/G2)** and the **`anyComponentStyle` warning set
  (G10)** — **both re-derived at lane base with their commands and outputs**
  (`plan-overview.md` §0.2; the commands are
  `node …/bundle-baseline.measure.js dist/frontend/browser` and
  `grep -E "scss exceeded maximum budget" <build-log>`; the artifacts are
  `bundle-baseline.json` + `bundle-baseline.measure.js`). G10 is **9 paths**,
  not 8 (C-3). No other MEASURED figure gates this phase.
