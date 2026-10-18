# Phase 3: FE settings surface

## Objective

The existing `/settings` page gains a "Daemon" section where the LLM worker limit can be viewed with its active source tier, edited with `ge=1` validation, saved as a DB override, or reset — with an unmissable "requires restart" hint throughout.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Extend `frontend/src/app/services/settings.service.ts`: mirror the Phase-2 frozen contract as TS types (`DaemonSettingEntry`, `DaemonSettingsResponse`, `DaemonSettingUpdate`) + client methods `getDaemonSettings()`, `putDaemonSetting(key, value)`, `deleteDaemonSetting(key)` hitting `/api/settings/daemon[/{key}]` | P2 t1 | Types copied verbatim from the frozen contract; methods follow the file's existing HttpClient/Observable idiom |
| 2 | Add a fifth `<section class="setting-section">` ("Daemon") in `settings.component.html` — rendered data-driven over `settings[]` so future registry keys appear without new markup: numeric input (`type="number"`, `min="1"`, integer-only), effective value line, tier chip (`DB`/`ENV`/`YAML`/`DEFAULT`), read-only display of lower tiers (`env_value` / `yaml_value` / `db_override`) when present. **Divergence rendering (adopted note 9):** `value` (the `applied_value`-backed boot-applied field) and `source` (live tier) render as separate visible fields — after an override is deleted server-side, the section must show the STILL-RUNNING boot-applied `value` next to the NEW `source` tier distinctly, never collapsed into one number | P2 t1, t1-fe | Section renders from a stubbed GET payload in spec; chip reflects `source` exactly; divergence state (value ≠ resolved tier) renders both distinctly |
| 3 | Restart hint: always-visible **"Requires restart"** badge on any key with `requires_restart: true`; after successful PUT show "Saved — takes effect after daemon restart" (not a live-apply pretense); after DELETE show "Override cleared — file/env/default applies after restart" | t2 | Badge renders in every key state; save/clear confirmations worded per acceptance |
| 4 | Wire component logic in `settings.component.ts`: load on init, dirty-tracking, save → PUT (disabled while invalid: empty, non-integer, `< 1`), "Reset to file/env" → DELETE; reload effective state after either; error surface consistent with the page's existing sections | t2, service t1 | Invalid input blocks save; successful PUT/DELETE triggers re-GET; API errors surface without breaking the rest of the page |
| 5 | Styling in `settings.component.scss` — Material M3 idiom consistent with the four existing sections; include the muted **co-bound info line** (architect Ruling 5, corrected arithmetic): "Real parallelism is also capped by the fixed worker thread pools (5 worker + 2 chat = 7 concurrent graphs — constants, not settable here)." (documentation only, per commission constraint; no control; NO code-level `min()` exists — the 7 cap is emergent from the two pool sizes) | t2 | Visual review against sibling sections; no design-tokens reference (directory does not exist on this tree) |

## Tests

- EDIT `frontend/src/app/pages/settings/settings.component.spec.ts`:
  - Section renders from mocked `getDaemonSettings()` payload.
  - Validation: `0` / empty / non-integer → save disabled.
  - Restart badge present when `requires_restart: true`.
  - Tier chip renders per `source` (one case per tier).
  - **Divergence spec (adopted note 9): mocked post-DELETE payload where `value` is the `applied_value`-backed boot-applied number and `source` the new tier → the template renders BOTH distinctly (value line ≠ tier chip), never collapsed.**
  - Save calls `putDaemonSetting`; reset calls `deleteDaemonSetting`; both re-fetch.
- Service spec coverage in the same file's idiom (contract-shape assertions).

## Coupling

- **Tight with Phase 2** — consumes the frozen API contract; start only after P2 t1.
- **Independent of Phase 1** (FE never sees the service layer).

## Risks

- Page-section regression (the settings page is shared chrome) — mitigate by pattern-matching the existing four sections exactly; FE spec asserts existing sections still render.
- Users misreading "saved" as "applied" — mitigated by the task-3 wording rule (never imply live-apply).

## Exit Criterion

Section live on `/settings` (gear menu → Settings): effective value + tier chip + restart badge + working save/clear; FE specs green; no changes to routes, nav, or app shell.
