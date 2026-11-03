# Phase 7: Route fate (D-3) — retire `/resources` + param-preserving redirect

## Objective

Retire the standalone `/resources` route **in the same release** that mounts
tab 1, behind a **param-preserving** redirect to `/metrics?tab=resource`, and
clean every surface that referenced the old route — the gear menu, the specs,
and the retired page's own assets.

D-3: *"retire /resources with a SAME-RELEASE redirect to /metrics?tab=resource.
Predefined tabs stand; Gridster2 door closed."*

## Tasks

| # | Task | Depends On | Acceptance |
|---|---|---|---|
| 7.1 | Replace the `/resources` route in `frontend/src/app/app.routes.ts:74` with a **`canMatch` guard returning a `UrlTree`** (`/metrics` + the preserved query params + `tab=resource`). The app's existing `redirectTo` idiom (`:35,:104`) **does not preserve params** — a redirect component was the alternative; the guard is preferred because the route table stays the single source of route truth | Phase 2 | The redirect spec asserts: `/resources` ⇒ `/metrics?tab=resource`; `/resources?window=24h&x=1` ⇒ `/metrics?tab=resource&window=24h&x=1`; the original query params are **all** preserved |
| 7.2 | Guard the redirect's target id against the manifest: the redirect writes `tab=resource`, and the manifest's tab-1 id **is** `resource` — a spec asserts the two agree (a rename in one place would otherwise land on the default tab silently) | Phase 2, 7.1 | The spec fails if the manifest's tab-1 id changes without the redirect |
| 7.3 | Remove the **`Resources` gear-menu entry** (`frontend/src/app/app.ts:580`) and its ordering pin (`frontend/src/app/pages/resources/resources-gear-menu.spec.ts:26-41`); `Metrics` (`:597`) becomes the single entry for these surfaces | 7.1 | The gear-menu specs are green with the `resourcesIdx` assertion removed/converted; the menu has **no** `/resources` entry |
| 7.4 | Delete the retired page's own assets: `frontend/src/app/pages/resources/resources.component.ts` / `.html` / `.scss` / `.spec.ts` and `resources-gear-menu.spec.ts` — the module itself already moved in Phase 5 | Phase 5, 7.1 | The directory `frontend/src/app/pages/resources/` no longer exists; the source-grep pins at `resources.component.spec.ts:23-25` are retired **with** the file they pin |
| 7.5 | Update `frontend/src/app/pages/metrics/metrics-gear-menu.spec.ts:30-45` (drop the `resourcesIdx` assertion) and `:47-53` (the "Resources still before appends" assertion) | 7.3 | The specs are green; no assertion silently deleted (each removal is listed with its reason) |
| 7.6 | **Repo-wide sweep** (the audit's sweep was NOT exhaustive — `resource-refactor-architecture.md` §7): `grep -rn "/resources" frontend/src frontend/e2e --include=*.spec.ts --include=*.ts --include=*.html` and classify **every** hit as (a) the intended redirect target, (b) the `/api/resources/*` endpoints (which stay), or (c) a stale reference to the retired route. Class (c) is fixed | 7.1, 7.4 | The sweep's full output is recorded in the lane evidence with a classification per hit; **zero** class-(c) hits remain |
| 7.7 | Retire or re-point the **resources Playwright gate** (`frontend/playwright.resources-gate.config.ts`, port 14202) — the route it drives no longer exists. Either delete it, or re-point its spec at `/metrics?tab=resource` and rename it accordingly. The choice is recorded in the lane evidence | 7.1, 7.4 | No Playwright config targets the retired route; the gate either is gone or drives the new URL |
| 7.8 | Update the **e2e deep-link** expectations in `frontend/e2e/metrics-gate.spec.ts` for the new tab shape and add the redirect leg (the redirect leg is specified in Phase 9's Playwright list) | 7.1, Phase 2 | The e2e specs are green against the stubbed server |

## Coupling

- **Tight with:** Phase 2 (the manifest's tab id; the shell's landing surface),
  Phase 5 (the module moved; the retired shell is deleted here), Phase 8 (the
  landing surface must show something true).
- **Loose with:** Phase 9 (PX-9).
- **Independent of:** Phase 1, Phase 3, Phase 4, Phase 6.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| The redirect **drops the query params** (the app's default idiom does) | Medium — a bookmarked `/resources?window=24h` would land on the default window | 7.1's spec asserts the preservation explicitly, including a multi-param case |
| A stale `/resources` reference survives in a spec and fails later | Medium | 7.6's exhaustive, **classified** sweep with the output recorded |
| The redirect loops (old URL ⇒ new URL ⇒ old URL) | High | The redirect target is `/metrics` (a different path); 7.1's spec asserts a single navigation settles |
| The retired route breaks a live bookmark in a way the user cannot recover from | Low | The redirect is permanent and param-preserving; the gear menu carries the Metrics entry |

## Exit Criterion

- `/resources` resolves to `/metrics?tab=resource` **with every query param
  preserved**, verified by the spec and by PX-9.
- `frontend/src/app/pages/resources/` is **gone**.
- The gear menu has no `Resources` entry; the metrics entry remains.
- The repo-wide sweep is recorded with a per-hit classification; **zero**
  stale-route references remain.
- No Playwright config drives the retired route.
- The e2e specs are green; the stubbed-server gate still passes.
