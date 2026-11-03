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

## Scope note

`frontend/` only. The `agents/` tree is untouched. No `daemon/` change.

## Tasks

| # | Task | Depends On | Acceptance |
|---|---|---|---|
| 1.1 | Add `chart.js` (`^4.6.1`) to `dependencies` and `jest-canvas-mock` (`^2.5.8`) to `devDependencies` in `frontend/package.json` — **exactly these two direct entries**, no more | Phase 0 | `npm ls --depth=0` shows exactly the two additions; the lockfile closure carries `@kurkle/color`, `cssfontparser`, `moo-color` (the transitive deps, measured via `npm view` 2026-11-03) |
| 1.2 | **Same-commit** amend the pins spec `frontend/src/app/pages/metrics/metrics.bindings.pins.spec.ts:94` — the banned list becomes `['d3-', "from 'd3'", 'ngx-charts', 'moment', 'dayjs']` (dropping the `chart.js` entry) **and** a NEW positive pin is added: the chart library is imported **only** from the lazy metrics route tree — a static import of `chart.js` reachable by an entry point fails the spec | 1.1 | The spec red before the code and green after; the positive pin has a **planted-violation** test (a static import in a fixture fails) — otherwise it is vacuous |
| 1.3 | **Same-commit** amend pack Leg 7 in `test/packs/metrics_fe_dual_side_test.sh:275-286` — flip the zero-diff guard to an **expected-dependency-set** guard: the `frontend/package.json` diff must contain exactly `chart.js` (dependencies) and `jest-canvas-mock` (devDependencies); the assertion is **by name**, never by a raw line count (a transitive bump must not break it). The existing `DEPDIFF_LOG` becomes the evidence artifact | 1.1 | The amended leg passes on the branch; the same leg **fails** when a third direct dependency is added (planted-violation check) |
| 1.4 | **Same-commit** amend the "zero FE deps" documentation/checklist sites: `docs/architecture/metrics-fe-charts.md:126-128` and `:144`, and `docs/architecture/metrics-promote-disclosure.md:122` item #2. `CHANGELOG.md:141` ("FE package.json empty") stays **unamended** — it is a historical record | 1.1 | A repo-wide grep for the zero-dep claim returns only the historical CHANGELOG line (recorded as allowed) |
| 1.5 | Create `frontend/src/app/components/charts/chart-theme.ts` — the single palette + the unit/format vocabulary extracted from the 3+ duplicated sites (`charts-tab.component.ts:37-45`, `svg-timeline.component.ts:169-173`). It is imported by the existing consumers so the extraction is **behaviour-neutral at this phase** | none | The palette exists in exactly one module; the consumers import it; the existing specs stay green (no behavioural change) |
| 1.6 | Record the measured npm facts in the lane's evidence (the tree-shaken controller set's real min+gz is measured in Phase 9 — this phase records the **metadata**: version, unpacked size, dep closure, license) | 1.1 | The evidence file carries the `npm view` output verbatim |
| 1.7 | Write the **wrapper contract** as a doc comment in a NEW stub `frontend/src/app/components/charts/chart/chart-panel.component.ts` (the contract Phase 4 implements): the per-series **DOM proxy** (preserving the `data-series` / `data-render-mode` hooks), the **visually-hidden data table** (a11y + a live DOM-assertion gate), **`chart.destroy()` in `ngOnDestroy`** (the shell destroys tab bodies via `NgComponentOutlet`; a missing destroy leaks a canvas + a ResizeObserver per tab switch), and the **zoneless rule** ("no Angular-rendered overlay may depend on a Chart.js redraw" — the app is zoneless: `zone.js` is declared but never loaded) | 1.5 | The stub compiles, exports the contract in its doc comment, and its spec asserts the four contract clauses are present in the source (a source-grep pin, the house pattern) |
| 1.8 | Register the amended pack in `.agents/tester/PACKS.md` (keep-both, newest-first) with the node count **re-derived mechanically at gate time** (house convention: `pytest --collect-only -q` / the pack's own census — never a declared number) | 1.3 | The row exists, newest-first, and its count is a measured value |

## Coupling

- **Tight with:** Phase 0 (§8's "same commit" clause is this phase's contract) and
  Phase 4 (the wrapper contract written here is implemented there).
- **Loose with:** Phase 9 (the bundle measurement of the new chunk).
- **Independent of:** Phases 2, 3, 5, 6, 7, 8.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| The dependency leaks into the **initial** bundle | High — the production build hard-fails at 6 MB (headroom 89,982 B) and both FE packs go red | 1.2's positive pin (static import of `chart.js` fails) + the Phase 9 bundle guard B1/B3 |
| The governance amendment misses a "zero FE deps" site → the next lane inherits a contradiction | Medium | 1.4's repo-wide grep as the exit criterion |
| The pins spec becomes vacuous (a ban list with no positive pin) | Medium | 1.2 requires a planted-violation test that must FAIL on a static import |
| The canvas mock is added as a devDependency but the jest environment is not configured for it | Medium | 1.1's acceptance requires `npm ls` to resolve it; the jest setup wiring is Phase 4's task (4.3) and must land before any canvas-rendering spec exists |

## Exit Criterion

- **One commit** carries: the two dependency entries + the lockfile, the
  pins-spec amendment + its positive pin, the pack Leg-7 amendment, the two
  doc-site amendments, `chart-theme.ts` (+ its extraction to the existing
  consumers), and the wrapper stub with its contract + spec.
- Commit message shape: `feat(fe): adopt chart.js v4 for interactive panels — governance amendment in the same commit (D-1)`.
- `frontend/package.json` diff = **exactly 2 added lines** (one dependency, one
  devDependency).
- The pins spec and the amended pack leg both pass, and both have a proven
  **planted-violation** leg (a red→green proof recorded in the lane evidence).
- The existing FE specs are **green** (no behavioural change from the theme
  extraction).
- `agents/.prompt-artifacts.sha256` still verifies **161/161**.
- No file under `daemon/`, `agents/`, `scripts/`, or `test/packs/` other than
  `test/packs/metrics_fe_dual_side_test.sh` is touched.
