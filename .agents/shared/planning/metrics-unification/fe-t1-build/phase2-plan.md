# Phase 2: Tab manifest + 7-tab shell + `?tab=` deep links

## Objective

Create the FE-local typed **tab manifest** and rewire the `/metrics` shell
around it: 7 module tabs (6 central + 1 custom), a frame-level family-catalog
drawer (replacing the Families tab), and `?tab=` deep links that read and
write the query parameter.

The manifest is the config promise: **adding a series to a wired module is
registration-only**; only a new *tab* costs an FE commit.

## Tasks

| # | Task | Depends On | Acceptance |
|---|---|---|---|
| 2.1 | Create `frontend/src/app/pages/metrics/tabs.manifest.ts` — the FE-local typed manifest: `MetricsTabManifest { version: 1; tabs: readonly MetricsTabDef[] }` with the discriminated `CentralDataTab | CustomDataTab` slots, per `resource-refactor-architecture.md` §1.1. It carries **only what the backend cannot know**: tab grouping, titles, ordering, window defaults/allowed sets, drill-down destinations, empty-state copy, and the live/planned onboarding state. Everything derivable from the wire (`chart_role`, `role`, labels, availability, buckets) is read at runtime and **never duplicated** | Phase 1 | A new spec pins the manifest shape: 7 `live` tabs; unique, stable `id` slugs; `planned` entries present but renderable-nowhere; every `central` tab's `familyIds` are real family ids |
| 2.2 | Populate the 7 entries per `plan-overview.md` §4 — including the **bracketed planned families**: Runtime binds `[F1, F3]`, DB Pool binds `[F6]`. They are bound **now** and gated by availability, so R-ii / R-i land with **zero FE change** (ADJ-vi) | 2.1 | The manifest spec asserts the exact family binding per tab; a spec asserts the bracketed families are bound and currently dark |
| 2.3 | Populate the **planned (hidden) entries** for the no-source modules: LLM token usage (F14), LLM request latency (F15), source circuit-breakers/token-buckets — each with a `trackingNote` naming the gap and its owner. They render **nothing** | 2.1 | The spec asserts no planned entry contributes a tab, a panel, or a route |
| 2.4 | Create the 7 tab bodies under `frontend/src/app/pages/metrics/tabs/` — one component per module tab. The 6 central tabs are thin: they bind their families, request their series through the existing `MetricsService`, and hand the data to the renderer registry. Tab 1 (Resources) is the `custom` slot and is built in Phase 5 — Phase 2 lands its **manifest entry + shell wiring** with the body mounting the relocated module once Phase 5 lands (the tab is present and correct in the manifest; the body may render a "module landing here" placeholder **only until Phase 5 completes** — that placeholder is removed in Phase 5 and never reaches a release) | 2.1 | The shell renders 7 tabs; the 6 central tabs fetch their families and surface their availability; the tab-1 placeholder is tracked as a Phase 5 debt and removed there |
| 2.5 | Rewire `frontend/src/app/pages/metrics/metrics.component.ts` — the `sections` registry (`:84-88`) is replaced by the manifest-driven tab list; `activeTabId` (`:118`) is now driven by the manifest ids; `onTabChange` (`:204-209`) writes `?tab=<id>` | 2.1 | The shell spec asserts the rendered tab labels and ids match the manifest exactly |
| 2.6 | Implement the deep-link read in `ngOnInit` (`:199-201`): read `queryParamMap`'s `tab`; a **known** id selects that tab; an **unknown/legacy** id (e.g. the old `overview` / `charts` / `families`) falls back to the default tab **without a redirect loop**; no `tab` param ⇒ the default tab (tab 1, Resources) | 2.5 | The shell spec asserts all four cases (known, unknown, legacy, absent); a legacy `?tab=overview` lands on tab 1 with **no** navigation storm |
| 2.7 | Move the family catalog from a tab to a **frame-level drawer** in the metrics shell: the drawer is opened from the frame toolbar, renders the existing catalog content (the `families-tab.component.ts` row rendering + `formatAvailabilityReason`, `metrics.model.ts:294-313`), and adds the missing affordances the audit found: sortable/filterable columns, `aria-sort` + click on the `<th>` cells (today inert — `families-tab.component.ts:50-55`), and the **error-gated** empty copy ("No families are registered." must not render under a failed load) | 2.5 | The drawer spec asserts the three new affordances; the error-gated copy has a planted-violation test (a failed catalog load ⇒ the error state, never "No families are registered.") |
| 2.8 | Remove the **dead `active` input** (`families-tab.component.ts:210` — declared, documented, passed by the shell at `metrics.component.ts:188`, never referenced; it implies a poll gate that does not exist) together with its shell plumbing | 2.7 | A repo-wide grep finds **zero** references to the removed input; the catalog component no longer accepts it |
| 2.9 | Update `frontend/src/app/pages/metrics/metrics.component.spec.ts` (519 L) and `frontend/src/app/pages/metrics/metrics-gear-menu.spec.ts` to the new shape; **preserve** every existing behavioural assertion that still applies (the registry-degraded badge, the truncated-catalog hint, the empty-panel conditions at `:146-155`, the error copy at `:245`) and re-point the ones that describe the retired 3-tab shape | 2.5, 2.7 | The specs are green; a diff review shows no assertion silently deleted — each removal is either re-pointed or explicitly listed with a reason |

## Coupling

- **Tight with:** Phase 3 (the manifest's `allowedWindows`/`defaultWindowS` ARE
  the frame's range options — the frame must not own a second list), Phase 5
  (tab 1's custom slot), Phase 7 (the redirect target id must equal the
  manifest entry's id: `resource`), Phase 8 (the empty state lives in the shell).
- **Loose with:** Phase 9 (the pixel list exercises the 7 tabs).
- **Independent of:** Phase 1 (except the theme extraction), Phase 4, Phase 6.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| The manifest duplicates wire-derivable facts (roles, availability) and drifts | Medium — the honest drift class the house warns about | The manifest spec asserts the manifest carries **no** wire-derivable field; the runtime reads availability from the catalog projection |
| A tab renders a dark family as an empty chart | Medium | The per-family availability gate (ADJ-vi) is a Phase 2 acceptance, not a Phase 8 nicety |
| The deep-link write-back fights the tab group's own selection change | Medium | One owner of `activeTabId`: the manifest id is the single slug source; the spec asserts the URL and the selected tab never disagree |
| The 519 L shell spec is churned into meaninglessness | Medium | 2.9 requires each deleted assertion to be listed with a reason — a silent deletion fails the exit criterion |

## Exit Criterion

- `frontend/src/app/pages/metrics/tabs.manifest.ts` exists, is typed, and is
  covered by a new spec.
- The shell renders **7** tabs whose labels + ids match the manifest exactly;
  the old 3-tab shape is gone.
- `?tab=resource` selects tab 1; an unknown id falls back with **no** loop;
  the URL is written back on tab change.
- The family catalog is a frame-level drawer with sortable, `aria-sort`-bearing,
  error-gated content.
- The dead `active` input and its plumbing are gone (grep: 0 hits).
- The existing metrics specs are green; no assertion was silently dropped.
- `agents/.prompt-artifacts.sha256` still verifies **161/161**.
