# Phase 8: Empty state + honesty (D-2)

## Objective

Make the page **honest about its failures** — and make the fail-closed state
**actionable**. D-2 requires the FE to distinguish 503 `AUTH_NOT_CONFIGURED`
from every other dark cause and to render an actionable empty state pointing
at `docs/runbooks/oauth2-auth.md`.

Today's `/metrics` empty state is honest about the cause and dishonest about
the consequence: two identical banners, one self-contradictory ("serves no
metrics. — the last known values are kept", with no values rendered); two Retry
buttons that can never succeed; three tabs left enabled that all fail
identically; no HTTP status surfaced; no runbook link; a **green header dot**
while the body reports hard failure; and a 900 px banner/Retry collision.

## Tasks

| # | Task | Depends On | Acceptance |
|---|---|---|---|
| 8.1 | Add a **distinct `auth_not_configured` state** (IMPL-09) at the frame level: the cause ("the metrics door is fail-closed — auth is not configured on the daemon"), what it means ("no credential can fix this — it is daemon configuration"), the **runbook pointer** (`docs/runbooks/oauth2-auth.md`), and **no Retry button** (a config error is not transient). The other four door classes (`scope_insufficient`, `registry_unavailable`, `network`, `unexpected`) keep their banner + Retry semantics | Phase 2 | The empty-state spec asserts: `auth_not_configured` renders the actionable panel with the runbook pointer and **zero** Retry buttons; the other four classes keep theirs; a planted "Retry on the auth state" variant fails the spec |
| 8.2 | **De-duplicate the banners.** Today the page renders a page-level banner AND a per-tab banner for the same failure. The door state is frame-level: the frame renders the state **once**; the tab bodies do not each re-render it | 8.1 | The spec asserts exactly **one** banner/panel per failure state; a planted second banner fails |
| 8.3 | Remove the **self-contradictory copy**: the current text promises "the last known values are kept" while rendering no values. The last-good/stale behaviour is real (the house's never-blank degradation) — the copy must state the truth **for the case that is happening** (no last-good exists yet ⇒ say so; a last-good exists ⇒ the STALE badge carries it) | 8.1 | A copy assertion: the empty-state text does not claim last-good values when none are rendered; the STALE badge still appears when a last-good exists |
| 8.4 | Fix the **green header dot during hard failure** — the header's health indicator must reflect the door's state (a hard failure ⇒ not green). The dot is app-shell state, so the implementation is a frame-level signal the shell reads | 8.1 | The spec asserts the dot's class/state is not "healthy" while the door is in a hard-failure state; a planted green-dot variant fails |
| 8.5 | Fix the **900 px collision** (banner text vs the Retry button): the banner layout must reflow (stack at narrow widths, or the text must wrap without overlapping the button). The audit captured it at 900×1000 (screenshot `01b`) | 8.1 | A layout spec at 900 px asserts the banner text and the button do not overlap (a geometry assertion, not a screenshot) |
| 8.6 | **Do not leave enabled-but-empty tabs.** Today three tabs stay enabled and fail identically. Under the new shell, the frame's state governs: the tabs stay visible (they are the page's structure) but the **body** shows the frame's state, not three independent failures. Where a tab's families are all dark, the tab shows the family's own reason (ADJ-vi), not an empty shell | 8.2, Phase 2 | The spec asserts a tab whose families are all dark renders the family reasons; a planted "enabled-but-empty" variant fails |
| 8.7 | Fix the **`chartSpecs` honesty gap** (ADJ-vi): filter by `availability.state` **before** the `METRICS_SELECTOR_CEILING` slice (`frontend/src/app/pages/metrics/metrics.component.ts:162-175`, ceiling 50 at `:40`), and **surface the FE's own truncation** when it truncates ("N families are not shown — the door's selector ceiling is 50"). Today dark families consume the budget and later live families are cut **silently** | Phase 2 | The spec asserts: a dark family does not consume the ceiling; when the FE's slice truncates, the hint is rendered; a planted "dark family consumed a slot" variant fails |
| 8.8 | Fix the **inert catalog table** (the drawer's inherited defect): the `<th>` cells (`families-tab.component.ts:50-55`) get `aria-sort` + click sorting, and the dark families become **findable** (a filter). The empty copy is **error-gated** ("No families are registered." must not render under a failed load) | Phase 2 | The spec asserts the sorting, the `aria-sort` attribute, the filter, and the error-gated copy (planted: a failed load ⇒ the error state, never "No families are registered.") |
| 8.9 | Fix the **stale-data marker**: the audit found no marker of **where the data ends** on the chart itself, and no last-good timestamp on the stat cards (only the Charts meta line prints the sample age, `charts-tab.component.ts:98`). The consolidated panels carry a data-end marker and the stat strip carries the sample age | Phase 4, Phase 6 | The spec asserts the data-end marker exists on a stale panel and the stat strip shows the age |

## Coupling

- **Tight with:** Phase 2 (the frame + the drawer), Phase 4 (the data-end
  marker is a renderer capability), Phase 7 (the redirect lands on this surface).
- **Loose with:** Phase 9 (PX-8, PX-14).
- **Independent of:** Phase 1, Phase 3, Phase 5, Phase 6.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| The actionable auth state leaks implementation detail ("ENSEMBLE_AUTH_*") into the UI | Medium | The copy states the **fact and the runbook**, not the env vars; a copy review asserts no env var name appears |
| The frame-level state hides a **tab-specific** failure (a tab's own fetch fails while the frame's catalog is fine) | Medium | The frame state governs the **door**; a tab's own fetch failure keeps its own tab-level error banner (the two are different facts) |
| The empty-state rewrite breaks the existing degradation specs (the house's never-blank behaviour is a strength) | Medium | The STALE / last-good path is preserved explicitly (8.3); the existing degradation specs stay green, with the changed assertions listed |
| The "unit unknown" / "not on this page" states read as bugs | Low | Each is a **labelled** state with a one-line explanation — honesty over blankness |

## Exit Criterion

- The 503 `AUTH_NOT_CONFIGURED` case renders **one** actionable panel with the
  runbook pointer and **no** Retry button.
- The other four door classes keep their own banners + Retry semantics.
- No duplicate banners; no self-contradictory "last known values" claim when
  no values are rendered.
- The header dot is not green during a hard failure.
- The 900 px banner/button collision is gone (a geometry assertion, not a
  screenshot).
- A tab whose families are all dark shows the **reasons**, not an empty shell.
- The FE's own selector slice is filtered by availability and **surfaced** when
  it truncates.
- The catalog table sorts, filters, carries `aria-sort`, and is error-gated.
- The stale panels carry a data-end marker; the stat strip shows the age.
