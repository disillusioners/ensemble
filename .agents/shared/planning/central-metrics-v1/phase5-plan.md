# Phase 5: FE Charts Page (Zero-Dependency SVG)

Status: pending · Tier **1** (FE pack + e2e; `frontend/` only — the mechanical escalation list is untouched)
**FOLDED 2026-11-02:** architect v2 amendments folded — ids cited inline as `(AM-n)`. **Tier unchanged**;
the gate gains the **production-config build leg** (AM-23).
Feeds: architect → plan council → fresh approver → implementation

## Objective

Ship the developer-facing charts surface on the new door (AD2: **same release/stage** as the query API — Must
tier), with **zero new npm dependencies** (D2): a generalized inline-SVG chart primitive extracted from the
`svg-timeline` precedent, a typed model mirror of the frozen envelope, a service with static URL constants,
three tabs (Overview / Charts / Families), the house's three integration points (lazy route, gear-menu
entry, ordering pins), and the kill-switch/availability states rendered as **real states, not errors**.

The house precedent is explicit: `frontend/src/app/pages/resources/svg-timeline/svg-timeline.component.ts:17-18`
— *"Dependency-free inline SVG timeline primitive (D4 — no chart library exists and v1 adds none)"*. That
ruling is reaffirmed by D2 and generalised here; the mermaid-transitive `d3` is off-limits.

## Tasks

| # | Task | Depends On | Acceptance | Traceability |
|---|---|---|---|---|
| 5.1 | Add the **typed model mirror** under `src/app/models/` (+ barrel export): the envelope, the three route payloads, the family/snapshot/series shapes — the **canonical frozen family shape**, quoted token-identically from `plan-overview.md` §frozen contract (`decisions.md` D4 is the designated contract): `{id, names:[{name, kind, labels:{key:[values]}\|null, help, buckets:[…]\|null, cardinality_class, monotonic}], live_series_count, availability:{state:"live"\|"dark", reason?}, role, source_idiom, durability, cardinality_contribution}` (**including the per-name `buckets` array** — approver B3 + leader ruling), the availability reason union, and the `restart_epoch`. **Verbatim** mirror of the Phase-1/Phase-3 contract — drift is escalated, never normalized client-side. Wire timestamps are **aware-UTC** (AM-5: the BE emits `timestamps.now_utc_iso()`). They are parsed with the **same house helper** — `parseNaiveUtc` (`resources.models.ts:291-296`) — which already accepts a string that carries a zone designator/offset (`/[zZ]$\|[+-]\d{2}:?\d{2}$/` → `new Date(value)`) and only appends `Z` when the string is zone-less. **No new variant, no new helper, no behavioral change**: the aware payload converges on the existing helper; the naive-payload path is untouched. Nullable scalars render `—`. `vocabulary_version` is typed as `number` (never a literal) and the availability reason is a **widened union with an `unknown(<value>)` fallback** (AM-7) so a future reason never breaks the FE compile. | Phase 3 (envelope) | The model file matches the wire contract field-for-field; a spec asserts the field set and the `unknown(<value>)` fallback | FR-E3 · house rule (`resources.models.ts:1-13`) · **AM-5, AM-7** |
| 5.2 | Add the **service** with `static readonly` URL constants (`/api/metrics/families`, `/api/metrics/snapshot`, `/api/metrics/series`) — relative `/api/...` paths, no `src/environments/` — and `JSON_HEADERS = {Accept:'application/json'}` (the auth middleware 302-redirects wildcard-Accept browsers). | 5.1 | The service exposes typed methods per route; the constants are the single FE↔BE surface truth | FR-E2, FR-A9 · research ex-fe §2/§3 (`resources.service.ts:35-39`, `mcps-gateway.service.ts:67`) |
| 5.3 | Build the **shared SVG chart primitive** (e.g. `components/charts/svg-line-chart/`): fixed viewBox, **exported pure scale/axis functions** (unit-assertable, house pattern `svg-timeline:88-114`), per-series min/max normalization, native `<title>` tooltips, UTC axis labels. **Feature-frozen**: lines + **steps** + areas + stat cards + tooltips only — zoom/brush/crosshair/gradients are OUT (the same closure `svg-timeline` declared). **Step rendering is IN (approver B5 + leader ruling): COUNTER series render STEP, GAUGE series render LINEAR** — the ring stores step-function samples for counters, so a linear interpolation would assert values that were never observed (data honesty). The per-series mode comes from the family metadata `chart_role` (task 2.7b roster). ONE shared primitive, never per-page copies. | — | The pure functions are exported and unit-tested; no chart library is imported; the primitive renders series + legend + tooltips | FR-E8 (a)–(d) · D2 · research ex-fe §1/§4 |
| 5.4 | Build the **page** (shell + section registry, `resources.component.ts:35-49` pattern) with three lazy tab bodies: **Overview** (stat cards from `/snapshot`, 10 s), **Charts** (`/series`, 15 s, visible-tab-gated; requests `window_s=3600` — the 60-min default window ⇒ **240 points at the 15 s effective spacing ⇒ 15 s effective resolution out-of-the-box**, inherited from the `max_points` default of 240 (approver B1 + leader ruling)), **Families** (manual load, table of families + availability). Standalone components, `OnPush`, signals + computed, per-component Material imports, **zoneless** (no polyfills). | 5.2, 5.3 | The three tabs render from the door; lazy tab bodies gate their own polling; the **Charts tab renders the roster's chart-primary families** (task 2.7b) with **counters as STEP and gauges as LINEAR** (approver B5 + leader ruling) | FR-E1, FR-E6 · research ex-fe §2/§5/§6 |
| 5.5 | Implement **polling discipline**: named `POLL_INTERVAL_MS` constants; `setInterval` + `clearInterval` or `RxJS timer` + `takeUntilDestroyed`; polling stops on unmount and on tab-hide; a **skip-if-in-flight** guard; **monotonic supersede tokens** so a slower earlier response cannot overwrite a newer one; a **single error banner + retry CTA that retains last-good data** (the `live-tab.component.ts:69-99` pattern). | 5.4 | No leaked intervals on unmount (bindings pin); a stale response is discarded (supersede token); the error banner keeps the last-good data | FR-E4 · AC-E.2 · research ex-fe §3 |
| 5.6 | Wire the **three integration points**: a lazy `loadComponent` route in `app.routes.ts` with a `title` (placed **before** any `:id` routes), a gear-menu entry in `app.ts`'s static `SettingsMenuItem[]` array, and the **ordering pins** kept green (the `resources-gear-menu.spec.ts` precedent). | 5.4 | The route, the gear entry and the ordering pins are green; the page is reachable by URL | FR-E1 · research ex-fe §2 ("Fact list" item 2) |
| 5.7 | Render the **activation/availability states**: per-family dark-with-reason (from the envelope's `availability` block), and the empty panel when **no family is live** — driven by availability, **not** by the kill-switch (flag D-8 / D-11b: the door is never switch-gated, so an empty body must not be labelled "disabled" unless availability says so). **Copy fix (AM-19(d)): the panel reads "no metrics available yet" — never "metrics disabled"** (the old copy was itself the false banner D-8 forbids). Never an error banner for a 200-empty state. | 5.4 | The panel appears exactly when no family is live; a dark family shows its reason; a 200-empty response never renders the error banner; the copy contains neither "disabled" nor "error" | FR-E5, FR-A7, NFR-9 · D1 · flag D-8 · **AM-19(d)** · AC-E.3 |
| 5.8 | Add the **colocated specs**: pure-function specs for the scale/format helpers; a models-mirror spec; a **bindings pin** that greps the component source for `POLL_INTERVAL_MS` and the teardown call (`clearInterval` / `takeUntilDestroyed`); an availability/OFF-state spec. | 5.4, 5.7 | All specs green; the bindings pin is RED if the constant or the teardown is removed | FR-E7 · AC-E.2, AC-E.3 · research ex-fe §6 ("house spec styles") |
| 5.9 | Add the **Playwright e2e** (own proxy config + gate pattern, the `playwright.resources-gate.config.ts` style) and run **TWO build legs (AM-23)**: (i) `ng build --configuration development` — the AOT leg (real: the application builder compiles AOT unconditionally; the dev config only disables optimization) after template work; (ii) `ng build --configuration production` — **the only configuration where `budgets` + `outputHashing` live**, so the dev build alone checks nothing about the bundle/style budget. Any budget WARN is a **reported finding** (not an auto-fix). Assert the **`package.json` dependency diff is empty** (D2's zero-dependency guard), that the per-component SCSS stays under the 8 kB warn budget, and that the **expected FE-manifest delta** is recorded for the Phase-6 stage-prep checklist. | 5.6, 5.8 | e2e green (including a step-rendering assertion: a counter series renders as a STEP, not a line — approver B5); **both** builds pass with 0 errors (production build: any budget WARN is recorded as a finding); `package.json` diff = 0 dependency entries; the FE-manifest delta is recorded | FR-E7, FR-E8 (d)(e), NFR-3, NFR-4 · D2 · research ex-fe §6 · **AM-23** · risks P-R4, P-R10, M-6 |

**Not in this phase:** a charting dependency of any kind; a second page; i18n (the house has none); a
production HTTP interceptor.

## Gate legs

| Leg | Tier | Scope | D1 (lane-owned, HEAD-only) | D2 (dual-side, comparable) | Expected D1 / D2 nodes |
|---|---|---|---|---|---|
| **P5-G1** | **1** | `frontend/src/app/**` only | `metrics-fe-models` · `metrics-fe-service` · `metrics-fe-svg-chart` · `metrics-fe-page` · `metrics-fe-bindings-pins` | **REGISTERED PACK `metrics_fe_dual_side`** — composes: the `resources-gear-menu` ordering pin + the FE pack affected by the route table | ~35 / ~20 (PROJECTION) |

**Node-count discipline:** projections only; re-derive with the FE collect-only equivalent and record the
derived numbers.

**Mutation→test table (stub):**

| Label | Mutation | Must go RED |
|---|---|---|
| `P5-M1` | `POLL_INTERVAL_MS` renamed/removed | `metrics-fe-bindings-pins` |
| `P5-M2` | the `clearInterval` / `takeUntilDestroyed` teardown removed | `metrics-fe-bindings-pins` |
| `P5-M3` | the supersede token check inverted (older wins) | `metrics-fe-page` |
| `P5-M4` | the empty-state condition switched from "no family live" to "empty series" | `metrics-fe-page` (the OFF-state spec) |
| `P5-M5` | the model mirror's field name drifted from the wire | `metrics-fe-models` |
| `P5-M6` | the SVG scale function's normalisation removed | `metrics-fe-svg-chart` |
| `P5-M7` | the gear-menu ordering pin's expected position changed | the ordering pin (D2 leg) |
| `P5-M8` | `JSON_HEADERS` dropped from the service call | `metrics-fe-service` |
| `P5-M9` | the availability union narrowed to a closed literal set (a new reason breaks the compile) | `metrics-fe-models` (AM-7) |
| `P5-M10` | the panel copy reverted to "metrics disabled" | `metrics-fe-page` (AM-19(d)) |
| `P5-M11` | the production-config build leg dropped from the gate | the FE gate (AM-23) |

**Slicing proof:** the FE pack's selected spec set must equal the declared scope before any comparison.

**Pre-exclusion registry:** `regression_performance` (>300 s) · e2e specs needing a live daemon on `:8079`
(→ Release Gate) · `tests/packs/g7` (packs only).

**Invocations (W9/W10):** the D2 set is the **registered pack `metrics_fe_dual_side`** (registered in
`.agents/tester/PACKS.md`, invoked by pack id — never an ad-hoc jest/pytest list; the Playwright e2e runs
through its registered pack script). **Mandatory recipe load:**
`.agents/tester/skills/blast-radius-leg/blast-radius-leg.md` (Tier-1 scoped dual-side) **plus**
`.agents/tester/skills/failed-digest-equality-gate/failed-digest-equality-gate.md` where digests are
compared. Node counts are PROJECTIONS, re-derived via the FE collect-only output.

**Dependency guard:** the `frontend/package.json` diff must be empty at merge time — a charting dependency
entering here is a D2 breach and a phase-blocking finding, not a review nit.

**Build-leg guard (AM-23):** the **dev** build leg proves the template compiles (AOT); the **production**
build leg is the only one that can see the bundle/style budgets (`budgets` + `outputHashing` live only
under `configurations.production`). A green dev build is **not** evidence of budget compliance — the
production leg is mandatory, and any budget WARN is a reported finding carried into the Phase-6 checklist.

## Coupling

- **Tight with Phase 3** — the models mirror the envelope field-for-field; any envelope change is a model
  change (both land in the same commit per D4's contract rule).
- **Loose with Phase 4** — family growth changes the family list and the availability reasons shown, not the
  page's shape.
- **Loose with Phase 1** — the OFF-state semantics resolved there (availability-driven) are what 5.7
  implements.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| AOT-only template errors (no repo-level AOT gate) | Broken page found late | Mandatory `ng build --configuration development` leg (5.9) + it is an exit criterion. |
| A charting dependency is added "for the axis" | D2 breach + bundle budget | `package.json` diff gate; D2's disclosure names the closure; the primitive family covers the need. |
| Bundle / component-style budgets | Build warn/error | One shared primitive, no duplication; per-component SCSS < 8 kB warn; colours from `styles.scss:1-19`. |
| Polling leaks (interval survives unmount) | Hidden CPU/RSS growth in the FE | Bindings pin (`clearInterval` / `takeUntilDestroyed`) + a spec asserting teardown. |
| Out-of-order poll responses overwrite newer data | Wrong chart | Monotonic supersede tokens (the `timeline-tab` precedent) + a spec. |
| A 200-empty response is rendered as an error | False error banner (FR-E5/AC-E.3) | Availability-driven empty state (5.7) + the OFF-state spec + the `activation` block surfaced to the operator. |
| Wire timestamps rendered as local time (the payload is **aware**-UTC per AM-5) | Misleading axis | The same house helper `parseNaiveUtc` (`resources.models.ts:291-296`) parses both forms — a string that already carries a zone designator is used as-is; only a zone-less string gets `Z` appended. **No behavioral change and no second variant** — task 5.1 and this row now state the same rule. |

## Exit criterion

The `/metrics` page is reachable by route and from the gear menu with the ordering pins green; the Overview
and Charts tabs poll from the door and tear down cleanly on unmount/tab-hide; the Families tab lists families
with their availability reasons; the availability-driven empty state renders (never a false error banner);
the SVG primitive's pure functions are unit-tested; **zero** new npm dependencies; **both** build legs pass
(`--configuration development` for AOT and `--configuration production` for the budgets — AM-23) with 0
errors and any budget WARN recorded as a finding; the FE-manifest delta is recorded for the stage-prep
checklist; and P5-G1 ran as a **Tier-1** leg with a declared D1/D2 split, a slicing proof and 11/11 mutation
legs RED→GREEN.
