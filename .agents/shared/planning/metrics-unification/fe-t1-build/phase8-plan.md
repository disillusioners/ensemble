# Phase 8: Empty state + honesty (D-2)

## Objective

Make the page **honest about its failures** — and make the fail-closed state
**actionable**. D-2 requires the FE to distinguish 503 `AUTH_NOT_CONFIGURED`
from every other dark cause and to render an actionable empty state pointing
at `docs/runbooks/oauth2-auth.md`.

**The state model is PER SOURCE (AM-1):** the `auth_not_configured` panel
renders on **central-door surfaces only** (the catalog drawer + the 6 central
tab bodies); **each custom slot renders its own source's state** — so under
the default install posture **tab 1 stays lit**.

Today's `/metrics` empty state is honest about the cause and dishonest about
the consequence: two identical banners, one self-contradictory ("serves no
metrics. — the last known values are kept", with no values rendered); two Retry
buttons that can never succeed; three tabs left enabled that all fail
identically; no HTTP status surfaced; no runbook link; a **green header dot**
while the body reports hard failure; and a 900 px banner/Retry collision.

## Tasks

| # | Task | Depends On | Acceptance |
|---|---|---|---|
| 8.1 | **The door-state class table, WIDENED (AM-4).** The five-class table misses the FIRST state every real user will meet: **401 `AUTH_REQUIRED`** — the FE sends `Accept: application/json`, so the auth middleware's **401** branch applies (not the browser 302), and a bearer rejection is also 401 — yet 401 decodes to `unexpected` + a dead Retry today (`frontend/src/app/models/metrics.model.ts:437-441`). The table becomes: `auth_required` (401 — cause + "sign in / obtain a scoped token"; **no** Retry), `auth_not_configured` (503 — actionable, runbook pointer, **no** Retry), `scope_insufficient` (403 with the `SCOPE_INSUFFICIENT` token), `forbidden_other` (a 403 without it), `unknown_family` (404 — version skew; **no** Retry), `bad_request` (400 `invalid_label_filter`/`invalid_cursor` — an FE-bug class; **no** Retry), `timeout` (the client-side ~15 s deadline from Phase 2's 2.10; **no** Retry), `registry_unavailable` (503 with that body code), `network` (status 0), `unexpected`. The three routes declare 401 on all three (`daemon/routers/metrics.py:586,:636,:709`), 404 at `:643,:697`, and 400 at `:736` | Phase 2 | The empty-state spec asserts **each** class: its copy, its action, and **whether** a Retry button is present. The class table is a **closed union** — an unhandled status is a spec failure, not a silent `unexpected` |
| 8.2 | **The actionable `auth_not_configured` state — on CENTRAL surfaces only (AM-1).** The cause ("the metrics door is fail-closed — auth is not configured on the daemon"), what it means ("no credential can fix this — it is daemon configuration"), the **runbook pointer** (`docs/runbooks/oauth2-auth.md`), and **no Retry button** (a config error is not transient). It renders on the **catalog drawer + the 6 central tab bodies**; **the custom slot renders its own source's state** — tab 1 stays lit under a central-door failure | 8.1, Phase 2 | The spec asserts: `auth_not_configured` renders the actionable panel with the runbook pointer and **zero** Retry buttons; the other classes keep their banner + Retry semantics; a planted "Retry on the auth state" variant fails; and **a central failure does NOT blank a custom tab** |
| 8.3 | **De-duplicate the banners.** Today the page renders a page-level banner AND a per-tab banner for the same failure. The door state is **per source**: the central surfaces render the state **once**; the tab bodies do not each re-render it | 8.2 | The spec asserts exactly **one** banner/panel per failure state per surface; a planted second banner fails |
| 8.4 | Remove the **self-contradictory copy**: the current text promises "the last known values are kept" while rendering no values. The last-good/stale behaviour is real (the house's never-blank degradation) — the copy must state the truth **for the case that is happening** (no last-good exists yet ⇒ say so; a last-good exists ⇒ the STALE badge carries it) | 8.2 | A copy assertion: the empty-state text does not claim last-good values when none are rendered; the STALE badge still appears when a last-good exists |
| 8.5 | **The door posture is a PAGE-SCOPED chip, not the global health dot (AM-21).** 8.4's original mechanism — recolouring the app-shell's status dot — coupled a page-local posture to the **global** health indicator, which is bound to `/api/health` (`frontend/src/app/app.html:92-94`, `frontend/src/app/app.ts:779-785`). The daemon is healthy when the door is deliberately fail-closed; a red dot would be a **false global statement**. **The dot stays bound to `/api/health`**; the metrics header renders a **page-scoped chip** carrying the door posture | 8.2 | The shell's dot class is unchanged by a door failure (a spec asserts it); the metrics header carries the posture chip; a planted "red global dot" variant fails |
| 8.6 | Fix the **900 px collision** (banner text vs the Retry button): the banner layout must reflow (stack at narrow widths, or the text must wrap without overlapping the button). The audit captured it at 900×1000 (screenshot `01b`) | 8.2 | A layout spec at 900 px asserts the banner text and the button do not overlap (a geometry assertion, not a screenshot) |
| 8.7 | **Do not leave enabled-but-empty tabs.** Today three tabs stay enabled and fail identically. The state is **per source** (AM-1): a central tab shows the central door's state; the custom tab shows its own source's; where a tab's families are all dark, the tab shows the family's own reason (ADJ-vi), not an empty shell | 8.3, Phase 2 | The spec asserts a tab whose families are all dark renders the family reasons; a planted "enabled-but-empty" variant fails; **and** the custom tab under a central failure shows **its** data (not the central state) |
| 8.8 | Fix the **`chartSpecs` honesty gap** (ADJ-vi): filter by `availability.state` **before** the `METRICS_SELECTOR_CEILING` slice (`frontend/src/app/pages/metrics/metrics.component.ts:162-175`, ceiling 50 at `:40`), and **surface the FE's own truncation** when it truncates ("N families are not shown — the door's selector ceiling is 50"). Today dark families consume the budget and later live families are cut **silently** | Phase 2 | The spec asserts: a dark family does not consume the ceiling; when the FE's slice truncates, the hint is rendered; a planted "dark family consumed a slot" variant fails |
| 8.9 | Fix the **inert catalog table** (the drawer's inherited defect): the `<th>` cells (`frontend/src/app/pages/metrics/families/families-tab.component.ts:50-55`) get `aria-sort` + click sorting, and the dark families become **findable** (a filter). The empty copy is **error-gated** ("No families are registered." must not render under a failed load) | Phase 2 | The spec asserts the sorting, the `aria-sort` attribute, the filter, and the error-gated copy (planted: a failed load ⇒ the error state, never "No families are registered.") |
| 8.10 | Fix the **stale-data marker**: the audit found no marker of **where the data ends** on the chart itself, and no last-good timestamp on the stat cards (only the Charts meta line prints the sample age, `frontend/src/app/pages/metrics/charts/charts-tab.component.ts:98`). The consolidated panels carry a data-end marker and the stat strip carries the sample age | Phase 4, Phase 6 | The spec asserts the data-end marker exists on a stale panel and the stat strip shows the age |
| 8.11 | **One copy SSOT (AM-20).** The door-state copy becomes **one structured const per class** in `frontend/src/app/models/metrics.model.ts` — each with **cause / meaning / action / pointer** — replacing the flat `Record<MetricsDoorErrorCode, string>` (`:384-392`) that cannot hold a 3-part actionable copy. The **two duplicated degraded-registry suffixes** (`frontend/src/app/pages/metrics/overview/overview-tab.component.ts:295` and `frontend/src/app/pages/metrics/charts/charts-tab.component.ts:311`) move into that SSOT | 8.1 | The spec asserts every class's copy comes from the SSOT (the two duplicated suffixes are gone — a grep proves it) |
| 8.12 | **The 403 predicate is narrowed (AM-20).** `scope_insufficient` is `rawCode === 'SCOPE_INSUFFICIENT'` (case-insensitive) **only**; any other 403 becomes the new **`forbidden_other`** class ("rejected before the scope check"). The current predicate labels *any* 403-with-a-code as `scope_insufficient` (`frontend/src/app/models/metrics.model.ts:426-428`) — a false statement about the operator's session, contradicting the function's own doc comment. **Both 403 branches are pinned** | 8.1 | Two spec legs: a 403 with `SCOPE_INSUFFICIENT` ⇒ `scope_insufficient`; a 403 with another code ⇒ `forbidden_other` (never `scope_insufficient`) |

## Coupling

- **Tight with:** Phase 2 (the frame, the drawer, the per-source state model,
  the door timeout's `timeout` class), Phase 4 (the data-end marker is a
  renderer capability), Phase 6 (the panels' meta), **Phase 7 (the redirect
  lands on this surface — 7.1 depends on this phase, AM-7)**.
- **Loose with:** Phase 9 (PX-8, PX-14).
- **Independent of:** Phase 1, Phase 3, Phase 5.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| The actionable auth state leaks implementation detail ("ENSEMBLE_AUTH_*") into the UI | Medium | The copy states the **fact and the runbook**, not the env vars; a copy review asserts no env var name appears |
| The frame-level state hides a **tab-specific** failure (a tab's own fetch fails while the frame's catalog is fine) | Medium | The per-source model (8.2) keeps a tab's own fetch failure as a **tab-level** state — a different fact from the door's state |
| The empty-state rewrite breaks the existing degradation specs (the house's never-blank behaviour is a strength) | Medium | The STALE / last-good path is preserved explicitly (8.4); the existing degradation specs stay green, with the changed assertions listed |
| The **global** health dot is recoloured by a page-local condition | Medium — a false global statement | 8.5's page-scoped chip; the dot stays bound to `/api/health` (a spec asserts it) |
| The 403 predicate keeps labelling a CSRF-guard 403 as a scope failure | Medium — a false statement about the operator's session | 8.12's narrowed predicate + both branches pinned |
| The class table grows unboundedly (one class per status) | Low | The table is a **closed union**; an unhandled status is a spec failure, not a silent fall-through |

## Exit Criterion

- The door-state table is a **closed union** of the ten classes with per-class
  copy, action and Retry policy — each pinned; 401 (`auth_required`) is a
  first-class **sign-in** state, not `unexpected`.
- The 503 `AUTH_NOT_CONFIGURED` case renders **one** actionable panel with the
  runbook pointer and **no** Retry button, **on the central surfaces only**.
- **A central-door failure does not blank the custom tab** (8.2's spec): tab 1
  stays lit.
- The other classes keep their own banners + Retry semantics; the 403 branches
  are both pinned (`scope_insufficient` vs `forbidden_other`).
- No duplicate banners; no self-contradictory "last known values" claim when
  no values are rendered.
- The **global** health dot is untouched by a door failure; the door posture
  appears as a **page-scoped chip**.
- The 900 px banner/button collision is gone (a geometry assertion, not a
  screenshot).
- A tab whose families are all dark shows the **reasons**, not an empty shell.
- The FE's own selector slice is filtered by availability and **surfaced** when
  it truncates.
- The catalog table sorts, filters, carries `aria-sort`, and is error-gated.
- The stale panels carry a data-end marker; the stat strip shows the age.
- The copy lives in **one** SSOT; the two duplicated suffixes are gone.
- **E-1 re-derivation:** this phase gates on **no** MEASURED figure. Its
  inputs (the error-decode constants, the copy sites, the banner markup) are
  **structural facts** re-verified at lane base and re-read at this phase's
  freeze.
