# Phase 7: Route fate (D-3) — retire `/resources` + param-preserving redirect

## Objective

Retire the standalone `/resources` route **in the same release** that mounts
tab 1, behind a **param-preserving** redirect to `/metrics?tab=resource`, and
clean every surface that referenced the old route — the gear menu, the specs,
the Playwright gate, the README and the operator runbooks.

D-3: *"retire /resources with a SAME-RELEASE redirect to /metrics?tab=resource.
Predefined tabs stand; Gridster2 door closed."*

## Tasks

| # | Task | Depends On | Acceptance |
|---|---|---|---|
| 7.1 | Replace the `/resources` route in `frontend/src/app/app.routes.ts:74` with a **`canMatch` guard returning a `UrlTree`** (`/metrics` + the preserved query params + `tab=resource`). **The mechanism is named (AM-3):** a `canMatch` guard receives `(route, segments)` — **NOT** query params — so the guard reads `Router.getCurrentNavigation()?.extractedUrl.queryParams` with an **explicit null-fallback** (log + degrade to a param-less redirect; **never** crash, never a silent param loss). **Precedence:** the redirect's own `tab=resource` **overrides** an incoming `tab`, so the preservation claim narrows to **"all params except `tab`"** | Phase 2, Phase 8 | The acceptance is **end-to-end** and carries **TWO** legs (W-2 — the second is the architect's own Required clause and was previously un-gated): (i) `router.navigateByUrl('/resources?window=24h&x=1')` ⇒ the landed URL's **parsed** `queryParams` equal the input's (plus `tab=resource`); (ii) `router.navigateByUrl('/resources?tab=charts')` ⇒ the landed URL's **parsed** `queryParams.tab === 'resource'` (the incoming `tab` is overridden). A raw-string comparison is **not** accepted (order-brittle), and a unit assertion on a hand-built UrlTree is **not** accepted |
| 7.2 | Guard the redirect's target id against the manifest: the redirect writes `tab=resource`, and the manifest's tab-1 id **is** `resource` — a spec asserts the two agree (a rename in one place would otherwise land on the default tab silently) | Phase 2, 7.1 | The spec fails if the manifest's tab-1 id changes without the redirect |
| 7.3 | Remove the **`Resources` gear-menu entry** (`frontend/src/app/app.ts:580`) and its ordering pin (`frontend/src/app/pages/resources/resources-gear-menu.spec.ts:26-41`); `Metrics` (`:597`) becomes the single entry for these surfaces | 7.1 | The gear-menu specs are green with the `resourcesIdx` assertion removed/converted; the menu has **no** `/resources` entry |
| 7.4 | Delete the retired page's own assets: `frontend/src/app/pages/resources/resources.component.ts` / `.html` / `.scss` / `.spec.ts` and `resources-gear-menu.spec.ts` — the module itself already moved in Phase 5 | Phase 5, 7.1 | The directory `frontend/src/app/pages/resources/` no longer exists; the source-grep pins at `resources.component.spec.ts:23-25` are retired **with** the file they pin |
| 7.5 | Update `frontend/src/app/pages/metrics/metrics-gear-menu.spec.ts:30-45` (drop the `resourcesIdx` assertion) and `:47-53` (the "Resources still before appends" assertion) | 7.3 | The specs are green; no assertion silently deleted (each removal is listed with its reason) |
| 7.6 | **Repo-wide sweep, WIDENED (AM-22).** The original sweep (`frontend/src frontend/e2e`, three extensions) could not see `frontend/playwright.resources-gate.config.ts` (outside both dirs), `frontend/e2e/README.md` (`.md`), or the operator docs. The sweep is now `grep -rn "/resources" frontend/ docs/ --exclude-dir=node_modules --exclude-dir=dist` with a **per-extension include list** (`.ts .html .scss .json .mjs .md`), and **every hit is classified** as (a) the intended redirect target, (b) the `/api/resources/*` endpoints (which stay), (c) a stale reference to the retired route, or (d) an operator doc that still points operators at the retired route. Class (c) is fixed; class (d) is fixed by 7.8 | 7.1, 7.4 | The sweep's **full output** is recorded in the lane evidence with a classification per hit; **zero** class-(c) hits remain |
| 7.7 | Retire or re-point the **resources Playwright gate** (`frontend/playwright.resources-gate.config.ts`, port 14202) — the route it drives no longer exists. The acceptance covers **the spec, not only the config** (AM-22): `frontend/e2e/resources-gate.spec.ts:24` (the URL) and `:189` (the gear-menu assertion) must be re-pointed or the leg deleted, and an explicit **no-Playwright-config-targets-the-retired-route** assertion is added | 7.1, 7.4, 7.6 | No Playwright config **and no Playwright spec** targets the retired route; the gate either is gone or drives the new URL, with its gear-menu leg re-pointed |
| 7.8 | **Operator docs (AM-22).** `docs/runbooks/resource-cost-control.md` and `docs/runbooks/resource-hygiene-operator-checklist.md` still direct operators at the retired route — update both to `/metrics?tab=resource` | 7.1 | Both runbooks name the new URL; a doc grep finds no operator instruction pointing at `/resources` |
| 7.9 | Update the **e2e deep-link** expectations in `frontend/e2e/metrics-gate.spec.ts` for the new tab shape and add the redirect leg (the redirect leg is specified in Phase 9's Playwright list) | 7.1, Phase 2 | The e2e specs are green against the stubbed server |

## Coupling

- **Tight with:** Phase 2 (the manifest's tab id; the shell's landing surface),
  Phase 5 (the module moved; the retired shell is deleted here), Phase 8 (the
  landing surface must show something true — **7.1 depends on Phase 8**,
  per AM-7).
- **Loose with:** Phase 9 (PX-9).
- **Independent of:** Phase 1, Phase 3, Phase 4, Phase 6.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| The redirect **drops the query params** (the app's default idiom does) | Medium — a bookmarked `/resources?window=24h` would land on the default window | 7.1's end-to-end **parsed**-`queryParams` assertion, including a multi-param case |
| A preserved incoming `tab` collides with the redirect's own `tab=resource` | Medium — "all preserved" and "writes tab=resource" cannot both hold | 7.1's precedence rule (`tab=resource` **overrides**) + the narrowed claim ("all params except `tab`") + the explicit leg `/resources?tab=charts` ⇒ lands on `?tab=resource` |
| The redirect lands on a page carrying the **contradictory double banner** | Medium — the redirect's whole point is a truthful landing | 7.1's **task-graph edge: `Depends On: Phase 8`** (AM-7) — the redirect may not land before the empty state is fixed |
| A stale `/resources` reference survives in a spec, a config, a README or a runbook | Medium | 7.6's widened, **classified** sweep (now including `frontend/` root files and `docs/`) + 7.8 |
| The redirect loops (old URL ⇒ new URL ⇒ old URL) | High | The redirect target is `/metrics` (a different path); 7.1's spec asserts a single navigation settles |

## Exit Criterion

- `/resources` resolves to `/metrics?tab=resource` **with every query param
  preserved** (except `tab`, which the redirect overrides), verified by the
  **end-to-end parsed-`queryParams`** spec and by PX-9.
- The null-fallback path (no current navigation) is covered: the guard logs,
  degrades to a param-less redirect, and does not crash.
- `frontend/src/app/pages/resources/` is **gone**.
- The gear menu has no `Resources` entry; the metrics entry remains.
- The repo-wide sweep (widened to `frontend/` + `docs/`, all relevant
  extensions) is recorded with a per-hit classification; **zero** stale-route
  references and **zero** stale operator instructions remain.
- No Playwright config **or spec** drives the retired route; the gear-menu leg
  is re-pointed or deleted.
- The e2e specs are green; the stubbed-server gate still passes.
- **E-1 re-derivation:** this phase gates on **no** MEASURED figure. Its
  inputs (the route table, the gear-menu entries, the e2e specs) are
  **structural facts** re-verified at lane base and re-read at this phase's
  freeze.
